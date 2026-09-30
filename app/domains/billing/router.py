"""Premium routes: the RevenueCat webhook, and the app asking where it stands."""

import hmac
import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_db_session
from app.domains.billing.schemas import PlanOut
from app.domains.billing.service import PlanService
from app.shared.auth import current_user
from app.shared.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])

#: Ten tries in ten minutes per person: plenty for typos, useless for guessing.
redeem_limiter = RateLimiter(limit=10, window=600, name="redeem")


class RedeemIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)


def _service(request: Request, db: AsyncSession, settings: Settings) -> PlanService:
    return PlanService(db, settings, request.app.state.http_client)


@router.get("/plan", response_model=PlanOut)
async def my_plan(
    request: Request,
    user_id: uuid.UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PlanOut:
    """Premium or not, and what is left of this month's free allowance."""
    return await _service(request, db, settings).plan_for(user_id)


@router.post("/sync", response_model=PlanOut)
async def sync_plan(
    request: Request,
    user_id: uuid.UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PlanOut:
    """Called by the app right after a purchase or restore, so Premium works
    at once rather than when the webhook lands."""
    return await _service(request, db, settings).sync(user_id)


@router.post("/redeem", response_model=PlanOut)
async def redeem_code(
    body: RedeemIn,
    request: Request,
    user_id: uuid.UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PlanOut:
    """Unlock Premium with a promo code (PROMO_CODES). 400 if it is not valid
    or used up; 429 after too many tries."""
    redeem_limiter.check(f"user:{user_id}")
    return await _service(request, db, settings).redeem(user_id, body.code)


@router.post("/revenuecat", status_code=status.HTTP_200_OK, include_in_schema=False)
async def revenuecat_webhook(
    request: Request,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """RevenueCat's webhook. Authenticated by the header value configured in
    its dashboard; disabled when REVENUECAT_WEBHOOK_AUTH is unset.

    Always 200 once authenticated — even for events about nobody we know —
    because anything else makes RevenueCat retry an event that will never
    mean more than it does now.
    """
    expected = settings.revenuecat_webhook_auth
    if not expected:
        raise HTTPException(503, "The RevenueCat webhook is not configured.")
    presented = authorization or ""
    if not (
        hmac.compare_digest(presented, expected)
        or hmac.compare_digest(presented, f"Bearer {expected}")
    ):
        raise HTTPException(401, "Bad webhook authorization.")
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Expected a JSON body.") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "Expected a JSON object.")
    await _service(request, db, settings).apply_webhook(body)
    return {"ok": True}
