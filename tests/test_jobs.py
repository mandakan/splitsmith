"""Unit tests for the production-UI job registry."""

from __future__ import annotations

import threading
import time

import pytest

from splitsmith.ui.jobs import JobCancelled, JobRegistry, JobStatus


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


# ---------------------------------------------------------------------------
# Cancellation (issue #26)
# ---------------------------------------------------------------------------


def test_cancel_pending_job_skips_worker_and_marks_cancelled() -> None:
    """A cancel that arrives before the worker thread starts must be
    observed in ``_run`` and short-circuit straight to CANCELLED. We hold
    the executor with a slow predecessor so the second submit is still
    PENDING when we cancel it."""
    reg = JobRegistry(max_concurrent=1)
    proceed = threading.Event()
    started = threading.Event()

    def hold(_h):
        started.set()
        proceed.wait(timeout=2.0)

    blocker = reg.submit(kind="hold", fn=hold)
    assert started.wait(timeout=2.0)

    ran = threading.Event()

    def victim(_h):
        ran.set()

    queued = reg.submit(kind="victim", fn=victim)
    assert reg.get(queued.id).status == JobStatus.PENDING
    snapshot = reg.cancel(queued.id)
    assert snapshot is not None
    assert snapshot.cancel_requested is True

    proceed.set()
    assert _wait_until(lambda: reg.get(queued.id).status == JobStatus.CANCELLED)
    assert not ran.is_set(), "cancelled-before-start jobs must skip the worker"
    # Drain the blocker so the suite doesn't leak threads.
    assert _wait_until(lambda: reg.get(blocker.id).status == JobStatus.SUCCEEDED)


def test_cancel_running_job_via_check_cancel() -> None:
    """A long-running worker observes the cancel via ``check_cancel`` and
    raises ``JobCancelled``; the registry maps that to status=CANCELLED
    instead of FAILED."""
    reg = JobRegistry(max_concurrent=1)
    started = threading.Event()
    proceed = threading.Event()
    finished = threading.Event()

    def slow(handle):
        handle.update(progress=0.1, message="phase 1")
        started.set()
        proceed.wait(timeout=2.0)
        # Cancel arrived during the wait -- this raises JobCancelled.
        handle.check_cancel()
        finished.set()  # never reached

    job = reg.submit(kind="slow", fn=slow)
    assert started.wait(timeout=2.0)
    reg.cancel(job.id)
    proceed.set()
    assert _wait_until(lambda: reg.get(job.id).status == JobStatus.CANCELLED)
    assert finished.is_set() is False
    snapshot = reg.get(job.id)
    assert snapshot.cancel_requested is True
    assert snapshot.error is None  # CANCELLED is not an error


def test_cancel_finished_job_is_noop() -> None:
    """Cancelling an already-succeeded job returns the snapshot unchanged
    -- idempotent behaviour the SPA can rely on when the user clicks
    Cancel right as the job completes."""
    reg = JobRegistry(max_concurrent=1)
    job = reg.submit(kind="quick", fn=lambda _h: None)
    assert _wait_until(lambda: reg.get(job.id).status == JobStatus.SUCCEEDED)
    snapshot = reg.cancel(job.id)
    assert snapshot is not None
    assert snapshot.status == JobStatus.SUCCEEDED
    assert snapshot.cancel_requested is False


def test_cancel_unknown_job_returns_none() -> None:
    reg = JobRegistry()
    assert reg.cancel("does-not-exist") is None


def test_attach_subprocess_terminates_on_cancel() -> None:
    """When ffmpeg is registered via ``attach_subprocess`` and a cancel
    arrives, the registry calls ``terminate()`` so the encoder unblocks
    promptly. Without this the worker sits inside ``proc.wait()`` until
    the entire 2-4 minute encode finishes."""
    reg = JobRegistry(max_concurrent=1)

    class FakePopen:
        def __init__(self) -> None:
            self.terminated = threading.Event()

        def poll(self) -> int | None:
            return None if not self.terminated.is_set() else 1

        def terminate(self) -> None:
            self.terminated.set()

    fake = FakePopen()
    started = threading.Event()
    raised: list[BaseException] = []

    def worker(handle):
        handle.attach_subprocess(fake)
        started.set()
        # Simulate ffmpeg blocking on a long encode; we wait on the
        # FakePopen's flag which the cancel flips.
        if not fake.terminated.wait(timeout=2.0):
            return
        try:
            handle.check_cancel()
        except JobCancelled as exc:  # noqa: PERF203 -- we want to capture it
            raised.append(exc)
            raise

    job = reg.submit(kind="ffmpeg", fn=worker)
    assert started.wait(timeout=2.0)
    reg.cancel(job.id)
    assert _wait_until(lambda: reg.get(job.id).status == JobStatus.CANCELLED)
    assert fake.terminated.is_set(), "registry must terminate the registered subprocess"
    assert raised, "worker should have observed the cancel after terminate()"


def test_attach_after_cancel_terminates_immediately() -> None:
    """If the worker registers a subprocess after the cancel has already
    been observed (race: cancel between submit and attach), we still
    terminate it and the worker bails out."""
    reg = JobRegistry(max_concurrent=1)

    class FakePopen:
        def __init__(self) -> None:
            self.terminated = False

        def poll(self) -> int | None:
            return 1 if self.terminated else None

        def terminate(self) -> None:
            self.terminated = True

    fake = FakePopen()
    proceed = threading.Event()
    started = threading.Event()
    cancel_observed = threading.Event()

    def worker(handle):
        started.set()
        proceed.wait(timeout=2.0)
        try:
            handle.attach_subprocess(fake)
        except JobCancelled:
            cancel_observed.set()
            raise

    job = reg.submit(kind="ffmpeg", fn=worker)
    assert started.wait(timeout=2.0)
    reg.cancel(job.id)  # cancel before attach
    proceed.set()
    assert _wait_until(lambda: reg.get(job.id).status == JobStatus.CANCELLED)
    assert fake.terminated, "post-cancel attach must still terminate the proc"
    assert cancel_observed.is_set()


# ---------------------------------------------------------------------------
# Failure acknowledgment (issue #73)
# ---------------------------------------------------------------------------


def test_acknowledge_flips_failed_job_to_seen() -> None:
    reg = JobRegistry(max_concurrent=1)

    def boom(_h):
        raise ValueError("kaboom")

    job = reg.submit(kind="test", fn=boom)
    assert _wait_until(lambda: reg.get(job.id).status == JobStatus.FAILED)
    assert reg.get(job.id).acknowledged is False
    snap = reg.acknowledge(job.id)
    assert snap is not None
    assert snap.acknowledged is True
    # Idempotent: a second ack returns the same snapshot.
    again = reg.acknowledge(job.id)
    assert again is not None and again.acknowledged is True


def test_acknowledge_noop_for_non_failed_job() -> None:
    reg = JobRegistry(max_concurrent=1)
    job = reg.submit(kind="test", fn=lambda _h: None)
    assert _wait_until(lambda: reg.get(job.id).status == JobStatus.SUCCEEDED)
    snap = reg.acknowledge(job.id)
    assert snap is not None
    assert snap.acknowledged is False, "acknowledge() must not mark non-failed jobs"


def test_acknowledge_unknown_job_returns_none() -> None:
    reg = JobRegistry()
    assert reg.acknowledge("does-not-exist") is None


def test_acknowledge_all_failures_marks_only_unacked_failures() -> None:
    reg = JobRegistry(max_concurrent=1)
    fail_a = reg.submit(kind="a", fn=lambda _h: (_ for _ in ()).throw(RuntimeError("a")))
    fail_b = reg.submit(kind="b", fn=lambda _h: (_ for _ in ()).throw(RuntimeError("b")))
    ok = reg.submit(kind="ok", fn=lambda _h: None)

    def _all_done() -> bool:
        return all(
            reg.get(j.id) is not None
            and reg.get(j.id).status in (JobStatus.SUCCEEDED, JobStatus.FAILED)
            for j in (fail_a, fail_b, ok)
        )

    assert _wait_until(_all_done)

    # Pre-ack one failure so we can confirm the bulk endpoint skips it.
    reg.acknowledge(fail_a.id)

    affected = reg.acknowledge_all_failures()
    affected_ids = {j.id for j in affected}
    assert affected_ids == {fail_b.id}
    assert reg.get(fail_b.id).acknowledged is True
    assert reg.get(ok.id).acknowledged is False, "non-failed jobs must stay untouched"


def test_retention_protects_unacked_failures_from_succeeded_flood() -> None:
    """An unacknowledged failure must survive a flurry of successes
    that would otherwise push it past the retention cap."""
    reg = JobRegistry(max_concurrent=1, retain_recent=2)

    def boom(_h):
        raise RuntimeError("boom")

    fail = reg.submit(kind="fail", fn=boom)
    assert _wait_until(lambda: reg.get(fail.id).status == JobStatus.FAILED)

    for i in range(3):
        reg.submit(kind=f"ok-{i}", fn=lambda _h: None)
    assert _wait_until(
        lambda: not any(
            j.status in (JobStatus.PENDING, JobStatus.RUNNING) for j in reg.list()
        )
    )

    surviving = {j.id for j in reg.list()}
    assert fail.id in surviving, "unacknowledged failure must not be evicted"


def test_retention_evicts_acked_failure_before_unacked() -> None:
    """When the registry is forced to drop a failure, the dismissed one
    goes first so the user keeps seeing the failures they haven't
    acknowledged yet."""
    reg = JobRegistry(max_concurrent=1, retain_recent=1)

    def boom(_h):
        raise RuntimeError("boom")

    older = reg.submit(kind="older", fn=boom)
    assert _wait_until(lambda: reg.get(older.id).status == JobStatus.FAILED)
    reg.acknowledge(older.id)

    newer = reg.submit(kind="newer", fn=boom)
    assert _wait_until(lambda: reg.get(newer.id).status == JobStatus.FAILED)

    # retention=1 with two failures (one acked, one not) must evict the
    # acknowledged one and keep the unacknowledged one visible.
    surviving = {j.id for j in reg.list()}
    assert newer.id in surviving
    assert older.id not in surviving
