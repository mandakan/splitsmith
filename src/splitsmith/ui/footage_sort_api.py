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
import unicodedata
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
from ..match_project import VIDEO_EXTENSIONS, StageAutoMatch, atomic_write_json
from ..runtime import ENV_CONFIG_FILE
from ..video_match import recording_start_from_tags

logger = logging.getLogger(__name__)

router = APIRouter()

JOB_KIND = "footage_sort_scan"
SORT_DIR = "footage_sort"

ScanStatus = Literal["scanning", "ready", "failed", "imported"]


class ScannedClip(BaseModel):
    """One probed video of the scan. Where the file is already registered is
    looked up live on every read (:func:`_registrations`), never stored: the
    user imports and assigns between scan and review."""

    clip: SortClip
    path: str
    # Written by scans before 0.46.1; ignored.
    imported_by: str | None = None
    thumbnail: bool = False


class Registration(BaseModel):
    """Where a scanned file is already registered."""

    shooter: str
    # ``StageVideo.path`` in that shooter's project.
    video_path: str
    # On a stage: done, never imported again. Unassigned: sorted like a new
    # file, and moved to the right shooter on import.
    assigned: bool


class ScanRecord(BaseModel):
    """``<match>/footage_sort/<scan_id>.json``."""

    scan_id: str
    # The folder walked, or for an ``unassigned`` scan the common parent of
    # its files (clip ids and camera folders are relative to it).
    source_dir: str
    # An ``unassigned`` scan reads exactly these files instead of a walk.
    paths: list[str] = Field(default_factory=list)
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
    # The shooter whose stage already has this file (never imported again).
    imported_by: str | None
    # The shooter whose unassigned list holds it (import moves it as needed).
    unassigned_in: str | None
    thumbnail: bool
    # A hover-scrub strip exists (built after the scan, while the user
    # reviews; :func:`run_footage_sort_scan`).
    strip: bool
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
    # Scrub strips still being built; the page polls while this is > 0.
    strips_pending: int
    # The stored decisions, so the page edits them rather than rebuilding.
    anchors: list[Anchor]
    overrides: list[Override]
    user_checked: dict[str, bool]


class ScanRequest(BaseModel):
    """A folder to walk, or ``unassigned``: every video that sits unassigned
    in any shooter's project (the Footage page's "Sort across shooters")."""

    source_dir: str | None = None
    unassigned: bool = False


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
    registered = _registrations(state)
    thumbs = _sort_dir(state) / "thumbs"
    clips = []
    for index, scanned in enumerate(record.clips):
        p = by_id[scanned.clip.clip_id]
        reg = registered.get(_resolved(scanned.path))
        imported_by = reg.shooter if reg is not None and reg.assigned else None
        # Pre-checked: what the engine is sure of, and what the user's own
        # anchor placed (they answered for that camera already).
        sure = p.confidence == "high" or (p.confidence == "medium" and p.camera_key in anchored)
        default = sure and imported_by is None
        importable = p.shooter is not None and p.stage is not None and imported_by is None
        clips.append(
            ClipView(
                clip_id=scanned.clip.clip_id,
                index=index,
                folder=scanned.clip.folder,
                filename=scanned.clip.filename,
                start=scanned.clip.start,
                duration=scanned.clip.duration,
                model=scanned.clip.model or scanned.clip.make,
                imported_by=imported_by,
                unassigned_in=reg.shooter if reg is not None and not reg.assigned else None,
                thumbnail=scanned.thumbnail,
                strip=thumbnail.cached_strip(Path(scanned.path), thumbs) is not None,
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
        strips_pending=(
            sum(1 for c in clips if not c.strip and (c.duration or 0) > 0) if record.status == "ready" else 0
        ),
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


def _resolved(path: str | Path) -> Path:
    """The path as a lookup key: symlinks followed, Unicode composed. macOS
    lists names decomposed (``o`` + combining diaeresis) while a typed or
    pasted path is composed (``ö``); both open the same file, and a club
    mate's folder named after a match like "Höstfinalen" must match itself."""
    try:
        resolved = Path(path).resolve()
    except OSError:
        resolved = Path(path)
    return Path(unicodedata.normalize("NFC", str(resolved)))


def _registrations(state: Any) -> dict[Path, Registration]:
    """Resolved source path -> where it is registered. An assigned entry
    wins over an unassigned one for the same file."""
    out: dict[Path, Registration] = {}
    for slug in match_model.Match.load(state.match_root).shooters:
        project = state.shooter_project(slug)
        root = state.shooter_root(slug)
        entries = [(v, False) for v in project.unassigned_videos]
        entries += [(v, True) for s in project.stages for v in s.videos]
        for video, assigned in entries:
            try:
                source = _resolved(project.resolve_video_path(root, video.path))
            except OSError:
                continue
            if source in out and out[source].assigned and not assigned:
                continue
            out[source] = Registration(shooter=slug, video_path=str(video.path), assigned=assigned)
    return out


def keep_this_shooters(
    state: Any,
    slug: str,
    project: Any,
    root: Path,
    suggestions: dict[int, StageAutoMatch],
) -> dict[int, StageAutoMatch]:
    """Filter the per-shooter scan's :meth:`MatchProject.auto_match`
    suggestions through the footage sort engine.

    ``auto_match`` sees one shooter's scorecard windows, and a squad mate's
    run minutes earlier falls in them (Höstfinalen XI 2026: Anton's glasses
    clips auto-assigned as Mathias's primaries). With two or more shooters
    in the match, a clip stays suggested only when the engine, over every
    shooter's scorecards, puts it on ``slug`` with ``high`` or ``medium``
    confidence; the rest stay unassigned for the sort. One shooter: the
    suggestions are returned as they are.
    """
    cards, _ = _shooters(state)
    if sum(1 for c in cards if c.scorecards) < 2 or not suggestions:
        return suggestions
    stored = [p for s in suggestions.values() for p in ([s.primary] if s.primary else []) + s.secondaries]
    clips = []
    for path in stored:
        source = project.resolve_video_path(root, path)
        meta = probe_clip(source)
        clips.append(
            SortClip(
                clip_id=str(path),
                folder=str(_resolved(source).parent),
                filename=source.name,
                start=meta.get("start"),
                duration=meta.get("duration"),
                make=meta.get("make"),
                model=meta.get("model"),
            )
        )
    proposal = propose(clips, cards, config=_config())
    ours = {p.clip_id for p in proposal.clips if p.shooter == slug and p.confidence in ("high", "medium")}
    kept: dict[int, StageAutoMatch] = {}
    for stage_number, s in suggestions.items():
        if s.primary is None:
            secondaries = [p for p in s.secondaries if str(p) in ours]
            if secondaries:
                kept[stage_number] = StageAutoMatch(primary=None, secondaries=secondaries)
            continue
        run = [p for p in [s.primary, *s.secondaries] if str(p) in ours]
        if run:
            kept[stage_number] = StageAutoMatch(primary=run[0], secondaries=run[1:])
    return kept


def run_footage_sort_scan(handle: Any, *, state: Any, scan_id: str) -> None:
    """Job body for ``footage_sort_scan``: walk, probe, thumbnail, save."""
    record = _load(state, scan_id)
    source = Path(record.source_dir)
    try:
        if record.paths:
            videos = [Path(p) for p in record.paths]
        else:
            files = sorted(p for p in source.rglob("*") if p.is_file() and not p.name.startswith("."))
            videos = [p for p in files if p.suffix.lower() in VIDEO_EXTENSIONS]
            record.skipped_files = len(files) - len(videos)
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
    # The review is usable now; scrub strips follow, one ffmpeg call each
    # (1-3 s from a USB drive). They are files, never fields on the record,
    # so this loop cannot race the user's decisions being saved.
    for i, item in enumerate(record.clips):
        handle.check_cancel()
        handle.update(progress=i / max(len(record.clips), 1), message=f"Preview {item.clip.filename}")
        if not item.clip.duration:
            continue
        try:
            thumbnail.ensure_strip(Path(item.path), cache_dir=thumbs, duration=item.clip.duration)
        except Exception:  # noqa: BLE001 -- a preview never fails the scan
            logger.debug("footage sort: no strip for %s", item.path, exc_info=True)


def _move_unassigned(
    state: Any,
    source_project: Any,
    source_root: Path,
    target_project: Any,
    target_root: Path,
    reg: Registration,
) -> Path:
    """Move one unassigned video between shooters with
    :func:`shooter_move.move_shooter` and return its path in the target.
    An unassigned video has no stage, so no audit is read or written."""
    from .shooter_move import move_shooter

    def no_audit(*_: Any) -> None:
        return None

    outcome = move_shooter(
        source_project=source_project,
        source_root=source_root,
        target_project=target_project,
        target_root=target_root,
        video_paths=[reg.video_path],
        load_target_audit=no_audit,
        save_target_audit=no_audit,
        load_source_audit=no_audit,
        clear_source_audit=no_audit,
        storage=state.storage,
    )
    if outcome.blocked:
        raise ValueError(outcome.blocked[0].reason)
    return Path(outcome.moved[0].video_path)


@router.post("/api/match/footage-sort/scan")
async def start_scan(req: ScanRequest, request: Request) -> dict[str, Any]:
    _local_only()
    state = request.app.state.splitsmith_state
    if len(match_model.Match.load(state.match_root).shooters) == 0:
        raise HTTPException(status_code=409, detail="add the match's shooters first")
    if (req.source_dir is None) == (not req.unassigned):
        raise HTTPException(status_code=400, detail="give a source_dir or unassigned, not both")
    paths: list[str] = []
    if req.unassigned:
        paths = sorted(
            {str(path) for path, reg in _registrations(state).items() if not reg.assigned and path.is_file()}
        )
        if not paths:
            raise HTTPException(status_code=409, detail="no unassigned videos to sort")
        source = Path(os.path.commonpath(paths))
        if source.is_file():
            source = source.parent
    else:
        assert req.source_dir is not None
        source = Path(req.source_dir).expanduser()
        if not source.is_dir():
            raise HTTPException(status_code=400, detail=f"not a folder: {source}")
        source = source.resolve()
    record = ScanRecord(
        scan_id=uuid.uuid4().hex[:12], source_dir=str(source), paths=paths, created_at=datetime.now(UTC)
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


@router.get("/api/match/footage-sort/{scan_id}/thumbs/{index}/strip.jpg")
def get_strip(scan_id: str, index: int, request: Request) -> FileResponse:
    _local_only()
    state = request.app.state.splitsmith_state
    record = _load(state, scan_id)
    if not 0 <= index < len(record.clips):
        raise HTTPException(status_code=404, detail="no such clip")
    hit = thumbnail.cached_strip(Path(record.clips[index].path), _sort_dir(state) / "thumbs")
    if hit is None:
        raise HTTPException(status_code=404, detail="no strip yet")
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
    registered = _registrations(state)
    projects: dict[str, tuple[Any, Path]] = {}

    def project_of(slug: str) -> tuple[Any, Path]:
        if slug not in projects:
            projects[slug] = (state.shooter_project(slug), state.shooter_root(slug))
        return projects[slug]

    for c in chosen:
        slug = c.proposal.shooter
        stage_number = c.proposal.stage
        assert slug is not None and stage_number is not None
        project, root = project_of(slug)
        reg = registered.get(_resolved(paths[c.clip_id]))
        try:
            if reg is None:
                video_path = project.register_video(paths[c.clip_id], root, link_mode=req.link_mode).path
            elif reg.shooter == slug:
                # Imported earlier under the right shooter, never placed.
                video_path = Path(reg.video_path)
            else:
                # Imported earlier under the wrong shooter (the per-shooter
                # Add footage files everything under the active one): move
                # it through the one move path there is, then place it.
                video_path = _move_unassigned(state, *project_of(reg.shooter), project, root, reg)
            stage = project.stage(stage_number)
            role = "primary" if stage.primary() is None and c.proposal.role == "primary" else "secondary"
            video = project.assign_video(video_path, to_stage_number=stage_number, role=role)
        except (FileNotFoundError, KeyError, ValueError) as exc:
            not_imported[c.clip_id] = str(exc)
            continue
        imported.append(
            ImportedClip(
                clip_id=c.clip_id, shooter=slug, stage=stage_number, role=video.role, path=str(video.path)
            )
        )
        queued.append((slug, project, stage_number, video))
    for project, root in projects.values():
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
