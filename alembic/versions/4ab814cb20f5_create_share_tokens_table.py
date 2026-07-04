"""create share_tokens table

Adds the ``share_tokens`` table for public read-only match access (#349).
Each row represents one share link owned by a user; the raw token is the
capability (256 bits from ``secrets.token_urlsafe``), stored raw so the
owner's share dialog can re-display the full link URL.

Not placed under Row-Level Security, following the ``sessions`` precedent:
anonymous token resolution runs before any ``app.user_id`` GUC exists. The
unique-token lookup bounds the anonymous path; owner-management queries
filter by ``user_id`` explicitly in ``ShareTokenStore``.

``revoked_at`` is set instead of deleting revoked rows - the share dialog
shows revoked links as an audit trail. ``expires_at`` is honored by the
resolver but always NULL from the MVP UI.

Revision ID: 4ab814cb20f5
Revises: 20aa31aeca11
Create Date: 2026-07-04 15:01:59.468923

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4ab814cb20f5"
down_revision: str | Sequence[str] | None = "20aa31aeca11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "share_tokens",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("match_id", sa.String(), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token", name="uq_share_tokens_token"),
    )
    op.create_index(
        op.f("ix_share_tokens_user_id"),
        "share_tokens",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_share_tokens_user_id"), table_name="share_tokens")
    op.drop_table("share_tokens")
