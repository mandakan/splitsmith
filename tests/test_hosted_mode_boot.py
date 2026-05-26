"""Tests for the hosted-mode bootstrap (doc 01, doc 10 Tier 3/2).

When ``SPLITSMITH_MODE=hosted`` is set, ``create_app`` swaps the
local-mode auth + per-user stores + job backend for their
Postgres-backed equivalents. These tests prove the wiring lands the
right classes on AppState, that :class:`HostedLoopbackAuth` is
idempotent across restarts, and that every store ends up bound to
the same user id.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from splitsmith.db import (
    HOSTED_LOOPBACK_EMAIL,
    Base,
    HostedLoopbackAuth,
    PostgresJobBackend,
    PostgresRecentProjectsStore,
    PostgresScoreboardIdentityStore,
    create_engine,
    sessionmaker,
)
from splitsmith.db import (
    User as UserRow,
)


@pytest.fixture
def hosted_db(tmp_path: Path) -> Iterator[str]:
    """A file-backed SQLite database with the schema applied + the
    SPLITSMITH_DATABASE_URL env var set for the lifetime of the test.

    File-backed (not ``:memory:``) because the API process boots the
    job backend on a thread pool that opens its own connections;
    in-memory SQLite is per-connection and the workers would see
    empty databases. The fixture also yields the URL so individual
    tests can drop additional connections against the same DB to
    inspect side effects.
    """
    db_path = tmp_path / "hosted.sqlite"
    url = f"sqlite+aiosqlite:///{db_path}"

    # Bring the schema up to head so HostedLoopbackAuth + the stores
    # can write without crashing on missing tables.
    engine = create_engine(url)

    async def _create_all() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())

    prior_url = os.environ.get("SPLITSMITH_DATABASE_URL")
    prior_mode = os.environ.get("SPLITSMITH_MODE")
    os.environ["SPLITSMITH_DATABASE_URL"] = url
    os.environ["SPLITSMITH_MODE"] = "hosted"
    try:
        yield url
    finally:
        if prior_url is None:
            os.environ.pop("SPLITSMITH_DATABASE_URL", None)
        else:
            os.environ["SPLITSMITH_DATABASE_URL"] = prior_url
        if prior_mode is None:
            os.environ.pop("SPLITSMITH_MODE", None)
        else:
            os.environ["SPLITSMITH_MODE"] = prior_mode


def test_hosted_loopback_auth_bootstraps_a_user_row(hosted_db: str) -> None:
    """Constructing the auth backend should leave exactly one row in
    ``users`` with the loopback email, regardless of how many times
    it's instantiated -- mirrors a server-restart loop."""
    factory = sessionmaker(create_engine(hosted_db))

    first = HostedLoopbackAuth(factory)
    second = HostedLoopbackAuth(factory)

    assert first.user_id == second.user_id  # same row reused on the second boot
    assert first.user.email == HOSTED_LOOPBACK_EMAIL
    assert first.user.display_name == "Hosted Operator"

    async def _count_rows() -> int:
        from sqlalchemy import func, select

        async with factory() as s:
            count = (
                await s.execute(
                    select(func.count()).select_from(UserRow).where(UserRow.email == HOSTED_LOOPBACK_EMAIL)
                )
            ).scalar_one()
            return int(count)

    assert asyncio.run(_count_rows()) == 1


def test_create_app_swaps_in_postgres_backends(hosted_db: str) -> None:
    """``create_app`` under ``SPLITSMITH_MODE=hosted`` must replace
    the local-mode default stores on AppState with Postgres-backed
    impls, all bound to the same user id."""
    from splitsmith.ui.server import create_app

    app = create_app()
    state = app.state.splitsmith_state

    assert isinstance(state.auth, HostedLoopbackAuth)
    assert isinstance(state.recent_projects, PostgresRecentProjectsStore)
    assert isinstance(state.scoreboard_identity, PostgresScoreboardIdentityStore)
    assert isinstance(state.jobs, PostgresJobBackend)
    # No S3 env vars set in this fixture -> storage stays unwired.
    assert state.storage is None

    expected_uid = state.auth.user_id
    # Probe the private attr so the test fails loudly if a future
    # refactor renames it -- the invariant we want to lock down is
    # that every store talks to the same user, not the exact attr
    # name.
    assert state.recent_projects._user_id == expected_uid
    assert state.scoreboard_identity._user_id == expected_uid
    assert state.jobs._user_id == expected_uid


def test_hosted_storage_wired_when_bucket_env_set(
    hosted_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``SPLITSMITH_S3_BUCKET`` is set in hosted mode the boot
    must construct an :class:`S3Storage` scoped under the loopback
    user's ``users/<id>/`` prefix. Boto3's client construction is
    lazy -- we don't need a real bucket to assert the wiring landed.
    """
    pytest.importorskip("moto")
    from moto import mock_aws

    from splitsmith.storage import S3Storage

    monkeypatch.setenv("SPLITSMITH_S3_BUCKET", "splitsmith-test")
    monkeypatch.setenv("SPLITSMITH_S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("SPLITSMITH_S3_REGION", "us-east-1")
    monkeypatch.setenv("SPLITSMITH_S3_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("SPLITSMITH_S3_SECRET_ACCESS_KEY", "secret")

    from splitsmith.ui.server import create_app

    with mock_aws():
        app = create_app()
    state = app.state.splitsmith_state

    assert isinstance(state.storage, S3Storage)
    assert state.storage.bucket == "splitsmith-test"
    assert state.storage.prefix == f"users/{state.auth.user_id}/"


def test_hosted_storage_misconfig_bucket_without_creds_fails_loud(
    hosted_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Half-configured S3 wiring is worse than no wiring -- the boot
    must raise so a typo'd creds doesn't 500 the upload endpoint
    on the first request."""
    monkeypatch.setenv("SPLITSMITH_S3_BUCKET", "splitsmith-test")
    monkeypatch.delenv("SPLITSMITH_S3_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("SPLITSMITH_S3_SECRET_ACCESS_KEY", raising=False)

    from splitsmith.ui.server import create_app

    with pytest.raises(RuntimeError, match="SPLITSMITH_S3_ACCESS_KEY_ID"):
        create_app()


def test_recent_projects_round_trip_through_hosted_app(hosted_db: str) -> None:
    """End-to-end against the live AppState wiring: record_open then
    list returns the entry. Proves the Postgres store is wired in and
    operating against the right user row."""
    from splitsmith.ui.server import create_app

    app = create_app()
    state = app.state.splitsmith_state

    async def _flow() -> None:
        await state.recent_projects.record_open(Path("/tmp/hosted-test-match"), "Hosted Test", kind="match")
        rows = await state.recent_projects.list()
        assert len(rows) == 1
        assert rows[0].name == "Hosted Test"
        assert rows[0].kind == "match"

    asyncio.run(_flow())


def test_create_app_errors_clearly_when_database_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hosted mode without ``SPLITSMITH_DATABASE_URL`` is a misconfig
    we want to surface loudly, not paper over with a default."""
    monkeypatch.setenv("SPLITSMITH_MODE", "hosted")
    monkeypatch.delenv("SPLITSMITH_DATABASE_URL", raising=False)
    from splitsmith.ui.server import create_app

    with pytest.raises(RuntimeError, match="SPLITSMITH_DATABASE_URL"):
        create_app()


def test_create_app_skips_hosted_wiring_when_mode_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without ``SPLITSMITH_MODE=hosted`` (or with any other value),
    the local-mode defaults stay in place even if
    ``SPLITSMITH_DATABASE_URL`` happens to be set."""
    monkeypatch.delenv("SPLITSMITH_MODE", raising=False)
    monkeypatch.setenv("SPLITSMITH_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/x.sqlite")
    from splitsmith.ui.jobs import JobRegistry
    from splitsmith.ui.server import create_app
    from splitsmith.user_config import (
        JsonRecentProjectsStore,
        JsonScoreboardIdentityStore,
    )

    app = create_app()
    state = app.state.splitsmith_state
    assert isinstance(state.recent_projects, JsonRecentProjectsStore)
    assert isinstance(state.scoreboard_identity, JsonScoreboardIdentityStore)
    assert isinstance(state.jobs, JobRegistry)
    # Storage stays unwired in local mode regardless of any S3 env
    # vars that happen to be set -- desktop reads/writes the user's
    # chosen project folder directly via pathlib.
    assert state.storage is None
