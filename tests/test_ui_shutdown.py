"""Tests for POST /api/shutdown and JobRegistry drain (issue #369)."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith.ui.jobs import JobRegistry, JobStatus, ShutdownInProgressError
from splitsmith.ui.server import create_app


def _wait_until(predicate, *, timeout=2.0, poll=0.01) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return predicate()


# ----------------------------------------------------------------------
# JobRegistry-level drain primitives
# ----------------------------------------------------------------------


def test_begin_shutdown_blocks_new_submissions() -> None:
    reg = JobRegistry(max_concurrent=1)
    reg.begin_shutdown()
    with pytest.raises(ShutdownInProgressError):
        asyncio.run(submit_fn(reg, kind="test", fn=lambda _h: None))


def test_begin_shutdown_is_idempotent() -> None:
    reg = JobRegistry(max_concurrent=1)
    reg.begin_shutdown()
    reg.begin_shutdown()  # second call is a no-op
    assert reg.is_shutting_down


def test_wait_for_drain_returns_immediately_when_idle() -> None:
    reg = JobRegistry(max_concurrent=1)
    assert reg.wait_for_drain(timeout_s=0.1) is True


def test_wait_for_drain_waits_for_in_flight_then_returns() -> None:
    reg = JobRegistry(max_concurrent=1)
    proceed = threading.Event()

    def slow(_handle):
        proceed.wait(timeout=2.0)

    job = asyncio.run(submit_fn(reg, kind="test", fn=slow))
    assert _wait_until(lambda: asyncio.run(reg.get(job.id)).status == JobStatus.RUNNING)

    reg.begin_shutdown()

    # Drain hasn't completed yet -- the worker is still blocked.
    assert reg.wait_for_drain(timeout_s=0.05) is False

    proceed.set()
    assert reg.wait_for_drain(timeout_s=2.0) is True
    assert reg.active_count() == 0


def test_wait_for_drain_returns_false_on_timeout() -> None:
    reg = JobRegistry(max_concurrent=1)
    proceed = threading.Event()

    def slow(_handle):
        proceed.wait(timeout=5.0)

    asyncio.run(submit_fn(reg, kind="test", fn=slow))
    assert _wait_until(lambda: reg.active_count() == 1)

    reg.begin_shutdown()
    assert reg.wait_for_drain(timeout_s=0.05) is False

    # Cleanup: let the worker finish so the executor shuts down promptly.
    proceed.set()


def test_active_count_excludes_terminal_states() -> None:
    reg = JobRegistry(max_concurrent=1)

    def quick(_handle):
        pass

    job = asyncio.run(submit_fn(reg, kind="test", fn=quick))
    assert _wait_until(lambda: asyncio.run(reg.get(job.id)).status == JobStatus.SUCCEEDED)
    assert reg.active_count() == 0


# ----------------------------------------------------------------------
# HTTP route
# ----------------------------------------------------------------------


from tests.conftest import scaffold_match, submit_fn  # noqa: E402


def _make_client(tmp_path: Path) -> TestClient:
    root, _ = scaffold_match(tmp_path, name="Shutdown Test")
    app = create_app(project_root=root, project_name="Shutdown Test")
    return TestClient(app)


def test_shutdown_returns_202_and_kicks_drain(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    resp = client.post("/api/shutdown")
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "shutting_down"
    assert body["already"] is False

    state = client.app.state.splitsmith_state
    assert state.jobs.is_shutting_down


def test_shutdown_is_idempotent(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    first = client.post("/api/shutdown")
    second = client.post("/api/shutdown")
    third = client.post("/api/shutdown")
    assert first.status_code == 202
    assert first.json()["already"] is False
    assert second.status_code == 202
    assert second.json()["already"] is True
    assert third.status_code == 202
    assert third.json()["already"] is True


def test_shutdown_rejects_non_loopback(tmp_path: Path) -> None:
    root, _ = scaffold_match(tmp_path, name="Shutdown Test")
    app = create_app(project_root=root, project_name="Shutdown Test")
    client = TestClient(app, client=("192.168.1.50", 51234))
    resp = client.post("/api/shutdown")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "loopback_only"
    state = client.app.state.splitsmith_state
    assert state.jobs.is_shutting_down is False


def test_shutdown_in_progress_error_mapped_to_503(tmp_path: Path) -> None:
    """The global exception handler turns ShutdownInProgressError into a 503."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI()

    # Re-register the same handler the production app uses, so this test
    # validates the mapping shape without depending on a route that happens
    # to call submit().
    @app.exception_handler(ShutdownInProgressError)
    async def _h(_request, exc: ShutdownInProgressError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": "shutting_down", "message": str(exc)}},
        )

    @app.get("/__probe_raise")
    def _probe() -> dict:
        raise ShutdownInProgressError("server is shutting down; no new jobs accepted")

    client = TestClient(app)
    resp = client.get("/__probe_raise")
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "shutting_down"


def test_create_app_registers_shutdown_exception_handler(tmp_path: Path) -> None:
    """create_app registers the ShutdownInProgressError handler so 503s
    work at every submit() callsite without per-route try/except."""
    root, _ = scaffold_match(tmp_path, name="x")
    app = create_app(project_root=root, project_name="x")
    assert ShutdownInProgressError in app.exception_handlers


def test_submit_during_shutdown_raises_at_registry(tmp_path: Path) -> None:
    """End-to-end: after /api/shutdown, JobRegistry.submit refuses new work."""
    client = _make_client(tmp_path)
    client.post("/api/shutdown")
    state = client.app.state.splitsmith_state
    assert state.jobs.is_shutting_down
    with pytest.raises(ShutdownInProgressError):
        asyncio.run(submit_fn(state.jobs, kind="probe", fn=lambda _h: None))


def test_shutdown_calls_handler_after_drain(tmp_path: Path) -> None:
    """The registered shutdown_handler is invoked once the drain completes."""
    client = _make_client(tmp_path)
    state = client.app.state.splitsmith_state

    stopped = threading.Event()
    state.shutdown_handler = stopped.set

    resp = client.post("/api/shutdown")
    assert resp.status_code == 202

    # No active jobs -> drain completes immediately -> handler fires.
    assert stopped.wait(timeout=2.0), "shutdown_handler not invoked after drain"


def test_print_active_jobs_skips_quietly_under_running_loop(caplog) -> None:
    """Regression: hosted ``serve`` handles SIGTERM (a redeploy) while
    uvicorn's event loop is still running. ``_print_active_jobs`` must not
    call ``asyncio.run`` there -- the old code did, caught the resulting
    "cannot be called from a running event loop" RuntimeError, and logged a
    traceback on every redeploy. It should now skip the job dump quietly."""
    import logging

    from fastapi import FastAPI

    from splitsmith.ui.server import _print_active_jobs

    class _JobsThatMustNotRun:
        async def list(self):  # pragma: no cover -- must never be driven here
            raise AssertionError("jobs.list() must not run from inside a live loop")

    class _State:
        jobs = _JobsThatMustNotRun()

    app = FastAPI()
    app.state.splitsmith_state = _State()

    async def _call() -> None:
        _print_active_jobs(app)  # a loop is running -> must just return

    with caplog.at_level(logging.WARNING):
        asyncio.run(_call())  # must not raise

    assert "could not enumerate jobs on shutdown" not in caplog.text


def test_shutdown_drains_in_flight_job_before_calling_handler(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    state = client.app.state.splitsmith_state

    proceed = threading.Event()
    handler_fired = threading.Event()
    state.shutdown_handler = handler_fired.set

    def slow(_handle):
        proceed.wait(timeout=5.0)

    asyncio.run(submit_fn(state.jobs, kind="test", fn=slow))
    assert _wait_until(lambda: state.jobs.active_count() == 1)

    resp = client.post("/api/shutdown")
    assert resp.status_code == 202

    # Handler must not fire while the worker is still in-flight.
    assert not handler_fired.wait(timeout=0.1)

    proceed.set()
    assert handler_fired.wait(timeout=2.0)
