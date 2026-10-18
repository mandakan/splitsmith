"""The highlight reel over the API (spec 2026-10-18-highlight-reel-design,
part 2): readiness and the moment menu.

Reads the project and its audit docs only, so it answers the same locally
and hosted; the render job is what touches footage.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import export_runs, youtube_sidecar
from ..access import Feature
from ..async_bridge import run_sync
from ..config import DEFAULT_MOMENTS, Config, MomentKind
from ..export_naming import reel_file_name
from ..highlights import (
    BEAT_ORDER,
    MOMENT_GROUPS,
    OUT_OF_PLAY,
    MomentGroup,
    ReadinessState,
    readiness_for_project,
    reel_unlocked,
)
from ..reel_inputs import make_reel
from ..reel_plan import MOMENT_LABELS
from ..runtime import runtime
from . import audio, export_storage, match_exports
from .access_gate import require_feature

router = APIRouter()


class StageReadinessOut(BaseModel):
    stage_number: int
    state: ReadinessState
    proposals: int


class MenuItem(BaseModel):
    kind: str
    label: str
    group: MomentGroup
    default: bool


class HighlightsOut(BaseModel):
    unlocked: bool
    ready: int
    total: int
    stages: list[StageReadinessOut]
    menu: list[MenuItem]
    cuts: list[str] = ["normal", "short"]


def stage_rows(state: Any, slug: str) -> tuple:
    """Readiness for every stage of ``slug``: the audit docs in one query
    hosted, one file per stage locally."""
    project = state.shooter_project(slug)
    root = state.shooter_root(slug)
    docs = state.load_audit_docs(slug)
    statuses = project.stage_statuses(root, audit_docs=docs)
    if docs is None:
        docs = {stage.stage_number: state.load_audit(slug, stage.stage_number)[0] for stage in project.stages}
    return readiness_for_project(project, statuses, docs)


@router.get("/api/shooters/{slug}/highlights", response_model=HighlightsOut)
def get_highlights(slug: str, request: Request) -> HighlightsOut:
    rows = stage_rows(request.app.state.splitsmith_state, slug)
    counted = [r for r in rows if r.state not in ("skipped", "no_video")]
    return HighlightsOut(
        unlocked=reel_unlocked(rows),
        ready=sum(1 for r in counted if r.state == "ready"),
        total=len(counted),
        stages=[
            StageReadinessOut(stage_number=r.stage_number, state=r.state, proposals=r.proposals) for r in rows
        ],
        menu=[
            MenuItem(
                kind=kind,
                label=MOMENT_LABELS[kind],
                group=MOMENT_GROUPS[kind],
                default=kind in DEFAULT_MOMENTS,
            )
            for kind in BEAT_ORDER
        ],
    )


class HighlightReelRequest(BaseModel):
    """What Export's reel mode sends. Plain JSON on the job, so the job
    journal needs no model for it."""

    model_config = ConfigDict(extra="forbid")

    moments: list[MomentKind] = Field(min_length=1)
    short: bool = False
    upright: bool = True
    force: bool = False
    #: Chain the YouTube upload after the render, as the match export does.
    youtube_upload: bool = False
    youtube_privacy: Literal["unlisted", "private", "public"] = "unlisted"


#: Renders that write this shooter's exports and must not run beside a reel.
_EXCLUSIVE_RENDERS = ("match_export", "render_upload")


@router.post(
    "/api/shooters/{slug}/highlights",
    dependencies=[Depends(require_feature(Feature.hosted_compute))],
)
async def submit_highlight_reel(slug: str, req: HighlightReelRequest, request: Request) -> JSONResponse:
    """Queue the reel. A reel already running for the shooter is answered
    with that job (as the match export does); a locked match is refused
    here, naming its stages, rather than failing as a job."""
    state = request.app.state.splitsmith_state
    existing = await state.jobs.find_active(kind=JOB_KIND, shooter_slug=slug)
    if existing is not None:
        return JSONResponse(existing.model_dump(mode="json"))
    for kind in _EXCLUSIVE_RENDERS:
        if await state.jobs.find_active(kind=kind, shooter_slug=slug) is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "busy",
                    "message": "an export is rendering for this shooter; try when it finishes",
                },
            )
    rows = await run_in_threadpool(stage_rows, state, slug)
    if not req.force and not reel_unlocked(rows):
        blocking = [
            {"stage_number": r.stage_number, "state": r.state, "proposals": r.proposals}
            for r in rows
            if r.state != "ready" and r.state not in OUT_OF_PLAY
        ]
        raise HTTPException(
            status_code=409,
            detail={
                "code": "locked",
                "message": "not every stage is audited and broken down",
                "stages": blocking,
            },
        )
    job = await state.jobs.submit(
        kind=JOB_KIND, shooter_slug=slug, args={"slug": slug, "request": req.model_dump()}
    )
    return JSONResponse(job.model_dump(mode="json"))


JOB_KIND = "highlight_reel"
#: Every reel's YouTube tags.
REEL_TAGS = ["IPSC", "practical shooting", "highlights", "splitsmith"]


def _prefetch_footage(project: Any, root: Path) -> None:
    """Hosted: mirror each stage's clips down before the render reads the
    disk (the primary, and each secondary with a confirmed beep): the trim,
    else, on a web-only mirror, its rendition. Both helpers are no-ops
    locally, so this runs in either mode."""
    for stage in project.stages:
        for video in stage.videos:
            if video.beep_time is None or (video.role != "primary" and not video.beep_reviewed):
                continue
            pulled = audio.pull_trimmed_video(root, stage.stage_number, video, project=project)
            if not (pulled.exists() and pulled.stat().st_size > 0):
                audio.try_pull_web_trim(
                    project, audio.trimmed_video_path(root, stage.stage_number, video, project=project)
                )


def run_highlight_reel(handle: Any, slug: str, request: dict[str, Any], *, state: Any) -> None:
    """The ``highlight_reel`` job: pull the footage down when hosted, cut
    the reel, write its YouTube sidecar, record the run in the export
    history and push the files to storage."""
    from .server import _record_export_run  # the server module imports this one

    req = HighlightReelRequest.model_validate(request)
    started = time.monotonic()
    handle.update(progress=0.03, message="Reading stages")
    project = state.shooter_project(slug)
    root = state.shooter_root(slug)
    for stage in project.stages:
        state.materialize_audit(slug, stage.stage_number)
    handle.update(progress=0.08, message="Fetching footage")
    _prefetch_footage(project, root)
    handle.check_cancel()

    handle.update(progress=0.15, message="Cutting the reel")
    config = Config()
    label = project.competitor_name or slug
    output = project.exports_path(root) / reel_file_name(project.name, short=req.short)
    outcome = make_reel(
        project,
        root,
        label=label,
        output=output,
        upright=req.upright,
        force=req.force,
        moments=req.moments,
        short=req.short,
        config=config.highlights,
        segment_cache=match_exports.render_segment_cache(config.output),
        ffmpeg_binary=runtime().ffmpeg_binary,
    )
    if outcome.result is None:
        names = ", ".join(f"stage {r.stage_number} ({r.state})" for r in outcome.blockers) or "no stage"
        raise RuntimeError(f"no reel: nothing is ready ({names})")

    handle.update(progress=0.95, message="Saving")
    sidecar_path = youtube_sidecar.sidecar_path_for(output)
    title = f"{project.name} highlights" + (" #Shorts" if req.short and req.upright else "")
    youtube_sidecar.write_sidecar(
        youtube_sidecar.YouTubeSidecar(
            title=title,
            description="\n".join(
                [f"{label}, {project.name}.", "", *outcome.end_lines, "", "Made with splitsmith"]
            ),
            tags=list(REEL_TAGS),
            output_video=output.name,
        ),
        sidecar_path,
    )
    for path in (output, sidecar_path):
        export_storage.push_export_file(project, path)
    _record_export_run(
        state,
        slug,
        export_runs.ExportRun(
            run_id=export_runs.new_run_id(),
            kind="match",
            finished_at=datetime.now(UTC),
            duration_seconds=round(time.monotonic() - started, 3),
            stage_numbers=list(outcome.stages),
            formats=[JOB_KIND],
            anomaly_count=len(outcome.result.degradations),
            artifacts=[
                export_runs.ExportArtifact(filename=output.name, kind="match_video"),
                export_runs.ExportArtifact(filename=sidecar_path.name, kind="sidecar"),
            ],
        ),
    )
    # The upload is its own job (#1000): its own progress and cancel, and a
    # failed upload never fails a finished render. ``again`` because the
    # render just rewrote the sidecar.
    if req.youtube_upload:
        run_sync(
            state.jobs.submit(
                kind="youtube_upload",
                shooter_slug=slug,
                args={
                    "slug": slug,
                    "filename": output.name,
                    "privacy": req.youtube_privacy,
                    "again": True,
                    "playlist": None,
                    "playlist_id": None,
                    "publish_at": None,
                    "notify_subscribers": True,
                },
            )
        )
    handle.set_result(
        {
            "filename": output.name,
            "duration_seconds": outcome.result.duration_seconds,
            "degradations": list(outcome.result.degradations),
            "left_out": [r.stage_number for r in outcome.blockers],
        }
    )


__all__ = ["HighlightReelRequest", "JOB_KIND", "router", "run_highlight_reel", "stage_rows"]
