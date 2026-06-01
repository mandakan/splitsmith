"""create state_docs table

Hosted match-state JSON docs (match / per-shooter project / per-stage
audit) move out of ephemeral-disk JSON files mirrored to S3 and into this
Postgres table with optimistic-concurrency versioning. See
:class:`splitsmith.db.models.StateDocRow` and
:class:`splitsmith.db.project_state.ProjectStateStore`.

The sharp edge here is uniqueness. A doc is logically unique on
``(user_id, match_id, doc_kind, slug, stage_number)`` -- but a plain
unique constraint over those columns is wrong: ``slug`` is NULL for the
``match`` kind and ``stage_number`` is NULL for ``match``/``project``,
and SQL treats NULL as distinct-from-NULL in a unique index, so two
``match`` rows (both NULL slug + stage) would coexist. On Postgres we use
an expression unique index over ``coalesce(slug,'')`` and
``coalesce(stage_number,-1)`` so the NULLs collapse to real values and
the constraint bites. SQLite (the unit-test engine, and the clean-DB
migration smoke test) has no expression-index-as-constraint story we need
here and never sees concurrent writers, so it gets a plain unique index --
its NULL-distinct weakness is harmless in tests.

The ``doc`` column starts as generic JSON (so ``create_table`` works on
both engines) and is ALTERed to JSONB on Postgres. RLS enablement is
inlined, same body as ``a7c4e9d21b06`` -- ``state_docs`` joins the
``tenant_isolation`` policy family.

Revision ID: d1f7b25c8a3e
Revises: c3f1a8e90d24
Create Date: 2026-06-01 09:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1f7b25c8a3e"
down_revision: str | Sequence[str] | None = "c3f1a8e90d24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY = "tenant_isolation"
_EXPR_INDEX = "uq_state_docs_identity"
_USER_INDEX = "ix_state_docs_user_id"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "state_docs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("match_id", sa.String(), nullable=False),
        sa.Column("doc_kind", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=True),
        sa.Column("stage_number", sa.Integer(), nullable=True),
        sa.Column("doc", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(_USER_INDEX, "state_docs", ["user_id"], unique=False)

    is_pg = op.get_bind().dialect.name == "postgresql"
    if is_pg:
        # Read whole / written whole, but JSONB is the correct column type
        # and keeps future indexed access open without another migration.
        op.execute("ALTER TABLE state_docs ALTER COLUMN doc TYPE JSONB USING doc::jsonb")
        # The NULL-collapsing expression index is the real uniqueness
        # guard (see module docstring).
        op.execute(
            f"CREATE UNIQUE INDEX {_EXPR_INDEX} ON state_docs "
            "(user_id, match_id, doc_kind, coalesce(slug, ''), coalesce(stage_number, -1))"
        )
    else:
        # SQLite test/smoke engine: plain unique index over the columns.
        # No concurrent writers + no duplicate-creating tests, so the
        # NULL-distinct weakness never triggers.
        op.create_index(
            _EXPR_INDEX,
            "state_docs",
            ["user_id", "match_id", "doc_kind", "slug", "stage_number"],
            unique=True,
        )

    if is_pg:
        # state_docs joins the tenant_isolation RLS policy family. Same
        # body as a7c4e9d21b06; each statement issued separately because
        # asyncpg can't run multiple commands in one prepared statement.
        op.execute("ALTER TABLE state_docs ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE state_docs FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {_POLICY} ON state_docs "
            f"FOR ALL "
            f"USING (user_id = current_setting('app.user_id', true)) "
            f"WITH CHECK (user_id = current_setting('app.user_id', true))"
        )


def downgrade() -> None:
    """Downgrade schema."""
    is_pg = op.get_bind().dialect.name == "postgresql"
    if is_pg:
        op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON state_docs")
        op.execute("ALTER TABLE state_docs NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE state_docs DISABLE ROW LEVEL SECURITY")
        op.execute(f"DROP INDEX IF EXISTS {_EXPR_INDEX}")
    else:
        op.drop_index(_EXPR_INDEX, table_name="state_docs")
    op.drop_index(_USER_INDEX, table_name="state_docs")
    op.drop_table("state_docs")
