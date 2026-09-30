"""Request and response shapes for starting a voice session.

These are the contract between the Flutter app and this backend. Field names
use camelCase on the wire (what the app sends) but snake_case in Python;
`populate_by_name` + `alias` bridge the two, so the app sends `agentId` and the
code reads `agent_id`.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    """Body for `POST /api/v1/sessions`.

    The app asks to talk to an agent; it does not choose the room or the token
    — the server mints both, because only the server holds the LiveKit secret.
    """

    model_config = ConfigDict(populate_by_name=True)

    #: Bounded and plain: it is stored in a 64-character column and forwarded
    #: to the agent, so anything longer or stranger is refused here as a 422
    #: rather than surfacing as a database error.
    agent_id: str = Field(
        alias="agentId", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"
    )
    #: A display name for the caller in the LiveKit room. Optional; defaults to
    #: a generated guest name.
    participant_name: str | None = Field(
        default=None, alias="participantName", max_length=64
    )
    #: Carry on an earlier conversation instead of starting a fresh one. The
    #: session keeps its room name, so the agent rejoins the same LangGraph
    #: thread and picks up where it left off rather than asking again.
    resume_session_id: uuid.UUID | None = Field(
        default=None, alias="resumeSessionId"
    )


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

    session_id: uuid.UUID = Field(alias="sessionId")
    agent_id: str = Field(alias="agentId")
    livekit: LiveKitTransport
    #: True when this call continues an earlier conversation.
    is_resuming: bool = Field(default=False, alias="isResuming")
    #: The transcript so far, oldest first. Empty for a new conversation. The
    #: app paints these into the UI before the call connects, so resuming looks
    #: like reopening a chat rather than starting one.
    previous_messages: list["MessageOut"] = Field(
        default_factory=list, alias="previousMessages"
    )


# --- history -------------------------------------------------------------
#
# Everything below is the history feature: listing past conversations, reading
# one back, and resuming it. Same camelCase-on-the-wire convention as above.


class SessionSummary(BaseModel):
    """One row of the history list. No transcript — see `SessionDetail`."""

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: uuid.UUID
    title: str | None
    #: The latest thing said, for the tile's second line.
    preview: str | None = None
    #: 'train' | 'flight' | 'stay', or null if the conversation never produced
    #: a result. The app maps it to an icon and to the filter chips.
    mode: str | None = None
    agent_id: str = Field(alias="agentId")
    #: Bookmarked from the chat screen. Drives the "Saved" filter.
    saved: bool = False
    #: Hearted from the history list. Drives the "Favourites" filter.
    favourite: bool = False
    #: The user's folder for it, or null.
    group_id: uuid.UUID | None = Field(default=None, alias="groupId")
    #: 'active' (still planning), 'completed' (trip taken) or 'archived'.
    status: str
    message_count: int = Field(alias="messageCount")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    ended_at: datetime | None = Field(default=None, alias="endedAt")


class SessionList(BaseModel):
    """A page of history.

    `total` is deliberately absent: counting every row to render a screen that
    shows twenty is work the database does not need to do on every scroll.
    `hasMore` answers the only question the UI actually asks.
    """

    model_config = ConfigDict(populate_by_name=True)

    sessions: list[SessionSummary]
    has_more: bool = Field(alias="hasMore")


class MessageOut(BaseModel):
    """One turn, as it is replayed into the UI."""

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    created_at: datetime = Field(alias="createdAt")


class TripResultOut(BaseModel):
    """A card the agent showed, exactly as it was shown at the time."""

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: uuid.UUID
    result_type: str = Field(alias="resultType")
    payload: dict
    created_at: datetime = Field(alias="createdAt")


class SessionDetail(SessionSummary):
    """One conversation, whole — what the app needs to replay it."""

    messages: list[MessageOut] = Field(default_factory=list)
    trip_results: list[TripResultOut] = Field(
        default_factory=list, alias="tripResults"
    )


class SessionUpdate(BaseModel):
    """Body for `PATCH /sessions/{id}`. Every field optional; send any.

    `groupId` is the one where absent and null differ: absent leaves the
    folder alone, `null` takes the conversation out of its folder. The service
    reads `model_fields_set` to tell them apart.
    """

    model_config = ConfigDict(populate_by_name=True)

    title: str | None = Field(default=None, min_length=1, max_length=200)
    #: 'active', 'completed' or 'archived'. There is no 'deleted' — DELETE
    #: deletes.
    status: str | None = Field(
        default=None, pattern="^(active|completed|archived)$"
    )
    #: Bookmark or un-bookmark. Independent of status: a conversation can be
    #: both saved and archived.
    saved: bool | None = None
    favourite: bool | None = None
    group_id: uuid.UUID | None = Field(default=None, alias="groupId")


class GroupIn(BaseModel):
    """Body for creating or renaming a group."""

    name: str = Field(min_length=1, max_length=40)


class GroupOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: uuid.UUID
    name: str
    #: How many of the user's conversations are in it — the chip shows it.
    count: int = 0
    created_at: datetime = Field(alias="createdAt")


class MessageIn(BaseModel):
    """Body for the internal route the agent posts each turn to.

    Keyed by room name rather than session id because the room is what the
    agent was handed when it was asked to join; it never learns our primary
    keys, and it should not have to.
    """

    model_config = ConfigDict(populate_by_name=True)

    room_name: str = Field(alias="roomName", max_length=128)
    role: str = Field(pattern="^(user|agent)$")
    #: Capped well above any spoken turn, so a runaway loop upstream cannot
    #: write megabytes into one row.
    content: str = Field(min_length=1, max_length=20_000)
    meta: dict = Field(default_factory=dict)


class TripResultIn(BaseModel):
    """Body for the internal route the agent posts a shown card to."""

    model_config = ConfigDict(populate_by_name=True)

    room_name: str = Field(alias="roomName", max_length=128)
    result_type: str = Field(alias="resultType", pattern="^(train|flight|stay)$")
    payload: dict


class TitleIn(BaseModel):
    """Body for the internal route the agent names a conversation with.

    Sent once, when a call ends and the agent has read back over what was
    said. Keyed by room for the same reason the message route is: the agent
    never learns our primary keys.
    """

    model_config = ConfigDict(populate_by_name=True)

    room_name: str = Field(alias="roomName", max_length=128)
    title: str = Field(min_length=1, max_length=80)


class EndIn(BaseModel):
    """Body for the internal route the agent reports a finished call on."""

    model_config = ConfigDict(populate_by_name=True)

    room_name: str = Field(alias="roomName", max_length=128)
    #: The bot reporting. A resume replaces the bot in a room, and the old
    #: one's report can land after the new call started; it is ignored.
    run_id: str | None = Field(default=None, alias="runId", max_length=64)


# SessionEnvelope refers to MessageOut, which is defined below it — resolve the
# forward reference now that both exist.
SessionEnvelope.model_rebuild()
