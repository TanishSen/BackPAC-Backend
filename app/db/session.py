"""The async database engine, startup, and the per-request session dependency.

Layering, top to bottom:  router -> service -> repository -> DB.
A router depends on `get_db_session`, hands the session to a service, the
service hands it to a repository, and the repository is the only layer that
runs queries. Nothing above the repository writes SQL.

Two rules this module exists to enforce:

1. **One session per request, always closed.** `get_db_session` yields it and
   cleans up, so no route has to remember to.

2. **A missing database must not take the whole API down.** Voice calls and
   trip search touch no tables at all; only saved-trips does. So startup logs
   loudly and carries on if the DB is unreachable, and the handful of routes
   that genuinely need it return a clear 503 instead of a stack trace. See
   `init_database` and `get_db_session` below.
"""

import logging
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.shared.exceptions import ConfigurationError

logger = logging.getLogger(__name__)

_settings = get_settings()

# Creating the engine opens no connection — it just parses the URL and picks a
# driver. A wrong host fails later, at first use, which is what lets the app
# boot without a database running.
engine = create_async_engine(
    _settings.database_url,
    pool_pre_ping=True,  # drop dead connections instead of erroring on them
    future=True,
)

SessionFactory = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,  # objects stay usable after commit
    class_=AsyncSession,
)

# Flipped to True by `init_database` once tables exist. Read it through
# `is_db_ready()`; /readyz reports it so "why did saving fail?" is answerable
# from outside without reading logs.
_db_ready = False


def is_db_ready() -> bool:
    return _db_ready


async def init_database() -> bool:
    """Create any missing tables. Returns whether the database is usable.

    Importing the models is what registers them on `Base.metadata`, so
    `create_all` knows the tables exist to be made. Fine for now; swap for
    Alembic migrations before this schema has data anyone would miss.
    """
    global _db_ready

    from app.db.base import Base
    from app.domains.trips import models as _trip_models  # noqa: F401 (registers tables)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001 — a dead DB is not a reason to not boot
        _db_ready = False
        logger.warning(
            "database unavailable (%s): %s. The API is still up — voice sessions "
            "and trip search need no database. Saved-trip routes will return 503 "
            "until it is reachable.",
            _settings.database_url.split("://", 1)[0],
            exc,
        )
        return False

    _db_ready = True
    logger.info("database ready (%s)", _settings.database_url.split("://", 1)[0])
    return True


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, always closed.

    Usage in a router:
        async def route(db: AsyncSession = Depends(get_db_session)): ...
    """
    if not _db_ready:
        # A clear, actionable 503 beats an asyncpg connection traceback.
        raise ConfigurationError(
            "The database is not available. Start Postgres (or set DATABASE_URL "
            "to sqlite+aiosqlite:///./backpac.db for local work) and restart."
        )
    async with SessionFactory() as session:
        yield session
