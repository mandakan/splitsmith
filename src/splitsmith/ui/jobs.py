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
- Submitted jobs are gated through an internal pending list keyed by
  :data:`JOB_PRIORITY` (lower number wins). The registry only hands a
  job to the executor when a worker slot frees up, so a high-priority
  ``detect_beep`` submitted behind a queue of ``shot_detect`` runs as
  soon as one of the slow jobs finishes -- no preemption, but no
  head-of-line blocking either. Within a priority tier, FIFO.
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

import contextvars
import logging
import subprocess
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel

logger = logging.getLogger(__name__)


JOB_PRIORITY: dict[str, int] = {
    # User-facing ingest path -- responsive when a new video lands.
    "detect_beep": 0,
    "trim": 1,
    # Slow ensemble; happily yields its slot to incoming beep/trim work.
    "shot_detect": 2,
    # Background prefetch of slim ONNX artifacts on first UI launch.
    # Lower than DEFAULT so any user-initiated work that arrives while
    # the download is still queued jumps ahead.
    "model_download": 9,
}
DEFAULT_JOB_PRIORITY: int = 3


def _priority_for_kind(kind: str) -> int:
    return JOB_PRIORITY.get(kind, DEFAULT_JOB_PRIORITY)


class _Unset:
    """Sentinel for ``find_active``'s optional ``video_id`` filter.

    ``None`` is a valid match (stage-level jobs have ``video_id=None``); we
    need a third state for "caller didn't pass it" so legacy primary-only
    dedupe queries stay role-agnostic.
    """


_UNSET = _Unset()


# A job body performs one job kind. Signature: ``body(handle, **args)``.
# ``args`` is whatever the submit callsite passed and MUST be JSON-
# serialisable so the hosted transport can ship it to a worker process.
JobBody = Callable[..., None]


class UnknownJobKindError(KeyError):
    """Raised by :meth:`JobBodyRegistry.get` for an unregistered kind.

    This is a programming error (a ``submit(kind=...)`` whose body was
    never registered in ``register_job_bodies``), not a user-facing
    condition -- it should surface loudly in development, never in prod.
    """


class JobBodyRegistry:
    """Maps a job ``kind`` to the callable that performs it.

    The unification point of the worker-fleet dispatch (doc 04): both the
    local in-process :class:`JobRegistry` and the hosted
    ``PostgresJobBackend`` resolve the body to run from this same map, so
    there is one dispatch shape (``kind`` + serialisable ``args``) and no
    closure-vs-task_name divergence between desktop and hosted.

    ``register_job_bodies(state)`` in ``server.py`` populates one of these
    against a process's :class:`AppState`; the hosted worker bootstrap
    populates an identical one against its own hosted state. The bodies
    close over that ``state`` -- which is why each process registers its
    own -- but every callsite and the wire format stay state-agnostic.
    """

    def __init__(self) -> None:
        self._bodies: dict[str, JobBody] = {}

    def register(self, kind: str, body: JobBody) -> None:
        self._bodies[kind] = body

    def get(self, kind: str) -> JobBody:
        try:
            return self._bodies[kind]
        except KeyError:
            raise UnknownJobKindError(
                f"no body registered for job kind {kind!r}; "
                "register it in splitsmith.ui.server.register_job_bodies"
            ) from None

    def __contains__(self, kind: str) -> bool:
        return kind in self._bodies


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


class ShutdownInProgressError(RuntimeError):
    """Raised by :meth:`JobRegistry.submit` after :meth:`begin_shutdown`.

    The HTTP layer maps this to a ``503 shutting_down`` so the desktop
    shell can tell the user "wait for current work to finish, then
    relaunch" instead of silently dropping new submissions.
    """


class Job(BaseModel):
    """Wire shape returned by ``/api/jobs/{id}``."""

    id: str
    kind: str  # "detect_beep" | "trim" | etc; used for SPA copy + filtering
    stage_number: int | None = None
    # Targets a specific ``StageVideo`` when the operation is per-video
    # (multi-cam beep detect / trim). ``None`` for stage-level jobs that
    # are intrinsically primary-bound (shot_detect, export). The SPA
    # disambiguates concurrent per-camera jobs in JobsPanel by this id.
    video_id: str | None = None
    status: JobStatus
    progress: float | None = None  # 0..1 when known
    message: str | None = None  # human-readable phase, e.g. "Extracting audio..."
    error: str | None = None  # populated only on FAILED
    # True after the SPA POSTed /api/jobs/{id}/cancel. The worker observes
    # this between phases and bails out cooperatively, ending the job in
    # status=CANCELLED. The flag stays True on the terminal snapshot so
    # the UI can label the row "Cancelled by user" instead of "Aborted".
    cancel_requested: bool = False
    # True after the SPA POSTed /api/jobs/{id}/acknowledge (issue #73).
    # The flag is meaningful only on FAILED jobs: the JobsPanel's badge
    # counts only failures with acknowledged=False, and retention prefers
    # evicting acknowledged failures so the user's dismissed errors roll
    # off faster than the ones they haven't seen yet.
    acknowledged: bool = False
    # Optional structured result payload set by the worker via
    # :meth:`JobHandle.set_result`. Reserved for jobs whose successful
    # output is meaningful to the SPA (e.g. match-export emits the FCPXML
    # path, total duration, and per-stage anomalies). The schema is per-
    # kind: the SPA branches on ``Job.kind`` to interpret the dict. Stays
    # ``None`` for jobs that signal success solely by writing files.
    result: dict[str, Any] | None = None
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

    def set_result(self, payload: dict[str, Any]) -> None:
        """Stash a structured result on the Job snapshot.

        The SPA reads ``Job.result`` after the job succeeds; the schema is
        per-kind. Workers that complete by writing files (trim, export,
        shot-detect) typically don't need this.
        """
        self._registry._patch(self._job_id, result=payload)

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


class JobBackend(Protocol):
    """The slice of :class:`JobRegistry` that handlers and the SPA
    actually depend on.

    Tier 2 of doc 10 (singleton elimination): in-memory job state
    survives only as long as the process. The hosted-mode backend
    (:class:`splitsmith.db.PostgresJobBackend`) persists jobs to
    ``compute_jobs`` (doc 04) and runs work on an in-process executor
    for now -- multi-machine workers via Procrastinate/arq are a
    follow-up. Both impls present the same surface to handlers so
    we don't fork the call sites.

    **Sync vs async split:**

    The DB-touching methods (``submit`` / ``get`` / ``list`` /
    ``cancel`` / ``acknowledge`` / ``acknowledge_all_failures`` /
    ``find_active``) are async so the hosted backend can issue
    real async DB queries without sync-over-async into the FastAPI
    event loop. The lifecycle methods (``is_shutting_down`` /
    ``active_count`` / ``begin_shutdown`` / ``wait_for_drain``)
    stay sync because they're local-process concepts: hosted-mode
    boots and shuts a thread pool the same way the local
    :class:`JobRegistry` does, with no DB round-trip in the hot
    path. Keeping those sync also leaves the boot/shutdown helpers
    in ``server.py`` and ``embedded.py`` untouched.
    """

    # The kind->body map this backend dispatches through. Populated by
    # ``register_job_bodies(state)`` against this process's AppState.
    bodies: JobBodyRegistry

    @property
    def is_shutting_down(self) -> bool: ...

    def active_count(self) -> int: ...

    def begin_shutdown(self) -> None: ...

    def wait_for_drain(self, timeout_s: float) -> bool: ...

    async def submit(
        self,
        *,
        kind: str,
        args: dict[str, Any] | None = None,
        stage_number: int | None = None,
        video_id: str | None = None,
    ) -> Job: ...

    async def get(self, job_id: str) -> Job | None: ...

    async def list(self) -> list[Job]: ...

    async def cancel(self, job_id: str) -> Job | None: ...

    async def acknowledge(self, job_id: str) -> Job | None: ...

    async def acknowledge_all_failures(self) -> list[Job]: ...

    async def find_active(
        self,
        *,
        kind: str | None = None,
        stage_number: int | None = None,
        video_id: str | None = None,
    ) -> Job | None: ...


class JobRegistry:
    """Thread-safe in-memory job backend.

    Implements :class:`JobBackend` plus local-only orchestration
    (the in-process ``ThreadPoolExecutor``, ``begin_shutdown`` /
    ``wait_for_drain`` for clean uvicorn exit, subprocess
    attachment for cooperative ffmpeg cancellation).

    The hosted-mode alternative (Tier 2 of doc 10) will persist
    jobs to ``compute_jobs`` and dispatch via arq; it satisfies
    :class:`JobBackend` but skips the executor + drain machinery
    because workers live in separate processes.
    """

    def __init__(self, *, max_concurrent: int = 2, retain_recent: int = 50) -> None:
        self.bodies = JobBodyRegistry()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()
        self._max_concurrent = max_concurrent
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrent,
            thread_name_prefix="splitsmith-job",
        )
        self._retain = retain_recent
        # Jobs awaiting dispatch. We keep this list ourselves rather than
        # leaning on the executor's internal queue because the executor
        # is FIFO-only; the dispatcher picks the highest-priority entry
        # (lowest ``JOB_PRIORITY`` value) whenever a worker slot frees up.
        self._pending: list[tuple[str, Callable[[JobHandle], None]]] = []
        self._running_count = 0
        # Registered subprocesses keyed by job_id. The trim worker spawns
        # ffmpeg via Popen and registers it via JobHandle.attach_subprocess
        # so we can terminate it on cancel even when the worker is blocked
        # on proc.wait().
        self._subprocs: dict[str, subprocess.Popen] = {}
        # Set by :meth:`begin_shutdown`. Once True, ``submit`` raises
        # :class:`ShutdownInProgressError` and ``wait_for_drain`` waits
        # for the active set to clear before the embedded entrypoint
        # stops uvicorn. Idempotent: a second call is a no-op.
        self._shutting_down = False
        self._drained = threading.Event()

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down

    def active_count(self) -> int:
        """Count of PENDING or RUNNING jobs at this instant."""
        with self._lock:
            return self._active_count_locked()

    def _active_count_locked(self) -> int:
        return sum(1 for j in self._jobs.values() if j.status in (JobStatus.PENDING, JobStatus.RUNNING))

    def _signal_drain_if_complete_locked(self) -> None:
        """Set the drain event when shutting down and the active set is empty."""
        if self._shutting_down and self._active_count_locked() == 0:
            self._drained.set()

    def begin_shutdown(self) -> None:
        """Reject new submissions; flip the drain event when active is 0.

        Idempotent. After the call, :meth:`submit` raises
        :class:`ShutdownInProgressError`; :meth:`wait_for_drain` returns
        once active drops to zero (or its timeout elapses).
        """
        with self._lock:
            if self._shutting_down:
                return
            self._shutting_down = True
            self._signal_drain_if_complete_locked()

    def wait_for_drain(self, timeout_s: float) -> bool:
        """Block until active_count is 0 or ``timeout_s`` elapses.

        Returns True if the drain completed, False on timeout. Safe to
        call before :meth:`begin_shutdown` -- if there's no active work
        it returns immediately.
        """
        if self.active_count() == 0:
            return True
        return self._drained.wait(timeout=timeout_s)

    async def submit(
        self,
        *,
        kind: str,
        args: dict[str, Any] | None = None,
        stage_number: int | None = None,
        video_id: str | None = None,
    ) -> Job:
        """Schedule the body registered for ``kind`` to run on the thread
        pool, called as ``body(handle, **args)``.

        Returns the job snapshot (status=PENDING) immediately so the HTTP
        handler can hand the id back to the caller without blocking.

        Raises :class:`ShutdownInProgressError` if :meth:`begin_shutdown`
        has been called -- the HTTP layer maps this to a 503. Raises
        :class:`UnknownJobKindError` if no body is registered for ``kind``.

        The submitting HTTP request's ``contextvars`` (notably
        ``current_match_root`` / ``current_match_id`` set by the
        ``/api/matches/{match_id}/`` alias middleware) are captured
        here and replayed inside the worker thread. Without this the
        ContextVars are unset on the executor thread and any
        ``state.shooter_root(...)`` call inside the worker 409s
        ``no_project``. See Tier 1 step 3 of doc 10. (The hosted backend
        instead reconstructs the match context from the ``args`` it ships
        to the worker process, which has no inherited contextvars.)
        """
        if self._shutting_down:
            raise ShutdownInProgressError("server is shutting down; no new jobs accepted")
        body = self.bodies.get(kind)
        call_args = args or {}
        now = datetime.now(UTC)
        job = Job(
            id=uuid.uuid4().hex,
            kind=kind,
            stage_number=stage_number,
            video_id=video_id,
            status=JobStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        captured_ctx = contextvars.copy_context()

        def _ctx_fn(handle: JobHandle) -> None:
            captured_ctx.run(lambda: body(handle, **call_args))

        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._pending.append((job.id, _ctx_fn))
            self._trim_retained_locked()
            # Snapshot before releasing the lock + dispatching to the
            # executor; otherwise the worker may have already flipped
            # status to RUNNING by the time we copy.
            snapshot = job.model_copy(deep=True)
            self._dispatch_locked()
        return snapshot

    async def get(self, job_id: str) -> Job | None:
        with self._lock:
            j = self._jobs.get(job_id)
            return j.model_copy(deep=True) if j is not None else None

    async def list(self) -> list[Job]:
        """Snapshot of all retained jobs in submission order."""
        with self._lock:
            return [self._jobs[jid].model_copy(deep=True) for jid in self._order if jid in self._jobs]

    async def cancel(self, job_id: str) -> Job | None:
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
            # If the job is still queued (never handed to the executor),
            # transition it to CANCELLED right here. Otherwise it would
            # sit in ``_pending`` as PENDING forever -- the worker that
            # would have observed the cancel never gets scheduled.
            for i, (jid, _fn) in enumerate(self._pending):
                if jid == job_id:
                    del self._pending[i]
                    j.status = JobStatus.CANCELLED
                    j.finished_at = datetime.now(UTC)
                    j.updated_at = j.finished_at
                    self._trim_retained_locked()
                    break
            self._signal_drain_if_complete_locked()
            snapshot = j.model_copy(deep=True)
        # Kill outside the lock so a slow ``terminate()`` (proc held a
        # held resource etc.) can't stall other registry callers.
        if proc_to_kill is not None and proc_to_kill.poll() is None:
            try:
                proc_to_kill.terminate()
            except OSError:
                logger.warning("cancel: terminate() failed for job %s", job_id, exc_info=True)
        return snapshot

    async def acknowledge(self, job_id: str) -> Job | None:
        """Mark a failed job as seen by the user (issue #73).

        No-op for non-FAILED jobs and for failures already acknowledged.
        Returns the post-ack snapshot, or ``None`` if ``job_id`` is
        unknown. Acknowledgment lowers the job's eviction priority, so
        a dismissed failure rolls off the retained list before the
        unacknowledged ones the user still hasn't seen.
        """
        with self._lock:
            j = self._jobs.get(job_id)
            if j is None:
                return None
            if j.status == JobStatus.FAILED and not j.acknowledged:
                j.acknowledged = True
                j.updated_at = datetime.now(UTC)
            return j.model_copy(deep=True)

    async def acknowledge_all_failures(self) -> list[Job]:
        """Mark every currently-unacknowledged FAILED job as seen.

        Returns the snapshots that actually changed (already-acknowledged
        failures and non-failed jobs are skipped). The SPA uses the
        return value to decide whether to flash a "Dismissed N failures"
        toast.
        """
        now = datetime.now(UTC)
        affected: list[Job] = []
        with self._lock:
            for jid in self._order:
                j = self._jobs.get(jid)
                if j is None:
                    continue
                if j.status == JobStatus.FAILED and not j.acknowledged:
                    j.acknowledged = True
                    j.updated_at = now
                    affected.append(j.model_copy(deep=True))
        return affected

    async def find_active(
        self,
        *,
        kind: str,
        stage_number: int | None = None,
        video_id: str | None | _Unset = _UNSET,
    ) -> Job | None:
        """Return the first PENDING/RUNNING job matching the keys.

        Used to dedupe submissions: a second click of "Trim now" while the
        first job is still running should adopt the existing job instead
        of spawning a parallel ffmpeg that races on the same output file.

        ``video_id`` is matched only when explicitly passed; legacy callers
        that don't know about per-video jobs keep their previous behaviour
        of "match on (kind, stage_number)" so a stage-level shot_detect
        dedupe still works against a video_id-less submission.
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
                if not isinstance(video_id, _Unset) and j.video_id != video_id:
                    continue
                return j.model_copy(deep=True)
            return None

    # ------------------------------------------------------------------
    # Internal -- worker glue. These run on the executor thread.
    # ------------------------------------------------------------------

    def _run(self, job_id: str, fn: Callable[[JobHandle], None]) -> None:
        try:
            with self._lock:
                j = self._jobs.get(job_id)
                if j is None:
                    return
                # If the job was cancelled between dispatch and the worker
                # picking it up, skip straight to CANCELLED. Cancels for
                # still-queued jobs are now resolved in ``cancel()``, but
                # this branch still covers the race where cancel() fires
                # after dispatch and before _run acquires the lock.
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
        finally:
            with self._lock:
                self._running_count = max(0, self._running_count - 1)
                self._dispatch_locked()
                self._signal_drain_if_complete_locked()

    def _dispatch_locked(self) -> None:
        """Hand the highest-priority pending job to the executor.

        Called under ``self._lock``. Drains pending entries up to the
        worker-slot ceiling; ties within a priority tier go FIFO by
        original submission order.
        """
        while self._running_count < self._max_concurrent and self._pending:
            best_idx = 0
            best_priority = _priority_for_kind(self._jobs[self._pending[0][0]].kind)
            for i in range(1, len(self._pending)):
                kind = self._jobs[self._pending[i][0]].kind
                p = _priority_for_kind(kind)
                if p < best_priority:
                    best_idx = i
                    best_priority = p
            job_id, fn = self._pending.pop(best_idx)
            self._running_count += 1
            self._executor.submit(self._run, job_id, fn)

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
        """Evict finished jobs once the retention limit is hit.

        Active jobs (PENDING / RUNNING) are never evicted. Called under
        ``self._lock``; do not acquire it again.

        Eviction priority (issue #73): succeeded jobs go first, then
        acknowledged failures, then unacknowledged failures last. Within
        each tier we evict oldest first. This keeps an unseen failure
        visible in the registry even when a flurry of trim/beep
        successes would otherwise have pushed it off the tail.
        """
        finished = [
            jid
            for jid in self._order
            if jid in self._jobs and self._jobs[jid].status in (JobStatus.SUCCEEDED, JobStatus.FAILED)
        ]
        excess = len(finished) - self._retain
        if excess <= 0:
            return

        def _eviction_priority(jid: str) -> int:
            j = self._jobs[jid]
            if j.status == JobStatus.SUCCEEDED:
                return 0
            if j.status == JobStatus.FAILED and j.acknowledged:
                return 1
            return 2  # unacknowledged failure -- protect

        order_index = {jid: i for i, jid in enumerate(self._order)}
        ranked = sorted(finished, key=lambda jid: (_eviction_priority(jid), order_index[jid]))
        for jid in ranked[:excess]:
            self._jobs.pop(jid, None)
            self._order.remove(jid)
