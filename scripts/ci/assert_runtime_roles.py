"""CI gate: the runtime roles can write rows and nothing else, and RLS binds them.

Runs after ``assert_migrations_match_models.py`` against the same Postgres
service, schema at head, connected as the owner through
``SPLITSMITH_MIGRATION_DATABASE_URL``. It provisions the roles with
``scripts/db_roles.py`` (the script the operator runs on Neon), gives
``splitsmith_app`` a password, connects as it, and checks:

1. the boot check accepts it, and refuses the owner (a superuser here);
2. every table but the migration ledger has full row access, and every
   sequence and function is usable, so a migration that adds an object
   the default privileges miss fails here, not in production;
3. a tenant table's row is invisible without ``app.user_id`` and visible
   with it (RLS applies to the role);
4. DDL, relaxing RLS, writing ``alembic_version`` and creating roles are
   all refused.

The same checks were run by hand on a Neon branch of production, whose
owner is a non-superuser with ``BYPASSRLS``; this keeps them true as the
schema grows.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import secrets
import sys
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from splitsmith.db import create_engine, sessionmaker
from splitsmith.db.models import ExportPresetRow, User
from splitsmith.db.roles import APP_ROLE, assert_runtime_role

REPO_ROOT = Path(__file__).resolve().parents[2]

GRANT_GAPS_SQL = """
SELECT
  (SELECT count(*) FROM pg_class c
    WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')
      AND c.relname <> 'alembic_version'
      AND NOT (has_table_privilege(:role, c.oid, 'SELECT')
               AND has_table_privilege(:role, c.oid, 'INSERT')
               AND has_table_privilege(:role, c.oid, 'UPDATE')
               AND has_table_privilege(:role, c.oid, 'DELETE')))
    AS tables,
  (SELECT count(*) FROM pg_class c
    WHERE c.relnamespace = 'public'::regnamespace
      AND CASE WHEN c.relkind = 'S' THEN NOT has_sequence_privilege(:role, c.oid, 'USAGE') ELSE false END)
    AS sequences,
  (SELECT count(*) FROM pg_proc p
    WHERE p.pronamespace = 'public'::regnamespace AND NOT has_function_privilege(:role, p.oid, 'EXECUTE'))
    AS functions
"""

#: Statements the runtime role must be refused, with what each would do.
FORBIDDEN: tuple[tuple[str, str], ...] = (
    ("create a table", "CREATE TABLE public.runtime_role_probe (id int)"),
    ("relax RLS", "ALTER TABLE public.export_presets NO FORCE ROW LEVEL SECURITY"),
    ("write the migration ledger", "UPDATE public.alembic_version SET version_num = version_num"),
    ("create a role", "CREATE ROLE runtime_role_probe LOGIN"),
)


def _load_db_roles() -> Any:
    spec = importlib.util.spec_from_file_location("db_roles", REPO_ROOT / "scripts" / "db_roles.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


async def _check(owner_url: str, app_url: str) -> None:
    owner = create_engine(owner_url)
    app = create_engine(app_url)
    try:
        print("[2/5] the boot check accepts the runtime role and refuses the owner ...", flush=True)
        await assert_runtime_role(app)
        try:
            await assert_runtime_role(owner)
        except RuntimeError:
            pass
        else:
            _fail("assert_runtime_role accepted the owner, a superuser")

        print("[3/5] every table, sequence and function is reachable ...", flush=True)
        async with owner.connect() as conn:
            gaps = (await conn.execute(text(GRANT_GAPS_SQL), {"role": APP_ROLE})).one()
        if any(gaps):
            _fail(
                f"{APP_ROLE} is missing grants: {gaps.tables} table(s), {gaps.sequences} sequence(s), "
                f"{gaps.functions} function(s). An object the owner did not create escapes the default "
                "privileges; create it as the owner, or re-run scripts/db_roles.py apply."
            )

        print("[4/5] RLS hides another tenant's rows from the runtime role ...", flush=True)
        async with sessionmaker(owner)() as session:
            user = User(email=f"rls-probe-{secrets.token_hex(4)}@splitsmith.invalid")
            session.add(user)
            await session.flush()
            session.add(ExportPresetRow(user_id=user.id, preset_id="probe", name="probe", body={}))
            await session.commit()
            user_id = user.id
        async with app.connect() as conn:
            unscoped = (await conn.execute(text("SELECT count(*) FROM export_presets"))).scalar_one()
        async with app.begin() as conn:
            await conn.execute(text("SELECT set_config('app.user_id', :uid, true)"), {"uid": user_id})
            scoped = (await conn.execute(text("SELECT count(*) FROM export_presets"))).scalar_one()
        if unscoped != 0 or scoped != 1:
            _fail(f"RLS does not bind {APP_ROLE}: {unscoped} row(s) without app.user_id, {scoped} with it")

        print("[5/5] DDL, RLS changes, ledger writes and role creation are refused ...", flush=True)
        for what, statement in FORBIDDEN:
            try:
                async with app.begin() as conn:
                    await conn.execute(text(statement))
            except DBAPIError:
                continue
            _fail(f"{APP_ROLE} was allowed to {what}: {statement}")
    finally:
        await app.dispose()
        await owner.dispose()


def main() -> int:
    owner_url = os.environ.get("SPLITSMITH_MIGRATION_DATABASE_URL", "").strip()
    if not owner_url:
        _fail("SPLITSMITH_MIGRATION_DATABASE_URL is not set")
    db_roles = _load_db_roles()
    owner = make_url(owner_url)

    print("[1/5] provisioning the runtime roles (scripts/db_roles.py) ...", flush=True)
    db_roles._apply(owner)
    # A second apply must be a no-op, not an error: it runs on every grant change.
    db_roles._apply(owner)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "app.url"
        db_roles._rotate(owner, APP_ROLE, out, pooled=False)
        app_url = out.read_text()

    asyncio.run(_check(owner_url, app_url))
    print("OK: the runtime roles write rows and nothing else, and RLS binds them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
