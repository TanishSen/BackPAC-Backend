"""Liveness and readiness — the two questions a load balancer asks.

`/healthz`  is the process alive at all?
`/readyz`   is it configured enough to actually serve traffic?

Neither calls out to LiveKit, the agent or the database on purpose: a health
check that fans out is a health check that flaps. `/readyz` reports flags the
process already knows, so a failing call can be diagnosed from outside without
reading logs — "livekit_configured: false" answers "why won't sessions start?"
in one request.
"""

from fastapi import APIRouter

from app.core.config import get_settings
from app.db.session import is_db_ready

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
        # False => POST /sessions returns 503. Fill LIVEKIT_* in .env.
        "livekit_configured": settings.livekit_configured,
        # False => only the saved-trip routes fail (503). Voice and search are
        # unaffected; they touch no tables.
        "database_ready": is_db_ready(),
        "agent_base_url": settings.agent_base_url,
    }
