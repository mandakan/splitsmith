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

    def connect(self, worker_id: str) -> asyncio.Queue[str]:
        """Open a channel for worker_id.

        If a channel already exists for this id, pushes "replaced" onto the
        old queue and installs a fresh queue. Returns the new queue.
        """
        existing = self._channels.get(worker_id)
        if existing is not None:
            existing.put_nowait("replaced")
            logger.debug("worker_channel: replaced existing channel for %s", worker_id)
        q: asyncio.Queue[str] = asyncio.Queue()
        self._channels[worker_id] = q
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

        Returns True if the worker was connected, False otherwise.
        """
        q = self._channels.get(worker_id)
        if q is None:
            return False
        q.put_nowait(event)
        return True

    def connected_ids(self) -> frozenset[str]:
        """Return the set of currently connected worker ids."""
        return frozenset(self._channels)
