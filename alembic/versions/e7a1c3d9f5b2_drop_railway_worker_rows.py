"""drop the railway worker rows

Hosting moved from Railway to Cloud Run (2026-10-22). The dispatcher has no
Railway leg any more, so the seeded ``kind='railway'`` platform row is dead
weight in the admin view and in every tier walk. Data only; the schema is
unchanged. Downgrade does not recreate it: an older serve that still has the
Railway leg re-seeds its row on boot (``ensure_platform_row``).

Revision ID: e7a1c3d9f5b2
Revises: c4e8a2f17b90
Create Date: 2026-10-22 16:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7a1c3d9f5b2"
down_revision: str | Sequence[str] | None = "c4e8a2f17b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Delete the Railway platform row(s)."""
    op.execute(sa.text("DELETE FROM workers WHERE kind = 'railway'"))


def downgrade() -> None:
    """Nothing to restore: a serve with a Railway leg seeds its own row."""
