"""create match_comments table

Public timestamped comments on a shooter's stage video. ``user_id`` is
the match owner (see :class:`splitsmith.db.models.CommentRow`), so the
table joins the ``tenant_isolation`` RLS policy family unchanged - the
owner's tenant is what an anonymous write is impersonating by the time
it reaches here.

Two indexes, both driven by real queries: the thread read is
``(user_id, match_id, slug, stage_number)`` and the two bulk-moderation
deletes are by ``share_token_id`` and by ``author_key_hash``.

Revision ID: b4d8f1a90c27
Revises: a1c9e3b7d5f0
Create Date: 2026-08-13 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b4d8f1a90c27"
down_revision: str | Sequence[str] | None = "a1c9e3b7d5f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY = "tenant_isolation"
_THREAD_INDEX = "ix_match_comments_thread"
_TOKEN_INDEX = "ix_match_comments_share_token_id"
_AUTHOR_INDEX = "ix_match_comments_author_key_hash"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "match_comments",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("match_id", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("stage_number", sa.Integer(), nullable=False),
        sa.Column("anchor_t", sa.Float(), nullable=False),
        sa.Column("anchor_kind", sa.String(), nullable=False),
        sa.Column("anchor_shot_id", sa.String(), nullable=True),
        sa.Column("author_kind", sa.String(), nullable=False),
        sa.Column("author_user_id", sa.String(), nullable=True),
        sa.Column("author_handle", sa.String(), nullable=False),
        sa.Column("author_key_hash", sa.String(), nullable=False),
        sa.Column("share_token_id", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        _THREAD_INDEX,
        "match_comments",
        ["user_id", "match_id", "slug", "stage_number"],
        unique=False,
    )
    op.create_index(_TOKEN_INDEX, "match_comments", ["share_token_id"], unique=False)
    op.create_index(_AUTHOR_INDEX, "match_comments", ["author_key_hash"], unique=False)

    if op.get_bind().dialect.name == "postgresql":
        # Same body as d1f7b25c8a3e; each statement issued separately
        # because asyncpg can't run multiple commands in one prepared
        # statement.
        op.execute("ALTER TABLE match_comments ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE match_comments FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {_POLICY} ON match_comments "
            f"FOR ALL "
            f"USING (user_id = current_setting('app.user_id', true)) "
            f"WITH CHECK (user_id = current_setting('app.user_id', true))"
        )


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON match_comments")
        op.execute("ALTER TABLE match_comments NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE match_comments DISABLE ROW LEVEL SECURITY")
    op.drop_index(_AUTHOR_INDEX, table_name="match_comments")
    op.drop_index(_TOKEN_INDEX, table_name="match_comments")
    op.drop_index(_THREAD_INDEX, table_name="match_comments")
    op.drop_table("match_comments")
