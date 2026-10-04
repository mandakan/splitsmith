# Hosted Connection Pooling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hosted engine's `NullPool` with a pool per long-lived event loop, routing every sync database caller through one persistent `DbRunner` thread, so no asyncpg connection is ever used from a loop other than the one that created it.

**Architecture:** `splitsmith.async_bridge.DbRunner` is a daemon thread with a persistent loop; `run_sync` submits to it when one is installed and otherwise behaves exactly as today. `splitsmith.db.engine.LoopEngines` hands out one pooled `AsyncEngine` per *adopted* loop (the runner's, uvicorn's, the Procrastinate worker's) and a single shared `NullPool` engine to any other loop, which is today's behaviour. The hosted wiring builds both; local mode builds neither.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0.50 async (`AsyncAdaptedQueuePool`), asyncpg 0.31, FastAPI/Starlette lifespan, pytest (+ the `docker` marker's compose stack).

**Spec:** `docs/superpowers/specs/2026-10-03-hosted-connection-pooling-design.md`

## Global Constraints

- Desktop / local mode must not change behaviour: it never builds a Postgres engine and never installs a runner; every shared path takes the "no runner" branch of `run_sync`, which is today's code.
- Docker compose (hosted on `postgres:16-alpine` + the `worker` service) must boot and run a job with no `attached to a different loop` in either container's log.
- No module-level import of `splitsmith.db` may be added to anything on the `create_app` path (`scripts/ci/assert_slim_import_surface.py`). `async_bridge` is imported by `match_model` / `match_project` and must stay free of `splitsmith.db`.
- The pool makes no traffic the request did not ask for: no warm-up, no periodic ping, no `pool_recycle`. `pool_pre_ping=True` only.
- Pool settings: `pool_size=5`, `max_overflow=10`, `pool_timeout=30`, `pool_pre_ping=True`.
- The URL's `prepared_statement_cache_size=0` stays (Neon's PgBouncer, transaction mode); `create_engine` passes the URL through untouched.
- `create_engine(url, *, echo=False, pool_disabled=False)` keeps its signature: `tests/test_postgres_job_backend.py`, `tests/test_ui_server.py`, `tests/test_device_auth_docker.py`, `tests/test_share_readonly_docker.py`, `tests/test_sync_docker.py` and `tests/test_hosted_docker_smoke.py` pass `pool_disabled=True` on purpose.
- `alembic/env.py` is untouched (own engine, `poolclass=NullPool`, #559 retry).
- Code style: Black 110, Ruff, type hints, `from __future__ import annotations`, stdlib / third-party / local import groups.
- Tests run with `uv run pytest`; `-n0` for a single file; docker tests with `uv run pytest -m docker -n0 <file>`.

## Review Focus

Inputs the spec implies but no single task's happy path exercises. Each has its pinning test in the owning task.

1. **A coroutine already running on the runner loop calls `run_sync`** (a sync state accessor called from inside a coroutine that a sync handler submitted). Expected: it completes, it does not deadlock the runner. Pinned in Task 1 (`test_run_sync_nested_on_runner_loop_falls_back_to_a_fresh_loop`).
2. **A read-scoped share request reaches the database through the runner.** Expected: the RLS listener still sees `current_share_scope` and issues `SET TRANSACTION READ ONLY`. Pinned in Task 1 (`test_runner_propagates_contextvars`) and Task 2 (`test_loop_sessionmaker_sessions_carry_the_tenant_listener`).
3. **A loop that was never adopted asks for an engine** (a `concurrent.futures` thread, a test's `asyncio.run`). Expected: a usable engine that never reuses a connection across loops. Pinned in Task 2 (`test_unadopted_loop_gets_the_shared_nullpool_engine`).
4. **The hosted wiring runs twice in one process** (`create_app` called in several tests). Expected: one runner, not one per app; the second app reuses it. Pinned in Task 4 (`test_second_create_app_reuses_the_process_runner`).
5. **Neon closes a pooled idle connection after five minutes.** Expected: the next checkout reconnects silently. Pinned in Task 2 by asserting `pool_pre_ping` is set on every pooled engine (`test_pooled_engine_settings`); the live proof is the docker test's second pass after `pg_terminate_backend` in Task 5.

---

### Task 1: `DbRunner` and `run_sync` routing

**Files:**
- Modify: `src/splitsmith/async_bridge.py`
- Test: `tests/test_async_bridge.py` (new)

**Interfaces:**
- Produces:
  - `class DbRunner:` with `__init__(self, *, on_start: Callable[[], object] | None = None, on_stop: Callable[[], Awaitable[None]] | None = None)`, `start() -> None`, `stop() -> None`, `run(coro: Coroutine[Any, Any, T]) -> T`, property `loop -> asyncio.AbstractEventLoop`, property `is_running -> bool`.
  - `install_runner(runner: DbRunner | None) -> None`, `get_runner() -> DbRunner | None`.
  - `run_sync(coro)` unchanged signature; routes to the installed runner.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for :mod:`splitsmith.async_bridge`: the DbRunner thread and run_sync routing."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator

import pytest

from splitsmith import async_bridge
from splitsmith.async_bridge import DbRunner, get_runner, install_runner, run_sync
from splitsmith.db.share_guard import current_share_scope, share_request_is_read_only


@pytest.fixture
def runner() -> Iterator[DbRunner]:
    r = DbRunner()
    r.start()
    install_runner(r)
    try:
        yield r
    finally:
        install_runner(None)
        r.stop()


def test_runner_runs_coroutine_from_a_thread_with_no_loop(runner: DbRunner) -> None:
    async def _which() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    assert runner.run(_which()) is runner.loop


def test_runner_runs_coroutine_from_inside_another_running_loop(runner: DbRunner) -> None:
    async def _which() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    async def _outer() -> asyncio.AbstractEventLoop:
        # A sync accessor called from an async handler: a loop is running here.
        return runner.run(_which())

    assert asyncio.run(_outer()) is runner.loop


def test_runner_surfaces_the_coroutine_exception(runner: DbRunner) -> None:
    async def _boom() -> None:
        raise ValueError("from the runner")

    with pytest.raises(ValueError, match="from the runner"):
        runner.run(_boom())


def test_runner_propagates_contextvars(runner: DbRunner) -> None:
    """The RLS listener reads ``current_share_scope`` at transaction begin.
    Without an explicit context the task would see the runner thread's
    empty context and a read-scoped share request would stop getting
    ``SET TRANSACTION READ ONLY`` (#779)."""

    async def _read_only() -> bool:
        return share_request_is_read_only()

    token = current_share_scope.set("read")
    try:
        assert runner.run(_read_only()) is True
    finally:
        current_share_scope.reset(token)
    assert runner.run(_read_only()) is False


def test_runner_stop_joins_the_thread() -> None:
    r = DbRunner()
    r.start()
    assert r.is_running
    r.stop()
    assert not r.is_running
    assert not any(t.name == "splitsmith-db-runner" for t in threading.enumerate())


def test_runner_calls_on_start_on_its_loop_and_on_stop_before_exit() -> None:
    seen: dict[str, object] = {}

    def _on_start() -> None:
        seen["start_loop"] = asyncio.get_running_loop()

    async def _on_stop() -> None:
        seen["stop_loop"] = asyncio.get_running_loop()

    r = DbRunner(on_start=_on_start, on_stop=_on_stop)
    r.start()
    loop = r.loop
    r.stop()
    assert seen["start_loop"] is loop
    assert seen["stop_loop"] is loop


def test_run_sync_uses_the_installed_runner_from_both_caller_kinds(runner: DbRunner) -> None:
    async def _which() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    assert run_sync(_which()) is runner.loop

    async def _outer() -> asyncio.AbstractEventLoop:
        return run_sync(_which())

    assert asyncio.run(_outer()) is runner.loop


def test_run_sync_nested_on_runner_loop_falls_back_to_a_fresh_loop(runner: DbRunner) -> None:
    """A coroutine on the runner loop that calls run_sync must not block
    the runner waiting on itself. It gets today's throwaway-thread path."""

    async def _inner() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    async def _on_runner() -> asyncio.AbstractEventLoop:
        return run_sync(_inner())

    got = runner.run(_on_runner())
    assert got is not runner.loop


def test_run_sync_without_a_runner_keeps_todays_behaviour() -> None:
    assert get_runner() is None

    async def _which() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    # No loop on this thread: asyncio.run, a fresh loop that is closed after.
    loop = run_sync(_which())
    assert loop.is_closed()

    async def _outer() -> asyncio.AbstractEventLoop:
        return run_sync(_which())

    # A running loop: a throwaway thread with its own loop, not the caller's.
    outer_loop_holder: list[asyncio.AbstractEventLoop] = []

    async def _outer2() -> asyncio.AbstractEventLoop:
        outer_loop_holder.append(asyncio.get_running_loop())
        return run_sync(_which())

    inner = asyncio.run(_outer2())
    assert inner is not outer_loop_holder[0]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_async_bridge.py -n0 -q`
Expected: FAIL with `ImportError: cannot import name 'DbRunner'`.

- [ ] **Step 3: Implement `DbRunner`, `install_runner`, `get_runner`, and reroute `run_sync`**

Replace the body of `src/splitsmith/async_bridge.py` below the imports with:

```python
"""Run an async coroutine to completion from synchronous code.

The hosted-mode per-user stores (``ProjectStateStore`` et al.) are async,
but the call sites that drive them are a mix:

- **Sync FastAPI handlers** run in a threadpool with *no* event loop on
  the thread.
- **Async FastAPI handlers** run on the event loop; ``asyncio.run`` there
  raises ``RuntimeError: asyncio.run() cannot be called from a running
  event loop``.
- The model ``save()`` methods and the ``AppState`` state accessors are
  *sync* (so the ~100 handler call sites that use them stay unchanged --
  the whole point of the state-refactor seam) yet are reached from both
  kinds of handler.

:func:`run_sync` papers over the difference. When a :class:`DbRunner` is
installed (hosted mode), every call lands on the runner's persistent
event loop, so the pooled asyncpg engine bound to that loop is reused
across calls (#1178). asyncpg connections are loop-bound: a connection
created on one loop crashes when used from another ("attached to a
different loop", #423), which is why database work must never run on a
throwaway loop in hosted mode.

Without a runner (local mode, which has no database engine) the old
behaviour stands: no running loop -> ``asyncio.run`` inline; a loop
already running -> a throwaway worker thread with its own fresh loop.
That path is also the fallback for the one re-entrant case, a coroutine
*on* the runner loop calling :func:`run_sync` -- blocking the runner on
itself would deadlock, and the fresh loop gets the shared NullPool engine
from :class:`splitsmith.db.engine.LoopEngines`, so it is safe.

This module must not import :mod:`splitsmith.db`: it is on the local-mode
import path (``match_model`` / ``match_project``) that the slim install
check builds without the hosted dependencies.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import logging
import threading
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

_RUNNER_THREAD_NAME = "splitsmith-db-runner"


class DbRunner:
    """A daemon thread with a persistent event loop for sync callers.

    ``on_start`` runs on the loop thread once the loop is running (the
    hosted wiring passes ``LoopEngines.adopt_current_loop`` so the loop
    gets its pooled engine); ``on_stop`` is awaited on the loop just
    before it closes (``LoopEngines.dispose_current_loop``).
    """

    def __init__(
        self,
        *,
        on_start: Callable[[], object] | None = None,
        on_stop: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        # ``object`` return, not ``None``: the hosted wiring passes
        # ``LoopEngines.adopt_current_loop``, which returns the engine.
        self._on_start = on_start
        self._on_stop = on_stop
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            raise RuntimeError("DbRunner has not been started")
        return self._loop

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._main, name=_RUNNER_THREAD_NAME, daemon=True)
        self._thread.start()
        self._ready.wait()

    def _main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)

        async def _boot() -> None:
            if self._on_start is not None:
                self._on_start()
            self._ready.set()

        loop.run_until_complete(_boot())
        try:
            loop.run_forever()
        finally:
            try:
                if self._on_stop is not None:
                    loop.run_until_complete(self._on_stop())
            except Exception:  # noqa: BLE001 -- shutdown must not raise out of the thread
                logger.warning("DbRunner on_stop failed", exc_info=True)
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    def stop(self) -> None:
        thread = self._thread
        if thread is None or self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        thread.join(timeout=30.0)
        self._thread = None

    def run(self, coro: Coroutine[Any, Any, _T]) -> _T:
        """Run ``coro`` on the runner loop and block until it is done.

        The caller's :mod:`contextvars` context travels with the task:
        ``asyncio.run_coroutine_threadsafe`` creates the task on the
        target thread, which would otherwise hand the RLS listener the
        runner thread's empty ``current_share_scope``.
        """
        ctx = contextvars.copy_context()
        loop = self.loop

        async def _in_context() -> _T:
            return await asyncio.create_task(coro, context=ctx)

        return asyncio.run_coroutine_threadsafe(_in_context(), loop).result()


_runner: DbRunner | None = None


def install_runner(runner: DbRunner | None) -> None:
    """Make ``runner`` the process-wide target of :func:`run_sync` (``None`` uninstalls)."""
    global _runner
    _runner = runner


def get_runner() -> DbRunner | None:
    return _runner


def _run_on_fresh_loop(coro: Coroutine[Any, Any, _T]) -> _T:
    """Today's behaviour: inline ``asyncio.run`` with no running loop, a
    throwaway thread with its own loop otherwise."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def run_sync(coro: Coroutine[Any, Any, _T]) -> _T:
    """Drive ``coro`` to completion regardless of the caller's loop state."""
    runner = _runner
    if runner is None or not runner.is_running:
        return _run_on_fresh_loop(coro)
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is runner.loop:
        # Re-entrant: a coroutine on the runner loop reached a sync
        # accessor. Blocking the runner on itself would deadlock.
        return _run_on_fresh_loop(coro)
    return runner.run(coro)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_async_bridge.py -n0 -q`
Expected: 10 passed.

- [ ] **Step 5: Run the callers' existing tests**

Run: `uv run pytest tests/test_sync_pull.py tests/test_sync_integration.py tests/test_match_model.py -q`
Expected: all pass (no runner installed, so behaviour is unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/async_bridge.py tests/test_async_bridge.py
git commit -m "feat(db): DbRunner, a persistent loop for sync database callers (#1178)"
```

---

### Task 2: `LoopEngines` and `loop_sessionmaker`

**Files:**
- Modify: `src/splitsmith/db/engine.py`
- Modify: `src/splitsmith/db/__init__.py:33,105-108`
- Test: `tests/test_db_engine_loops.py` (new)

**Interfaces:**
- Consumes: nothing from Task 1 (independent).
- Produces:
  - `class LoopEngines:` with `__init__(self, url: str, *, echo: bool = False, pool_size: int = 5, max_overflow: int = 10)`, `pooled: bool` property (True for `postgresql+asyncpg`), `adopt_current_loop() -> AsyncEngine`, `for_current_loop() -> AsyncEngine`, `async dispose_current_loop() -> None`, `async dispose_fallback() -> None`.
  - `loop_sessionmaker(engines: LoopEngines) -> Callable[[], AsyncSession]`.
  - `tenant_session_factory(base_factory, user_id)` accepts `Callable[[], AsyncSession]` (annotation widened; body unchanged).

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for :class:`splitsmith.db.engine.LoopEngines` and :func:`loop_sessionmaker`.

No Postgres: ``create_async_engine`` is patched to a recorder for the
asyncpg cases, and SQLite for the round trip.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy.pool import NullPool

from splitsmith.db import Base, User, create_engine, sessionmaker
from splitsmith.db import engine as engine_mod
from splitsmith.db.engine import LoopEngines, loop_sessionmaker, tenant_session_factory

PG_URL = "postgresql+asyncpg://u:p@h/db?ssl=require&prepared_statement_cache_size=0"


class _FakeEngine:
    def __init__(self, url: str, kwargs: dict[str, Any]) -> None:
        self.url = url
        self.kwargs = kwargs
        self.disposed = 0

    async def dispose(self) -> None:
        self.disposed += 1


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> list[_FakeEngine]:
    made: list[_FakeEngine] = []

    def _fake_create(url: str, **kwargs: Any) -> _FakeEngine:
        e = _FakeEngine(url, kwargs)
        made.append(e)
        return e

    monkeypatch.setattr(engine_mod, "create_async_engine", _fake_create)
    return made


def test_pooled_is_true_for_asyncpg_only() -> None:
    assert LoopEngines(PG_URL).pooled is True
    assert LoopEngines("sqlite+aiosqlite:///:memory:").pooled is False


def test_adopted_loops_get_distinct_pooled_engines(recorder: list[_FakeEngine]) -> None:
    engines = LoopEngines(PG_URL)

    async def _adopt_and_ask() -> tuple[Any, Any]:
        a = engines.adopt_current_loop()
        b = engines.for_current_loop()
        return a, b

    a1, b1 = asyncio.run(_adopt_and_ask())
    a2, b2 = asyncio.run(_adopt_and_ask())
    assert a1 is b1, "the same loop asks twice and gets the same engine"
    assert a1 is not a2, "two loops, two engines"
    assert len(recorder) == 2


def test_pooled_engine_settings(recorder: list[_FakeEngine]) -> None:
    engines = LoopEngines(PG_URL)

    async def _adopt() -> None:
        engines.adopt_current_loop()

    asyncio.run(_adopt())
    (made,) = recorder
    assert made.url == PG_URL, "the URL (and its prepared_statement_cache_size=0) passes through untouched"
    assert made.kwargs["pool_size"] == 5
    assert made.kwargs["max_overflow"] == 10
    assert made.kwargs["pool_timeout"] == 30
    assert made.kwargs["pool_pre_ping"] is True
    assert "pool_recycle" not in made.kwargs
    assert "poolclass" not in made.kwargs


def test_unadopted_loop_gets_the_shared_nullpool_engine(recorder: list[_FakeEngine]) -> None:
    """A loop nobody adopted (a concurrent.futures thread, a test's
    asyncio.run) must get an engine that never reuses a connection across
    loops: one shared NullPool engine, however many loops ask."""
    engines = LoopEngines(PG_URL)

    async def _ask() -> Any:
        return engines.for_current_loop()

    e1 = asyncio.run(_ask())
    e2 = asyncio.run(_ask())
    assert e1 is e2
    assert len(recorder) == 1
    assert recorder[0].kwargs["poolclass"] is NullPool


def test_sqlite_url_gets_one_nullpool_engine_whatever_loop_asks(recorder: list[_FakeEngine]) -> None:
    """aiosqlite is not loop-bound and ``:memory:`` is one database per
    connection, so SQLite keeps exactly what the hosted wiring built
    before: one NullPool engine."""
    engines = LoopEngines("sqlite+aiosqlite:///:memory:")

    async def _both() -> tuple[Any, Any]:
        return engines.adopt_current_loop(), engines.for_current_loop()

    a1, b1 = asyncio.run(_both())
    a2, b2 = asyncio.run(_both())
    assert a1 is b1 is a2 is b2
    assert len(recorder) == 1
    assert recorder[0].kwargs["poolclass"] is NullPool


def test_dispose_current_loop_disposes_and_forgets(recorder: list[_FakeEngine]) -> None:
    engines = LoopEngines(PG_URL)

    async def _cycle() -> None:
        first = engines.adopt_current_loop()
        await engines.dispose_current_loop()
        assert first.disposed == 1
        second = engines.adopt_current_loop()
        assert second is not first
        await engines.dispose_current_loop()
        # Disposing an un-adopted loop is a no-op, not an error.
        await engines.dispose_current_loop()

    asyncio.run(_cycle())
    assert len(recorder) == 2


def test_dispose_fallback(recorder: list[_FakeEngine]) -> None:
    engines = LoopEngines(PG_URL)

    async def _use_and_dispose() -> None:
        engines.for_current_loop()  # un-adopted -> fallback
        await engines.dispose_fallback()
        await engines.dispose_fallback()  # idempotent

    asyncio.run(_use_and_dispose())
    assert recorder[0].disposed == 1


def test_dead_loop_entry_is_dropped(recorder: list[_FakeEngine]) -> None:
    """Engines are weak-keyed on the loop; a closed, collected loop takes
    its entry with it instead of pinning a dead engine forever."""
    import gc

    engines = LoopEngines(PG_URL)

    async def _adopt() -> None:
        engines.adopt_current_loop()

    asyncio.run(_adopt())
    gc.collect()
    assert engines.adopted_count == 0


def test_loop_sessionmaker_round_trips_on_sqlite(tmp_path) -> None:
    """The stores call ``factory()`` with no arguments and ``async with`` the
    result; a loop_sessionmaker factory must satisfy that unchanged."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'x.sqlite'}"
    setup = create_engine(url)

    async def _create_all() -> None:
        async with setup.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await setup.dispose()

    asyncio.run(_create_all())

    engines = LoopEngines(url)
    factory = loop_sessionmaker(engines)

    async def _write_then_read() -> str:
        async with factory() as s:
            u = User(email="m@thias.se")
            s.add(u)
            await s.commit()
            await s.refresh(u)
            uid = u.id
        async with factory() as s:
            got = await s.get(User, uid)
            assert got is not None
            return got.email

    assert asyncio.run(_write_then_read()) == "m@thias.se"


def test_loop_sessionmaker_sessions_carry_the_tenant_listener(tmp_path) -> None:
    """``tenant_session_factory`` wraps a loop_sessionmaker factory the same
    way it wraps ``sessionmaker``'s: the after_begin listener is attached
    to each session it opens."""
    from sqlalchemy import event

    url = f"sqlite+aiosqlite:///{tmp_path / 'y.sqlite'}"
    factory = tenant_session_factory(loop_sessionmaker(LoopEngines(url)), "user-1")

    async def _open() -> bool:
        session = factory()
        try:
            return event.contains(session.sync_session, "after_begin", _any_listener(session))
        finally:
            await session.close()

    def _any_listener(session):  # noqa: ANN001, ANN202
        # ``event.contains`` needs the exact fn; read it back off the dispatch.
        listeners = list(session.sync_session.dispatch.after_begin)
        assert listeners, "no after_begin listener attached"
        return listeners[0]

    assert asyncio.run(_open()) is True


def test_plain_sessionmaker_still_works() -> None:
    """Regression guard: the old factory shape is still what the tests and
    alembic-adjacent code build."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    factory = sessionmaker(engine)

    async def _open() -> None:
        async with factory() as s:
            assert s is not None

    asyncio.run(_open())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_db_engine_loops.py -n0 -q`
Expected: FAIL with `ImportError: cannot import name 'LoopEngines'`.

- [ ] **Step 3: Implement `LoopEngines` and `loop_sessionmaker`**

In `src/splitsmith/db/engine.py`, extend the imports:

```python
import asyncio
import weakref
from collections.abc import Callable
```

Replace `create_engine`'s docstring paragraph that starts "``pool_disabled=True`` uses :class:`NullPool` so every" with:

```python
    ``pool_disabled=True`` uses :class:`NullPool` so every ``session()``
    opens a fresh DB connection and closes it on release. The hosted
    wiring no longer uses it (see :class:`LoopEngines`); it remains for
    the tests that drive a store through many short-lived event loops on
    purpose, and for callers that want one connection per call.
```

Add after `sessionmaker`:

```python
_POOLED_DRIVERS = ("postgresql+asyncpg://",)


class LoopEngines:
    """One pooled :class:`AsyncEngine` per *adopted* event loop.

    asyncpg binds a connection to the loop that created it; a pooled
    connection handed to another loop crashes with "attached to a
    different loop" (#423). So an engine with a real pool may only serve
    one loop, and only a long-lived one is worth a pool. The hosted
    process has exactly two: the main loop (uvicorn's in ``serve``, the
    Procrastinate worker's in ``worker``) and the ``DbRunner`` loop that
    every sync caller submits to. Each adopts itself with
    :meth:`adopt_current_loop` and gets its own engine with
    ``pool_size=5, max_overflow=10, pool_pre_ping=True``.

    Any other loop -- a ``concurrent.futures`` thread's ``asyncio.run``,
    the re-entrant fallback in :func:`splitsmith.async_bridge.run_sync`
    -- gets the shared **fallback** engine, a :class:`NullPool` engine
    that never reuses a connection and is therefore safe from every loop.
    That is exactly what the whole hosted process used before #1178.

    Non-asyncpg URLs (SQLite in tests) are not loop-bound and ``:memory:``
    is one database per connection, so they get the fallback engine from
    every method: one NullPool engine, as the hosted wiring built before.

    The pool adds no traffic of its own: no warm-up, no periodic ping, no
    ``pool_recycle``. Neon closes an idle connection after five minutes
    and suspends the compute; ``pool_pre_ping`` turns the closed
    connection into a silent reconnect on the next checkout. That is the
    scale-to-zero condition from the 2026-07-03 cost plan.
    """

    def __init__(self, url: str, *, echo: bool = False, pool_size: int = 5, max_overflow: int = 10) -> None:
        self._url = url
        self._echo = echo
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self._pooled = url.startswith(_POOLED_DRIVERS)
        self._by_loop: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, AsyncEngine] = (
            weakref.WeakKeyDictionary()
        )
        self._fallback: AsyncEngine | None = None

    @property
    def pooled(self) -> bool:
        """Whether adopted loops get a real pool (asyncpg) or the fallback (everything else)."""
        return self._pooled

    @property
    def adopted_count(self) -> int:
        return len(self._by_loop)

    def _fallback_engine(self) -> AsyncEngine:
        if self._fallback is None:
            self._fallback = create_async_engine(self._url, echo=self._echo, poolclass=NullPool)
        return self._fallback

    def adopt_current_loop(self) -> AsyncEngine:
        """Mark the running loop long-lived and return its pooled engine
        (created on first call). For a non-pooled URL this is the fallback."""
        if not self._pooled:
            return self._fallback_engine()
        loop = asyncio.get_running_loop()
        engine = self._by_loop.get(loop)
        if engine is None:
            engine = create_async_engine(
                self._url,
                echo=self._echo,
                pool_size=self._pool_size,
                max_overflow=self._max_overflow,
                pool_timeout=30,
                pool_pre_ping=True,
            )
            self._by_loop[loop] = engine
        return engine

    def for_current_loop(self) -> AsyncEngine:
        """The running loop's engine if it was adopted, else the fallback."""
        if not self._pooled:
            return self._fallback_engine()
        loop = asyncio.get_running_loop()
        engine = self._by_loop.get(loop)
        return engine if engine is not None else self._fallback_engine()

    async def dispose_current_loop(self) -> None:
        """Dispose and forget the running loop's engine; no-op if it has none.

        Must run *on* that loop: asyncpg closes connections on the loop that
        owns them.
        """
        if not self._pooled:
            return
        loop = asyncio.get_running_loop()
        engine = self._by_loop.pop(loop, None)
        if engine is not None:
            await engine.dispose()

    async def dispose_fallback(self) -> None:
        engine, self._fallback = self._fallback, None
        if engine is not None:
            await engine.dispose()


def loop_sessionmaker(engines: LoopEngines) -> Callable[[], AsyncSession]:
    """A session factory that picks the current loop's engine at open time.

    Same ``() -> AsyncSession`` contract as :func:`sessionmaker`'s result
    (every store calls ``self._session_factory()`` with no arguments and
    ``async with``-es it), same ``expire_on_commit=False``.
    """

    def _open() -> AsyncSession:
        return AsyncSession(engines.for_current_loop(), expire_on_commit=False)

    return _open
```

Widen `tenant_session_factory`'s first parameter annotation from `async_sessionmaker[AsyncSession]` to `Callable[[], AsyncSession]` (the body already only calls `base_factory()`). Keep `async_sessionmaker` in the imports for `sessionmaker`.

In `src/splitsmith/db/__init__.py`, line 33 becomes:

```python
from .engine import LoopEngines, create_engine, loop_sessionmaker, sessionmaker, tenant_session_factory
```

and add `"LoopEngines"` and `"loop_sessionmaker"` to `__all__` next to `"create_engine"` / `"sessionmaker"`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_db_engine_loops.py tests/test_tenant_session_factory.py -n0 -q`
Expected: all pass. If `test_dead_loop_entry_is_dropped` is flaky under `gc`, the loop is being kept alive by a reference in the test frame: make sure `_adopt` does not return the engine and that `asyncio.run` has returned before `gc.collect()`.

- [ ] **Step 5: Run ruff and mypy on the touched module**

Run: `uv run ruff check src/splitsmith/db/engine.py src/splitsmith/db/__init__.py && uv run ruff format --check src/splitsmith/db && uv run mypy src/splitsmith/db/engine.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/db/engine.py src/splitsmith/db/__init__.py tests/test_db_engine_loops.py
git commit -m "feat(db): LoopEngines, a pooled engine per adopted event loop (#1178)"
```

---

### Task 3: Job backend and YouTube bridges go through `run_sync`

**Files:**
- Modify: `src/splitsmith/db/job_backend.py:165,612,705,760,785` (the five `asyncio.run(` sites) and the docstrings that name `asyncio.run`
- Modify: `src/splitsmith/ui/youtube_api.py:186,210`
- Test: `tests/test_postgres_job_backend.py` (existing, must stay green) and a new test appended to it

**Interfaces:**
- Consumes: `splitsmith.async_bridge.run_sync`, `DbRunner`, `install_runner` (Task 1).
- Produces: no new names. `PostgresJobBackend.__init__` keeps `sweep_on_boot`; the sweep, `_run`, `_finalize_with_timings`, `_patch`, `_is_cancel_requested` all bridge through `run_sync`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_postgres_job_backend.py` (reuse the file's existing engine / backend fixtures by name; read the top of the file for the fixture that yields a backend over a file-backed SQLite and a registered no-op body, and use the same one here):

```python
def test_body_side_bridges_land_on_the_installed_runner(tmp_path) -> None:
    """``_patch`` and ``_is_cancel_requested`` are called from the job
    thread. With a DbRunner installed they must run on its loop, not on a
    fresh ``asyncio.run`` loop -- that is the whole point of #1178."""
    import asyncio

    from splitsmith.async_bridge import DbRunner, install_runner
    from splitsmith.db import Base, create_engine, sessionmaker
    from splitsmith.db.job_backend import PostgresJobBackend

    seen: list[asyncio.AbstractEventLoop] = []
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.sqlite'}", pool_disabled=True)

    async def _create_all() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())
    backend = PostgresJobBackend(sessionmaker(engine), user_id="u1", deferrer=None, sweep_on_boot=False)

    original = backend._patch_async

    async def _spy(job_id: str, kwargs: dict) -> None:
        seen.append(asyncio.get_running_loop())
        await original(job_id, kwargs)

    backend._patch_async = _spy  # type: ignore[method-assign]

    runner = DbRunner()
    runner.start()
    install_runner(runner)
    try:
        backend._patch("no-such-job", progress=0.5)
        assert seen and seen[0] is runner.loop
    finally:
        install_runner(None)
        runner.stop()
```

If the file's `PostgresJobBackend` constructor signature differs from the call above (check `deferrer`'s type), match the file's existing construction.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_postgres_job_backend.py::test_body_side_bridges_land_on_the_installed_runner -n0 -q`
Expected: FAIL -- `seen[0]` is a fresh loop, not `runner.loop`.

- [ ] **Step 3: Replace the five `asyncio.run` sites in `job_backend.py`**

Add the import (local group):

```python
from ..async_bridge import run_sync
```

Then, line by line:

```python
# line 165
            run_sync(self._sweep_stuck_jobs_on_boot())
# line 612
            row_snapshot = run_sync(self._begin_run(job_id, now))
# line 705
        run_sync(self._finalize_run(job_id, status, error=error, timings=timings))
# line 760
        run_sync(self._patch_async(job_id, kwargs))
# line 785
        return run_sync(self._is_cancel_requested_async(job_id))
```

Update the three docstrings / comments that say the bridge is `asyncio.run` (around lines 609-611, 676-679, 757-759) to say: "bridges to async DB through ``run_sync``, which lands on the process ``DbRunner`` in hosted mode (#1178) and on a fresh loop otherwise". Remove `import asyncio` from the module only if nothing else in it uses `asyncio` (`run_job` uses `asyncio.to_thread`, so it stays).

- [ ] **Step 4: Replace the two sites in `youtube_api.py`**

```python
from ..async_bridge import run_sync
```

```python
# line 186
    return build_client(conn, on_reauthorize=lambda: run_sync(store.clear()))
# line 210
    conn = run_sync(_hosted_connection(store))
```

Update `_hosted_client`'s docstring: "The hook runs on whatever thread the token refresh happens on; ``run_sync`` is the bridge (the process DbRunner in hosted mode)."

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_postgres_job_backend.py tests/test_youtube_api.py tests/test_youtube_api_hosted.py tests/test_youtube_upload.py -q`
Expected: all pass, including the new test.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/db/job_backend.py src/splitsmith/ui/youtube_api.py tests/test_postgres_job_backend.py
git commit -m "refactor(db): job backend and YouTube bridges go through run_sync (#1178)"
```

---

### Task 4: Hosted wiring, lifespan, worker, and the `server.py` call sites

**Files:**
- Modify: `src/splitsmith/ui/server.py` -- `AppState` (near line 1716), `_apply_hosted_mode_wiring` (lines 6987-6994), `_hosted_boot_lifespan` (lines 7470-7502), and every `asyncio.run(` site listed below
- Modify: `src/splitsmith/queue.py:393` (`run_worker`)
- Test: `tests/test_hosted_mode_boot.py` (append), `tests/test_async_bridge.py` (nothing new), full hosted subset

**Interfaces:**
- Consumes: `DbRunner`, `install_runner`, `get_runner`, `run_sync` (Task 1); `LoopEngines`, `loop_sessionmaker` (Task 2).
- Produces: `AppState.db_engines: LoopEngines | None = None`. The process runner is reachable through `splitsmith.async_bridge.get_runner()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hosted_mode_boot.py`. The file's `hosted_db` fixture (line 39) already creates a file-backed SQLite schema and sets `SPLITSMITH_DATABASE_URL`, `SPLITSMITH_MODE=hosted` and `SPLITSMITH_PUBLIC_URL` for the test's lifetime; the new tests take it as a parameter, the same way `test_create_app_builds_postgres_tenant_per_user(hosted_db: str)` does. Add `import threading` to the module imports.

```python
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

    install_runner(None)  # a previous hosted test in this worker may have left one
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
```

The local-mode test deliberately takes no `hosted_db`: it needs the hosted env vars absent, which `monkeypatch.delenv` guarantees whatever an earlier test in the same xdist worker left behind.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_hosted_mode_boot.py -n0 -q -k "loop_engines or process_runner or no_engines or lifespan_adopts"`
Expected: FAIL -- `AppState` has no `db_engines`.

- [ ] **Step 3: Add the `AppState` field**

Inside the `if TYPE_CHECKING:` block at `server.py:99-112`, add `LoopEngines` to the `from ..db import (...)` list. In the `AppState` dataclass, next to `project_state: ProjectStateStore | None` (line 1717), add:

```python
    #: Hosted only: the per-loop pooled engines (#1178). ``None`` in local
    #: mode, which has no database. The lifespan adopts the app loop and
    #: disposes it; the process ``DbRunner`` (``async_bridge.get_runner``)
    #: adopts its own.
    db_engines: LoopEngines | None = None
```

(If the dataclass has non-default fields after that position, place the new field with the other defaulted ones near `workers_store` at line 1908 instead; dataclass ordering rules apply.)

- [ ] **Step 4: Rewire `_apply_hosted_mode_wiring`**

Replace lines 6987-6994 (the comment block and the two assignments) with:

```python
    # One pooled engine per long-lived loop (#1178), replacing the NullPool
    # that #423 introduced to survive asyncpg's loop binding. The app loop
    # adopts itself in ``_hosted_boot_lifespan`` (the worker's in
    # ``queue.run_worker``); every sync caller lands on the process
    # ``DbRunner`` through ``run_sync``; any other loop gets the shared
    # NullPool fallback. See ``splitsmith.db.engine.LoopEngines``.
    engines = LoopEngines(url)
    state.db_engines = engines
    session_factory = loop_sessionmaker(engines)
    runner = get_runner()
    if runner is None or not runner.is_running:
        runner = DbRunner(on_start=engines.adopt_current_loop, on_stop=engines.dispose_current_loop)
        runner.start()
        install_runner(runner)
```

Imports: `_apply_hosted_mode_wiring` imports its `..db` names lazily inside the function (`from ..db import (... create_engine, sessionmaker, ...)` around line 6936-6941). Add `LoopEngines` and `loop_sessionmaker` to that list and remove `create_engine` and `sessionmaker` from it if nothing else in the function uses them (grep the function body). Never add a module-level `from ..db import`: the slim-install check fails on it. `async_bridge` has no such restriction: extend the existing module-level `from ..async_bridge import run_sync` at line 167 to `from ..async_bridge import DbRunner, get_runner, install_runner, run_sync`.

Note on the second `create_app` in one process: the runner's `on_start` bound to the *first* app's engines is what adopts the runner loop. A later app's `LoopEngines` sees the runner loop as un-adopted and serves the fallback engine from it, which is correct and is what the SQLite tests get anyway. In production there is one `create_app` per process.

- [ ] **Step 5: Adopt and dispose the app loop in `_hosted_boot_lifespan`**

Replace the function body from `workers_store = ...` through `return _lifespan` with:

```python
    workers_store = state.workers_store if load_railway_config() is not None else None
    retrigger = state.boot_retrigger
    engines = state.db_engines
    if workers_store is None and retrigger is None and engines is None:
        return None

    @asynccontextmanager
    async def _lifespan(_app: Any) -> AsyncIterator[None]:
        if engines is not None:
            # The app loop is long-lived: give it its pooled engine (#1178).
            engines.adopt_current_loop()
        if workers_store is not None:
            await workers_store.ensure_railway_row(version=splitsmith_version)
        if retrigger is not None:
            # Cold starts include every wake from Railway app sleeping, so a
            # stranded queue job recovers on the next visit instead of
            # waiting for the 6-hourly safety cron.
            await retrigger()
        try:
            yield
        finally:
            if engines is not None:
                await engines.dispose_current_loop()
```

Update the docstring's "Two boot duties" to three, naming the adoption. The runner is **not** stopped here: it is process-global and shared by every app in the process (tests); its loop's engine is disposed by `on_stop` when the process exits or a test stops it.

- [ ] **Step 6: Adopt the worker loop in `queue.run_worker`**

After `state = await asyncio.to_thread(build_worker_state)` at `queue.py:393`:

```python
    if state.db_engines is not None:
        # The worker's main loop is long-lived: pooled engine (#1178). Job
        # bodies on ``asyncio.to_thread`` threads reach the database through
        # ``run_sync`` -> the process DbRunner, which adopted its own loop.
        state.db_engines.adopt_current_loop()
```

and in the function's `finally:` block (currently the single line `await app.connector.close_async()` at the end of `run_worker`), add after that line:

```python
        if state.db_engines is not None:
            await state.db_engines.dispose_current_loop()
```

- [ ] **Step 7: Replace every `asyncio.run(` in `server.py` with `run_sync(`**

The sites, by current line: 624, 636, 2950, 3598, 3603, 3617, 3716, 3735, 4617, 7284, 7698, 7834, 7835, 9522, 9810, 9820, 9851, 9862, 10220, 18643. Mechanical: `asyncio.run(` -> `run_sync(` at each; the arguments do not change. Do it with a targeted edit per site (not a blanket sed, so a future `asyncio.run` that is genuinely a main loop is not caught). Lines 7834-7835 are local-mode only (`if not _hosted_mode_active()`); with no runner `run_sync` is `asyncio.run` there, so they change for uniformity and lose nothing.

Run afterwards: `grep -n "asyncio.run(" src/splitsmith/ui/server.py`
Expected: no matches.

Also replace the two in `src/splitsmith/ui/embedded.py:262,264` the same way (local-mode shutdown, unchanged behaviour), importing `from .async_bridge import run_sync` lazily where the function imports `asyncio` today.

- [ ] **Step 8: Run the new tests, then the hosted subset**

Run: `uv run pytest tests/test_hosted_mode_boot.py -n0 -q`
Expected: all pass.

Run: `uv run pytest -q tests/test_hosted_mode_boot.py tests/test_ui_server.py tests/test_sync_api.py tests/test_magic_link_auth.py tests/test_hosted_raw_upload.py tests/test_compare_stream_ref.py tests/test_match_delete.py tests/test_comments_signed_in.py tests/test_youtube_api.py tests/test_audit_lock_wiring.py`
Expected: all pass. A failure that mentions "attached to a different loop" or a `:memory:` table missing means a SQLite URL was given a per-loop engine: check `LoopEngines.pooled` is False for it.

- [ ] **Step 9: Run the slim import check and lint**

Run: `uv run python scripts/ci/assert_slim_import_surface.py` (read its header for the exact invocation; it builds a slim venv) and `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/splitsmith/async_bridge.py src/splitsmith/db/engine.py`
Expected: clean. If the slim check fails naming `async_bridge`, a `splitsmith.db` import leaked into it -- Task 1 forbids that.

- [ ] **Step 10: Run the whole suite**

Run: `uv run pytest -q`
Expected: green. (About 60-70 s on the 12-core box.)

- [ ] **Step 11: Commit**

```bash
git add src/splitsmith/ui/server.py src/splitsmith/ui/embedded.py src/splitsmith/queue.py tests/test_hosted_mode_boot.py
git commit -m "feat(hosted): pooled engines per loop and a process DbRunner replace NullPool (#1178)"
```

---

### Task 5: Docker regression test -- the #423 repro and the pooling proof

**Files:**
- Create: `tests/test_pooling_docker.py`
- Reuses: `tests/test_hosted_docker_smoke.py` -- `hosted_stack` (via conftest discovery), `API_BASE`, `_magic_link_login`, `_psql`, `_compose`, `_compose_env`

**Interfaces:**
- Consumes: the compose stack's API on `API_BASE`, Postgres through `_psql` as the `splitsmith` superuser, the `splitsmith_session` cookie from `_magic_link_login`.
- Produces: nothing for later tasks.

- [ ] **Step 1: Write the test**

```python
"""Docker-compose proof for hosted connection pooling (#1178).

Three things the SQLite suite cannot show, all against the compose
stack's live Postgres and the API + worker containers that run on it:

1. The #423 crash does not come back: a sync handler, an async handler
   and a worker job all touch the database in one process each, and no
   container logs "attached to a different loop".
2. Pooling is real: after twenty requests, at least one of the API's
   connections has a ``backend_start`` from *before* the twentieth
   request. NullPool opened and closed one per call, so every connection
   it left behind (none, usually) was fresh.
3. A connection Neon closes while idle is reconnected silently:
   ``pg_terminate_backend`` on the API's idle connections, then the next
   request still answers 200 (``pool_pre_ping``).

Run with ``uv run pytest -m docker -n0 tests/test_pooling_docker.py -v``.
"""

from __future__ import annotations

import subprocess
import time

import httpx
import pytest

from .test_hosted_docker_smoke import API_BASE, _compose, _compose_env, _magic_link_login, _psql

pytestmark = pytest.mark.docker

# The API container connects as the non-superuser app role; the worker does
# too, but through Procrastinate's psycopg pool, whose connections carry
# ``application_name`` ``procrastinate``. Filter those out.
_API_CONNS = (
    "FROM pg_stat_activity WHERE usename = 'splitsmith_app' "
    "AND datname = 'splitsmith' AND application_name NOT ILIKE '%procrastinate%'"
)


def _container_logs(service: str) -> str:
    out = subprocess.run(
        _compose("logs", "--no-color", service), capture_output=True, text=True, env=_compose_env()
    )
    return out.stdout + out.stderr


def _ping(cookies: dict[str, str], path: str) -> None:
    r = httpx.get(f"{API_BASE}{path}", cookies=cookies, timeout=10.0)
    assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:200]}"


def test_sync_and_async_handlers_share_pooled_connections_without_loop_errors(hosted_stack: None) -> None:
    _, secret = _magic_link_login("pooling@example.com")
    cookies = {"splitsmith_session": secret}

    # One async handler and one sync handler, both hitting Postgres.
    _ping(cookies, "/api/me")
    _ping(cookies, "/api/me/recent-projects?detail=true")
    oldest_before = _psql(f"SELECT min(backend_start) {_API_CONNS}")
    assert oldest_before, "the API holds no connection after a request; the pool is not pooling"

    t0 = time.time()
    for _ in range(20):
        _ping(cookies, "/api/me")
        _ping(cookies, "/api/me/recent-projects?detail=true")
    elapsed = time.time() - t0

    count = int(_psql(f"SELECT count(*) {_API_CONNS}"))
    assert count <= 30, f"{count} connections: more than two full pools (2 x (5 + 10))"
    oldest_after = _psql(f"SELECT min(backend_start) {_API_CONNS}")
    assert oldest_after == oldest_before, (
        "the oldest API connection was replaced during the run; connections are not being reused"
    )
    # 40 requests, each of which used to pay a fresh handshake (~5 ms on the
    # compose network, ~200 ms against Neon). Generous bound: the point is
    # the backend_start assertion above, this only catches a pathological
    # regression.
    assert elapsed < 20.0

    # Compose service names (docker-compose.yml): the API is ``splitsmith``,
    # the queue drainer is ``worker``.
    for service in ("splitsmith", "worker"):
        logs = _container_logs(service)
        assert "attached to a different loop" not in logs, f"{service} logged the #423 crash"


def test_idle_connection_closed_by_the_server_is_reconnected_silently(hosted_stack: None) -> None:
    """Neon's Activity Monitor closes idle connections after five minutes.
    ``pool_pre_ping`` must turn that into a reconnect, not a 500."""
    _, secret = _magic_link_login("pooling-idle@example.com")
    cookies = {"splitsmith_session": secret}
    _ping(cookies, "/api/me")

    terminated = _psql(
        f"SELECT count(pg_terminate_backend(pid)) {_API_CONNS} AND state = 'idle'"
    )
    assert int(terminated) >= 1, "nothing to terminate: the pool kept no idle connection"

    # The next checkout pings, finds the connection dead, reconnects.
    _ping(cookies, "/api/me")
    _ping(cookies, "/api/me/recent-projects?detail=true")
    logs = _container_logs("splitsmith")
    assert "attached to a different loop" not in logs
```

- [ ] **Step 2: Run the docker test against the pre-change code to see it fail for the right reason**

Check out the parent of Task 1's commit into a worktree **or** temporarily revert Task 4's wiring commit, build the compose image, and run:

`PATH=~/.claude-tmp/bin:$PATH uv run pytest -m docker -n0 tests/test_pooling_docker.py -v`

Expected on NullPool: the first test fails at `oldest_before` ("the API holds no connection after a request") because NullPool closed it. Record the failure text in the PR. Restore the branch.

An editable install makes a pre-fix worktree run post-fix code unless the worktree has its own venv -- use `uv sync` inside the worktree before running.

- [ ] **Step 3: Run the docker test against the branch**

`PATH=~/.claude-tmp/bin:$PATH uv run pytest -m docker -n0 tests/test_pooling_docker.py -v`
Expected: 2 passed. Then the existing docker suite, serially:

`PATH=~/.claude-tmp/bin:$PATH uv run pytest -m docker -n0 tests/test_hosted_docker_smoke.py tests/test_sync_docker.py tests/test_share_readonly_docker.py tests/test_device_auth_docker.py tests/test_comments_rls_docker.py -v`
Expected: all pass. `test_worker_runs_compute_job_end_to_end` is the worker-side #423 repro (job thread bridges through the runner).

- [ ] **Step 4: Commit**

```bash
git add tests/test_pooling_docker.py
git commit -m "test(docker): pooling proof and the #423 loop-binding repro (#1178)"
```

---

### Task 6: Documentation

**Files:**
- Modify: `CLAUDE.md` (new subsection under "Detection pipeline"'s neighbours, next to "State doc kinds and the sync allowlist")
- Modify: `tests/test_hosted_docker_smoke.py:220-224` (comment only)
- Modify: `docs/HOSTED-LOCAL.md` if it names NullPool (grep first)

**Interfaces:** none.

- [ ] **Step 1: Add the CLAUDE.md section**

Insert before "## State doc kinds and the sync allowlist":

```markdown
## Hosted database connections (#1178)

asyncpg binds a connection to the event loop that created it; using it
from another loop crashes with "attached to a different loop" (#423).
Hosted mode therefore runs database work on exactly two long-lived
loops per process -- the main loop (uvicorn's, or the Procrastinate
worker's) and the ``DbRunner`` thread's (``splitsmith.async_bridge``) --
and ``db.engine.LoopEngines`` gives each *adopted* loop its own pooled
engine (``pool_size=5, max_overflow=10, pool_pre_ping=True``). Any other
loop gets the shared NullPool fallback, which never reuses a connection.
Every sync caller (the state accessors, the job backend's thread-side
bridges, ``youtube_api``) goes through ``run_sync``, never
``asyncio.run``: a new ``asyncio.run(<store coroutine>)`` anywhere on
the hosted path is a per-call connection again. Local mode builds no
engine and installs no runner, so ``run_sync`` there is ``asyncio.run``
as before. The pool adds no traffic of its own (no warm-up, no ping, no
``pool_recycle``): Neon closes idle connections after five minutes and
scales to zero, and ``pre_ping`` reconnects on the next request. The
regression gate is ``tests/test_pooling_docker.py`` (the compose stack
is what #423 crashed on); SQLite in tests keeps one NullPool engine on
purpose, a per-loop ``:memory:`` engine would be a database per loop.
```

- [ ] **Step 2: Update the comment in `test_hosted_docker_smoke._magic_link_login`**

Lines 220-224 explain "one event loop for begin + complete" with `pool_disabled=True`. Append one sentence: "The production wiring now pools per loop (#1178); this helper is a standalone client and keeps NullPool deliberately."

- [ ] **Step 3: Check `docs/HOSTED-LOCAL.md`**

Run: `grep -n "NullPool\|different loop" docs/HOSTED-LOCAL.md README.md docs/saas-readiness/*.md`
For every hit that describes the hosted engine as NullPool, change it to point at `LoopEngines` and this plan's CLAUDE.md section. Troubleshooting entries about the *crash* stay (the crash is still what a stray `asyncio.run` produces).

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md tests/test_hosted_docker_smoke.py docs
git commit -m "docs: hosted database connections, the loop rule and the pooling gate (#1178)"
```

---

### Task 7: Verify on staging and production

**Files:** none.

- [ ] **Step 1: Open the PR, merge on green**

`gh pr create --title "feat(hosted): pooled database connections per event loop (#1178)" --body-file <summary>`; `gh pr checks <n> --watch`; the PR body cites the docker test's pre-change failure text from Task 5 Step 2 and names the two docker files run.

- [ ] **Step 2: Staging**

The merge deploys staging. From `~/work/splitsmith`: `~/.railway/bin/railway logs --http --json -s serve -e staging -n 400 > ~/.claude-tmp/perf/staging-after.jsonl`, then load the match list and open a match on `my.staging.splitsmith.app` and read `GET /api/me` and `GET /api/me/recent-projects` durations. Expected: `/api/me` under 60 ms warm; recent-projects well under its 8.9 s (its call count is #1179's job, but every call is now cheaper). Also leave the desktop app syncing against staging for a day and check `railway logs -s serve -e staging` for `attached to a different loop` (expected: none).

- [ ] **Step 3: Production, after release**

Same read on `-e production`. Add the before/after numbers to #1178 and #1183, then close #1178.
