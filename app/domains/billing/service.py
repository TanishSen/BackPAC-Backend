"""Premium: who has it, keeping our copy of that fresh, and the free allowance.

RevenueCat is the source of truth. It talks to the App Store and Google Play,
turns their receipts into one answer — "has the `premium` entitlement until
this date" — and tells us about changes by webhook. We store that answer per
user (`entitlements`) so a call can start without asking RevenueCat first, and
refresh it:

- after a purchase or restore, when the app calls `POST /billing/sync`, and
- whenever a webhook arrives.

Both refreshes go through `_refresh`, which asks RevenueCat's REST API for the
subscriber's current state rather than trusting an event's contents. That is
what RevenueCat recommends — events can arrive late, twice, or out of order,
and "what is true now" is immune to all three. Without a secret key the
webhook's own fields are applied instead, which is correct for the ordinary
events and blind to transfers.

The RevenueCat app user id is the Supabase user id; the app logs in with it.
Anything else (anonymous `$RCAnonymousID:` ids) is not an account of ours.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domains.account.models import Entitlement
from app.domains.billing.schemas import PlanOut
from app.domains.sessions.repository import SessionRepository
from app.shared.exceptions import PaymentRequiredError

logger = logging.getLogger(__name__)

RC_API = "https://api.revenuecat.com/v1"

#: A lifetime purchase has no expiry. Stored as a date far enough away to
#: never matter, so "is it active" stays one comparison.
_FOREVER = datetime(9999, 1, 1, tzinfo=timezone.utc)


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _parse_time(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_user_id(raw: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        return None


class PlanService:
    def __init__(self, db: AsyncSession, settings: Settings, http: httpx.AsyncClient):
        self._db = db
        self._settings = settings
        self._http = http

    # --- reading -------------------------------------------------------------

    async def _entitlement(self, user_id: uuid.UUID) -> Entitlement | None:
        return (
            await self._db.execute(
                select(Entitlement).where(Entitlement.user_id == user_id)
            )
        ).scalar_one_or_none()

    async def plan_for(self, user_id: uuid.UUID) -> PlanOut:
        now = datetime.now(timezone.utc)
        ent = await self._entitlement(user_id)
        premium = bool(ent and ent.premium_until and ent.premium_until > now)
        limit = self._settings.free_monthly_trip_plans
        used = await SessionRepository(self._db).count_plans_since(
            user_id=user_id, since=_month_start(now)
        )
        unlimited = premium or limit <= 0
        return PlanOut(
            billing_enabled=self._settings.billing_enabled,
            premium=premium,
            premium_until=(
                ent.premium_until
                if ent and ent.premium_until and ent.premium_until != _FOREVER
                else None
            ),
            will_renew=bool(ent and ent.will_renew and premium),
            product_id=ent.product_id if premium and ent else None,
            free_monthly_limit=None if unlimited else limit,
            used_this_month=used,
            remaining_this_month=None if unlimited else max(0, limit - used),
        )

    async def assert_can_start(self, user_id: uuid.UUID) -> None:
        """Raise 402 if a free account has used this month's new trip plans.

        Only a *new* conversation counts: resuming one is carrying on a plan
        already started, and must always work.
        """
        if not self._settings.billing_enabled:
            return
        plan = await self.plan_for(user_id)
        if plan.remaining_this_month is not None and plan.remaining_this_month <= 0:
            raise PaymentRequiredError(
                f"You've used your {plan.free_monthly_limit} free trip plans "
                "this month. Upgrade to Premium for unlimited plans, or pick "
                "up one of your earlier conversations."
            )

    # --- writing ----------------------------------------------------------------

    async def _store(
        self,
        user_id: uuid.UUID,
        *,
        premium_until: datetime | None,
        product_id: str | None,
        will_renew: bool,
    ) -> None:
        stmt = insert(Entitlement).values(
            user_id=user_id,
            premium_until=premium_until,
            product_id=product_id,
            will_renew=will_renew,
        )
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[Entitlement.user_id],
                set_={
                    "premium_until": stmt.excluded.premium_until,
                    "product_id": stmt.excluded.product_id,
                    "will_renew": stmt.excluded.will_renew,
                    "updated_at": datetime.now(timezone.utc),
                },
            )
        )

    async def _refresh(self, user_id: uuid.UUID) -> bool:
        """Ask RevenueCat what is true now and store it. False if we could not."""
        key = self._settings.revenuecat_secret_key
        if not key:
            return False
        try:
            resp = await self._http.get(
                f"{RC_API}/subscribers/{user_id}",
                headers={"Authorization": f"Bearer {key}"},
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            logger.warning("RevenueCat unreachable for %s: %s", user_id, exc)
            return False
        if resp.status_code != 200:
            logger.warning(
                "RevenueCat refused subscriber %s (%s)", user_id, resp.status_code
            )
            return False

        subscriber = (resp.json() or {}).get("subscriber") or {}
        ent = (subscriber.get("entitlements") or {}).get(
            self._settings.premium_entitlement_id
        )
        if not ent:
            await self._store(user_id, premium_until=None, product_id=None, will_renew=False)
            return True

        product = ent.get("product_identifier")
        expires = _parse_time(ent.get("expires_date"))
        sub = (subscriber.get("subscriptions") or {}).get(product) or {}
        await self._store(
            user_id,
            premium_until=expires or _FOREVER,
            product_id=product,
            # Renews unless the store has told RevenueCat it was cancelled.
            will_renew=bool(expires) and not sub.get("unsubscribe_detected_at"),
        )
        return True

    async def sync(self, user_id: uuid.UUID) -> PlanOut:
        """After a purchase or restore: refresh from RevenueCat, then answer."""
        if await self._refresh(user_id):
            await self._db.commit()
        return await self.plan_for(user_id)

    async def apply_webhook(self, body: dict) -> None:
        event = body.get("event") or {}
        kind = str(event.get("type", ""))
        if kind == "TEST":
            logger.info("RevenueCat test webhook received")
            return

        # Everyone the event concerns. A transfer moves a purchase from one
        # account to another, so both ends need refreshing.
        raw_ids = [
            event.get("app_user_id"),
            event.get("original_app_user_id"),
            *(event.get("aliases") or []),
            *(event.get("transferred_from") or []),
            *(event.get("transferred_to") or []),
        ]
        users = {u for u in (_as_user_id(r) for r in raw_ids) if u is not None}
        if not users:
            logger.info("RevenueCat %s for no account of ours; ignored", kind)
            return

        if self._settings.revenuecat_secret_key:
            for user in users:
                await self._refresh(user)
            await self._db.commit()
            return

        # No secret key: apply the event as sent. Right for purchases,
        # renewals, cancellations and expiry; a transfer cannot be resolved
        # without asking RevenueCat, and is logged instead.
        user = _as_user_id(event.get("app_user_id"))
        entitlements = event.get("entitlement_ids")
        concerns_us = entitlements is None or (
            self._settings.premium_entitlement_id in entitlements
        )
        if user is None or not concerns_us or kind == "TRANSFER":
            logger.warning(
                "RevenueCat %s not applied (set REVENUECAT_SECRET_KEY to resolve it)",
                kind,
            )
            return
        ms = event.get("expiration_at_ms")
        expires = (
            datetime.fromtimestamp(ms / 1000, tz=timezone.utc) if ms else _FOREVER
        )
        await self._store(
            user,
            premium_until=expires,
            product_id=event.get("product_id"),
            will_renew=kind
            not in ("CANCELLATION", "EXPIRATION", "BILLING_ISSUE", "SUBSCRIPTION_PAUSED")
            and ms is not None,
        )
        await self._db.commit()

    async def forget(self, user_id: uuid.UUID) -> None:
        """Account deletion: our copy goes, and RevenueCat's customer record
        too when we hold a key. The store subscription itself can only be
        cancelled by its owner — the app tells them so."""
        await self._db.execute(delete(Entitlement).where(Entitlement.user_id == user_id))
        key = self._settings.revenuecat_secret_key
        if not key:
            return
        try:
            await self._http.delete(
                f"{RC_API}/subscribers/{user_id}",
                headers={"Authorization": f"Bearer {key}"},
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            logger.warning("could not delete RevenueCat subscriber %s: %s", user_id, exc)
