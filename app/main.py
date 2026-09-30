"""FastAPI application factory and entrypoint.

Run it with:  uvicorn app.main:app --reload --port 8000

`create_app()` wires the pieces together in one readable place:
  - logging
  - a single shared httpx client for the process (the lifespan block)
  - CORS so the Flutter app can call it from a browser
  - the exception handlers (domain errors -> JSON)
  - the health routes and the /api/v1 routes
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.health import router as health_router
from app.legal import router as legal_router
from app.shared.errors_handlers import register_exception_handlers


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Everything that must exist before the first request, and be cleaned up
    after the last one.

    Two things live here:

    - **The database.** `init_database` creates any missing tables and reports
      whether the DB is usable. It deliberately does NOT raise: a voice call
      and trip search touch no tables, so an unreachable database must not stop
      the API from serving them. Only saved-trip routes 503, with a clear
      message. See db/session.py.

    - **One httpx client for the whole process**, used to call the agent — so
      we reuse a single connection pool instead of opening one per request.
    """
    from app.db.session import init_database, warm_pool
    from app.shared.auth import warm_jwks

    app.state.db_ready = await init_database()

    # Pay the cold-start costs here rather than on whoever asks first.
    #
    # The database lives in another region and the signing keys come from
    # Supabase over the network, so the first authenticated request used to
    # carry a TLS handshake to Singapore *and* a key fetch on top of its own
    # work — over twenty seconds on a cold process, which the app read as the
    # server being unreachable and reported as such while it was still coming.
    #
    # Both are gathered rather than awaited in turn: they are independent, and
    # startup should not be the sum of two round trips when it can be the
    # longer of them.
    import asyncio

    await asyncio.gather(
        warm_pool(),
        warm_jwks(get_settings()),
    )

    async with httpx.AsyncClient() as client:
        app.state.http_client = client
        yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    docs = settings.docs_enabled
    app = FastAPI(
        title="BackPAC Backend",
        description="Sessions (LiveKit broker) + trips (search & saved).",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(legal_router)
    app.include_router(api_router)
    return app


app = create_app()
