"""In-process wake-channel registry for self-hosted workers.

Per-replica seam: this registry lives in the FastAPI server's AppState for a
single process / single event loop. Wakes delivered here are best-effort;
safety nets (polling, retries, watchdog) cover the cases where a worker is
connected to a different replica or the event is missed.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class WakeChannelRegistry:
    """One live asyncio.Queue per connected worker, keyed by worker_id.

    All methods are safe to call from a single event loop without locks.
    """

    def __init__(self) -> None:
        self._channels: dict[str, asyncio.Queue[str]] = {}
        # Worker ids with a wake that was pushed while their channel was down.
        # A flapping SSE connection drops wakes pushed during the reconnect
        # gap; owing them here replays exactly the missed wake on the next
        # connect so the queued job drains without waiting for the throttled
        # boot re-check or the safety cron. In-process/best-effort like the
        # rest of the registry: a worker that reconnects to a different
        # replica falls back to those safety nets.
        self._owed_wakes: set[str] = set()

    def connect(self, worker_id: str) -> asyncio.Queue[str]:
        """Open a channel for worker_id.

        If a channel already exists for this id, pushes "replaced" onto the
        old queue and installs a fresh queue. A wake owed from a push that
        missed while the worker was disconnected is delivered on the fresh
        queue. Returns the new queue.
        """
        existing = self._channels.get(worker_id)
        if existing is not None:
            existing.put_nowait("replaced")
            logger.debug("worker_channel: replaced existing channel for %s", worker_id)
        q: asyncio.Queue[str] = asyncio.Queue()
        self._channels[worker_id] = q
        if worker_id in self._owed_wakes:
            self._owed_wakes.discard(worker_id)
            q.put_nowait("wake")
            logger.debug("worker_channel: delivered owed wake to %s on connect", worker_id)
        return q

    def disconnect(self, worker_id: str, queue: asyncio.Queue[str]) -> None:
        """Remove the channel for worker_id only if it IS queue (identity check).

        A stale disconnect after a reconnect does not evict the new channel.
        """
        current = self._channels.get(worker_id)
        if current is queue:
            del self._channels[worker_id]
            logger.debug("worker_channel: disconnected %s", worker_id)

    def push(self, worker_id: str, event: str) -> bool:
        """Push event onto the channel for worker_id.

        Returns True if the worker was connected, False otherwise. A ``"wake"``
        that misses (worker not connected) is owed and replayed on the next
        connect; control frames (``disabled``/``enabled``/``replaced``) are
        stateful and never replayed. The return value stays "was it connected"
        so the dispatcher's tier/escalation logic is unaffected by owing.
        """
        q = self._channels.get(worker_id)
        if q is None:
            if event == "wake":
                self._owed_wakes.add(worker_id)
            return False
        q.put_nowait(event)
        return True

    def connected_ids(self) -> frozenset[str]:
        """Return the set of currently connected worker ids."""
        return frozenset(self._channels)
