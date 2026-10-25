"""export id and parts on compute jobs

Every job an export route queues carries the export it belongs to, and a
job publishes its parts for the export progress panel (spec 2026-10-25).

Revision ID: a3f9c2e81d47
Revises: e7a1c3d9f5b2
Create Date: 2026-10-25 08:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a3f9c2e81d47"
down_revision: str | Sequence[str] | None = "e7a1c3d9f5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("compute_jobs", sa.Column("export_id", sa.String(), nullable=True))
    op.add_column(
        "compute_jobs",
        sa.Column("parts", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True),
    )
    op.create_index("ix_compute_jobs_export_id", "compute_jobs", ["export_id"])


def downgrade() -> None:
    op.drop_index("ix_compute_jobs_export_id", table_name="compute_jobs")
    op.drop_column("compute_jobs", "parts")
    op.drop_column("compute_jobs", "export_id")
