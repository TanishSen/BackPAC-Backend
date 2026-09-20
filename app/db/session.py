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

from sqlalchemy import text
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
def _connect_args() -> dict:
    """Driver options that depend on where the database actually is.

    Supabase is reached through a connection pooler, and a pooler hands the
    same backend connection to different clients over time. asyncpg's prepared
    statement cache assumes the opposite — that a statement it prepared is
    still there next time — so against a pooler it eventually raises
    DuplicatePreparedStatementError under load. Turning the cache off is the
    supported fix and costs a re-parse per query, which is nothing next to the
    round trip to Singapore.

    SQLite takes none of this, hence the check rather than an unconditional
    dict: passing asyncpg options to aiosqlite is an immediate TypeError.
    """
    if _settings.database_url.startswith("postgresql"):
        return {"statement_cache_size": 0}
    return {}


# Creating the engine opens no connection — it just parses the URL and picks a
# driver. A wrong host fails later, at first use, which is what lets the app
# boot without a database running.
engine = create_async_engine(
    _settings.database_url,
    pool_pre_ping=True,  # drop dead connections instead of erroring on them
    future=True,
    connect_args=_connect_args(),
    # Sized deliberately, because the ceiling is not ours.
    #
    # SQLAlchemy defaults to pool_size=5 with max_overflow=10, so one process
    # will happily open 15 connections. Supabase's pooler allows 15 clients in
    # total on the free plan — for everything, including the agent's
    # checkpointer pool and whatever psql you have open. The default therefore
    # exhausts the quota on its own under load and fails with EMAXCONNSESSION,
    # which reads like a database outage rather than a config mistake.
    #
    # These numbers are for one API container against a free project. Raise
    # them together with the plan, not ahead of it.
    pool_size=5,
    max_overflow=2,
    pool_timeout=20,
    # Recycle before the pooler decides an idle connection is stale, so a quiet
    # night is not followed by a burst of errors in the morning.
    pool_recycle=900,
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
    """Check the database is reachable. Returns whether it is usable.

    It does NOT create tables. Schema is Alembic's job now (`alembic upgrade
    head`), because `create_all` only ever creates what is missing — it cannot
    alter a column, so the first change to a live table would be silently
    ignored here and discovered in production. A table that has not been
    migrated shows up as a clear error on the route that needs it.
    """
    global _db_ready

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
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


async def warm_pool() -> None:
    """Open the pool's first connections before anyone needs them.

    Connecting to Postgres is not cheap when it is in another region: DNS, a
    TCP handshake, TLS, then authentication — and `pool_pre_ping` adds a round
    trip on top when the connection is checked out. Doing that inside the
    first real request made it several seconds slower than every request after
    it, for no reason other than arriving first.

    Two connections, not the whole pool: enough that the first couple of
    requests are fast, without making startup wait on five handshakes.
    """
    if not _db_ready:
        return
    try:
        async with engine.connect() as a, engine.connect() as b:
            await a.execute(text("SELECT 1"))
            await b.execute(text("SELECT 1"))
        logger.info("database pool warmed")
    except Exception as exc:  # noqa: BLE001 — an optimisation, not a dependency
        logger.warning("could not warm the database pool: %s", exc)


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
