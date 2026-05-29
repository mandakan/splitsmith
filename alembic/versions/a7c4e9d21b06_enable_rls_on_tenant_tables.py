"""enable row-level security on tenant tables

Adds database-enforced tenant isolation on the three per-user tables
(``recent_projects``, ``matches``, ``compute_jobs``). Each gets ENABLE +
FORCE ROW LEVEL SECURITY and a single ``tenant_isolation`` policy keyed on
the ``app.user_id`` session GUC the app sets per connection (see
``splitsmith.db.engine.tenant_session_factory``).

This is the backstop for the application-layer ``WHERE user_id = ...``
filters every store already writes: even a future raw-SQL helper that
forgets that clause sees only the current tenant's rows.

- ``FORCE`` is required because the app role owns these tables, and the
  owner bypasses RLS without it.
- ``current_setting('app.user_id', true)`` (missing_ok) returns NULL when
  the GUC is unset, so the predicate is false and the query is fail-closed
  (0 rows / rejected writes) rather than leaking.
- ``WITH CHECK`` blocks inserting or updating a row into another tenant.

Postgres-only: SQLite (the unit-test engine) has no RLS, so the body is
dialect-guarded and no-ops there. The proof lives in the ``pytest -m
docker`` isolation test.

``users`` is deliberately excluded: auth must resolve identity (look up by
email) before any ``app.user_id`` exists, so an RLS'd ``users`` would break
login. The ``procrastinate_*`` queue tables are infra, not tenant tables.

Revision ID: a7c4e9d21b06
Revises: b74c9ab3ed2f
Create Date: 2026-05-29 13:10:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c4e9d21b06"
down_revision: str | Sequence[str] | None = "b74c9ab3ed2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The per-user tables that hold a ``user_id`` column. Keep in sync with the
# multi-tenant store classes in ``splitsmith.db``.
TENANT_TABLES = ("recent_projects", "matches", "compute_jobs")

_POLICY = "tenant_isolation"


def upgrade() -> None:
    """Upgrade schema."""
    if op.get_bind().dialect.name != "postgresql":
        # SQLite (unit tests) has no RLS. No-op so the suite still runs the
        # migration chain end to end.
        return
    for table in TENANT_TABLES:
        # Each statement issued separately: asyncpg cannot run multiple
        # commands in one prepared statement (see the #440 hotfix).
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
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
