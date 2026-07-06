"""add version to workers

Adds a nullable ``version`` column to the ``workers`` table: the semver of
the code each worker is running. Self-hosted agents report it at register
time and refresh it on every wake-channel reconnect; the Railway row is
stamped at serve boot. Observational only - nothing gates on it today.

Revision ID: 100a98d99b0e
Revises: d2010e29f0c1
Create Date: 2026-07-06 09:33:54.940556

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "100a98d99b0e"
down_revision: str | Sequence[str] | None = "d2010e29f0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("workers", sa.Column("version", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("workers", "version")
