"""Turns domain exceptions into JSON HTTP responses, in one place.

Register once at startup (main.py calls `register_exception_handlers`). After
that, a service can `raise NotFoundError("no such trip")` and the client
receives `404 {"error": "no such trip"}` — the router never touches it.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

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
