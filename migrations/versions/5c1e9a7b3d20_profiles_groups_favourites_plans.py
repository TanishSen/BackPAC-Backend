"""profiles, groups, favourites and plans

Everything the profile and full-history screens need that did not exist:

- `sessions.favourite`, and `sessions.group_id` into the new `session_groups`
- `profiles` — name, home city, bio and avatar, one row per person
- `entitlements` — our copy of RevenueCat's answer to "is this person Premium"

Additive only, so the previous API keeps working against this schema during a
rollback. RLS goes on every new table with no policies, exactly as the first
migration explains: the anon key ships inside the app.

Revision ID: 5c1e9a7b3d20
Revises: 0040d81cf747
Create Date: 2026-09-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5c1e9a7b3d20"
down_revision: Union[str, None] = "0040d81cf747"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_TABLES = ("session_groups", "profiles", "entitlements")


def upgrade() -> None:
    op.create_table(
        "session_groups",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_session_groups_user_name"),
    )
    op.create_index(
        op.f("ix_session_groups_user_id"), "session_groups", ["user_id"], unique=False
    )

    op.add_column(
        "sessions",
        sa.Column("favourite", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column("sessions", sa.Column("group_id", sa.UUID(), nullable=True))
    op.create_index(op.f("ix_sessions_group_id"), "sessions", ["group_id"], unique=False)
    op.create_foreign_key(
        "fk_sessions_group_id",
        "sessions",
        "session_groups",
        ["group_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "profiles",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("display_name", sa.String(length=60), nullable=True),
        sa.Column("home_city", sa.String(length=80), nullable=True),
        sa.Column("bio", sa.String(length=160), nullable=True),
        sa.Column("avatar", sa.String(length=16), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "entitlements",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("premium_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("product_id", sa.String(length=120), nullable=True),
        sa.Column("will_renew", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )

    for table in _NEW_TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON public.{table} FROM anon, authenticated")


def downgrade() -> None:
    op.drop_table("entitlements")
    op.drop_table("profiles")
    op.drop_constraint("fk_sessions_group_id", "sessions", type_="foreignkey")
    op.drop_index(op.f("ix_sessions_group_id"), table_name="sessions")
    op.drop_column("sessions", "group_id")
    op.drop_column("sessions", "favourite")
    op.drop_index(op.f("ix_session_groups_user_id"), table_name="session_groups")
    op.drop_table("session_groups")
