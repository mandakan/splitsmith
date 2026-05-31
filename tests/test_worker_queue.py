"""PR-beta: the ``splitsmith worker`` CLI + queue smoke task.

These are db-free unit tests -- they build the Procrastinate App and
inspect it, but never open a connector or touch Postgres. The live
round-trip (API defers ``ping`` -> worker process runs it) is exercised
by the ``pytest -m docker`` smoke, not here.
"""

from __future__ import annotations

import inspect

import pytest
from typer.testing import CliRunner

from splitsmith.cli import app
from splitsmith.queue import _to_psycopg_dsn, build_app, run_worker

_FAKE_PG_URL = "postgresql+asyncpg://u:p@localhost:5432/db"


def test_to_psycopg_dsn_strips_async_dialect() -> None:
    assert _to_psycopg_dsn(_FAKE_PG_URL) == "postgresql://u:p@localhost:5432/db"


def test_to_psycopg_dsn_translates_ssl_and_drops_asyncpg_params() -> None:
    """A Neon-style asyncpg URL must become a valid libpq DSN: ``ssl`` ->
    ``sslmode`` and asyncpg-only ``prepared_statement_cache_size`` dropped,
    else psycopg raises ``invalid URI query parameter: "ssl"`` and the
    worker can't connect."""
    url = (
        "postgresql+asyncpg://u:p@ep-x-pooler.eu-central-1.aws.neon.tech/neondb"
        "?ssl=require&prepared_statement_cache_size=0"
    )
    dsn = _to_psycopg_dsn(url)
    assert dsn.startswith("postgresql://u:p@ep-x-pooler.eu-central-1.aws.neon.tech/neondb?")
    assert "sslmode=require" in dsn
    assert "ssl=require" not in dsn  # the asyncpg key must be gone
    assert "prepared_statement_cache_size" not in dsn


def test_to_psycopg_dsn_preserves_other_query_params() -> None:
    dsn = _to_psycopg_dsn("postgresql+asyncpg://u:p@h:5432/db?ssl=require&application_name=ss")
    assert "sslmode=require" in dsn
    assert "application_name=ss" in dsn


def test_build_app_registers_ping_task() -> None:
    """A worker built from :func:`build_app` must have ``ping`` ready to
    run; without it the PR-beta round-trip has nothing to dispatch."""
    queue_app = build_app(_FAKE_PG_URL)
    assert "ping" in queue_app.tasks


def test_build_app_rejects_sqlite() -> None:
    """Procrastinate is Postgres-only; a SQLite URL must fail loudly at
    build time rather than dying deep in the connector."""
    with pytest.raises(ValueError, match="Postgres"):
        build_app("sqlite+aiosqlite:///x.db")


def test_run_worker_is_async() -> None:
    assert inspect.iscoroutinefunction(run_worker)


def test_worker_command_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing ``SPLITSMITH_DATABASE_URL`` exits 2 with a clear message,
    before any attempt to connect."""
    monkeypatch.delenv("SPLITSMITH_DATABASE_URL", raising=False)
    # Tracked by monkeypatch so the command's ``setdefault`` is undone.
    monkeypatch.delenv("SPLITSMITH_MODE", raising=False)

    result = CliRunner().invoke(app, ["worker"])

    assert result.exit_code == 2
    assert "SPLITSMITH_DATABASE_URL is not set" in result.stdout
