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
        self._start_exception: BaseException | None = None

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
        self._start_exception = None
        thread = threading.Thread(target=self._main, name=_RUNNER_THREAD_NAME, daemon=True)
        self._thread = thread
        thread.start()
        self._ready.wait()
        if self._start_exception is not None:
            exc = self._start_exception
            self._start_exception = None
            # Join before clearing state: _main is already past the point
            # where it touches self._loop, but joining keeps is_running
            # false the instant start() raises instead of racing the
            # thread's own cleanup.
            thread.join(timeout=5.0)
            self._thread = None
            self._loop = None
            raise exc

    def _main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)

        async def _boot() -> None:
            if self._on_start is not None:
                self._on_start()

        try:
            loop.run_until_complete(_boot())
        except BaseException as exc:  # noqa: BLE001 -- surfaced to start() via _start_exception
            self._start_exception = exc
            self._ready.set()
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # noqa: BLE001 -- best-effort; start() is about to raise anyway
                logger.warning("DbRunner shutdown after a failed on_start also failed", exc_info=True)
            loop.close()
            return

        self._ready.set()
        try:
            loop.run_forever()
        finally:
            self._shutdown(loop)

    def _shutdown(self, loop: asyncio.AbstractEventLoop) -> None:
        """Drain ``loop`` on a normal :meth:`stop` so no in-flight
        :meth:`run` caller is left blocked on a Future that will never
        resolve.

        Order: flush once -- a ``run()`` dispatched just before ``stop()``
        can still be sitting as an unprocessed callback when
        ``run_forever`` returns, because ``call_soon_threadsafe`` only
        guarantees it lands in the ready queue, not that it is drained
        before the stop callback is noticed -- then cancel whatever tasks
        that flush leaves on the loop (this is what turns a hanging
        ``run()`` into ``CancelledError`` instead of a permanent hang),
        then ``on_stop``, then close. ``on_stop`` runs *after* the cancel
        sweep on purpose: it is the hosted wiring's engine disposal, and
        disposing the pool while a query task is still in flight would be
        worse than cancelling the query first.
        """
        try:
            loop.run_until_complete(asyncio.sleep(0))
        except Exception:  # noqa: BLE001 -- best-effort flush, never block shutdown
            logger.warning("DbRunner flush before shutdown failed", exc_info=True)
        _cancel_all_tasks(loop)
        if self._on_stop is not None:
            try:
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
        if thread.is_alive():
            logger.warning("DbRunner thread %r did not stop within the join timeout", thread.name)
        self._thread = None

    def run(self, coro: Coroutine[Any, Any, _T]) -> _T:
        """Run ``coro`` on the runner loop and block until it is done.

        The caller's :mod:`contextvars` context travels with the task:
        ``asyncio.run_coroutine_threadsafe`` creates the task on the
        target thread, which would otherwise hand the RLS listener the
        runner thread's empty ``current_share_scope``.
        """
        if not self.is_running:
            coro.close()
            raise RuntimeError("DbRunner is not running")
        ctx = contextvars.copy_context()
        loop = self.loop

        async def _in_context() -> _T:
            return await asyncio.create_task(coro, context=ctx)

        return asyncio.run_coroutine_threadsafe(_in_context(), loop).result()


def _cancel_all_tasks(loop: asyncio.AbstractEventLoop) -> None:
    """Mirror :func:`asyncio.runners._cancel_all_tasks`: cancel every task
    still on ``loop`` and wait for the cancellation to land.

    Without this, a task representing an in-flight :meth:`DbRunner.run`
    call is simply abandoned when the loop closes, and the caller's
    ``concurrent.futures.Future`` (from ``run_coroutine_threadsafe``)
    never resolves -- it blocks on ``.result()`` forever instead of
    seeing ``CancelledError``.
    """
    to_cancel = asyncio.all_tasks(loop)
    if not to_cancel:
        return
    for task in to_cancel:
        task.cancel()
    loop.run_until_complete(asyncio.gather(*to_cancel, return_exceptions=True))
    for task in to_cancel:
        if task.cancelled():
            continue
        exc = task.exception()
        if exc is not None:
            loop.call_exception_handler(
                {
                    "message": "unhandled exception during DbRunner shutdown",
                    "exception": exc,
                    "task": task,
                }
            )


_runner: DbRunner | None = None


def install_runner(runner: DbRunner | None) -> None:
    """Make ``runner`` the process-wide target of :func:`run_sync` (``None`` uninstalls)."""
    global _runner
    _runner = runner


def get_runner() -> DbRunner | None:
    return _runner


def _run_on_fresh_loop(coro: Coroutine[Any, Any, _T]) -> _T:
    """Today's behaviour: inline ``asyncio.run`` with no running loop, a
    throwaway thread with its own loop otherwise.

    Out of scope here, ledgered for a later pass: a coroutine that reaches
    this path and itself calls :func:`run_sync` again would deadlock --
    nothing dispatches a second re-entrant hop anywhere else.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # ThreadPoolExecutor.submit does not copy the calling context on its
    # own, so without this the RLS listener would see the throwaway
    # thread's empty current_share_scope instead of the caller's (#779).
    ctx = contextvars.copy_context()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(ctx.run, asyncio.run, coro).result()


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
