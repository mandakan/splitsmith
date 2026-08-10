"""add args to compute_jobs

Persists the wire-serialised submit args (job_journal.to_wire_args shape)
so a failed job can be re-enqueued by the retry endpoint. Nullable, no
backfill: rows are ephemeral (boot-swept), and NULL is the retry
endpoint's "predates retry support" sentinel. Plain metadata on an
already-RLS'd table - the tenant_isolation policy keys on user_id only,
so no RLS DDL here.

Revision ID: e7b0c250a19c
Revises: 0c1dbb2ce678
Create Date: 2026-08-10 17:09:13.921869

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7b0c250a19c"
down_revision: str | Sequence[str] | None = "0c1dbb2ce678"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "compute_jobs",
        sa.Column("args", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("compute_jobs", "args")
