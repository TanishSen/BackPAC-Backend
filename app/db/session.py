"""The async database engine and the per-request session dependency.

Layering, top to bottom:  router -> service -> repository -> DB.
A router depends on `get_db_session`, hands the session to a service, the
service hands it to a repository, and the repository is the only layer that
runs queries. Nothing above the repository writes SQL.

`get_db_session` yields one session per request and always closes it, so no
route needs to remember to.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_settings = get_settings()

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


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, always closed.

    Usage in a router:
        async def route(db: AsyncSession = Depends(get_db_session)): ...
    """
    async with SessionFactory() as session:
        yield session
