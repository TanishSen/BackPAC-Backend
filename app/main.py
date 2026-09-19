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
    from app.db.session import init_database

    app.state.db_ready = await init_database()

    async with httpx.AsyncClient() as client:
        app.state.http_client = client
        yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="BackPAC Backend",
        description="Sessions (LiveKit broker) + trips (search & saved).",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router)
    return app


app = create_app()
