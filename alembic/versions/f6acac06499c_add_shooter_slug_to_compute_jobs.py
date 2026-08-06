"""add shooter_slug to compute_jobs

Adds a nullable ``shooter_slug`` column to ``compute_jobs``. Stage numbers
repeat across every shooter in a match, so the job dedupe key (and the
SPA's "is detection running here?" predicates) must include the owning
shooter or one shooter's in-flight job blocks another's at the same stage
number (issue #664). NULL for jobs with no owning shooter (model_download,
generate_proxy, compare-grid, lab jobs).

Plain metadata on an already-tenant-scoped, already-RLS'd row - the
``tenant_isolation`` policy (a7c4e9d21b06) keys on ``user_id`` only and is
unchanged by adding a column, so this migration issues no RLS DDL. Nullable,
no server_default: rows are ephemeral (boot-swept), so no backfill is
needed and pre-migration rows behave like slug-less jobs.

Revision ID: f6acac06499c
Revises: 100a98d99b0e
Create Date: 2026-08-06 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6acac06499c"
down_revision: str | Sequence[str] | None = "100a98d99b0e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("compute_jobs", sa.Column("shooter_slug", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("compute_jobs", "shooter_slug")
