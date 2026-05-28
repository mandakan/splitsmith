"""Postgres-backed :class:`JobBackend` (doc 04, Tier 2 of doc 10).

Hosted-mode counterpart to :class:`splitsmith.ui.jobs.JobRegistry`'s
in-memory dict + thread pool. The persistence layer is Postgres; the
dispatch layer is still an in-process :class:`ThreadPoolExecutor`
inside the API server. Multi-machine workers via Procrastinate / arq
+ a closure-free ``submit(task_name, args)`` shape land in a follow-up
PR -- this one just makes job state survive a server restart, which is
the gate for the docker-compose smoke test.

**Restart hygiene:** at construction the backend sweeps any
``compute_jobs`` rows for this user that were ``pending`` or
``running`` and flips them to ``failed`` with an explanatory error
message. The in-process executor that was supposed to drive them is
gone; the SPA would otherwise see them stuck in ``pending`` forever.

**Multi-tenant:** every query filters by
``ComputeJobRow.user_id == self._user_id`` (see the
``multitenant-table-invariants`` memory entry). Tests in
``test_postgres_job_backend.py`` guard the boundary.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import subprocess
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

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

    Persistence-only for now: ``submit`` writes a PENDING row and
    schedules the callable on an in-process :class:`ThreadPoolExecutor`
    the same way :class:`JobRegistry` does. The DB-touching methods on
    the Protocol (``submit`` / ``get`` / ``list`` / ``cancel`` /
    ``acknowledge`` / ``acknowledge_all_failures`` / ``find_active``)
    are async; the lifecycle methods stay sync because they're
    process-local concepts (a thread pool, a drain flag).
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        user_id: str,
        max_concurrent: int = 2,
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
        # Populated by ``register_job_bodies(state)`` in ``create_app``,
        # same as the local :class:`JobRegistry`. The hosted worker
        # process holds its own identically-populated registry.
        self.bodies = JobBodyRegistry()

        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrent,
            thread_name_prefix="splitsmith-pgjob",
        )
        self._lock = threading.RLock()
        self._subprocs: dict[str, subprocess.Popen] = {}
        self._shutting_down = False
        self._drained = threading.Event()
        # In-process count of jobs we're currently driving on the
        # executor. Does NOT include rows other API processes may be
        # working on -- this is purely local-process bookkeeping for
        # the drain primitive.
        self._inflight = 0

        # Restart hygiene: any rows still marked PENDING or RUNNING
        # for this user belong to a previous server process that's
        # gone. Mark them FAILED so the SPA doesn't see ghosts.
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
        body = self.bodies.get(kind)
        call_args = args or {}
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

        # Capture ContextVars + dispatch on the executor. Matches
        # :class:`JobRegistry`'s behaviour so workers that depend on
        # request-scoped state still see it (Tier 1 step 3 of doc 10).
        captured_ctx = contextvars.copy_context()

        def _ctx_fn(handle: JobHandle) -> None:
            captured_ctx.run(lambda: body(handle, **call_args))

        with self._lock:
            self._inflight += 1
        self._executor.submit(self._run, job_id, _ctx_fn)
        return snapshot

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
            if row_snapshot.status == JobStatus.CANCELLED.value:
                # cancel() raced ahead of the worker; nothing to do.
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
