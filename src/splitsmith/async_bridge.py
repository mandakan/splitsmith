"""Run an async coroutine to completion from synchronous code.

The hosted-mode per-user stores (``ProjectStateStore`` et al.) are async,
but the call sites that drive them are a mix:

- **Sync FastAPI handlers** run in a threadpool with *no* event loop on
  the thread, so ``asyncio.run`` works directly.
- **Async FastAPI handlers** run on the event loop; ``asyncio.run`` there
  raises ``RuntimeError: asyncio.run() cannot be called from a running
  event loop``.
- The model ``save()`` methods and the ``AppState`` state accessors are
  *sync* (so the ~100 handler call sites that use them stay unchanged --
  the whole point of the state-refactor seam) yet are reached from both
  kinds of handler.

:func:`run_sync` papers over the difference: no running loop -> run inline
with ``asyncio.run``; a loop is already running -> run the coroutine on a
throwaway worker thread (its own fresh loop) and block the caller on the
result. Blocking the event loop for the duration of one small state query
matches the blocking character the prior synchronous-boto3 JSON mirror
already had, so this is not a regression in loop-friendliness.

The hosted engine uses :class:`~sqlalchemy.pool.NullPool` precisely so a
fresh per-call event loop is tolerated: asyncpg connections are
loop-bound, and NullPool opens + closes one per session rather than
reusing a connection across loops. See ``splitsmith.db.engine``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Coroutine
from typing import Any, TypeVar

_T = TypeVar("_T")


def run_sync(coro: Coroutine[Any, Any, _T]) -> _T:
    """Drive ``coro`` to completion regardless of the caller's loop state."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop on this thread (sync handler in the threadpool, worker
        # callback, boot path) -- run inline.
        return asyncio.run(coro)
    # A loop is already running here (async handler). asyncio.run would
    # reject; hand the coroutine to a worker thread with its own loop and
    # block on it. Single-use executor: the call rate is handler I/O, not
    # a hot loop, and a fresh thread keeps the loops cleanly separated.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
