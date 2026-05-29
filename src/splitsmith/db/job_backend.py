"""Postgres-backed :class:`JobBackend` (doc 04, Tier 2 of doc 10).

Hosted-mode counterpart to :class:`splitsmith.ui.jobs.JobRegistry`'s
in-memory dict + thread pool. Persistence is Postgres (``compute_jobs``);
dispatch is out-of-process via Procrastinate (PR-gamma). :meth:`submit`
writes a ``pending`` row and hands the job to the injected
:data:`JobDeferrer`, which enqueues it on the per-tenant Procrastinate
queue. A separate ``splitsmith worker`` process pops it and calls
:meth:`run_job`, which drives the same ``kind`` -> body callable the
local :class:`JobRegistry` uses (registered via
``register_job_bodies(state)``) and writes the terminal status back to
the row the API created. The SPA polls ``/api/jobs/{id}`` against that
row throughout.

The body closures only carry JSON-serialisable ``args`` across the
process boundary -- a closure can't be pickled onto a queue, which is
why PR-gamma reshaped ``submit(fn=...)`` to ``submit(kind=, args=)``.

**Restart hygiene:** at construction (when ``sweep_on_boot``) the
backend sweeps this user's ``pending``/``running`` rows to ``failed``.
This is correct for the API process on restart; the worker passes
``sweep_on_boot=False`` so it never fails jobs it is about to run.

**Multi-tenant:** every query filters by
``ComputeJobRow.user_id == self._user_id`` (see the
``multitenant-table-invariants`` memory entry). Tests in
``test_postgres_job_backend.py`` guard the boundary.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..ui.jobs import (
    Job,
    JobBodyRegistry,
    JobCancelled,
    JobHandle,
    JobStatus,
    ShutdownInProgressError,
)
from .models import ComputeJobRow

logger = logging.getLogger(__name__)

# Enqueue side of the split: ``submit`` calls this with a fully
# JSON-serialisable payload; the implementation (see
# ``splitsmith.queue.make_deferrer``) defers a ``run_compute_job``
# Procrastinate task onto ``queue_name_for_user(user_id)``. Injected so
# the backend stays free of any ``procrastinate`` import and so tests can
# pass a fake that records or inline-runs the payload.
JobDeferrer = Callable[..., Awaitable[None]]


def _to_wire_args(args: dict[str, Any]) -> dict[str, Any]:
    """Project ``args`` to a JSON-serialisable dict for the queue.

    Pydantic models (the ``req`` carried by ``export`` / ``match_export``)
    become ``model_dump(mode="json")`` dicts; the worker rehydrates them
    to the typed request before calling the body. Everything else
    (slug, stage_number, flags) is already JSON-native and passes
    through untouched.
    """
    return {k: (v.model_dump(mode="json") if isinstance(v, BaseModel) else v) for k, v in args.items()}


_ROW_TO_JOB_FIELDS = (
    "id",
    "kind",
    "stage_number",
    "video_id",
    "status",
    "progress",
    "message",
    "error",
    "cancel_requested",
    "acknowledged",
    "result",
    "created_at",
    "updated_at",
    "started_at",
    "finished_at",
)


def _row_to_job(row: ComputeJobRow) -> Job:
    """Project an ORM row to the wire-shape :class:`Job` pydantic model."""
    return Job(**{name: getattr(row, name) for name in _ROW_TO_JOB_FIELDS})


class PostgresJobBackend:
    """Per-user :class:`JobBackend` backed by ``compute_jobs``.

    The API process constructs one with a ``deferrer`` and uses
    :meth:`submit` to enqueue. The worker process constructs one with
    ``sweep_on_boot=False`` and uses :meth:`run_job` to execute. Both
    share the persistence + :class:`JobHandle` bridge methods; the
    DB-touching Protocol methods (``submit`` / ``get`` / ``list`` /
    ``cancel`` / ``acknowledge`` / ``acknowledge_all_failures`` /
    ``find_active``) are async; the lifecycle methods stay sync because
    they're process-local concepts (a drain flag, the subprocess map).
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        user_id: str,
        deferrer: JobDeferrer | None = None,
        sweep_on_boot: bool = True,
        bodies: JobBodyRegistry | None = None,
    ) -> None:
        # Defence-in-depth: see :class:`PostgresRecentProjectsStore`
        # for the same fail-loud-on-empty-user_id rationale.
        if not isinstance(user_id, str) or not user_id:
            raise ValueError(
                "PostgresJobBackend requires a non-empty user_id; "
                f"got {user_id!r}. The auth layer must resolve a real "
                "user before constructing the per-request backend."
            )
        self._session_factory = session_factory
        self._user_id = user_id
        # ``submit`` hands the enqueue to this; ``None`` means the backend
        # can persist + execute (``run_job``) but not enqueue. Calling
        # ``submit`` without one is a programming error (surfaced there).
        self._deferrer = deferrer
        # Populated by ``register_job_bodies(state)`` in ``create_app``
        # (API) and the worker bootstrap, same as the local
        # :class:`JobRegistry`. The kind -> body mapping is a process-level
        # constant, so when the hosted wiring builds a fresh backend per
        # request / per job it injects one shared registry rather than
        # re-registering bodies on every instance. ``None`` (the default,
        # used by tests + the boot-time backend) creates a private one.
        self.bodies = bodies if bodies is not None else JobBodyRegistry()

        self._lock = threading.RLock()
        self._subprocs: dict[str, subprocess.Popen] = {}
        self._shutting_down = False
        self._drained = threading.Event()
        # In-process count of jobs this process is currently driving in
        # ``run_job``. Zero in the API process (it only enqueues); the
        # drain primitive is meaningful in the worker.
        self._inflight = 0

        # Restart hygiene: any rows still PENDING/RUNNING for this user
        # belong to a previous API process that's gone. The worker
        # disables this -- it must not fail jobs queued for it to run.
        if sweep_on_boot:
            asyncio.run(self._sweep_stuck_jobs_on_boot())

    # ------------------------------------------------------------------
    # Lifecycle (sync) -- match JobRegistry semantics
    # ------------------------------------------------------------------

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down

    def active_count(self) -> int:
        """In-process active count. Mirrors :class:`JobRegistry` -- a
        meaningful drain signal here means "this process has no more
        worker threads in flight", not "no rows are pending anywhere
        in the cluster". The latter is meaningless during a rolling
        restart anyway."""
        with self._lock:
            return self._inflight

    def begin_shutdown(self) -> None:
        with self._lock:
            if self._shutting_down:
                return
            self._shutting_down = True
            self._signal_drain_if_complete_locked()

    def wait_for_drain(self, timeout_s: float) -> bool:
        if self.active_count() == 0:
            return True
        return self._drained.wait(timeout=timeout_s)

    def _signal_drain_if_complete_locked(self) -> None:
        if self._shutting_down and self._inflight == 0:
            self._drained.set()

    # ------------------------------------------------------------------
    # DB-touching methods (async, per JobBackend Protocol)
    # ------------------------------------------------------------------

    async def submit(
        self,
        *,
        kind: str,
        args: dict[str, Any] | None = None,
        stage_number: int | None = None,
        video_id: str | None = None,
    ) -> Job:
        if self._shutting_down:
            raise ShutdownInProgressError("server is shutting down; no new jobs accepted")
        if self._deferrer is None:
            raise RuntimeError(
                "PostgresJobBackend.submit needs a deferrer; this backend was "
                "built execute-only (worker side). Enqueue from the API process."
            )
        # Resolve the body now so an unknown kind fails fast at the
        # caller -- before a row is written or a job is enqueued.
        self.bodies.get(kind)
        # Match identity rides the queue so the worker can re-set the
        # ``current_match_*`` ContextVars before running the body.
        # ``model_download`` (submitted at startup) carries no match.
        from ..ui.server import current_match_id

        match_id = current_match_id.get()
        now = datetime.now(UTC)
        job_id = uuid.uuid4().hex
        row = ComputeJobRow(
            id=job_id,
            user_id=self._user_id,
            kind=kind,
            stage_number=stage_number,
            video_id=video_id,
            status=JobStatus.PENDING.value,
            cancel_requested=False,
            acknowledged=False,
            created_at=now,
            updated_at=now,
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            snapshot = _row_to_job(row)

        await self._deferrer(
            job_id=job_id,
            user_id=self._user_id,
            kind=kind,
            args=_to_wire_args(args or {}),
            match_id=match_id,
        )
        return snapshot

    async def run_job(
        self,
        *,
        job_id: str,
        kind: str,
        args: dict[str, Any] | None = None,
        before_body: Callable[[], None] | None = None,
    ) -> None:
        """Execute a previously-:meth:`submit`-ted job. Worker entry point.

        The worker's Procrastinate task calls this. Looks up the ``kind``
        -> body callable and drives it through the same persistence path
        the in-process executor used to. Offloaded to a thread because
        :meth:`_run` and the :class:`JobHandle` bridge use ``asyncio.run``
        for their DB writes, which can't run inside the worker's
        already-running event loop; ``to_thread`` also copies the current
        context so the ContextVars propagate.

        ``before_body`` runs on the worker thread immediately before the
        body, *inside* :meth:`_run`'s failure capture. The worker uses it
        to re-set the ``current_match_*`` ContextVars from the queued
        ``match_id``; routing it through here (rather than the calling
        task) means a failure to resolve the match surfaces as a FAILED
        job row with a legible message instead of an escaped exception
        that strands the row at PENDING.
        """
        body = self.bodies.get(kind)
        call_args = args or {}

        def _ctx_fn(handle: JobHandle) -> None:
            if before_body is not None:
                before_body()
            body(handle, **call_args)

        with self._lock:
            self._inflight += 1
        await asyncio.to_thread(self._run, job_id, _ctx_fn)

    async def get(self, job_id: str) -> Job | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(ComputeJobRow).where(
                        ComputeJobRow.id == job_id,
                        ComputeJobRow.user_id == self._user_id,
                    )
                )
            ).scalar_one_or_none()
        return _row_to_job(row) if row is not None else None

    async def list(self) -> list[Job]:
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(ComputeJobRow)
                        .where(ComputeJobRow.user_id == self._user_id)
                        .order_by(ComputeJobRow.created_at)
                    )
                )
                .scalars()
                .all()
            )
        return [_row_to_job(r) for r in rows]

    async def cancel(self, job_id: str) -> Job | None:
        proc_to_kill: subprocess.Popen | None = None
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(ComputeJobRow).where(
                        ComputeJobRow.id == job_id,
                        ComputeJobRow.user_id == self._user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            if row.status in (
                JobStatus.SUCCEEDED.value,
                JobStatus.FAILED.value,
                JobStatus.CANCELLED.value,
            ):
                return _row_to_job(row)
            now = datetime.now(UTC)
            row.cancel_requested = True
            row.updated_at = now
            # If the worker hasn't picked it up yet (still PENDING in
            # this process's view), flip straight to CANCELLED. A
            # RUNNING row's worker observes ``cancel_requested`` and
            # bails at its next phase boundary.
            if row.status == JobStatus.PENDING.value:
                row.status = JobStatus.CANCELLED.value
                row.finished_at = now
            await session.commit()
            snapshot = _row_to_job(row)

        with self._lock:
            proc_to_kill = self._subprocs.pop(job_id, None)
        if proc_to_kill is not None and proc_to_kill.poll() is None:
            try:
                proc_to_kill.terminate()
            except OSError:
                logger.warning("cancel: terminate() failed for job %s", job_id, exc_info=True)
        return snapshot

    async def acknowledge(self, job_id: str) -> Job | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(ComputeJobRow).where(
                        ComputeJobRow.id == job_id,
                        ComputeJobRow.user_id == self._user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            if row.status == JobStatus.FAILED.value and not row.acknowledged:
                row.acknowledged = True
                row.updated_at = datetime.now(UTC)
                await session.commit()
            return _row_to_job(row)

    async def acknowledge_all_failures(self) -> list[Job]:
        affected: list[Job] = []
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(ComputeJobRow).where(
                            ComputeJobRow.user_id == self._user_id,
                            ComputeJobRow.status == JobStatus.FAILED.value,
                            ComputeJobRow.acknowledged.is_(False),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                row.acknowledged = True
                row.updated_at = now
                affected.append(_row_to_job(row))
            if rows:
                await session.commit()
        return affected

    async def find_active(
        self,
        *,
        kind: str,
        stage_number: int | None = None,
        video_id: Any = None,
    ) -> Job | None:
        # ``video_id`` defaults to ``None``: callers that omit it want
        # stage-level dedupe, and the SQL filter is only added when an
        # explicit value is passed. (The :class:`JobRegistry` Protocol
        # form uses an _UNSET sentinel for the same disambiguation;
        # here ``None`` is sufficient because the row's ``video_id``
        # column is itself nullable and callers don't currently rely
        # on "match rows where video_id IS NULL specifically".)
        async with self._session_factory() as session:
            stmt = (
                select(ComputeJobRow)
                .where(
                    ComputeJobRow.user_id == self._user_id,
                    ComputeJobRow.kind == kind,
                    ComputeJobRow.stage_number == stage_number,
                    ComputeJobRow.status.in_([JobStatus.PENDING.value, JobStatus.RUNNING.value]),
                )
                .order_by(ComputeJobRow.created_at)
            )
            if video_id is not None:
                stmt = stmt.where(ComputeJobRow.video_id == video_id)
            row = (await session.execute(stmt)).scalars().first()
        return _row_to_job(row) if row is not None else None

    # ------------------------------------------------------------------
    # Internal worker glue
    # ------------------------------------------------------------------

    async def _sweep_stuck_jobs_on_boot(self) -> None:
        """Mark this user's PENDING/RUNNING rows as FAILED on startup.

        Any row in those states belongs to a previous API server
        process that's now gone -- the in-process executor that was
        supposed to drive it doesn't exist anymore. Surface as a
        FAILED with a clear message so the SPA can show what happened
        instead of polling forever.
        """
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            await session.execute(
                update(ComputeJobRow)
                .where(
                    ComputeJobRow.user_id == self._user_id,
                    ComputeJobRow.status.in_([JobStatus.PENDING.value, JobStatus.RUNNING.value]),
                )
                .values(
                    status=JobStatus.FAILED.value,
                    error="server restarted before this job finished",
                    updated_at=now,
                    finished_at=now,
                )
            )
            await session.commit()

    def _run(self, job_id: str, fn: Callable[[JobHandle], None]) -> None:
        """Worker entry on the executor thread. Pull the row up to
        RUNNING, drive ``fn``, then write the terminal status.

        Mirrors :meth:`JobRegistry._run` but persists through the
        ``compute_jobs`` table instead of the in-memory dict.
        """
        try:
            now = datetime.now(UTC)
            # Synchronous dispatch on this thread; bridge to async DB
            # with ``asyncio.run`` per call. The worker thread has no
            # event loop of its own.
            row_snapshot = asyncio.run(self._begin_run(job_id, now))
            if row_snapshot is None:
                return
            if row_snapshot.status in (
                JobStatus.CANCELLED.value,
                JobStatus.FAILED.value,
                JobStatus.SUCCEEDED.value,
            ):
                # cancel() (or a duplicate delivery) raced ahead of the
                # worker; the row is already terminal. Nothing to run.
                return

            handle = JobHandle(self, job_id)  # type: ignore[arg-type]
            try:
                fn(handle)
            except JobCancelled:
                asyncio.run(self._finalize_run(job_id, JobStatus.CANCELLED, error=None))
                return
            except Exception as exc:  # noqa: BLE001 -- surface as FAILED
                asyncio.run(self._finalize_run(job_id, JobStatus.FAILED, error=str(exc)))
                return
            asyncio.run(self._finalize_run(job_id, JobStatus.SUCCEEDED, error=None))
        finally:
            with self._lock:
                self._inflight = max(0, self._inflight - 1)
                self._subprocs.pop(job_id, None)
                self._signal_drain_if_complete_locked()

    async def _begin_run(self, job_id: str, now: datetime) -> Job | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(ComputeJobRow).where(
                        ComputeJobRow.id == job_id,
                        ComputeJobRow.user_id == self._user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            # Already terminal: a ``cancel()`` (or a duplicate delivery)
            # beat the worker to this row. Don't resurrect it to RUNNING
            # -- return the snapshot so ``_run`` can skip the body.
            if row.status in (
                JobStatus.CANCELLED.value,
                JobStatus.FAILED.value,
                JobStatus.SUCCEEDED.value,
            ):
                return _row_to_job(row)
            if row.cancel_requested and row.status == JobStatus.PENDING.value:
                row.status = JobStatus.CANCELLED.value
                row.finished_at = now
                row.updated_at = now
                await session.commit()
                return _row_to_job(row)
            row.status = JobStatus.RUNNING.value
            row.started_at = row.started_at or now
            row.updated_at = now
            await session.commit()
            return _row_to_job(row)

    async def _finalize_run(self, job_id: str, status: JobStatus, *, error: str | None) -> None:
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(ComputeJobRow).where(
                        ComputeJobRow.id == job_id,
                        ComputeJobRow.user_id == self._user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return
            row.status = status.value
            row.finished_at = now
            row.updated_at = now
            if status == JobStatus.SUCCEEDED:
                row.progress = 1.0
                row.error = None
            elif status == JobStatus.FAILED:
                row.error = error
            elif status == JobStatus.CANCELLED:
                row.error = None
            await session.commit()

    # ------------------------------------------------------------------
    # JobHandle bridge: workers call these via the handle. Implemented
    # synchronously because the handle lives on the worker thread.
    # ------------------------------------------------------------------

    def _patch(self, job_id: str, **kwargs) -> None:
        """Update a subset of fields (progress, message, result). Called
        from the worker thread via :class:`JobHandle`. Bridges to async
        DB via ``asyncio.run``."""
        asyncio.run(self._patch_async(job_id, kwargs))

    async def _patch_async(self, job_id: str, kwargs: dict) -> None:
        if not kwargs:
            return
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(ComputeJobRow).where(
                        ComputeJobRow.id == job_id,
                        ComputeJobRow.user_id == self._user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return
            for key, value in kwargs.items():
                if hasattr(row, key):
                    setattr(row, key, value)
            row.updated_at = now
            await session.commit()

    def _is_cancel_requested(self, job_id: str) -> bool:
        """Sync check called from the worker thread."""
        return asyncio.run(self._is_cancel_requested_async(job_id))

    async def _is_cancel_requested_async(self, job_id: str) -> bool:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(ComputeJobRow.cancel_requested).where(
                        ComputeJobRow.id == job_id,
                        ComputeJobRow.user_id == self._user_id,
                    )
                )
            ).scalar_one_or_none()
        return bool(row)

    def _attach_subprocess(self, job_id: str, proc: subprocess.Popen) -> bool:
        """Register ``proc`` under ``job_id``; return True if a cancel
        was already requested (worker should terminate immediately)."""
        with self._lock:
            self._subprocs[job_id] = proc
        if self._is_cancel_requested(job_id):
            with self._lock:
                self._subprocs.pop(job_id, None)
            try:
                proc.terminate()
            except OSError:
                pass
            return True
        return False

    def _detach_subprocess(self, job_id: str) -> None:
        with self._lock:
            self._subprocs.pop(job_id, None)
