"""In-memory job registry for long-running production-UI operations.

Why this exists
---------------
Operations like ``detect-beep``, audit-mode ``trim``, future shot-detect
and FCPXML export shell out to ffmpeg / numpy and can take many seconds
on large clips. The original endpoints blocked the HTTP request for the
whole duration. Two consequences fell out of that:

1. **Reload erases progress visibility.** A frustrated user who reloads
   mid-trim has no way to tell whether the operation is still running or
   has crashed.
2. **No feedback channel.** ffmpeg's stderr is captured but never
   surfaced; the SPA shows a generic spinner with no indication of phase
   or estimated time.

The job registry decouples submission from completion. Endpoints submit
a callable, get a ``Job`` handle back immediately, and the SPA polls
``/api/jobs/{id}`` until the job finishes. The registry survives across
HTTP requests for the lifetime of the server process; restart still
loses jobs (which is fine for v1 -- they'll just need to be re-run).

Design notes
------------
- One ``JobRegistry`` per server process; held by ``AppState``.
- Jobs run on a small ``ThreadPoolExecutor`` (default 2 workers). FFmpeg
  is the bottleneck; running 2 in parallel is already on the edge of
  saturating a typical CPU and disk on the user's laptop.
- Jobs report progress + a human-readable message via :class:`JobHandle`,
  which is the write-only view passed to the worker function.
- Finished jobs are retained for ~50 entries so a recent failure stays
  visible after the user clicks somewhere else; older finished jobs are
  evicted in insertion order. Active jobs are never evicted.
- This module has zero knowledge of the splitsmith pipeline -- it's a
  general-purpose async job pump. Pipeline-specific logic stays in
  ``server.py`` (or grows into a sibling ``operations.py`` if it gets
  unwieldy).
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class JobStatus(StrEnum):
    """Job lifecycle states.

    PENDING -> RUNNING -> (SUCCEEDED | FAILED)

    There is no CANCELLED state in v1. Cancellation needs a cooperative
    check inside the worker; we'll add it when the first user-facing need
    appears.
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Job(BaseModel):
    """Wire shape returned by ``/api/jobs/{id}``."""

    id: str
    kind: str  # "detect_beep" | "trim" | etc; used for SPA copy + filtering
    stage_number: int | None = None
    status: JobStatus
    progress: float | None = None  # 0..1 when known
    message: str | None = None  # human-readable phase, e.g. "Extracting audio..."
    error: str | None = None  # populated only on FAILED
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JobHandle:
    """Limited write-only view passed to job functions.

    Workers should not mutate Job instances directly; they go through
    :meth:`update` which serializes via the registry's lock. This keeps
    the polling endpoint always observing a consistent snapshot.
    """

    def __init__(self, registry: JobRegistry, job_id: str) -> None:
        self._registry = registry
        self._job_id = job_id

    @property
    def id(self) -> str:
        return self._job_id

    def update(
        self,
        *,
        progress: float | None = None,
        message: str | None = None,
    ) -> None:
        """Bump progress / message. Both arguments are optional."""
        kwargs: dict[str, Any] = {}
        if progress is not None:
            kwargs["progress"] = max(0.0, min(1.0, float(progress)))
        if message is not None:
            kwargs["message"] = message
        if kwargs:
            self._registry._patch(self._job_id, **kwargs)


class JobRegistry:
    """Thread-safe in-memory job tracker."""

    def __init__(self, *, max_concurrent: int = 2, retain_recent: int = 50) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrent,
            thread_name_prefix="splitsmith-job",
        )
        self._retain = retain_recent

    def submit(
        self,
        *,
        kind: str,
        fn: Callable[[JobHandle], None],
        stage_number: int | None = None,
    ) -> Job:
        """Schedule ``fn`` to run on the thread pool.

        Returns the job snapshot (status=PENDING) immediately so the HTTP
        handler can hand the id back to the caller without blocking.
        """
        now = datetime.now(UTC)
        job = Job(
            id=uuid.uuid4().hex,
            kind=kind,
            stage_number=stage_number,
            status=JobStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._trim_retained_locked()
            # Snapshot before releasing the lock + dispatching to the
            # executor; otherwise the worker may have already flipped
            # status to RUNNING by the time we copy.
            snapshot = job.model_copy(deep=True)
        self._executor.submit(self._run, job.id, fn)
        return snapshot

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            j = self._jobs.get(job_id)
            return j.model_copy(deep=True) if j is not None else None

    def list(self) -> list[Job]:
        """Snapshot of all retained jobs in submission order."""
        with self._lock:
            return [
                self._jobs[jid].model_copy(deep=True) for jid in self._order if jid in self._jobs
            ]

    def find_active(self, *, kind: str, stage_number: int | None = None) -> Job | None:
        """Return the first PENDING/RUNNING job matching ``(kind, stage_number)``.

        Used to dedupe submissions: a second click of "Trim now" while the
        first job is still running should adopt the existing job instead
        of spawning a parallel ffmpeg that races on the same output file.
        """
        with self._lock:
            for jid in self._order:
                j = self._jobs.get(jid)
                if j is None:
                    continue
                if j.status not in (JobStatus.PENDING, JobStatus.RUNNING):
                    continue
                if j.kind != kind or j.stage_number != stage_number:
                    continue
                return j.model_copy(deep=True)
            return None

    # ------------------------------------------------------------------
    # Internal -- worker glue. These run on the executor thread.
    # ------------------------------------------------------------------

    def _run(self, job_id: str, fn: Callable[[JobHandle], None]) -> None:
        with self._lock:
            j = self._jobs.get(job_id)
            if j is None:
                return
            j.status = JobStatus.RUNNING
            j.started_at = datetime.now(UTC)
            j.updated_at = j.started_at
        try:
            fn(JobHandle(self, job_id))
        except Exception as exc:  # noqa: BLE001 -- worker exceptions become job state
            logger.exception("job %s failed", job_id)
            with self._lock:
                j = self._jobs.get(job_id)
                if j is not None:
                    j.status = JobStatus.FAILED
                    j.error = str(exc)
                    j.finished_at = datetime.now(UTC)
                    j.updated_at = j.finished_at
                self._trim_retained_locked()
            return
        with self._lock:
            j = self._jobs.get(job_id)
            if j is not None and j.status == JobStatus.RUNNING:
                j.status = JobStatus.SUCCEEDED
                if j.progress is None or j.progress < 1.0:
                    j.progress = 1.0
                j.finished_at = datetime.now(UTC)
                j.updated_at = j.finished_at
            self._trim_retained_locked()

    def _patch(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            j = self._jobs.get(job_id)
            if j is None:
                return
            for k, v in fields.items():
                setattr(j, k, v)
            j.updated_at = datetime.now(UTC)

    def _trim_retained_locked(self) -> None:
        """Evict oldest finished jobs once the retention limit is hit.

        Active jobs (PENDING / RUNNING) are never evicted. Called under
        ``self._lock``; do not acquire it again.
        """
        finished = [
            jid
            for jid in self._order
            if jid in self._jobs
            and self._jobs[jid].status in (JobStatus.SUCCEEDED, JobStatus.FAILED)
        ]
        excess = len(finished) - self._retain
        if excess > 0:
            for jid in finished[:excess]:
                self._jobs.pop(jid, None)
                self._order.remove(jid)
