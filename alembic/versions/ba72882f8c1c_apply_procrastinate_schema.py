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

import re
from collections.abc import Sequence

from alembic import op

revision: str = "ba72882f8c1c"
down_revision: str | Sequence[str] | None = "5f824d1e237f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Matches an opening/closing dollar-quote tag: ``$$`` or ``$name$``.
_DOLLAR_TAG_RE = re.compile(r"\$[A-Za-z0-9_]*\$")


def _split_sql_statements(sql: str) -> list[str]:
    """Split a multi-statement SQL script into individual statements.

    asyncpg (our production driver) rejects multiple commands in a
    single ``execute`` -- "cannot insert multiple commands into a
    prepared statement". Procrastinate ships its schema as one ~40-
    statement script, so we split on top-level ``;`` and run each
    statement on its own.

    The split is dollar-quote aware: Procrastinate's PL/pgSQL function
    bodies are wrapped in ``$$ ... $$`` (or ``$tag$ ... $tag$``) and
    contain their own semicolons that must NOT split the statement.
    ``--`` line comments are skipped so a semicolon inside a comment
    can't break a statement boundary.
    """
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    dollar_tag: str | None = None
    while i < n:
        if dollar_tag is None:
            # Line comment: skip to end of line.
            if sql.startswith("--", i):
                eol = sql.find("\n", i)
                if eol == -1:
                    break
                buf.append(sql[i:eol])
                i = eol
                continue
            # Opening dollar quote ($tag$).
            m = _DOLLAR_TAG_RE.match(sql, i)
            if m:
                dollar_tag = m.group(0)
                buf.append(dollar_tag)
                i = m.end()
                continue
            if sql[i] == ";":
                stmt = "".join(buf).strip()
                if stmt:
                    statements.append(stmt)
                buf = []
                i += 1
                continue
            buf.append(sql[i])
            i += 1
        else:
            # Inside a dollar-quoted body: only the matching tag closes it.
            if sql.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
                continue
            buf.append(sql[i])
            i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    from procrastinate.schema import SchemaManager

    for statement in _split_sql_statements(SchemaManager.get_schema()):
        op.execute(statement)


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
