"""create shooter_book and account_profiles tables

The account's shooter book and profile (spec 2026-10-08): a shooter's look
keyed by SSI shooter id, and the video maker's brand. Their own tables, not
``state_docs`` kinds: they belong to a user, not a match, and must stay out
of the sync manifest. Both join the ``tenant_isolation`` RLS policy family
like ``export_presets`` (c3e8a1d47f92) and ``user_looks`` (e7c2a9b41d63).

Revision ID: b9d4e2a7c1f3
Revises: f3b8d1c6a2e9
Create Date: 2026-10-08 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b9d4e2a7c1f3"
down_revision: str | Sequence[str] | None = "f3b8d1c6a2e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY = "tenant_isolation"
_TABLES = ("shooter_book", "account_profiles")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "shooter_book",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("shooter_id", sa.Integer(), nullable=False),
        sa.Column("identity", sa.JSON(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "shooter_id"),
    )
    op.create_table(
        "account_profiles",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("brand", sa.JSON(), nullable=True),
        sa.Column("backfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    if op.get_bind().dialect.name == "postgresql":
        # Each statement separately: asyncpg cannot run several commands
        # in one prepared statement.
        for table in _TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY {_POLICY} ON {table} "
                f"FOR ALL "
                f"USING (user_id = current_setting('app.user_id', true)) "
                f"WITH CHECK (user_id = current_setting('app.user_id', true))"
            )


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().dialect.name == "postgresql":
        for table in _TABLES:
            op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("account_profiles")
    op.drop_table("shooter_book")
