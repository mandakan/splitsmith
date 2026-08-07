"""add desktop_tokens table and matches.origin column

Two independent, additive changes for the desktop-to-hosted sync MVP
(doc 2026-08-07):

1. ``matches.origin`` distinguishes a natively-created hosted match
   ("hosted") from one mirrored down by a desktop sync push ("desktop").
   Plain metadata on an already-tenant-scoped, already-RLS'd row - the
   ``tenant_isolation`` policy (a7c4e9d21b06) keys on ``user_id`` only and
   is unchanged by adding a column, so this migration issues no RLS DDL
   for ``matches`` (same reasoning as ``f6acac06499c``).

2. ``desktop_tokens`` holds the bearer credentials the desktop app
   presents when pushing a match up to a user's hosted account. Structure
   mirrors ``share_tokens`` (4ab814cb20f5): one row per issued token,
   ``user_id`` FK CASCADE, ``revoked_at`` set instead of deleting so the
   settings UI can still show a revoked token as an audit trail. Like
   ``share_tokens`` (and ``sessions``, ``magic_link_tokens``), this table
   is **not** placed under Row-Level Security: the sync-push endpoint must
   hash the presented bearer token and look up its owning ``user_id``
   before any ``app.user_id`` GUC can be set, so the resolution query runs
   pre-tenant via the raw session factory. An RLS'd table would make that
   lookup return zero rows and break authentication outright. Unlike
   ``share_tokens`` the raw token itself is not stored - only its SHA-256
   hash (``token_hash``), the ``sessions``/``workers`` precedent, since a
   desktop token is a durable infra credential rather than a display-once
   share link.

Revision ID: 73e6636ba194
Revises: f6acac06499c
Create Date: 2026-08-07 12:38:37.108806

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "73e6636ba194"
down_revision: str | Sequence[str] | None = "f6acac06499c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("matches", sa.Column("origin", sa.String(), nullable=False, server_default="hosted"))

    op.create_table(
        "desktop_tokens",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_desktop_tokens_token_hash"),
    )
    op.create_index(
        op.f("ix_desktop_tokens_user_id"),
        "desktop_tokens",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_desktop_tokens_user_id"), table_name="desktop_tokens")
    op.drop_table("desktop_tokens")
    op.drop_column("matches", "origin")
