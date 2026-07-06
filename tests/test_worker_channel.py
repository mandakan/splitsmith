"""Tests for WakeChannelRegistry.

Uses asyncio.run() around each call - same pattern as test_workers_store.py.
The registry is single-event-loop, so asyncio.run() gives a fresh loop per
top-level call which is fine for unit tests (no shared state across calls).
"""

from __future__ import annotations

import asyncio

from splitsmith.worker_channel import WakeChannelRegistry


# - push to unconnected id returns False
def test_push_unconnected_returns_false() -> None:
    reg = WakeChannelRegistry()
    result = reg.push("worker-1", "wake")
    assert result is False


# - connect + push("wake") is readable from the queue
def test_connect_and_push_readable() -> None:
    reg = WakeChannelRegistry()

    async def _run() -> str:
        q = reg.connect("worker-1")
        reg.push("worker-1", "wake")
        return await q.get()

    event = asyncio.run(_run())
    assert event == "wake"


# - push returns True when connected
def test_push_connected_returns_true() -> None:
    reg = WakeChannelRegistry()
    reg.connect("worker-2")
    result = reg.push("worker-2", "wake")
    assert result is True


# - reconnect supersedes: old queue receives "replaced", push lands on new queue
def test_reconnect_supersedes() -> None:
    reg = WakeChannelRegistry()

    async def _run() -> tuple[str, str]:
        old_q = reg.connect("worker-1")
        new_q = reg.connect("worker-1")
        # old queue should receive "replaced"
        replaced_msg = await old_q.get()
        # push lands on the new queue
        reg.push("worker-1", "wake")
        new_msg = await new_q.get()
        return replaced_msg, new_msg

    replaced_msg, new_msg = asyncio.run(_run())
    assert replaced_msg == "replaced"
    assert new_msg == "wake"


# - disconnect with the stale queue does not remove the new connection
def test_disconnect_stale_queue_no_evict() -> None:
    reg = WakeChannelRegistry()
    old_q = reg.connect("worker-1")
    _new_q = reg.connect("worker-1")
    # disconnect with the old (stale) queue - must not evict the new connection
    reg.disconnect("worker-1", old_q)
    result = reg.push("worker-1", "wake")
    assert result is True


# - disconnect with the current queue removes the mapping
def test_disconnect_current_queue_removes_mapping() -> None:
    reg = WakeChannelRegistry()
    q = reg.connect("worker-1")
    reg.disconnect("worker-1", q)
    result = reg.push("worker-1", "wake")
    assert result is False


# - disconnect for unknown id is a no-op
def test_disconnect_unknown_id_noop() -> None:
    reg = WakeChannelRegistry()
    q: asyncio.Queue[str] = asyncio.Queue()
    reg.disconnect("no-such-worker", q)  # must not raise


# - connected_ids reflects connect/disconnect state
def test_connected_ids_reflects_state() -> None:
    reg = WakeChannelRegistry()
    assert reg.connected_ids() == frozenset()

    q1 = reg.connect("worker-A")
    assert reg.connected_ids() == frozenset({"worker-A"})

    reg.connect("worker-B")
    assert reg.connected_ids() == frozenset({"worker-A", "worker-B"})

    reg.disconnect("worker-A", q1)
    assert reg.connected_ids() == frozenset({"worker-B"})


# - push to a disconnected id after disconnect returns False
def test_push_after_disconnect_returns_false() -> None:
    reg = WakeChannelRegistry()
    q = reg.connect("worker-1")
    reg.disconnect("worker-1", q)
    result = reg.push("worker-1", "disabled")
    assert result is False


# - a wake pushed while the worker is disconnected is owed and delivered on
#   the next connect, so a wake lost during a channel flap is recovered
def test_missed_wake_delivered_on_reconnect() -> None:
    reg = WakeChannelRegistry()

    async def _run() -> str:
        # Worker flapped: the wake lands while no channel is open.
        assert reg.push("worker-1", "wake") is False
        # It reconnects; the fresh queue carries the owed wake.
        q = reg.connect("worker-1")
        return await asyncio.wait_for(q.get(), timeout=1.0)

    event = asyncio.run(_run())
    assert event == "wake"


# - only "wake" is owed; control frames (disabled/enabled) are not replayed
def test_control_frames_are_not_owed_on_reconnect() -> None:
    reg = WakeChannelRegistry()
    reg.push("worker-1", "disabled")
    reg.push("worker-1", "enabled")
    q = reg.connect("worker-1")
    assert q.empty()


# - an owed wake is delivered once, not on every subsequent reconnect
def test_owed_wake_delivered_only_once() -> None:
    reg = WakeChannelRegistry()

    async def _run() -> bool:
        reg.push("worker-1", "wake")  # owed while offline
        q1 = reg.connect("worker-1")
        assert await asyncio.wait_for(q1.get(), timeout=1.0) == "wake"  # delivered on first reconnect
        reg.disconnect("worker-1", q1)
        # Reconnect again with no new wake: nothing owed this time.
        q2 = reg.connect("worker-1")
        return q2.empty()

    assert asyncio.run(_run()) is True


# - a wake delivered to a live channel is not also owed on the next reconnect
def test_delivered_wake_is_not_owed() -> None:
    reg = WakeChannelRegistry()

    async def _run() -> bool:
        q1 = reg.connect("worker-1")
        assert reg.push("worker-1", "wake") is True
        assert await asyncio.wait_for(q1.get(), timeout=1.0) == "wake"
        reg.disconnect("worker-1", q1)
        q2 = reg.connect("worker-1")
        return q2.empty()

    assert asyncio.run(_run()) is True
