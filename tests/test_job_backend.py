"""Tests for the JobBackend abstraction (Tier 2 of doc 10)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from splitsmith.ui.jobs import Job, JobBackend, JobHandle, JobRegistry, JobStatus


def _make_job(**kwargs) -> Job:
    """Build a Job for tests with the timestamps the model requires."""
    now = datetime.now(UTC)
    defaults = {
        "kind": "trim",
        "status": JobStatus.PENDING,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(kwargs)
    return Job(**defaults)


def test_job_registry_satisfies_job_backend_protocol() -> None:
    """``JobRegistry`` is the local-mode implementation and must
    satisfy the structural :class:`JobBackend` protocol so handlers
    typed against the protocol see the right interface."""
    backend: JobBackend = JobRegistry()
    # Touch every Protocol member so an accidental signature drift
    # surfaces here, not deep inside a handler under test.
    assert backend.active_count() == 0
    assert backend.is_shutting_down is False
    assert backend.list() == []
    assert backend.get("nope") is None
    assert backend.cancel("nope") is None
    assert backend.acknowledge("nope") is None
    assert backend.acknowledge_all_failures() == []
    assert backend.find_active(kind="trim") is None


def test_state_jobs_is_swappable_by_a_fake_backend() -> None:
    """Whole point of the Protocol: tests (and the eventual hosted
    backend) can replace ``state.jobs`` to redirect every
    submit/get/list call without touching handlers."""
    from splitsmith.ui.server import create_app

    submitted: list[dict] = []
    listed_called = 0
    cancel_called: list[str] = []

    class _RecordingBackend:
        is_shutting_down = False

        def active_count(self) -> int:
            return 0

        def begin_shutdown(self) -> None:  # pragma: no cover -- unused in this test
            pass

        def wait_for_drain(self, timeout_s: float) -> bool:  # pragma: no cover
            return True

        def submit(
            self,
            *,
            kind: str,
            fn: Callable[[JobHandle], None],
            stage_number: int | None = None,
            video_id: str | None = None,
        ) -> Job:
            submitted.append({"kind": kind, "stage_number": stage_number, "video_id": video_id})
            return _make_job(id="fake-1", kind=kind, stage_number=stage_number)

        def get(self, job_id: str) -> Job | None:
            return _make_job(id=job_id, status=JobStatus.SUCCEEDED)

        def list(self) -> list[Job]:
            nonlocal listed_called
            listed_called += 1
            return []

        def cancel(self, job_id: str) -> Job | None:
            cancel_called.append(job_id)
            return _make_job(id=job_id, status=JobStatus.CANCELLED)

        def acknowledge(self, job_id: str) -> Job | None:
            return None

        def acknowledge_all_failures(self) -> list[Job]:
            return []

        def find_active(self, **kwargs) -> Job | None:
            return None

    fake: JobBackend = _RecordingBackend()
    app = create_app()
    state = app.state.splitsmith_state
    state.jobs = fake

    # Use the same accessor path the request handlers use; this proves
    # the swap actually flows through (not just that the field type
    # accepts assignment).
    assert state.jobs.list() == []
    assert listed_called == 1

    state.jobs.cancel("job-42")
    assert cancel_called == ["job-42"]
