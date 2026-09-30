"""The signed-in person's account as a whole: their profile, and deleting it.

Both app stores require that an app which lets you create an account also lets
you delete it, from inside the app, and that doing so closes the account — not
just hides it (App Store 5.1.1(v), Google Play's account deletion policy).
GDPR and India's DPDP Act ask the same of the data behind it.

Two halves, in this order:

1. **Our data** — every conversation, transcript, card and saved trip. Erased
   and committed first. If the second half then fails, the person can simply
   try again: they are still signed in, and erasing nothing twice is harmless.
   The other order would strand data behind an account that can no longer
   sign in to ask for it to be removed.
2. **The sign-in account** in Supabase Auth, through its admin API. Needs
   `SUPABASE_SERVICE_ROLE_KEY`; without it the response says the account was
   left in place, so the app can tell the person the truth.
"""

import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_db_session
from app.domains.account.models import Profile
from app.domains.account.schemas import MeOut, ProfileOut, ProfileUpdate, StatsOut
from app.domains.billing.service import PlanService
from app.domains.sessions.repository import SessionRepository
from app.domains.sessions.service import SessionService
from app.domains.trips.models import SavedTrip
from app.shared.auth import current_user
from app.shared.exceptions import UpstreamError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/account", tags=["account"])
me_router = APIRouter(prefix="/me", tags=["account"])


async def _profile(db: AsyncSession, user_id: uuid.UUID) -> ProfileOut:
    row = (
        await db.execute(select(Profile).where(Profile.user_id == user_id))
    ).scalar_one_or_none()
    return ProfileOut.model_validate(row) if row else ProfileOut()


async def _me(
    request: Request, db: AsyncSession, settings: Settings, user_id: uuid.UUID
) -> MeOut:
    stats = await SessionRepository(db).stats_for_user(user_id=user_id)
    bucket = (
        await db.execute(
            select(func.count(SavedTrip.id)).where(SavedTrip.user_ref == str(user_id))
        )
    ).scalar_one()
    plan = await PlanService(db, settings, request.app.state.http_client).plan_for(
        user_id
    )
    return MeOut(
        profile=await _profile(db, user_id),
        stats=StatsOut(**stats, bucket_list=int(bucket)),
        plan=plan,
    )


@me_router.get("", response_model=MeOut)
async def get_me(
    request: Request,
    user_id: uuid.UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> MeOut:
    """The profile screen in one request: profile, journey numbers, plan."""
    return await _me(request, db, settings, user_id)


@me_router.patch("", response_model=MeOut)
async def update_me(
    body: ProfileUpdate,
    request: Request,
    user_id: uuid.UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> MeOut:
    """Edit profile. Only the fields sent change; "" clears one."""
    changes = {
        field: (" ".join(value.split()) or None) if isinstance(value, str) else value
        for field, value in body.model_dump(exclude_unset=True).items()
    }
    if changes:
        stmt = insert(Profile).values(user_id=user_id, **changes)
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=[Profile.user_id],
                set_={**{k: stmt.excluded[k] for k in changes}, "updated_at": func.now()},
            )
        )
        await db.commit()
    return await _me(request, db, settings, user_id)


class AccountDeleted(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    #: Conversations, transcripts, cards and saved trips: gone.
    data_erased: bool = Field(alias="dataErased")
    #: The Supabase sign-in account: closed. False only when this server is
    #: not configured to close accounts.
    account_deleted: bool = Field(alias="accountDeleted")


@router.delete("", response_model=AccountDeleted)
async def delete_account(
    request: Request,
    user_id: uuid.UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AccountDeleted:
    """Delete the caller's account: all their data, then their sign-in."""
    http: httpx.AsyncClient = request.app.state.http_client
    # Profile and plan first, in the same transaction the sessions commit.
    await db.execute(delete(Profile).where(Profile.user_id == user_id))
    await PlanService(db, settings, http).forget(user_id)
    await SessionService(http, settings, db).delete_everything_for_user(
        user_id=user_id
    )

    if not settings.supabase_service_role_key:
        logger.warning(
            "erased data for %s but cannot close the sign-in account: "
            "SUPABASE_SERVICE_ROLE_KEY is unset",
            user_id,
        )
        return AccountDeleted(data_erased=True, account_deleted=False)

    key = settings.supabase_service_role_key
    try:
        resp = await http.delete(
            f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        logger.error("could not reach Supabase to delete %s: %s", user_id, exc)
        raise UpstreamError(
            "Your data was erased, but closing the account failed. Please try again."
        ) from exc

    # 404: already gone — a retry after a response that never arrived.
    if resp.status_code not in (200, 204, 404):
        logger.error(
            "Supabase refused to delete %s (%s): %s",
            user_id,
            resp.status_code,
            resp.text[:200],
        )
        raise UpstreamError(
            "Your data was erased, but closing the account failed. Please try again."
        )

    logger.info("account %s deleted by its owner", user_id)
    return AccountDeleted(data_erased=True, account_deleted=True)
