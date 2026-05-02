"""FastAPI app for the production UI.

Endpoints (locked v1 surface):

  GET  /api/health                  -- project metadata + schema version
  GET  /api/project                 -- full MatchProject dump
  GET  /api/fs/list?path=...        -- list directory entries (folder picker)
  POST /api/scoreboard/import       -- import an SSI Scoreboard JSON
  POST /api/project/placeholder-stages -- bootstrap source-first (no scoreboard yet)
  POST /api/videos/scan             -- register videos (folder or explicit paths)
  POST /api/videos/auto-match       -- run video_match.py heuristic, return suggestions
  POST /api/videos/remove           -- remove a registered video + cleanup caches
  POST /api/assignments/move        -- set role / unassign / move between stages
  POST /api/project/settings        -- update raw/audio/trimmed/exports dir overrides
  GET  /api/fs/probe?path=...       -- probe + thumbnail one source file on demand
  GET  /api/thumbnails/{key}.jpg    -- serve cached thumbnail
  POST /api/stages/{n}/detect-beep  -- submit a beep-detection job (runs in pool)
  POST /api/stages/{n}/beep         -- manual beep_time override (synchronous)
  POST /api/stages/{n}/trim         -- submit an audit-mode trim job
  POST /api/stages/{n}/shot-detect  -- submit shot detection on the audit clip
  GET  /api/jobs                    -- list all retained jobs
  GET  /api/jobs/{job_id}           -- poll a single job for progress / status
  GET  /api/stages/{n}/audio        -- serve cached primary WAV (Range supported)
  GET  /api/stages/{n}/peaks?bins=N -- waveform peak data for the audit screen
  GET  /api/stages/{n}/beep-preview -- ~1s MP4 around the detected beep (#27)
  GET  /api/videos/stream?path=...  -- serve a registered video file (Range)
  GET  /api/stages/{n}/audit        -- read the stage's audit JSON (404 if none)
  PUT  /api/stages/{n}/audit        -- atomically write the stage's audit JSON
  GET  /api/fixture/audit?path=...  -- standalone fixture review (read JSON)
  PUT  /api/fixture/audit?path=...  -- standalone fixture review (write JSON)
  GET  /api/fixture/peaks?path=...  -- waveform peaks for a fixture's sibling WAV
  GET  /api/fixture/audio?path=...  -- serve the fixture's sibling WAV (Range)
  GET  /api/fixture/video?path=...  -- serve a fixture-bound video file (Range)

Design notes:
- Localhost only. No auth, no CORS configuration beyond what Vite needs in dev.
- The server holds a single ``MatchProject`` open at a time, identified by
  ``project_root`` at startup. Multi-project orchestration lives in the SPA.
- All on-disk mutations go through the project model's atomic save.
- The server re-loads the project from disk for every request (no caching), so
  external edits are visible without restart.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import beep_detect, video_probe
from .. import shot_detect as shot_detect_module
from .. import thumbnail as thumbnail_helpers
from .. import waveform as waveform_helpers
from ..config import ShotDetectConfig
from . import audio as audio_helpers
from .jobs import Job, JobHandle, JobRegistry
from .project import (
    VIDEO_EXTENSIONS,
    MatchProject,
    ScoreboardImportConflictError,
    VideoRole,
)

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent.parent / "ui_static" / "dist"
UI_SOURCE_DIR = Path(__file__).parent.parent / "ui_static"


def _ensure_ui_built() -> None:
    """Rebuild the SPA bundle if missing or older than any tracked source file.

    Without this, ``splitsmith ui`` happily serves a stale ``dist/`` from a
    previous build, so source edits never reach the browser. We compare
    mtimes of every file under ``ui_static/src/`` (plus the manifests that
    affect the build) against the newest file in ``dist/``; if anything is
    newer, run ``npm run build``. ``node_modules/`` and ``dist/`` itself are
    excluded.

    No-op when ``npm`` isn't on PATH (the user might be in a deploy environment
    that ships dist/ separately) -- log a warning and serve whatever's there.
    """
    src_dir = UI_SOURCE_DIR / "src"
    if not src_dir.exists():
        return  # not a development checkout
    tracked: list[Path] = []
    tracked.extend(src_dir.rglob("*"))
    for manifest in (
        "package.json",
        "package-lock.json",
        "vite.config.ts",
        "tsconfig.json",
        "tsconfig.app.json",
        "tsconfig.node.json",
        "tailwind.config.js",
        "postcss.config.js",
        "index.html",
    ):
        p = UI_SOURCE_DIR / manifest
        if p.exists():
            tracked.append(p)
    src_mtime = max(
        (p.stat().st_mtime for p in tracked if p.is_file()),
        default=0.0,
    )

    dist_index = STATIC_DIR / "index.html"
    dist_mtime = (
        min(
            (p.stat().st_mtime for p in STATIC_DIR.rglob("*") if p.is_file()),
            default=0.0,
        )
        if dist_index.exists()
        else 0.0
    )

    if dist_index.exists() and dist_mtime >= src_mtime:
        return  # already up to date

    npm = shutil.which("npm")
    if npm is None:
        logger.warning(
            "ui_static/dist appears stale but npm is not on PATH; "
            "serving whatever is in dist/"
        )
        return

    logger.info(
        "Rebuilding SPA bundle (dist mtime %.0f < source %.0f)...",
        dist_mtime,
        src_mtime,
    )
    try:
        subprocess.run(
            [npm, "run", "build"],
            cwd=UI_SOURCE_DIR,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        logger.error("npm run build failed (exit %d); serving stale bundle", exc.returncode)


def _now_iso() -> str:
    """ISO-8601 UTC timestamp for audit_events entries."""
    return datetime.now(UTC).isoformat()


@dataclass
class AppState:
    """Per-process state. One project root per server instance."""

    project_root: Path
    jobs: JobRegistry = field(default_factory=JobRegistry)

    def load(self) -> MatchProject:
        return MatchProject.load(self.project_root)


class HealthResponse(BaseModel):
    status: str = "ok"
    project_name: str
    project_root: str
    schema_version: int


# Request bodies ----------------------------------------------------------


class ScoreboardImportRequest(BaseModel):
    data: dict[str, Any]
    overwrite: bool = False


class PlaceholderStagesRequest(BaseModel):
    """Bootstrap request: create N placeholder stages without a scoreboard."""

    stage_count: int
    match_name: str | None = None
    match_date: date | None = None


class ScanRequest(BaseModel):
    """Either ``source_dir`` (folder scan, current behaviour) or
    ``source_paths`` (explicit list of files, USB-cam workflow). Exactly one
    must be provided."""

    source_dir: str | None = None
    source_paths: list[str] | None = None
    auto_assign_primary: bool = True
    link_mode: str = "symlink"


class ScanResponse(BaseModel):
    registered: list[str]
    auto_assigned: dict[int, str]
    skipped: list[str]


class SettingsRequest(BaseModel):
    """Partial update for project storage overrides (#23). Any field omitted
    is left unchanged. Pass ``""`` (empty string) to clear an override back to
    the project-root default. Set ``confirm=True`` to acknowledge that any
    existing files in the *old* directories will be left behind (no migration)."""

    raw_dir: str | None = None
    audio_dir: str | None = None
    trimmed_dir: str | None = None
    exports_dir: str | None = None
    probes_dir: str | None = None
    thumbs_dir: str | None = None
    # Audit-mode trim buffers (#15 / #16). Pre = pad before beep,
    # post = pad after stage end. Both must be non-negative.
    trim_pre_buffer_seconds: float | None = None
    trim_post_buffer_seconds: float | None = None
    confirm: bool = False


class MoveRequest(BaseModel):
    video_path: str
    to_stage_number: int | None = None
    role: VideoRole = "secondary"


class BeepOverrideRequest(BaseModel):
    beep_time: float | None  # None clears the override


class RemoveVideoRequest(BaseModel):
    """Body for POST /api/videos/remove (#24).

    ``reset_audit`` only takes effect when removing a primary; otherwise it
    is ignored (audit is stage-level state, and non-primary removals do not
    invalidate it).
    """

    video_path: str
    reset_audit: bool = False


class FsEntry(BaseModel):
    name: str
    kind: Literal["dir", "video", "file"]
    video_count: int | None = None  # populated for dirs
    size_bytes: int | None = None  # populated for files
    mtime: float | None = None
    # Populated for videos when probed (issue #24). ``duration`` may be null
    # if probing was skipped (no ?probe=true) or hit the per-listing budget.
    # ``thumbnail_url`` points at /api/thumbnails/{key}.jpg when a thumbnail
    # is cached; the caller can hit /api/fs/probe to generate one on demand.
    duration: float | None = None
    thumbnail_url: str | None = None


class FsListing(BaseModel):
    path: str
    parent: str | None
    entries: list[FsEntry]
    suggested_starts: list[str]  # bookmarks: home, ~/Movies, last_scanned_dir, etc.


def create_app(*, project_root: Path, project_name: str) -> FastAPI:
    """Create the FastAPI app bound to a single match project on disk.

    The project is initialized on first call (idempotent), then the app keeps
    the root path and re-loads on every request that needs it. We avoid
    caching the model in memory so external edits to ``project.json`` are
    visible without restarting the server.
    """
    MatchProject.init(project_root, name=project_name)
    state = AppState(project_root=project_root.resolve())

    app = FastAPI(
        title="splitsmith UI",
        description="Production UI backend (issue #11/#12).",
        version="0.1.0",
    )

    # ----------------------------------------------------------------------
    # API
    # ----------------------------------------------------------------------

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        project = state.load()
        return HealthResponse(
            project_name=project.name,
            project_root=str(state.project_root),
            schema_version=project.schema_version,
        )

    @app.get("/api/project")
    def get_project() -> JSONResponse:
        return JSONResponse(state.load().model_dump(mode="json"))

    @app.post("/api/scoreboard/import")
    def import_scoreboard(req: ScoreboardImportRequest) -> JSONResponse:
        project = state.load()
        try:
            project.import_scoreboard(req.data, overwrite=req.overwrite)
        except ScoreboardImportConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        project.save(state.project_root)
        return JSONResponse(project.model_dump(mode="json"))

    @app.post("/api/project/placeholder-stages")
    def create_placeholder_stages(req: PlaceholderStagesRequest) -> JSONResponse:
        """Create N placeholder stages so source-first ingest works without a
        scoreboard. A real scoreboard import later overlays the placeholders
        and preserves video assignments by ``stage_number``."""
        project = state.load()
        try:
            project.init_placeholder_stages(
                req.stage_count,
                match_name=req.match_name,
                match_date=req.match_date,
            )
        except ScoreboardImportConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        project.save(state.project_root)
        return JSONResponse(project.model_dump(mode="json"))

    @app.post("/api/videos/scan", response_model=ScanResponse)
    def scan_videos(req: ScanRequest) -> ScanResponse:
        if req.link_mode not in ("symlink", "copy"):
            raise HTTPException(status_code=400, detail="link_mode must be 'symlink' or 'copy'")
        if (req.source_dir is None) == (req.source_paths is None):
            raise HTTPException(
                status_code=400,
                detail="exactly one of source_dir or source_paths must be provided",
            )

        # Build the list of files to register and capture the directory we'll
        # remember as last_scanned_dir. For source_paths mode we use the parent
        # of the first file as a sensible default for the picker next time.
        candidates: list[Path] = []
        last_dir: Path | None = None
        if req.source_dir is not None:
            source = Path(req.source_dir).expanduser()
            if not source.exists():
                raise HTTPException(status_code=400, detail=f"source dir not found: {source}")
            if not source.is_dir():
                raise HTTPException(status_code=400, detail=f"not a directory: {source}")
            for entry in sorted(source.iterdir()):
                if entry.is_dir() or entry.suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                candidates.append(entry)
            last_dir = source.resolve()
        else:
            assert req.source_paths is not None
            if not req.source_paths:
                raise HTTPException(status_code=400, detail="source_paths must be non-empty")
            for raw in req.source_paths:
                p = Path(raw).expanduser()
                if not p.exists():
                    raise HTTPException(status_code=400, detail=f"file not found: {p}")
                if not p.is_file():
                    raise HTTPException(status_code=400, detail=f"not a file: {p}")
                candidates.append(p)
            last_dir = candidates[0].parent.resolve() if candidates else None

        project = state.load()
        registered: list[str] = []
        skipped: list[str] = []
        for entry in candidates:
            try:
                video = project.register_video(
                    entry, state.project_root, link_mode=req.link_mode  # type: ignore[arg-type]
                )
            except (FileNotFoundError, ValueError) as exc:
                skipped.append(f"{entry.name}: {exc}")
                continue
            registered.append(str(video.path))

        auto_assigned: dict[int, str] = {}
        if req.auto_assign_primary:
            suggestions = project.auto_match(state.project_root)
            for stage_num, video_path in suggestions.items():
                stage = project.stage(stage_num)
                # Only auto-assign primary when the stage has no primary yet.
                if stage.primary() is not None:
                    continue
                project.assign_video(video_path, to_stage_number=stage_num, role="primary")
                auto_assigned[stage_num] = str(video_path)

        if last_dir is not None:
            project.last_scanned_dir = str(last_dir)

        project.save(state.project_root)
        return ScanResponse(
            registered=registered,
            auto_assigned=auto_assigned,
            skipped=skipped,
        )

    @app.post("/api/project/settings")
    def update_settings(req: SettingsRequest) -> JSONResponse:
        """Update storage path overrides. Any None field is left unchanged;
        pass an empty string to clear back to the project-root default.

        If a path field is changing and the *old* directory contains files,
        return 409 with a structured ``non_empty_old_dirs`` payload unless
        ``confirm=True`` is sent. Existing files are not auto-migrated --
        the warning lets the caller surface "you'll be leaving these behind".
        """
        project = state.load()
        update: dict[str, str | None] = {}
        for fname in (
            "raw_dir",
            "audio_dir",
            "trimmed_dir",
            "exports_dir",
            "probes_dir",
            "thumbs_dir",
        ):
            value = getattr(req, fname)
            if value is None:
                continue
            normalized = value.strip() or None
            update[fname] = normalized

        # Detect non-empty old dirs for fields that are actually changing.
        resolver_for = {
            "raw_dir": project.raw_path,
            "audio_dir": project.audio_path,
            "trimmed_dir": project.trimmed_path,
            "exports_dir": project.exports_path,
            "probes_dir": project.probes_path,
            "thumbs_dir": project.thumbs_path,
        }
        non_empty: list[dict[str, object]] = []
        for fname, new_value in update.items():
            if new_value == getattr(project, fname):
                continue  # no change
            old_path = resolver_for[fname](state.project_root)
            if not old_path.exists() or not old_path.is_dir():
                continue
            try:
                file_count = sum(1 for _ in old_path.iterdir())
            except OSError:
                continue
            if file_count == 0:
                continue
            non_empty.append(
                {
                    "field": fname,
                    "path": str(old_path),
                    "file_count": file_count,
                }
            )

        if non_empty and not req.confirm:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "non_empty_old_dirs",
                    "message": (
                        "Changing these paths will leave existing files behind "
                        "in the old location -- splitsmith does not migrate. "
                        "Resend with confirm=true to proceed."
                    ),
                    "dirs": non_empty,
                },
            )

        for fname, value in update.items():
            setattr(project, fname, value)

        for buf_field in ("trim_pre_buffer_seconds", "trim_post_buffer_seconds"):
            value = getattr(req, buf_field)
            if value is None:
                continue
            if value < 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"{buf_field} must be non-negative, got {value}",
                )
            setattr(project, buf_field, value)

        # Make sure each configured directory is creatable; surface a 400
        # rather than failing later in detect-beep / trim / export.
        for resolver in (
            project.raw_path,
            project.audio_path,
            project.trimmed_path,
            project.exports_path,
            project.probes_path,
            project.thumbs_path,
        ):
            target = resolver(state.project_root)
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"cannot create directory {target}: {exc}",
                ) from exc

        project.save(state.project_root)
        return JSONResponse(project.model_dump(mode="json"))

    @app.post("/api/stages/{stage_number}/detect-beep")
    def detect_beep(stage_number: int, force: bool = False) -> JSONResponse:
        """Submit a beep-detection job for the stage's primary.

        Returns a Job snapshot (status=PENDING) immediately; the SPA polls
        ``/api/jobs/{id}`` for progress and refetches ``/api/project`` on
        completion. The job pipeline is detect-beep -> auto-trim (when
        the stage time is known); both happen atomically inside one job.

        Validations are still performed up front and surface as HTTP
        errors before any job is queued, so the SPA can show e.g. a 409
        for "manual override exists" without spinning a useless job.
        """
        project = state.load()
        try:
            stage = project.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        primary = stage.primary()
        if primary is None:
            raise HTTPException(
                status_code=400,
                detail=f"stage {stage_number} has no primary video",
            )
        if primary.beep_source == "manual" and not force:
            raise HTTPException(
                status_code=409,
                detail=(
                    "stage has a manual beep override; pass ?force=true to "
                    "replace it with auto-detected output"
                ),
            )

        def run(handle: JobHandle) -> None:
            handle.update(progress=0.05, message="Loading project...")
            proj = state.load()
            stg = proj.stage(stage_number)
            prim = stg.primary()
            if prim is None:  # pragma: no cover -- defensive; pre-checked above
                raise RuntimeError("primary video disappeared mid-flight")
            source = proj.resolve_video_path(state.project_root, prim.path)
            handle.update(progress=0.15, message="Extracting audio + detecting beep...")
            beep = audio_helpers.detect_primary_beep(
                state.project_root,
                stage_number,
                source,
                project=proj,
            )
            handle.update(progress=0.55, message="Saving beep...")
            prim.beep_time = beep.time
            prim.beep_source = "auto"
            prim.beep_peak_amplitude = beep.peak_amplitude
            prim.beep_duration_ms = beep.duration_ms
            prim.processed["beep"] = True

            trimmed_ok = False
            if stg.time_seconds > 0:
                handle.update(progress=0.55, message="Trimming audit clip (short GOP)...")
                try:
                    audio_helpers.ensure_audit_trim(
                        state.project_root,
                        stage_number,
                        source,
                        prim.beep_time,
                        stg.time_seconds,
                        project=proj,
                    )
                    prim.processed["trim"] = True
                    trimmed_ok = True
                except (FileNotFoundError, audio_helpers.AudioExtractionError) as exc:
                    # Soft failure: the beep is valuable on its own.
                    logger.warning("auto-trim failed for stage %d: %s", stage_number, exc)
                    handle.update(message=f"beep saved; trim failed: {exc}")
            handle.update(progress=0.85, message="Saving project...")
            proj.save(state.project_root)
            # Auto-chain shot detection so the audit screen lands populated.
            # Dedupe handles the case where the user already kicked one off.
            if trimmed_ok:
                if state.jobs.find_active(kind="shot_detect", stage_number=stage_number) is None:
                    state.jobs.submit(
                        kind="shot_detect",
                        stage_number=stage_number,
                        fn=lambda h, n=stage_number: _run_shot_detect(h, n),
                    )
            handle.update(progress=1.0, message="Done")

        existing = state.jobs.find_active(kind="detect_beep", stage_number=stage_number)
        if existing is not None:
            return JSONResponse(existing.model_dump(mode="json"))
        job = state.jobs.submit(kind="detect_beep", stage_number=stage_number, fn=run)
        return JSONResponse(job.model_dump(mode="json"))

    @app.post("/api/stages/{stage_number}/trim")
    def trim_stage(stage_number: int) -> JSONResponse:
        """Submit an audit-mode short-GOP trim job for the stage's primary.

        Returns a Job snapshot. Idempotent on the worker side: when the
        cached MP4 is newer than the source, the job completes near-
        instantly without re-encoding.
        """
        project = state.load()
        try:
            stage = project.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        primary = stage.primary()
        if primary is None:
            raise HTTPException(
                status_code=400,
                detail=f"stage {stage_number} has no primary video",
            )
        if primary.beep_time is None:
            raise HTTPException(
                status_code=400,
                detail=f"stage {stage_number} primary has no beep_time yet",
            )
        if stage.time_seconds <= 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"stage {stage_number} has time_seconds=0; import a "
                    "scoreboard or set the stage time before trimming"
                ),
            )

        existing = state.jobs.find_active(kind="trim", stage_number=stage_number)
        if existing is not None:
            return JSONResponse(existing.model_dump(mode="json"))
        job = state.jobs.submit(
            kind="trim",
            stage_number=stage_number,
            fn=lambda h, n=stage_number: _run_trim_for_stage(h, n),
        )
        return JSONResponse(job.model_dump(mode="json"))

    def _run_trim_for_stage(handle: JobHandle, stage_number: int) -> None:
        """Worker for the audit-mode trim. Auto-chains shot detection on
        success so a re-trim (e.g. after manual beep override) refreshes
        the candidate pool the audit screen reads from."""
        handle.update(progress=0.1, message="Preparing trim...")
        proj = state.load()
        stg = proj.stage(stage_number)
        prim = stg.primary()
        if prim is None or prim.beep_time is None:
            raise RuntimeError("primary or beep disappeared mid-flight")
        source = proj.resolve_video_path(state.project_root, prim.path)
        handle.update(progress=0.3, message="Encoding short-GOP MP4...")
        audio_helpers.ensure_audit_trim(
            state.project_root,
            stage_number,
            source,
            prim.beep_time,
            stg.time_seconds,
            project=proj,
        )
        handle.update(progress=0.85, message="Saving project...")
        prim.processed["trim"] = True
        proj.save(state.project_root)
        if state.jobs.find_active(kind="shot_detect", stage_number=stage_number) is None:
            state.jobs.submit(
                kind="shot_detect",
                stage_number=stage_number,
                fn=lambda h, n=stage_number: _run_shot_detect(h, n),
            )
        handle.update(progress=1.0, message="Done")

    def _run_shot_detect(
        handle: JobHandle, stage_number: int, reset: bool = False
    ) -> None:
        """Worker that runs shot detection on the stage's audit clip.

        Reads the trimmed clip's WAV (extracting it on demand if needed),
        runs ``splitsmith.shot_detect`` over the [beep .. beep+stage] window,
        and merges the resulting candidates into the audit JSON's
        ``_candidates_pending_audit`` block. Existing ``shots[]`` are not
        touched -- the user has authority over what's kept; this just
        refreshes the candidate pool the audit screen draws markers from.

        If ``reset`` is True, ``shots[]`` is wiped before re-seeding so the
        user can start over after a bad beep / detector pass.
        """
        proj = state.load()
        stg = proj.stage(stage_number)
        prim = stg.primary()
        if prim is None or prim.beep_time is None:
            raise RuntimeError(f"stage {stage_number} has no primary or no beep yet")
        if stg.time_seconds <= 0:
            raise RuntimeError(
                f"stage {stage_number} has time_seconds=0; import a "
                "scoreboard before running shot detection"
            )
        source = proj.resolve_video_path(state.project_root, prim.path)

        handle.update(progress=0.1, message="Preparing audio...")
        audit = audio_helpers.ensure_audit_audio(
            state.project_root,
            stage_number,
            source,
            prim.beep_time,
            project=proj,
        )
        beep_in_clip = audit.beep_in_clip if audit.beep_in_clip is not None else prim.beep_time

        handle.update(progress=0.4, message="Detecting shots...")
        audio_array, sr = beep_detect.load_audio(audit.audio_path)
        detected = shot_detect_module.detect_shots(
            audio_array,
            sr,
            beep_in_clip,
            stg.time_seconds,
            ShotDetectConfig(),
        )

        candidates: list[dict[str, Any]] = []
        for i, s in enumerate(detected, start=1):
            candidates.append(
                {
                    "candidate_number": i,
                    "time": round(s.time_absolute, 4),
                    "ms_after_beep": round(s.time_from_beep * 1000),
                    "peak_amplitude": round(float(s.peak_amplitude), 4),
                    "confidence": round(float(s.confidence), 3),
                }
            )

        handle.update(progress=0.85, message="Saving audit JSON...")
        audit_dir = proj.audit_path(state.project_root)
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_file = audit_dir / f"stage{stage_number}.json"

        if audit_file.exists():
            try:
                existing_json = json.loads(audit_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing_json = {}
        else:
            existing_json = {
                "stage_number": stg.stage_number,
                "stage_name": stg.stage_name,
                "stage_time_seconds": stg.time_seconds,
                "beep_time": round(beep_in_clip, 4),
                "shots": [],
            }
        existing_json["_candidates_pending_audit"] = {
            "_note": "Auto-detected by shot_detect via the production UI.",
            "candidates": candidates,
        }
        # Seed shots[] only with high-confidence candidates so the audit
        # screen lands with a sane keep/reject split instead of either
        # extreme. The detector emits many low-confidence echoes and AGC
        # ducks alongside real shots; auto-keeping every candidate produces
        # 50+ spurious "kept" markers on a typical short course. The 0.3
        # cutoff is conservative -- noise events fall well below, real
        # shots typically land >= 0.4. The user can flip individuals
        # afterward; ``reset=true`` re-runs this seeding from scratch.
        SEED_KEEP_CONFIDENCE = 0.3
        if reset:
            existing_json["shots"] = []
        seeded_shots = False
        if not existing_json.get("shots"):
            kept = [c for c in candidates if (c.get("confidence") or 0.0) >= SEED_KEEP_CONFIDENCE]
            existing_json["shots"] = [
                {
                    "shot_number": i,
                    "candidate_number": c["candidate_number"],
                    "time": c["time"],
                    "ms_after_beep": c["ms_after_beep"],
                    "source": "detected",
                }
                for i, c in enumerate(kept, start=1)
            ]
            seeded_shots = True
        events = list(existing_json.get("audit_events") or [])
        events.append(
            {
                "ts": _now_iso(),
                "kind": "shot_detect_run",
                "payload": {
                    "candidate_count": len(candidates),
                    "seeded_shots": seeded_shots,
                },
            }
        )
        existing_json["audit_events"] = events

        # Atomic write + .bak (mirrors put_stage_audit).
        tmp = audit_file.with_suffix(audit_file.suffix + ".tmp")
        backup = audit_file.with_suffix(audit_file.suffix + ".bak")
        tmp.write_text(json.dumps(existing_json, indent=2) + "\n", encoding="utf-8")
        if audit_file.exists():
            if backup.exists():
                backup.unlink()
            audit_file.replace(backup)
        tmp.replace(audit_file)

        prim.processed["shot_detect"] = True
        proj.save(state.project_root)
        handle.update(progress=1.0, message=f"Done -- {len(candidates)} candidates")

    @app.post("/api/stages/{stage_number}/shot-detect")
    def shot_detect_endpoint(stage_number: int, reset: bool = False) -> JSONResponse:
        """Submit a shot-detection job for the stage's audit clip.

        Returns a Job snapshot. Idempotent dedupe via the registry: a second
        click while one is running adopts the existing job. The candidate
        list lands in the audit JSON's ``_candidates_pending_audit`` block,
        which is what the audit screen reads to render markers.

        ``reset=true`` wipes ``shots[]`` first so the user can start over.
        """
        project = state.load()
        try:
            stage = project.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        primary = stage.primary()
        if primary is None:
            raise HTTPException(
                status_code=400,
                detail=f"stage {stage_number} has no primary video",
            )
        if primary.beep_time is None:
            raise HTTPException(
                status_code=400,
                detail=f"stage {stage_number} primary has no beep_time yet",
            )
        if stage.time_seconds <= 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"stage {stage_number} has time_seconds=0; import a "
                    "scoreboard before running shot detection"
                ),
            )

        existing = state.jobs.find_active(kind="shot_detect", stage_number=stage_number)
        if existing is not None:
            return JSONResponse(existing.model_dump(mode="json"))
        job = state.jobs.submit(
            kind="shot_detect",
            stage_number=stage_number,
            fn=lambda h, r=reset: _run_shot_detect(h, stage_number, reset=r),
        )
        return JSONResponse(job.model_dump(mode="json"))

    @app.get("/api/jobs", response_model=list[Job])
    def list_jobs() -> list[Job]:
        """Snapshot of all retained jobs (active + recently finished)."""
        return state.jobs.list()

    @app.get("/api/jobs/{job_id}", response_model=Job)
    def get_job(job_id: str) -> Job:
        """Poll a single job. SPA polls ~1 Hz while a job is active."""
        job = state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        return job

    @app.post("/api/stages/{stage_number}/beep")
    def override_beep(stage_number: int, req: BeepOverrideRequest) -> JSONResponse:
        project = state.load()
        try:
            stage = project.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        primary = stage.primary()
        if primary is None:
            raise HTTPException(
                status_code=400,
                detail=f"stage {stage_number} has no primary video",
            )
        if req.beep_time is None:
            # Clear: back to "no beep yet"
            primary.beep_time = None
            primary.beep_source = None
            primary.beep_peak_amplitude = None
            primary.beep_duration_ms = None
            primary.processed["beep"] = False
            primary.processed["trim"] = False
            primary.processed["shot_detect"] = False
        else:
            if req.beep_time < 0.0:
                raise HTTPException(status_code=400, detail="beep_time must be >= 0")
            primary.beep_time = req.beep_time
            primary.beep_source = "manual"
            primary.processed["beep"] = True
            # Diagnostics from a previous auto-detect are no longer authoritative.
            primary.beep_peak_amplitude = None
            primary.beep_duration_ms = None
            # New beep -> previous trim was cut around the wrong window.
            # processed.trim flips back so the SPA prompts re-trim.
            primary.processed["trim"] = False
            primary.processed["shot_detect"] = False

        # Either branch invalidates the cached trim + audit WAV so the
        # next audit-screen visit can't serve a stale clip cut around an
        # outdated beep. Auto-fires a trim job (which auto-chains shot
        # detection) when we still have enough info to run; otherwise
        # the user gets the badge on the audit screen.
        audio_helpers.invalidate_audit_trim(state.project_root, stage_number, project=project)
        project.save(state.project_root)
        if (
            req.beep_time is not None
            and stage.time_seconds > 0
            and state.jobs.find_active(kind="trim", stage_number=stage_number) is None
        ):
            state.jobs.submit(
                kind="trim",
                stage_number=stage_number,
                fn=lambda h, n=stage_number: _run_trim_for_stage(h, n),
            )
        return JSONResponse(project.model_dump(mode="json"))

    def _resolve_audit_audio(
        project: MatchProject, stage_number: int
    ) -> audio_helpers.AuditAudioResult:
        """Shared resolver for /audio + /peaks: prefers the trimmed clip's
        WAV, falls back to full primary on cache miss / no trim."""
        try:
            stage = project.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        primary = stage.primary()
        if primary is None:
            raise HTTPException(
                status_code=404,
                detail=f"stage {stage_number} has no primary video",
            )
        try:
            return audio_helpers.ensure_audit_audio(
                state.project_root,
                stage_number,
                project.resolve_video_path(state.project_root, primary.path),
                primary.beep_time,
                project=project,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except audio_helpers.AudioExtractionError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/stages/{stage_number}/beep-preview")
    def stage_beep_preview(stage_number: int) -> FileResponse:
        """Serve a tiny MP4 around the primary's detected beep (#27).

        Lets the Ingest screen render an inline preview right after
        detection runs so the user can eyeball "did the detector land on
        the right beep?" without jumping into the audit screen. Cache
        keys on (source mtime/size, beep_time, duration), so a re-detect
        or manual override naturally regenerates the clip.
        """
        project = state.load()
        try:
            stage = project.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        primary = stage.primary()
        if primary is None:
            raise HTTPException(
                status_code=404,
                detail=f"stage {stage_number} has no primary video",
            )
        if primary.beep_time is None:
            raise HTTPException(
                status_code=404,
                detail=f"stage {stage_number} has no beep_time yet",
            )
        source = project.resolve_video_path(state.project_root, primary.path)
        if not source.is_file():
            raise HTTPException(
                status_code=404,
                detail=f"primary video missing on disk: {source}",
            )
        thumbs_dir = project.thumbs_path(state.project_root)
        try:
            clip = thumbnail_helpers.ensure_clip(
                source,
                cache_dir=thumbs_dir,
                center_time=float(primary.beep_time),
                duration_s=1.0,
                width=480,
            )
        except thumbnail_helpers.ThumbnailError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return FileResponse(clip, media_type="video/mp4", filename=clip.name)

    @app.get("/api/stages/{stage_number}/audio")
    def stage_audio(stage_number: int) -> FileResponse:
        """Serve the audit-clip WAV for ``stage_number``.

        Prefers ``stage<N>_audit.wav`` (extracted from the short-GOP trimmed
        MP4 produced by Sub 5 / #16) so the waveform timeline matches what
        the user is auditing. Falls back to the full ``stage<N>_primary.wav``
        when no trimmed clip exists yet -- the SPA surfaces this with a
        "trim required" hint.
        """
        project = state.load()
        result = _resolve_audit_audio(project, stage_number)
        return FileResponse(
            result.audio_path,
            media_type="audio/wav",
            filename=result.audio_path.name,
        )

    @app.get("/api/stages/{stage_number}/peaks")
    def stage_peaks(
        stage_number: int,
        bins: int = Query(default=1200, ge=16, le=8192),
    ) -> JSONResponse:
        """Return ``bins`` peak magnitudes (0..1) for the stage's audit clip.

        The peaks come from whichever WAV ``_resolve_audit_audio`` picked
        (trimmed clip if available, full source otherwise). The response
        carries ``beep_time`` translated into the served clip's local
        timeline + ``trimmed`` so the SPA can render the beep marker
        correctly and warn when the user is auditing an untrimmed source.
        """
        project = state.load()
        audit = _resolve_audit_audio(project, stage_number)
        peaks = waveform_helpers.ensure_peaks(audit.audio_path, bins)
        payload = peaks.model_dump(mode="json")
        payload["beep_time"] = audit.beep_in_clip
        payload["trimmed"] = audit.trimmed
        return JSONResponse(payload)

    @app.get("/api/stages/{stage_number}/audit")
    def get_stage_audit(stage_number: int) -> JSONResponse:
        """Return the stage's audit JSON (issue #15) if one has been written.

        Lives at ``<project>/audit/stage<N>.json`` -- the same path the
        existing audit-prep / audit-apply flow uses. 404 when no audit file
        exists yet (the audit screen treats this as "fresh -- start from
        candidates if any, otherwise empty markers").
        """
        project = state.load()
        try:
            project.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        audit_file = project.audit_path(state.project_root) / f"stage{stage_number}.json"
        if not audit_file.exists():
            raise HTTPException(
                status_code=404,
                detail=f"no audit JSON yet for stage {stage_number}",
            )
        try:
            payload = json.loads(audit_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=500, detail=f"audit read failed: {exc}") from exc
        return JSONResponse(payload)

    @app.put("/api/stages/{stage_number}/audit")
    def put_stage_audit(stage_number: int, payload: dict[str, Any]) -> JSONResponse:
        """Atomically write the audit JSON for a stage.

        Layout follows the existing audit-prep / audit-apply convention plus
        an ``audit_events`` append-only log (issue #15). The SPA owns the
        document shape -- this endpoint just verifies it's a JSON object,
        writes it under ``<project>/audit/stage<N>.json``, and keeps the
        previous version as ``stage<N>.json.bak`` so a bad save can be
        recovered.

        The atomic-write pattern is: serialize -> ``stage<N>.json.tmp`` ->
        rename existing final to ``.bak`` (replacing any prior backup) ->
        rename ``.tmp`` to final. A crashed process never leaves the SPA
        without a readable JSON.
        """
        project = state.load()
        try:
            project.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        audit_dir = project.audit_path(state.project_root)
        audit_dir.mkdir(parents=True, exist_ok=True)
        target = audit_dir / f"stage{stage_number}.json"
        tmp = target.with_suffix(target.suffix + ".tmp")
        backup = target.with_suffix(target.suffix + ".bak")
        try:
            tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            if target.exists():
                if backup.exists():
                    backup.unlink()
                target.replace(backup)
            tmp.replace(target)
        except OSError as exc:
            if tmp.exists():
                tmp.unlink()
            raise HTTPException(status_code=500, detail=f"audit write failed: {exc}") from exc
        return JSONResponse(payload)

    # ----------------------------------------------------------------------
    # Fixture-review endpoints (closes #19 -- the old splitsmith.review_server
    # standalone). The /review SPA route reads a single audit fixture (JSON
    # + sibling WAV + optional video) and edits it in place. No project
    # context, no stages, no jobs. Localhost-only convention applies; paths
    # are passed by the user via CLI / query string.
    # ----------------------------------------------------------------------

    def _resolve_fixture_path(path: str) -> Path:
        """Validate + resolve a fixture path the SPA passed in. Localhost
        only -- we trust the user's machine but still refuse non-existent
        files cleanly so the SPA can show a 404 message."""
        target = Path(path).expanduser()
        try:
            resolved = target.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise HTTPException(status_code=404, detail=f"fixture not found: {target}") from exc
        if not resolved.is_file():
            raise HTTPException(status_code=400, detail=f"fixture is not a file: {resolved}")
        return resolved

    @app.get("/api/fixture/audit")
    def get_fixture_audit(path: str = Query(...)) -> JSONResponse:
        """Read the fixture JSON at ``path``."""
        target = _resolve_fixture_path(path)
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=500, detail=f"fixture read failed: {exc}") from exc
        return JSONResponse(payload)

    @app.put("/api/fixture/audit")
    def put_fixture_audit(
        path: str = Query(...),
        payload: dict[str, Any] = Body(...),  # noqa: B008  FastAPI default-arg DI
    ) -> JSONResponse:
        """Atomically rewrite a fixture JSON. Same .bak backup pattern as
        ``/api/stages/{n}/audit``: serialize -> .tmp -> rename existing
        target to .bak (replacing any prior backup) -> rename .tmp to
        target. A crashed write never leaves the SPA without a JSON."""
        target = _resolve_fixture_path(path)
        tmp = target.with_suffix(target.suffix + ".tmp")
        backup = target.with_suffix(target.suffix + ".bak")
        try:
            tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            if backup.exists():
                backup.unlink()
            target.replace(backup)
            tmp.replace(target)
        except OSError as exc:
            if tmp.exists():
                tmp.unlink()
            raise HTTPException(status_code=500, detail=f"fixture write failed: {exc}") from exc
        return JSONResponse(payload)

    @app.get("/api/fixture/peaks")
    def get_fixture_peaks(
        path: str = Query(...),
        bins: int = Query(default=1200, ge=16, le=8192),
    ) -> JSONResponse:
        """Compute peaks for the fixture's sibling WAV (``<path>.with_suffix('.wav')``).

        Returns the same payload as the project peaks endpoint so the
        Review page can reuse the same client-side rendering, including
        the ``beep_time`` and ``trimmed`` fields. The fixture is treated
        as already-trimmed (clip-local timeline) by convention.
        """
        target = _resolve_fixture_path(path)
        wav = target.with_suffix(".wav")
        if not wav.exists():
            raise HTTPException(
                status_code=404,
                detail=f"fixture audio not found: {wav}",
            )
        result = waveform_helpers.ensure_peaks(wav, bins)
        # The fixture JSON owns the canonical beep_time; we expose it via
        # the audit endpoint instead of re-deriving here. peaks payload
        # gets ``trimmed=true`` since fixtures are clip-local.
        payload = result.model_dump(mode="json")
        try:
            fixture_json = json.loads(target.read_text(encoding="utf-8"))
            payload["beep_time"] = fixture_json.get("beep_time")
        except (OSError, json.JSONDecodeError):
            payload["beep_time"] = None
        payload["trimmed"] = True
        return JSONResponse(payload)

    @app.get("/api/fixture/audio")
    def get_fixture_audio(path: str = Query(...)) -> FileResponse:
        """Serve the WAV alongside ``path`` (same stem, .wav extension)."""
        target = _resolve_fixture_path(path)
        wav = target.with_suffix(".wav")
        if not wav.exists():
            raise HTTPException(
                status_code=404,
                detail=f"fixture audio not found: {wav}",
            )
        return FileResponse(wav, media_type="audio/wav", filename=wav.name)

    @app.get("/api/fixture/video")
    def get_fixture_video(path: str = Query(...)) -> FileResponse:
        """Serve an arbitrary video file the user binds to a fixture.

        The CLI's ``splitsmith review --video <path>`` passes this through
        to the SPA via a query param. Localhost-only; we don't restrict
        the path beyond "must exist + must be a file".
        """
        target = _resolve_fixture_path(path)
        media_type = "video/mp4" if target.suffix.lower() == ".mp4" else "application/octet-stream"
        return FileResponse(target, media_type=media_type, filename=target.name)

    @app.get("/api/videos/stream")
    def stream_video(path: str = Query(...)) -> FileResponse:
        """Serve a registered video file with HTTP Range support.

        For the primary of a stage, prefers the short-GOP trimmed MP4
        produced by Sub 5 / #16 (``<trimmed>/stage<N>_trimmed.mp4``). The
        re-encoded clip seeks frame-accurately, which is what makes the
        audit screen's drag-scrubbing feel responsive. Secondaries fall
        through to their source file -- per-video trim runs aren't wired
        through the production UI yet.

        Validates that ``path`` matches a video registered to the project
        (any stage, any role, or unassigned) so the endpoint cannot be
        used as a generic file-read primitive.
        """
        project = state.load()
        located = project.find_video(Path(path))
        if located is None:
            raise HTTPException(
                status_code=404,
                detail=f"video not registered with project: {path}",
            )
        stage, video = located

        served_path: Path | None = None
        if stage is not None and video.role == "primary":
            trimmed = audio_helpers.trimmed_primary_path(
                state.project_root, stage.stage_number, project=project
            )
            if trimmed.exists():
                served_path = trimmed.resolve()
        if served_path is None:
            served_path = project.resolve_video_path(state.project_root, video.path).resolve()
            if not served_path.is_file():
                raise HTTPException(
                    status_code=404,
                    detail=f"video missing on disk: {served_path}",
                )

        media_type = (
            "video/mp4" if served_path.suffix.lower() == ".mp4" else "application/octet-stream"
        )
        return FileResponse(served_path, media_type=media_type, filename=served_path.name)

    @app.get("/api/fs/list", response_model=FsListing)
    def fs_list(
        path: str | None = Query(default=None),
        probe: bool = Query(default=False),
    ) -> FsListing:
        """List a directory's children for the in-app folder picker.

        - ``path=None`` returns the user's home directory plus suggested-start
          bookmarks (last scanned, ~/Movies, ~/Videos, ~).
        - Hidden entries (dot-prefixed) are skipped.
        - Symlinks are resolved before listing; broken symlinks are silently
          dropped from the entries list.
        - Per-directory ``video_count`` is computed by a single shallow scan.
        - When ``probe=true``, video entries get ``duration`` + thumbnail
          generation via ffprobe / ffmpeg, bounded by a wall-clock budget
          (~5 s) so a USB-mounted directory with hundreds of clips doesn't
          stall the picker. Entries past the budget come back with null
          fields and a per-row Generate affordance in the SPA. Cached
          probes / thumbnails always populate -- the budget only gates the
          first-time work.
        """
        project = state.load()
        target = Path(path).expanduser() if path else _default_start(project.last_scanned_dir)
        try:
            target = target.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise HTTPException(status_code=404, detail=f"path not found: {target}") from exc
        if not target.is_dir():
            raise HTTPException(status_code=400, detail=f"not a directory: {target}")

        entries: list[FsEntry] = []
        try:
            children = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        probes_dir = project.probes_path(state.project_root)
        thumbs_dir = project.thumbs_path(state.project_root)
        budget_deadline = time.monotonic() + 5.0  # wall-clock budget for new probes

        for child in children:
            if child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    video_count = _count_videos_shallow(child)
                    entries.append(FsEntry(name=child.name, kind="dir", video_count=video_count))
                elif child.is_file():
                    is_video = child.suffix.lower() in VIDEO_EXTENSIONS
                    stat = child.stat()
                    duration: float | None = None
                    thumbnail_url: str | None = None
                    if is_video:
                        duration, thumbnail_url = _video_metadata_for(
                            child,
                            probes_dir=probes_dir,
                            thumbs_dir=thumbs_dir,
                            allow_new=probe and time.monotonic() < budget_deadline,
                            duration_for_thumb=None,
                        )
                    entries.append(
                        FsEntry(
                            name=child.name,
                            kind="video" if is_video else "file",
                            size_bytes=stat.st_size,
                            mtime=stat.st_mtime,
                            duration=duration,
                            thumbnail_url=thumbnail_url,
                        )
                    )
            except (PermissionError, OSError):
                # Broken symlink, permission issue on a child -- skip rather
                # than fail the whole listing.
                continue

        parent = str(target.parent) if target.parent != target else None
        suggested = _suggested_starts(project.last_scanned_dir)

        return FsListing(
            path=str(target),
            parent=parent,
            entries=entries,
            suggested_starts=suggested,
        )

    @app.get("/api/fs/probe")
    def fs_probe(path: str = Query(...)) -> JSONResponse:
        """Probe a single video file on demand: ffprobe + thumbnail extraction.

        Used by the SPA when a picker row came back with null fields (the
        list-time budget was exhausted, or ``probe=true`` wasn't passed).
        Cached results are returned without re-running the binaries.
        """
        target = Path(path).expanduser()
        try:
            target = target.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise HTTPException(status_code=404, detail=f"path not found: {target}") from exc
        if not target.is_file():
            raise HTTPException(status_code=400, detail=f"not a file: {target}")
        if target.suffix.lower() not in VIDEO_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"not a video: {target}")

        project = state.load()
        probes_dir = project.probes_path(state.project_root)
        thumbs_dir = project.thumbs_path(state.project_root)
        duration, thumbnail_url = _video_metadata_for(
            target,
            probes_dir=probes_dir,
            thumbs_dir=thumbs_dir,
            allow_new=True,
            duration_for_thumb=None,
        )
        return JSONResponse({"duration": duration, "thumbnail_url": thumbnail_url})

    @app.get("/api/thumbnails/{cache_key}.jpg", include_in_schema=False)
    def serve_thumbnail(cache_key: str) -> FileResponse:
        """Serve a cached thumbnail by its content-addressed key.

        Keys are 16-char hex from :func:`video_probe.source_cache_key`. We
        validate the key shape so we never accept an arbitrary path that
        could escape the thumbs directory.
        """
        if not cache_key.isalnum() or len(cache_key) > 32:
            raise HTTPException(status_code=400, detail="invalid thumbnail key")
        project = state.load()
        thumbs_dir = project.thumbs_path(state.project_root)
        candidate = thumbs_dir / f"{cache_key}.jpg"
        if not candidate.exists():
            raise HTTPException(status_code=404, detail="thumbnail not cached")
        return FileResponse(candidate, media_type="image/jpeg", filename=candidate.name)

    @app.post("/api/videos/auto-match")
    def auto_match() -> JSONResponse:
        project = state.load()
        suggestions = project.auto_match(state.project_root)
        return JSONResponse({str(stage_num): str(path) for stage_num, path in suggestions.items()})

    @app.post("/api/videos/remove")
    def remove_video(req: RemoveVideoRequest) -> JSONResponse:
        """Remove a registered video and clean up its caches.

        Walks the :class:`RemovalPlan` returned by the model: unlinks the
        symlink under ``raw_dir`` (the source on USB / external storage is
        never touched), and clears the audio + trimmed caches if the removed
        video was a primary that had been processed. When ``reset_audit`` is
        set and the video was a primary, the per-stage audit JSON is
        deleted too -- otherwise audit data is preserved so a re-ingest of
        the same stage with a different file picks up where the user left
        off.
        """
        project = state.load()
        try:
            plan = project.remove_video(
                Path(req.video_path),
                state.project_root,
                reset_audit=req.reset_audit,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        # Symlink under raw_dir: remove only if it's actually a symlink we
        # created. If the user pointed raw_dir at the source and the link is
        # in fact the original file, leave it alone.
        try:
            if plan.raw_link_path.is_symlink():
                plan.raw_link_path.unlink()
            elif plan.raw_link_path.exists():
                # Treat as a copy splitsmith placed (link_mode="copy"). We
                # only delete files inside raw_dir; never anything beyond.
                raw_dir = project.raw_path(state.project_root).resolve()
                try:
                    plan.raw_link_path.resolve().relative_to(raw_dir)
                    plan.raw_link_path.unlink()
                except ValueError:
                    logger.debug(
                        "skipping raw cleanup; %s is outside raw_dir %s",
                        plan.raw_link_path,
                        raw_dir,
                    )
        except OSError as exc:
            logger.warning("could not unlink %s: %s", plan.raw_link_path, exc)

        for cache_path in (plan.audio_cache_path, plan.trimmed_cache_path):
            if cache_path is None:
                continue
            try:
                cache_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("could not remove cache %s: %s", cache_path, exc)

        if plan.audit_path is not None:
            try:
                plan.audit_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("could not remove audit %s: %s", plan.audit_path, exc)

        project.save(state.project_root)
        return JSONResponse(
            {
                "project": project.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
            }
        )

    @app.post("/api/assignments/move")
    def move_assignment(req: MoveRequest) -> JSONResponse:
        project = state.load()
        try:
            project.assign_video(
                Path(req.video_path),
                to_stage_number=req.to_stage_number,
                role=req.role,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        project.save(state.project_root)
        return JSONResponse(project.model_dump(mode="json"))

    # ----------------------------------------------------------------------
    # Static asset serving (SPA)
    # ----------------------------------------------------------------------
    #
    # In dev, the user runs ``npm run dev`` in ``ui_static/`` and Vite serves
    # the SPA on its own port, proxying ``/api/*`` to this backend. In that
    # mode ``STATIC_DIR`` may not exist; the API routes still work and the
    # browser hits the Vite dev server directly.
    #
    # In prod, ``ui_static/dist`` is built and we serve it here.

    if STATIC_DIR.exists():
        # Mount built assets at /assets (matches Vite's default output).
        assets_dir = STATIC_DIR / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        # SPA fallback: any non-API route returns index.html so the React
        # router can handle it client-side.
        @app.get("/{full_path:path}", include_in_schema=False)
        def spa_fallback(full_path: str) -> FileResponse:
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="api route not found")
            index = STATIC_DIR / "index.html"
            if not index.exists():
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "SPA bundle not built. Run `npm run build` in "
                        "src/splitsmith/ui_static/ or use `npm run dev`."
                    ),
                )
            return FileResponse(index)

    return app


def _default_start(last_scanned_dir: str | None) -> Path:
    """Pick a default starting directory for the folder picker.

    Preference: last-scanned-dir (if it still exists) → ~/Movies → ~/Videos → ~.
    """
    if last_scanned_dir:
        p = Path(last_scanned_dir).expanduser()
        if p.is_dir():
            return p
    home = Path.home()
    for candidate in (home / "Movies", home / "Videos"):
        if candidate.is_dir():
            return candidate
    return home


def _suggested_starts(last_scanned_dir: str | None) -> list[str]:
    """Bookmarks the folder picker shows in a sidebar."""
    home = Path.home()
    candidates = []
    if last_scanned_dir:
        p = Path(last_scanned_dir).expanduser()
        if p.is_dir():
            candidates.append(str(p))
    for c in (home, home / "Movies", home / "Videos", home / "Downloads", home / "Desktop"):
        if c.is_dir():
            candidates.append(str(c))
    # De-duplicate while preserving order.
    seen: set[str] = set()
    result = []
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        result.append(c)
    return result


def _video_metadata_for(
    source: Path,
    *,
    probes_dir: Path,
    thumbs_dir: Path,
    allow_new: bool,
    duration_for_thumb: float | None,
) -> tuple[float | None, str | None]:
    """Return ``(duration, thumbnail_url)`` for a source video.

    Always uses cached results when available. Runs ffprobe / ffmpeg only
    when ``allow_new`` is True (i.e. the caller wants on-demand work and is
    OK paying the cost). Failures are swallowed -- the picker's contract is
    "best effort, never block the listing".
    """
    duration: float | None = None
    cached_probe = video_probe.cached(source, probes_dir)
    if cached_probe is not None:
        duration = cached_probe.duration
    elif allow_new:
        try:
            result = video_probe.probe(source, cache_dir=probes_dir)
            duration = result.duration
        except video_probe.ProbeError as exc:
            logger.debug("probe failed for %s: %s", source, exc)

    thumbnail_url: str | None = None
    cached_thumb = thumbnail_helpers.cached(source, thumbs_dir)
    if cached_thumb is not None:
        thumbnail_url = f"/api/thumbnails/{cached_thumb.stem}.jpg"
    elif allow_new:
        try:
            t_dur = duration_for_thumb if duration_for_thumb is not None else duration
            extracted = thumbnail_helpers.ensure(
                source,
                cache_dir=thumbs_dir,
                duration=t_dur,
            )
            thumbnail_url = f"/api/thumbnails/{extracted.stem}.jpg"
        except thumbnail_helpers.ThumbnailError as exc:
            logger.debug("thumbnail failed for %s: %s", source, exc)

    return duration, thumbnail_url


def _count_videos_shallow(directory: Path, *, cap: int = 200) -> int:
    """Count video files directly inside ``directory`` (no recursion).

    Capped at ``cap`` to keep the picker responsive on huge folders. The UI
    only uses this as a ranking hint ("this folder has videos in it").
    """
    count = 0
    try:
        for entry in directory.iterdir():
            if entry.is_file() and entry.suffix.lower() in VIDEO_EXTENSIONS:
                count += 1
                if count >= cap:
                    return count
    except (PermissionError, OSError):
        return 0
    return count


def serve(
    *,
    project_root: Path,
    project_name: str,
    host: str = "127.0.0.1",
    port: int = 5174,
    reload: bool = False,
) -> None:
    """Boot uvicorn synchronously. Used by the ``splitsmith ui`` CLI command."""
    import uvicorn

    _ensure_ui_built()

    if reload:
        # Reload mode requires an importable factory; pass the path string and
        # use environment variables to feed the project context. Simpler: just
        # log a warning and run without reload for now. Reload is a dev
        # convenience that we can wire properly when we have a real config.
        logger.warning("reload=True is not supported yet; running without reload")

    app = create_app(project_root=project_root, project_name=project_name)
    uvicorn.run(app, host=host, port=port, log_level="info")
