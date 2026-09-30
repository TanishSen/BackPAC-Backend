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

from app.domains.sessions.models import Message, Session, SessionGroup, TripResult


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

    async def mark_started(self, *, session_id: uuid.UUID, run_id: str) -> None:
        """A bot is now in this conversation's room: record which, and open it.

        An explicit UPDATE rather than setting attributes on the ORM object:
        `ended_at = None` on an object that already reads None is no change to
        the ORM and writes nothing — but the row may have been ended since we
        read it, by the bot this call just replaced reporting its own hang-up.
        `||` merges the run id into the JSONB rather than replacing it, so a
        title source written meanwhile survives.
        """
        await self._db.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(
                ended_at=None,
                meta=Session.meta.op("||")(
                    cast({"agent_run_id": run_id}, JSONB)
                ),
            )
            .execution_options(synchronize_session=False)
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

    async def set_favourite(
        self, *, session_id: uuid.UUID, user_id: uuid.UUID, favourite: bool
    ) -> bool:
        result = await self._db.execute(
            update(Session)
            .where(Session.id == session_id, Session.user_id == user_id)
            .values(favourite=favourite)
        )
        return result.rowcount > 0

    async def set_group(
        self,
        *,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        group_id: uuid.UUID | None,
    ) -> bool:
        """File a conversation in a group, or (None) take it out of one.

        The caller has already checked the group is this user's; the session
        is scoped here as always.
        """
        result = await self._db.execute(
            update(Session)
            .where(Session.id == session_id, Session.user_id == user_id)
            .values(group_id=group_id)
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
        await self._db.execute(
            delete(SessionGroup).where(SessionGroup.user_id == user_id)
        )
        return result.rowcount

    # --- groups -------------------------------------------------------------

    async def list_groups(self, *, user_id: uuid.UUID) -> list[tuple[SessionGroup, int]]:
        """This user's groups, oldest first (the order they made them), each
        with how many non-empty conversations are in it."""
        counts = (
            select(Session.group_id, func.count(Session.id).label("n"))
            .where(Session.user_id == user_id, Session.message_count > 0)
            .group_by(Session.group_id)
            .subquery()
        )
        stmt = (
            select(SessionGroup, func.coalesce(counts.c.n, 0))
            .outerjoin(counts, counts.c.group_id == SessionGroup.id)
            .where(SessionGroup.user_id == user_id)
            .order_by(SessionGroup.created_at, SessionGroup.name)
        )
        return [(g, int(n)) for g, n in (await self._db.execute(stmt)).all()]

    async def get_group(
        self, *, group_id: uuid.UUID, user_id: uuid.UUID
    ) -> SessionGroup | None:
        return (
            await self._db.execute(
                select(SessionGroup).where(
                    SessionGroup.id == group_id, SessionGroup.user_id == user_id
                )
            )
        ).scalar_one_or_none()

    async def find_group_by_name(
        self, *, name: str, user_id: uuid.UUID
    ) -> SessionGroup | None:
        return (
            await self._db.execute(
                select(SessionGroup).where(
                    SessionGroup.user_id == user_id,
                    func.lower(SessionGroup.name) == name.lower(),
                )
            )
        ).scalar_one_or_none()

    async def count_groups(self, *, user_id: uuid.UUID) -> int:
        return int(
            (
                await self._db.execute(
                    select(func.count(SessionGroup.id)).where(
                        SessionGroup.user_id == user_id
                    )
                )
            ).scalar_one()
        )

    async def create_group(self, *, user_id: uuid.UUID, name: str) -> SessionGroup:
        row = SessionGroup(user_id=user_id, name=name)
        self._db.add(row)
        await self._db.flush()
        return row

    async def rename_group(
        self, *, group_id: uuid.UUID, user_id: uuid.UUID, name: str
    ) -> bool:
        result = await self._db.execute(
            update(SessionGroup)
            .where(SessionGroup.id == group_id, SessionGroup.user_id == user_id)
            .values(name=name)
        )
        return result.rowcount > 0

    async def delete_group(self, *, group_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """The conversations stay; `ON DELETE SET NULL` takes them out of it."""
        result = await self._db.execute(
            delete(SessionGroup).where(
                SessionGroup.id == group_id, SessionGroup.user_id == user_id
            )
        )
        return result.rowcount > 0

    # --- the profile's numbers ----------------------------------------------

    async def stats_for_user(self, *, user_id: uuid.UUID) -> dict[str, int]:
        """Everything the profile counts, in two queries.

        Only conversations someone actually spoke in count — the same rule the
        history list uses, so the numbers match what the lists show.

        "Places" is the distinct destinations the agent searched, read from
        the query each card was made from. Cards stored before the agent began
        recording its query carry none and are not counted: an undercount that
        heals, rather than a guess.
        """
        spoke = Session.message_count > 0
        row = (
            await self._db.execute(
                select(
                    func.count(Session.id).filter(spoke),
                    func.count(Session.id).filter(spoke, Session.saved.is_(True)),
                    func.count(Session.id).filter(spoke, Session.favourite.is_(True)),
                    func.count(Session.id).filter(spoke, Session.status == "completed"),
                    func.count(Session.id).filter(spoke, Session.status == "active"),
                ).where(Session.user_id == user_id)
            )
        ).one()
        destination = func.lower(
            func.trim(TripResult.payload["query"]["destination"].astext)
        )
        places = (
            await self._db.execute(
                select(func.count(func.distinct(destination)))
                .select_from(TripResult)
                .join(Session, Session.id == TripResult.session_id)
                .where(
                    Session.user_id == user_id,
                    TripResult.payload["query"]["destination"].astext.is_not(None),
                    destination != "",
                )
            )
        ).scalar_one()
        trips, saved, favourites, completed, in_progress = (int(v) for v in row)
        return {
            "trips": trips,
            "places": int(places),
            "saved": saved,
            "favourites": favourites,
            "completed": completed,
            "in_progress": in_progress,
        }

    async def count_plans_since(self, *, user_id: uuid.UUID, since: datetime) -> int:
        """Conversations started since `since` that anyone spoke in — what the
        free monthly allowance counts. Opening the mic and backing out is not a
        trip plan and costs nothing against it."""
        return int(
            (
                await self._db.execute(
                    select(func.count(Session.id)).where(
                        Session.user_id == user_id,
                        Session.created_at >= since,
                        Session.message_count > 0,
                    )
                )
            ).scalar_one()
        )

    # --- reads ------------------------------------------------------------

    async def list_for_user(
        self,
        *,
        user_id: uuid.UUID,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
        saved: bool | None = None,
        favourite: bool | None = None,
        group_id: uuid.UUID | None = None,
        mode: str | None = None,
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
        if favourite is not None:
            stmt = stmt.where(Session.favourite == favourite)
        if group_id is not None:
            stmt = stmt.where(Session.group_id == group_id)
        if mode is not None:
            stmt = stmt.where(Session.mode == mode)
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
