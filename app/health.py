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
    body = {
        "status": "ok",
        "service": settings.service_name,
        "environment": settings.environment,
        # False => POST /sessions returns 503. Fill LIVEKIT_* in .env.
        "livekit_configured": settings.livekit_configured,
        # False => every signed-in route returns 503. Set SUPABASE_PROJECT_REF.
        "auth_configured": settings.auth_configured,
        # False => the agent cannot log transcripts or search. Set SERVICE_TOKEN.
        "service_token_configured": bool(settings.service_token),
        # False => sample flights, clearly labelled. Set TRAVELPAYOUTS_TOKEN.
        "flights_live": settings.flights_live,
        # False => history and saved trips return 503 until it is reachable;
        # the next request that needs it retries the connection.
        "database_ready": is_db_ready(),
    }
    # An internal address is a map for an attacker; only show it off-prod.
    if not settings.is_production:
        body["agent_base_url"] = settings.agent_base_url
    return body
