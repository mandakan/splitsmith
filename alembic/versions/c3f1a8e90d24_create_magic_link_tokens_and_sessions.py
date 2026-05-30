"""create magic_link_tokens and sessions tables

The two tables the in-house magic-link auth backend
(``splitsmith.db.magic_link.MagicLinkAuth``) needs:

- ``magic_link_tokens`` -- one row per issued passwordless login
  challenge. Stores the SHA-256 hash of the emailed token (never the raw
  value), a 15-minute expiry, and a single-use ``consumed_at`` latch.
- ``sessions`` -- one row per authenticated browser session. Stores the
  SHA-256 hash of the cookie's session secret, the owning ``user_id``, a
  30-day sliding ``expires_at``, and last-used / UA / IP metadata.

Neither table is placed under Row-Level Security. Both are auth
infrastructure resolved *before* any ``app.user_id`` GUC exists (you can't
set the GUC until you've identified the user, which is what these tables
do), exactly the reasoning that keeps ``users`` out of RLS. Isolation for
``sessions`` is the ``user_id`` FK + the unguessable ``token_hash`` lookup.

Revision ID: c3f1a8e90d24
Revises: a7c4e9d21b06
Create Date: 2026-05-30 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f1a8e90d24"
down_revision: str | Sequence[str] | None = "a7c4e9d21b06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "magic_link_tokens",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_magic_link_tokens_email"),
        "magic_link_tokens",
        ["email"],
        unique=False,
    )
    op.create_index(
        op.f("ix_magic_link_tokens_token_hash"),
        "magic_link_tokens",
        ["token_hash"],
        unique=True,
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("ip", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_sessions_token_hash"),
        "sessions",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_sessions_user_id"),
        "sessions",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_token_hash"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_index(op.f("ix_magic_link_tokens_token_hash"), table_name="magic_link_tokens")
    op.drop_index(op.f("ix_magic_link_tokens_email"), table_name="magic_link_tokens")
    op.drop_table("magic_link_tokens")
