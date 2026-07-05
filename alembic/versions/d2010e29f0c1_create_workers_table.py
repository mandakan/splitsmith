"""create workers table

Adds the ``workers`` table for the self-hosted compute worker registry.
Each row represents one compute-worker target: either a self-hosted box
registered via the admin UI, or the Railway worker service (a single row
seeded at serve boot when Railway launcher env vars are present).

Operator infrastructure, not tenant data: no user_id column and not under
Row-Level Security - the multi-tenant table checklist does not apply here.

Tokens are stored as sha256 hex digests (sessions precedent, NOT the raw
share_tokens one): a worker token bootstraps infra credentials, so a DB
leak must not yield usable tokens.

Revision ID: d2010e29f0c1
Revises: 4ab814cb20f5
Create Date: 2026-07-04 19:30:17.897032

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2010e29f0c1"
down_revision: str | Sequence[str] | None = "4ab814cb20f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "workers",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("registration_token_hash", sa.String(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_token_hash", sa.String(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_wake_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("info", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_workers_name"),
        sa.UniqueConstraint("registration_token_hash", name="uq_workers_registration_token_hash"),
        sa.UniqueConstraint("worker_token_hash", name="uq_workers_worker_token_hash"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("workers")
