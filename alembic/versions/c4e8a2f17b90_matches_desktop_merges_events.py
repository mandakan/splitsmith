"""matches.desktop_merges_events

A desktop mirror whose desktop merges Breakdown's regions (spec
2026-10-18-breakdown-hosted-sync-design). The sync sets it once the desktop
says so; only then does the mirror accept a region edit. Existing rows are
``false``: their desktops have not said so yet.

Revision ID: c4e8a2f17b90
Revises: b9d4e2a7c1f3
Create Date: 2026-10-18 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4e8a2f17b90"
down_revision: str | Sequence[str] | None = "b9d4e2a7c1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "matches",
        sa.Column("desktop_merges_events", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("matches", "desktop_merges_events")
