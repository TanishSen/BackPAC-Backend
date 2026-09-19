"""Liveness and readiness — the two questions a load balancer asks.

`/healthz`  is the process alive at all?
`/readyz`   is it configured enough to actually serve traffic?

Neither touches the database or any upstream on purpose: a health check that
fans out is a health check that flaps.
"""

from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.service_name,
        "environment": settings.environment,
        # Surfaced so "why won't sessions start?" is answerable from the
        # outside without reading logs.
        "livekit_configured": settings.livekit_configured,
    }
