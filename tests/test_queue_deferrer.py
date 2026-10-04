"""The queue deferrer keeps one open Procrastinate App per long-lived loop (#1199).

No Postgres: ``queue.build_app`` is patched to a fake whose connector
records opens and closes and, like a psycopg pool, is bound to the loop
that opened it and runs a background task there. ``LoopEngines`` is the
real one with ``create_async_engine`` patched, so adoption and shutdown go
through the same seam the hosted wiring uses. The real-Postgres session
count is ``tests/test_pooling_docker.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest

from splitsmith import queue
from splitsmith.async_bridge import DbRunner
from splitsmith.db import engine as engine_mod
from splitsmith.db.engine import LoopEngines

PG_URL = "postgresql+asyncpg://u:p@h/db"


class _FakeEngine:
    async def dispose(self) -> None:
        pass


class _FakeConnector:
    """Loop-bound like a psycopg pool: defers must come from the loop that
    opened it, and ``close`` waits on a worker task the pool runs there."""

    def __init__(self, log: dict[str, Any]) -> None:
        self._log = log
        self.loop: asyncio.AbstractEventLoop | None = None
        self.closed_cleanly = False
        self._worker: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None

    async def open_async(self) -> None:
        self._log["opens"] += 1
        fail = self._log["fail_opens"]
        if fail:
            self._log["fail_opens"] = fail - 1
            raise OSError("connection refused")
        # Yield as a real connect does, so concurrent defers can interleave.
        for _ in range(3):
            await asyncio.sleep(0)
        self.loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        self._worker = asyncio.create_task(self._stop.wait(), name="fake-pool-worker")

    async def close_async(self) -> None:
        self._log["closes"] += 1
        if self._worker is None:
            return
        assert self._stop is not None
        self._stop.set()
        await self._worker  # re-raises CancelledError if the worker was cancelled
        self._worker = None
        self.closed_cleanly = True


class _FakeTask:
    def __init__(self, app: _FakeApp, queue_name: str) -> None:
        self._app = app
        self._queue = queue_name

    async def defer_async(self, **kwargs: Any) -> None:
        conn = self._app.connector
        assert conn.loop is asyncio.get_running_loop(), "deferred from a loop other than the pool's"
        assert conn._worker is not None, "deferred on a closed connector"
        self._app.log["defers"].append((self._queue, kwargs["job_id"]))


class _FakeApp:
    def __init__(self, log: dict[str, Any]) -> None:
        self.log = log
        self.connector = _FakeConnector(log)

    def configure_task(self, **kwargs: str) -> _FakeTask:
        assert kwargs["name"] == queue.RUN_COMPUTE_JOB_TASK
        return _FakeTask(self, kwargs["queue"])

    @contextlib.asynccontextmanager
    async def open_async(self) -> AsyncIterator[_FakeApp]:
        # The pre-#1199 deferrer opened through the App; kept so the old
        # code runs against this fake and fails on counts, not on a missing
        # attribute.
        await self.connector.open_async()
        try:
            yield self
        finally:
            await self.connector.close_async()


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    log: dict[str, Any] = {"builds": 0, "opens": 0, "closes": 0, "fail_opens": 0, "defers": [], "apps": []}

    def _build(url: str, *, application_name: str | None = None) -> _FakeApp:
        log["builds"] += 1
        log.setdefault("names", []).append(application_name)
        app = _FakeApp(log)
        log["apps"].append(app)
        return app

    monkeypatch.setattr(queue, "build_app", _build)
    monkeypatch.setattr(queue, "_process_deferrers", {})
    monkeypatch.setattr(engine_mod, "create_async_engine", lambda url, **kw: _FakeEngine())
    return log


def _kwargs(i: int) -> dict[str, Any]:
    return {"job_id": f"job-{i}", "user_id": "u1", "kind": "trim", "args": {"i": i}, "match_id": None}


def test_submits_on_an_adopted_loop_open_the_connector_once(fake: dict[str, Any]) -> None:
    engines = LoopEngines(PG_URL)
    defer = queue.make_deferrer(PG_URL, loops=engines)

    async def _main() -> None:
        engines.adopt_current_loop()
        for i in range(5):
            await defer(**_kwargs(i))
        # Concurrent submits on the same loop must not race the open either.
        await asyncio.gather(*(defer(**_kwargs(i)) for i in range(5, 15)))
        assert fake["opens"] == 1
        assert fake["closes"] == 0, "the open App stays open between submits"
        await engines.dispose_current_loop()

    asyncio.run(_main())
    assert fake["builds"] == 1
    assert len(fake["defers"]) == 15
    assert {q for q, _ in fake["defers"]} == {"user-u1"}
    assert fake["closes"] == 1
    assert fake["apps"][0].connector.closed_cleanly


def test_concurrent_first_submits_share_one_open(fake: dict[str, Any]) -> None:
    engines = LoopEngines(PG_URL)
    defer = queue.make_deferrer(PG_URL, loops=engines)

    async def _main() -> None:
        engines.adopt_current_loop()
        await asyncio.gather(*(defer(**_kwargs(i)) for i in range(10)))
        await engines.dispose_current_loop()

    asyncio.run(_main())
    assert (fake["builds"], fake["opens"], fake["closes"]) == (1, 1, 1)
    assert len(fake["defers"]) == 10


def test_a_throwaway_loop_opens_and_closes_per_submit(fake: dict[str, Any]) -> None:
    engines = LoopEngines(PG_URL)
    defer = queue.make_deferrer(PG_URL, loops=engines)

    async def _main() -> None:
        # Not adopted: the fresh-loop fallback of ``run_sync`` looks like this.
        await defer(**_kwargs(1))
        await asyncio.gather(*(defer(**_kwargs(i)) for i in range(2, 5)))

    asyncio.run(_main())
    assert (fake["builds"], fake["opens"], fake["closes"]) == (4, 4, 4)
    assert all(app.connector.closed_cleanly for app in fake["apps"])
    assert len(fake["defers"]) == 4
    assert queue.loop_deferrers(PG_URL).open_count == 0


def test_without_a_registry_every_submit_opens_and_closes(fake: dict[str, Any]) -> None:
    defer = queue.make_deferrer(PG_URL)

    async def _main() -> None:
        await defer(**_kwargs(1))
        await defer(**_kwargs(2))

    asyncio.run(_main())
    assert (fake["builds"], fake["opens"], fake["closes"]) == (2, 2, 2)


def test_each_adopted_loop_gets_its_own_app_and_never_shares_it(fake: dict[str, Any]) -> None:
    engines = LoopEngines(PG_URL)
    defer = queue.make_deferrer(PG_URL, loops=engines)

    async def _main() -> None:
        engines.adopt_current_loop()
        await defer(**_kwargs(1))
        await defer(**_kwargs(2))
        await engines.dispose_current_loop()

    asyncio.run(_main())
    asyncio.run(_main())
    # The fake's defer asserts the pool's loop is the caller's, so a shared
    # App would have failed in the second run.
    assert (fake["builds"], fake["opens"], fake["closes"]) == (2, 2, 2)


def test_a_failed_open_is_not_cached(fake: dict[str, Any]) -> None:
    engines = LoopEngines(PG_URL)
    defer = queue.make_deferrer(PG_URL, loops=engines)
    fake["fail_opens"] = 1

    async def _main() -> None:
        engines.adopt_current_loop()
        with pytest.raises(OSError):
            await defer(**_kwargs(1))
        await defer(**_kwargs(2))
        await defer(**_kwargs(3))
        await engines.dispose_current_loop()

    asyncio.run(_main())
    # A fresh App for the retry (a pool that failed to open stays closed).
    assert fake["builds"] == 2
    assert fake["opens"] == 2
    assert [job for _, job in fake["defers"]] == ["job-2", "job-3"]


def test_the_dbrunner_closes_its_app_before_cancelling_the_loop(fake: dict[str, Any]) -> None:
    """The hosted DbRunner wiring: the App opened on the runner loop closes
    cleanly at stop. Without ``on_close`` the cancel sweep kills the pool's
    worker task first and ``close`` re-raises its cancellation (what a real
    psycopg pool does)."""
    engines = LoopEngines(PG_URL)
    defer = queue.make_deferrer(PG_URL, loops=engines)
    runner = DbRunner(
        on_start=engines.adopt_current_loop,
        on_close=engines.close_loop_resources,
        on_stop=engines.dispose_current_loop,
    )
    runner.start()
    try:
        for i in range(3):
            runner.run(defer(**_kwargs(i)))
    finally:
        runner.stop()
    assert (fake["builds"], fake["opens"], fake["closes"]) == (1, 1, 1)
    assert fake["apps"][0].connector.closed_cleanly
    assert queue.loop_deferrers(PG_URL).open_count == 0


def test_a_cancelled_pool_task_at_close_is_logged_not_raised(
    fake: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """Belt and braces for a loop whose tasks were cancelled before its
    resources closed: the closer must not crash the shutdown path."""
    engines = LoopEngines(PG_URL)
    defer = queue.make_deferrer(PG_URL, loops=engines)
    runner = DbRunner(on_start=engines.adopt_current_loop, on_stop=engines.dispose_current_loop)
    runner.start()
    try:
        runner.run(defer(**_kwargs(1)))
    finally:
        runner.stop()
    assert fake["closes"] == 1
    assert not fake["apps"][0].connector.closed_cleanly
    assert "interrupted by cancelled pool tasks" in caplog.text


def test_wiring_twice_reuses_the_process_deferrers(fake: dict[str, Any]) -> None:
    """The self-hosted agent re-wires hosted mode per drain; a second
    deferrer for the same URL must reuse the loop's open App rather than
    open a second one that nothing would close."""
    engines = LoopEngines(PG_URL)
    first = queue.make_deferrer(PG_URL, loops=engines)
    second = queue.make_deferrer(PG_URL, loops=engines)

    async def _main() -> None:
        engines.adopt_current_loop()
        await first(**_kwargs(1))
        await second(**_kwargs(2))
        await engines.dispose_current_loop()

    asyncio.run(_main())
    assert (fake["builds"], fake["opens"], fake["closes"]) == (1, 1, 1)


def test_wrap_deferrer_still_schedules_after_each_defer(fake: dict[str, Any]) -> None:
    from splitsmith.worker_trigger import wrap_deferrer

    class _Launcher:
        scheduled = 0

        def schedule(self) -> None:
            self.scheduled += 1

    engines = LoopEngines(PG_URL)
    launcher = _Launcher()
    defer = wrap_deferrer(queue.make_deferrer(PG_URL, loops=engines), launcher)  # type: ignore[arg-type]

    async def _main() -> None:
        engines.adopt_current_loop()
        await defer(**_kwargs(1))
        await defer(**_kwargs(2))
        await engines.dispose_current_loop()

    asyncio.run(_main())
    assert launcher.scheduled == 2
    assert fake["opens"] == 1


def test_every_app_carries_the_process_queue_name(fake: dict[str, Any]) -> None:
    """#1198: the persistent App and a one-off App on an un-adopted loop are
    both ``<process>-queue`` in ``pg_stat_activity``."""
    engines = LoopEngines(PG_URL)
    defer = queue.make_deferrer(PG_URL, application_name="splitsmith-serve-queue", loops=engines)

    async def _adopted() -> None:
        engines.adopt_current_loop()
        try:
            await defer(**_kwargs(1))
        finally:
            await engines.dispose_current_loop()

    async def _throwaway() -> None:
        await defer(**_kwargs(2))

    asyncio.run(_adopted())
    asyncio.run(_throwaway())
    assert fake["names"] == ["splitsmith-serve-queue", "splitsmith-serve-queue"]
