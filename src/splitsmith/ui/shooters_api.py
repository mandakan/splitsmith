"""The per-shooter routes: ``/api/shooters/{slug}/...`` other than the per-stage ones.

Lifted out of ``server.create_app`` (#680). Handlers read the process state
from ``request.app.state.splitsmith_state``; helpers shared with other route
families are called through the ``server`` module so a test that patches
``server.<helper>`` still reaches them. Those helpers and the request models
move here, or to their own domain modules, as the split continues.
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import tempfile
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import AwareDatetime, BaseModel, Field, ValidationError
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from .. import automation as automation_settings
from .. import backup as backup_mod
from .. import camera_select, match_model, video_probe
from .. import cleanup as cleanup_module
from .. import coach as coach_module
from .. import coach_distributions as coach_distributions_module
from .. import shooter_book as shooter_book_module
from .. import thumbnail as thumbnail_helpers
from .. import trim as trim_module
from .. import waveform as waveform_helpers
from ..async_bridge import run_sync
from ..auth import User
from ..beep import windows as beep_windows
from ..config import BeepWindowConfig
from ..fingerprint import clip_fingerprint
from ..identity import LOGO_DIR, LOGO_MAX_BYTES, LOGO_MAX_SIDE, ShooterIdentity, logo_name
from ..match_project import (
    VIDEO_EXTENSIONS,
    MatchProject,
    RawVideo,
    ScoreboardImportConflictError,
    StageVideo,
    VideoRole,
)
from ..runtime import runtime as process_runtime
from ..storage import Storage
from . import audio as audio_helpers
from . import server as _server
from .context import current_match_origin, current_match_root, current_share_request
from .http_errors import ensure_source_reachable
from .identity_media import ensure_local_logo, identity_source
from .jobs import Job, JobStatus
from .project_view import ProjectView, build_project_view
from .scoreboard import CachingScoreboardClient, LocalJsonScoreboard, MatchNotFound, ScoreboardError
from .server import (
    AttachRawVideoRequest,
    FsEntry,
    FsListing,
    LinkStatusEntry,
    RelinkAppliedEntry,
    RelinkEntryResponse,
    RevealRequest,
    ScanRequest,
)
from .state import AppState

logger = logging.getLogger(__name__)

router = APIRouter()


class _NoOpClient:
    """Inner stub so ``CachingScoreboardClient`` can be instantiated for cache
    invalidation alone (no upstream call expected). All four protocol methods
    raise -- callers shouldn't reach them when only ``invalidate_*`` is used.
    """

    def search_matches(self, query: str):  # type: ignore[no-untyped-def]
        raise RuntimeError("_NoOpClient.search_matches: cache-invalidate only")

    def get_match(self, content_type: int, match_id: int):  # type: ignore[no-untyped-def]
        raise RuntimeError("_NoOpClient.get_match: cache-invalidate only")

    def find_shooter(self, name: str):  # type: ignore[no-untyped-def]
        raise RuntimeError("_NoOpClient.find_shooter: cache-invalidate only")

    def get_shooter(self, shooter_id: int):  # type: ignore[no-untyped-def]
        raise RuntimeError("_NoOpClient.get_shooter: cache-invalidate only")

    def get_stage_times(  # type: ignore[no-untyped-def]
        self, content_type: int, match_id: int, competitor_id: int
    ):
        raise RuntimeError("_NoOpClient.get_stage_times: cache-invalidate only")


async def _any_active_job(state: AppState) -> Job | None:
    """Return the first PENDING/RUNNING job, or None.

    Used by destructive endpoints (cleanup) to refuse running while
    workers are mid-flight. ``find_active`` on the registry is
    kind-scoped; this helper scans every kind so a beep job blocks a
    cleanup just as a trim does.
    """

    for job in await state.jobs.list():
        if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
            return job
    return None


class ScoreboardImportRequest(BaseModel):
    data: dict[str, Any]
    overwrite: bool = False


class SuggestCoverageRequest(BaseModel):
    """Body for POST /api/shooters/{slug}/videos/suggest-coverage.

    The SPA probes the file duration client-side (to avoid server-side ffprobe
    of remote R2 objects) and passes the wall-clock span here. When all fields
    are null the endpoint returns an empty suggestion so the caller falls back
    to manual selection.
    """

    recorded_start: AwareDatetime | None = None
    duration_s: float | None = None
    path: str | None = None


class TakeRepairRequest(BaseModel):
    """Body for POST /api/shooters/{slug}/raw-videos/repair (#1214)."""

    filename: str


class CoverageEditRequest(BaseModel):
    """Body for PATCH /api/shooters/{slug}/raw-videos/coverage.

    ``covers_stages`` replaces the full per-stage assignment for this raw
    video. Declared order is preserved (it is the shooting order for
    sequential-mode takes). Stages absent from the new list are removed and
    their trim/audio caches invalidated.
    """

    filename: str
    covers_stages: list[int]


class ScoreboardUploadRequest(BaseModel):
    """Body for POST /api/scoreboard/upload (#50).

    The SPA reads the dropped file as text, parses it as JSON, and posts
    the dict here. The backend writes it to ``<project>/scoreboard/match.json``
    and uses :class:`LocalJsonScoreboard` to populate the project; the same
    ``MatchData`` shape an online ``get_match`` would return.
    """

    data: dict[str, Any]
    overwrite: bool = False


class ScoreboardFetchRequest(BaseModel):
    """Body for POST /api/scoreboard/fetch (#50).

    Pulls a full match from the live scoreboard (cache-first via
    :class:`CachingScoreboardClient`) and populates the project. When the
    project already has a local ``scoreboard/match.json`` we still honour
    the offline path -- the endpoint refuses with 409 so the user clears
    the local file before falling back to the live source.
    """

    content_type: int
    match_id: int
    overwrite: bool = False


class SelectShooterRequest(BaseModel):
    """Body for POST /api/scoreboard/select-shooter (#64).

    Both ids are required: the SPA picks a shooter from
    ``/api/scoreboard/shooter/search`` (which returns ``shooterId``), then
    looks up the matching ``competitor_id`` in the loaded ``MatchData``
    before posting. Server-side derivation of the competitor id would
    require us to refetch ``MatchData`` here; the SPA already has it.
    """

    shooter_id: int
    competitor_id: int


class PlaceholderStagesRequest(BaseModel):
    """Bootstrap request: create N placeholder stages without a scoreboard."""

    stage_count: int
    match_name: str | None = None
    match_date: date | None = None


class ScanResponse(BaseModel):
    registered: list[str]
    # Stage -> the clip made primary.
    auto_assigned: dict[int, str]
    # Stage -> the other cameras on the same run, attached as secondaries
    # (to a primary from this scan or one the stage already had).
    auto_secondary: dict[int, list[str]] = Field(default_factory=dict)
    skipped: list[str]


class RelinkScanRequest(BaseModel):
    """Body for POST /api/videos/relink/scan.

    Walks ``search_root`` recursively, indexes videos by basename, and
    matches them against the project's registered ``raw/<name>``
    entries. Pure dry-run: never touches the filesystem.
    """

    search_root: str


class RelinkApplyRequest(BaseModel):
    """Body for POST /api/videos/relink/apply.

    ``decisions`` maps ``video_id`` to the absolute filesystem path the
    user picked (typically one of the candidates returned by the scan
    endpoint, or a manually-typed override). Any video_id not listed is
    left untouched.
    """

    decisions: dict[str, str]


class RelinkScanResponse(BaseModel):
    search_root: str
    entries: list[RelinkEntryResponse]


class RelinkApplyResponse(BaseModel):
    applied: list[RelinkAppliedEntry]


class LinkStatusResponse(BaseModel):
    """Per-video filesystem status for the ``raw/<name>`` symlinks.
    Surfaced separately from ``/api/project`` so we don't pollute the
    persisted ``MatchProject`` model with computed fs state.
    """

    entries: list[LinkStatusEntry]


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
    # Layered automation override (#215). ``None`` keeps the current
    # value; pass an :class:`AutomationOverride`-shaped dict with
    # field values (or nulls) to set / clear individual project-level
    # overrides. The resolved effective value is exposed via
    # ``GET /api/automation``.
    automation: automation_settings.AutomationOverride | None = None
    confirm: bool = False


class MoveRequest(BaseModel):
    video_path: str
    to_stage_number: int | None = None
    role: VideoRole = "secondary"
    # The stage whose registration moves: a multi-stage single take has one
    # per stage on the same path (#1212). None = the first registration.
    from_stage_number: int | None = None


class ShooterIdentityRequest(BaseModel):
    """Body for PATCH /api/shooters/{slug}/identity (#1243). Only the keys
    sent are applied; ``null`` clears one. The logo has its own routes.
    ``scope`` (spec 2026-10-08): ``book`` also saves the result to the
    shooter book under the shooter's SSI id; ``match`` keeps it here only."""

    accent: str | None = None
    club: str | None = None
    scope: Literal["book", "match"] = "book"


class CompareCameraRequest(BaseModel):
    """Body for PATCH /api/shooters/{slug}/compare-camera.

    ``camera`` is a camera mount ("chest") or a role ("primary",
    "secondary"); ``None`` clears the selection back to the primary.
    """

    camera: str | None = None


class SwapPrimaryRequest(BaseModel):
    """Body for POST /api/assignments/swap-primary.

    Promotes ``video_path`` to primary on ``stage_number``. When the stage
    has audit work and ``confirm`` is False, the endpoint refuses with a
    409 response describing what would be lost so the SPA can prompt the
    user. Passing ``confirm=True`` performs the swap and renames the
    existing audit JSON to ``stage<N>.pre-swap-<time>.json``, which no
    later save overwrites.
    """

    video_path: str
    stage_number: int
    confirm: bool = False


class NudgeDismissRequest(BaseModel):
    """Body for POST /api/project/nudges/dismiss (#218 phase 4).

    The audit-pending reminder shown on stages with empty
    ``shots[]`` but a non-empty candidate pool. ``dismissed=True``
    adds ``stage_number`` to the project's
    ``nudges_dismissed_stages`` list; ``dismissed=False`` clears it
    so the reminder reappears (e.g., the user wants the prompt
    back).
    """

    stage_number: int
    dismissed: bool = True


class CleanupRequest(BaseModel):
    """Body for POST /api/project/cleanup.

    The server re-plans server-side from this list rather than trusting a
    client-supplied path list -- a malicious or buggy client cannot ask
    us to delete arbitrary files outside the project's known buckets.

    ``include_unrebuildable`` is the API's half of the item-level consent
    the SPA dialog and ``splitsmith clean --include-unrebuildable``
    already enforce (#926). It defaults to False so the safe direction is
    what a caller gets by saying nothing: every client that predates the
    field keeps working and none of them can destroy data by omission.
    It cannot be a path list for the reason above -- the server re-plans,
    so this is a yes/no about a class of item, not a selection.
    """

    categories: list[cleanup_module.CleanupCategory]
    include_unrebuildable: bool = False


class RemoveVideoRequest(BaseModel):
    """Body for POST /api/videos/remove (#24).

    ``reset_audit`` only takes effect when removing a primary; otherwise it
    is ignored (audit is stage-level state, and non-primary removals do not
    invalidate it).
    """

    video_path: str
    reset_audit: bool = False
    # The stage whose registration goes (#1212); None = the first one.
    stage_number: int | None = None


def _apply_raw_video_coverage(
    project: MatchProject,
    root: Path,
    storage_path: str,
    covers: list[int],
) -> list[StageVideo]:
    """Diff and apply per-stage coverage for a raw video.

    Creates StageVideo entries for stages in ``covers`` not yet
    registered under ``storage_path``; removes entries for stages
    that were previously registered but are no longer listed (and
    invalidates their trim/audio caches). Updates
    ``rv.covers_stages`` with ``covers`` as-is (declared order is
    shooting order for sequential-mode takes; no implicit sort).
    Removes a matching ``unassigned_videos`` entry when coverage is
    first applied (the video moves from the tray to named stages).

    Creates the ``RawVideo`` manifest entry if one does not exist
    yet (local-mode scan only puts the video in
    ``unassigned_videos``; attach or PATCH coverage creates the
    raw entry).

    Returns the newly created ``StageVideo`` instances.
    """
    video_path = Path(storage_path)
    rv = project.find_raw_video(storage_path)
    if rv is None:
        rv = RawVideo(
            original_filename=video_path.name,
            storage_path=storage_path,
        )
        project.raw_videos.append(rv)

    previously_covered: set[int] = set(rv.covers_stages)
    new_covers_set: set[int] = set(covers)

    created: list[StageVideo] = []

    # Add StageVideos for stages not yet registered.
    for stage_number in covers:
        stage = project.stage(stage_number)
        if any(str(v.path) == storage_path for v in stage.videos):
            continue  # already registered - skip
        role: VideoRole = "secondary" if stage.primary() is not None else "primary"
        sv = StageVideo(path=video_path, role=role, stage_number=stage_number)
        stage.videos.append(sv)
        created.append(sv)

    # Remove StageVideos for stages no longer in the new list and
    # invalidate their trim/audio caches so a later re-assign starts clean.
    for stage_number in sorted(previously_covered - new_covers_set):
        try:
            stage = project.stage(stage_number)
        except KeyError:
            continue
        to_remove = [v for v in stage.videos if str(v.path) == storage_path]
        stage.videos = [v for v in stage.videos if str(v.path) != storage_path]
        for v in to_remove:
            audio_helpers.invalidate_video_audit_trim(root, stage_number, v, project=project)

    # Update covers_stages with the caller-declared order (no sort).
    rv.covers_stages = list(dict.fromkeys(covers))

    # Remove from unassigned tray when coverage is being applied for
    # the first time (moves it from "unregistered" to named stages).
    project.unassigned_videos = [v for v in project.unassigned_videos if str(v.path) != storage_path]

    return created


def _remove_local_logo(state: AppState, slug: str, name: str | None) -> None:
    if name is None:
        return
    try:
        (_server._identity_logo_dir(state, slug) / name).unlink()
    except FileNotFoundError:
        pass


async def _save_to_book(state: AppState, project: MatchProject, slug: str) -> None:
    """Write the shooter's identity to the shooter book under their SSI
    id (spec 2026-10-08): the whole record, the logo copied into the
    book's own files (same content name); an identity that sets nothing
    removes the entry. No SSI id, nothing: the book never keys by name."""
    sid = project.selected_shooter_id
    if sid is None:
        return
    try:
        await _server._write_book(state, project, slug, sid)
    except Exception as exc:  # noqa: BLE001 -- the match was saved; the book is the extra
        logger.warning("shooter book: %s's look was saved to the match but not the book (%s)", slug, exc)


def _book_seeded_identity(state: AppState, project: MatchProject, slug: str) -> ShooterIdentity:
    """What an edit starts from: the look the videos draw, so an edit
    never drops what the user did not touch. That is the book's entry
    when it sets anything (the book wins, spec 2026-10-09; its logo is
    copied into this match's ``identity/``), else the match's own record."""
    own = project.identity
    book = shooter_book_module.load_snapshot(state.shooter_book)
    entry = book.get(project.selected_shooter_id)
    if not shooter_book_module.is_set(entry):
        return own
    assert entry is not None
    logo = None
    source = book.logo_path(entry)
    if entry.logo is not None and source is not None:
        target = _server._identity_logo_dir(state, slug) / entry.logo
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = source.read_bytes()
            target.write_bytes(data)
            storage = project._storage  # type: ignore[attr-defined]
            storage_scope = project._storage_scope  # type: ignore[attr-defined]
            if storage is not None and storage_scope is not None:
                storage.write_bytes(f"{storage_scope}/{LOGO_DIR}/{entry.logo}", data)
            logo = entry.logo
        except OSError as exc:
            logger.warning("identity: could not copy the book's logo into %s (%s)", slug, exc)
    return entry.model_copy(update={"logo": logo})


def _identity_view(state: AppState, project: MatchProject) -> dict[str, Any]:
    """The identity sheet's view: where the look comes from and what it is."""
    book = shooter_book_module.load_snapshot(state.shooter_book)
    source = identity_source(project, book)
    effective = project.identity if source != "book" else book.get(project.selected_shooter_id)
    return {
        "source": source,
        "identity": (effective or ShooterIdentity()).model_dump(mode="json"),
        "shooter_id": project.selected_shooter_id,
        # Whether the book holds a look for this shooter ("Use shooter
        # book" has something to fall back to) and whether this server
        # keeps a book at all (one with no store answers an empty one).
        "book_entry": shooter_book_module.is_set(book.get(project.selected_shooter_id)),
        "book_available": not isinstance(state.shooter_book, shooter_book_module.EmptyShooterBookStore),
    }


def _hosted_scrub(
    storage: Storage,
    project: MatchProject,
    root: Path,
    stage: Any,
    video: StageVideo,
) -> RedirectResponse:
    """``kind=scrub`` on hosted (#1209): the fresh rendition, else the
    trim, else 404 -- never the source. Two ``stat`` calls, the cost of
    the two ``exists`` the other kinds make."""
    local_mp4 = audio_helpers.trimmed_video_path(root, stage.stage_number, video, project=project)
    web_local = trim_module.web_trim_path(local_mp4)
    trim_key = audio_helpers._storage_trim_key(project, local_mp4)
    web_key = audio_helpers._storage_trim_key(project, web_local)
    if trim_key is None or web_key is None:
        raise HTTPException(status_code=404, detail="trimmed clip not built yet")
    trim_obj = storage.stat(trim_key)
    web_obj = storage.stat(web_key)
    if audio_helpers.fresh_rendition(
        _server._storage_fact(trim_obj),
        _server._storage_fact(web_obj),
        trim_required=not _server._is_mirror(),
    ):
        return _server.serve_media(storage, web_key, web_local, content_type="video/mp4")
    if trim_obj is not None:
        return _server.serve_media(storage, trim_key, local_mp4, content_type="video/mp4")
    raise HTTPException(status_code=404, detail="trimmed clip not built yet")


@router.get("/api/shooters/{slug}/project", response_model=ProjectView)
def get_project(request: Request, slug: str) -> JSONResponse:
    """The project as the SPA reads it: the stored document plus the fields
    ``build_project_view`` computes per request (``project_view.py``)."""
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    return JSONResponse(build_project_view(state, slug, project))


@router.get("/api/shooters/{slug}/project/export")
def export_project_endpoint(
    request: Request,
    slug: str,
    include_trimmed: bool = Query(False),
    include_exports: bool = Query(False),
    include_raw: bool = Query(False),
    include_audio: bool = Query(False),
) -> FileResponse:
    """Stream the bound project as a ``.tar.gz`` download.

    The default archive contains only ``project.json``, ``audit/`` and
    ``scoreboard/`` -- the artefacts that cannot be regenerated from
    the source footage. The ``include_*`` query flags opt
    regeneratable directories into the archive. The archive is
    written to a temp file and deleted once the response finishes
    streaming.
    """
    state = request.app.state.splitsmith_state
    root = state.shooter_root(slug)
    tmp = Path(tempfile.mkdtemp(prefix="splitsmith-export-"))
    archive = tmp / f"{root.name}.tar.gz"
    try:
        result = backup_mod.export_project(
            root,
            archive,
            include_trimmed=include_trimmed,
            include_exports=include_exports,
            include_raw=include_raw,
            include_audio=include_audio,
        )
    except backup_mod.BackupError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    stamp = datetime.now(UTC).strftime("%Y%m%d")
    download_name = f"{root.name}-backup-{stamp}.tar.gz"
    return FileResponse(
        result.archive_path,
        media_type="application/gzip",
        filename=download_name,
        background=BackgroundTask(shutil.rmtree, tmp, ignore_errors=True),
    )


@router.post("/api/shooters/{slug}/raw-videos/attach")
def attach_raw_video(
    request: Request,
    slug: str,
    body: AttachRawVideoRequest,
    user: User = Depends(_server.get_current_user),
) -> JSONResponse:
    """Register an uploaded raw video against a shooter's project (doc 05).

    Hosted-only -- 503 when ``state.storage`` is unwired. The
    upload itself must already be in object storage (the SPA calls
    ``POST /api/me/raw/upload`` first); this endpoint validates that
    and records the manifest entry on ``match.json``.

    Body shape: ``{filename, sha256?, size_bytes?, covers_stages?}``.
    The trailing fields default to whatever the upload endpoint
    echoed back; size + sha256 fill in the legacy backfill
    placeholders if the project's migration left them empty. When
    ``covers_stages`` is provided, the endpoint also creates
    ``StageVideo`` entries on those stages (path =
    ``raw/<filename>``, role auto-promoted to primary when the
    stage has no primary yet -- same rule as
    :meth:`MatchProject.assign_video`).

    Returns the canonical ``RawVideo`` (either the freshly attached
    entry or the merged-into existing one).

    Error contract:

    - 503 -- no hosted storage wired (local mode).
    - 400 -- filename failed ``_sanitize_raw_filename`` (path
      separators, ``..``, empty).
    - 404 -- no object at ``raw/<filename>`` in storage.
    - 422 -- ``covers_stages`` references a stage_number the
      project doesn't have.
    """
    state = request.app.state.splitsmith_state
    storage = state.storage
    if storage is None:
        raise HTTPException(
            status_code=503,
            detail="raw video attach is hosted-mode only",
        )

    name = _server._sanitize_raw_filename(body.filename)
    storage_path = f"raw/{name}"
    stat = storage.stat(storage_path)
    if stat is None:
        raise HTTPException(
            status_code=404,
            detail=f"no upload at {storage_path!r}; upload first via POST /api/me/raw/upload",
        )

    project = state.shooter_project(slug)
    root = state.shooter_root(slug)

    # A raw object is one shooter's footage. The hosted ``raw/`` pool is
    # shared per-user with no filesystem-enforced owner (unlike the
    # desktop layout), so refuse an object another shooter in this match
    # already claims -- otherwise the same upload gets duplicated across
    # shooters (#562). Same map the available-uploads list labels from,
    # so what the SPA hides and what this refuses cannot disagree.
    #
    # Any *other* owner is a conflict, not just the first: a match from
    # before this guard can hold one object on two shooters, and letting
    # a caller through because it is one of them would entrench that.
    claimed_by_other = next(
        (s for s in _server._raw_video_owners(state).get(storage_path, []) if s != slug), None
    )
    if claimed_by_other is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "already_attached_to_shooter",
                "shooter": claimed_by_other,
                "storage_path": storage_path,
            },
        )

    # Preserve the caller's declared order; remove duplicates but do not sort.
    covers = list(dict.fromkeys(body.covers_stages or []))
    if covers:
        # Verify every claimed stage exists before mutating anything;
        # an unknown stage_number is a 422 (client bug) rather than a
        # silent skip that leaves the manifest in a half-populated state.
        known = {s.stage_number for s in project.stages}
        unknown = [n for n in covers if n not in known]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "unknown_stage_numbers",
                    "stage_numbers": unknown,
                },
            )

    # Trust the upload endpoint's sha256 verification: it already
    # rolled the object back on mismatch (see the X-Content-SHA256
    # path in upload_raw_video). The size on the body is informational;
    # we always defer to S3's reported ContentLength when available
    # so the manifest matches the object the worker will download.
    rv = RawVideo(
        original_filename=name,
        size_bytes=int(stat.size if stat.size else (body.size_bytes or 0)),
        sha256=body.sha256,
        storage_path=storage_path,
        covers_stages=covers,
        duration_seconds=body.duration_seconds,
        recorded_start=body.recorded_start,
    )
    canonical = project.attach_raw_video(rv)

    # Stamp optional metadata from body onto the canonical entry even
    # when attach_raw_video returned an existing merged entry.
    if body.duration_seconds is not None and canonical.duration_seconds is None:
        canonical.duration_seconds = body.duration_seconds
    if body.recorded_start is not None and canonical.recorded_start is None:
        canonical.recorded_start = body.recorded_start

    # Decide where the per-stage StageVideo entries land:
    #   - covers_stages given -> one StageVideo per named stage via
    #     _apply_raw_video_coverage (handles create/remove diff).
    #   - covers_stages empty -> a single entry in unassigned_videos
    #     so the ingest tray UI picks it up the same way local-mode
    #     scan does. The user then drags to stages or runs auto-match.
    if covers:
        # Use the canonical merged covers (order-preserving union already
        # computed by project.attach_raw_video) so that repeat attaches
        # with different covers_stages accumulate rather than replace.
        created = _apply_raw_video_coverage(project, root, storage_path, canonical.covers_stages)
    else:
        video_path = Path(storage_path)
        already_registered = project.find_video(video_path) is not None
        if not already_registered:
            project.unassigned_videos.append(StageVideo(path=video_path))
        created = []

    project.save(root)

    if created:
        _server._queue_take_detects(state, slug, project, created)

    return JSONResponse(canonical.model_dump(mode="json"))


@router.patch("/api/shooters/{slug}/raw-videos/coverage")
def edit_raw_video_coverage(
    request: Request,
    slug: str,
    body: CoverageEditRequest,
    user: User = Depends(_server.get_current_user),
) -> JSONResponse:
    """Update the per-stage coverage list for a registered raw video.

    Works in local and hosted mode - identity key is the StageVideo
    path string ``raw/<filename>``, the same in both modes.

    Body: ``{"filename": str, "covers_stages": list[int]}``. Stages
    are applied in declared order (shooting order for sequential-mode
    takes). Stages previously covered but now absent are removed and
    their trim/audio caches invalidated. Stages not previously covered
    are created. After saving, enqueues detect_beep for newly created
    entries via ``_queue_take_detects``.

    Error contract:

    - 400 - filename failed ``_sanitize_raw_filename``.
    - 404 - no RawVideo or registered video matches ``raw/<filename>``.
    - 422 - ``covers_stages`` references a stage not in the project.
    """
    state = request.app.state.splitsmith_state
    name = _server._sanitize_raw_filename(body.filename)
    storage_path = f"raw/{name}"
    video_path = Path(storage_path)

    project = state.shooter_project(slug)
    root = state.shooter_root(slug)

    # 404 when the path isn't registered anywhere in the project.
    if project.find_raw_video(storage_path) is None and project.find_video(video_path) is None:
        raise HTTPException(
            status_code=404,
            detail=f"no video registered at {storage_path!r}",
        )

    covers = list(dict.fromkeys(body.covers_stages))
    if covers:
        known = {s.stage_number for s in project.stages}
        unknown = [n for n in covers if n not in known]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail={"error": "unknown_stage_numbers", "stage_numbers": unknown},
            )

    created = _apply_raw_video_coverage(project, root, storage_path, covers)
    project.save(root)

    if created:
        _server._queue_take_detects(state, slug, project, created)

    canonical = project.find_raw_video(storage_path)
    if canonical is None:
        raise HTTPException(status_code=500, detail="coverage applied but raw video entry missing")
    return JSONResponse(canonical.model_dump(mode="json"))


@router.post("/api/shooters/{slug}/raw-videos/repair")
def repair_take(
    request: Request,
    slug: str,
    body: TakeRepairRequest,
    user: User = Depends(_server.get_current_user),
) -> JSONResponse:
    """Repair a single take damaged by the old #1212 bug (#1214).

    A stage that lists the file more than once keeps its first entry
    (its own registration: beep, review, trim) and loses the appended
    extras (``MatchProject.repair_take``). Nothing is re-detected and no
    cache is touched: the extras share the kept entry's ``video_id``.
    A healthy take is a no-op.

    Response: ``{"repaired_stages": [...], "project": {...}}``. 404 when
    ``raw/<filename>`` is not registered on the project.
    """
    state = request.app.state.splitsmith_state
    name = _server._sanitize_raw_filename(body.filename)
    storage_path = f"raw/{name}"
    project = state.shooter_project(slug)
    root = state.shooter_root(slug)
    if project.find_video(Path(storage_path)) is None:
        raise HTTPException(status_code=404, detail=f"{storage_path!r} is not registered")

    repaired = project.repair_take(storage_path)
    if repaired:
        project.save(root)
    return JSONResponse({"repaired_stages": repaired, "project": project.model_dump(mode="json")})


@router.get("/api/shooters/{slug}/raw-videos/overview")
def raw_video_overview(
    request: Request,
    slug: str,
    filename: str = Query(...),
    user: User = Depends(_server.get_current_user),
) -> JSONResponse:
    """Return the take-level overview for a registered raw video.

    Response shape:

    .. code-block:: json

        {
          "raw_video": { ...RawVideo dump... },
          "duration_seconds": 1800.0,
          "stages": [
            {
              "stage_number": 2, "stage_name": "Short course",
              "video_id": "abc123def456", "role": "primary",
              "beep_time": 512.3, "beep_confidence": 0.91,
              "beep_reviewed": true, "beep_window": [420.0, 700.0],
              "beep_window_source": "scoreboard", "status": "found"
            }
          ],
          "conflicts": [2, 3]
        }

    ``status`` per stage-video:
    - ``"found"`` -- beep_time is set
    - ``"none"`` -- beep_auto_detect_failed is True
    - ``"pending"`` -- not yet detected

    ``conflicts`` lists stage numbers whose beep times sit within
    ``BeepWindowConfig.conflict_threshold_s`` of each other.
    """
    state = request.app.state.splitsmith_state
    name = _server._sanitize_raw_filename(filename)
    storage_path = f"raw/{name}"
    project = state.shooter_project(slug)

    rv = project.find_raw_video(storage_path)
    if rv is None:
        raise HTTPException(status_code=404, detail=f"no raw video at {storage_path!r}")

    cfg = BeepWindowConfig()
    stages_out = []
    beeps_for_conflicts: dict[int, float] = {}

    for stage_num in rv.covers_stages:
        try:
            stage = project.stage(stage_num)
        except KeyError:
            continue
        # Find the StageVideo for this take (matched by path).
        sv = next((v for v in stage.videos if str(v.path) == storage_path), None)
        if sv is None:
            continue

        # Determine status.
        if sv.beep_time is not None:
            status = "found"
            beeps_for_conflicts[stage_num] = sv.beep_time
        elif sv.beep_auto_detect_failed:
            status = "none"
        else:
            status = "pending"

        beep_window_list = list(sv.beep_window) if sv.beep_window is not None else None
        stages_out.append(
            {
                "stage_number": stage.stage_number,
                "stage_name": stage.stage_name,
                "video_id": sv.video_id,
                "role": sv.role,
                "beep_time": sv.beep_time,
                "beep_confidence": sv.beep_confidence,
                "beep_reviewed": sv.beep_reviewed,
                "beep_window": beep_window_list,
                "beep_window_source": sv.beep_window_source,
                "status": status,
            }
        )

    conflicts = sorted(beep_windows.find_beep_conflicts(beeps_for_conflicts, cfg.conflict_threshold_s))

    payload = {
        "raw_video": rv.model_dump(mode="json"),
        "duration_seconds": rv.duration_seconds,
        "stages": stages_out,
        "conflicts": conflicts,
    }
    return JSONResponse(payload)


@router.get("/api/shooters/{slug}/raw-videos/peaks")
def raw_video_peaks(
    request: Request,
    slug: str,
    filename: str = Query(...),
    bins: int = Query(default=3000, ge=16, le=8192),
    user: User = Depends(_server.get_current_user),
) -> JSONResponse:
    """Return ``bins`` peak magnitudes for the take's whole-file envelope waveform.

    Local mode: compute peaks from the 8 kHz take WAV a job published (202 while it is made).

    Hosted mode: the worker pushes a pre-computed peaks JSON to storage
    after detection. When the JSON is absent from storage this endpoint
    returns ``202 {"pending": true}`` so the SPA can poll and render a
    spinner in the meantime.

    Success response shape is identical to the per-stage
    ``/stages/{n}/peaks`` endpoint.
    """
    state = request.app.state.splitsmith_state
    name = _server._sanitize_raw_filename(filename)
    storage_path = f"raw/{name}"
    root = state.shooter_root(slug)
    project = state.shooter_project(slug)

    rv = project.find_raw_video(storage_path)
    if rv is None:
        raise HTTPException(status_code=404, detail=f"no raw video at {storage_path!r}")

    take_wav = audio_helpers.take_audio_path(root, storage_path, project=project)

    # Hosted mode: the worker pushed the peaks JSON to storage.
    if state.storage is not None and project._storage_scope is not None:
        # Hosted peaks are fixed at 3000 bins (worker-emitted); ignore requested bins.
        hosted_bins = 3000
        peaks_path = waveform_helpers.cache_path(take_wav, hosted_bins)
        # Serve from local cache when already pulled by a prior request.
        # WAV is never present locally in hosted mode so gate on peaks JSON only.
        if peaks_path.exists():
            result = waveform_helpers.PeaksResult.model_validate_json(peaks_path.read_text(encoding="utf-8"))
            return JSONResponse(result.model_dump(mode="json"))
        peaks_key = f"{project._storage_scope}/audio/{peaks_path.name}"
        pulled = False
        try:
            if state.storage.exists(peaks_key):
                peaks_path.parent.mkdir(parents=True, exist_ok=True)
                with state.storage.open_stream(peaks_key) as src, peaks_path.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                pulled = True
        except Exception as exc:
            logger.info("take peaks: storage pull failed: %s", exc)
        if not pulled:
            active_job = any(
                run_sync(state.jobs.find_active(kind="detect_beep", stage_number=n, shooter_slug=slug))
                is not None
                for n in rv.covers_stages
            )
            return JSONResponse({"pending": True, "active_job": active_job}, status_code=202)
        result = waveform_helpers.PeaksResult.model_validate_json(peaks_path.read_text(encoding="utf-8"))
        return JSONResponse(result.model_dump(mode="json"))

    # Local mode: compute from the take WAV a job published.
    wav = audio_helpers.published_take_audio(root, storage_path, project=project)
    if wav is None:
        return _server._audio_pending(state, slug, "take", storage_path=storage_path)
    result = waveform_helpers.ensure_peaks(wav, bins)
    return JSONResponse(result.model_dump(mode="json"))


@router.post("/api/shooters/{slug}/videos/suggest-coverage")
def suggest_coverage(
    request: Request,
    slug: str,
    body: SuggestCoverageRequest,
    user: User = Depends(_server.get_current_user),
) -> JSONResponse:
    """Propose which stages a file's wall-clock span covers.

    The SPA probes the file duration client-side (avoiding server-side
    ffprobe of remote R2 objects) and sends the span here. Returns a
    list of stage numbers whose match windows overlap the span, ordered
    by scorecard time, plus the resolved span in ISO format.

    Body: ``{"recorded_start": iso | null, "duration_s": float | null,
    "path": str | null}``.

    Returns ``{"covers_stages": [ints], "span": {"start": iso, "end": iso}
    | null}``; empty list and null span when no span is resolvable.

    Local mode only: when ``recorded_start`` is null but ``path`` is
    provided, the server resolves the span from the recording start the
    camera embedded in the file plus the ffprobe duration; without an
    embedded start, the file's birthtime/mtime is taken as the end.
    """
    state = request.app.state.splitsmith_state
    from .. import video_match as vm
    from ..config import StageData as EngineStageData
    from ..config import VideoMatchConfig

    project = state.shooter_project(slug)
    cfg = VideoMatchConfig()

    # Build the span.
    start_dt: datetime | None = body.recorded_start
    end_dt: datetime | None = None

    if start_dt is not None and body.duration_s is not None:
        end_dt = start_dt + timedelta(seconds=body.duration_s)
    elif body.path is not None and state.storage is None:
        # Local mode: resolve from file timestamps + ffprobe.
        root = state.shooter_root(slug)
        video_path = project.resolve_video_path(root, Path(body.path))
        duration: float | None = None
        try:
            duration = video_probe.probe(video_path, cache_dir=project.probes_path(root)).duration
        except video_probe.ProbeError:
            pass
        embedded_start = vm.embedded_recording_start(video_path)
        if duration is not None and embedded_start is not None:
            # A shared or copied file carries the copy time on disk;
            # the camera's own start is the only reliable anchor.
            start_dt = embedded_start
            end_dt = start_dt + timedelta(seconds=duration)
        elif duration is not None:
            st = video_path.stat()
            birth = getattr(st, "st_birthtime", None)
            end_dt = (
                datetime.fromtimestamp(birth, tz=UTC)
                if birth is not None
                else datetime.fromtimestamp(st.st_mtime, tz=UTC)
            )
            start_dt = end_dt - timedelta(seconds=duration)

    if start_dt is None or end_dt is None:
        return JSONResponse({"covers_stages": [], "span": None})

    # Gather stages that have a scorecard timestamp.
    engine_stages: list[EngineStageData] = []
    for stg in project.stages:
        if stg.scorecard_updated_at is not None:
            engine_stages.append(
                EngineStageData(
                    stage_number=stg.stage_number,
                    stage_name=stg.stage_name,
                    time_seconds=stg.time_seconds,
                    scorecard_updated_at=stg.scorecard_updated_at,
                )
            )

    covers = vm.stages_in_span(start_dt, end_dt, engine_stages, cfg.tolerance_minutes)
    return JSONResponse(
        {
            "covers_stages": covers,
            "span": {
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
            },
        }
    )


@router.get("/api/shooters/{slug}/automation")
def get_automation(request: Request, slug: str) -> JSONResponse:
    """Resolved automation settings + per-field provenance (#215 / #216).

    Combines the global YAML / env-var defaults with the bound
    project's override. The SPA renders provenance badges next to
    each toggle so users see which layer set the current value.
    ``cli_value`` is always None on the daemon path -- the server
    doesn't take per-call CLI flags.
    """
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    resolved = automation_settings.resolve_automation(
        project_override=project.automation,
    )
    return JSONResponse(
        {
            "settings": resolved.settings.model_dump(mode="json"),
            "provenance": {
                field: {
                    "source": prov.source,
                    "cli_value": prov.cli_value,
                    "project_value": prov.project_value,
                    "global_value": prov.global_value,
                }
                for field, prov in resolved.provenance.items()
            },
        }
    )


@router.post("/api/shooters/{slug}/project/nudges/dismiss")
def dismiss_nudge(request: Request, slug: str, req: NudgeDismissRequest) -> JSONResponse:
    """Persist a per-project nudge dismissal (#218 phase 4).

    Adds ``stage_number`` to ``MatchProject.nudges_dismissed_stages``
    when ``dismissed=True`` (idempotent), removes it otherwise.
    Returns the full project dump so the SPA can replace its
    cached state without a separate refetch.
    """
    state = request.app.state.splitsmith_state
    root = state.shooter_root(slug)
    project = state.shooter_project(slug)
    try:
        project.stage(req.stage_number)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    current = set(project.nudges_dismissed_stages)
    if req.dismissed:
        current.add(req.stage_number)
    else:
        current.discard(req.stage_number)
    project.nudges_dismissed_stages = sorted(current)
    project.save(root)
    return JSONResponse(project.model_dump(mode="json"))


@router.get("/api/shooters/{slug}/hitl-queue")
def get_hitl_queue(request: Request, slug: str) -> JSONResponse:
    """Items in the project that need human (or agent) attention (#219).

    Walks every primary video and emits one item per beep that's
    either missing (auto-detection found no candidate) or below the
    ``beep_low_confidence_threshold`` automation gate -- exactly
    the cases where the auto-trust chain didn't fire and a human
    has to pick. Manual entries and high-confidence reviewed beeps
    never appear here.

    Response shape (stable; consumed by the SPA AND the MCP):

        {
          "items": [
            {
              "kind": "beep_missing" | "beep_low_confidence",
              "stage_number": int,
              "video_id": str,
              "confidence": float | None,
              "suggested_action": str,
            },
            ...
          ],
          "threshold": float,
        }

    Ordered by stage number ascending; severity ties (a low-conf
    and a missing on the same stage) keep their stable insertion
    order. The SPA shows this as a project-level work queue; the
    MCP exposes it as a resource so an agent can drive the picks.
    """
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    resolved = automation_settings.resolve_automation(
        project_override=project.automation,
    )
    threshold = resolved.settings.beep_low_confidence_threshold
    items: list[dict] = []
    for stg in sorted(project.stages, key=lambda s: s.stage_number):
        primary = next(
            (v for v in stg.videos if v.role == "primary"),
            None,
        )
        if primary is None:
            continue
        if primary.beep_auto_detect_failed:
            items.append(
                {
                    "kind": "beep_missing",
                    "stage_number": stg.stage_number,
                    "video_id": primary.video_id,
                    "confidence": None,
                    "suggested_action": (
                        "Set the beep manually on the waveform: open the "
                        "stage's ingest panel and click the beep marker."
                    ),
                }
            )
            continue
        if primary.beep_source == "auto" and primary.beep_time is not None and not primary.beep_reviewed:
            # Either confidence is below the threshold OR the field
            # predates layer 3a (None) -- both warrant review.
            items.append(
                {
                    "kind": "beep_low_confidence",
                    "stage_number": stg.stage_number,
                    "video_id": primary.video_id,
                    "confidence": primary.beep_confidence,
                    "suggested_action": (
                        "Listen to the ranked candidates and pick the "
                        "correct beep, or nudge the timestamp on the "
                        "waveform."
                    ),
                }
            )
    return JSONResponse({"items": items, "threshold": threshold})


@router.get("/api/shooters/{slug}/project/match-analysis")
def get_match_analysis(request: Request, slug: str) -> JSONResponse:
    """Run the canonical video-match heuristic over the project and return
    per-stage windows + per-video classification.

    Single source of truth for the SPA's match-window timeline:
    tolerance, window edges, and per-video classification are all
    produced by :mod:`splitsmith.video_match`. Future heuristic
    improvements (per-stage tolerance, ML-based scoring, confidence
    bands) extend this endpoint rather than adding policy to the SPA.
    """
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    return JSONResponse(project.match_analysis().model_dump(mode="json"))


@router.post("/api/shooters/{slug}/scoreboard/import")
def import_scoreboard(request: Request, slug: str, req: ScoreboardImportRequest) -> JSONResponse:
    state = request.app.state.splitsmith_state
    root = state.shooter_root(slug)
    project = state.shooter_project(slug)
    try:
        project.import_scoreboard(req.data, overwrite=req.overwrite)
    except ScoreboardImportConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    project.save(root)
    return JSONResponse(project.model_dump(mode="json"))


@router.get("/api/shooters/{slug}/scoreboard/source")
def scoreboard_source(request: Request, slug: str) -> JSONResponse:
    """Report whether the offline JSON or the live API will serve requests.

    The SPA renders a "loaded from local JSON, no network used" indicator
    when ``mode == "local"`` so the user can verify the offline path
    without watching dev tools.
    """
    state = request.app.state.splitsmith_state
    local_path = _server._local_match_path(state.shooter_root(slug))
    local = local_path.exists()
    # Informational since #1016: v1 reads are anonymous, a token only
    # buys the identified consumer's higher rate limit.
    http_ready = bool(os.environ.get("SPLITSMITH_SSI_TOKEN"))
    return JSONResponse(
        {
            "mode": "local" if local else "online",
            "local_match_json_path": str(local_path) if local else None,
            "http_token_set": http_ready,
        }
    )


@router.post("/api/shooters/{slug}/scoreboard/upload")
def scoreboard_upload(request: Request, slug: str, req: ScoreboardUploadRequest) -> JSONResponse:
    """Accept a dropped SSI ``match.json`` and populate the project.

    Three input shapes are accepted (see ``LocalJsonScoreboard``):
    pure SSI v1 ``MatchData``, a richer combined v1+stages format, or
    the legacy ``examples/`` shape. The file is written to
    ``<project>/scoreboard/match.json`` *first*, so a subsequent reload
    still finds the offline source even if populate throws. The
    offline ``LocalJsonScoreboard`` then parses it and -- when the
    file carries per-competitor stage results for exactly one
    competitor -- auto-pins that competitor and merges the times so
    the user lands on a fully-populated stage list in one drop.
    """
    state = request.app.state.splitsmith_state
    root = state.shooter_root(slug)
    local_path = _server._local_match_path(root)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(
        json.dumps(req.data, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    try:
        scoreboard = LocalJsonScoreboard(local_path)
    except Exception as exc:  # validation, KeyError, etc.
        raise HTTPException(
            status_code=400,
            detail=f"dropped JSON is not a recognized scoreboard payload: {exc}",
        ) from exc

    project = state.shooter_project(slug)
    try:
        project.populate_from_match_data(scoreboard.match, overwrite=req.overwrite)
    except ScoreboardImportConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Auto-pin path: a single-competitor richer file maps cleanly to
    # "this is me." Multi-competitor files (e.g. the upstream-issue
    # combined shape with multiple shooters' results) leave the user
    # to pick via the shooter pinner -- we don't guess.
    merged = 0
    default_cid = scoreboard.default_competitor_id()
    if default_cid is not None and scoreboard.has_stage_times:
        try:
            results = scoreboard.get_stage_times(
                scoreboard.content_type or 0,
                scoreboard.match_id or 0,
                default_cid,
            )
        except (KeyError, ScoreboardError):
            results = None
        if results is not None:
            merged = project.merge_stage_times(results)
            project.selected_competitor_id = default_cid
            # Resolve the picked competitor inside the parsed match.
            # We persist shooter_id (for the global "this is me"
            # identity) and competitor_name (so the SPA can show
            # who's pinned without re-fetching MatchData on every
            # render).
            picked = next(
                (c for c in scoreboard.match.competitors if c.id == default_cid),
                None,
            )
            if picked is not None:
                project.selected_shooter_id = picked.shooterId
                project.competitor_name = picked.name
                project.competitor_division = picked.division
                project.adopt_scoreboard_club(picked.club)
    project.save(root)
    return JSONResponse({**project.model_dump(mode="json"), "stage_times_merged": merged})


@router.get("/api/shooters/{slug}/scoreboard/search")
def scoreboard_search(request: Request, slug: str, q: str = Query("", min_length=0)) -> JSONResponse:
    """Search the active scoreboard source for matches by free-text query."""
    state = request.app.state.splitsmith_state
    with _server._resolve_scoreboard_client(state.shooter_root(slug)) as client:
        try:
            refs = client.search_matches(q)
        except ScoreboardError as exc:
            _server._raise_scoreboard_http(exc)
    return JSONResponse([ref.model_dump(mode="json") for ref in refs])


@router.post("/api/shooters/{slug}/scoreboard/fetch")
def scoreboard_fetch(request: Request, slug: str, req: ScoreboardFetchRequest) -> JSONResponse:
    """Fetch a full match (cache-first) and populate the project.

    When the project already has a pinned competitor (carried over from
    a previous session), auto-merge their stage times in the same
    round-trip. New picks (no pin yet) still need ``/select-shooter``
    afterwards -- the SPA flow stays the same.
    """
    state = request.app.state.splitsmith_state
    root = state.shooter_root(slug)
    with _server._resolve_scoreboard_client(root) as client:
        try:
            match_data = client.get_match(req.content_type, req.match_id)
        except MatchNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ScoreboardError as exc:
            _server._raise_scoreboard_http(exc)
    project = state.shooter_project(slug)
    try:
        project.populate_from_match_data(match_data, overwrite=req.overwrite)
    except ScoreboardImportConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    merged = 0
    if project.selected_competitor_id is not None:
        try:
            merged = _server._fetch_and_merge_stage_times(
                root,
                project,
                req.content_type,
                req.match_id,
                project.selected_competitor_id,
            )
        except HTTPException:
            # Stage times failed (upstream not shipped yet, etc.).
            # Persist the populated stage shell anyway so the user
            # has something to work with; they can hit refresh-times
            # once the upstream resolves.
            project.save(root)
            raise
    else:
        project.save(root)
    return JSONResponse({**project.model_dump(mode="json"), "stage_times_merged": merged})


@router.get("/api/shooters/{slug}/scoreboard/match-data")
def scoreboard_match_data(request: Request, slug: str) -> JSONResponse:
    """Return the resolved ``MatchData`` for the project's loaded match.

    The SPA needs this to map a picked ``shooterId`` to the per-match
    ``competitor_id`` before calling ``/select-shooter``. We don't
    denormalise that mapping onto the ``MatchProject`` because it
    drifts when upstream re-fetches; serving on demand keeps the
    cache as the single source of truth.
    """
    state = request.app.state.splitsmith_state
    root = state.shooter_root(slug)
    project = state.shooter_project(slug)
    if project.scoreboard_match_id is None or project.scoreboard_content_type is None:
        raise HTTPException(
            status_code=404,
            detail="project has no scoreboard match loaded yet",
        )
    try:
        ct = project.scoreboard_content_type
        mid = int(project.scoreboard_match_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                "project's scoreboard_match_id isn't numeric; this match "
                "predates the v1 wiring (#50). Re-import via /upload or /fetch."
            ),
        ) from exc
    with _server._resolve_scoreboard_client(root) as client:
        try:
            match_data = client.get_match(ct, mid)
        except MatchNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ScoreboardError as exc:
            _server._raise_scoreboard_http(exc)
    return JSONResponse(match_data.model_dump(mode="json"))


@router.get("/api/shooters/{slug}/scoreboard/shooter/search")
def scoreboard_shooter_search(request: Request, slug: str, q: str = Query("", min_length=0)) -> JSONResponse:
    """Find shooters by name. Offline mode searches this match's
    competitor list only; online mode hits the live shooter index."""
    state = request.app.state.splitsmith_state
    if not q.strip():
        return JSONResponse([])
    with _server._resolve_scoreboard_client(state.shooter_root(slug)) as client:
        try:
            refs = client.find_shooter(q)
        except ScoreboardError as exc:
            _server._raise_scoreboard_http(exc)
    return JSONResponse([ref.model_dump(mode="json") for ref in refs])


@router.post("/api/shooters/{slug}/scoreboard/select-shooter")
def scoreboard_select_shooter(request: Request, slug: str, req: SelectShooterRequest) -> JSONResponse:
    """Pin (shooter_id, competitor_id) and merge stage times into the project.

    Validates the competitor against the loaded match before
    persisting -- a wrong cid (typoed by hand, or the user picked a
    shooter who isn't in this match) shouldn't leave the project
    with an invalid pin that breaks every subsequent refresh.

    Failure modes:
    - 409 ``no_match_loaded``: project has no scoreboard match yet
    - 404 ``competitor_not_in_match``: the cid isn't in the loaded
      MatchData -- pin not persisted, user is asked to re-pick
    - 400 ``stage_times_offline_pure_matchdata``: offline source
      carries no stage results; pin *is* persisted so refresh-times
      can retry once the user drops a richer JSON or goes online
    - 502 ``scoreboard_offline``: transient upstream issue;
      pin persisted so refresh-times retries on the same selection
    """
    state = request.app.state.splitsmith_state
    root = state.shooter_root(slug)
    project = state.shooter_project(slug)
    if project.scoreboard_match_id is None or project.scoreboard_content_type is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "project has no scoreboard match loaded; pick a match "
                "(drop JSON or fetch) before pinning a shooter"
            ),
        )
    try:
        ct = project.scoreboard_content_type
        mid = int(project.scoreboard_match_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="project's scoreboard_match_id isn't numeric",
        ) from exc

    # Validate the competitor is actually in this match before
    # persisting. Cheap server-side guard (uses the cached MatchData),
    # avoids the "I picked a shooter and now my project is in a bad
    # state" pattern. 404 with a discrete code so the SPA can prompt
    # the user to pick again rather than rendering an upstream banner.
    with _server._resolve_scoreboard_client(root) as client:
        try:
            match_data = client.get_match(ct, mid)
        except ScoreboardError as exc:
            _server._raise_scoreboard_http(exc)
    picked = next(
        (c for c in match_data.competitors if c.id == req.competitor_id),
        None,
    )
    if picked is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "competitor_not_in_match",
                "message": (f"competitor {req.competitor_id} isn't in this match. Pick a different shooter."),
            },
        )

    project.selected_shooter_id = req.shooter_id
    project.selected_competitor_id = req.competitor_id
    # Persist the human-readable name so the SPA's collapsed
    # scoreboard summary doesn't have to re-fetch MatchData just to
    # render "pinned: Mathias Rinaldo" instead of an integer id.
    project.competitor_name = picked.name
    project.competitor_division = picked.division
    project.adopt_scoreboard_club(picked.club)
    project.save(root)

    merged = _server._fetch_and_merge_stage_times(root, project, ct, mid, req.competitor_id)
    return JSONResponse({**project.model_dump(mode="json"), "stage_times_merged": merged})


@router.post("/api/shooters/{slug}/scoreboard/refresh-times")
def scoreboard_refresh_times(request: Request, slug: str) -> JSONResponse:
    """Re-pull and re-merge stage times for the pinned competitor.

    Invalidates every cached stage-times entry for the match (not
    just the pinned competitor) because in-progress matches often
    update multiple shooters at once and a fresh pull is cheap. Use
    this after the user knows the upstream has new scorecards.
    """
    state = request.app.state.splitsmith_state
    root = state.shooter_root(slug)
    project = state.shooter_project(slug)
    if (
        project.selected_competitor_id is None
        or project.scoreboard_match_id is None
        or project.scoreboard_content_type is None
    ):
        raise HTTPException(
            status_code=409,
            detail="pin a shooter first; refresh-times has nothing to re-fetch",
        )
    try:
        ct = project.scoreboard_content_type
        mid = int(project.scoreboard_match_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="project's scoreboard_match_id isn't numeric",
        ) from exc

    # Drop every cached stage_times entry for this match so the
    # next get_stage_times call goes upstream.
    cache_dir = root / "scoreboard" / "cache"
    if cache_dir.exists():
        cache = CachingScoreboardClient(_NoOpClient(), cache_dir)
        cache.invalidate_match_stage_times(ct, mid)

    merged = _server._fetch_and_merge_stage_times(root, project, ct, mid, project.selected_competitor_id)
    return JSONResponse({**project.model_dump(mode="json"), "stage_times_merged": merged})


@router.post("/api/shooters/{slug}/project/placeholder-stages")
def create_placeholder_stages(request: Request, slug: str, req: PlaceholderStagesRequest) -> JSONResponse:
    """Create N placeholder stages so source-first ingest works without a
    scoreboard. A real scoreboard import later overlays the placeholders
    and preserves video assignments by ``stage_number``."""
    state = request.app.state.splitsmith_state
    root = state.shooter_root(slug)
    project = state.shooter_project(slug)
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
    project.save(root)
    return JSONResponse(project.model_dump(mode="json"))


@router.post("/api/shooters/{slug}/videos/scan", response_model=ScanResponse)
async def scan_videos(request: Request, slug: str, req: ScanRequest) -> ScanResponse:
    state = request.app.state.splitsmith_state
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
        # Walk recursively so the picker can accept a top-level folder
        # whose videos live in subdirectories (mirrors the relink
        # scanner's behaviour). ``rglob`` does not follow directory
        # symlinks by default, which avoids cycles on network shares.
        for entry in sorted(source.rglob("*")):
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in VIDEO_EXTENSIONS:
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

    root = state.shooter_root(slug)
    registered: list[str] = []
    skipped: list[str] = []
    elsewhere: dict[str, str] = {}
    if state.storage is None and current_match_root.get() is not None:
        from . import footage_sort_api

        # Backfills every shooter's project, this one's included, so
        # the project loads after it or its save would undo the fill.
        # Off the event loop: the backfill stats every registered source,
        # and one on a dead network mount would stall the server (#1227).
        elsewhere = await run_in_threadpool(footage_sort_api.imported_elsewhere, state, slug)
    project = state.shooter_project(slug)
    # ``register_video`` hands back the existing entry for a file this
    # shooter already has (same source, or a byte copy by fingerprint);
    # that is not an import, and counting it as one told the page a
    # video was added when none was (#1233).
    had_before = {str(v.path) for v in project.all_videos()}
    added_from: dict[str, str] = {}
    for entry in candidates:
        if elsewhere:
            try:
                reason = elsewhere.get(clip_fingerprint(entry) or "")
            except OSError:
                reason = None
            if reason is not None:
                skipped.append(f"{entry.name}: {reason}")
                continue
        try:
            video = project.register_video(
                entry,
                root,
                link_mode=req.link_mode,  # type: ignore[arg-type]
            )
        except (FileNotFoundError, ValueError) as exc:
            skipped.append(f"{entry.name}: {exc}")
            continue
        stored = str(video.path)
        if stored in had_before:
            skipped.append(f"{entry.name}: already imported for this shooter as {stored}")
            continue
        if stored in added_from:
            skipped.append(f"{entry.name}: same recording as {added_from[stored]} in this import")
            continue
        added_from[stored] = entry.name
        registered.append(stored)

    auto_assigned: dict[int, str] = {}
    auto_secondary: dict[int, list[str]] = {}
    if req.auto_assign_primary:
        suggestions = project.auto_match(root)
        if state.storage is None:
            # With squad mates in the match, this shooter's scorecard
            # window also holds their runs: keep only the clips the
            # footage sort, reading everyone's scorecards, puts here.
            from . import footage_sort_api

            suggestions = footage_sort_api.keep_this_shooters(state, slug, project, root, suggestions)
        for stage_num, suggestion in suggestions.items():
            stage = project.stage(stage_num)
            if suggestion.primary is not None:
                # A new primary only lands on a stage without one; a
                # stage that has one keeps it and gets nothing from
                # clips that are not the same run as it.
                if stage.primary() is not None:
                    continue
                project.assign_video(suggestion.primary, to_stage_number=stage_num, role="primary")
                auto_assigned[stage_num] = str(suggestion.primary)
            for video_path in suggestion.secondaries:
                project.assign_video(video_path, to_stage_number=stage_num, role="secondary")
                auto_secondary.setdefault(stage_num, []).append(str(video_path))

    if last_dir is not None:
        project.last_scanned_dir = str(last_dir)

    project.save(root)

    # Queue auto-beep for every freshly-attached video (#67). Done
    # after save so the persisted state reflects the assignment when
    # the worker re-loads the project.
    attached = list(auto_assigned.items())
    attached += [(n, p) for n, paths in auto_secondary.items() for p in paths]
    for stage_num, video_path in attached:
        stage = project.stage(stage_num)
        video = next((v for v in stage.videos if str(v.path) == video_path), None)
        if video is not None:
            await _server._auto_queue_beep_if_needed(state, slug, project, stage_num, video)

    return ScanResponse(
        registered=registered,
        auto_assigned=auto_assigned,
        auto_secondary=auto_secondary,
        skipped=skipped,
    )


@router.get("/api/shooters/{slug}/videos/link-status", response_model=LinkStatusResponse)
def get_link_status(request: Request, slug: str) -> LinkStatusResponse:
    """Per-video status of ``raw/<name>`` symlinks.

    Lets the SPA badge broken / missing entries on the project page
    without an extra walk on every project fetch.
    """
    state = request.app.state.splitsmith_state
    from .. import relink as relink_mod

    root = state.shooter_root(slug)
    project = state.shooter_project(slug)
    infos = relink_mod.inspect_links(project, root)
    return LinkStatusResponse(
        entries=[
            LinkStatusEntry(
                video_id=info.video_id,
                name=info.name,
                link_path=str(info.link_path),
                current_target=str(info.target) if info.target is not None else None,
                status=info.status,
            )
            for info in infos
        ]
    )


@router.post("/api/shooters/{slug}/videos/relink/scan", response_model=RelinkScanResponse)
def relink_scan(request: Request, slug: str, req: RelinkScanRequest) -> RelinkScanResponse:
    """Recursive dry-run: walk ``search_root`` and report per-video
    candidates without touching the filesystem.
    """
    state = request.app.state.splitsmith_state
    from .. import relink as relink_mod

    search_root = Path(req.search_root).expanduser()
    try:
        index = relink_mod.index_search_root(search_root)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    project = state.shooter_project(slug)
    infos = relink_mod.inspect_links(project, state.shooter_root(slug))
    plan = relink_mod.plan_relink(infos, index)
    return RelinkScanResponse(
        search_root=str(search_root.resolve()),
        entries=[
            RelinkEntryResponse(
                video_id=entry.video_id,
                name=entry.name,
                link_path=str(entry.link_path),
                current_target=(str(entry.current_target) if entry.current_target is not None else None),
                current_status=entry.current_status,
                candidates=[str(p) for p in entry.candidates],
                chosen_path=str(entry.chosen_path) if entry.chosen_path is not None else None,
                ambiguous=entry.ambiguous,
                found=entry.found,
            )
            for entry in plan
        ],
    )


@router.post("/api/shooters/{slug}/videos/relink/apply", response_model=RelinkApplyResponse)
def relink_apply(request: Request, slug: str, req: RelinkApplyRequest) -> RelinkApplyResponse:
    """Apply a user-confirmed set of symlink rewrites.

    ``project.json`` is not modified -- only the symlinks under
    ``raw/`` are repointed. The video_id is preserved so the SPA
    can match the response back to its rows.
    """
    state = request.app.state.splitsmith_state
    from .. import relink as relink_mod

    if not req.decisions:
        raise HTTPException(status_code=400, detail="decisions must be non-empty")
    project = state.shooter_project(slug)
    infos = {info.video_id: info for info in relink_mod.inspect_links(project, state.shooter_root(slug))}
    decisions: list[tuple[Path, Path]] = []
    id_for_link: dict[Path, str] = {}
    for video_id, target_str in req.decisions.items():
        info = infos.get(video_id)
        if info is None:
            raise HTTPException(status_code=400, detail=f"unknown video_id: {video_id}")
        if info.status == "not_a_symlink":
            raise HTTPException(
                status_code=400,
                detail=f"{info.name} is a regular file, not a symlink -- relink not supported",
            )
        target = Path(target_str).expanduser()
        if not target.exists():
            raise HTTPException(status_code=400, detail=f"target does not exist: {target}")
        if not target.is_file():
            raise HTTPException(status_code=400, detail=f"target is not a file: {target}")
        decisions.append((info.link_path, target))
        id_for_link[info.link_path] = video_id
    applied = relink_mod.apply_relink(decisions)
    return RelinkApplyResponse(
        applied=[
            RelinkAppliedEntry(
                video_id=id_for_link.get(entry.link_path, ""),
                name=entry.name,
                link_path=str(entry.link_path),
                previous_target=(str(entry.previous_target) if entry.previous_target is not None else None),
                new_target=str(entry.new_target),
            )
            for entry in applied
        ]
    )


@router.post("/api/shooters/{slug}/project/settings")
def update_settings(request: Request, slug: str, req: SettingsRequest) -> JSONResponse:
    """Update storage path overrides. Any None field is left unchanged;
    pass an empty string to clear back to the project-root default.

    If a path field is changing and the *old* directory contains files,
    return 409 with a structured ``non_empty_old_dirs`` payload unless
    ``confirm=True`` is sent. Existing files are not auto-migrated --
    the warning lets the caller surface "you'll be leaving these behind".
    """
    state = request.app.state.splitsmith_state
    root = state.shooter_root(slug)
    project = state.shooter_project(slug)
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
        old_path = resolver_for[fname](root)
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

    # #215 -- patch the automation override on the project.
    # Replacing the whole sub-object keeps the patch shape simple
    # (the override is small enough that field-level merging would
    # add complexity without a real benefit).
    if req.automation is not None:
        project.automation = req.automation

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
        target = resolver(root)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"cannot create directory {target}: {exc}",
            ) from exc

    project.save(root)
    return JSONResponse(project.model_dump(mode="json"))


@router.patch("/api/shooters/{slug}/compare-camera")
def set_compare_camera(request: Request, slug: str, req: CompareCameraRequest) -> JSONResponse:
    """Persist which camera this shooter contributes to grids and trims.

    Validated against the shooter's actual videos so a typo fails here
    rather than silently exporting every tile from the primary. Skipped
    stages don't count -- they contribute no footage to a grid.
    """
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    try:
        camera_select.validate_camera(
            [stage.videos for stage in project.stages if not stage.skipped], req.camera
        )
    except camera_select.CameraResolutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    project.compare_camera = req.camera
    project.save(state.shooter_root(slug))
    return JSONResponse(project.model_dump(mode="json"))


@router.get("/api/shooters/{slug}/identity")
def get_shooter_identity(request: Request, slug: str) -> JSONResponse:
    """Where this shooter's look comes from (``match``, ``book`` or
    ``none``) and the identity a render draws for them."""
    state = request.app.state.splitsmith_state
    return JSONResponse(_identity_view(state, state.shooter_project(slug)))


@router.post("/api/shooters/{slug}/identity/use-book")
def use_shooter_book(request: Request, slug: str) -> JSONResponse:
    """Drop this match's own record so the shooter book applies again.
    The book is not touched. Refused (409) when the book holds no look for
    this shooter: dropping the record would leave them with nothing."""
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    book = shooter_book_module.load_snapshot(state.shooter_book)
    if not shooter_book_module.is_set(book.get(project.selected_shooter_id)):
        raise HTTPException(status_code=409, detail="The shooter book has no look for this shooter.")
    previous = project.identity.logo
    project.identity = ShooterIdentity()
    project.save(state.shooter_root(slug))
    _remove_local_logo(state, slug, previous)
    return JSONResponse(_identity_view(state, project))


@router.patch("/api/shooters/{slug}/identity")
def set_shooter_identity(request: Request, slug: str, req: ShooterIdentityRequest) -> JSONResponse:
    """Set the shooter's accent and club line (#1243). Validated by the
    identity model itself, so a bad colour or an over-long club line
    is a 422 that names the field and writes nothing."""
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    current = _book_seeded_identity(state, project, slug)
    fields = req.model_dump(exclude_unset=True)
    try:
        project.identity = ShooterIdentity(
            accent=fields.get("accent", current.accent),
            club=fields.get("club", current.club),
            logo=current.logo,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()[0]["msg"]) from exc
    project.save(state.shooter_root(slug))
    if req.scope == "book":
        run_sync(_save_to_book(state, project, slug))
    return JSONResponse(project.model_dump(mode="json"))


@router.post("/api/shooters/{slug}/identity/logo")
async def upload_shooter_logo(
    request: Request, slug: str, file: UploadFile = File(...), scope: Literal["book", "match"] = Form("book")
) -> JSONResponse:
    """Store the shooter's logo (#1243): a PNG, JPEG or WebP of at most
    ``LOGO_MAX_BYTES``, content-named under ``<shooter>/identity/``
    (hosted: under the project's storage scope as well, which is the
    key the sync push writes), the previous logo file removed. The
    type is sniffed, never trusted from the name: SVG can script and
    a template loads the logo in Chromium."""
    state = request.app.state.splitsmith_state
    from PIL import Image, UnidentifiedImageError

    project = state.shooter_project(slug)
    data = await file.read(LOGO_MAX_BYTES + 1)
    if len(data) > LOGO_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"logo is over {LOGO_MAX_BYTES // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(status_code=422, detail="empty file")
    try:
        with Image.open(io.BytesIO(data)) as image:
            fmt = (image.format or "").upper()
            side = max(image.size)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        # A decompression bomb is an Exception, not an OSError: without
        # this it was a 500 (review of #1243).
        raise HTTPException(status_code=422, detail="logo must be a PNG, JPEG or WebP image") from exc
    # MPO is what a phone camera writes: a JPEG with a multi-picture
    # marker. Chromium decodes it as a JPEG.
    ext = {"PNG": "png", "JPEG": "jpeg", "MPO": "jpeg", "WEBP": "webp"}.get(fmt)
    if ext is None:
        raise HTTPException(status_code=422, detail="logo must be a PNG, JPEG or WebP image")
    if side > LOGO_MAX_SIDE:
        raise HTTPException(status_code=422, detail=f"logo is over {LOGO_MAX_SIDE} px on a side")
    name = logo_name(data, ext)
    previous = project.identity.logo
    logo_dir = _server._identity_logo_dir(state, slug)
    logo_dir.mkdir(parents=True, exist_ok=True)
    (logo_dir / name).write_bytes(data)
    storage = project._storage  # type: ignore[attr-defined]
    storage_scope = project._storage_scope  # type: ignore[attr-defined]
    if storage is not None and storage_scope is not None:
        storage.write_bytes(f"{storage_scope}/{LOGO_DIR}/{name}", data)
    project.identity = project.identity.model_copy(update={"logo": name})
    project.save(state.shooter_root(slug))
    if previous != name:
        _remove_local_logo(state, slug, previous)
    if scope == "book":
        await _save_to_book(state, project, slug)
    return JSONResponse(project.model_dump(mode="json"))


@router.get("/api/shooters/{slug}/identity/logo")
def get_shooter_logo(request: Request, slug: str) -> FileResponse:
    """The shooter's logo (#1249): for the Compare roster, the shooter
    chips and the share views. On the share GET allowlist: the logo is
    already drawn into every video the share shows, and the alias binds
    the request to that match's shooters. Content-named and sniffed on
    upload (PNG, JPEG, WebP, never SVG); served with ``nosniff``."""
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    book = _server._roster_book(state)
    if identity_source(project, book) == "book":
        entry = book.get(project.selected_shooter_id)
        path = book.logo_path(entry) if entry is not None else None
    else:
        path = ensure_local_logo(project, state.shooter_root(slug))
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="no logo")
    media = {".png": "image/png", ".jpeg": "image/jpeg", ".jpg": "image/jpeg", ".webp": "image/webp"}.get(
        path.suffix.lower()
    )
    if media is None:
        raise HTTPException(status_code=404, detail="no logo")
    return FileResponse(
        path,
        media_type=media,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"},
    )


@router.delete("/api/shooters/{slug}/identity/logo")
def remove_shooter_logo(
    request: Request, slug: str, scope: Literal["book", "match"] = "book"
) -> JSONResponse:
    """Clear the shooter's logo (#1243) and remove the local file; a
    hosted copy is swept by the next push's gc."""
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    previous = project.identity.logo
    project.identity = project.identity.model_copy(update={"logo": None})
    project.save(state.shooter_root(slug))
    _remove_local_logo(state, slug, previous)
    if scope == "book":
        run_sync(_save_to_book(state, project, slug))
    return JSONResponse(project.model_dump(mode="json"))


@router.get("/api/shooters/{slug}/coach/distributions")
def get_match_coach_distributions(request: Request, slug: str) -> JSONResponse:
    """Match-level distributions across every stage with an audit
    JSON. Stages without an audit are silently skipped -- they
    haven't been audited yet so they'd just dilute the average.
    """
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    cfg = coach_module.auto_classify_config()
    triples: list[tuple[int, str, list[dict[str, Any]]]] = []
    for stg in project.stages:
        stage_payload, _ = state.load_audit(slug, stg.stage_number)
        if stage_payload is None:
            continue
        shots = stage_payload.get("shots") or []
        if not isinstance(shots, list):
            continue
        triples.append((stg.stage_number, stg.stage_name, shots))
    result = coach_distributions_module.match_distributions(
        stages=triples,
        config=cfg,
        non_standard_starts=frozenset(
            s.stage_number for s in project.stages if not match_model.is_standard_start(s.start)
        ),
    )
    # ``coaching_note`` is private text, same as on the coach GET
    # (``_build_coach_response``) -- this route is on ``_SHARE_PATH_RE``,
    # so a share read gets every top-shot and flagged entry with its
    # note stripped. ``match_distributions`` stays pure; the route is
    # what knows whether this is a share request.
    if current_share_request.get():
        for entry in (*result.top_splits, *result.top_transitions):
            entry.coaching_note = None
        for entry in result.flagged_shots:
            entry.coaching_note = None
    return JSONResponse(result.model_dump())


@router.get("/api/shooters/{slug}/videos/stream", response_model=None)
def stream_video(
    request: Request,
    slug: str,
    path: str = Query(...),
    kind: Literal["auto", "trim", "source", "proxy", "web", "scrub"] = Query("auto"),
    stage: int | None = Query(None),
) -> FileResponse | RedirectResponse:
    """Serve a registered video file with HTTP Range support.

    ``kind`` selects which file backs the response:

    - ``trim``: per-video short-GOP MP4 (``<trimmed>/stage<N>_cam_<video_id>_trimmed.mp4``);
      404 if not built yet. Frame-accurate seeking makes audit-screen scrubbing fast.
    - ``web``: the trim's 720p faststart rendition (#1031). Hosted streams
      it from object storage; local serves it from disk when it is fresh
      (#1192). Falls back to the trim, then the source.
    - ``scrub``: the Audit players' pin (#1209): the trim's fresh 720p
      rendition, else the trim, else 404 -- never the source. A re-cut
      deletes both while it encodes, and a pinned player must error and
      remount on the new version, not play the source under trim
      offsets. On a mirror the pushed rendition stands in for the trim.
    - ``source``: the original camera file.
    - ``proxy``: low-res fast-seek MP4 (``raw_proxy/<name>.mp4``). In hosted mode,
      returns 425 ``preview_generating`` when the proxy object is absent - never
      silently falls back to source. In local mode, falls back to source on miss.
    - ``auto`` (default): trim if present, source otherwise.

    In hosted mode (storage bound, relative path) the endpoint presigns a GET
    URL and returns a 307. The browser reads ranged bytes directly from R2 - no
    whole-file mirror through this process.

    In local mode the endpoint returns FileResponse from disk, range-native.

    Validates that ``path`` matches a video registered to the project
    (any stage, any role, or unassigned) so the endpoint cannot be
    used as a generic file-read primitive. ``stage`` restricts the
    match to that stage: a multi-stage single take registers one path
    on several stages, and each has its own trim (#1211). Without it
    the first registration answers.
    """
    state = request.app.state.splitsmith_state
    root = state.shooter_root(slug)
    project = state.shooter_project(slug)
    # ``stage`` picks the registration: a multi-stage single take has
    # one StageVideo, and one trim, per stage on the same path (#1211).
    located = project.find_video(Path(path), stage_number=stage)
    if located is None:
        raise HTTPException(
            status_code=404,
            detail=f"video not registered with project: {path}",
        )
    stage, video = located

    storage = state.storage
    raw_str = str(video.path)
    # "hosted" here means "redirect the browser to a presigned URL". A
    # storage whose bytes aren't browser-reachable (FilesystemStorage)
    # takes the local byte-serving path below, same as no storage at all.
    is_hosted = storage is not None and storage.supports_presigned_get and not video.path.is_absolute()

    if kind == "proxy":
        if is_hosted and raw_str.startswith("raw/"):
            # hosted mode: redirect to proxy key in R2; 425 if not yet generated
            from ..proxy import proxy_key_for

            proxy_key = proxy_key_for(raw_str)
            if not storage.exists(proxy_key):  # type: ignore[union-attr]
                raise HTTPException(
                    status_code=425,
                    detail={
                        "code": "preview_generating",
                        "message": "proxy preview not ready yet - the worker is still generating it",
                        "path": raw_str,
                    },
                )
            return _server.serve_media(storage, proxy_key, root / proxy_key, content_type="video/mp4")
        else:
            # local mode: try cached proxy on disk; fall through to source on miss
            _resp = _server._serve_proxy(state, root, video)
            if _resp is not None:
                return _resp
            # kind=proxy + local + no proxy - serve source below

    if is_hosted:
        # hosted mode: resolve the R2 key and redirect; never mirror
        if kind == "scrub" and stage is not None:
            return _hosted_scrub(storage, project, root, stage, video)  # type: ignore[arg-type]
        if kind == "web" and stage is not None:
            web_resp = _server._hosted_web_redirect(storage, project, root, stage, video)  # type: ignore[arg-type]
            if web_resp is not None:
                return web_resp
        if kind in ("auto", "trim", "web") and stage is not None:
            # check for trim in storage without downloading
            local_mp4 = audio_helpers.trimmed_video_path(root, stage.stage_number, video, project=project)
            trim_key = audio_helpers._storage_trim_key(project, local_mp4)
            if trim_key is not None and storage.exists(trim_key):  # type: ignore[union-attr]
                return _server.serve_media(storage, trim_key, local_mp4, content_type="video/mp4")
            if _server._is_mirror():
                # Web-only mirror (spec 2026-09-27 v1.1): the rendition
                # is the trim. Native matches keep "trim never
                # substitutes": their audit scrubbing needs the real GOP.
                web_resp = _server._hosted_web_redirect(storage, project, root, stage, video)  # type: ignore[arg-type]
                if web_resp is not None:
                    return web_resp
            if kind == "trim":
                raise HTTPException(
                    status_code=404,
                    detail=f"trimmed clip not built yet for {path}",
                )
            # kind=auto: fall through to source redirect below
        return _server.serve_media(
            storage, raw_str, root / raw_str, content_type=_server.video_media_type(raw_str)
        )

    # local mode: disk-based serving. ``web`` and ``scrub`` serve the
    # trim's fresh 720p rendition when there is one, else the trim;
    # ``trim`` never substitutes. ``trim`` and ``scrub`` are pins: no
    # trim is a 404, never the source (#1209).
    served_path: Path | None = None
    if kind in ("auto", "trim", "web", "scrub") and stage is not None:
        # Per-video short-GOP trim is keyed per role: each angle has
        # its own scrub clip cut around its own beep.
        trimmed = audio_helpers.pull_trimmed_video(root, stage.stage_number, video, project=project)
        if trimmed.exists():
            served_path = trimmed.resolve()
            if kind in ("web", "scrub"):
                web = audio_helpers.fresh_web_trim(trimmed)
                if web is not None:
                    served_path = web.resolve()
        elif _server._is_mirror():
            # Web-only mirror on a storage without presigned GET
            # (#1078; FilesystemStorage in dev and tests): the anchor
            # reports "trim" measured on the pushed params, so serve
            # the rendition those params describe, as the presigned
            # branch above does. Without it kind=trim 404ed.
            web = audio_helpers.try_pull_web_trim(
                project,
                audio_helpers.trimmed_video_path(root, stage.stage_number, video, project=project),
            )
            if web is not None:
                served_path = web.resolve()
    if served_path is None:
        if kind == "trim" or (kind == "scrub" and stage is not None):
            raise HTTPException(
                status_code=404,
                detail=f"trimmed clip not built yet for {path}",
            )
        served_path = project.resolve_video_path(root, video.path).resolve()
        # Same structured shape as detect-beep / trim / preview so
        # the SPA's "reconnect external storage" surface is uniform.
        ensure_source_reachable(stage.stage_number if stage is not None else None, served_path)

    return FileResponse(
        served_path,
        media_type=_server.video_media_type(served_path),
        filename=served_path.name,
        content_disposition_type="inline",
    )


@router.get("/api/shooters/{slug}/fs/list", response_model=FsListing)
def fs_list(
    request: Request,
    slug: str,
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

    Local mode only (``route_scope.LOCAL_ONLY_ROUTES``): it browses the
    machine the server runs on.
    """
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    target = Path(path).expanduser() if path else _server._default_start(project.last_scanned_dir)
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

    probes_dir = project.probes_path(state.shooter_root(slug))
    thumbs_dir = project.thumbs_path(state.shooter_root(slug))
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
                        slug=slug,
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
    suggested = _server._suggested_starts(project.last_scanned_dir)

    return FsListing(
        path=str(target),
        parent=parent,
        entries=entries,
        suggested_starts=suggested,
    )


@router.get("/api/shooters/{slug}/fs/probe")
def fs_probe(request: Request, slug: str, path: str = Query(...)) -> JSONResponse:
    """Probe a single video file on demand: ffprobe + thumbnail extraction.

    Used by the SPA when a picker row came back with null fields (the
    list-time budget was exhausted, or ``probe=true`` wasn't passed).
    Cached results are returned without re-running the binaries. Also
    surfaces resolution/codec/size so the unassigned-tray rows can show
    enough metadata for the user to identify which clip is which.
    Local mode only, like the picker that calls it.
    """
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    # StageVideo.path is project-relative for default projects, so resolve
    # via the project root rather than process CWD before strict-resolving.
    raw_target = Path(path).expanduser()
    target = project.resolve_video_path(state.shooter_root(slug), raw_target)
    try:
        target = target.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=404, detail=f"path not found: {target}") from exc
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"not a file: {target}")
    if target.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"not a video: {target}")

    probes_dir = project.probes_path(state.shooter_root(slug))
    thumbs_dir = project.thumbs_path(state.shooter_root(slug))
    duration, thumbnail_url = _video_metadata_for(
        target,
        slug=slug,
        probes_dir=probes_dir,
        thumbs_dir=thumbs_dir,
        allow_new=True,
        duration_for_thumb=None,
    )
    cached_probe = video_probe.cached(target, probes_dir)
    size_bytes: int | None = None
    try:
        size_bytes = target.stat().st_size
    except OSError:
        size_bytes = None
    return JSONResponse(
        {
            "duration": duration,
            "thumbnail_url": thumbnail_url,
            "width": cached_probe.width if cached_probe is not None else None,
            "height": cached_probe.height if cached_probe is not None else None,
            "codec": cached_probe.codec if cached_probe is not None else None,
            "size_bytes": size_bytes,
        }
    )


@router.get("/api/shooters/{slug}/thumbnails/{cache_key}.jpg", include_in_schema=False)
def serve_thumbnail(request: Request, slug: str, cache_key: str) -> FileResponse:
    """Serve a cached thumbnail by its content-addressed key.

    Keys are 16-char hex from :func:`video_probe.source_cache_key`. We
    validate the key shape so we never accept an arbitrary path that
    could escape the thumbs directory. Local mode only: the picker
    routes above are the only writers of this cache.
    """
    state = request.app.state.splitsmith_state
    if not cache_key.isalnum() or len(cache_key) > 32:
        raise HTTPException(status_code=400, detail="invalid thumbnail key")
    project = state.shooter_project(slug)
    thumbs_dir = project.thumbs_path(state.shooter_root(slug))
    candidate = thumbs_dir / f"{cache_key}.jpg"
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="thumbnail not cached")
    return FileResponse(candidate, media_type="image/jpeg", filename=candidate.name)


@router.post("/api/shooters/{slug}/videos/auto-match")
def auto_match(request: Request, slug: str) -> JSONResponse:
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    suggestions = project.auto_match(state.shooter_root(slug))
    return JSONResponse(
        {str(stage_num): suggestion.model_dump(mode="json") for stage_num, suggestion in suggestions.items()}
    )


@router.post("/api/shooters/{slug}/videos/remove")
def remove_video(request: Request, slug: str, req: RemoveVideoRequest) -> JSONResponse:
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
    state = request.app.state.splitsmith_state
    root = state.shooter_root(slug)
    project = state.shooter_project(slug)
    try:
        plan = project.remove_video(
            Path(req.video_path),
            root,
            reset_audit=req.reset_audit,
            stage_number=req.stage_number,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Symlink under raw_dir: remove only if it's actually a symlink we
    # created. If the user pointed raw_dir at the source and the link is
    # in fact the original file, leave it alone. No link in the plan
    # while another stage still registers the file.
    try:
        if plan.raw_link_path is None:
            pass
        elif plan.raw_link_path.is_symlink():
            plan.raw_link_path.unlink()
        elif plan.raw_link_path.exists():
            # Treat as a copy splitsmith placed (link_mode="copy"). We
            # only delete files inside raw_dir; never anything beyond.
            raw_dir = project.raw_path(root).resolve()
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

    project.save(root)
    return JSONResponse(
        {
            "project": project.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
        }
    )


@router.get("/api/shooters/{slug}/project/cleanup/plan")
def cleanup_plan(
    request: Request,
    slug: str,
    categories: str = Query("", description="Comma-separated category list."),
) -> JSONResponse:
    """Preview a cleanup plan without deleting anything.

    Empty / unknown categories yield an empty plan rather than 400 so
    the SPA can debounce-fetch as the user toggles checkboxes
    without worrying about partial selections.
    """
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    cats: set[cleanup_module.CleanupCategory] = set()
    for token in categories.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            cats.add(cleanup_module.CleanupCategory(token))
        except ValueError:
            # Skip unknown categories silently; the SPA only sends
            # known ones, and a stale tab shouldn't crash here.
            continue
    root = state.shooter_root(slug)
    plan = cleanup_module.plan_cleanup(project, root, cats, audit_stages=set(state.load_audit_docs(slug)))
    return JSONResponse(plan.model_dump(mode="json"))


@router.post("/api/shooters/{slug}/project/cleanup")
async def cleanup_apply(request: Request, slug: str, req: CleanupRequest) -> JSONResponse:
    """Apply a cleanup. Refuses while jobs are pending or running.

    Re-plans server-side: the client only sends categories, never
    paths. Any active job is treated as a hard block (409) -- mid-
    flight ffmpeg writes into ``trimmed/`` or audit JSON saves into
    ``audit/`` would race with the deletes and corrupt state.

    Items whose own input is already gone are held back unless the
    request opts in (#926), matching the CLI's
    ``--include-unrebuildable`` and the dialog's per-item ticks. The
    *response* still carries the full plan, not the applied subset:
    the plan's contract is "here is everything in these categories",
    and filtering it would leave a caller unable to tell a file that
    was skipped from one that never existed. ``result`` is the record
    of what was actually deleted.
    """
    state = request.app.state.splitsmith_state
    active = await _any_active_job(state)
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "jobs_active",
                "message": (
                    f"Job '{active.kind}' is still {active.status}; "
                    "cancel or wait for it to finish before cleaning up."
                ),
                "job_id": active.id,
                "kind": active.kind,
            },
        )
    root = state.shooter_root(slug)
    project = state.shooter_project(slug)
    cats = set(req.categories)
    plan = cleanup_module.plan_cleanup(project, root, cats, audit_stages=set(state.load_audit_docs(slug)))
    # ``without_unreconstructable`` -- not a local "drop everything
    # unreconstructable" -- because it goes through
    # ``needs_item_opt_in``, which exempts AUDIT_DATA. Audit docs are
    # unreconstructable by definition, so re-deriving the condition
    # here would let the audit-data checkbox select the category and
    # this gate silently empty it.
    applied = plan if req.include_unrebuildable else cleanup_module.without_unreconstructable(plan)
    result = cleanup_module.apply_cleanup(applied, root=root, project=project)
    return JSONResponse(
        {
            "plan": plan.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
        }
    )


@router.post("/api/shooters/{slug}/assignments/move")
async def move_assignment(
    request: Request, slug: str, req: MoveRequest, background: BackgroundTasks
) -> JSONResponse:
    # Load + save await the store directly (no run_sync loop-block); the
    # SPA already applies the move optimistically, so the only thing gating
    # its reconcile is this round-trip - keep it off the event loop.
    state = request.app.state.splitsmith_state
    root = state.shooter_root(slug)
    project = await state.shooter_project_async(slug)
    try:
        moved = project.assign_video(
            Path(req.video_path),
            to_stage_number=req.to_stage_number,
            role=req.role,
            from_stage_number=req.from_stage_number,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await project.save_async(root)

    # Auto-queue beep on assignment to a real stage (#67). Skip when
    # unassigning (to_stage_number=None) or marking ignored -- those
    # don't put the video into the pipeline. Deferred to a background task:
    # it writes a job row, which the response doesn't need to wait on (the
    # SPA picks up the job via JobsPanel polling). It no longer mirrors
    # media from storage either -- see the auto-fire's preflight (#638).
    if req.to_stage_number is not None and req.role != "ignored":
        background.add_task(
            _server._auto_queue_beep_if_needed, state, slug, project, req.to_stage_number, moved
        )

    return JSONResponse(project.model_dump(mode="json"))


@router.post("/api/shooters/{slug}/assignments/swap-primary")
async def swap_primary(
    request: Request, slug: str, req: SwapPrimaryRequest, background: BackgroundTasks
) -> JSONResponse:
    """Promote ``video_path`` to primary on ``stage_number``.

    Audit-safe: when the stage has shots in its audit JSON, refuses with
    a 409 response unless ``confirm=True`` is passed. On confirm, the
    audit JSON is kept as ``stage<N>.pre-swap-<time>.json`` so a bad
    swap is recoverable, and the stage's trim and shots are redone on
    the new primary. A beep the user confirmed on it is kept, and the
    trim is chained at once; an unconfirmed one is detected again.
    """
    state = request.app.state.splitsmith_state
    root = state.shooter_root(slug)
    project = await state.shooter_project_async(slug)
    try:
        stage = project.stage(req.stage_number)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    current = stage.primary()
    if current is not None and str(current.path) == req.video_path:
        # Already the primary: nothing to swap, back up or redo.
        return JSONResponse(project.model_dump(mode="json"))
    warns = project.primary_swap_warns(root, stage_number=req.stage_number)
    if warns and not req.confirm:
        existing = stage.primary()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "audit_exists",
                "message": (
                    f"Stage {req.stage_number} has audit work on its current "
                    "primary. Confirm the swap to keep the audit as a backup "
                    "and run detection again on the new primary."
                ),
                "stage_number": req.stage_number,
                "current_primary": str(existing.path) if existing else None,
                "new_primary": req.video_path,
            },
        )
    try:
        project.swap_primary(
            Path(req.video_path),
            root=root,
            stage_number=req.stage_number,
            backup_audit=warns,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await project.save_async(root)

    # Auto-queue beep for the new primary (#67). swap_primary may
    # clear ``processed.beep`` to force re-detection on the new
    # video's audio; the helper picks up the cleared flag and queues
    # accordingly. No-op when the video already had a current beep.
    # Deferred (see move_assignment) so the job write stays off the
    # response path; the media mirror it used to pay for is gone (#638).
    new_primary = project.stage(req.stage_number).primary()
    if new_primary is not None:
        if new_primary.beep_reviewed and new_primary.beep_time is not None:
            # A confirmed beep was kept (2026-10-16): go straight to the
            # trim, which chains detection. Queuing a beep detection here
            # raced the confirmed beep and could leave it unreviewed.
            if current_match_origin.get() != "desktop":
                await _server._maybe_chain_trim(state, slug, project.stage(req.stage_number), new_primary)
        else:
            background.add_task(
                _server._auto_queue_beep_if_needed, state, slug, project, req.stage_number, new_primary
            )

    return JSONResponse(project.model_dump(mode="json"))


@router.post("/api/shooters/{slug}/videos/reveal")
def reveal_video(request: Request, slug: str, req: RevealRequest) -> JSONResponse:
    """Reveal a registered project video in the OS file manager.

    The generic ``/api/files/reveal`` requires the resolved path to be
    inside the project root, which excludes registered videos whose
    symlinks under ``raw/`` point at the original on USB / external
    storage. This endpoint looks the path up in project state and
    reveals the symlink target -- the user already consented by
    registering the source via the picker, so revealing the original
    location is the natural action for "open containing folder".
    """
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    located = project.find_video(Path(req.path))
    if located is None:
        raise HTTPException(status_code=404, detail=f"video not registered: {req.path}")
    _, video = located
    target = project.resolve_video_path(state.shooter_root(slug), Path(str(video.path)))
    try:
        resolved = target.resolve(strict=True)
    except (OSError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=f"not found: {target}") from exc
    _server._reveal_in_file_manager(resolved)
    return JSONResponse({"revealed": str(resolved)})


def _video_metadata_for(
    source: Path,
    *,
    slug: str,
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
            result = video_probe.probe(
                source,
                cache_dir=probes_dir,
                ffprobe_binary=process_runtime().ffprobe_binary,
            )
            duration = result.duration
        except video_probe.ProbeError as exc:
            logger.debug("probe failed for %s: %s", source, exc)

    thumbnail_url: str | None = None
    cached_thumb = thumbnail_helpers.cached(source, thumbs_dir)
    if cached_thumb is not None:
        thumbnail_url = f"/api/shooters/{slug}/thumbnails/{cached_thumb.stem}.jpg"
    elif allow_new:
        try:
            t_dur = duration_for_thumb if duration_for_thumb is not None else duration
            extracted = thumbnail_helpers.ensure(
                source,
                cache_dir=thumbs_dir,
                duration=t_dur,
                ffmpeg_binary=process_runtime().ffmpeg_binary,
            )
            thumbnail_url = f"/api/shooters/{slug}/thumbnails/{extracted.stem}.jpg"
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
