"""HTTP routes for sessions. Thin on purpose.

A router does three things and nothing else: read the request, call a service,
shape the response. No business logic, no SQL, no SDK calls. If you find
yourself writing an `if` about trip data here, it belongs in the service.
"""

from fastapi import APIRouter, Request

from app.core.config import get_settings
from app.domains.sessions.schemas import SessionCreate, SessionEnvelope
from app.domains.sessions.service import SessionService

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionEnvelope)
async def create_session(body: SessionCreate, request: Request) -> SessionEnvelope:
    """Start a voice session and return the room the app should join.

    The shared httpx client lives on `request.app.state.http_client` (set up in
    main.py's lifespan) so we reuse one connection pool.
    """
    service = SessionService(request.app.state.http_client, get_settings())
    return await service.start_session(body)
