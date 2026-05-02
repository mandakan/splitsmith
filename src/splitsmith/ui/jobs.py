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
import subprocess
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

    PENDING -> RUNNING -> (SUCCEEDED | FAILED | CANCELLED)

    Cancellation is cooperative (issue #26): the worker checks
    :meth:`JobHandle.is_cancel_requested` between phases and bails out
    when set. Workers that shell out to ffmpeg can register the running
    process via :meth:`JobHandle.attach_subprocess` so the registry can
    terminate it directly when a cancel arrives mid-encode.
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobCancelled(Exception):  # noqa: N818 -- control-flow, not error; mirrors CancelledError
    """Raised by a worker (or by :meth:`JobHandle.check_cancel`) once the
    registry observes a cancel request. The :class:`JobRegistry` catches
    this and writes ``status=CANCELLED`` instead of ``FAILED`` so the UI
    can distinguish user-driven aborts from real errors.

    Named for parity with :class:`asyncio.CancelledError` (which is also
    not an "Error"-suffixed exception class).
    """


class Job(BaseModel):
    """Wire shape returned by ``/api/jobs/{id}``."""

    id: str
    kind: str  # "detect_beep" | "trim" | etc; used for SPA copy + filtering
    stage_number: int | None = None
    status: JobStatus
    progress: float | None = None  # 0..1 when known
    message: str | None = None  # human-readable phase, e.g. "Extracting audio..."
    error: str | None = None  # populated only on FAILED
    # True after the SPA POSTed /api/jobs/{id}/cancel. The worker observes
    # this between phases and bails out cooperatively, ending the job in
    # status=CANCELLED. The flag stays True on the terminal snapshot so
    # the UI can label the row "Cancelled by user" instead of "Aborted".
    cancel_requested: bool = False
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

    def is_cancel_requested(self) -> bool:
        """True once the SPA POSTed ``/api/jobs/{id}/cancel`` for this job.

        Workers should poll this between phases (cheap; just a dict lookup
        under the registry lock) and ``raise JobCancelled`` when it returns
        True. Subprocesses registered via :meth:`attach_subprocess` are
        terminated automatically on cancel; the worker still has to bail
        out so the registry can flip status to CANCELLED.
        """
        return self._registry._is_cancel_requested(self._job_id)

    def check_cancel(self) -> None:
        """Raise :class:`JobCancelled` if a cancel has been requested.

        Convenience wrapper for the common ``if cancel: raise`` pattern.
        Use between phases of a multi-step worker so a long stage doesn't
        run to completion after the user clicked Cancel.
        """
        if self.is_cancel_requested():
            raise JobCancelled()

    def attach_subprocess(self, proc: subprocess.Popen) -> None:
        """Register ``proc`` so a future cancel terminates it directly.

        ffmpeg encoding the audit-mode trim ignores Python-side cancel
        flags -- it's blocked in C waiting on the codec. The registry
        keeps a reference to the running ``Popen`` and calls
        ``terminate()`` (then ``kill()`` if SIGTERM is ignored) when a
        cancel arrives. If a cancel was already requested before
        attachment, this terminates the process immediately and raises
        :class:`JobCancelled` so the worker can unwind without doing
        any further encode work.
        """
        already = self._registry._attach_subprocess(self._job_id, proc)
        if already:
            raise JobCancelled()

    def detach_subprocess(self) -> None:
        """Drop the subprocess reference (e.g. after ffmpeg exited normally)."""
        self._registry._detach_subprocess(self._job_id)


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
        # Registered subprocesses keyed by job_id. The trim worker spawns
        # ffmpeg via Popen and registers it via JobHandle.attach_subprocess
        # so we can terminate it on cancel even when the worker is blocked
        # on proc.wait().
        self._subprocs: dict[str, subprocess.Popen] = {}

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

    def cancel(self, job_id: str) -> Job | None:
        """Mark a job for cooperative cancellation.

        Idempotent: cancelling an already-finished job is a no-op (the
        existing snapshot is returned). Cancelling a PENDING job means
        the worker will see ``is_cancel_requested()`` True on its first
        check and bail immediately. For a RUNNING job that has registered
        an ffmpeg subprocess via :meth:`JobHandle.attach_subprocess`, the
        subprocess is also terminated so the encode unblocks promptly.

        Returns the post-cancel job snapshot, or ``None`` if ``job_id``
        is unknown.
        """
        proc_to_kill: subprocess.Popen | None = None
        with self._lock:
            j = self._jobs.get(job_id)
            if j is None:
                return None
            if j.status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED):
                return j.model_copy(deep=True)
            j.cancel_requested = True
            j.updated_at = datetime.now(UTC)
            proc_to_kill = self._subprocs.pop(job_id, None)
            snapshot = j.model_copy(deep=True)
        # Kill outside the lock so a slow ``terminate()`` (proc held a
        # held resource etc.) can't stall other registry callers.
        if proc_to_kill is not None and proc_to_kill.poll() is None:
            try:
                proc_to_kill.terminate()
            except OSError:
                logger.warning("cancel: terminate() failed for job %s", job_id, exc_info=True)
        return snapshot

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
            # If the job was cancelled while still PENDING in the queue,
            # don't even start the worker -- skip straight to CANCELLED.
            if j.cancel_requested:
                j.status = JobStatus.CANCELLED
                j.finished_at = datetime.now(UTC)
                j.updated_at = j.finished_at
                self._trim_retained_locked()
                return
            j.status = JobStatus.RUNNING
            j.started_at = datetime.now(UTC)
            j.updated_at = j.started_at
        try:
            fn(JobHandle(self, job_id))
        except JobCancelled:
            with self._lock:
                j = self._jobs.get(job_id)
                if j is not None:
                    j.status = JobStatus.CANCELLED
                    j.finished_at = datetime.now(UTC)
                    j.updated_at = j.finished_at
                self._subprocs.pop(job_id, None)
                self._trim_retained_locked()
            return
        except Exception as exc:  # noqa: BLE001 -- worker exceptions become job state
            logger.exception("job %s failed", job_id)
            with self._lock:
                j = self._jobs.get(job_id)
                if j is not None:
                    # If a cancel arrived mid-flight and the worker
                    # surfaced the resulting ffmpeg failure as a generic
                    # exception, prefer the CANCELLED label -- the user
                    # asked for the abort, the noisy stderr is expected.
                    if j.cancel_requested:
                        j.status = JobStatus.CANCELLED
                    else:
                        j.status = JobStatus.FAILED
                        j.error = str(exc)
                    j.finished_at = datetime.now(UTC)
                    j.updated_at = j.finished_at
                self._subprocs.pop(job_id, None)
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
            self._subprocs.pop(job_id, None)
            self._trim_retained_locked()

    def _patch(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            j = self._jobs.get(job_id)
            if j is None:
                return
            for k, v in fields.items():
                setattr(j, k, v)
            j.updated_at = datetime.now(UTC)

    def _is_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            j = self._jobs.get(job_id)
            return bool(j and j.cancel_requested)

    def _attach_subprocess(self, job_id: str, proc: subprocess.Popen) -> bool:
        """Register ``proc`` for kill-on-cancel.

        Returns True when a cancel was already requested; the caller
        should immediately raise :class:`JobCancelled` so the worker
        unwinds before doing more work. In that case the process is
        terminated here so it doesn't leak.
        """
        with self._lock:
            j = self._jobs.get(job_id)
            if j is None or j.cancel_requested:
                already_cancelled = True
            else:
                self._subprocs[job_id] = proc
                already_cancelled = False
        if already_cancelled and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        return already_cancelled

    def _detach_subprocess(self, job_id: str) -> None:
        with self._lock:
            self._subprocs.pop(job_id, None)

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
