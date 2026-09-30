"""Tables for the person behind the account: their profile, and their plan.

Both are keyed by the Supabase user id, one row per person, created on first
write. Neither is a foreign key to `auth.users` for the reason the sessions
module gives: that schema is Supabase's, not ours.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, PrimaryKeyConstraint, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Profile(Base):
    """What the profile screen shows about someone, in their own words.

    Every field optional: a person who never opens "Edit profile" has a
    perfectly good profile — the app falls back to the name they signed up
    with.
    """

    __tablename__ = "profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(60), default=None)
    #: "Kolkata, India". Free text — it is a line on a profile, not an address.
    home_city: Mapped[str | None] = mapped_column(String(80), default=None)
    bio: Mapped[str | None] = mapped_column(String(160), default=None)
    #: One of the app's avatar choices (an emoji). No uploads: a photo would
    #: need storage, moderation and a deletion story, and a cat does not.
    avatar: Mapped[str | None] = mapped_column(String(16), default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Entitlement(Base):
    """Whether someone has Premium, and until when — as RevenueCat last said.

    RevenueCat is the source of truth; this row is our copy of its answer, so
    a session start can check the plan without a round trip to it. Written by
    the webhook and by `POST /billing/sync` after a purchase.
    """

    __tablename__ = "entitlements"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    #: Null means no access. In the past means it lapsed. A lifetime purchase
    #: is stored as far-future rather than as a special case.
    premium_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    product_id: Mapped[str | None] = mapped_column(String(120), default=None)
    #: False once the user has cancelled: access continues to `premium_until`
    #: and then stops. The profile says which.
    will_renew: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    #: Premium from a promo code, until this moment. Kept apart from
    #: `premium_until` on purpose: that column is RevenueCat's answer and is
    #: overwritten by every refresh, which would silently cancel the code.
    promo_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PromoRedemption(Base):
    """One person redeeming one promo code — at most once each, and counted
    against the code's limit."""

    __tablename__ = "promo_redemptions"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    code: Mapped[str] = mapped_column(String(40))
    redeemed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (PrimaryKeyConstraint("user_id", "code"),)
