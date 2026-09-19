"""Voice bits that are not a call.

Today that is one thing: the spoken greeting the welcome screen plays while the
orb arrives. The synthesis happens in the agent, because that is where the
ElevenLabs key lives — this service's job is to let the app fetch it without
ever needing to know the agent exists.
"""

import logging

import httpx

from app.core.config import Settings
from app.domains.voice.schemas import GreetingOut, WelcomeLinesOut
from app.shared.exceptions import UpstreamError

logger = logging.getLogger(__name__)


class VoiceService:
    def __init__(self, http: httpx.AsyncClient, settings: Settings):
        self._http = http
        self._settings = settings

    async def greeting(self, text: str | None = None) -> GreetingOut:
        """Fetch (and implicitly cache, agent-side) the spoken hello.

        Raises UpstreamError if the agent is unreachable. The app treats that
        as "no spoken greeting" and falls back to a silent one — a hello is
        nice, not load-bearing.
        """
        url = f"{self._settings.agent_base_url}/greeting"
        params = {"text": text} if text else None
        try:
            response = await self._http.get(
                url, params=params, timeout=self._settings.agent_timeout_seconds
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("agent /greeting failed: %s", exc)
            raise UpstreamError(
                "Could not fetch the greeting. Is BackPAC-Agent running?"
            ) from exc
        return GreetingOut.model_validate(response.json())

    async def welcome_lines(self) -> WelcomeLinesOut:
        """Every line the welcome screen might speak.

        A longer request than `greeting` the first time — the agent synthesises
        the whole set in parallel — and instant afterwards. The app asks for it
        in the background, so neither case is visible.
        """
        url = f"{self._settings.agent_base_url}/welcome-lines"
        try:
            # A cold agent synthesises ~20 lines here, so this gets its own,
            # longer budget rather than the standard agent timeout.
            response = await self._http.get(url, timeout=120.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("agent /welcome-lines failed: %s", exc)
            raise UpstreamError(
                "Could not fetch the orb's lines. Is BackPAC-Agent running?"
            ) from exc
        return WelcomeLinesOut.model_validate(response.json())

    async def line_audio(self, text: str) -> bytes:
        """The WAV for one line, straight from the agent.

        Always a cache hit agent-side in practice: whatever listed this line
        (`greeting` or `welcome_lines`) synthesised it on the way past.
        """
        url = f"{self._settings.agent_base_url}/voice-line.wav"
        try:
            response = await self._http.get(
                url,
                params={"text": text},
                timeout=self._settings.agent_timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("agent /voice-line.wav failed: %s", exc)
            raise UpstreamError("Could not fetch the line's audio.") from exc
        return response.content
