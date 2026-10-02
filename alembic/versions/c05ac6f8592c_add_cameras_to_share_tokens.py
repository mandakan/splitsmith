"""add cameras to share_tokens

Adds a nullable ``cameras`` JSON column to ``share_tokens``: per link,
which camera each shooter starts on for its viewers (``{slug: selector}``,
a mount or role resolved per stage like ``camera_select``). NULL means
the link follows each shooter's saved ``compare_camera``, which is what
every link did before this column existed, so no backfill is needed.

``share_tokens`` is not under RLS (anonymous resolution runs before any
``app.user_id`` GUC exists; see the model's docstring), and adding a
column changes no policy, so this migration issues no RLS DDL.

Revision ID: c05ac6f8592c
Revises: d4b7e2c91a05
Create Date: 2026-10-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c05ac6f8592c"
down_revision: str | Sequence[str] | None = "d4b7e2c91a05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("share_tokens", sa.Column("cameras", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("share_tokens", "cameras")
