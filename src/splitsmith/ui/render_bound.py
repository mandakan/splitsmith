"""One process-wide bound on browser renders.

Every share card and every export-preview still launches Chromium.
``render_slot`` is the one gate in front of all of them: at most
``RENDER_CONCURRENCY`` renders run at once in this process. A render that
cannot get a slot within ``RENDER_WAIT_S`` raises :class:`RenderBusyError`
instead of waiting longer, because a waiting render holds a threadpool
thread (the routes are sync handlers; Playwright's sync API refuses the
event loop) and enough distinct requests would otherwise stall every
other sync route behind them. Each caller decides what "busy" looks like
to its client.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

#: Renders allowed at once in this process.
RENDER_CONCURRENCY = 2

#: How long a render waits for a slot. Zero: a busy renderer answers at
#: once rather than parking a thread.
RENDER_WAIT_S = 0.0

#: ``Retry-After`` a busy answer carries, in seconds.
RETRY_AFTER_S = 5

_slots = threading.BoundedSemaphore(RENDER_CONCURRENCY)


class RenderBusyError(Exception):
    """Every render slot was taken."""


@contextmanager
def render_slot() -> Iterator[None]:
    """Hold one render slot for the ``with`` body, or raise :class:`RenderBusyError`.

    Looks ``_slots`` up at call time so a test can replace it.
    """
    slots = _slots
    if RENDER_WAIT_S > 0:
        acquired = slots.acquire(timeout=RENDER_WAIT_S)
    else:
        acquired = slots.acquire(blocking=False)
    if not acquired:
        raise RenderBusyError
    try:
        yield
    finally:
        slots.release()
