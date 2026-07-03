"""Tests for the hosted-mode bootstrap (doc 01, doc 10 Tier 3/2).

When ``SPLITSMITH_MODE=hosted`` is set, ``create_app`` swaps the
local-mode auth + per-user stores for their Postgres-backed
equivalents. Auth is :class:`MagicLinkAuth` (real per-user accounts);
the per-user stores resolve per request via ``current_tenant``. These
tests prove the wiring installs the tenant factory + auth backend, that
two tenants resolve to isolated stores through one AppState, and that
hosted-mode misconfig fails loud.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from splitsmith.db import (
    Base,
    MagicLinkAuth,
    PostgresJobBackend,
    PostgresRecentProjectsStore,
    PostgresScoreboardIdentityStore,
    create_engine,
    new_ulid,
    sessionmaker,
)
from splitsmith.db import (
    User as UserRow,
)

PUBLIC_URL = "http://localhost:5174"


@pytest.fixture
def hosted_db(tmp_path: Path) -> Iterator[str]:
    """A file-backed SQLite database with the schema applied + the hosted
    env vars set for the lifetime of the test.

    File-backed (not ``:memory:``) because the API process boots the job
    backend on a thread pool that opens its own connections; in-memory
    SQLite is per-connection and the workers would see empty databases.
    The fixture yields the URL so individual tests can drop additional
    connections against the same DB to inspect side effects.
    """
    db_path = tmp_path / "hosted.sqlite"
    url = f"sqlite+aiosqlite:///{db_path}"

    engine = create_engine(url)

    async def _create_all() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())

    prior = {
        k: os.environ.get(k) for k in ("SPLITSMITH_DATABASE_URL", "SPLITSMITH_MODE", "SPLITSMITH_PUBLIC_URL")
    }
    os.environ["SPLITSMITH_DATABASE_URL"] = url
    os.environ["SPLITSMITH_MODE"] = "hosted"
    os.environ["SPLITSMITH_PUBLIC_URL"] = PUBLIC_URL
    try:
        yield url
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _seed_user(url: str, *, email: str = "seed@example.com") -> str:
    """Insert a ``users`` row directly (SQLite has no RLS) and return its id,
    so FK-bearing store writes have a real target without the magic-link
    dance."""
    user_id = new_ulid()

    async def _insert() -> None:
        factory = sessionmaker(create_engine(url))
        async with factory() as s:
            s.add(UserRow(id=user_id, email=email))
            await s.commit()

    asyncio.run(_insert())
    return user_id


def test_create_app_builds_postgres_tenant_per_user(hosted_db: str) -> None:
    """``create_app`` under hosted mode installs MagicLinkAuth + a tenant
    factory that builds Postgres-backed stores bound to a given user id.

    Stores are resolved per request (via ``current_tenant``), so the
    assertion is on ``build_tenant`` -- every store in the returned context
    talks to the requested user."""
    from splitsmith.ui.server import create_app

    app = create_app()
    state = app.state.splitsmith_state

    assert isinstance(state.auth, MagicLinkAuth)

    uid = "01TESTUSER0000000000000001"
    tenant = state.build_tenant(uid)
    assert tenant.user_id == uid
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

    # With no request in flight (no ``current_tenant`` pinned), the per-user
    # properties fall back to the local-mode singletons.
    assert state.storage is None


def test_per_user_properties_resolve_through_current_tenant(hosted_db: str) -> None:
    """The seam itself: ``state.jobs`` / ``recent_projects`` /
    ``scoreboard_identity`` / ``matches_store`` resolve to whichever tenant
    is pinned in ``current_tenant``, and fall back to the local backing slot
    when none is. Two different tenants must resolve to stores bound to two
    different user ids through the *same* AppState -- this is what lets one
    process serve concurrent users without leaking across them."""
    from splitsmith.ui.jobs import JobRegistry
    from splitsmith.ui.server import create_app, current_tenant

    app = create_app()
    state = app.state.splitsmith_state

    # No tenant pinned -> the backing slot. With MagicLinkAuth there is no
    # boot user, so the backing job backend is just the local JobRegistry
    # (a bodies holder; never serves queries because every request pins a
    # tenant first).
    assert current_tenant.get() is None
    assert isinstance(state.jobs, JobRegistry)

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

    # The shared body registry is the same object across tenants and equals
    # the process-level AppState registry (the kind -> body mapping is a
    # process constant, not per-user).
    assert tenant_a.jobs.bodies is tenant_b.jobs.bodies
    assert tenant_a.jobs.bodies is state.job_bodies


def test_hosted_storage_wired_when_bucket_env_set(
    hosted_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``SPLITSMITH_S3_BUCKET`` is set in hosted mode, a tenant's
    storage is an :class:`S3Storage` scoped under that user's ``users/<id>/``
    prefix. Boto3's client construction is lazy -- no real bucket needed."""
    pytest.importorskip("moto")
    from moto import mock_aws

    from splitsmith.storage import S3Storage

    monkeypatch.setenv("SPLITSMITH_S3_BUCKET", "splitsmith-test")
    monkeypatch.setenv("SPLITSMITH_S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("SPLITSMITH_S3_REGION", "us-east-1")
    monkeypatch.setenv("SPLITSMITH_S3_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("SPLITSMITH_S3_SECRET_ACCESS_KEY", "secret")

    from splitsmith.ui.server import create_app

    uid = "01TESTUSER0000000000000002"
    with mock_aws():
        app = create_app()
        state = app.state.splitsmith_state
        storage = state.build_tenant(uid).storage

    assert isinstance(storage, S3Storage)
    assert storage.bucket == "splitsmith-test"
    assert storage.prefix == f"users/{uid}/"


def test_hosted_storage_misconfig_bucket_without_creds_fails_loud(
    hosted_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Half-configured S3 wiring is worse than no wiring -- the boot must
    raise so typo'd creds don't 500 the upload endpoint on first request."""
    monkeypatch.setenv("SPLITSMITH_S3_BUCKET", "splitsmith-test")
    monkeypatch.delenv("SPLITSMITH_S3_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("SPLITSMITH_S3_SECRET_ACCESS_KEY", raising=False)

    from splitsmith.ui.server import create_app

    with pytest.raises(RuntimeError, match="SPLITSMITH_S3_ACCESS_KEY_ID"):
        create_app()


def test_recent_projects_round_trip_through_hosted_app(hosted_db: str) -> None:
    """End-to-end against the live AppState wiring: record_open then list
    returns the entry. Proves the per-tenant Postgres store is wired in and
    operates against the right user row."""
    from splitsmith.ui.server import create_app

    uid = _seed_user(hosted_db)
    app = create_app()
    state = app.state.splitsmith_state
    store = state.build_tenant(uid).recent_projects

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
    """Hosted mode without ``SPLITSMITH_DATABASE_URL`` is a misconfig we
    want to surface loudly, not paper over with a default."""
    monkeypatch.setenv("SPLITSMITH_MODE", "hosted")
    monkeypatch.delenv("SPLITSMITH_DATABASE_URL", raising=False)
    from splitsmith.ui.server import create_app

    with pytest.raises(RuntimeError, match="SPLITSMITH_DATABASE_URL"):
        create_app()


def test_create_app_errors_clearly_when_public_url_missing(
    hosted_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hosted mode needs ``SPLITSMITH_PUBLIC_URL`` (magic-link base + cookie
    Secure flag); its absence must fail loud, not guess an origin."""
    monkeypatch.delenv("SPLITSMITH_PUBLIC_URL", raising=False)
    from splitsmith.ui.server import create_app

    with pytest.raises(RuntimeError, match="SPLITSMITH_PUBLIC_URL"):
        create_app()


def test_cookie_secure_follows_public_url_scheme(
    hosted_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Secure is derived from the public URL scheme: off for http
    (docker/localhost, where a Secure cookie wouldn't round-trip), on for
    https (production)."""
    from splitsmith.ui.server import create_app

    # http (the fixture default) -> Secure off.
    state = create_app().state.splitsmith_state
    assert state.cookie_secure is False
    assert state.public_base_url == PUBLIC_URL

    # https -> Secure on.
    monkeypatch.setenv("SPLITSMITH_PUBLIC_URL", "https://splitsmith.app")
    state = create_app().state.splitsmith_state
    assert state.cookie_secure is True
    assert state.public_base_url == "https://splitsmith.app"


def test_create_app_skips_hosted_wiring_when_mode_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without ``SPLITSMITH_MODE=hosted`` (or with any other value), the
    local-mode defaults stay in place even if ``SPLITSMITH_DATABASE_URL``
    happens to be set."""
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
    assert state.storage is None
    # Local mode wires no auth config -- no cookies, no public URL.
    assert state.cookie_secure is False
    assert state.public_base_url is None


# ---------------------------------------------------------------------------
# Fix 3: worker process must not get launcher wiring
# ---------------------------------------------------------------------------


def test_worker_process_gets_no_boot_retrigger(
    hosted_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker process must never acquire launcher capabilities.

    Only the API process fires serviceInstanceRedeploy. When
    _apply_hosted_mode_wiring is called with worker=True, boot_retrigger
    must remain None even when all launcher env vars are present.
    """
    monkeypatch.setenv("SPLITSMITH_WORKER_TRIGGER_TOKEN", "t")
    monkeypatch.setenv("SPLITSMITH_WORKER_SERVICE_ID", "s")
    monkeypatch.setenv("SPLITSMITH_WORKER_ENVIRONMENT_ID", "e")

    from splitsmith.ui.server import AppState, _apply_hosted_mode_wiring

    state = AppState()
    _apply_hosted_mode_wiring(state, worker=True)
    assert state.boot_retrigger is None


# ---------------------------------------------------------------------------
# Fix 4: launcher wiring regression test - lifespan runs boot_retrigger
# ---------------------------------------------------------------------------


def test_create_app_launcher_wiring_and_boot_retrigger_lifespan(
    hosted_db: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When launcher env vars are present in hosted mode, create_app must wire
    boot_retrigger, and the startup lifespan must fire it. SQLite has no
    procrastinate_jobs table, so the check fails - the warning must be logged
    and the boot must NOT crash.

    This test fails on any future regression that deletes the lifespan kwarg
    or the boot_retrigger wiring assignment.
    """
    import logging

    from fastapi.testclient import TestClient

    from splitsmith.ui.server import create_app

    monkeypatch.setenv("SPLITSMITH_WORKER_TRIGGER_TOKEN", "t")
    monkeypatch.setenv("SPLITSMITH_WORKER_SERVICE_ID", "s")
    monkeypatch.setenv("SPLITSMITH_WORKER_ENVIRONMENT_ID", "e")

    app = create_app()
    state = app.state.splitsmith_state
    assert state.boot_retrigger is not None, "boot_retrigger must be wired when launcher env vars are set"

    with caplog.at_level(logging.WARNING, logger="splitsmith"):
        with TestClient(app):
            pass  # lifespan fires here

    assert any(
        "boot re-trigger" in r.message and "failed" in r.message for r in caplog.records
    ), "boot_retrigger warning not logged - lifespan may not have fired"
