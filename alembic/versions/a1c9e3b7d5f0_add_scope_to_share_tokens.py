"""add scope to share_tokens

#779: share tokens gain a named scope keying what a request they
authorize may do. 'read' is the only value shipped; the server maps
scope -> capability set and enforces READ ONLY transactions for
scopes without writes. server_default backfills every existing token
as read-scoped, which is exactly what they all are today.
share_tokens is not under RLS (models.py docstring), so no RLS DDL.

Revision ID: a1c9e3b7d5f0
Revises: f3a9c7e5d1b2
Create Date: 2026-08-12 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c9e3b7d5f0"
down_revision: str | Sequence[str] | None = "f3a9c7e5d1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "share_tokens",
        sa.Column("scope", sa.String(), nullable=False, server_default="read"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("share_tokens", "scope")
