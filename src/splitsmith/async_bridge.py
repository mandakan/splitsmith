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
    hosted wiring passes ``LoopEngines.adopt_current_loop``, so the loop
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
