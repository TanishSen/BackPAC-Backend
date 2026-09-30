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
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domains.account.models import Entitlement, PromoRedemption
from app.domains.billing.schemas import PlanOut
from app.domains.sessions.repository import SessionRepository
from app.shared.exceptions import BadRequestError, PaymentRequiredError

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


def _promo_duration(days: int) -> str:
    """The RevenueCat promotional duration that covers `days`."""
    for limit, name in (
        (1, "daily"), (3, "three_day"), (7, "weekly"), (31, "monthly"),
        (62, "two_month"), (93, "three_month"), (186, "six_month"), (366, "yearly"),
    ):
        if days <= limit:
            return name
    return "lifetime"


def _active_until(ent: Entitlement | None, now: datetime) -> tuple[bool, datetime | None]:
    """Whether Premium is on, from the store or a promo code, and until when."""
    if ent is None:
        return False, None
    live = [
        t for t in (ent.premium_until, ent.promo_until) if t is not None and t > now
    ]
    return bool(live), (max(live) if live else None)


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
        premium, until = _active_until(ent, now)
        store_premium = bool(ent and ent.premium_until and ent.premium_until > now)
        limit = self._settings.free_monthly_trip_plans
        used = await SessionRepository(self._db).count_plans_since(
            user_id=user_id, since=_month_start(now)
        )
        unlimited = premium or limit <= 0
        return PlanOut(
            billing_enabled=self._settings.billing_enabled,
            premium=premium,
            premium_until=until if until != _FOREVER else None,
            # Only a store subscription renews; a promo code simply ends.
            will_renew=bool(ent and ent.will_renew and store_premium),
            product_id=(
                ent.product_id
                if store_premium and ent
                else ("promo" if premium else None)
            ),
            free_monthly_limit=None if unlimited else limit,
            used_this_month=used,
            remaining_this_month=None if unlimited else max(0, limit - used),
        )

    async def is_premium(self, user_id: uuid.UUID) -> bool:
        return _active_until(
            await self._entitlement(user_id), datetime.now(timezone.utc)
        )[0]

    async def assert_can_start(self, user_id: uuid.UUID) -> None:
        """Raise 402 if a free account has used this month's new trip plans.

        Only a *new* conversation counts: resuming one is carrying on a plan
        already started, and must always work.

        Before refusing, ask RevenueCat once: someone who has just bought
        Premium may be starting a call before the webhook or the app's sync
        has reached us, and turning away a paying customer is the one mistake
        this check must not make. It costs a round trip only in that case.
        """
        if not self._settings.billing_enabled:
            return
        plan = await self.plan_for(user_id)
        if plan.remaining_this_month is not None and plan.remaining_this_month <= 0:
            if await self._refresh(user_id):
                await self._db.commit()
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
        key = self._settings.revenuecat_read_key
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
        entitlements = subscriber.get("entitlements") or {}
        ent = entitlements.get(self._settings.premium_entitlement_id)
        if ent is None and entitlements:
            # One tier, so any entitlement is Premium — which also survives the
            # dashboard's entitlement being named something other than ours.
            now = datetime.now(timezone.utc)
            live = [
                e for e in entitlements.values()
                if not e.get("expires_date")
                or (_parse_time(e.get("expires_date")) or now) > now
            ]
            ent = live[0] if live else next(iter(entitlements.values()))
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

        if self._settings.revenuecat_read_key:
            for user in users:
                await self._refresh(user)
            await self._db.commit()
            return

        # No secret key: apply the event as sent. Right for purchases,
        # renewals, cancellations and expiry; a transfer cannot be resolved
        # without asking RevenueCat, and is logged instead.
        user = _as_user_id(event.get("app_user_id"))
        # One tier: any entitlement on the event is Premium.
        entitlements = event.get("entitlement_ids")
        concerns_us = entitlements is None or bool(entitlements)
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

    async def redeem(self, user_id: uuid.UUID, code: str) -> PlanOut:
        """Unlock Premium with a promo code.

        Once per person per code — a second try answers with the plan as it
        stands, so a retry after a dropped response is harmless — and at most
        the code's limit across everyone. Days are added on top of any promo
        time already running, never instead of it.

        With a RevenueCat secret key the grant is also made in RevenueCat, as
        a promotional entitlement, so the app's SDK sees Premium too. Our copy
        is written either way and is what the backend enforces.
        """
        key = code.strip().upper()
        table = self._settings.promo_code_table
        if key not in table:
            raise BadRequestError("That code isn't valid. Check it and try again.")
        days, max_uses = table[key]

        already = (
            await self._db.execute(
                select(PromoRedemption).where(
                    PromoRedemption.user_id == user_id, PromoRedemption.code == key
                )
            )
        ).scalar_one_or_none()
        if already is not None:
            return await self.plan_for(user_id)

        if max_uses is not None:
            used = (
                await self._db.execute(
                    select(func.count()).select_from(PromoRedemption).where(
                        PromoRedemption.code == key
                    )
                )
            ).scalar_one()
            if used >= max_uses:
                raise BadRequestError("This code has been fully redeemed.")

        now = datetime.now(timezone.utc)
        ent = await self._entitlement(user_id)
        start = max(now, ent.promo_until) if ent and ent.promo_until else now
        until = start + timedelta(days=days)
        self._db.add(PromoRedemption(user_id=user_id, code=key))
        stmt = insert(Entitlement).values(user_id=user_id, promo_until=until)
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[Entitlement.user_id],
                set_={"promo_until": until, "updated_at": now},
            )
        )
        await self._db.commit()
        logger.info("promo %s redeemed by %s: Premium until %s", key, user_id, until)

        secret = self._settings.revenuecat_secret_key
        if secret:
            try:
                await self._http.post(
                    f"{RC_API}/subscribers/{user_id}/entitlements/"
                    f"{self._settings.premium_entitlement_id}/promotional",
                    headers={"Authorization": f"Bearer {secret}"},
                    json={"duration": _promo_duration(days)},
                    timeout=10.0,
                )
            except httpx.HTTPError as exc:
                logger.warning("RevenueCat promotional grant failed for %s: %s", user_id, exc)
        return await self.plan_for(user_id)

    async def forget(self, user_id: uuid.UUID) -> None:
        """Account deletion: our copy goes, and RevenueCat's customer record
        too when we hold a key. The store subscription itself can only be
        cancelled by its owner — the app tells them so."""
        await self._db.execute(delete(Entitlement).where(Entitlement.user_id == user_id))
        await self._db.execute(
            delete(PromoRedemption).where(PromoRedemption.user_id == user_id)
        )
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
