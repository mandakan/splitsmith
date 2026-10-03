"""Tests for :mod:`splitsmith.async_bridge`: the DbRunner thread and run_sync routing."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import time
from collections.abc import Iterator

import pytest

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

    # A running loop: a throwaway thread with its own loop, not the caller's,
    # which is closed after and was driven from a different OS thread.
    outer_loop_holder: list[asyncio.AbstractEventLoop] = []
    outer_thread_id_holder: list[int] = []

    async def _which_thread() -> tuple[asyncio.AbstractEventLoop, int]:
        return asyncio.get_running_loop(), threading.get_ident()

    async def _outer2() -> tuple[asyncio.AbstractEventLoop, int]:
        outer_loop_holder.append(asyncio.get_running_loop())
        outer_thread_id_holder.append(threading.get_ident())
        return run_sync(_which_thread())

    inner_loop, inner_thread_id = asyncio.run(_outer2())
    assert inner_loop is not outer_loop_holder[0]
    assert inner_loop.is_closed()
    assert inner_thread_id != outer_thread_id_holder[0]


def test_start_raises_when_on_start_fails_and_does_not_hang() -> None:
    def _bad_start() -> None:
        raise ValueError("boom")

    r = DbRunner(on_start=_bad_start)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(r.start)
        # If the fix regresses to the pre-fix hang, this times out loudly
        # instead of blocking the test suite forever.
        with pytest.raises(ValueError, match="boom"):
            future.result(timeout=5.0)

    assert not r.is_running


def test_stop_cancels_in_flight_run_callers() -> None:
    r = DbRunner()
    r.start()
    try:
        result: dict[str, BaseException | None] = {}

        def _call() -> None:
            try:
                r.run(asyncio.sleep(5))
            except BaseException as exc:  # noqa: BLE001 -- captured for the asserting thread
                result["exception"] = exc
            else:
                result["exception"] = None

        caller = threading.Thread(target=_call)
        caller.start()
        time.sleep(0.2)
        r.stop()
        caller.join(timeout=5.0)
        assert not caller.is_alive()
        assert isinstance(result["exception"], (concurrent.futures.CancelledError, asyncio.CancelledError))
    finally:
        if r.is_running:
            r.stop()


def test_on_stop_exception_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    async def _on_stop() -> None:
        raise RuntimeError("on_stop boom")

    r = DbRunner(on_stop=_on_stop)
    r.start()
    with caplog.at_level(logging.WARNING, logger="splitsmith.async_bridge"):
        r.stop()  # must not raise
    assert "on_stop failed" in caplog.text


def test_run_after_stop_raises_clearly() -> None:
    r = DbRunner()
    r.start()
    r.stop()

    async def _noop() -> None:
        return None

    with pytest.raises(RuntimeError, match="not running"):
        r.run(_noop())


def test_fresh_loop_fallback_propagates_contextvars() -> None:
    assert get_runner() is None

    async def _read_only() -> bool:
        return share_request_is_read_only()

    async def _outer() -> bool:
        return run_sync(_read_only())

    token = current_share_scope.set("read")
    try:
        assert asyncio.run(_outer()) is True
    finally:
        current_share_scope.reset(token)
