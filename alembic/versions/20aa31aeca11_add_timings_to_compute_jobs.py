"""add timings to compute_jobs

Adds a nullable ``timings`` column to ``compute_jobs`` holding per-job
observability metadata: ``{queue_wait_ms, total_ms, phases[], meta{}}``.
It is plain metadata on an already-tenant-scoped, already-RLS'd row -- the
``tenant_isolation`` policy (a7c4e9d21b06) keys on ``user_id`` only and is
unchanged by adding a column, so this migration issues no RLS DDL. A plain
``ADD COLUMN`` preserves ``relrowsecurity``/``relforcerowsecurity`` and the
existing policy.

The column is created as generic ``JSON`` so ``create``/``upgrade`` works on
both SQLite (the unit-test + clean-DB smoke engine) and Postgres; on Postgres
it is then ALTERed to ``JSONB`` (same dialect-guarded pattern as
d1f7b25c8a3e's ``doc`` column). Nullable, no server_default -- it is populated
once by the job backend when a job reaches a terminal status, matching the
``result`` column which also omits a default.

Revision ID: 20aa31aeca11
Revises: e2a5f9c4b318
Create Date: 2026-06-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20aa31aeca11"
down_revision: str | Sequence[str] | None = "e2a5f9c4b318"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("compute_jobs", sa.Column("timings", sa.JSON(), nullable=True))
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE compute_jobs ALTER COLUMN timings TYPE JSONB USING timings::jsonb")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("compute_jobs", "timings")
