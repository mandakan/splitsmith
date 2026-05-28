"""apply procrastinate schema

Revision ID: ba72882f8c1c
Revises: 5f824d1e237f
Create Date: 2026-05-27

Lands the Procrastinate (Postgres-native job queue) schema -- types,
tables, indexes, functions, triggers -- alongside our own
``compute_jobs`` table. ``compute_jobs`` stays the user-facing job
record; ``procrastinate_jobs`` (and friends) become the dispatch
layer that pops work to ``splitsmith worker`` processes.

This migration is **Postgres-only**. Procrastinate doesn't support
SQLite and its schema relies on custom types + PL/pgSQL functions
that have no SQLite equivalent. The aiosqlite-backed dev/smoke runs
(see ``pyproject.toml`` comment on the ``hosted`` extra) skip the
migration body entirely; nothing in those code paths exercises the
queue.

We embed the schema by calling ``procrastinate.schema.SchemaManager
.get_schema()`` so the SQL stays in lockstep with whatever
procrastinate version is installed. Bumping the procrastinate pin in
``pyproject.toml`` and re-running this migration on a fresh DB yields
the new schema automatically; for existing deploys, follow
procrastinate's release notes for any breaking migration steps.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "ba72882f8c1c"
down_revision: str | Sequence[str] | None = "5f824d1e237f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    from procrastinate.schema import SchemaManager

    op.execute(SchemaManager.get_schema())


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # Drop everything procrastinate owns. The schema only creates
    # objects prefixed ``procrastinate_*`` so a CASCADE on the public-
    # schema objects is safe. We list each object class explicitly so
    # this is auditable without rerunning the upstream schema script.
    op.execute("""
        DO $$
        DECLARE
            obj record;
        BEGIN
            FOR obj IN
                SELECT n.nspname AS schema, p.proname AS name,
                       pg_get_function_identity_arguments(p.oid) AS args
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE p.proname LIKE 'procrastinate_%'
            LOOP
                EXECUTE format(
                    'DROP FUNCTION IF EXISTS %I.%I(%s) CASCADE',
                    obj.schema, obj.name, obj.args
                );
            END LOOP;

            FOR obj IN
                SELECT c.relname AS name
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind = 'r' AND c.relname LIKE 'procrastinate_%'
            LOOP
                EXECUTE format('DROP TABLE IF EXISTS %I CASCADE', obj.name);
            END LOOP;

            FOR obj IN
                SELECT t.typname AS name
                FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE t.typname LIKE 'procrastinate_%'
            LOOP
                EXECUTE format('DROP TYPE IF EXISTS %I CASCADE', obj.name);
            END LOOP;
        END $$;
        """)
