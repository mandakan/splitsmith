"""users.access_tier and access_requests

Adds ``users.access_tier`` (spec 2026-10-03): names a feature set in
``splitsmith.access.AccessConfig``, separate from ``entitlement`` which
stays reserved for billing. Every existing account becomes ``full``:
nothing changes for anyone already in.

Also creates ``access_requests``, one row per request for a hosted
account. Not under RLS, like ``users``: only admin routes and the
intake writer touch it, and anonymous resolution (the intake form) runs
before any ``app.user_id`` GUC exists.

Revision ID: d4a8c1f37e20
Revises: c05ac6f8592c
Create Date: 2026-10-03 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4a8c1f37e20"
down_revision: str | Sequence[str] | None = "c05ac6f8592c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("access_tier", sa.String(), nullable=False, server_default="full"))
    op.create_table(
        "access_requests",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "last_requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("tier_granted", sa.String(), nullable=True),
        sa.Column("email_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("access_requests")
    op.drop_column("users", "access_tier")
