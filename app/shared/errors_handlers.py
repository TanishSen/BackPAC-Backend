"""Turns domain exceptions into JSON HTTP responses, in one place.

Register once at startup (main.py calls `register_exception_handlers`). After
that, a service can `raise NotFoundError("no such trip")` and the client
receives `404 {"error": "no such trip"}` — the router never touches it.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.shared.exceptions import AppError

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        # 5xx is our bug; log it loudly. 4xx is the caller's; stay quiet.
        if exc.status_code >= 500:
            logger.exception("AppError: %s", exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.message},
        )

    @app.exception_handler(OperationalError)
    @app.exception_handler(PoolTimeoutError)
    async def _handle_db_down(request: Request, exc: Exception) -> JSONResponse:
        # The database went away after boot, or every pooled connection is
        # busy. "Try again" is the truth; a bare 500 would say we are broken.
        logger.error("database error on %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse(
            status_code=503,
            content={"error": "The database is temporarily unavailable. Please try again."},
            headers={"Retry-After": "5"},
        )

    @app.exception_handler(DBAPIError)
    async def _handle_db_error(request: Request, exc: DBAPIError) -> JSONResponse:
        logger.exception("database error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"error": "Something went wrong."})

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Same JSON shape as every other error, so the app has one thing to
        # parse, and no stack trace or exception text reaches the caller.
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"error": "Something went wrong."})
