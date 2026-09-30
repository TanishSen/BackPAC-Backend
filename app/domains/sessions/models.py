"""Database tables for the sessions domain — one conversation and its contents.

Three tables, one shape: a `Session` is one conversation, and everything else
hangs off it and dies with it. Deleting a session cascades to its messages and
its trip results, in the database rather than in Python, so an erasure request
cannot half-succeed.

**Who a row belongs to.** `user_id` is the UUID from Supabase Auth's
`auth.users.id` — the `sub` claim of the JWT the app sends. There is no users
table here and there should not be one; Supabase owns identity. That also means
there is no foreign key to it: `auth.users` lives in another schema that this
app does not migrate, and pointing at it from a migration would couple our
schema to theirs. Scoping is enforced in the repository instead — every query
filters on `user_id`, every time.

**Why the room name is unique.** LiveKit rooms and LangGraph threads are both
keyed by it, so two sessions sharing one would silently merge two people's
conversations. The database refuses rather than trusting the code.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Session(Base):
    """One conversation: one LiveKit room, one LangGraph thread, one history."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    #: `auth.users.id` from Supabase — the JWT's `sub`. Not a foreign key; see
    #: the module docstring.
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)

    #: The LiveKit room. Also the LangGraph `thread_id`, which is why resuming
    #: a session puts the agent back into the same train of thought rather than
    #: a fresh one. One column, deliberately, so the two can never disagree.
    room_name: Mapped[str] = mapped_column(String(128), unique=True)

    #: Which agent was on the call, e.g. "trip-planner".
    agent_id: Mapped[str] = mapped_column(String(64))

    #: What the history list shows. Null until the conversation has enough in
    #: it to name — an untitled row is better than "New conversation" repeated
    #: down the screen.
    title: Mapped[str | None] = mapped_column(String(200), default=None)

    #: The second line of the history tile — the last thing said, either side.
    #:
    #: Stored rather than joined. The list shows twenty of these at a time, and
    #: fetching the newest message per row is either twenty queries or a
    #: window function over the whole messages table; keeping a copy costs one
    #: extra column on an UPDATE that was happening anyway.
    preview: Mapped[str | None] = mapped_column(String(160), default=None)

    #: What kind of trip this turned out to be: 'train', 'flight', 'stay'.
    #:
    #: Set from the first card the agent shows, because that is the first
    #: moment the conversation has actually committed to a kind. Null until
    #: then — a chat that never got as far as a result genuinely has no mode,
    #: and guessing one from the opening line would put a train icon on a
    #: conversation about hotels.
    mode: Mapped[str | None] = mapped_column(String(16), default=None)

    #: Bookmarked by the user, from the chat screen's save button.
    #:
    #: A column rather than a status, because it is not one: a conversation can
    #: be saved *and* archived, and the two answer different questions —
    #: "I want to find this again" versus "I am done with this". Folding them
    #: into one field would mean archiving something silently unsaved it.
    saved: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    #: Hearted by the user, from the history list. Another axis again, not a
    #: synonym for `saved`: "saved" is "keep this to come back to", a
    #: favourite is "I loved this one" — the profile counts them separately.
    favourite: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    #: The user's own folder for this conversation ("Mountains", "Honeymoon"),
    #: or null. One group per conversation, like a folder: nulled rather than
    #: deleted with it when the group goes, because deleting a folder should
    #: not delete what was in it.
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("session_groups.id", ondelete="SET NULL"),
        default=None,
        index=True,
    )

    #: 'active' while it is still being planned, 'completed' once the user
    #: says they took the trip, 'archived' once they have put it away. All
    #: three can be resumed. There is no 'deleted': a delete is a delete.
    status: Mapped[str] = mapped_column(String(16), default="active")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    #: Set when the call finishes. Null means it is still open — or that it was
    #: never closed cleanly, which is the same thing from here.
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    #: Kept as a column rather than counted on read: the history list shows it
    #: for every row, and a COUNT per row is the classic N+1 that makes that
    #: screen slow exactly when someone has enough history to care.
    message_count: Mapped[int] = mapped_column(Integer, default=0)

    #: Room for things that are not worth a column yet — which specialist ran,
    #: how many trips were planned. Never queried on; if you need to query it,
    #: it has earned a column.
    meta: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, server_default="{}"
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    trip_results: Mapped[list[TripResult]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        # The history list, exactly: this user's sessions, newest first. A
        # composite index because filtering by user and then sorting by date is
        # one operation to Postgres given the right index, and two without it.
        Index("ix_sessions_user_created", "user_id", "created_at"),
    )


class Message(Base):
    """One turn of a conversation, as it was spoken."""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        index=True,
    )

    #: 'user' or 'agent'. A string rather than an enum: adding a third speaker
    #: later should not need a migration lock on a table this size.
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    #: e.g. {"tool": "search_trains"} — what the agent did to produce this.
    meta: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, server_default="{}"
    )

    session: Mapped[Session] = relationship(back_populates="messages")

    __table_args__ = (
        # Replaying a conversation reads it in order, so the index carries the
        # order too and the read needs no sort.
        Index("ix_messages_session_created", "session_id", "created_at"),
    )


class TripResult(Base):
    """A card the agent put on screen — a train, a flight, a stay.

    Stored as the payload that was actually shown, not as a reference to a
    live search. Prices and availability move; what the user was offered on
    Tuesday should still read as it did on Tuesday when they open it on Friday.
    """

    __tablename__ = "trip_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        index=True,
    )

    #: 'train' | 'flight' | 'stay'
    result_type: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    session: Mapped[Session] = relationship(back_populates="trip_results")


class SessionGroup(Base):
    """A folder of conversations, named by the user — "Mountains", "Desert"."""

    __tablename__ = "session_groups"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: Owner, the JWT's `sub`. Scoped in the repository like everything else.
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    name: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        # Two folders both called "Mountains" is always a mistake.
        UniqueConstraint("user_id", "name", name="uq_session_groups_user_name"),
    )
