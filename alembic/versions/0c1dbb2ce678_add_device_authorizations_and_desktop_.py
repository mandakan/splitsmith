"""add device_authorizations table and desktop_tokens.scope

Browser-assisted desktop auth (#719).

1. ``desktop_tokens.scope`` -- ``server_default='full'`` so every row that
   predates this migration backfills to the legacy, unrestricted value.
   That is what keeps a desktop install in the field working: the scope
   gate in ``_auth_gate`` confines a token to the sync surface unless its
   scope is in the unrestricted allowlist ``{None, 'full'}``, so a
   backfilled 'full' row keeps the reach it had before this migration and
   any unrecognized scope is confined rather than falling open.
   Every token minted after this ships is 'sync' (see
   ``DesktopTokenStore.create``). Plain metadata on an already-tenant-
   scoped table; the ``tenant_isolation`` policy keys on ``user_id`` only
   and is unchanged by adding a column, so no RLS DDL here.

2. ``device_authorizations`` -- in-flight device-code authorizations.
   Like ``desktop_tokens`` / ``share_tokens`` / ``sessions``, this table
   is deliberately NOT under Row-Level Security: the poll request
   authenticates from the device code alone, before any ``app.user_id``
   GUC exists, so the lookup runs pre-tenant on the raw session factory.
   An RLS'd table would return zero rows and break authentication.

Revision ID: 0c1dbb2ce678
Revises: 73e6636ba194
Create Date: 2026-08-08 10:44:36.704793

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0c1dbb2ce678"
down_revision: str | Sequence[str] | None = "73e6636ba194"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "desktop_tokens",
        sa.Column("scope", sa.String(), nullable=False, server_default="full"),
    )

    op.create_table(
        "device_authorizations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("device_code_hash", sa.String(), nullable=False),
        sa.Column("user_code", sa.String(), nullable=False),
        sa.Column("device_name", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False, server_default="sync"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_code_hash", name="uq_device_authorizations_device_code_hash"),
        sa.UniqueConstraint("user_code", name="uq_device_authorizations_user_code"),
    )
    op.create_index(
        op.f("ix_device_authorizations_user_code"),
        "device_authorizations",
        ["user_code"],
        unique=False,
    )
    op.create_index(
        op.f("ix_device_authorizations_user_id"),
        "device_authorizations",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_device_authorizations_user_id"), table_name="device_authorizations")
    op.drop_index(op.f("ix_device_authorizations_user_code"), table_name="device_authorizations")
    op.drop_table("device_authorizations")
    op.drop_column("desktop_tokens", "scope")
