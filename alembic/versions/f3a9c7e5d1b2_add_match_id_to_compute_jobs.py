"""add match_id to compute_jobs

Persists the submit-time match binding (current_match_id ContextVar) so
retry can rebind the job's ORIGINAL match instead of inheriting whatever
match is ambient on the retrying request. Nullable, no backfill: rows
predating this migration have no recorded binding, and a legitimately
match-less job (e.g. model_download) also has NULL here - both cases
already fall out of the existing args-NULL retry guard, so no separate
sentinel is needed. Plain metadata on an already-RLS'd table - the
tenant_isolation policy keys on user_id only, so no RLS DDL here.

Revision ID: f3a9c7e5d1b2
Revises: e7b0c250a19c
Create Date: 2026-08-10 18:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a9c7e5d1b2"
down_revision: str | Sequence[str] | None = "e7b0c250a19c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "compute_jobs",
        sa.Column("match_id", sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("compute_jobs", "match_id")
