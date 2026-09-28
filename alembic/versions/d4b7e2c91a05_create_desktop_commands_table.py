"""create desktop_commands table

The desktop command queue (#1100, spec 2026-09-28): requests the phone
makes for the user's desktop to run. Its own table, not a ``state_docs``
kind, so it never enters the sync manifest. Joins the
``tenant_isolation`` RLS policy family like ``export_presets``
(c3e8a1d47f92).

Revision ID: d4b7e2c91a05
Revises: a7c2d9e41b3f
Create Date: 2026-09-28 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4b7e2c91a05"
down_revision: str | Sequence[str] | None = "a7c2d9e41b3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY = "tenant_isolation"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "desktop_commands",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("match_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=True),
        sa.Column("stage_number", sa.Integer(), nullable=True),
        sa.Column("args", sa.JSON(), nullable=False),
        sa.Column("expected_revision", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("claimed_by", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("progress_message", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["claimed_by"], ["desktop_tokens.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_desktop_commands_match_id"), "desktop_commands", ["match_id"], unique=False)
    op.create_index(op.f("ix_desktop_commands_status"), "desktop_commands", ["status"], unique=False)
    op.create_index(op.f("ix_desktop_commands_user_id"), "desktop_commands", ["user_id"], unique=False)
    if op.get_bind().dialect.name == "postgresql":
        # Each statement separately: asyncpg cannot run several commands
        # in one prepared statement.
        op.execute("ALTER TABLE desktop_commands ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE desktop_commands FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {_POLICY} ON desktop_commands "
            f"FOR ALL "
            f"USING (user_id = current_setting('app.user_id', true)) "
            f"WITH CHECK (user_id = current_setting('app.user_id', true))"
        )


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON desktop_commands")
        op.execute("ALTER TABLE desktop_commands NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE desktop_commands DISABLE ROW LEVEL SECURITY")
    op.drop_index(op.f("ix_desktop_commands_user_id"), table_name="desktop_commands")
    op.drop_index(op.f("ix_desktop_commands_status"), table_name="desktop_commands")
    op.drop_index(op.f("ix_desktop_commands_match_id"), table_name="desktop_commands")
    op.drop_table("desktop_commands")
