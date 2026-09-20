"""Every query against sessions, messages and trip results — and nothing else.

One rule holds this file together: **`user_id` is an argument, never an
assumption.** Each read and each write takes the caller's id and filters on it,
so "show me my history" and "show me session X" are the same kind of question
and neither can be answered about somebody else. A method that looked a session
up by id alone would work perfectly in testing and leak every conversation in
production, so there isn't one.

The agent is the exception that proves it. It writes messages for a session it
was told to join, with no user to be — so `session_for_agent` looks up by room
name instead, and the route that uses it is behind a service token rather than
a user's JWT. See `app/shared/auth.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import cast, delete, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domains.sessions.models import Message, Session, TripResult


class SessionRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    # --- writes -----------------------------------------------------------

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        room_name: str,
        agent_id: str,
        title: str | None = None,
    ) -> Session:
        row = Session(
            user_id=user_id,
            room_name=room_name,
            agent_id=agent_id,
            title=title,
        )
        self._db.add(row)
        await self._db.flush()  # populates id/created_at without committing
        return row

    async def add_message(
        self,
        *,
        session_id: uuid.UUID,
        role: str,
        content: str,
        meta: dict | None = None,
    ) -> Message:
        """Append a turn and move the session's counters in the same statement.

        `message_count` is bumped with `count + 1` computed by the database
        rather than read-then-write in Python. Both sides of a conversation can
        be logged at once — the user finishes talking while the agent is
        already answering — and two Python-side increments racing would each
        read the same number and both write it back, losing a turn.
        """
        row = Message(
            session_id=session_id, role=role, content=content, meta=meta or {}
        )
        self._db.add(row)
        await self._db.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(
                message_count=Session.message_count + 1,
                updated_at=func.now(),
            )
        )
        await self._db.flush()
        return row

    async def add_trip_result(
        self, *, session_id: uuid.UUID, result_type: str, payload: dict
    ) -> TripResult:
        row = TripResult(
            session_id=session_id, result_type=result_type, payload=payload
        )
        self._db.add(row)
        await self._db.flush()
        return row

    async def set_title(
        self, *, session_id: uuid.UUID, user_id: uuid.UUID, title: str
    ) -> bool:
        result = await self._db.execute(
            update(Session)
            .where(Session.id == session_id, Session.user_id == user_id)
            .values(title=title)
        )
        return result.rowcount > 0

    async def mark_title_source(
        self, *, session_id: uuid.UUID, user_id: uuid.UUID, source: str
    ) -> None:
        """Record who named this conversation — 'user' or 'agent'.

        Merged into the existing metadata with `||` rather than replacing it,
        so setting this does not wipe whatever else the JSONB holds.
        """
        await self._db.execute(
            update(Session)
            .where(Session.id == session_id, Session.user_id == user_id)
            .values(
                meta=Session.meta.op("||")(
                    cast({"title_source": source}, JSONB)
                )
            )
        )

    async def set_saved(
        self, *, session_id: uuid.UUID, user_id: uuid.UUID, saved: bool
    ) -> bool:
        result = await self._db.execute(
            update(Session)
            .where(Session.id == session_id, Session.user_id == user_id)
            .values(saved=saved)
        )
        return result.rowcount > 0

    async def set_status(
        self, *, session_id: uuid.UUID, user_id: uuid.UUID, status: str
    ) -> bool:
        result = await self._db.execute(
            update(Session)
            .where(Session.id == session_id, Session.user_id == user_id)
            .values(status=status)
        )
        return result.rowcount > 0

    async def end(self, *, session_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        result = await self._db.execute(
            update(Session)
            .where(Session.id == session_id, Session.user_id == user_id)
            .values(ended_at=datetime.now(timezone.utc))
        )
        return result.rowcount > 0

    async def delete(self, *, session_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Erase a conversation for good — row, transcript and cards.

        A real DELETE, not a status flag. Someone who deletes a conversation
        containing where they went and when has asked for it to be gone, and
        under both GDPR and India's DPDP Act that is a request we have to
        honour rather than hide. The messages and trip results go with it
        through `ON DELETE CASCADE`, in the database, so it cannot half-happen.
        """
        result = await self._db.execute(
            delete(Session).where(
                Session.id == session_id, Session.user_id == user_id
            )
        )
        return result.rowcount > 0

    async def delete_all_for_user(self, *, user_id: uuid.UUID) -> int:
        """Everything this user has. Account deletion calls this."""
        result = await self._db.execute(
            delete(Session).where(Session.user_id == user_id)
        )
        return result.rowcount

    # --- reads ------------------------------------------------------------

    async def list_for_user(
        self,
        *,
        user_id: uuid.UUID,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
        saved: bool | None = None,
    ) -> list[Session]:
        """The history screen. Newest first, without the transcripts.

        The messages are deliberately not loaded: the list shows a title and a
        date, and pulling every message of every conversation to render that
        is the difference between a screen that opens instantly and one that
        gets slower the more someone uses the app.
        """
        stmt = (
            select(Session)
            .where(
                Session.user_id == user_id,
                # A conversation nobody said anything in is not a conversation.
                #
                # The row is written the moment a call starts, before a word is
                # spoken, because the transcript needs somewhere to go. Open the
                # mic, change your mind, go back — and that leaves a row with
                # no title, no preview and nothing in it. Showing those means a
                # history list that fills up with "Untitled conversation"
                # every time someone taps and thinks better of it.
                #
                # Filtered on read rather than deleted, because the row is real
                # and the call may still be going: someone who starts talking
                # thirty seconds in should find their conversation waiting,
                # not discover it was tidied away.
                Session.message_count > 0,
            )
            .order_by(Session.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if status is not None:
            stmt = stmt.where(Session.status == status)
        if saved is not None:
            stmt = stmt.where(Session.saved == saved)
        return list((await self._db.execute(stmt)).scalars().all())

    async def count_for_user(
        self, *, user_id: uuid.UUID, since: datetime | None = None
    ) -> int:
        """How many conversations this user has had — the number the free-tier
        limit is checked against."""
        stmt = select(func.count(Session.id)).where(Session.user_id == user_id)
        if since is not None:
            stmt = stmt.where(Session.created_at >= since)
        return int((await self._db.execute(stmt)).scalar_one())

    async def get(
        self, *, session_id: uuid.UUID, user_id: uuid.UUID
    ) -> Session | None:
        """One session, by id, belonging to this user. Both halves required."""
        return (
            await self._db.execute(
                select(Session).where(
                    Session.id == session_id, Session.user_id == user_id
                )
            )
        ).scalar_one_or_none()

    async def get_with_history(
        self, *, session_id: uuid.UUID, user_id: uuid.UUID
    ) -> Session | None:
        """One session with its transcript and cards, for replay.

        `selectinload` rather than lazy access: these relationships would
        otherwise be loaded on first touch, which in async SQLAlchemy is not a
        slow query but a `MissingGreenlet` crash at render time.
        """
        return (
            await self._db.execute(
                select(Session)
                .where(Session.id == session_id, Session.user_id == user_id)
                .options(
                    selectinload(Session.messages),
                    selectinload(Session.trip_results),
                )
            )
        ).scalar_one_or_none()

    async def session_for_agent(self, *, room_name: str) -> Session | None:
        """Look up by room, for the agent writing its transcript.

        Not scoped by user on purpose — the agent has none. The room name is
        a server-minted UUID that never leaves the two services, and the route
        behind this is gated on the service token.
        """
        return (
            await self._db.execute(
                select(Session).where(Session.room_name == room_name)
            )
        ).scalar_one_or_none()
