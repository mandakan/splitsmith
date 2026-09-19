"""add users.youtube_connection json column

Revision ID: a7c2d9e41b3f
Revises: c3e8a1d47f92
Create Date: 2026-09-19 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c2d9e41b3f"
down_revision: str | Sequence[str] | None = "c3e8a1d47f92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """One nullable JSON column: the account's YouTube connection (issue #1000, phase 2)."""
    op.add_column("users", sa.Column("youtube_connection", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "youtube_connection")
