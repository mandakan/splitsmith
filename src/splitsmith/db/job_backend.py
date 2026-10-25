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
import contextlib
import logging
import subprocess
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, not_, or_, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from .. import __version__
from ..access import Feature, FeatureRequiredError
from ..async_bridge import run_sync
from ..observability import PhaseTimer, WorkerProbe, capture_job_exception, emit_job_event
from ..ui.context import current_export_id
from ..ui.job_journal import rehydrate_args, to_wire_args
from ..ui.jobs import (
    Job,
    JobBodyRegistry,
    JobCancelled,
    JobHandle,
    JobNotRetryableError,
    JobPart,
    JobStatus,
    ShutdownInProgressError,
    UnknownJobKindError,
    export_is_active,
)
from .models import ComputeJobRow

logger = logging.getLogger(__name__)

# The SQLite stand-in for the advisory lock (``export_lock``), per user.
_SQLITE_EXPORT_LOCKS: dict[str, asyncio.Lock] = {}

# Enqueue side of the split: ``submit`` calls this with a fully
# JSON-serialisable payload; the implementation (see
# ``splitsmith.queue.make_deferrer``) defers a ``run_compute_job``
# Procrastinate task onto ``queue_name_for_user(user_id)``. Injected so
# the backend stays free of any ``procrastinate`` import and so tests can
# pass a fake that records or inline-runs the payload.
JobDeferrer = Callable[..., Awaitable[None]]


# Shared with the local crash-recovery journal (issue #665): one source
# of truth for how a Pydantic ``req`` is projected across a persistence
# boundary. The worker rehydrates via the matching
# ``job_journal.rehydrate_args`` before calling the body.
_to_wire_args = to_wire_args


_ROW_TO_JOB_FIELDS = (
    "id",
    "kind",
    "match_id",
    "stage_number",
    "shooter_slug",
    "video_id",
    "status",
    "progress",
    "message",
    "error",
    "cancel_requested",
    "acknowledged",
    "result",
    "timings",
    "export_id",
    "parts",
    "created_at",
    "updated_at",
    "started_at",
    "finished_at",
)


#: How many finished jobs beyond the active and unacknowledged-failed set
#: :meth:`PostgresJobBackend.list` returns (#1182). The SPA's poll reads
#: active jobs and unacknowledged failures from the list for everything it
#: shows, plus a short tail of terminal rows: the progress strip's "done"
#: count and the SyncCard's wait for its own job to turn terminal. Twenty
#: covers both and mirrors the local :class:`JobRegistry`'s retention in
#: spirit; results are read by id, never from the list.
RECENT_FINISHED_RETAINED = 20


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
        submit_allowed: Callable[[], Awaitable[bool]] | None = None,
        on_running_cancel: Callable[[], None] | None = None,
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
        # Account-feature backstop (spec 2026-10-03): when set, ``submit``
        # refuses unless it answers True. Hosted wiring always sets it;
        # ``None`` is tests and the local registry.
        self._submit_allowed = submit_allowed
        # Called (never awaited, must not raise) after a cancel is recorded
        # on a RUNNING row. Hosted wiring passes the worker dispatcher's
        # ``schedule``: when that row's worker is dead, only a drain's
        # stalled sweep can end it, and nothing else would start one.
        self._on_running_cancel = on_running_cancel

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
            run_sync(self._sweep_stuck_jobs_on_boot())

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
        shooter_slug: str | None = None,
        video_id: str | None = None,
        parts: list[JobPart] | None = None,
    ) -> Job:
        if self._shutting_down:
            raise ShutdownInProgressError("server is shutting down; no new jobs accepted")
        if self._submit_allowed is not None and not await self._submit_allowed():
            raise FeatureRequiredError(Feature.hosted_compute)
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
            args=_to_wire_args(args or {}),
            stage_number=stage_number,
            shooter_slug=shooter_slug,
            video_id=video_id,
            match_id=match_id,
            export_id=current_export_id.get(),
            parts=[part.model_dump(mode="json") for part in parts] if parts else None,
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
        :meth:`_run` and the :class:`JobHandle` bridge use ``run_sync``
        for their DB writes, whose fresh-loop fallback can't run inside
        the worker's already-running event loop; ``to_thread`` also
        copies the current context so the ContextVars propagate.

        ``before_body`` runs on the worker thread immediately before the
        body, *inside* :meth:`_run`'s failure capture. The worker uses it
        to re-set the ``current_match_*`` ContextVars from the queued
        ``match_id``; routing it through here (rather than the calling
        task) means a failure to resolve the match surfaces as a FAILED
        job row with a legible message instead of an escaped exception
        that strands the row at PENDING.

        The body is looked up inside that capture too (#1495): a worker
        whose image predates a job kind ends the row FAILED, saying so, and
        the next request queues a fresh job. Looked up before it, the
        ``UnknownJobKindError`` escaped, the row stayed PENDING, and every
        later request reused it through ``find_active`` and waited forever.
        """
        call_args = args or {}
        # The account check runs again here, not only at submit: a job
        # queued before a downgrade (or woken by the boot re-trigger) must
        # not run once the account has lost ``hosted_compute``. It fails
        # through ``_run`` like any body error, so the row ends FAILED with
        # the refusal as its message and the body never starts.
        refused = self._submit_allowed is not None and not await self._submit_allowed()
        # The export rides the row, not the queue payload: a job the body
        # submits (a chained upload, a grid stage) joins the same export.
        queued = await self.get(job_id)
        export_id = queued.export_id if queued is not None else None

        def _ctx_fn(handle: JobHandle) -> None:
            current_export_id.set(export_id)
            if refused:
                raise FeatureRequiredError(Feature.hosted_compute)
            try:
                body = self.bodies.get(kind)
            except UnknownJobKindError as exc:
                # A KeyError's message prints in quotes; the row's error is
                # what the jobs list and the logs show.
                raise RuntimeError(
                    f"this worker (splitsmith {__version__}) cannot run job kind {kind!r}: "
                    "it runs an image older than the server; update it"
                ) from exc
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
        """The jobs the poll needs, in ``created_at`` order (#1182).

        Every pending or running job and every unacknowledged failure,
        however old -- the strip, the settle detector, the failures sheet,
        acknowledge and retry read those -- plus the
        :data:`RECENT_FINISHED_RETAINED` most recently created of the rest.
        This used to return every job the user ever ran, which made the
        5-second poll grow without bound (62 KB per poll on production).
        Nothing is deleted: older rows stay in the table and are reachable
        by id through :meth:`get`. One edge: a job that sat pending while
        twenty newer jobs were created and finished leaves the list the
        moment it finishes instead of appearing as succeeded; the SPA's
        settle detectors treat active-to-missing as settled, so nothing
        hangs on it.
        """
        mine = ComputeJobRow.user_id == self._user_id
        must_show = or_(
            ComputeJobRow.status.in_((JobStatus.PENDING.value, JobStatus.RUNNING.value)),
            and_(ComputeJobRow.status == JobStatus.FAILED.value, ComputeJobRow.acknowledged.is_(False)),
        )
        recent_rest = (
            select(ComputeJobRow.id)
            .where(mine, not_(must_show))
            .order_by(ComputeJobRow.created_at.desc())
            .limit(RECENT_FINISHED_RETAINED)
            .scalar_subquery()
        )
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(ComputeJobRow)
                        .where(mine, or_(must_show, ComputeJobRow.id.in_(recent_rest)))
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

        if snapshot.status == JobStatus.RUNNING and self._on_running_cancel is not None:
            self._on_running_cancel()

        with self._lock:
            proc_to_kill = self._subprocs.pop(job_id, None)
        if proc_to_kill is not None and proc_to_kill.poll() is None:
            try:
                proc_to_kill.terminate()
            except OSError:
                logger.warning("cancel: terminate() failed for job %s", job_id, exc_info=True)
        return snapshot

    async def end_orphaned(self, job_id: str, *, error: str) -> Job | None:
        """End a job whose worker died mid-run (#1677); the worker's stalled sweep calls it.

        A row still PENDING or RUNNING ends CANCELLED when a cancel was
        requested (the dead worker can no longer honour it, which is why the
        cancel alone never cleared one), else FAILED with ``error``, so the
        failures sheet offers a retry. A row already terminal is left alone.
        Returns the row's snapshot, or None for an id this user doesn't own.
        """
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
            if row.status in (JobStatus.PENDING.value, JobStatus.RUNNING.value):
                now = datetime.now(UTC)
                if row.cancel_requested:
                    row.status = JobStatus.CANCELLED.value
                    row.error = None
                else:
                    row.status = JobStatus.FAILED.value
                    row.error = error
                row.finished_at = now
                row.updated_at = now
                await session.commit()
            return _row_to_job(row)

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

    async def retry(self, job_id: str) -> Job | None:
        """Re-enqueue a FAILED job as a new row, replaying its persisted args.

        ``acknowledged`` doubles as the atomic claim bit, mirroring
        :class:`~splitsmith.ui.jobs.JobRegistry`. A conditional UPDATE
        (status FAILED, acknowledged False) is the claim itself, so it is
        race-safe across processes, unlike the in-memory registry's
        thread lock: two API replicas racing the same job id can only have
        one of them see rowcount 1. The loser falls through to the
        read-only branch below to report why.

        Returns the NEW pending job. ``None`` for an id this user doesn't
        own. Raises JobNotRetryableError when the row is not FAILED, was
        already retried or dismissed, or predates the args column (args is
        NULL). The args check runs after the claim is won rather than
        before it, so it can't reintroduce the race a pre-read would; a
        row that fails that check stays acknowledged (the claim isn't
        refunded), matching the registry's post-claim failure behavior.

        The resubmit is bound to the ORIGINAL job's ``match_id`` (see
        :meth:`submit`), not whatever match is ambient on the retrying
        request - a job failed while viewing match A must not resubmit
        under match B just because that's the match the retry HTTP
        request happened to carry. ``current_match_id`` is set from the
        persisted ``row.match_id`` (``None`` included - that's correct for
        a legitimately match-less kind like ``model_download``) around the
        :meth:`submit` call so its ``current_match_id.get()`` read sees
        the right value regardless of ambient request context.
        """
        from ..ui.server import current_match_id

        now = datetime.now(UTC)
        async with self._session_factory() as session:
            claim = await session.execute(
                update(ComputeJobRow)
                .where(
                    ComputeJobRow.id == job_id,
                    ComputeJobRow.user_id == self._user_id,
                    ComputeJobRow.status == JobStatus.FAILED.value,
                    ComputeJobRow.acknowledged.is_(False),
                )
                .values(acknowledged=True, updated_at=now)
            )
            await session.commit()
            won_claim = claim.rowcount == 1

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
            if not won_claim:
                if row.status != JobStatus.FAILED.value:
                    raise JobNotRetryableError(f"job is {row.status}; only failed jobs can be retried")
                raise JobNotRetryableError("job was already retried or dismissed")

            if row.args is None:
                raise JobNotRetryableError("job predates retry support; re-run it from its surface")
            kind = row.kind
            wire_args = row.args
            stage_number = row.stage_number
            shooter_slug = row.shooter_slug
            video_id = row.video_id
            row_match_id = row.match_id
            row_export_id = row.export_id

        match_token = current_match_id.set(row_match_id)
        export_token = current_export_id.set(row_export_id)
        try:
            return await self.submit(
                kind=kind,
                args=rehydrate_args(kind, wire_args),
                stage_number=stage_number,
                shooter_slug=shooter_slug,
                video_id=video_id,
            )
        finally:
            current_export_id.reset(export_token)
            current_match_id.reset(match_token)

    @contextlib.asynccontextmanager
    async def export_lock(self) -> AsyncIterator[None]:
        """Hold this user's export slot check (``ui.export_slot.admit``): two
        starts at once from two devices are judged one after the other.
        Postgres: a transaction-scoped advisory lock on its own connection,
        released when the block ends however it ends; SQLite (tests): a
        process lock per user."""
        async with self._session_factory() as session:
            if session.bind.dialect.name != "postgresql":
                lock = _SQLITE_EXPORT_LOCKS.setdefault(self._user_id, asyncio.Lock())
                async with lock:
                    yield
                return
            async with session.begin():
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"export:{self._user_id}"}
                )
                yield

    async def recent_export_jobs(self, limit: int = 200) -> list[Job]:
        """This user's most recently finished export jobs: the history the
        export estimates learn from (``export_estimates.history_rates``)."""
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(ComputeJobRow)
                        .where(
                            ComputeJobRow.user_id == self._user_id,
                            ComputeJobRow.export_id.is_not(None),
                            ComputeJobRow.status == JobStatus.SUCCEEDED.value,
                        )
                        .order_by(ComputeJobRow.created_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        return [_row_to_job(r) for r in rows]

    async def requeue(self, job_id: str, *, args_update: dict[str, Any]) -> Job | None:
        """Queue this job's kind again with its args updated, bound to its
        own match and export, whatever its status (the retry of a grid
        stage re-runs a grid that succeeded). ``None`` when unknown."""
        from ..ui.server import current_match_id

        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(ComputeJobRow).where(
                        ComputeJobRow.id == job_id, ComputeJobRow.user_id == self._user_id
                    )
                )
            ).scalar_one_or_none()
        if row is None or row.args is None:
            return None
        match_token = current_match_id.set(row.match_id)
        export_token = current_export_id.set(row.export_id)
        try:
            return await self.submit(
                kind=row.kind,
                args={**rehydrate_args(row.kind, row.args), **args_update},
                stage_number=row.stage_number,
                shooter_slug=row.shooter_slug,
                video_id=row.video_id,
            )
        finally:
            current_export_id.reset(export_token)
            current_match_id.reset(match_token)

    async def list_export(self, export_id: str) -> list[Job]:
        """This user's jobs of one export, in ``created_at`` order."""
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(ComputeJobRow)
                        .where(ComputeJobRow.user_id == self._user_id, ComputeJobRow.export_id == export_id)
                        .order_by(ComputeJobRow.created_at)
                    )
                )
                .scalars()
                .all()
            )
        return [_row_to_job(r) for r in rows]

    async def active_export_ids(self) -> list[str]:
        """The exports still holding a slot (``ui.jobs.export_is_active``):
        every job of each export with a job pending or running, read in one
        query and judged together."""
        live = (
            select(ComputeJobRow.export_id)
            .where(
                ComputeJobRow.user_id == self._user_id,
                ComputeJobRow.export_id.is_not(None),
                ComputeJobRow.status.in_((JobStatus.PENDING.value, JobStatus.RUNNING.value)),
            )
            .scalar_subquery()
        )
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(ComputeJobRow)
                        .where(ComputeJobRow.user_id == self._user_id, ComputeJobRow.export_id.in_(live))
                        .order_by(ComputeJobRow.created_at)
                    )
                )
                .scalars()
                .all()
            )
        grouped: dict[str, list[Job]] = {}
        for row in rows:
            grouped.setdefault(row.export_id, []).append(_row_to_job(row))
        return [export_id for export_id, jobs in grouped.items() if export_is_active(jobs)]

    async def find_active(
        self,
        *,
        kind: str,
        stage_number: int | None = None,
        shooter_slug: str | None = None,
        video_id: Any = None,
    ) -> Job | None:
        # ``video_id`` defaults to ``None``: callers that omit it want
        # stage-level dedupe, and the SQL filter is only added when an
        # explicit value is passed. (The :class:`JobRegistry` Protocol
        # form uses an _UNSET sentinel for the same disambiguation;
        # here ``None`` is sufficient because the row's ``video_id``
        # column is itself nullable and callers don't currently rely
        # on "match rows where video_id IS NULL specifically".)
        # ``shooter_slug`` is strict like ``stage_number``: ``None``
        # compiles to IS NULL, so slug-less lookups only match slug-less
        # jobs and never adopt another shooter's work (issue #664).
        # ``match_id`` is strict the same way, keyed on the value
        # :meth:`submit` would stamp from this context: slugs and stage
        # numbers repeat across a user's matches, so without it match B
        # adopted match A's job and its own was never queued (#1688). A
        # lookup with no match bound (``/api/me/*``, e.g. the upload's
        # ``generate_proxy``) only matches match-less rows, which is what
        # its submit writes. Unlike the local registry, a match-bound
        # lookup does not also match match-less rows: hosted mode has no
        # match-less kind a match-bound route looks up (``model_download``
        # is local only), so such a row is a stray that must not be
        # adopted by every match.
        from ..ui.server import current_match_id

        async with self._session_factory() as session:
            stmt = (
                select(ComputeJobRow)
                .where(
                    ComputeJobRow.user_id == self._user_id,
                    ComputeJobRow.kind == kind,
                    ComputeJobRow.stage_number == stage_number,
                    ComputeJobRow.shooter_slug == shooter_slug,
                    ComputeJobRow.match_id == current_match_id.get(),
                    ComputeJobRow.status.in_([JobStatus.PENDING.value, JobStatus.RUNNING.value]),
                )
                .order_by(ComputeJobRow.created_at)
            )
            if video_id is not None:
                stmt = stmt.where(ComputeJobRow.video_id == video_id)
            row = (await session.execute(stmt)).scalars().first()
        return _row_to_job(row) if row is not None else None

    async def cancel_active_for_match(self, match_id: str) -> int:
        """Cancel this user's PENDING/RUNNING jobs on ``match_id``; return the count.

        Used by the match-delete cascade to stop in-flight compute before
        tearing the match's storage/state down, so a worker can't re-write
        objects we're about to delete. Scoped to the row's ``match_id``:
        deleting one match must not stop the user's work in another.
        Match-less rows (``match_id`` NULL) are left alone; they have no
        match scope to write into.
        Reuses :meth:`cancel` so subprocess teardown + PENDING->CANCELLED
        semantics stay in one place.
        """
        async with self._session_factory() as session:
            ids = (
                (
                    await session.execute(
                        select(ComputeJobRow.id).where(
                            ComputeJobRow.user_id == self._user_id,
                            ComputeJobRow.match_id == match_id,
                            ComputeJobRow.status.in_([JobStatus.PENDING.value, JobStatus.RUNNING.value]),
                        )
                    )
                )
                .scalars()
                .all()
            )
        cancelled = 0
        for job_id in ids:
            if await self.cancel(job_id) is not None:
                cancelled += 1
        return cancelled

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
            # Synchronous dispatch on this thread; bridges to async DB
            # through ``run_sync``, which lands on the process
            # ``DbRunner`` in hosted mode (#1178) and on a fresh loop
            # otherwise.
            row_snapshot = run_sync(self._begin_run(job_id, now))
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

            # Build the timer from the row's wall-clock timestamps so
            # queue_wait_ms (enqueue->start, across processes) is derived from
            # the persisted ts, not perf_counter. The body opens phases via
            # ``handle.timer``; this thread owns the single persist + emit.
            timer = PhaseTimer(
                created_at=row_snapshot.created_at,
                started_at=row_snapshot.started_at,
            )
            handle = JobHandle(self, job_id, timer=timer)  # type: ignore[arg-type]
            kind = row_snapshot.kind
            probe = WorkerProbe.start()
            try:
                fn(handle)
            except JobCancelled:
                timer.set_worker(probe.finish())
                self._finalize_with_timings(job_id, kind, JobStatus.CANCELLED, timer, error=None)
                return
            except Exception as exc:  # noqa: BLE001 -- surface as FAILED
                timer.set_worker(probe.finish())
                # An account refusal is policy, not a crash: it fails the
                # row but is not reported to Sentry.
                if not isinstance(exc, FeatureRequiredError):
                    capture_job_exception(exc, kind=kind, user_id=self._user_id)
                self._finalize_with_timings(job_id, kind, JobStatus.FAILED, timer, error=str(exc))
                return
            timer.set_worker(probe.finish())
            self._finalize_with_timings(job_id, kind, JobStatus.SUCCEEDED, timer, error=None)
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

    def _finalize_with_timings(
        self,
        job_id: str,
        kind: str,
        status: JobStatus,
        timer: PhaseTimer,
        *,
        error: str | None,
    ) -> None:
        """Persist terminal status + timings in one write, then emit one event.

        Runs on the worker thread (synchronous), bridges to async DB
        through ``run_sync``, which lands on the process ``DbRunner`` in
        hosted mode (#1178) and on a fresh loop otherwise. The timer is
        built once here so a body that raised mid-phase still carries its
        partial timeline. The structured ``job.completed`` / ``job.failed``
        log is emitted once per job from this single terminal path.
        """
        timings = timer.build()
        run_sync(self._finalize_run(job_id, status, error=error, timings=timings))
        event = "job.completed" if status == JobStatus.SUCCEEDED else "job.failed"
        emit_job_event(
            logger,
            event=event,
            kind=kind,
            user_id=self._user_id,
            status=status.value,
            timings=timings,
            error=error,
        )

    async def _finalize_run(
        self,
        job_id: str,
        status: JobStatus,
        *,
        error: str | None,
        timings: dict[str, Any] | None = None,
    ) -> None:
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
            if row.status not in (JobStatus.PENDING.value, JobStatus.RUNNING.value):
                # Already ended by someone else: the stalled sweep, which
                # also failed the Procrastinate job. One terminal write per
                # row; overwriting it would leave the row and the queue
                # disagreeing.
                logger.warning(
                    "job %s finished %s but its row was already %s; keeping it", job_id, status, row.status
                )
                return
            row.status = status.value
            row.finished_at = now
            row.updated_at = now
            if timings is not None:
                row.timings = timings
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
        from the worker thread via :class:`JobHandle`. Bridges to async DB
        through ``run_sync``, which lands on the process ``DbRunner`` in
        hosted mode (#1178) and on a fresh loop otherwise."""
        run_sync(self._patch_async(job_id, kwargs))

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
                if key == "parts" and value is not None:
                    value = [p.model_dump(mode="json") if hasattr(p, "model_dump") else p for p in value]
                if hasattr(row, key):
                    setattr(row, key, value)
            row.updated_at = now
            await session.commit()

    def _is_cancel_requested(self, job_id: str) -> bool:
        """Sync check called from the worker thread."""
        return run_sync(self._is_cancel_requested_async(job_id))

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
