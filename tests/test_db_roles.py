"""Least-privilege database roles (``splitsmith.db.roles``, ``scripts/db_roles.py``).

The SQL itself was proven on a Neon branch of production: the runtime role
sees 0 of a tenant's rows without ``app.user_id`` and all of them with it,
cannot create tables, relax RLS, write ``alembic_version`` or create roles,
and inherits grants on tables the owner creates later. These tests pin the
rendered statements that proof depended on, the boot check, and the
migration-URL precedence.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import inspect
from sqlalchemy.engine import make_url

from splitsmith.db import create_engine
from splitsmith.db.roles import (
    AGENT_ROLE,
    ALLOW_PRIVILEGED_ROLE_ENV,
    APP_ROLE,
    RUNTIME_GROUP,
    assert_runtime_role,
    password_statement,
    privileged_role_problem,
    provision_statements,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location("db_roles_script", REPO_ROOT / "scripts" / "db_roles.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _statements() -> list[str]:
    return provision_statements(owner="neondb_owner", database="neondb")


def test_every_runtime_role_is_created_without_rls_bypass() -> None:
    joined = "\n".join(_statements())
    for role in (RUNTIME_GROUP, APP_ROLE, AGENT_ROLE):
        assert f'CREATE ROLE "{role}"' in joined
    creates = [s for s in _statements() if "CREATE ROLE" in s]
    assert len(creates) == 3
    for statement in creates:
        assert "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION" in statement


def test_alter_role_never_names_superuser() -> None:
    # Postgres 16+ refuses ``ALTER ROLE ... NOSUPERUSER`` from a non-superuser
    # owner ("permission denied to alter role" on Neon), which would fail the
    # whole provisioning transaction.
    for statement in _statements():
        alter = statement.partition("ALTER ROLE")[2]
        assert "SUPERUSER" not in alter


def test_login_roles_join_the_group_and_the_group_holds_the_grants() -> None:
    statements = _statements()
    assert f'GRANT "{RUNTIME_GROUP}" TO "{APP_ROLE}"' in statements
    assert f'GRANT "{RUNTIME_GROUP}" TO "{AGENT_ROLE}"' in statements
    grants = [s for s in statements if s.startswith("GRANT") and " ON " in s]
    assert grants and all(s.endswith(f'TO "{RUNTIME_GROUP}"') for s in grants)
    assert not any("CREATE" in s for s in grants if "ON SCHEMA" in s)


def test_default_privileges_are_keyed_on_the_owner() -> None:
    # Default privileges only apply to objects created by the role named in
    # FOR ROLE; migrations run as the owner, so it has to be the owner.
    defaults = [s for s in _statements() if s.startswith("ALTER DEFAULT PRIVILEGES")]
    assert {s.split(" ON ")[1].split(" TO ")[0] for s in defaults} == {
        "TABLES",
        "SEQUENCES",
        "FUNCTIONS",
        "TYPES",
    }
    assert all('FOR ROLE "neondb_owner"' in s for s in defaults)


def test_the_migration_ledger_is_read_only_for_the_runtime() -> None:
    revoke = [s for s in _statements() if "alembic_version" in s]
    assert len(revoke) == 1
    assert "REVOKE INSERT, UPDATE, DELETE, TRUNCATE" in revoke[0]


def test_identifiers_and_passwords_are_quoted() -> None:
    statements = provision_statements(owner='own"er', database="db", login_roles=("a'b",))
    assert any('FOR ROLE "own""er"' in s for s in statements)
    assert any("rolname = 'a''b'" in s for s in statements)
    assert password_statement(APP_ROLE, "it's") == f"ALTER ROLE \"{APP_ROLE}\" PASSWORD 'it''s'"


@pytest.mark.parametrize(
    ("superuser", "bypass", "expected"),
    [(False, False, None), (True, False, "is a superuser"), (False, True, "has BYPASSRLS")],
)
def test_privileged_role_problem(superuser: bool, bypass: bool, expected: str | None) -> None:
    problem = privileged_role_problem("neondb_owner", superuser=superuser, bypass_rls=bypass)
    if expected is None:
        assert problem is None
    else:
        assert problem is not None and expected in problem and "neondb_owner" in problem


class _FakeEngine:
    """Just enough AsyncEngine for ``assert_runtime_role``."""

    def __init__(self, dialect: str, row: SimpleNamespace | None = None) -> None:
        self.dialect = SimpleNamespace(name=dialect)
        self._row = row
        self.connected = False

    def connect(self) -> Any:
        engine = self

        class _Conn:
            async def __aenter__(self) -> Any:
                engine.connected = True
                return self

            async def __aexit__(self, *exc: object) -> None:
                return None

            async def execute(self, _stmt: object) -> Any:
                return SimpleNamespace(one=lambda: engine._row)

        return _Conn()


def test_boot_check_refuses_a_bypassrls_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ALLOW_PRIVILEGED_ROLE_ENV, raising=False)
    engine = _FakeEngine(
        "postgresql", SimpleNamespace(role="neondb_owner", rolsuper=False, rolbypassrls=True)
    )
    with pytest.raises(RuntimeError, match="BYPASSRLS"):
        asyncio.run(assert_runtime_role(engine))  # type: ignore[arg-type]


def test_boot_check_passes_a_least_privilege_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ALLOW_PRIVILEGED_ROLE_ENV, raising=False)
    engine = _FakeEngine("postgresql", SimpleNamespace(role=APP_ROLE, rolsuper=False, rolbypassrls=False))
    asyncio.run(assert_runtime_role(engine))  # type: ignore[arg-type]
    assert engine.connected


def test_boot_check_override_logs_instead(monkeypatch: pytest.MonkeyPatch, caplog: Any) -> None:
    monkeypatch.setenv(ALLOW_PRIVILEGED_ROLE_ENV, "1")
    engine = _FakeEngine(
        "postgresql", SimpleNamespace(role="neondb_owner", rolsuper=False, rolbypassrls=True)
    )
    asyncio.run(assert_runtime_role(engine))  # type: ignore[arg-type]
    assert "BYPASSRLS" in caplog.text


def test_boot_check_is_a_no_op_off_postgres() -> None:
    engine = _FakeEngine("sqlite")
    asyncio.run(assert_runtime_role(engine))  # type: ignore[arg-type]
    assert not engine.connected


def test_alembic_prefers_the_migration_url(tmp_path: Path) -> None:
    owner_db = tmp_path / "owner.sqlite"
    runtime_db = tmp_path / "runtime.sqlite"
    env = {
        **os.environ,
        "SPLITSMITH_MIGRATION_DATABASE_URL": f"sqlite+aiosqlite:///{owner_db}",
        "SPLITSMITH_DATABASE_URL": f"sqlite+aiosqlite:///{runtime_db}",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not runtime_db.exists()

    async def _tables() -> list[str]:
        engine = create_engine(f"sqlite+aiosqlite:///{owner_db}")
        try:
            async with engine.connect() as conn:
                return await conn.run_sync(lambda c: inspect(c).get_table_names())
        finally:
            await engine.dispose()

    assert "users" in asyncio.run(_tables())


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        (
            "ep-cool-bird-123.c-3.eu-central-1.aws.neon.tech",
            "ep-cool-bird-123-pooler.c-3.eu-central-1.aws.neon.tech",
        ),
        (
            "ep-cool-bird-123-pooler.c-3.eu-central-1.aws.neon.tech",
            "ep-cool-bird-123-pooler.c-3.eu-central-1.aws.neon.tech",
        ),
        ("postgres", "postgres"),
        ("db.example.com", "db.example.com"),
    ],
)
def test_pooled_host(host: str, expected: str) -> None:
    assert _load_script().pooled_host(host) == expected


def test_runtime_url_swaps_credentials_and_keeps_the_rest() -> None:
    owner = make_url(
        "postgresql+asyncpg://neondb_owner:ownerpw@ep-a-1.c-3.eu-central-1.aws.neon.tech/neondb?ssl=require"
    )
    url = _load_script().runtime_url(owner, role=APP_ROLE, password="newpw", pooled=True)
    assert url.username == APP_ROLE
    assert url.password == "newpw"
    assert url.host == "ep-a-1-pooler.c-3.eu-central-1.aws.neon.tech"
    assert url.database == "neondb"
    assert url.drivername == "postgresql+asyncpg"
    assert dict(url.query) == {"ssl": "require"}


def test_rotate_refuses_to_overwrite_and_leaves_no_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _load_script()
    owner = make_url("postgresql+asyncpg://neondb_owner:pw@ep-a-1.example.neon.tech/neondb")

    existing = tmp_path / "exists.url"
    existing.write_text("keep me")
    with pytest.raises(SystemExit):
        script._rotate(owner, APP_ROLE, existing, pooled=True)
    assert existing.read_text() == "keep me"

    async def _boom(*_args: object) -> None:
        raise RuntimeError("alter failed")

    monkeypatch.setattr(script, "_run", _boom)
    out = tmp_path / "new.url"
    with pytest.raises(RuntimeError, match="alter failed"):
        script._rotate(owner, APP_ROLE, out, pooled=True)
    assert not out.exists()


def test_rotate_writes_the_url_with_owner_only_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    script = _load_script()
    statements: list[str] = []

    async def _record(_owner: object, stmts: list[str]) -> None:
        statements.extend(stmts)

    monkeypatch.setattr(script, "_run", _record)
    owner = make_url("postgresql+asyncpg://neondb_owner:pw@ep-a-1.example.neon.tech/neondb")
    out = tmp_path / "app.url"
    script._rotate(owner, AGENT_ROLE, out, pooled=True)

    written = make_url(out.read_text())
    assert written.username == AGENT_ROLE
    assert out.stat().st_mode & 0o777 == 0o600
    assert statements == [password_statement(AGENT_ROLE, written.password or "")]
    # The password never reaches stdout.
    assert (written.password or "") not in capsys.readouterr().out


def test_serve_refuses_to_boot_when_the_role_check_fails(
    monkeypatch: pytest.MonkeyPatch,
    hosted_env: str,
) -> None:
    """The check is wired into serve's boot lifespan: a failing verdict stops
    the app before it serves a request."""
    from fastapi.testclient import TestClient

    import splitsmith.db.roles as roles
    from splitsmith.ui.server import create_app

    async def _refuse(_engine: object) -> None:
        raise RuntimeError("role check ran")

    monkeypatch.setattr(roles, "assert_runtime_role", _refuse)
    with pytest.raises(RuntimeError, match="role check ran"):
        with TestClient(create_app()):
            pass


def test_direct_url_strips_the_pooler_even_from_a_pooled_owner_url() -> None:
    owner = make_url(
        "postgresql+asyncpg://neondb_owner:pw@ep-a-1-pooler.c-3.eu-central-1.aws.neon.tech/neondb"
    )
    url = _load_script().runtime_url(owner, role=APP_ROLE, password="x", pooled=False)
    assert url.host == "ep-a-1.c-3.eu-central-1.aws.neon.tech"


def test_rotate_direct_out_shares_the_password_on_the_direct_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _load_script()

    async def _noop(*_args: object) -> None:
        return None

    monkeypatch.setattr(script, "_run", _noop)
    owner = make_url("postgresql+asyncpg://neondb_owner:pw@ep-a-1.example.neon.tech/neondb")
    script._rotate(owner, APP_ROLE, tmp_path / "p.url", pooled=True, direct_out=tmp_path / "d.url")
    pooled = make_url((tmp_path / "p.url").read_text())
    direct = make_url((tmp_path / "d.url").read_text())
    assert pooled.host == "ep-a-1-pooler.example.neon.tech"
    assert direct.host == "ep-a-1.example.neon.tech"
    assert pooled.password == direct.password
    assert (tmp_path / "d.url").stat().st_mode & 0o777 == 0o600
