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
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from splitsmith.auth import CompositeAuth
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

    assert isinstance(state.auth, CompositeAuth)
    assert isinstance(state.auth.backends[0], MagicLinkAuth)

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


def test_hosted_wiring_installs_loop_engines_and_the_process_runner(hosted_db: str) -> None:
    from splitsmith.async_bridge import get_runner
    from splitsmith.db import LoopEngines
    from splitsmith.ui.server import create_app

    app = create_app()
    state = app.state.splitsmith_state
    assert isinstance(state.db_engines, LoopEngines)
    assert state.db_engines.pooled is False, "SQLite in tests: one NullPool engine, today's behaviour"
    runner = get_runner()
    assert runner is not None and runner.is_running


def test_second_create_app_reuses_the_process_runner(hosted_db: str) -> None:
    from splitsmith.async_bridge import get_runner
    from splitsmith.ui.server import create_app

    create_app()
    first = get_runner()
    create_app()
    assert get_runner() is first
    assert sum(1 for t in threading.enumerate() if t.name == "splitsmith-db-runner") == 1


def test_local_mode_builds_no_engines_and_installs_no_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Desktop / ``splitsmith ui``: never a Postgres engine, never a runner.
    ``run_sync`` keeps its asyncio.run path by construction."""
    from splitsmith.async_bridge import get_runner, install_runner
    from splitsmith.ui.server import create_app

    # conftest's ``_stop_process_db_runner`` already stops any runner a
    # previous test left; stop one here too so this test never depends on it.
    leftover = get_runner()
    if leftover is not None:
        install_runner(None)
        leftover.stop()
    monkeypatch.delenv("SPLITSMITH_MODE", raising=False)
    monkeypatch.delenv("SPLITSMITH_DATABASE_URL", raising=False)
    app = create_app()
    assert app.state.splitsmith_state.db_engines is None
    assert get_runner() is None


def test_hosted_lifespan_adopts_the_app_loop_and_disposes_it_on_exit(
    hosted_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The main loop (uvicorn's; TestClient's portal here) adopts itself at
    startup and disposes its engine at shutdown. On SQLite both are no-ops
    inside LoopEngines, so assert through a spy."""
    from fastapi.testclient import TestClient

    from splitsmith.ui.server import create_app

    app = create_app()
    engines = app.state.splitsmith_state.db_engines
    calls: list[str] = []
    orig_adopt, orig_dispose = engines.adopt_current_loop, engines.dispose_current_loop

    def _adopt():  # noqa: ANN202
        calls.append("adopt")
        return orig_adopt()

    async def _dispose() -> None:
        calls.append("dispose")
        await orig_dispose()

    monkeypatch.setattr(engines, "adopt_current_loop", _adopt)
    monkeypatch.setattr(engines, "dispose_current_loop", _dispose)
    with TestClient(app):
        assert calls == ["adopt"]
    assert calls == ["adopt", "dispose"]


def _spy_on_loop_engines(engines: object, calls: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    orig_adopt = engines.adopt_current_loop  # type: ignore[attr-defined]
    orig_dispose = engines.dispose_current_loop  # type: ignore[attr-defined]

    def _adopt():  # noqa: ANN202
        calls.append("adopt")
        return orig_adopt()

    async def _dispose() -> None:
        calls.append("dispose")
        await orig_dispose()

    monkeypatch.setattr(engines, "adopt_current_loop", _adopt)
    monkeypatch.setattr(engines, "dispose_current_loop", _dispose)


def test_hosted_lifespan_adopts_before_the_boot_duties(
    hosted_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adopt precedes the first database use (the boot retrigger here), so
    the boot duties already run on the app loop's pooled engine."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from splitsmith.ui.server import _hosted_boot_lifespan, create_app

    state = create_app().state.splitsmith_state
    calls: list[str] = []
    _spy_on_loop_engines(state.db_engines, calls, monkeypatch)

    async def _retrigger() -> None:
        calls.append("retrigger")

    state.boot_retrigger = _retrigger
    lifespan = _hosted_boot_lifespan(state)
    assert lifespan is not None
    with TestClient(FastAPI(lifespan=lifespan)):
        pass
    assert calls == ["adopt", "retrigger", "dispose"]


def test_hosted_lifespan_disposes_when_a_boot_duty_fails(
    hosted_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A boot duty that raises after the adopt still disposes the engine.

    This is the case that pins the lifespan's ``finally``: TestClient sends a
    normal ``lifespan.shutdown`` even when the ``with`` body raises, so an
    exception there never reaches the generator and cannot tell a
    ``finally`` from code after a bare ``yield``."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from splitsmith.ui.server import _hosted_boot_lifespan, create_app

    state = create_app().state.splitsmith_state
    calls: list[str] = []
    _spy_on_loop_engines(state.db_engines, calls, monkeypatch)

    async def _retrigger() -> None:
        calls.append("retrigger")
        raise RuntimeError("retrigger down")

    state.boot_retrigger = _retrigger
    lifespan = _hosted_boot_lifespan(state)
    assert lifespan is not None
    with pytest.raises(RuntimeError, match="retrigger down"):
        with TestClient(FastAPI(lifespan=lifespan)):
            pass
    assert calls == ["adopt", "retrigger", "dispose"]


def test_rewiring_reuses_the_process_engines_so_the_runner_loop_stays_pooled(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The self-hosted agent calls ``run_worker`` once per wake in one process,
    so ``_apply_hosted_mode_wiring`` runs once per drain. The second wiring
    must reuse the first's ``LoopEngines``: the process ``DbRunner`` adopted
    its loop into that object, and a fresh one would serve every job-thread
    ``run_sync`` call from the NullPool fallback (#1178)."""

    from sqlalchemy.pool import NullPool

    from splitsmith.async_bridge import get_runner
    from splitsmith.db import engine as engine_mod
    from splitsmith.ui.server import AppState, _apply_hosted_mode_wiring

    made: list[Any] = []

    class _FakeEngine:
        def __init__(self, url: str, kwargs: dict[str, Any]) -> None:
            self.url = url
            self.kwargs = kwargs

        async def dispose(self) -> None:
            return None

    def _fake_create(url: str, **kwargs: Any) -> _FakeEngine:
        made.append(_FakeEngine(url, kwargs))
        return made[-1]

    monkeypatch.setattr(engine_mod, "create_async_engine", _fake_create)
    monkeypatch.setenv("SPLITSMITH_MODE", "hosted")
    monkeypatch.setenv("SPLITSMITH_DATABASE_URL", "postgresql+asyncpg://u:s3cret@h/db")
    monkeypatch.setenv("SPLITSMITH_PUBLIC_URL", PUBLIC_URL)

    caplog.set_level("INFO", logger="splitsmith.ui.server")
    first = AppState()
    _apply_hosted_mode_wiring(first, worker=True)
    second = AppState()
    _apply_hosted_mode_wiring(second, worker=True)

    assert first.db_engines is not None and first.db_engines.pooled
    assert second.db_engines is first.db_engines

    runner = get_runner()
    assert runner is not None and runner.is_running

    async def _ask() -> Any:
        assert second.db_engines is not None
        return second.db_engines.for_current_loop()

    on_runner = runner.run(_ask())
    assert on_runner.kwargs.get("poolclass") is not NullPool, "runner loop fell back to NullPool"
    # #1198: the runner loop's connections say which process and loop they serve.
    assert (
        on_runner.kwargs["connect_args"]["server_settings"]["application_name"] == "splitsmith-worker-runner"
    )

    # The wiring names the driver and whether it pooled; never the URL.
    assert "hosted database: driver=postgresql+asyncpg pooled=True" in caplog.text
    assert "s3cret" not in caplog.text


def _recording_engines(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    from splitsmith.db import engine as engine_mod

    made: list[Any] = []

    class _FakeEngine:
        def __init__(self, url: str, kwargs: dict[str, Any]) -> None:
            self.url = url
            self.kwargs = kwargs
            self.disposed = 0

        async def dispose(self) -> None:
            self.disposed += 1

    def _fake_create(url: str, **kwargs: Any) -> _FakeEngine:
        made.append(_FakeEngine(url, kwargs))
        return made[-1]

    monkeypatch.setattr(engine_mod, "create_async_engine", _fake_create)
    return made


def test_serve_wiring_names_its_engines_and_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """#1198: ``serve`` is ``splitsmith-serve`` on its engines and
    ``splitsmith-serve-queue`` on the deferrer's Procrastinate pool."""
    import splitsmith.queue as queue_mod
    from splitsmith.ui.server import AppState, _apply_hosted_mode_wiring

    _recording_engines(monkeypatch)
    deferrer_kwargs: list[dict[str, Any]] = []
    real_make_deferrer = queue_mod.make_deferrer

    def _make_deferrer(url: str, **kwargs: Any) -> Any:
        deferrer_kwargs.append(kwargs)
        return real_make_deferrer(url, **kwargs)

    monkeypatch.setattr(queue_mod, "make_deferrer", _make_deferrer)
    monkeypatch.setenv("SPLITSMITH_MODE", "hosted")
    monkeypatch.setenv("SPLITSMITH_DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    monkeypatch.setenv("SPLITSMITH_PUBLIC_URL", PUBLIC_URL)

    state = AppState()
    _apply_hosted_mode_wiring(state)
    assert deferrer_kwargs == [{"application_name": "splitsmith-serve-queue"}]

    async def _main() -> Any:
        assert state.db_engines is not None
        return state.db_engines.adopt_current_loop()

    main = asyncio.run(_main())
    assert main.kwargs["connect_args"]["server_settings"]["application_name"] == "splitsmith-serve"


def test_a_second_database_url_warns_that_the_runner_is_bound_elsewhere(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """#1201: the process ``DbRunner`` stays adopted into the first URL's
    engines, so a second URL's sync path is unpooled. Say so, by driver only."""
    from splitsmith.ui.server import _process_loop_engines

    _recording_engines(monkeypatch)
    caplog.set_level("INFO", logger="splitsmith.ui.server")
    first = _process_loop_engines("postgresql+asyncpg://u:first-pw@h/db")
    _process_loop_engines("postgresql+asyncpg://u:first-pw@h/db")
    assert "reuses the DbRunner" not in caplog.text, "the same URL again must not warn"

    second = _process_loop_engines("sqlite+aiosqlite:///second-pw.db")
    assert second is not first
    assert (
        "a second database URL (driver=sqlite+aiosqlite) reuses the DbRunner bound to another URL's "
        "engines (driver=postgresql+asyncpg)" in caplog.text
    )
    assert "first-pw" not in caplog.text and "second-pw" not in caplog.text

    # Once, when the engines are created; the next wiring of that URL is quiet.
    _process_loop_engines("sqlite+aiosqlite:///second-pw.db")
    assert caplog.text.count("reuses the DbRunner") == 1


def test_exit_stops_the_runner_and_disposes_its_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """#1197: an ``atexit`` hook stops the process ``DbRunner``, whose
    ``on_stop`` disposes the runner loop's pooled engine, so its connections
    close with a Terminate instead of dying with the interpreter."""
    import atexit

    from splitsmith.async_bridge import get_runner
    from splitsmith.ui import server

    made = _recording_engines(monkeypatch)
    hooks: list[Any] = []
    monkeypatch.setattr(atexit, "register", hooks.append)
    monkeypatch.setattr(server, "_runner_atexit_registered", False)

    engines = server._process_loop_engines("postgresql+asyncpg://u:p@h/db")
    runner = get_runner()
    assert runner is not None and runner.is_running
    assert hooks == [server._stop_process_runner]

    async def _ask() -> Any:
        return engines.for_current_loop()

    runner_engine = runner.run(_ask())
    assert runner_engine in made and runner_engine.disposed == 0

    hooks[0]()
    assert not runner.is_running
    assert get_runner() is None
    assert runner_engine.disposed == 1

    # Registered once per process, not once per runner a re-wiring starts.
    server._process_loop_engines("postgresql+asyncpg://u:p@h/db")
    assert len(hooks) == 1
