"""users.whats_new_seen

The What's new entry ids each account has seen (``splitsmith.whats_new``).
Nullable: ``NULL`` is "never looked", which the route resolves on the
first visit.

Revision ID: f3b8d1c6a2e9
Revises: e7c2a9b41d63
Create Date: 2026-10-07 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3b8d1c6a2e9"
down_revision: str | Sequence[str] | None = "e7c2a9b41d63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("whats_new_seen", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "whats_new_seen")
