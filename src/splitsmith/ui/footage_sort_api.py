"""Sort a shared footage folder across the match's shooters (spec 2026-10-01).

Local mode only: every route 404s hosted. A scan walks one parent folder
(club mates' subfolders), probes every video once, and stores the result in
``<match>/footage_sort/<scan_id>.json`` with the user's anchors, overrides
and check marks, so the review survives a reload. Every read re-runs the
pure engine (:func:`splitsmith.footage_sort.propose`) over the stored scan,
so the rules live in one place. Import registers each checked clip under its
shooter through the same ``register_video`` / ``assign_video`` the scan
route uses, queues beeps through the same hook, and writes
``<scan_id>-report.json``: every clip, the decision, who made it, the
outcome. Local files, never a ``state_docs`` kind (that would enter the sync
manifest).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import match_model, thumbnail
from ..config import Config, FootageSortConfig
from ..footage_sort import (
    Anchor,
    ClipProposal,
    Override,
    ShooterScorecards,
    SortCamera,
    SortClip,
    propose,
)
from ..match_project import VIDEO_EXTENSIONS, atomic_write_json
from ..runtime import ENV_CONFIG_FILE
from ..video_match import recording_start_from_tags

logger = logging.getLogger(__name__)

router = APIRouter()

JOB_KIND = "footage_sort_scan"
SORT_DIR = "footage_sort"

ScanStatus = Literal["scanning", "ready", "failed", "imported"]


class ScannedClip(BaseModel):
    """One probed video of the scan. ``imported_by`` is the shooter whose
    project already has this file; such a clip helps fit its camera's clock
    but is never imported twice."""

    clip: SortClip
    path: str
    imported_by: str | None = None
    thumbnail: bool = False


class ScanRecord(BaseModel):
    """``<match>/footage_sort/<scan_id>.json``."""

    scan_id: str
    source_dir: str
    created_at: datetime
    status: ScanStatus = "scanning"
    error: str | None = None
    clips: list[ScannedClip] = Field(default_factory=list)
    # Files that are not video (photos, sidecars), counted for the report.
    skipped_files: int = 0
    anchors: list[Anchor] = Field(default_factory=list)
    overrides: list[Override] = Field(default_factory=list)
    # The user's check marks; a clip absent here is checked when the engine
    # is confident (``high``) and the clip is not imported yet.
    checked: dict[str, bool] = Field(default_factory=dict)


class ShooterRef(BaseModel):
    key: str
    name: str
    stages: list[int]


class ClipView(BaseModel):
    clip_id: str
    index: int
    folder: str
    filename: str
    start: datetime | None
    duration: float | None
    model: str | None
    imported_by: str | None
    thumbnail: bool
    checked: bool
    proposal: ClipProposal


class SortView(BaseModel):
    scan_id: str
    status: ScanStatus
    error: str | None
    source_dir: str
    skipped_files: int
    shooters: list[ShooterRef]
    cameras: list[SortCamera]
    clips: list[ClipView]
    # The stored decisions, so the page edits them rather than rebuilding.
    anchors: list[Anchor]
    overrides: list[Override]
    user_checked: dict[str, bool]


class ScanRequest(BaseModel):
    source_dir: str


class DecisionsRequest(BaseModel):
    anchors: list[Anchor] = Field(default_factory=list)
    overrides: list[Override] = Field(default_factory=list)
    checked: dict[str, bool] = Field(default_factory=dict)


class ImportRequest(BaseModel):
    link_mode: Literal["symlink", "copy"] = "symlink"


class ImportedClip(BaseModel):
    clip_id: str
    shooter: str
    stage: int
    role: str
    path: str


class ImportResponse(BaseModel):
    imported: list[ImportedClip]
    # clip_id -> why it was not imported.
    not_imported: dict[str, str]
    report: str


def _local_only() -> None:
    from .server import _hosted_mode_active

    if _hosted_mode_active():
        raise HTTPException(status_code=404, detail="not found")


def _sort_dir(state: Any) -> Path:
    root: Path = state.match_root
    return root / SORT_DIR


def _record_path(state: Any, scan_id: str) -> Path:
    if not scan_id.isalnum():
        raise HTTPException(status_code=404, detail="scan not found")
    return _sort_dir(state) / f"{scan_id}.json"


def _load(state: Any, scan_id: str) -> ScanRecord:
    path = _record_path(state, scan_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="scan not found")
    return ScanRecord.model_validate_json(path.read_text(encoding="utf-8"))


def _save(state: Any, record: ScanRecord) -> None:
    atomic_write_json(_record_path(state, record.scan_id), record.model_dump(mode="json"))


def _shooters(state: Any) -> tuple[list[ShooterScorecards], list[ShooterRef]]:
    """Every shooter of the match with the scorecards their project holds."""
    cards: list[ShooterScorecards] = []
    refs: list[ShooterRef] = []
    for slug in match_model.Match.load(state.match_root).shooters:
        project = state.shooter_project(slug)
        name = project.competitor_name or project.name or slug
        cards.append(
            ShooterScorecards(
                key=slug,
                name=name,
                scorecards={
                    s.stage_number: s.scorecard_updated_at
                    for s in project.stages
                    if s.scorecard_updated_at is not None
                },
            )
        )
        refs.append(ShooterRef(key=slug, name=name, stages=[s.stage_number for s in project.stages]))
    return cards, refs


def _view(state: Any, record: ScanRecord) -> SortView:
    cards, refs = _shooters(state)
    config = _config()
    proposal = propose(
        [c.clip for c in record.clips],
        cards,
        anchors=record.anchors,
        overrides=record.overrides,
        config=config,
    )
    by_id = {p.clip_id: p for p in proposal.clips}
    anchored = {c.key for c in proposal.cameras if c.clock == "anchored"}
    clips = []
    for index, scanned in enumerate(record.clips):
        p = by_id[scanned.clip.clip_id]
        # Pre-checked: what the engine is sure of, and what the user's own
        # anchor placed (they answered for that camera already).
        sure = p.confidence == "high" or (p.confidence == "medium" and p.camera_key in anchored)
        default = sure and scanned.imported_by is None
        importable = p.shooter is not None and p.stage is not None and scanned.imported_by is None
        clips.append(
            ClipView(
                clip_id=scanned.clip.clip_id,
                index=index,
                folder=scanned.clip.folder,
                filename=scanned.clip.filename,
                start=scanned.clip.start,
                duration=scanned.clip.duration,
                model=scanned.clip.model or scanned.clip.make,
                imported_by=scanned.imported_by,
                thumbnail=scanned.thumbnail,
                checked=importable and record.checked.get(scanned.clip.clip_id, default),
                proposal=p,
            )
        )
    return SortView(
        scan_id=record.scan_id,
        status=record.status,
        error=record.error,
        source_dir=record.source_dir,
        skipped_files=record.skipped_files,
        shooters=refs,
        cameras=proposal.cameras,
        clips=clips,
        anchors=record.anchors,
        overrides=record.overrides,
        user_checked=record.checked,
    )


def _config() -> FootageSortConfig:
    """From ``SPLITSMITH_CONFIG`` when set, like the coach thresholds."""
    raw = os.environ.get(ENV_CONFIG_FILE, "").strip()
    if not raw:
        return FootageSortConfig()
    return Config.load(Path(raw).expanduser()).footage_sort


def probe_clip(path: Path, *, timeout: float = 8.0) -> dict[str, Any]:
    """One ffprobe: duration, the embedded start, make and model. Missing
    values are ``None``; a failed probe yields all ``None``."""
    if not shutil.which("ffprobe"):
        return {}
    cmd = [
        "ffprobe",
        "-hide_banner",
        "-loglevel",
        "error",
        "-print_format",
        "json",
        "-show_entries",
        "format=duration:format_tags",
        str(path),
    ]
    try:
        completed = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)
        fmt = json.loads(completed.stdout).get("format") or {}
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
        return {}
    tags = {str(k).lower(): str(v) for k, v in (fmt.get("tags") or {}).items()}
    try:
        duration = float(fmt["duration"]) if fmt.get("duration") is not None else None
    except (TypeError, ValueError):
        duration = None
    return {
        "start": recording_start_from_tags(tags),
        "duration": duration,
        "make": tags.get("com.apple.quicktime.make") or tags.get("make"),
        "model": tags.get("com.apple.quicktime.model") or tags.get("model"),
    }


def _registered_sources(state: Any) -> dict[Path, str]:
    """Resolved source path -> the shooter whose project has it."""
    out: dict[Path, str] = {}
    for slug in match_model.Match.load(state.match_root).shooters:
        project = state.shooter_project(slug)
        root = state.shooter_root(slug)
        videos = [*project.unassigned_videos, *(v for s in project.stages for v in s.videos)]
        for video in videos:
            try:
                out[project.resolve_video_path(root, video.path).resolve()] = slug
            except OSError:
                continue
    return out


def run_footage_sort_scan(handle: Any, *, state: Any, scan_id: str) -> None:
    """Job body for ``footage_sort_scan``: walk, probe, thumbnail, save."""
    record = _load(state, scan_id)
    source = Path(record.source_dir)
    try:
        files = sorted(p for p in source.rglob("*") if p.is_file() and not p.name.startswith("."))
        videos = [p for p in files if p.suffix.lower() in VIDEO_EXTENSIONS]
        record.skipped_files = len(files) - len(videos)
        registered = _registered_sources(state)
        thumbs = _sort_dir(state) / "thumbs"
        scanned: list[ScannedClip] = []
        for i, path in enumerate(videos):
            handle.check_cancel()
            handle.update(progress=i / max(len(videos), 1), message=f"Reading {path.name}")
            meta = probe_clip(path)
            folder = str(path.parent.relative_to(source)) if path.parent != source else ""
            clip = SortClip(
                clip_id=str(path.relative_to(source)),
                folder=folder,
                filename=path.name,
                start=meta.get("start"),
                duration=meta.get("duration"),
                make=meta.get("make"),
                model=meta.get("model"),
            )
            has_thumb = False
            try:
                thumbnail.ensure(path, cache_dir=thumbs, duration=meta.get("duration"))
                has_thumb = True
            except Exception:  # noqa: BLE001 -- a thumbnail never fails the scan
                logger.debug("footage sort: no thumbnail for %s", path, exc_info=True)
            scanned.append(
                ScannedClip(
                    clip=clip,
                    path=str(path),
                    imported_by=registered.get(path.resolve()),
                    thumbnail=has_thumb,
                )
            )
        record.clips = scanned
        record.status = "ready"
        handle.update(progress=1.0, message=f"{len(scanned)} videos read")
    except Exception as exc:
        record.status = "failed"
        record.error = str(exc)
        _save(state, record)
        raise
    _save(state, record)


@router.post("/api/match/footage-sort/scan")
async def start_scan(req: ScanRequest, request: Request) -> dict[str, Any]:
    _local_only()
    state = request.app.state.splitsmith_state
    source = Path(req.source_dir).expanduser()
    if not source.is_dir():
        raise HTTPException(status_code=400, detail=f"not a folder: {source}")
    if len(match_model.Match.load(state.match_root).shooters) == 0:
        raise HTTPException(status_code=409, detail="add the match's shooters first")
    record = ScanRecord(
        scan_id=uuid.uuid4().hex[:12], source_dir=str(source.resolve()), created_at=datetime.now(UTC)
    )
    _save(state, record)
    job = await state.jobs.submit(kind=JOB_KIND, args={"scan_id": record.scan_id})
    return {"scan_id": record.scan_id, "job": job.model_dump(mode="json")}


@router.get("/api/match/footage-sort/{scan_id}", response_model=SortView)
def get_scan(scan_id: str, request: Request) -> SortView:
    _local_only()
    state = request.app.state.splitsmith_state
    return _view(state, _load(state, scan_id))


@router.put("/api/match/footage-sort/{scan_id}/decisions", response_model=SortView)
def put_decisions(scan_id: str, req: DecisionsRequest, request: Request) -> SortView:
    _local_only()
    state = request.app.state.splitsmith_state
    record = _load(state, scan_id)
    if record.status != "ready":
        raise HTTPException(status_code=409, detail=f"scan is {record.status}")
    known = {c.clip.clip_id for c in record.clips}
    unknown = [a.clip_id for a in req.anchors if a.clip_id not in known] + [
        o.clip_id for o in req.overrides if o.clip_id not in known
    ]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown clips: {unknown[:3]}")
    record.anchors, record.overrides = req.anchors, req.overrides
    record.checked = {k: v for k, v in req.checked.items() if k in known}
    _save(state, record)
    return _view(state, record)


@router.get("/api/match/footage-sort/{scan_id}/thumbs/{index}.jpg")
def get_thumbnail(scan_id: str, index: int, request: Request) -> FileResponse:
    _local_only()
    state = request.app.state.splitsmith_state
    record = _load(state, scan_id)
    if not 0 <= index < len(record.clips):
        raise HTTPException(status_code=404, detail="no such clip")
    hit = thumbnail.cached(Path(record.clips[index].path), _sort_dir(state) / "thumbs")
    if hit is None:
        raise HTTPException(status_code=404, detail="no thumbnail")
    return FileResponse(hit, media_type="image/jpeg")


@router.get("/api/match/footage-sort/{scan_id}/clips/{index}/video")
def get_clip_video(scan_id: str, index: int, request: Request) -> FileResponse:
    """The scanned file itself, so the review can play a clip before it is
    imported. Only files the scan recorded; range requests are served."""
    _local_only()
    state = request.app.state.splitsmith_state
    record = _load(state, scan_id)
    if not 0 <= index < len(record.clips):
        raise HTTPException(status_code=404, detail="no such clip")
    path = Path(record.clips[index].path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file is gone")
    return FileResponse(path)


@router.post("/api/match/footage-sort/{scan_id}/import", response_model=ImportResponse)
async def import_scan(scan_id: str, req: ImportRequest, request: Request) -> ImportResponse:
    """Register and assign every checked clip under its shooter.

    The user confirmed each (shooter, stage) on the review page, so a clip
    joins its stage: as primary when the stage has none (the run's primary
    first, so the head cam wins), otherwise as secondary.
    """
    _local_only()
    state = request.app.state.splitsmith_state
    record = _load(state, scan_id)
    if record.status != "ready":
        raise HTTPException(status_code=409, detail=f"scan is {record.status}")
    view = _view(state, record)
    paths = {c.clip.clip_id: Path(c.path) for c in record.clips}
    chosen = [c for c in view.clips if c.checked]
    # Primaries before secondaries so a run's primary claims an empty stage.
    chosen.sort(key=lambda c: (c.proposal.shooter or "", c.proposal.stage or 0, c.proposal.role != "primary"))
    imported: list[ImportedClip] = []
    not_imported = {c.clip_id: "not checked" for c in view.clips if not c.checked}
    queued: list[tuple[str, Any, int, Any]] = []
    by_shooter: dict[str, list[ClipView]] = {}
    for c in chosen:
        by_shooter.setdefault(c.proposal.shooter or "", []).append(c)
    for slug, clips in by_shooter.items():
        project = state.shooter_project(slug)
        root = state.shooter_root(slug)
        for c in clips:
            stage_number = c.proposal.stage
            assert stage_number is not None
            try:
                video = project.register_video(paths[c.clip_id], root, link_mode=req.link_mode)
                stage = project.stage(stage_number)
                role = "primary" if stage.primary() is None and c.proposal.role == "primary" else "secondary"
                video = project.assign_video(video.path, to_stage_number=stage_number, role=role)
            except (FileNotFoundError, KeyError, ValueError) as exc:
                not_imported[c.clip_id] = str(exc)
                continue
            imported.append(
                ImportedClip(
                    clip_id=c.clip_id, shooter=slug, stage=stage_number, role=video.role, path=str(video.path)
                )
            )
            queued.append((slug, project, stage_number, video))
        project.save(root)
    queue_beep = getattr(request.app.state, "auto_queue_beep", None)
    if queue_beep is not None:
        for slug, project, stage_number, video in queued:
            await queue_beep(slug, project, stage_number, video)

    record.status = "imported"
    _save(state, record)
    report_path = _sort_dir(state) / f"{scan_id}-report.json"
    atomic_write_json(
        report_path,
        {
            "scan_id": scan_id,
            "source_dir": record.source_dir,
            "imported_at": datetime.now(UTC).isoformat(),
            "skipped_files": record.skipped_files,
            "cameras": [c.model_dump(mode="json") for c in view.cameras],
            "clips": [
                {
                    "clip_id": c.clip_id,
                    "path": str(paths[c.clip_id]),
                    "proposal": c.proposal.model_dump(mode="json"),
                    "checked": c.checked,
                    "outcome": next(
                        (i.model_dump(mode="json") for i in imported if i.clip_id == c.clip_id),
                        {"not_imported": not_imported.get(c.clip_id, "")},
                    ),
                }
                for c in view.clips
            ],
        },
    )
    return ImportResponse(imported=imported, not_imported=not_imported, report=str(report_path))
