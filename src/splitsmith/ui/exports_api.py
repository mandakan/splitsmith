"""Export routes: per-stage + match export submission, the overview, the
deliverable download, and the run history (#629).

Lifted out of ``server.py`` under #919's standing rule -- every feature
that touches ``server.py`` lifts its own routes to a domain router on the
way past. Paths are unchanged, so the ``/api/matches/{id}/`` alias
middleware, the test harness's ``_SCOPED_PREFIXES`` and every SPA call
site are untouched.

**This module must never import ``server``**: ``server`` imports the two
request models from here, so an import back would be a cycle at load
time. Anything shared goes to a third module -- that is why
``ensure_source_reachable`` lives in ``http_errors``.

``GET /api/match/templates`` stays in ``server.py``: it is an export-
dialog route by name only -- it serves the template registry, owns no
export state, and moving it would widen this module past the routes
#629 actually touches.

The export *job bodies* stay in ``server.py``. Lifting
``register_job_bodies`` is a separate and much larger job.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .. import export_runs
from ..match_project import trim_blocker
from . import export_storage
from .http_errors import ensure_source_reachable

router = APIRouter()


class ExportStageRequest(BaseModel):
    """Body for POST /api/stages/{n}/export.

    Each toggle defaults True; turning one off skips that artefact while
    leaving the others on. ``write_trim`` produces the lossless stream-copy
    trim into ``<project>/exports/`` -- distinct from the audit-mode
    short-GOP scrub copy in ``<project>/trimmed/``. The FCPXML always
    references the lossless trim so SPA exports match ``splitsmith single``.
    """

    write_trim: bool = True
    write_csv: bool = True
    write_fcpxml: bool = True
    write_report: bool = True
    # Pre-rendered alpha overlay MOV (issue #45). Defaults False because
    # the render rasterizes sprites through a browser and encodes them --
    # non-trivially slower than the other writers. The Analysis & Export
    # checkbox opts-in per stage.
    write_overlay: bool = False
    # Overlay format knobs (issue #45 follow-up). Defaults match the
    # legacy ProRes 4444 path on platforms without VideoToolbox; on macOS
    # ``"auto"`` switches to ``hevc-alpha`` (~10-20x smaller). Resolution
    # and fps caps are off by default to preserve frame-for-frame parity
    # with the source clip.
    overlay_codec: Literal["auto", "hevc-alpha", "prores-4444"] = "auto"
    overlay_max_height: int | None = None
    overlay_max_fps: float | None = None
    # Palette preset for the overlay text + stroke. ``"splitsmith"``
    # (default) uses the same tokens the web UI ships, mirrored into
    # ``data/overlay_theme.json``. ``"clean"`` is the neutral
    # white-on-amber alternative.
    overlay_theme: Literal["splitsmith", "clean"] = "splitsmith"
    # Multi-cam selection (issue #54). Allowlist of secondary
    # ``video_id``s to ride the FCPXML / get their own lossless trim. The
    # default ``None`` means "include every secondary with a beep" -- the
    # legacy behaviour. An empty list excludes all secondaries; a non-empty
    # list ships only the named cams (silently dropping any id not on the
    # stage). Cams without a beep are still skipped regardless of selection
    # since they can't be sync-aligned.
    secondary_video_ids: list[str] | None = None


class MatchExportRequest(BaseModel):
    """Body for POST /api/match/export (issue #171).

    Stitches the listed stages into one FCPXML, in the order given. Each
    stage must already have a lossless trim + audit shots (run the per-stage
    export first); the match export composes from those without re-encoding.
    ``head_pad_seconds`` / ``tail_pad_seconds`` are the visible padding
    around the beep / final shot per stage and are clamped server-side to
    the project's pre/post buffer settings (default 5.0s) -- exceeding the
    cap returns 400. ``project_name`` defaults to the bound project's name
    when omitted.
    """

    stage_numbers: list[int]
    head_pad_seconds: float = 5.0
    tail_pad_seconds: float = 5.0
    include_secondaries: bool = True
    include_overlay: bool = True
    # Overlay format knobs forwarded to per-stage re-renders. Match the
    # single-stage defaults so a match export with no overlay edits is
    # byte-comparable with the per-stage export.
    overlay_codec: Literal["auto", "hevc-alpha", "prores-4444"] = "auto"
    overlay_max_height: int | None = None
    overlay_max_fps: float | None = None
    overlay_theme: Literal["splitsmith", "clean"] = "splitsmith"
    project_name: str | None = None
    # Issue #193. ``"stacked"`` keeps secondaries full-frame (today's
    # behaviour). ``"pip-corners"`` adds an ``<adjust-transform>`` to each
    # secondary, rotating through TR -> TL -> BR -> BL at 25% scale.
    pip_layout: Literal["stacked", "pip-corners"] = "stacked"
    # Issue #197. ``"fcpxml"`` writes Final Cut Pro 1.10 (the default).
    # ``"fcp7xml"`` writes a Final Cut Pro 7-style xmeml ``.xml``
    # importable into Premiere Pro and DaVinci Resolve. Issue #174:
    # ``"mp4"`` bakes the stitched composition into a single MP4 via
    # ffmpeg (overlays / PiP burned in, no NLE needed).
    output_format: Literal["fcpxml", "fcp7xml", "mp4"] = "fcpxml"
    # Issue #195. Uniform transition between every consecutive stage
    # pair, or ``"none"`` for hard cuts. Currently only the FCPXML
    # renderer emits transitions; FCP7 / MP4 surface a "transitions
    # ignored" anomaly when set together with those formats.
    transition_kind: Literal["none", "zoom", "static"] = "none"
    transition_duration_seconds: float = 0.5
    # Issue #196. Per-stage title cards. ``"slate"`` adds a pre-stage
    # card on the spine; ``"lower-third"`` is a connected text clip
    # overlaid on the start of the primary. FCPXML only today;
    # FCP7 / MP4 surface a "titles ignored" anomaly when combined.
    title_kind: Literal["none", "slate", "lower-third"] = "none"
    title_duration_seconds: float = 1.5
    # Issue #173. Optional intro / outro video paths. Server expands
    # ``~`` and probes the file to validate frame rate against the
    # timeline. Missing files surface as anomalies; non-fatal so the
    # rest of the export still ships.
    intro_path: str | None = None
    outro_path: str | None = None
    # Issue #204 layer 1. Generate a YouTube-shaped JSON sidecar
    # alongside the export plus a per-shot ``.srt``. FCPXML route
    # also gets chapter markers embedded so they survive an NLE
    # round-trip into an MP4 chapter atom.
    youtube_sidecar: bool = False
    # Issue #204 layer 2. Encode the MP4 with YouTube's recommended
    # H.264 profile / GOP / colour / audio params. Only meaningful for
    # ``output_format == "mp4"``; ignored otherwise (anomaly surfaced).
    youtube_preset: bool = False


@router.get("/api/shooters/{slug}/exports/overview")
def export_overview(slug: str, request: Request) -> JSONResponse:
    """Match-overview payload for the Analysis & Export screen.

    Returns one row per stage with audit + export status (shot count,
    pending candidates, file paths, last export time, ready-to-export
    flag). Pure stat: no detection, no rewriting of audit JSON.

    ``match_exports`` lists the match-level deliverables the same way
    (#629). Before this, the only thing that knew a match FCPXML
    existed was the export job's own ``Job.result``, so a hosted user
    who reloaded lost the download link to a file that was in R2 the
    whole time -- the per-stage rows survived a reload and the match
    output did not.
    """
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    # Hosted: audit docs live in state_docs, not on this container's
    # disk, so load each stage's doc and hand it to the overview
    # (which would otherwise read an absent local file -> 0 shots).
    # Local: load_audit reads the file, same as before.
    audit_docs: dict[int, dict] = {}
    for stg in project.stages:
        doc, _ = state.load_audit(slug, stg.stage_number)
        if doc is not None:
            audit_docs[stg.stage_number] = doc
    root = state.shooter_root(slug)
    rows = project.export_overview(root, audit_docs=audit_docs)
    match_files = project.match_export_files(root)
    return JSONResponse(
        {
            "stages": [r.model_dump(mode="json") for r in rows],
            "match_exports": [m.model_dump(mode="json") for m in match_files],
        }
    )


@router.get("/api/shooters/{slug}/exports/runs")
def list_export_runs(slug: str, request: Request) -> JSONResponse:
    """The shooter's export history, newest first (#629).

    Deliberately separate from ``exports/overview``: the overview answers
    "what can I download now" and this answers "what happened". Run
    grouping, duration, selected formats and anomaly count are the four
    facts a directory listing cannot reconstruct, which is the whole
    reason a record is written at export time.

    A malformed or unreadable log reads as an empty history rather than a
    500 -- ``export_runs.load_log`` drops what it cannot parse.
    """
    state = request.app.state.splitsmith_state
    doc, _version = state.load_export_runs(slug)
    log = export_runs.load_log(doc)
    return JSONResponse({"runs": [r.model_dump(mode="json") for r in log.runs]})


@router.get("/api/shooters/{slug}/exports/file/{filename:path}")
def download_export_file(slug: str, filename: str, request: Request) -> FileResponse:
    """Serve an export deliverable for download.

    Local mode reads the file straight off the project's ``exports/``
    dir. Hosted mode pulls it from object storage first: the worker that
    produced it ran in a separate container, so the bytes only exist in
    S3 until this seam mirrors them down (the export analogue of
    ``stream_video``'s ``pull_trimmed_video``). The SPA uses this in
    place of "Reveal in Finder", which is meaningless across containers.

    ``filename`` is confined to the ``exports/`` dir: the resolved path
    must stay inside it, so ``..`` traversal is a 400.
    """
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    exports_dir = project.exports_path(state.shooter_root(slug)).resolve()
    target = (exports_dir / filename).resolve()
    try:
        target.relative_to(exports_dir)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="download path must be inside the exports folder"
        ) from exc
    if not (target.exists() and target.is_file()):
        export_storage.pull_export_file(project, target)
    if not (target.exists() and target.is_file()):
        raise HTTPException(status_code=404, detail=f"export not found: {filename}")
    media_types = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".fcpxml": "application/xml",
        ".xml": "application/xml",
        ".csv": "text/csv",
        ".txt": "text/plain",
        ".srt": "application/x-subrip",
        ".json": "application/json",
    }
    media_type = media_types.get(target.suffix.lower(), "application/octet-stream")
    return FileResponse(target, media_type=media_type, filename=target.name)


@router.post("/api/shooters/{slug}/stages/{stage_number}/export")
async def export_stage(
    slug: str, stage_number: int, req: ExportStageRequest, request: Request
) -> JSONResponse:
    """Submit a per-stage export job.

    Wraps the ``export_helpers.export_stage`` orchestrator (lossless trim
    + CSV + FCPXML + report) in a JobRegistry entry so the SPA's
    JobsPanel surfaces progress alongside detect-beep / trim /
    shot-detect. Returns a Job snapshot; the SPA polls
    ``/api/jobs/{id}`` until status leaves running, then re-fetches
    ``/api/exports/overview`` to refresh paths and ``last_export_at``.

    Pre-flight validations (stage exists, primary present, beep ready,
    source reachable, scoreboard not placeholder) still raise HTTP
    errors up front so the SPA can show a clear error before queueing
    a useless job.
    """
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    try:
        stage = project.stage(stage_number)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # One rule, three surfaces (#613): ``trim_blocker`` is what the CLI
    # planner and ``export_overview.ready_to_trim`` ask too. The verdict
    # is only decomposed here to word the 400 -- the SPA shows the
    # detail verbatim, so "no beep" and "no stage time" can't share one
    # message. Note a positive ``time_seconds`` is the whole duration
    # test: an untouched placeholder has 0.0. Also demanding a
    # ``scorecard_updated_at`` or a ``time_seconds_manual`` stamp used to
    # reject a scoreboard row whose timestamp failed to parse -- a stage
    # the CLI cut without complaint.
    primary = stage.primary()
    blocker = trim_blocker(stage, primary)
    if blocker is not None:
        detail = {
            "skipped": f"stage {stage_number} is marked skipped; un-skip it before exporting",
            "no_beep": (
                f"stage {stage_number} has no primary or no beep yet; "
                "finish ingest + audit before exporting"
            ),
            "no_stage_time": (
                f"stage {stage_number} is a placeholder; set a stage time "
                "or import a scoreboard before exporting"
            ),
        }[blocker]
        raise HTTPException(status_code=400, detail=detail)
    assert primary is not None  # guaranteed: ``trim_blocker`` said no_beep otherwise
    # Source-reachability surfaces as a structured 424 so the SPA
    # renders the same "reconnect external storage" message used
    # elsewhere -- even if the user only wants CSV/report (those would
    # still work, but the explicit 424 lets them re-try after
    # reconnecting rather than hunting for the partial degradation
    # message in the per-row anomaly list).
    # ``source_present``, not ``resolve_video_path``: the export job owns
    # the ffmpeg pass, so resolving here mirrored the raw source into the
    # API container purely as an existence check (#638).
    if req.write_trim or req.write_fcpxml:
        root = state.shooter_root(slug)
        if not project.source_present(root, primary.path):
            ensure_source_reachable(stage_number, root / primary.path)

    existing = await state.jobs.find_active(kind="export", stage_number=stage_number, shooter_slug=slug)
    if existing is not None:
        return JSONResponse(existing.model_dump(mode="json"))
    job = await state.jobs.submit(
        kind="export",
        stage_number=stage_number,
        shooter_slug=slug,
        args={"slug": slug, "stage_number": stage_number, "req": req},
    )
    return JSONResponse(job.model_dump(mode="json"))


@router.post("/api/shooters/{slug}/export/match")
async def export_match(slug: str, req: MatchExportRequest, request: Request) -> JSONResponse:
    """Stitch N stages into one FCPXML (issue #171, #172).

    Job-queued: per-stage trims (and optional overlays) can take
    minutes for a real match, so the response is a Job snapshot the
    SPA polls via ``/api/me/jobs/{id}``. The worker re-runs any
    missing per-stage exports before invoking the match composer,
    so the user doesn't have to click Generate on each stage first.

    Validation up-front (404 on unbound project, 400 on empty
    selection / unknown stage / missing primary or beep / padding out
    of range) so the SPA shows a clear error before queueing.
    """
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    if not req.stage_numbers:
        raise HTTPException(status_code=400, detail="stage_numbers cannot be empty")

    # Padding cap: clamp at the project's pre/post buffer. Exceeding
    # the cap is a 400 with a precise message, not a silent clamp --
    # the user's slider in #172 already enforces the same bound, so a
    # value above it is a real bug worth surfacing.
    max_head = project.trim_pre_buffer_seconds
    max_tail = project.trim_post_buffer_seconds
    if not 0.0 <= req.head_pad_seconds <= max_head:
        raise HTTPException(
            status_code=400,
            detail=(
                f"head_pad_seconds={req.head_pad_seconds} out of range; "
                f"must be in [0.0, {max_head}] (project trim_pre_buffer)"
            ),
        )
    if not 0.0 <= req.tail_pad_seconds <= max_tail:
        raise HTTPException(
            status_code=400,
            detail=(
                f"tail_pad_seconds={req.tail_pad_seconds} out of range; "
                f"must be in [0.0, {max_tail}] (project trim_post_buffer)"
            ),
        )

    # Pre-flight stage validations. Loaded once here so a bad
    # selection 400s before we queue a worker. The audit-shots check
    # happens in the worker (it reads the JSON anyway) so we don't
    # double-parse.
    for stage_number in req.stage_numbers:
        try:
            stage = project.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"stage {stage_number} not found in project",
            ) from exc
        primary = stage.primary()
        if primary is None or primary.beep_time is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"stage {stage_number} has no primary or no beep yet; "
                    "finish ingest + audit before match export"
                ),
            )
        # Source-reachability matters because the worker may have to
        # produce missing trims via ffmpeg. Surface up-front rather
        # than letting the worker fail mid-flight.
        # ``source_present``, not ``resolve_video_path``: the latter
        # mirrors a hosted object into the local cache, so a preflight
        # that only decides whether to queue downloaded every source
        # into the API container -- twice per stage (#637). The path
        # handed to the 424 helper is rebuilt rather than resolved;
        # ``root / path`` is what ``resolve_video_path`` returns in the
        # no-storage and mirror-hit cases, and ``pathlib`` drops the
        # left operand when ``primary.path`` is absolute.
        if not project.source_present(state.shooter_root(slug), primary.path):
            ensure_source_reachable(
                stage_number,
                state.shooter_root(slug) / primary.path,
            )

    existing = await state.jobs.find_active(kind="match_export", shooter_slug=slug)
    if existing is not None:
        return JSONResponse(existing.model_dump(mode="json"))
    job = await state.jobs.submit(
        kind="match_export",
        shooter_slug=slug,
        args={"slug": slug, "req": req},
    )
    return JSONResponse(job.model_dump(mode="json"))
