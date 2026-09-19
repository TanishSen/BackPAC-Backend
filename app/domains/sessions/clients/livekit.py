"""Talks to LiveKit: mint a room and an access token.

A "client" wraps one external dependency. Keeping LiveKit here means the
service layer says "give me a room + token" without knowing anything about the
LiveKit SDK — and if the SDK changes, only this file does.

Docs: https://docs.livekit.io/home/get-started/authentication/
"""

import logging

from livekit import api

from app.core.config import Settings
from app.shared.exceptions import ConfigurationError

logger = logging.getLogger(__name__)


class LiveKitClient:
    def __init__(self, settings: Settings):
        self._settings = settings

    def _require_configured(self) -> None:
        if not self._settings.livekit_configured:
            raise ConfigurationError(
                "LiveKit is not configured. Set LIVEKIT_URL, LIVEKIT_API_KEY "
                "and LIVEKIT_API_SECRET in .env (free tier at livekit.io)."
            )

    def create_access_token(
        self, room_name: str, participant_name: str
    ) -> str:
        """A JWT scoped to exactly one room.

        The app can join `room_name` and nothing else with this token, and it
        expires — so it is safe to hand to a phone. The API *secret* never
        leaves the server.
        """
        self._require_configured()
        token = (
            api.AccessToken(
                self._settings.livekit_api_key,
                self._settings.livekit_api_secret,
            )
            .with_identity(participant_name)
            .with_name(participant_name)
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=True,
                )
            )
        )
        return token.to_jwt()
