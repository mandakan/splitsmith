"""create user_looks table

Per-user saved Looks (issue #1263, spec 2026-10-07 s3): colours, accent
series, card styles and the base Look, never a template. Its own table,
not a ``state_docs`` kind: a Look belongs to a user, not a match, and
must stay out of the sync manifest. Composite primary key
``(user_id, name)``; joins the ``tenant_isolation`` RLS policy family
like ``export_presets`` (c3e8a1d47f92).

Revision ID: e7c2a9b41d63
Revises: d4a8c1f37e20
Create Date: 2026-10-07 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7c2a9b41d63"
down_revision: str | Sequence[str] | None = "d4a8c1f37e20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY = "tenant_isolation"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "user_looks",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "name"),
    )
    if op.get_bind().dialect.name == "postgresql":
        # Each statement separately: asyncpg cannot run several commands
        # in one prepared statement.
        op.execute("ALTER TABLE user_looks ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE user_looks FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {_POLICY} ON user_looks "
            f"FOR ALL "
            f"USING (user_id = current_setting('app.user_id', true)) "
            f"WITH CHECK (user_id = current_setting('app.user_id', true))"
        )


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON user_looks")
        op.execute("ALTER TABLE user_looks NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE user_looks DISABLE ROW LEVEL SECURITY")
    op.drop_table("user_looks")
