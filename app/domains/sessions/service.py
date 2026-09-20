"""Session orchestration — the one genuinely multi-step operation here.

Starting a voice call is a composition: find or create the session row, mint a
token for its room, put the agent in that room, hand the token back. The router
does none of this; it just calls a method here and shapes the response.

**Order matters, and the order is deliberate.** The row is written before the
agent is asked to join, so a session always exists to attach a transcript to by
the time anyone can speak. But the transaction is not committed until the agent
has actually joined — if the agent is down, the call is going to fail, and a
history list full of conversations that never happened is worse than no row at
all. The router owns the commit; see the note on `start_session`.

**Resuming is the same operation with an older room.** A resumed session keeps
its `room_name`, which is also its LangGraph `thread_id`, so the agent rejoins
the train of thought instead of a blank one. Nothing else differs — which is
why there is one method rather than two.
"""

import logging
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domains.sessions.clients.agent import AgentClient
from app.domains.sessions.clients.livekit import LiveKitClient
from app.domains.sessions.repository import SessionRepository
from app.domains.sessions.schemas import (
    LiveKitTransport,
    MessageOut,
    SessionCreate,
    SessionDetail,
    SessionEnvelope,
    SessionList,
    SessionSummary,
    SessionUpdate,
)
from app.shared.exceptions import NotFoundError

logger = logging.getLogger(__name__)

#: How many messages of an old conversation to send back on resume. Enough to
#: fill the screen and give the user their bearings; not the whole history of a
#: long conversation over a phone connection. The full transcript is one
#: request away at GET /sessions/{id}.
RESUME_MESSAGE_LIMIT = 50


class SessionService:
    def __init__(
        self, http: httpx.AsyncClient, settings: Settings, db: AsyncSession
    ):
        self._livekit = LiveKitClient(settings)
        self._agent = AgentClient(http, settings)
        self._settings = settings
        self._repo = SessionRepository(db)
        self._db = db

    async def start_session(
        self, body: SessionCreate, *, user_id: uuid.UUID
    ) -> SessionEnvelope:
        """Start a new conversation, or carry on an old one.

        Commits only once the agent is in the room — see the module docstring.
        """
        previous: list[MessageOut] = []

        if body.resume_session_id is not None:
            session = await self._repo.get_with_history(
                session_id=body.resume_session_id, user_id=user_id
            )
            if session is None:
                # Scoped by user, so this is also the answer when the session
                # belongs to somebody else. Deliberately indistinguishable: a
                # 403 here would confirm that a given id exists.
                raise NotFoundError("That conversation could not be found.")
            is_resuming = True
            # Oldest first, capped — the UI replays them top to bottom.
            ordered = sorted(session.messages, key=lambda m: m.created_at)
            previous = [
                MessageOut.model_validate(m)
                for m in ordered[-RESUME_MESSAGE_LIMIT:]
            ]
        else:
            is_resuming = False
            room_name = f"backpac-{uuid.uuid4().hex[:16]}"
            session = await self._repo.create(
                user_id=user_id, room_name=room_name, agent_id=body.agent_id
            )

        participant = body.participant_name or f"guest-{uuid.uuid4().hex[:6]}"

        # 1. Mint the room token (raises ConfigurationError if LiveKit unset).
        token = self._livekit.create_access_token(
            session.room_name, participant
        )

        # 2. Put the agent in the room BEFORE returning, so the app never joins
        #    an empty room. If this raises, the whole request fails and the
        #    uncommitted session row is rolled back with it — better a clear
        #    error now than a silent room with no bot and a ghost in the
        #    history list.
        # A fresh run id every time, even when resuming.
        #
        # Two different identities meet here and it is worth being explicit
        # about which is which. `session.id` names a *conversation* and lives
        # forever. The id the agent wants names one *running bot process* and
        # dies when the call ends — it is the handle you would later POST to
        # /stop. Sending the conversation id as both meant resuming a chat
        # asked the agent to start a bot under an id it already had, which it
        # correctly refused with a 409, surfacing to the user as the thoroughly
        # misleading "Is BackPAC-Agent running?".
        #
        # What carries the conversation across calls is the room name, which is
        # also the LangGraph thread — not this.
        await self._agent.start(
            room_name=session.room_name,
            session_id=f"run_{uuid.uuid4().hex[:12]}",
            agent_id=session.agent_id,
            thread_id=session.room_name,
            is_resuming=is_resuming,
        )

        await self._db.commit()

        logger.info(
            "session %s %s for agent %s in room %s",
            session.id,
            "resumed" if is_resuming else "started",
            session.agent_id,
            session.room_name,
        )

        # 3. Hand the app what it needs to join, and what to put on screen.
        return SessionEnvelope(
            session_id=session.id,
            agent_id=session.agent_id,
            livekit=LiveKitTransport(
                url=self._settings.livekit_url,
                token=token,
                room_name=session.room_name,
            ),
            is_resuming=is_resuming,
            previous_messages=previous,
        )

    # --- history ----------------------------------------------------------

    async def list_sessions(
        self,
        *,
        user_id: uuid.UUID,
        limit: int,
        offset: int,
        status: str | None,
        saved: bool | None = None,
    ) -> SessionList:
        """A page of this user's history.

        Asks for one row more than requested and drops it: that is how you know
        whether there is a next page without a second COUNT query over the
        whole table.
        """
        rows = await self._repo.list_for_user(
            user_id=user_id,
            limit=limit + 1,
            offset=offset,
            status=status,
            saved=saved,
        )
        has_more = len(rows) > limit
        return SessionList(
            sessions=[SessionSummary.model_validate(r) for r in rows[:limit]],
            has_more=has_more,
        )

    async def get_session(
        self, *, session_id: uuid.UUID, user_id: uuid.UUID
    ) -> SessionDetail:
        session = await self._repo.get_with_history(
            session_id=session_id, user_id=user_id
        )
        if session is None:
            raise NotFoundError("That conversation could not be found.")
        detail = SessionDetail.model_validate(session)
        # The relationship comes back in whatever order the database found the
        # rows; a transcript has to be in the order it was said.
        detail.messages.sort(key=lambda m: m.created_at)
        detail.trip_results.sort(key=lambda t: t.created_at)
        return detail

    async def update_session(
        self, *, session_id: uuid.UUID, user_id: uuid.UUID, body: SessionUpdate
    ) -> SessionDetail:
        touched = False
        if body.title is not None:
            touched = await self._repo.set_title(
                session_id=session_id, user_id=user_id, title=body.title
            )
            # Remember whose name it is, so the agent's own titling leaves it
            # alone when the call ends. See set_agent_title.
            if touched:
                await self._repo.mark_title_source(
                    session_id=session_id, user_id=user_id, source="user"
                )
        if body.status is not None:
            touched = (
                await self._repo.set_status(
                    session_id=session_id, user_id=user_id, status=body.status
                )
                or touched
            )
        if body.saved is not None:
            touched = (
                await self._repo.set_saved(
                    session_id=session_id, user_id=user_id, saved=body.saved
                )
                or touched
            )
        if not touched:
            raise NotFoundError("That conversation could not be found.")
        await self._db.commit()
        return await self.get_session(session_id=session_id, user_id=user_id)

    async def delete_session(
        self, *, session_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        deleted = await self._repo.delete(
            session_id=session_id, user_id=user_id
        )
        if not deleted:
            raise NotFoundError("That conversation could not be found.")
        await self._db.commit()
        logger.info("session %s deleted by its owner", session_id)

    async def delete_everything_for_user(self, *, user_id: uuid.UUID) -> int:
        """Account deletion. Every conversation, transcript and card, gone.

        Apple requires an in-app path to this if you offer accounts at all
        (App Store guideline 5.1.1(v)), and GDPR/DPDP require it regardless of
        the store. Deleting the Supabase auth user is the app's half; this is
        ours.
        """
        count = await self._repo.delete_all_for_user(user_id=user_id)
        await self._db.commit()
        logger.info("erased %d session(s) for user %s", count, user_id)
        return count

    # --- written by the agent, not by a person ----------------------------

    async def log_message(
        self, *, room_name: str, role: str, content: str, meta: dict
    ) -> None:
        """Append a turn to whichever session owns this room.

        Silently ignores a room with no session. The agent can outlive the row
        — a user deleting a conversation mid-call is rare but entirely legal —
        and a 404 storm into the agent's logs helps nobody. The message is
        simply dropped, which is what the user asked for.
        """
        session = await self._repo.session_for_agent(room_name=room_name)
        if session is None:
            logger.info("dropping a %s message for unknown room %s", role, room_name)
            return

        await self._repo.add_message(
            session_id=session.id, role=role, content=content, meta=meta
        )

        # Name the conversation after the first thing the user said. It is the
        # single best summary available at zero cost, and it is what the
        # history list shows.
        if session.title is None and role == "user":
            session.title = _title_from(content)

        # The second line of the tile is the latest thing said, whoever said
        # it — the same convention every messaging app uses, and the one that
        # answers "where did I leave this?" at a glance. It changes as the
        # conversation grows, which keeps the list looking alive rather than
        # frozen at whatever was said first.
        session.preview = _preview_from(content)

        await self._db.commit()

    async def set_agent_title(self, *, room_name: str, title: str) -> None:
        """Name a conversation, from the agent, once it has ended.

        Overwrites the placeholder taken from the user's first sentence — that
        is a stand-in that exists so the list is never blank mid-call, and this
        is the real thing. It does not overwrite a name the *user* chose: a
        title set through PATCH is theirs, and having the agent quietly rename
        it afterwards would be the app arguing with them.
        """
        session = await self._repo.session_for_agent(room_name=room_name)
        if session is None:
            logger.info("dropping a title for unknown room %s", room_name)
            return
        if session.meta.get("title_source") == "user":
            logger.info("keeping the user's own title for session %s", session.id)
            return

        session.title = _title_from(title, limit=80)
        # JSONB is replaced wholesale rather than mutated: SQLAlchemy does not
        # see an in-place dict change, so `meta["x"] = y` alone would never be
        # written.
        session.meta = {**session.meta, "title_source": "agent"}
        await self._db.commit()
        logger.info("session %s named %r by the agent", session.id, session.title)

    async def log_trip_result(
        self, *, room_name: str, result_type: str, payload: dict
    ) -> None:
        session = await self._repo.session_for_agent(room_name=room_name)
        if session is None:
            logger.info("dropping a %s card for unknown room %s", result_type, room_name)
            return
        await self._repo.add_trip_result(
            session_id=session.id, result_type=result_type, payload=payload
        )
        # The first card decides what kind of trip this was. Later cards do not
        # change it: a conversation that looked at trains and then at hotels is
        # still, to the person scanning their history, the trains one.
        if session.mode is None:
            session.mode = result_type
        await self._db.commit()


def _preview_from(text: str, *, limit: int = 155) -> str:
    """One line of a message, for the history tile.

    Whitespace collapsed first: a transcript can arrive with newlines in it,
    and a tile is one line tall, so an un-collapsed string would render as its
    first few words followed by nothing.
    """
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else f"{clean[:limit].rstrip()}\u2026"


def _title_from(text: str, *, limit: int = 60) -> str:
    """A conversation's name, from its opening line.

    Cut on a word boundary rather than mid-word: "Trains to Varanasi over…"
    reads like a title, "Trains to Varanasi ove" reads like a bug.
    """
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    cut = clean[:limit].rsplit(" ", 1)[0]
    return f"{cut or clean[:limit]}…"
