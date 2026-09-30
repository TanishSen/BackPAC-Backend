"""Talks to the agent runtime (BackPAC-Agent).

After this backend has minted a LiveKit room, it asks the agent service to put
a bot into that room. That is this client's whole job: one HTTP call to the
agent's `POST /start`.

The shared httpx client (one connection pool for the process) is created in
main.py's lifespan and passed in, rather than opening a new connection per
request.
"""

import logging

import httpx

from app.core.config import Settings
from app.shared.exceptions import UpstreamError

logger = logging.getLogger(__name__)


class AgentClient:
    def __init__(self, http: httpx.AsyncClient, settings: Settings):
        self._http = http
        self._settings = settings

    @property
    def _headers(self) -> dict[str, str]:
        """The shared secret, so the agent can refuse anyone who is not us.

        `/start` puts a billable bot in a room; an agent reachable from the
        internet without this check is an open tap on three paid APIs.
        """
        token = self._settings.service_token
        return {"X-Service-Token": token} if token else {}

    async def start(
        self,
        *,
        room_name: str,
        session_id: str,
        agent_id: str,
        thread_id: str,
        is_resuming: bool = False,
    ) -> None:
        """Ask the agent to join `room_name`. Raises UpstreamError if the agent
        is down or refuses — the caller turns that into a 502 so the app knows
        the room it was about to join has no one in it."""
        url = f"{self._settings.agent_base_url}/start"
        payload = {
            "roomName": room_name,
            "sessionId": session_id,
            "agentId": agent_id,
            # The LangGraph thread to think in. Equal to the room name today,
            # but sent as its own field so the two can be separated later
            # without a change on both sides of the wire at once.
            "threadId": thread_id,
            # A hint, not an instruction: the agent loads whatever state the
            # thread has either way. It uses this to decide whether to open
            # with a greeting or pick up mid-conversation.
            "isResuming": is_resuming,
        }
        try:
            resp = await self._http.post(
                url,
                json=payload,
                headers=self._headers,
                timeout=self._settings.agent_timeout_seconds,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("agent /start failed: %s", exc)
            raise UpstreamError(
                "Could not start the voice agent. Is BackPAC-Agent running?"
            ) from exc

    async def stop(self, *, session_id: str) -> bool:
        """Ask the agent to end one running bot. Best effort; never raises.

        Used when the app hangs up. The agent also notices the caller leaving
        the room on its own, so a failure here costs at most the time until
        it does — not worth failing the user's hang-up over.
        """
        try:
            resp = await self._http.post(
                f"{self._settings.agent_base_url}/stop",
                json={"sessionId": session_id},
                headers=self._headers,
                timeout=5.0,
            )
        except httpx.HTTPError as exc:
            logger.warning("agent /stop failed for %s: %s", session_id, exc)
            return False
        # 404 means it already ended by itself, which is the outcome we wanted.
        return resp.status_code in (200, 404)
