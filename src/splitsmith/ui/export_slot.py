"""How many exports a hosted account runs at once (spec 2026-10-25).

Every export route queues its jobs inside :func:`export_slot`: it gives
the export its id (``ui.context.export_scope``, stamped on every job), holds
the wakes to one at the end (``worker_trigger.hold_wakes``) and, hosted,
refuses a start over the account's limit (``access.export_limit``) with a
409 naming the export already running. The check and the queueing happen
under the user's export lock, so two starts from two devices in the same
second are judged one after the other. Desktop and local mode: no limit.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel

from ..access import export_limit
from .context import _hosted_mode_active, export_scope
from .jobs import EXPORT_LEAD_KINDS, Job

#: What the panel and the 409 call each kind of export.
EXPORT_KIND_LABELS: dict[str, str] = {
    "compare-grid": "Compare grid",
    "match_export": "Match export",
    "highlight_reel": "Highlight reel",
    "export": "Stage export",
}


class ExportSummary(BaseModel):
    export_id: str
    kind: str
    label: str
    match_id: str | None


class ExportLimitReachedError(Exception):
    def __init__(self, running: ExportSummary) -> None:
        super().__init__(f"export {running.export_id} is running")
        self.running = running


def lead_job(jobs: list[Job]) -> Job | None:
    """The job an export's kind and status follow: its grid, match export
    or reel, else (a stage export, trims only) its first job."""
    ordered = sorted(jobs, key=lambda j: j.created_at)
    return next((j for j in ordered if j.kind in EXPORT_LEAD_KINDS), ordered[0] if ordered else None)


def summarize(export_id: str, jobs: list[Job]) -> ExportSummary:
    lead = lead_job(jobs)
    kind = lead.kind if lead is not None else "export"
    label = EXPORT_KIND_LABELS.get(kind, kind)
    stages = sorted({j.stage_number for j in jobs if j.stage_number is not None})
    if kind == "export" and len(stages) > 1:
        label = f"Trims, {len(stages)} stages"
    elif kind == "export" and stages:
        label = f"{label}, stage {stages[0]}"
    return ExportSummary(
        export_id=export_id, kind=kind, label=label, match_id=lead.match_id if lead is not None else None
    )


@contextlib.asynccontextmanager
async def admit(jobs: Any, *, limit: int | None, export_id: str) -> AsyncIterator[str]:
    """Queue the block's jobs as export ``export_id`` if the account has a
    slot. ``limit`` None is no limit. Raises :class:`ExportLimitReachedError`
    naming the oldest export still running. ``export_id`` already running
    (a retry inside its own export) does not count against itself."""
    from ..worker_trigger import hold_wakes

    # No limit (desktop, local) leaves nothing to serialize.
    lock = jobs.export_lock() if limit is not None else contextlib.nullcontext()
    async with lock:
        if limit is not None:
            active = [i for i in await jobs.active_export_ids() if i != export_id]
            if len(active) >= limit:
                raise ExportLimitReachedError(summarize(active[0], await jobs.list_export(active[0])))
        with export_scope(export_id):
            async with hold_wakes():
                yield export_id


@contextlib.asynccontextmanager
async def export_slot(request: Request, *, export_id: str | None = None) -> AsyncIterator[str]:
    """The export route's wrapper around its submits: yields the export id.
    Hosted, over the limit: HTTP 409 ``{"code": "export_limit", "running":
    ExportSummary}``."""
    state = request.app.state.splitsmith_state
    new_id = export_id or uuid.uuid4().hex
    limit: int | None = None
    if _hosted_mode_active():
        user = getattr(request.state, "user", None)
        override = await state.accounts.concurrent_exports(user.id) if user is not None else None
        limit = export_limit(getattr(user, "access_tier", None), override, state.access)
    try:
        async with admit(state.jobs, limit=limit, export_id=new_id) as admitted:
            yield admitted
    except ExportLimitReachedError as exc:
        raise HTTPException(
            status_code=409, detail={"code": "export_limit", "running": exc.running.model_dump(mode="json")}
        ) from exc
