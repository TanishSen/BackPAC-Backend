"""HTTP routes for sessions. Thin on purpose.

A router does three things and nothing else: read the request, call a service,
shape the response. No business logic, no SQL, no SDK calls. If you find
yourself writing an `if` about trip data here, it belongs in the service.

**Two doors, two keys.** Everything under `/sessions` is a signed-in person's
own data and depends on `current_user`, which turns a Supabase JWT into a user
id or raises 401. The `/sessions/internal/*` routes are the agent writing a
transcript while a call is in progress; it is a server with no user to be, so
it presents a shared secret instead. Neither door accepts the other's key.
"""

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db_session
from app.domains.sessions.schemas import (
    EndIn,
    GroupIn,
    GroupOut,
    MessageIn,
    SessionCreate,
    SessionDetail,
    SessionEnvelope,
    SessionList,
    SessionSummary,
    SessionUpdate,
    TitleIn,
    TripResultIn,
)
from app.domains.sessions.service import SessionService
from app.shared.auth import current_user, require_service
from app.shared.rate_limit import RateLimiter

router = APIRouter(prefix="/sessions", tags=["sessions"])

_settings = get_settings()
session_start_limiter = RateLimiter(
    limit=_settings.session_start_limit,
    window=_settings.session_start_window_seconds,
    name="session-start",
)


def _service(request: Request, db: AsyncSession) -> SessionService:
    """The shared httpx client lives on `request.app.state.http_client` (set up
    in main.py's lifespan) so we reuse one connection pool."""
    return SessionService(request.app.state.http_client, get_settings(), db)


@router.post("", response_model=SessionEnvelope)
async def create_session(
    body: SessionCreate,
    request: Request,
    user_id: uuid.UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
) -> SessionEnvelope:
    """Start a voice session — or resume one — and return the room to join.

    Send `resumeSessionId` to carry on an earlier conversation; the response
    then carries `isResuming: true` and the transcript so far.

    Limited per user (429 with `Retry-After`): every call puts a billable bot
    in a room.
    """
    session_start_limiter.check(f"user:{user_id}")
    return await _service(request, db).start_session(body, user_id=user_id)


@router.get("", response_model=SessionList)
async def list_sessions(
    request: Request,
    user_id: uuid.UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status_filter: str | None = Query(
        default=None, alias="status", pattern="^(active|completed|archived)$"
    ),
    saved: bool | None = Query(default=None),
    favourite: bool | None = Query(default=None),
    group_id: uuid.UUID | None = Query(default=None, alias="groupId"),
    mode: str | None = Query(default=None, pattern="^(train|flight|stay)$"),
) -> SessionList:
    """This user's conversations, newest first. The history screen.

    Narrow with `?saved=true`, `?favourite=true`, `?groupId=…`,
    `?mode=train|flight|stay` or `?status=active|completed|archived`; they
    combine.
    """
    return await _service(request, db).list_sessions(
        user_id=user_id,
        limit=limit,
        offset=offset,
        status=status_filter,
        saved=saved,
        favourite=favourite,
        group_id=group_id,
        mode=mode,
    )


@router.get("/{session_id:uuid}", response_model=SessionDetail)
async def get_session(
    session_id: uuid.UUID,
    request: Request,
    user_id: uuid.UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
) -> SessionDetail:
    """One conversation in full: transcript and the cards it produced."""
    return await _service(request, db).get_session(
        session_id=session_id, user_id=user_id
    )


@router.patch("/{session_id:uuid}", response_model=SessionDetail)
async def update_session(
    session_id: uuid.UUID,
    body: SessionUpdate,
    request: Request,
    user_id: uuid.UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
) -> SessionDetail:
    """Rename, save, favourite, file in a group (`groupId`, or null to take it
    out), or set its status (`active` planning, `completed` taken, `archived`)."""
    return await _service(request, db).update_session(
        session_id=session_id, user_id=user_id, body=body
    )


@router.post("/{session_id:uuid}/end", response_model=SessionSummary)
async def end_session(
    session_id: uuid.UUID,
    request: Request,
    user_id: uuid.UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
) -> SessionSummary:
    """Hang up: mark the call ended and release the agent straight away.

    Idempotent. Optional for the app — the agent notices the caller leaving
    and records the end itself — but calling it frees the bot immediately.
    """
    return await _service(request, db).end_session(
        session_id=session_id, user_id=user_id
    )


@router.delete("/{session_id:uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    request: Request,
    user_id: uuid.UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Erase a conversation — the row, its transcript and its cards.

    A real delete, not an archive. `PATCH {"status": "archived"}` is the
    reversible one.
    """
    await _service(request, db).delete_session(
        session_id=session_id, user_id=user_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all_sessions(
    request: Request,
    user_id: uuid.UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Erase everything this user has ever said here.

    The server half of account deletion — the app deletes the Supabase auth
    user, this removes what we stored about them. Also the honest answer to a
    GDPR/DPDP erasure request.
    """
    await _service(request, db).delete_everything_for_user(user_id=user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- internal: the agent writing as it talks -----------------------------
#
# Not for the app. These take a service token, never a user token, and they
# identify the conversation by room name because that is all the agent knows.


@router.post(
    "/internal/messages",
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
    dependencies=[Depends(require_service)],
)
async def log_message(
    body: MessageIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Append one turn to a live conversation.

    202 rather than 201: the agent does not wait to find out where the message
    landed, and must not — it is in the middle of a phone call.
    """
    await _service(request, db).log_message(
        room_name=body.room_name,
        role=body.role,
        content=body.content,
        meta=body.meta,
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/internal/title",
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
    dependencies=[Depends(require_service)],
)
async def set_title(
    body: TitleIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Name a conversation, once the agent has seen how it went."""
    await _service(request, db).set_agent_title(
        room_name=body.room_name, title=body.title
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/internal/end",
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
    dependencies=[Depends(require_service)],
)
async def agent_ended(
    body: EndIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """The agent's call in this room is over. Records `endedAt`."""
    await _service(request, db).agent_ended(
        room_name=body.room_name, run_id=body.run_id
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/internal/trip-results",
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
    dependencies=[Depends(require_service)],
)
async def log_trip_result(
    body: TripResultIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Record a card the agent put on screen, as it was shown."""
    await _service(request, db).log_trip_result(
        room_name=body.room_name,
        result_type=body.result_type,
        payload=body.payload,
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)


# --- groups: the user's folders of conversations -----------------------------

groups_router = APIRouter(prefix="/groups", tags=["sessions"])


@groups_router.get("", response_model=list[GroupOut])
async def list_groups(
    request: Request,
    user_id: uuid.UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[GroupOut]:
    """This user's groups, in the order they made them, with counts."""
    return await _service(request, db).list_groups(user_id=user_id)


@groups_router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
async def create_group(
    body: GroupIn,
    request: Request,
    user_id: uuid.UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
) -> GroupOut:
    return await _service(request, db).create_group(user_id=user_id, body=body)


@groups_router.patch("/{group_id:uuid}", response_model=GroupOut)
async def rename_group(
    group_id: uuid.UUID,
    body: GroupIn,
    request: Request,
    user_id: uuid.UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
) -> GroupOut:
    return await _service(request, db).rename_group(
        group_id=group_id, user_id=user_id, body=body
    )


@groups_router.delete("/{group_id:uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: uuid.UUID,
    request: Request,
    user_id: uuid.UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Delete the group. Its conversations stay, ungrouped."""
    await _service(request, db).delete_group(group_id=group_id, user_id=user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
