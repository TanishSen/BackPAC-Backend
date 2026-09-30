"""promo codes

`entitlements.promo_until` — Premium from a promo code, kept apart from
RevenueCat's `premium_until` so a refresh cannot cancel it — and
`promo_redemptions`, which holds each person to one use per code and counts
uses against a code's limit. Additive; RLS on the new table as on every other.

Revision ID: 7d2f4c9e1a55
Revises: 5c1e9a7b3d20
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7d2f4c9e1a55"
down_revision: Union[str, None] = "5c1e9a7b3d20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "entitlements",
        sa.Column("promo_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "promo_redemptions",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column(
            "redeemed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id", "code"),
    )
    op.execute("ALTER TABLE public.promo_redemptions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.promo_redemptions FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON public.promo_redemptions FROM anon, authenticated")


def downgrade() -> None:
    op.drop_table("promo_redemptions")
    op.drop_column("entitlements", "promo_until")
