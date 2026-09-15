"""create export_presets table

Per-user saved export presets (spec 2026-09-15 s1). Its own table, not
a ``state_docs`` kind: a preset belongs to a user, not a match, and
must stay out of the sync manifest. Composite primary key
``(user_id, preset_id)``; joins the ``tenant_isolation`` RLS policy
family like ``match_comments`` (b4d8f1a90c27).

Revision ID: c3e8a1d47f92
Revises: 58603835d0bd
Create Date: 2026-09-15 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3e8a1d47f92"
down_revision: str | Sequence[str] | None = "58603835d0bd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY = "tenant_isolation"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "export_presets",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("preset_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "preset_id"),
    )
    if op.get_bind().dialect.name == "postgresql":
        # Each statement separately: asyncpg cannot run several commands
        # in one prepared statement.
        op.execute("ALTER TABLE export_presets ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE export_presets FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {_POLICY} ON export_presets "
            f"FOR ALL "
            f"USING (user_id = current_setting('app.user_id', true)) "
            f"WITH CHECK (user_id = current_setting('app.user_id', true))"
        )


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON export_presets")
        op.execute("ALTER TABLE export_presets NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE export_presets DISABLE ROW LEVEL SECURITY")
    op.drop_table("export_presets")
