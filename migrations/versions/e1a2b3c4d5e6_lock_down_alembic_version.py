"""lock down alembic_version

Alembic creates its own bookkeeping table, so it is not covered by the
migration that creates ours — and Supabase's default privileges hand every new
public table to `anon` and `authenticated`, the roles behind the key that ships
inside the Flutter app. RLS with no policy already blocks them, but a role
holding UPDATE on the table that records which migrations have run is not
something to leave lying around on a technicality.

Revision ID: e1a2b3c4d5e6
Revises: d479dba453ee
Create Date: 2026-09-20
"""
from typing import Sequence, Union

from alembic import op

revision: str = "e1a2b3c4d5e6"
down_revision: Union[str, None] = "d479dba453ee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE public.alembic_version ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.alembic_version FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON public.alembic_version FROM anon, authenticated")


def downgrade() -> None:
    # Deliberately not re-granting. Undoing a lockdown is not a thing a
    # downgrade should silently do.
    op.execute("ALTER TABLE public.alembic_version NO FORCE ROW LEVEL SECURITY")
