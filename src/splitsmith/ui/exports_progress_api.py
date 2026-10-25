"""The export progress panel's routes (spec 2026-10-25).

``GET /api/me/exports/active`` is the one route the Export page polls: the
account's exports still running (any match), each with a row per part, its
time left from ``export_estimates``, and the account's export limit (``None``
on desktop). An export is its jobs (``Job.export_id``); its kind and status
follow its lead job (``export_slot.lead_job``).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..access import Feature, export_limit
from ..auth import User
from ..export_estimates import estimate_export, history_rates, parts_of
from . import server as _server
from .access_gate import require_feature
from .context import _hosted_mode_active
from .export_slot import EXPORT_KIND_LABELS, export_slot, lead_job, summarize
from .jobs import Job, JobNotRetryableError, JobPart, JobStatus, export_is_active

router = APIRouter()

#: How many finished export jobs the estimates learn from.
HISTORY_JOBS = 200

_ENDED = (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)
_GRID_KINDS = ("compare-grid", "compare-grid-stage")


class ExportRow(BaseModel):
    job_id: str
    key: str
    label: str
    status: str
    progress: float | None
    elapsed_s: float | None
    remaining_s: float
    overrun: bool
    retryable: bool


class ExportView(BaseModel):
    export_id: str
    kind: str
    label: str
    match_id: str | None
    status: str
    started_at: datetime
    time_left_s: float | None
    finish_at: datetime | None
    approximate: bool
    rows: list[ExportRow]


class ActiveExports(BaseModel):
    limit: int | None
    exports: list[ExportView]


class RetryBody(BaseModel):
    job_id: str


def export_status(jobs: list[Job]) -> str:
    """Running while a job of it holds the slot; then its lead's status;
    with no lead, failed if any failed, else cancelled if any was."""
    if export_is_active(jobs):
        return "running"
    lead = lead_job(jobs)
    if lead is not None and lead.kind in EXPORT_KIND_LABELS and lead.kind != "export":
        return lead.status.value
    statuses = {j.status for j in jobs}
    for status in (JobStatus.FAILED, JobStatus.CANCELLED):
        if status in statuses:
            return status.value
    return JobStatus.SUCCEEDED.value


def _row_label(job: Job, key: str, label: str) -> str:
    if key != "job":
        return label
    if job.stage_number is not None:
        return f"Stage {job.stage_number}"
    return EXPORT_KIND_LABELS.get(job.kind, label)


def _run_order(ordered: list[Job]) -> list[tuple[Job, JobPart]]:
    """Every job's parts in the order they run: a lead job that waits for
    its siblings (a grid and its stage jobs) shows the parts before its wait,
    then the siblings, then the rest of its own; jobs queued after the lead
    (a chained upload) come last. Otherwise, in the order they were queued."""
    lead = lead_job(ordered)
    lead_parts = parts_of(lead) if lead is not None else []
    wait = next((i for i, p in enumerate(lead_parts) if p.kind == "wait"), None)
    if lead is None or wait is None:
        return [(job, part) for job in ordered for part in parts_of(job)]
    # A stage job the lead took over (cancelled, rendered by the lead as its
    # own ``stage-N`` part) is that part's row, not a cancelled one.
    taken = {p.key for p in lead_parts if p.kind == "grid_stage"}
    siblings = [
        j
        for j in ordered
        if j is not lead
        and j.created_at <= lead.created_at
        and not (j.status is JobStatus.CANCELLED and f"stage-{j.stage_number}" in taken)
    ]
    followers = [j for j in ordered if j is not lead and j.created_at > lead.created_at]
    return [
        *((lead, part) for part in lead_parts[:wait]),
        *((job, part) for job in siblings for part in parts_of(job)),
        *((lead, part) for part in lead_parts[wait:]),
        *((job, part) for job in followers for part in parts_of(job)),
    ]


def build_view(
    export_id: str, jobs: list[Job], history: dict[tuple[str, str | None], float], *, now: datetime
) -> ExportView:
    ordered = sorted(jobs, key=lambda j: j.created_at)
    summary = summarize(export_id, ordered)
    status = export_status(ordered)
    estimate = estimate_export(ordered, history, now=now, hosted=_hosted_mode_active())
    remaining = {(p.job_id, p.key): p for p in estimate.parts}
    rows: list[ExportRow] = []
    for job, part in _run_order(ordered):
        if part.kind == "wait":
            continue  # a grid's stage jobs are its rows
        est = remaining[(job.id, part.key)]
        elapsed = None
        if part.started_at is not None:
            end = part.finished_at or (now if part.status == "running" else None)
            elapsed = (end - part.started_at).total_seconds() if end is not None else None
        failed = part.status == "failed" or job.status is JobStatus.FAILED
        rows.append(
            ExportRow(
                job_id=job.id,
                key=part.key,
                label=_row_label(job, part.key, part.label),
                status=(
                    "cancelled"
                    if job.status is JobStatus.CANCELLED
                    else (
                        "failed"
                        if job.status is JobStatus.FAILED and part.status == "running"
                        else part.status
                    )
                ),
                progress=part.progress,
                elapsed_s=elapsed,
                remaining_s=est.remaining_s,
                overrun=est.overrun,
                retryable=failed and status != "running",
            )
        )
    running = status == "running"
    return ExportView(
        export_id=export_id,
        kind=summary.kind,
        label=summary.label,
        match_id=summary.match_id,
        status=status,
        started_at=ordered[0].created_at,
        time_left_s=estimate.time_left_s if running else None,
        finish_at=estimate.finish_at if running else None,
        approximate=estimate.approximate,
        rows=rows,
    )


async def _limit(request: Request, user: User) -> int | None:
    if not _hosted_mode_active():
        return None
    state = request.app.state.splitsmith_state
    override = await state.accounts.concurrent_exports(user.id)
    return export_limit(user.access_tier, override, state.access)


async def _view(request: Request, export_id: str) -> ExportView:
    state = request.app.state.splitsmith_state
    jobs = await state.jobs.list_export(export_id)
    if not jobs:
        raise HTTPException(status_code=404, detail=f"unknown export: {export_id}")
    history = history_rates(await state.jobs.recent_export_jobs(HISTORY_JOBS))
    return build_view(export_id, jobs, history, now=datetime.now(UTC))


@router.get("/api/me/exports/active", response_model=ActiveExports)
async def active_exports(request: Request, user: User = Depends(_server.get_current_user)) -> ActiveExports:
    """The account's running exports, every match, oldest first."""
    state = request.app.state.splitsmith_state
    history = history_rates(await state.jobs.recent_export_jobs(HISTORY_JOBS))
    now = datetime.now(UTC)
    views = [
        build_view(export_id, await state.jobs.list_export(export_id), history, now=now)
        for export_id in await state.jobs.active_export_ids()
    ]
    return ActiveExports(limit=await _limit(request, user), exports=sorted(views, key=lambda v: v.started_at))


@router.get("/api/me/exports/{export_id}", response_model=ExportView)
async def get_export(
    request: Request, export_id: str, user: User = Depends(_server.get_current_user)
) -> ExportView:
    return await _view(request, export_id)


@router.post("/api/me/exports/{export_id}/cancel", response_model=ExportView)
async def cancel_export(
    request: Request, export_id: str, user: User = Depends(_server.get_current_user)
) -> ExportView:
    """Cancel every job of the export still waiting or running."""
    state = request.app.state.splitsmith_state
    jobs = await state.jobs.list_export(export_id)
    if not jobs:
        raise HTTPException(status_code=404, detail=f"unknown export: {export_id}")
    for job in jobs:
        if job.status not in _ENDED:
            await state.jobs.cancel(job.id)
    return await _view(request, export_id)


@router.post(
    "/api/me/exports/{export_id}/retry",
    response_model=ExportView,
    dependencies=[Depends(require_feature(Feature.hosted_compute))],
)
async def retry_export_part(
    request: Request, export_id: str, body: RetryBody, user: User = Depends(_server.get_current_user)
) -> ExportView:
    """Run a failed part again, inside the same export (and its slot). A
    grid's failed stage re-runs the grid job, which reuses every stored
    stage and renders the missing one itself; any other failed job is
    retried with its own settings."""
    state = request.app.state.splitsmith_state
    jobs = await state.jobs.list_export(export_id)
    job = next((j for j in jobs if j.id == body.job_id), None)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job in export: {body.job_id}")
    if export_status(jobs) == "running":
        raise HTTPException(status_code=409, detail="the export is still running")
    part_failed = any(p.status == "failed" for p in job.parts or ())
    if job.status is not JobStatus.FAILED and not part_failed:
        raise HTTPException(status_code=409, detail="only a failed part can be retried")
    async with export_slot(request, export_id=export_id):
        if job.kind in _GRID_KINDS:
            grid = next((j for j in jobs if j.kind == "compare-grid"), None)
            if grid is None or await state.jobs.requeue(grid.id, args_update={"stage_jobs": {}}) is None:
                raise HTTPException(status_code=409, detail="the grid job cannot be queued again")
        else:
            try:
                await state.jobs.retry(job.id)
            except JobNotRetryableError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _view(request, export_id)
