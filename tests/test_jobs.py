"""Unit tests for the production-UI job registry."""

from __future__ import annotations

import threading
import time

import pytest

from splitsmith.ui.jobs import JobRegistry, JobStatus


def _wait_until(predicate, *, timeout=2.0, poll=0.01):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return predicate()


def test_submit_runs_to_succeeded() -> None:
    reg = JobRegistry(max_concurrent=2)
    seen = threading.Event()

    def work(_handle):
        seen.set()

    job = reg.submit(kind="test", fn=work)
    assert job.status == JobStatus.PENDING
    assert seen.wait(timeout=2.0), "worker should have run"
    assert _wait_until(lambda: reg.get(job.id).status == JobStatus.SUCCEEDED)
    assert reg.get(job.id).progress == 1.0


def test_failed_job_records_error() -> None:
    reg = JobRegistry(max_concurrent=1)

    def boom(_handle):
        raise ValueError("oh no")

    job = reg.submit(kind="test", fn=boom)
    assert _wait_until(lambda: reg.get(job.id).status == JobStatus.FAILED)
    assert reg.get(job.id).error == "oh no"
    assert reg.get(job.id).finished_at is not None


def test_handle_update_changes_progress_and_message() -> None:
    reg = JobRegistry(max_concurrent=1)
    proceed = threading.Event()
    started = threading.Event()

    def slow(handle):
        handle.update(progress=0.25, message="phase 1")
        started.set()
        proceed.wait(timeout=2.0)

    job = reg.submit(kind="test", fn=slow)
    assert started.wait(timeout=2.0)
    snapshot = reg.get(job.id)
    assert snapshot.status == JobStatus.RUNNING
    assert snapshot.progress == pytest.approx(0.25)
    assert snapshot.message == "phase 1"
    proceed.set()
    assert _wait_until(lambda: reg.get(job.id).status == JobStatus.SUCCEEDED)


def test_progress_clamped_to_unit_range() -> None:
    reg = JobRegistry(max_concurrent=1)
    captured = []

    def fn(handle):
        handle.update(progress=-0.5)
        captured.append(reg.get(handle.id).progress)
        handle.update(progress=2.0)
        captured.append(reg.get(handle.id).progress)

    job = reg.submit(kind="test", fn=fn)
    assert _wait_until(lambda: reg.get(job.id).status == JobStatus.SUCCEEDED)
    assert captured == [0.0, 1.0]


def test_list_preserves_insertion_order() -> None:
    reg = JobRegistry(max_concurrent=2)
    a = reg.submit(kind="a", fn=lambda _h: None)
    b = reg.submit(kind="b", fn=lambda _h: None)
    c = reg.submit(kind="c", fn=lambda _h: None)
    ids = [j.id for j in reg.list()]
    assert ids == [a.id, b.id, c.id]


def test_finished_jobs_are_evicted_past_retention_limit() -> None:
    reg = JobRegistry(max_concurrent=2, retain_recent=2)
    jobs = []
    for i in range(5):
        jobs.append(reg.submit(kind=f"job-{i}", fn=lambda _h: None))

    def _all_done() -> bool:
        for j in jobs:
            snap = reg.get(j.id)
            if snap is not None and snap.status != JobStatus.SUCCEEDED:
                return False
        return True

    assert _wait_until(_all_done)
    remaining = reg.list()
    assert len(remaining) == 2
    # The two most recent jobs survive.
    assert [j.kind for j in remaining] == ["job-3", "job-4"]


def test_running_jobs_never_evicted() -> None:
    reg = JobRegistry(max_concurrent=3, retain_recent=1)
    proceed = threading.Event()

    def slow(_h):
        proceed.wait(timeout=2.0)

    a = reg.submit(kind="slow-a", fn=slow)
    b = reg.submit(kind="slow-b", fn=slow)
    # Submit several quick finishers; retention is 1 but the two slow jobs
    # are RUNNING and must survive the eviction sweep.
    for _ in range(5):
        reg.submit(kind="quick", fn=lambda _h: None)
    time.sleep(0.05)  # let quick jobs complete
    snapshots = {j.id: j for j in reg.list()}
    assert a.id in snapshots
    assert b.id in snapshots
    assert snapshots[a.id].status == JobStatus.RUNNING
    proceed.set()


def test_get_returns_none_for_unknown_id() -> None:
    reg = JobRegistry()
    assert reg.get("does-not-exist") is None
