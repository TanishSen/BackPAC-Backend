"""Session orchestration — the one genuinely multi-step operation here.

Starting a voice call is a composition: mint a room, mint a token, put the
agent in the room, hand the token back to the app. The router does none of
this; it just calls `start_session` and shapes the response. All the ordering
lives here.

If you later add "record the session in the DB", it goes in this method,
between minting the room and calling the agent, via a repository — the same
router/service/repository layering the trips domain shows.
"""

import logging
import uuid

import httpx

from app.core.config import Settings
from app.domains.sessions.clients.agent import AgentClient
from app.domains.sessions.clients.livekit import LiveKitClient
from app.domains.sessions.schemas import (
    LiveKitTransport,
    SessionCreate,
    SessionEnvelope,
)

logger = logging.getLogger(__name__)


class SessionService:
    def __init__(self, http: httpx.AsyncClient, settings: Settings):
        self._livekit = LiveKitClient(settings)
        self._agent = AgentClient(http, settings)
        self._settings = settings

    async def start_session(self, body: SessionCreate) -> SessionEnvelope:
        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        room_name = f"backpac-{session_id}"
        participant = body.participant_name or f"guest-{uuid.uuid4().hex[:6]}"

        # 1. Mint the room token (raises ConfigurationError if LiveKit unset).
        token = self._livekit.create_access_token(room_name, participant)

        # 2. Put the agent in the room BEFORE returning, so the app never joins
        #    an empty room. If this raises, the whole request fails — better a
        #    clear error now than a silent room with no bot.
        await self._agent.start(
            room_name=room_name,
            session_id=session_id,
            agent_id=body.agent_id,
        )

        logger.info(
            "session %s started for agent %s in room %s",
            session_id,
            body.agent_id,
            room_name,
        )

        # 3. Hand the app what it needs to join.
        return SessionEnvelope(
            session_id=session_id,
            agent_id=body.agent_id,
            livekit=LiveKitTransport(
                url=self._settings.livekit_url,
                token=token,
                room_name=room_name,
            ),
        )
