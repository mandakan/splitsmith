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


def test_create_app_builds_postgres_tenant_per_user(hosted_db: str) -> None:
    """``create_app`` under ``SPLITSMITH_MODE=hosted`` installs a tenant
    factory that builds Postgres-backed stores bound to a given user id.

    Stores are resolved per request (via ``current_tenant``) rather than
    bound to one user at boot, so the assertion is on ``build_tenant`` --
    every store in the returned context talks to the requested user."""
    from splitsmith.ui.server import create_app

    app = create_app()
    state = app.state.splitsmith_state

    assert isinstance(state.auth, HostedLoopbackAuth)

    uid = state.auth.user_id
    tenant = state.build_tenant(uid)
    assert isinstance(tenant.recent_projects, PostgresRecentProjectsStore)
    assert isinstance(tenant.scoreboard_identity, PostgresScoreboardIdentityStore)
    assert isinstance(tenant.jobs, PostgresJobBackend)
    # No S3 env vars set in this fixture -> storage stays unwired.
    assert tenant.storage is None

    # Probe the private attr so the test fails loudly if a future refactor
    # renames it -- the invariant is that every store in a tenant context
    # talks to that tenant's user, not the exact attr name.
    assert tenant.recent_projects._user_id == uid
    assert tenant.scoreboard_identity._user_id == uid
    assert tenant.jobs._user_id == uid

    # With no request in flight (no ``current_tenant`` pinned), the
    # per-user properties fall back to the local-mode singletons. The
    # hosted wiring never binds them, so ``storage`` reads back as None.
    assert state.storage is None


def test_per_user_properties_resolve_through_current_tenant(hosted_db: str) -> None:
    """The seam itself: ``state.jobs`` / ``recent_projects`` /
    ``scoreboard_identity`` / ``matches_store`` resolve to whichever
    tenant is pinned in ``current_tenant``, and fall back to the local
    singleton when none is. Two different tenants must resolve to stores
    bound to two different user ids through the *same* AppState -- this is
    what lets one process serve concurrent users without leaking across
    them once MagicLinkAuth lands."""
    from splitsmith.ui.server import create_app, current_tenant

    app = create_app()
    state = app.state.splitsmith_state

    # No tenant pinned -> the backing slot. In hosted mode that's the
    # boot backend (Postgres, holds the shared body registry + ran the
    # restart sweep), not a per-request one.
    assert current_tenant.get() is None
    assert isinstance(state.jobs, PostgresJobBackend)

    tenant_a = state.build_tenant("user-a")
    tenant_b = state.build_tenant("user-b")

    token = current_tenant.set(tenant_a)
    try:
        assert state.jobs._user_id == "user-a"
        assert state.recent_projects._user_id == "user-a"
        assert state.scoreboard_identity._user_id == "user-a"
        assert state.matches_store._user_id == "user-a"
    finally:
        current_tenant.reset(token)

    token = current_tenant.set(tenant_b)
    try:
        assert state.jobs._user_id == "user-b"
        assert state.recent_projects._user_id == "user-b"
    finally:
        current_tenant.reset(token)

    # The shared body registry is the same object across tenants (the
    # kind -> body mapping is a process constant, not per-user).
    assert tenant_a.jobs.bodies is tenant_b.jobs.bodies


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
        storage = state.build_tenant(state.auth.user_id).storage

    assert isinstance(storage, S3Storage)
    assert storage.bucket == "splitsmith-test"
    assert storage.prefix == f"users/{state.auth.user_id}/"


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
    store = state.build_tenant(state.auth.user_id).recent_projects

    async def _flow() -> None:
        await store.record_open(Path("/tmp/hosted-test-match"), "Hosted Test", kind="match")
        rows = await store.list()
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
