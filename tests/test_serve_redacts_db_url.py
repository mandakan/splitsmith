"""``splitsmith serve`` never prints the database password.

The startup line naming the migration target lands in the platform's
logs, which more people can read than the database.
"""

from __future__ import annotations

import subprocess

import pytest
from typer.testing import CliRunner

from splitsmith.cli import app

SECRET = "s3cr3t-pw"
URL = f"postgresql+asyncpg://owner:{SECRET}@db.example.com:5432/splitsmith?sslmode=require"


def test_serve_startup_line_masks_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLITSMITH_DATABASE_URL", URL)
    # serve setdefaults this in os.environ; owning it here lets teardown
    # restore it, so hosted mode never leaks into later tests on the worker.
    monkeypatch.setenv("SPLITSMITH_MODE", "hosted")

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        # Fail the migration so serve exits before starting a server.
        return subprocess.CompletedProcess(args=[], returncode=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = CliRunner().invoke(app, ["serve"])

    assert result.exit_code == 2
    assert "applying migrations against" in result.output
    assert SECRET not in result.output
    assert "db.example.com" in result.output


def test_an_unparseable_url_is_not_echoed() -> None:
    from splitsmith.cli import _redacted_db_url

    assert _redacted_db_url(f"not a url {SECRET}") == "(unparseable database URL)"
