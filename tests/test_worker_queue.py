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


def test_build_app_pool_min_size_one_for_cold_start() -> None:
    """The connector pool must open with ``min_size=1`` so the one-shot cron
    worker's first connection lands inside Procrastinate's 30s ``open(wait=True)``
    window while a scale-to-zero Neon compute resumes. The psycopg_pool default
    of 4 forced four cold SSL connections at open and timed out (PoolTimeout)."""
    queue_app = build_app(_FAKE_PG_URL)
    pool_args = queue_app.connector._pool_args  # type: ignore[attr-defined]
    assert pool_args["min_size"] == 1
    assert pool_args["max_size"] == 4


def test_build_app_rejects_sqlite() -> None:
    """Procrastinate is Postgres-only; a SQLite URL must fail loudly at
    build time rather than dying deep in the connector."""
    with pytest.raises(ValueError, match="Postgres"):
        build_app("sqlite+aiosqlite:///x.db")


def test_run_worker_is_async() -> None:
    assert inspect.iscoroutinefunction(run_worker)


def test_run_worker_warms_ensemble_and_inits_sentry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Worker boot must: init Sentry once, build state, configure logging, then
    warm the ensemble singleton before draining -- so the first ``shot_detect``
    does not pay a hidden cold model load."""
    import asyncio
    import sys
    import types

    calls: list[str] = []

    # Stub the ui.server symbols run_worker imports lazily.
    server = types.ModuleType("splitsmith.ui.server")

    def _build_worker_state() -> object:
        calls.append("build_worker_state")
        return object()

    def _configure_app_logging() -> None:
        calls.append("configure_logging")

    def _warm() -> None:
        calls.append("warm")

    server.build_worker_state = _build_worker_state  # type: ignore[attr-defined]
    server._configure_app_logging = _configure_app_logging  # type: ignore[attr-defined]
    server.warm_ensemble_runtime = _warm  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "splitsmith.ui.server", server)

    import splitsmith.queue as queue_mod

    monkeypatch.setattr(queue_mod, "init_sentry", lambda **_k: calls.append("sentry"))

    class _FakeConnector:
        async def open_async(self) -> None:
            return None

        async def close_async(self) -> None:
            return None

    class _FakeApp:
        connector = _FakeConnector()

        async def run_worker_async(self, **_kwargs: object) -> None:
            calls.append("drain")

    monkeypatch.setattr(queue_mod, "build_app", lambda _url: _FakeApp())
    monkeypatch.setattr(queue_mod, "register_compute_task", lambda _app, _state: None)

    asyncio.run(run_worker(_FAKE_PG_URL))

    assert calls == ["sentry", "build_worker_state", "configure_logging", "warm", "drain"]


def test_run_worker_warmup_failure_is_non_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A model-load failure at boot must not crash the worker: it logs a
    warning and still drains (non-shot_detect jobs are unaffected; the cold
    cost is re-timed as the ``cold_model_load`` phase on the first detection)."""
    import asyncio
    import sys
    import types

    calls: list[str] = []
    server = types.ModuleType("splitsmith.ui.server")
    server.build_worker_state = lambda: object()  # type: ignore[attr-defined]
    server._configure_app_logging = lambda: None  # type: ignore[attr-defined]

    def _warm_boom() -> None:
        raise RuntimeError("model weights unreachable")

    server.warm_ensemble_runtime = _warm_boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "splitsmith.ui.server", server)

    import splitsmith.queue as queue_mod

    monkeypatch.setattr(queue_mod, "init_sentry", lambda **_k: None)

    class _FakeConnector:
        async def open_async(self) -> None:
            return None

        async def close_async(self) -> None:
            return None

    class _FakeApp:
        connector = _FakeConnector()

        async def run_worker_async(self, **_kwargs: object) -> None:
            calls.append("drain")

    monkeypatch.setattr(queue_mod, "build_app", lambda _url: _FakeApp())
    monkeypatch.setattr(queue_mod, "register_compute_task", lambda _app, _state: None)

    asyncio.run(run_worker(_FAKE_PG_URL))

    assert calls == ["drain"]  # warmup raised but the worker still reached drain


def test_run_worker_defaults_to_blocking_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default long-lived worker must pass ``wait=True`` to
    ``run_worker_async`` so it blocks on LISTEN/NOTIFY and keeps draining new
    jobs forever -- the always-on fleet behaviour."""
    import asyncio
    import sys
    import types

    server = types.ModuleType("splitsmith.ui.server")
    server.build_worker_state = lambda: object()  # type: ignore[attr-defined]
    server._configure_app_logging = lambda: None  # type: ignore[attr-defined]
    server.warm_ensemble_runtime = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "splitsmith.ui.server", server)

    import splitsmith.queue as queue_mod

    monkeypatch.setattr(queue_mod, "init_sentry", lambda **_k: None)

    captured: dict[str, object] = {}

    class _FakeConnector:
        async def open_async(self) -> None:
            return None

        async def close_async(self) -> None:
            return None

    class _FakeApp:
        connector = _FakeConnector()

        async def run_worker_async(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(queue_mod, "build_app", lambda _url: _FakeApp())
    monkeypatch.setattr(queue_mod, "register_compute_task", lambda _app, _state: None)

    asyncio.run(run_worker(_FAKE_PG_URL))

    assert captured["wait"] is True


def test_run_worker_one_shot_drains_and_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    """``run_worker(wait=False)`` must forward ``wait=False`` to
    ``run_worker_async`` -- the cron/one-shot drain that processes the jobs
    already queued and then returns, so nothing holds a connection open and
    the Neon compute can scale to zero between runs."""
    import asyncio
    import sys
    import types

    server = types.ModuleType("splitsmith.ui.server")
    server.build_worker_state = lambda: object()  # type: ignore[attr-defined]
    server._configure_app_logging = lambda: None  # type: ignore[attr-defined]
    server.warm_ensemble_runtime = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "splitsmith.ui.server", server)

    import splitsmith.queue as queue_mod

    monkeypatch.setattr(queue_mod, "init_sentry", lambda **_k: None)

    captured: dict[str, object] = {}

    class _FakeConnector:
        async def open_async(self) -> None:
            return None

        async def close_async(self) -> None:
            return None

    class _FakeApp:
        connector = _FakeConnector()

        async def run_worker_async(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(queue_mod, "build_app", lambda _url: _FakeApp())
    monkeypatch.setattr(queue_mod, "register_compute_task", lambda _app, _state: None)

    asyncio.run(run_worker(_FAKE_PG_URL, wait=False))

    assert captured["wait"] is False


def test_run_worker_retries_db_connect_then_drains(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient ``PoolTimeout`` on connect must be retried, not crash the run.
    The short-lived cron drain reconnects to a serverless Neon pool every tick and
    ``open(wait=True)`` intermittently times out; after a couple of failures the
    worker connects and drains."""
    import asyncio
    import sys
    import types

    import psycopg_pool

    server = types.ModuleType("splitsmith.ui.server")
    server.build_worker_state = lambda: object()  # type: ignore[attr-defined]
    server._configure_app_logging = lambda: None  # type: ignore[attr-defined]
    server.warm_ensemble_runtime = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "splitsmith.ui.server", server)

    import splitsmith.queue as queue_mod

    monkeypatch.setattr(queue_mod, "init_sentry", lambda **_k: None)
    monkeypatch.setattr(queue_mod, "register_compute_task", lambda _app, _state: None)

    seen: dict[str, object] = {"opens": 0, "drained": False, "sleeps": []}

    class _FakeConnector:
        async def open_async(self) -> None:
            seen["opens"] = int(seen["opens"]) + 1  # type: ignore[call-overload]
            if int(seen["opens"]) <= 2:  # type: ignore[call-overload]
                raise psycopg_pool.PoolTimeout("pool initialization incomplete after 30.0 sec")

        async def close_async(self) -> None:
            return None

    class _FakeApp:
        def __init__(self) -> None:
            self.connector = _FakeConnector()

        async def run_worker_async(self, **_kwargs: object) -> None:
            seen["drained"] = True

    # A fresh App per attempt: _open_app_with_retry rebuilds because a
    # timed-out pool is left closed and will not re-open.
    monkeypatch.setattr(queue_mod, "build_app", lambda _url: _FakeApp())

    async def _fast_sleep(delay: float) -> None:
        seen["sleeps"].append(delay)  # type: ignore[union-attr]

    monkeypatch.setattr(queue_mod.asyncio, "sleep", _fast_sleep)

    asyncio.run(run_worker(_FAKE_PG_URL, wait=False))

    assert seen["opens"] == 3  # two PoolTimeouts, then a successful connect
    assert seen["drained"] is True
    assert seen["sleeps"] == [3.0, 6.0]  # linear backoff between the two retries


def test_worker_command_one_shot_passes_wait_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """``splitsmith worker --one-shot`` runs the drain with ``wait=False`` so a
    Railway cron run exits once the queue is empty instead of blocking."""
    captured: dict[str, object] = {}

    async def _fake_run_worker(_db_url: str, *, concurrency: int = 1, wait: bool = True) -> None:
        captured["concurrency"] = concurrency
        captured["wait"] = wait

    import splitsmith.queue as queue_mod

    monkeypatch.setattr(queue_mod, "run_worker", _fake_run_worker)
    monkeypatch.setenv("SPLITSMITH_MODE", "hosted")
    monkeypatch.setenv("SPLITSMITH_DATABASE_URL", "postgresql+asyncpg://u:p@h/db")

    result = CliRunner().invoke(app, ["worker", "--one-shot"])

    assert result.exit_code == 0, result.stdout
    assert captured["wait"] is False


def test_worker_command_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing ``SPLITSMITH_DATABASE_URL`` exits 2 with a clear message,
    before any attempt to connect."""
    monkeypatch.delenv("SPLITSMITH_DATABASE_URL", raising=False)
    # The ``worker`` command ``setdefault``s SPLITSMITH_MODE=hosted in the
    # shared ``os.environ`` (CliRunner does NOT isolate env). A bare
    # ``delenv(raising=False)`` on an already-absent var records nothing on
    # monkeypatch's undo stack, so the in-process mutation would leak to
    # later tests (e.g. ui.server boots in hosted mode and raises). Setting
    # the key first forces monkeypatch to track it, then deleting it gives
    # the command a clean "unset" starting point; teardown restores absence.
    monkeypatch.setenv("SPLITSMITH_MODE", "hosted")
    monkeypatch.delenv("SPLITSMITH_MODE", raising=False)

    result = CliRunner().invoke(app, ["worker"])

    assert result.exit_code == 2
    assert "SPLITSMITH_DATABASE_URL is not set" in result.stdout
