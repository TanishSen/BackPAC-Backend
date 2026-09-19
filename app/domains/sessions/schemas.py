"""Request and response shapes for starting a voice session.

These are the contract between the Flutter app and this backend. Field names
use camelCase on the wire (what the app sends) but snake_case in Python;
`populate_by_name` + `alias` bridge the two, so the app sends `agentId` and the
code reads `agent_id`.
"""

from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    """Body for `POST /api/v1/sessions`.

    The app asks to talk to an agent; it does not choose the room or the token
    — the server mints both, because only the server holds the LiveKit secret.
    """

    model_config = ConfigDict(populate_by_name=True)

    agent_id: str = Field(alias="agentId")
    #: A display name for the caller in the LiveKit room. Optional; defaults to
    #: a generated guest name.
    participant_name: str | None = Field(default=None, alias="participantName")


class LiveKitTransport(BaseModel):
    """Everything the app needs to join the room — and nothing it shouldn't
    have (no API secret; the token is scoped to this one room)."""

    model_config = ConfigDict(populate_by_name=True)

    url: str
    token: str
    room_name: str = Field(alias="roomName")


class SessionEnvelope(BaseModel):
    """Response for `POST /api/v1/sessions`. The app joins `livekit.url` with
    `livekit.token`; the agent is already in the room by the time this
    returns."""

    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(alias="sessionId")
    agent_id: str = Field(alias="agentId")
    livekit: LiveKitTransport
