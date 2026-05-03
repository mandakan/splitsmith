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
  POST /api/stages/{n}/beep/select  -- promote one ranked candidate (synchronous)
  POST /api/stages/{n}/trim         -- submit an audit-mode trim job
  POST /api/stages/{n}/shot-detect  -- submit shot detection on the audit clip
  POST /api/stages/{n}/videos/{vid}/detect-beep -- per-video beep detection
  POST /api/stages/{n}/videos/{vid}/beep         -- per-video manual override
  POST /api/stages/{n}/videos/{vid}/beep/select  -- per-video candidate select
  POST /api/stages/{n}/videos/{vid}/beep/snap    -- propose a snapped beep near a user hint
  POST /api/stages/{n}/videos/{vid}/trim         -- per-video audit-mode trim
  GET  /api/stages/{n}/videos/{vid}/beep-preview -- per-video ~1s preview MP4
  GET  /api/jobs                    -- list all retained jobs
  GET  /api/jobs/{job_id}           -- poll a single job for progress / status
  POST /api/jobs/{job_id}/cancel    -- cooperative cancel of a running job
  POST /api/jobs/{job_id}/acknowledge      -- dismiss a failed job (issue #73)
  POST /api/jobs/acknowledge-failures      -- dismiss every unacknowledged failure
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
  GET  /api/user/recent-projects    -- recently-opened MatchProject roots (#75)
  POST /api/user/recent-projects/forget -- drop one entry from the list
  GET  /api/user/scoreboard-identity -- saved SSI identity (404 if none)
  PUT  /api/user/scoreboard-identity -- write the SSI identity

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
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import beep_detect, report, user_config, video_probe
from .. import ensemble as ensemble_module
from .. import shot_detect as shot_detect_module  # noqa: F401  (kept for legacy monkeypatch points)
from .. import thumbnail as thumbnail_helpers
from .. import waveform as waveform_helpers
from ..config import BeepDetectConfig, Config
from . import audio as audio_helpers
from . import exports as export_helpers
from .jobs import Job, JobCancelled, JobHandle, JobRegistry
from .project import (
    VIDEO_EXTENSIONS,
    MatchProject,
    ScoreboardImportConflictError,
    StageEntry,
    StageVideo,
    VideoRole,
)
from .scoreboard import (
    CachingScoreboardClient,
    CompetitorNotInMatch,
    LocalJsonScoreboard,
    MatchNotFound,
    ScoreboardAuthError,
    ScoreboardClient,
    ScoreboardError,
    ScoreboardRateLimited,
    ScoreboardUpstreamError,
    ShooterNotFound,
    SsiHttpClient,
    StageTimesNotImplemented,
    StageTimesUnavailable,
)
from .scoreboard.local import DEFAULT_MATCH_FILENAME, DEFAULT_SCOREBOARD_DIRNAME


def _ensure_source_reachable(stage_number: int | None, source: Path) -> None:
    """Raise a structured 424 when ``source`` doesn't exist on disk.

    The SPA reads ``detail.code == "source_unreachable"`` to render a
    uniform "reconnect the USB / SD card" message wherever a source-bound
    operation is invoked (detect-beep, audit-mode trim, beep preview,
    video stream, export). Callers handle the "no primary" check with
    their endpoint-specific status code before calling this -- the helper
    only handles the upstream-dependency-offline case.
    """
    if source.exists():
        return
    raise HTTPException(
        status_code=424,
        detail={
            "code": "source_unreachable",
            "stage_number": stage_number,
            "path": str(source),
            "message": (
                "Source video"
                + (f" for stage {stage_number}" if stage_number is not None else "")
                + f" is not reachable: {source}. If it lives on external "
                f"storage (USB drive, SD card), reconnect and try again."
            ),
        },
    )


def _cancellable_runner(handle: JobHandle):
    """Build a ``trim.Runner`` that registers ffmpeg with the job for cancel.

    Mirrors the ``subprocess.run(check=True, capture_output=True)`` shape
    that :func:`splitsmith.trim.trim_video` expects, but uses ``Popen`` so
    the registry can ``terminate()`` ffmpeg the moment a cancel arrives.
    Without this the worker thread sits inside ``proc.wait()`` until the
    whole encode finishes -- a couple of minutes on a 4K Insta360 stage.
    """

    def runner(cmd, *, check=True, capture_output=True, text=True, **kwargs):
        stdout_arg = subprocess.PIPE if capture_output else None
        stderr_arg = subprocess.PIPE if capture_output else None
        proc = subprocess.Popen(  # noqa: S603 -- argv is constructed by us
            cmd,
            stdout=stdout_arg,
            stderr=stderr_arg,
            text=text,
            **kwargs,
        )
        try:
            handle.attach_subprocess(proc)
            try:
                stdout, stderr = proc.communicate()
            finally:
                handle.detach_subprocess()
        except JobCancelled:
            # attach_subprocess terminated the proc when the cancel
            # arrived before we could attach -- reap it so we don't leak
            # a zombie, then propagate. Use a short wait to bound the
            # impact if the child is wedged in uninterruptible I/O.
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            raise
        if check and proc.returncode != 0:
            # When the registry terminated ffmpeg as part of a cancel,
            # surface the cancel rather than the noisy non-zero exit.
            if handle.is_cancel_requested():
                raise JobCancelled()
            raise subprocess.CalledProcessError(proc.returncode, cmd, output=stdout, stderr=stderr)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
        )

    return runner


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
            "ui_static/dist appears stale but npm is not on PATH; " "serving whatever is in dist/"
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


def _load_env_files(project_root: Path) -> list[Path]:
    """Pick up ``.env.local`` then ``.env`` from the project root, the cwd
    the user launched the server from, and the global user-config dir.

    ``SPLITSMITH_SSI_TOKEN`` (and any other ``SPLITSMITH_*`` env var) lives
    in one of these files for typical setups; without this hook the token
    only works if the user remembered to ``export`` it before launching
    ``splitsmith ui``.

    Search order (process env always wins via ``override=False``; the
    first file to define a key keeps it):

    1. ``<project_root>/.env`` -- shared per-project defaults
    2. ``<project_root>/.env.local`` -- per-machine secret, gitignored
    3. ``<cwd>/.env``           -- repo / launch-dir shared default
    4. ``<cwd>/.env.local``     -- repo / launch-dir per-machine secret
    5. ``<user_config_dir>/.env`` -- global per-user default
    6. ``<user_config_dir>/.env.local`` -- global per-user secret (#75)

    The user-config layer is the natural home for the SSI API token,
    which is per-user rather than per-project; per-project settings still
    win because they're loaded earlier.
    Duplicate paths (when the user runs from inside the project directory,
    or sets ``SPLITSMITH_HOME`` to one of the other locations) are loaded
    once.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return []

    candidates: list[Path] = []
    seen: set[Path] = set()
    bases: list[Path] = [project_root.resolve(), Path.cwd().resolve()]
    if not user_config.is_disabled():
        bases.append(user_config.user_config_dir())
    for base in bases:
        for name in (".env", ".env.local"):
            try:
                candidate = (base / name).resolve()
            except OSError:
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate.is_file():
                candidates.append(candidate)

    for path in candidates:
        load_dotenv(path, override=False)
    return candidates


class _ScoreboardClientCtx:
    """Context-manager wrapper so Local + HTTP-backed clients close cleanly.

    The protocol doesn't require ``close()``, but the HTTP client owns an
    ``httpx.Client`` we want to release per request. Local clients are a
    no-op. The wrapper proxies the four protocol methods only -- callers
    must not poke at internal types.
    """

    def __init__(
        self,
        inner: ScoreboardClient,
        *,
        owns_close: bool,
        inner_http: SsiHttpClient | None = None,
    ) -> None:
        self._inner = inner
        self._owns_close = owns_close
        self._inner_http = inner_http

    def __enter__(self) -> ScoreboardClient:
        return self._inner

    def __exit__(self, *_exc: object) -> None:
        if self._owns_close and self._inner_http is not None:
            self._inner_http.close()


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


def _raise_scoreboard_http(exc: ScoreboardError) -> None:
    """Translate a typed ``ScoreboardError`` into the right HTTP status.

    Codes are stable so the SPA can match on ``detail.code`` regardless of
    the human message. Banner copy lives client-side; we ship the message
    + retry hint, not the rendered string.
    """
    if isinstance(exc, ScoreboardAuthError):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "scoreboard_auth",
                "message": str(exc),
                "env_var": "SPLITSMITH_SSI_TOKEN",
                "docs_url": "https://github.com/mandakan/ssi-scoreboard/blob/main/docs/api-v1.md",
            },
        )
    if isinstance(exc, ScoreboardRateLimited):
        raise HTTPException(
            status_code=429,
            detail={
                "code": "scoreboard_rate_limited",
                "message": str(exc),
                "retry_after": exc.retry_after,
            },
        )
    if isinstance(exc, ScoreboardUpstreamError):
        raise HTTPException(
            status_code=502,
            detail={
                "code": "scoreboard_offline",
                "message": str(exc),
            },
        )
    if isinstance(exc, StageTimesNotImplemented):
        raise HTTPException(
            status_code=502,
            detail={
                "code": "stage_times_blocked_on_upstream",
                "message": str(exc),
                "upstream_issue": "ssi-scoreboard#400",
                "upstream_url": "https://github.com/mandakan/ssi-scoreboard/issues/400",
            },
        )
    if isinstance(exc, StageTimesUnavailable):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "stage_times_offline_pure_matchdata",
                "message": str(exc),
            },
        )
    if isinstance(exc, ShooterNotFound):
        raise HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, CompetitorNotInMatch):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "competitor_not_in_match",
                "message": str(exc),
            },
        )
    raise HTTPException(status_code=500, detail=str(exc))


@dataclass
class AppState:
    """Per-process state. One project root per server instance."""

    project_root: Path
    jobs: JobRegistry = field(default_factory=JobRegistry)

    def load(self) -> MatchProject:
        return MatchProject.load(self.project_root)


# Module-level cache for the 4-voter ensemble runtime (issue #31). Heavy
# (CLAP ~600 MB, PANN ~80 MB), so it is loaded on the first shot-detect
# call and reused for subsequent calls. The threading lock guards against
# two shot-detect jobs colliding on first init when the model weights
# haven't downloaded yet.
_ENSEMBLE_RUNTIME: ensemble_module.EnsembleRuntime | None = None
_ENSEMBLE_RUNTIME_LOCK = threading.Lock()


def _get_ensemble_runtime() -> ensemble_module.EnsembleRuntime:
    """Lazy-load + cache the ensemble runtime; thread-safe.

    Test code monkeypatches this function (and
    ``ensemble_module.detect_shots_ensemble``) to avoid pulling the heavy
    model weights into the test process.
    """
    global _ENSEMBLE_RUNTIME
    if _ENSEMBLE_RUNTIME is None:
        with _ENSEMBLE_RUNTIME_LOCK:
            if _ENSEMBLE_RUNTIME is None:
                _ENSEMBLE_RUNTIME = ensemble_module.load_ensemble_runtime()
    return _ENSEMBLE_RUNTIME


class HealthResponse(BaseModel):
    status: str = "ok"
    project_name: str
    project_root: str
    schema_version: int


# Request bodies ----------------------------------------------------------


class ScoreboardImportRequest(BaseModel):
    data: dict[str, Any]
    overwrite: bool = False


class ForgetRecentProjectRequest(BaseModel):
    """Body for POST /api/user/recent-projects/forget (#75)."""

    path: str


class ScoreboardIdentityRequest(BaseModel):
    """Body for PUT /api/user/scoreboard-identity (#75).

    Mirrors :class:`splitsmith.user_config.ScoreboardIdentity`. ``shooter_id``
    is required; everything else is optional so the SPA can save a partial
    identity without forcing the user to fill division / club.
    """

    shooter_id: int
    display_name: str | None = None
    division: str | None = None
    club: str | None = None
    base_url: str | None = None


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


class BeepSelectRequest(BaseModel):
    """Body for POST /api/stages/{n}/beep/select.

    ``time`` is matched against ``primary.beep_candidates`` within 1 ms.
    Time-based addressing (rather than an index into the list) is robust
    against the SPA holding a stale candidate snapshot when a concurrent
    trim job re-persists the project: the user picked a *time*, so the
    server should honour that exact pick regardless of where it lands in
    the current list.
    """

    time: float


class BeepReviewRequest(BaseModel):
    """Body for POST /api/stages/{n}/videos/{vid}/beep/review (#71).

    Pure UI-state flag: pipeline doesn't gate on it. Setting ``True``
    requires that ``beep_time`` already exists on the video.
    """

    reviewed: bool


class BeepSnapRequest(BaseModel):
    """Body for POST /api/stages/{n}/videos/{vid}/beep/snap.

    ``hint_time`` is the user's ear-aligned guess (seconds into source).
    ``window_s`` is the half-window the snap is allowed to search inside
    -- defaults to 1.5 s. Wider than the eyeball precision of a click
    on a long-range waveform, narrow enough that competing in-stage
    transients (steel rings, shots) don't enter the candidate pool when
    the marker is anywhere reasonable.
    """

    hint_time: float
    window_s: float = 1.5


class BeepSnapResponse(BaseModel):
    """Result of a snap-to-beep proposal. Stateless -- the SPA decides
    whether to accept (PUT the snapped time as the manual override) or
    dismiss (keep the user's hint)."""

    snapped_time: float
    delta: float
    peak_amplitude: float
    score: float
    duration_ms: float


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
    # the render is per-frame PIL + ffmpeg ProRes 4444 -- non-trivially
    # slower than the other writers. The Analysis & Export checkbox
    # opts-in per stage.
    write_overlay: bool = False


class RevealRequest(BaseModel):
    """Body for POST /api/files/reveal.

    Opens the OS file manager at ``path``'s parent, selecting the file when
    the platform supports it (``open -R`` on macOS). The path must resolve
    inside the project root for safety -- we don't expose a generic shell
    out.
    """

    path: str


class SwapPrimaryRequest(BaseModel):
    """Body for POST /api/assignments/swap-primary.

    Promotes ``video_path`` to primary on ``stage_number``. When the stage
    has audit work and ``confirm`` is False, the endpoint refuses with a
    409 response describing what would be lost so the SPA can prompt the
    user. Passing ``confirm=True`` performs the swap and renames the
    existing audit JSON to ``stage<N>.json.bak``.
    """

    video_path: str
    stage_number: int
    confirm: bool = False


class SkipStageRequest(BaseModel):
    """Body for POST /api/stages/{n}/skip. Toggles ``stage.skipped``."""

    skipped: bool


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


class SuggestedStart(BaseModel):
    """One sidebar bookmark in the FolderPicker.

    Distinct from a flat string list so the SPA can group entries by
    ``kind`` (recent / home / removable / network) and render the right
    icon. ``label`` is what to show in the sidebar; ``path`` is the
    absolute path to navigate to. Only ``kind="removable"`` and
    ``"network"`` carry the platform-specific mount discovery output --
    everything else is the user-stable bookmarks.
    """

    path: str
    label: str
    kind: Literal["recent", "home", "removable", "network"]


class FsListing(BaseModel):
    path: str
    parent: str | None
    entries: list[FsEntry]
    suggested_starts: list[SuggestedStart]


def create_app(*, project_root: Path, project_name: str) -> FastAPI:
    """Create the FastAPI app bound to a single match project on disk.

    The project is initialized on first call (idempotent), then the app keeps
    the root path and re-loads on every request that needs it. We avoid
    caching the model in memory so external edits to ``project.json`` are
    visible without restarting the server.
    """
    MatchProject.init(project_root, name=project_name)
    resolved_root = project_root.resolve()
    state = AppState(project_root=resolved_root)
    loaded_env = _load_env_files(resolved_root)
    if loaded_env:
        logger.info("Loaded env from %s", ", ".join(str(p) for p in loaded_env))

    # Record this open in the global recent-projects list (issue #75). The
    # disk name on the project may differ from ``project_name`` -- prefer
    # whatever ``MatchProject.init`` settled on so re-opens don't flip the
    # display name based on which CLI invocation got there first.
    try:
        loaded_project = MatchProject.load(resolved_root)
        recorded_name = loaded_project.name or project_name
    except Exception:  # pragma: no cover -- defensive: never block boot
        recorded_name = project_name
    user_config.record_project_open(resolved_root, recorded_name)

    app = FastAPI(
        title="splitsmith UI",
        description="Production UI backend (issue #11/#12).",
        version="0.1.0",
    )
    # Stash on app.state so the uvicorn server wrapper in :func:`serve`
    # can read live job state when handling Ctrl-C: the signal handler
    # needs to enumerate pending / running jobs to tell the user what's
    # being waited on, and the registry isn't otherwise reachable from
    # outside the closure.
    app.state.splitsmith_state = state

    # ----------------------------------------------------------------------
    # API
    # ----------------------------------------------------------------------

    def _local_match_path() -> Path:
        """Resolve ``<project>/scoreboard/match.json`` (offline source path)."""
        return state.project_root / DEFAULT_SCOREBOARD_DIRNAME / DEFAULT_MATCH_FILENAME

    def _resolve_scoreboard_client() -> ScoreboardClient:
        """Pick the concrete ``ScoreboardClient`` for this request.

        Local JSON wins when present so the user can stay fully offline by
        dropping a file. Otherwise we wrap the HTTP client in the project-
        local cache so a second open of the same match is a cache hit. The
        caller is responsible for ``close()`` -- both implementations are
        context managers (Local is a no-op; HTTP closes the httpx Client).
        """
        local_path = _local_match_path()
        if local_path.exists():
            local = LocalJsonScoreboard(local_path)
            return _ScoreboardClientCtx(local, owns_close=False)
        try:
            http = SsiHttpClient()
        except ScoreboardAuthError as exc:
            _raise_scoreboard_http(exc)
        cache_dir = state.project_root / "scoreboard" / "cache"
        cached = CachingScoreboardClient(http, cache_dir)
        return _ScoreboardClientCtx(cached, owns_close=True, inner_http=http)

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

    @app.get("/api/project/match-analysis")
    def get_match_analysis() -> JSONResponse:
        """Run the canonical video-match heuristic over the project and return
        per-stage windows + per-video classification.

        Single source of truth for the SPA's match-window timeline:
        tolerance, window edges, and per-video classification are all
        produced by :mod:`splitsmith.video_match`. Future heuristic
        improvements (per-stage tolerance, ML-based scoring, confidence
        bands) extend this endpoint rather than adding policy to the SPA.
        """
        project = state.load()
        return JSONResponse(project.match_analysis().model_dump(mode="json"))

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

    # ----------------------------------------------------------------------
    # SSI Scoreboard v1 wiring (#50)
    # ----------------------------------------------------------------------
    #
    # The UI consumes only the ``ScoreboardClient`` Protocol -- the resolver
    # below picks the concrete implementation per request based on project
    # state. Drop a ``<project>/scoreboard/match.json`` and every request
    # transparently switches to the offline path; remove the file and the
    # next request hits the live API. Errors are mapped to HTTP statuses
    # the SPA can render as actionable banners (acceptance criterion).

    @app.get("/api/scoreboard/source")
    def scoreboard_source() -> JSONResponse:
        """Report whether the offline JSON or the live API will serve requests.

        The SPA renders a "loaded from local JSON, no network used" indicator
        when ``mode == "local"`` so the user can verify the offline path
        without watching dev tools.
        """
        local_path = _local_match_path()
        local = local_path.exists()
        http_ready = bool(os.environ.get("SPLITSMITH_SSI_TOKEN"))
        return JSONResponse(
            {
                "mode": "local" if local else "online",
                "local_match_json_path": str(local_path) if local else None,
                "http_token_set": http_ready,
            }
        )

    @app.post("/api/scoreboard/upload")
    def scoreboard_upload(req: ScoreboardUploadRequest) -> JSONResponse:
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
        local_path = _local_match_path()
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

        project = state.load()
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
        project.save(state.project_root)
        return JSONResponse({**project.model_dump(mode="json"), "stage_times_merged": merged})

    @app.get("/api/scoreboard/search")
    def scoreboard_search(q: str = Query("", min_length=0)) -> JSONResponse:
        """Search the active scoreboard source for matches by free-text query."""
        with _resolve_scoreboard_client() as client:
            try:
                refs = client.search_matches(q)
            except ScoreboardError as exc:
                _raise_scoreboard_http(exc)
        return JSONResponse([ref.model_dump(mode="json") for ref in refs])

    @app.post("/api/scoreboard/fetch")
    def scoreboard_fetch(req: ScoreboardFetchRequest) -> JSONResponse:
        """Fetch a full match (cache-first) and populate the project.

        When the project already has a pinned competitor (carried over from
        a previous session), auto-merge their stage times in the same
        round-trip. New picks (no pin yet) still need ``/select-shooter``
        afterwards -- the SPA flow stays the same.
        """
        with _resolve_scoreboard_client() as client:
            try:
                match_data = client.get_match(req.content_type, req.match_id)
            except MatchNotFound as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ScoreboardError as exc:
                _raise_scoreboard_http(exc)
        project = state.load()
        try:
            project.populate_from_match_data(match_data, overwrite=req.overwrite)
        except ScoreboardImportConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        merged = 0
        if project.selected_competitor_id is not None:
            try:
                merged = _fetch_and_merge_stage_times(
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
                project.save(state.project_root)
                raise
        else:
            project.save(state.project_root)
        return JSONResponse({**project.model_dump(mode="json"), "stage_times_merged": merged})

    @app.get("/api/scoreboard/match-data")
    def scoreboard_match_data() -> JSONResponse:
        """Return the resolved ``MatchData`` for the project's loaded match.

        The SPA needs this to map a picked ``shooterId`` to the per-match
        ``competitor_id`` before calling ``/select-shooter``. We don't
        denormalise that mapping onto the ``MatchProject`` because it
        drifts when upstream re-fetches; serving on demand keeps the
        cache as the single source of truth.
        """
        project = state.load()
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
        with _resolve_scoreboard_client() as client:
            try:
                match_data = client.get_match(ct, mid)
            except MatchNotFound as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ScoreboardError as exc:
                _raise_scoreboard_http(exc)
        return JSONResponse(match_data.model_dump(mode="json"))

    @app.get("/api/scoreboard/shooter/search")
    def scoreboard_shooter_search(q: str = Query("", min_length=0)) -> JSONResponse:
        """Find shooters by name. Offline mode searches this match's
        competitor list only; online mode hits the live shooter index."""
        if not q.strip():
            return JSONResponse([])
        with _resolve_scoreboard_client() as client:
            try:
                refs = client.find_shooter(q)
            except ScoreboardError as exc:
                _raise_scoreboard_http(exc)
        return JSONResponse([ref.model_dump(mode="json") for ref in refs])

    @app.post("/api/scoreboard/select-shooter")
    def scoreboard_select_shooter(req: SelectShooterRequest) -> JSONResponse:
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
        project = state.load()
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
        with _resolve_scoreboard_client() as client:
            try:
                match_data = client.get_match(ct, mid)
            except ScoreboardError as exc:
                _raise_scoreboard_http(exc)
        picked = next(
            (c for c in match_data.competitors if c.id == req.competitor_id),
            None,
        )
        if picked is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "competitor_not_in_match",
                    "message": (
                        f"competitor {req.competitor_id} isn't in this match. "
                        "Pick a different shooter."
                    ),
                },
            )

        project.selected_shooter_id = req.shooter_id
        project.selected_competitor_id = req.competitor_id
        # Persist the human-readable name so the SPA's collapsed
        # scoreboard summary doesn't have to re-fetch MatchData just to
        # render "pinned: Mathias Rinaldo" instead of an integer id.
        project.competitor_name = picked.name
        project.save(state.project_root)

        merged = _fetch_and_merge_stage_times(project, ct, mid, req.competitor_id)
        return JSONResponse({**project.model_dump(mode="json"), "stage_times_merged": merged})

    @app.post("/api/scoreboard/refresh-times")
    def scoreboard_refresh_times() -> JSONResponse:
        """Re-pull and re-merge stage times for the pinned competitor.

        Invalidates every cached stage-times entry for the match (not
        just the pinned competitor) because in-progress matches often
        update multiple shooters at once and a fresh pull is cheap. Use
        this after the user knows the upstream has new scorecards.
        """
        project = state.load()
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
        cache_dir = state.project_root / "scoreboard" / "cache"
        if cache_dir.exists():
            cache = CachingScoreboardClient(_NoOpClient(), cache_dir)
            cache.invalidate_match_stage_times(ct, mid)

        merged = _fetch_and_merge_stage_times(project, ct, mid, project.selected_competitor_id)
        return JSONResponse({**project.model_dump(mode="json"), "stage_times_merged": merged})

    def _fetch_and_merge_stage_times(
        project: MatchProject, ct: int, mid: int, competitor_id: int
    ) -> int:
        """Shared helper -- get_stage_times + merge_stage_times, with the
        right error mapping. Persists the project on success."""
        with _resolve_scoreboard_client() as client:
            try:
                results = client.get_stage_times(ct, mid, competitor_id)
            except ScoreboardError as exc:
                _raise_scoreboard_http(exc)
            except KeyError as exc:
                raise HTTPException(
                    status_code=404,
                    detail=str(exc),
                ) from exc
        merged = project.merge_stage_times(results)
        project.save(state.project_root)
        return merged

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

        # Queue auto-beep for every freshly-primaried video (#67). Done
        # after save so the persisted state reflects the assignment when
        # the worker re-loads the project.
        for stage_num, video_path in auto_assigned.items():
            stage = project.stage(stage_num)
            video = next((v for v in stage.videos if str(v.path) == video_path), None)
            if video is not None:
                _auto_queue_beep_if_needed(project, stage_num, video)

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

    def _resolve_stage_video(
        stage_number: int, video_id: str
    ) -> tuple[MatchProject, StageEntry, StageVideo]:
        """Load the project + stage + video for a per-video endpoint.

        Returns the trio so the caller doesn't have to re-read state. Raises
        404 when stage / video doesn't exist; pre-flight check on the
        source-on-disk lives at the call site so endpoints that don't need
        a reachable file (clear, manual override) can skip it.
        """
        project = state.load()
        try:
            stage = project.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        video = stage.find_video_by_id(video_id)
        if video is None:
            raise HTTPException(
                status_code=404,
                detail=f"stage {stage_number} has no video with id {video_id!r}",
            )
        return project, stage, video

    def _run_detect_beep_for_video(handle: JobHandle, stage_number: int, video_id: str) -> None:
        """Worker: detect ``video``'s beep, then auto-chain trim.

        Generic over role:
          - primary: detect -> trim -> shot_detect (existing pipeline).
          - secondary: detect -> trim (no shot_detect; the audit timeline
            is anchored to the primary's beep).

        Trim is treated as a soft failure: the beep is valuable on its
        own and the SPA can re-trim later.
        """
        handle.update(progress=0.05, message="Loading project...")
        proj = state.load()
        stg = proj.stage(stage_number)
        video = stg.find_video_by_id(video_id)
        if video is None:
            raise RuntimeError(f"video {video_id} disappeared from stage {stage_number} mid-flight")
        source = proj.resolve_video_path(state.project_root, video.path)
        role_label = "primary" if video.role == "primary" else f"cam {video.video_id[:6]}"
        handle.update(
            progress=0.15,
            message=f"Extracting audio + detecting beep ({role_label})...",
        )
        beep = audio_helpers.detect_video_beep(
            state.project_root,
            stage_number,
            video,
            source,
            project=proj,
        )
        handle.update(progress=0.55, message="Saving beep...")
        video.beep_time = beep.time
        video.beep_source = "auto"
        video.beep_peak_amplitude = beep.peak_amplitude
        video.beep_duration_ms = beep.duration_ms
        video.beep_candidates = list(beep.candidates)
        video.processed["beep"] = True
        # Auto-detected beeps need explicit user review (#71). Reset
        # the flag here so a re-detect on a previously reviewed video
        # invalidates the prior approval.
        video.beep_reviewed = False

        trimmed_ok = False
        if stg.time_seconds > 0:
            handle.check_cancel()
            handle.update(progress=0.55, message=f"Trimming audit clip ({role_label})...")
            try:
                audio_helpers.ensure_video_audit_trim(
                    state.project_root,
                    stage_number,
                    video,
                    source,
                    video.beep_time,
                    stg.time_seconds,
                    project=proj,
                    runner=_cancellable_runner(handle),
                )
                video.processed["trim"] = True
                trimmed_ok = True
            except (FileNotFoundError, audio_helpers.AudioExtractionError) as exc:
                # Soft failure: beep alone is still useful, especially for
                # secondaries (audit alignment works without a trim cache).
                logger.warning(
                    "auto-trim failed for stage %d video %s: %s",
                    stage_number,
                    video.video_id,
                    exc,
                )
                handle.update(message=f"beep saved; trim failed: {exc}")
        handle.update(progress=0.85, message="Saving project...")
        # Read-modify-write the project file: extract / detection /
        # ffmpeg trim can take 30+ s during which the user may have
        # toggled ``beep_reviewed`` on other stages. Reloading and
        # copying our targeted fields onto the fresh project preserves
        # those concurrent edits instead of stomping them with the
        # snapshot we loaded at job start.
        fresh = state.load()
        try:
            stg_fresh = fresh.stage(stage_number)
        except KeyError:
            stg_fresh = None
        v_fresh = stg_fresh.find_video_by_id(video_id) if stg_fresh is not None else None
        if v_fresh is not None:
            v_fresh.beep_time = video.beep_time
            v_fresh.beep_source = video.beep_source
            v_fresh.beep_peak_amplitude = video.beep_peak_amplitude
            v_fresh.beep_duration_ms = video.beep_duration_ms
            v_fresh.beep_candidates = list(video.beep_candidates)
            v_fresh.beep_reviewed = video.beep_reviewed
            v_fresh.processed["beep"] = True
            if trimmed_ok:
                v_fresh.processed["trim"] = True
            fresh.save(state.project_root)
        if trimmed_ok and video.role == "primary" and video.beep_reviewed:
            # Shot detection is primary-only AND gated on the user
            # confirming the beep (#71). Auto-detect always leaves
            # ``beep_reviewed=False`` so this branch only fires for
            # manual-override paths or after the user explicitly clicked
            # "Mark reviewed" (which re-triggers chaining via
            # ``set_beep_reviewed``). Saves the heavy CLAP / GBDT / PANN
            # ensemble work when the beep timestamp is wrong, since
            # everything downstream of it would be garbage anyway.
            if state.jobs.find_active(kind="shot_detect", stage_number=stage_number) is None:
                state.jobs.submit(
                    kind="shot_detect",
                    stage_number=stage_number,
                    fn=lambda h, n=stage_number: _run_shot_detect(h, n),
                )
        handle.update(progress=1.0, message="Done")

    def _submit_detect_beep(stage_number: int, video: StageVideo) -> JSONResponse:
        """Validate + dedupe + queue a detect-beep job for ``video``.

        Shared by the per-video endpoint and the primary-only legacy
        endpoint so both honour the same reachability + manual-override
        pre-flight checks.
        """
        existing = state.jobs.find_active(
            kind="detect_beep", stage_number=stage_number, video_id=video.video_id
        )
        if existing is not None:
            return JSONResponse(existing.model_dump(mode="json"))
        job = state.jobs.submit(
            kind="detect_beep",
            stage_number=stage_number,
            video_id=video.video_id,
            fn=lambda h, n=stage_number, vid=video.video_id: _run_detect_beep_for_video(h, n, vid),
        )
        return JSONResponse(job.model_dump(mode="json"))

    def _auto_queue_beep_if_needed(
        project: MatchProject, stage_number: int, video: StageVideo
    ) -> bool:
        """Best-effort auto-queue of detect_beep on a freshly-assigned video.

        Hooked into scan auto-assign, ``/assignments/move``, and
        ``/assignments/swap-primary`` so the user doesn't have to click
        "detect beep" on every camera before the audit screen is useful
        (#67). Auto-firing is conservative -- we silently skip whenever
        manual user action is the right call:

        - already detected (``processed.beep``) or manually overridden
          (``beep_source == "manual"``) -- never replace user input
        - role ``ignored`` -- video is intentionally outside the pipeline
        - source not reachable -- USB / SD card likely unplugged; the
          user will retry by hand once it's back
        - duplicate active job -- ``_submit_detect_beep`` already dedupes
          by (kind, stage, video_id), but checking up front avoids the
          unnecessary JSONResponse round-trip
        - ``SPLITSMITH_AUTO_BEEP_DISABLED=1`` is set -- escape hatch for
          tests that need to assert pre-detection state without racing
          against an auto-fired worker

        Returns True when a job was queued (or already running), False
        when skipped. Callers don't need to react; the SPA picks up the
        new job via its existing JobsPanel polling.
        """
        if os.environ.get("SPLITSMITH_AUTO_BEEP_DISABLED") == "1":
            return False
        if video.role == "ignored":
            return False
        if video.processed.get("beep") or video.beep_time is not None:
            return False
        if video.beep_source == "manual":
            return False
        source = project.resolve_video_path(state.project_root, video.path)
        if not source.exists():
            logger.info(
                "auto-beep skipped for stage %d video %s: source not reachable (%s)",
                stage_number,
                video.video_id,
                source,
            )
            return False
        _submit_detect_beep(stage_number, video)
        return True

    @app.post("/api/stages/{stage_number}/videos/{video_id}/detect-beep")
    def detect_beep_for_video(
        stage_number: int, video_id: str, force: bool = False
    ) -> JSONResponse:
        """Submit a beep-detection job for ``video_id`` on ``stage_number``.

        Generic over role (primary or secondary): each video gets its own
        detect job, its own beep timestamp, its own short-GOP trim, and
        its own dedupe slot in the registry so the user can run primary +
        Cam 2 + Cam 3 in parallel. Shot detection auto-chains only for
        primary results; secondaries align to the primary timeline by
        their own beep so they don't need their own shot timeline.
        """
        project, _stage, video = _resolve_stage_video(stage_number, video_id)
        _ensure_source_reachable(
            stage_number, project.resolve_video_path(state.project_root, video.path)
        )
        if video.beep_source == "manual" and not force:
            raise HTTPException(
                status_code=409,
                detail=(
                    "video has a manual beep override; pass ?force=true to "
                    "replace it with auto-detected output"
                ),
            )
        return _submit_detect_beep(stage_number, video)

    @app.post("/api/stages/{stage_number}/detect-beep")
    def detect_beep(stage_number: int, force: bool = False) -> JSONResponse:
        """Submit a beep-detection job for the stage's primary.

        Backward-compat shim that resolves the primary's id and forwards to
        the per-video pipeline. Returns a Job snapshot immediately; the SPA
        polls ``/api/jobs/{id}`` for progress and refetches ``/api/project``
        on completion.
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
        _ensure_source_reachable(
            stage_number, project.resolve_video_path(state.project_root, primary.path)
        )
        if primary.beep_source == "manual" and not force:
            raise HTTPException(
                status_code=409,
                detail=(
                    "stage has a manual beep override; pass ?force=true to "
                    "replace it with auto-detected output"
                ),
            )
        return _submit_detect_beep(stage_number, primary)

    @app.post("/api/stages/{stage_number}/trim")
    def trim_stage(stage_number: int) -> JSONResponse:
        """Submit an audit-mode short-GOP trim job for the stage's primary.

        Backward-compat shim: forwards to the per-video pipeline. Returns
        a Job snapshot. Idempotent on the worker side: when the cached
        MP4 is newer than the source, the job completes near-instantly
        without re-encoding.
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
        return _submit_trim(stage_number, stage, primary, project)

    def _submit_trim(
        stage_number: int,
        stage: StageEntry,
        video: StageVideo,
        project: MatchProject,
    ) -> JSONResponse:
        """Validate + dedupe + queue a trim job for ``video``.

        Pre-flight checks (reachability, beep present, stage_time set)
        apply equally to primary and secondary -- both need a beep_time
        and a non-zero stage_time to define the trim window.
        """
        _ensure_source_reachable(
            stage_number, project.resolve_video_path(state.project_root, video.path)
        )
        if video.beep_time is None:
            raise HTTPException(
                status_code=400,
                detail=f"stage {stage_number} video has no beep_time yet",
            )
        if stage.time_seconds <= 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"stage {stage_number} has time_seconds=0; import a "
                    "scoreboard or set the stage time before trimming"
                ),
            )
        existing = state.jobs.find_active(
            kind="trim", stage_number=stage_number, video_id=video.video_id
        )
        if existing is not None:
            return JSONResponse(existing.model_dump(mode="json"))
        job = state.jobs.submit(
            kind="trim",
            stage_number=stage_number,
            video_id=video.video_id,
            fn=lambda h, n=stage_number, vid=video.video_id: _run_trim_for_video(h, n, vid),
        )
        return JSONResponse(job.model_dump(mode="json"))

    @app.post("/api/stages/{stage_number}/videos/{video_id}/trim")
    def trim_for_video(stage_number: int, video_id: str) -> JSONResponse:
        """Submit an audit-mode trim job for ``video_id`` on ``stage_number``."""
        project, stage, video = _resolve_stage_video(stage_number, video_id)
        return _submit_trim(stage_number, stage, video, project)

    def _run_trim_for_video(handle: JobHandle, stage_number: int, video_id: str) -> None:
        """Worker for the audit-mode trim of a specific video.

        Auto-chains shot detection only when the trimmed video is the
        primary; secondaries align to the primary's audit timeline by
        their own beep, so they do not need their own shot-detection run.
        """
        handle.update(progress=0.1, message="Preparing trim...")
        proj = state.load()
        stg = proj.stage(stage_number)
        video = stg.find_video_by_id(video_id)
        if video is None or video.beep_time is None:
            raise RuntimeError("video or beep disappeared mid-flight")
        source = proj.resolve_video_path(state.project_root, video.path)
        handle.check_cancel()
        role_label = "primary" if video.role == "primary" else f"cam {video.video_id[:6]}"
        handle.update(progress=0.3, message=f"Encoding short-GOP MP4 ({role_label})...")
        audio_helpers.ensure_video_audit_trim(
            state.project_root,
            stage_number,
            video,
            source,
            video.beep_time,
            stg.time_seconds,
            project=proj,
            runner=_cancellable_runner(handle),
        )
        handle.update(progress=0.85, message="Saving project...")
        # Read-modify-write to avoid stomping concurrent edits made
        # while ffmpeg was running (e.g. another stage's beep review).
        fresh = state.load()
        stg_fresh = fresh.stage(stage_number)
        v_fresh = stg_fresh.find_video_by_id(video_id)
        if v_fresh is not None:
            v_fresh.processed["trim"] = True
            fresh.save(state.project_root)
        if (
            video.role == "primary"
            and video.beep_reviewed
            and state.jobs.find_active(kind="shot_detect", stage_number=stage_number) is None
        ):
            # Same gate as the detect-then-trim path (#71): don't burn
            # CLAP / GBDT / PANN cycles on a beep the user hasn't
            # confirmed. Manual entries pre-set ``beep_reviewed`` to True
            # so this still runs; the auto-detect path waits for the
            # user's explicit "Mark reviewed" click which re-fires
            # shot_detect from there.
            state.jobs.submit(
                kind="shot_detect",
                stage_number=stage_number,
                fn=lambda h, n=stage_number: _run_shot_detect(h, n),
            )
        handle.update(progress=1.0, message="Done")

    def _run_trim_for_stage(handle: JobHandle, stage_number: int) -> None:
        """Backward-compat shim for legacy callers (e.g. beep-override
        endpoints) that submit a trim by stage_number alone.

        Resolves the stage's primary and forwards to the per-video
        worker. New callers should pass a ``video_id`` and submit via
        :func:`_run_trim_for_video` directly.
        """
        proj = state.load()
        try:
            stg = proj.stage(stage_number)
        except KeyError as exc:
            raise RuntimeError(f"stage {stage_number} disappeared mid-flight: {exc}") from exc
        primary = stg.primary()
        if primary is None:
            raise RuntimeError(f"stage {stage_number} has no primary mid-flight")
        _run_trim_for_video(handle, stage_number, primary.video_id)

    def _run_shot_detect(handle: JobHandle, stage_number: int, reset: bool = False) -> None:
        """Worker that runs the 4-voter ensemble on the stage's audit clip.

        Reads the trimmed clip's WAV (extracting it on demand if needed),
        runs ``splitsmith.ensemble.detect_shots_ensemble`` over the
        ``[beep .. beep+stage]`` window, and writes both the consensus
        ``shots[]`` and the full voter-A universe into the audit JSON.

        ``_candidates_pending_audit.candidates`` carries every candidate
        annotated with per-voter signals (vote_a/b/c/d, ensemble_score,
        score_c, clap_diff, gunshot_prob) so the audit UI can render the
        decision trail. ``shots[]`` is seeded from the consensus subset
        unless it is already populated -- the user retains authority. If
        ``reset`` is True, ``shots[]`` is wiped first so the user can
        start over after a bad beep / detector pass.

        Adaptive prior: if the audit JSON already carries
        ``stage_rounds.expected``, voter C switches to its adaptive
        top-(K+slack) mode and the apriori boost lifts the top-K
        confidence-ranked candidates over the consensus line.
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

        # Read existing audit JSON up-front: we need ``stage_rounds.expected``
        # before running detection (it changes voter C's mode and the
        # apriori boost) and we'll merge results back into the same dict.
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
        expected_rounds: int | None = None
        sr_block = existing_json.get("stage_rounds")
        if isinstance(sr_block, dict):
            raw = sr_block.get("expected")
            if isinstance(raw, int) and raw > 0:
                expected_rounds = raw

        handle.update(progress=0.2, message="Loading ensemble models...")
        runtime = _get_ensemble_runtime()

        handle.update(progress=0.4, message="Detecting shots (4-voter ensemble)...")
        audio_array, sr = beep_detect.load_audio(audit.audio_path)
        result = ensemble_module.detect_shots_ensemble(
            audio_array,
            sr,
            beep_in_clip,
            stg.time_seconds,
            runtime,
            expected_rounds=expected_rounds,
        )

        candidates: list[dict[str, Any]] = []
        for cand in result.candidates:
            candidates.append(
                {
                    "candidate_number": cand.candidate_number,
                    "time": cand.time,
                    "ms_after_beep": cand.ms_after_beep,
                    "peak_amplitude": cand.peak_amplitude,
                    "confidence": cand.confidence,
                    "vote_a": cand.vote_a,
                    "vote_b": cand.vote_b,
                    "vote_c": cand.vote_c,
                    "vote_d": cand.vote_d,
                    "vote_total": cand.vote_total,
                    "apriori_boost": cand.apriori_boost,
                    "ensemble_score": cand.ensemble_score,
                    "score_c": cand.score_c,
                    "clap_diff": cand.clap_diff,
                    "gunshot_prob": cand.gunshot_prob,
                }
            )

        handle.update(progress=0.85, message="Saving audit JSON...")
        existing_json["_candidates_pending_audit"] = {
            "_note": (
                "4-voter ensemble (issue #31). vote_a/b/c/d=1 means the "
                "voter kept the candidate; ensemble_score = vote_total + "
                "apriori_boost. shots[] is seeded from candidates with "
                "ensemble_score >= consensus."
            ),
            "consensus": result.consensus,
            "expected_rounds": result.expected_rounds,
            "candidates": candidates,
        }
        if reset:
            existing_json["shots"] = []
        seeded_shots = False
        if not existing_json.get("shots"):
            kept = [c for c in result.candidates if c.kept]
            existing_json["shots"] = [
                {
                    "shot_number": i,
                    "candidate_number": c.candidate_number,
                    "time": c.time,
                    "ms_after_beep": c.ms_after_beep,
                    "source": "detected",
                    "ensemble_votes": c.vote_total,
                    "apriori_boost": c.apriori_boost,
                    "ensemble_score": c.ensemble_score,
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
                    "kept_count": sum(1 for c in result.candidates if c.kept),
                    "consensus": result.consensus,
                    "expected_rounds": result.expected_rounds,
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

        # Read-modify-write the project file: a long-running shot_detect
        # job must not save the snapshot it loaded at start, because the
        # user may have toggled ``beep_reviewed`` on other stages in the
        # interim (the review action kicks shot_detect, so concurrent
        # bursts are the common case). Reloading from disk and mutating
        # only the targeted field preserves those concurrent edits.
        fresh = state.load()
        stg_fresh = fresh.stage(stage_number)
        prim_fresh = stg_fresh.primary()
        if prim_fresh is not None:
            prim_fresh.processed["shot_detect"] = True
            fresh.save(state.project_root)
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

    @app.post("/api/jobs/acknowledge-failures", response_model=list[Job])
    def acknowledge_all_failures() -> list[Job]:
        """Mark every currently-unacknowledged FAILED job as seen (issue #73).

        Used by the JobsPanel "Dismiss all failures" header action. Returns
        the snapshots that actually flipped to acknowledged so the SPA can
        diff against its in-memory list without an extra refetch.
        """
        return state.jobs.acknowledge_all_failures()

    @app.post("/api/jobs/{job_id}/acknowledge", response_model=Job)
    def acknowledge_job(job_id: str) -> Job:
        """Mark a single failed job as seen (issue #73).

        No-op for jobs that aren't failed or are already acknowledged --
        the snapshot is returned unchanged so the SPA can still pin its
        local state to the server response.
        """
        job = state.jobs.acknowledge(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        return job

    @app.post("/api/jobs/{job_id}/cancel", response_model=Job)
    def cancel_job(job_id: str) -> Job:
        """Request cooperative cancellation of a running or pending job.

        The registry sets ``cancel_requested=True`` and (for trim jobs)
        terminates the running ffmpeg subprocess. The worker then bails
        out at its next phase boundary, ending the job in
        ``status=cancelled``. Idempotent: cancelling a finished job
        returns the existing snapshot unchanged.
        """
        job = state.jobs.cancel(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        return job

    def _apply_beep_override(
        project: MatchProject,
        stage: StageEntry,
        video: StageVideo,
        beep_time: float | None,
    ) -> None:
        """Apply a manual beep override to ``video``.

        Generic over role: secondaries also need their cached trim
        invalidated when the user moves their beep, since the trim window
        is anchored to that beep. ``beep_time=None`` clears back to "no
        beep yet"; otherwise sets ``beep_source="manual"`` and drops the
        candidate list so the UI doesn't keep suggesting a stale auto pick.
        Shot-detect flag clearing is primary-only -- secondaries don't
        carry their own shot timeline.
        """
        if beep_time is None:
            video.beep_time = None
            video.beep_source = None
            video.beep_peak_amplitude = None
            video.beep_duration_ms = None
            video.beep_candidates = []
            video.processed["beep"] = False
            video.processed["trim"] = False
            video.beep_reviewed = False
            if video.role == "primary":
                video.processed["shot_detect"] = False
        else:
            video.beep_time = beep_time
            video.beep_source = "manual"
            video.beep_peak_amplitude = None
            video.beep_duration_ms = None
            video.beep_candidates = []
            video.processed["beep"] = True
            video.processed["trim"] = False
            # Manual beep entry implies the user looked at the
            # waveform to type the value -- skip the review pill (#71).
            video.beep_reviewed = True
            if video.role == "primary":
                video.processed["shot_detect"] = False

        audio_helpers.invalidate_video_audit_trim(
            state.project_root, stage.stage_number, video, project=project
        )

    def _maybe_chain_trim(stage: StageEntry, video: StageVideo) -> None:
        """Auto-fire a trim job for ``video`` when conditions allow.

        Used after a beep override / candidate select: if the user just
        gave us a beep and the stage time is known, the next thing they
        want is a fresh short-GOP trim. Dedupes through ``find_active``
        so a still-running job adopts instead of racing.
        """
        if video.beep_time is None or stage.time_seconds <= 0:
            return
        if (
            state.jobs.find_active(
                kind="trim", stage_number=stage.stage_number, video_id=video.video_id
            )
            is not None
        ):
            return
        state.jobs.submit(
            kind="trim",
            stage_number=stage.stage_number,
            video_id=video.video_id,
            fn=lambda h, n=stage.stage_number, vid=video.video_id: _run_trim_for_video(h, n, vid),
        )

    def _select_candidate_on_video(video: StageVideo, time_value: float) -> None:
        """Promote the candidate at ``time_value`` (within 1 ms) on ``video``.

        Mirrors the v1 primary path: the time still came from the detector
        so ``beep_source="auto"`` is preserved; only the chosen candidate
        changes. Raises ``HTTPException`` for caller-facing errors so both
        the per-video and legacy primary endpoints get the same response
        shapes.
        """
        if not video.beep_candidates:
            raise HTTPException(
                status_code=400,
                detail="video has no candidate list yet; run detect-beep first",
            )
        match_eps = 1e-3
        chosen = None
        for c in video.beep_candidates:
            if abs(c.time - time_value) <= match_eps:
                chosen = c
                break
        if chosen is None:
            available = ", ".join(f"{c.time:.3f}" for c in video.beep_candidates)
            raise HTTPException(
                status_code=400,
                detail=(
                    f"no candidate within {match_eps * 1000:.0f} ms of "
                    f"{time_value:.3f}s; available: [{available}]"
                ),
            )
        video.beep_time = chosen.time
        video.beep_source = "auto"
        video.beep_peak_amplitude = chosen.peak_amplitude
        video.beep_duration_ms = chosen.duration_ms
        video.processed["beep"] = True
        video.processed["trim"] = False
        # Switching candidate is a fresh claim about which moment is
        # the beep -- prior review approval doesn't carry over (#71).
        video.beep_reviewed = False
        if video.role == "primary":
            video.processed["shot_detect"] = False

    @app.post("/api/stages/{stage_number}/videos/{video_id}/beep")
    def override_beep_for_video(
        stage_number: int, video_id: str, req: BeepOverrideRequest
    ) -> JSONResponse:
        """Manually set or clear ``video``'s beep timestamp.

        ``req.beep_time = None`` clears back to "no beep yet"; otherwise
        the value (in seconds, must be >= 0) is taken as authoritative
        with ``beep_source="manual"``. Same dedupe + auto-trim chain as
        the legacy primary endpoint, just keyed per video.
        """
        project, stage, video = _resolve_stage_video(stage_number, video_id)
        if req.beep_time is not None and req.beep_time < 0.0:
            raise HTTPException(status_code=400, detail="beep_time must be >= 0")
        _apply_beep_override(project, stage, video, req.beep_time)
        project.save(state.project_root)
        if req.beep_time is not None:
            _maybe_chain_trim(stage, video)
        return JSONResponse(project.model_dump(mode="json"))

    @app.post("/api/stages/{stage_number}/beep")
    def override_beep(stage_number: int, req: BeepOverrideRequest) -> JSONResponse:
        """Backward-compat shim: manually set / clear the primary's beep."""
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
        if req.beep_time is not None and req.beep_time < 0.0:
            raise HTTPException(status_code=400, detail="beep_time must be >= 0")
        _apply_beep_override(project, stage, primary, req.beep_time)
        project.save(state.project_root)
        if req.beep_time is not None:
            _maybe_chain_trim(stage, primary)
        return JSONResponse(project.model_dump(mode="json"))

    @app.post("/api/stages/{stage_number}/videos/{video_id}/beep/snap")
    def snap_beep_for_video(stage_number: int, video_id: str, req: BeepSnapRequest) -> JSONResponse:
        """Refine a user-placed beep marker by snapping it to the strongest
        tone in a tight window around the hint.

        The user has listened to the audio and dropped a marker close to
        the beep; this endpoint runs the same bandpass + envelope detector
        on a slice of the WAV and returns the rise-foot leading edge of
        the strongest run inside ``[hint - window_s, hint + window_s]``.
        Stateless: caller decides whether to accept the proposal as a
        manual override (POST .../beep) or dismiss it.

        404 when no run in the window meets the duration / amplitude
        criteria. The SPA surfaces that as "no beep found nearby; widen
        the window or move the marker".
        """
        project, _stage, video = _resolve_stage_video(stage_number, video_id)
        source = project.resolve_video_path(state.project_root, video.path)
        _ensure_source_reachable(stage_number, source)
        if req.hint_time < 0.0:
            raise HTTPException(status_code=400, detail="hint_time must be >= 0")
        if req.window_s <= 0.0:
            raise HTTPException(status_code=400, detail="window_s must be > 0")

        audio_path = audio_helpers.ensure_video_audio(
            state.project_root, stage_number, video, source, project=project
        )
        audio, sr = beep_detect.load_audio(audio_path)
        duration_s = audio.size / sr if sr > 0 else 0.0
        if duration_s <= 0:
            raise HTTPException(status_code=500, detail="cached audio is empty")
        if req.hint_time > duration_s:
            raise HTTPException(
                status_code=400,
                detail=f"hint_time {req.hint_time:.3f}s exceeds audio duration {duration_s:.3f}s",
            )

        slice_start_s = max(0.0, req.hint_time - req.window_s)
        slice_end_s = min(duration_s, req.hint_time + req.window_s)
        start_idx = int(round(slice_start_s * sr))
        end_idx = int(round(slice_end_s * sr))
        if end_idx <= start_idx:
            raise HTTPException(
                status_code=400,
                detail="snap window is empty after clipping to audio bounds",
            )
        sliced = audio[start_idx:end_idx]

        # Relax detection thresholds: the user has already localized the
        # beep with their marker, so the snap doesn't need the full-clip
        # detector's belt-and-braces against false positives.
        # - silence_window_s shrinks to fit the slice (the configured
        #   1.5 s pre-window would be silently truncated otherwise).
        # - search_window_s = 0 disables the front-of-audio cap (the
        #   slice itself is the cap).
        # - min_duration_ms drops to 40 ms so a clipped / faint beep
        #   whose envelope only briefly clears the cutoff still
        #   registers. The full-clip detector keeps 150 ms to suppress
        #   short clicks; in a 2 s window centred on the marker, those
        #   competing transients aren't a concern.
        # - min_abs_peak drops to 0.01 so a quiet recording (Insta360
        #   GO 3S beep at low gain) still has a candidate run.
        cfg = BeepDetectConfig(
            silence_window_s=min(0.3, max(0.05, req.window_s * 0.6)),
            silence_pre_skip_s=0.05,
            search_window_s=0.0,
            top_n_candidates=8,
            min_duration_ms=40,
            min_abs_peak=0.01,
            min_amplitude=0.05,
        )
        try:
            detection = beep_detect.detect_beep(sliced, sr, cfg)
        except beep_detect.BeepNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        # Times in ``detection`` are slice-local; shift back to source
        # time. Pick the candidate closest to the user's hint -- in a
        # tight window the hint is the prior, not silence-preference.
        hint_in_slice = req.hint_time - slice_start_s
        chosen = min(detection.candidates, key=lambda c: abs(c.time - hint_in_slice))
        snapped_time = float(chosen.time + slice_start_s)
        return JSONResponse(
            BeepSnapResponse(
                snapped_time=snapped_time,
                delta=snapped_time - req.hint_time,
                peak_amplitude=float(chosen.peak_amplitude),
                score=float(chosen.score),
                duration_ms=float(chosen.duration_ms),
            ).model_dump()
        )

    @app.post("/api/stages/{stage_number}/videos/{video_id}/beep/select")
    def select_beep_candidate_for_video(
        stage_number: int, video_id: str, req: BeepSelectRequest
    ) -> JSONResponse:
        """Promote one of ``video``'s ranked candidates as authoritative."""
        project, stage, video = _resolve_stage_video(stage_number, video_id)
        _select_candidate_on_video(video, req.time)
        audio_helpers.invalidate_video_audit_trim(
            state.project_root, stage_number, video, project=project
        )
        project.save(state.project_root)
        _maybe_chain_trim(stage, video)
        return JSONResponse(project.model_dump(mode="json"))

    @app.post("/api/stages/{stage_number}/videos/{video_id}/beep/review")
    def set_beep_reviewed(stage_number: int, video_id: str, req: BeepReviewRequest) -> JSONResponse:
        """Flip ``video.beep_reviewed`` (issue #71).

        Setting True requires ``beep_time`` to be set; setting False is
        always allowed (e.g. user wants to re-review). For a primary
        whose trim is already cached, marking True kicks off shot
        detection -- this is the explicit unblock point for the
        downstream pipeline (auto-detect leaves the flag False so the
        ensemble doesn't burn cycles on an unconfirmed beep, and we
        finally fire it here once the user has listened and approved).
        """
        project, stage, video = _resolve_stage_video(stage_number, video_id)
        if req.reviewed and video.beep_time is None:
            raise HTTPException(
                status_code=400,
                detail="cannot mark a beep reviewed before one has been detected",
            )
        video.beep_reviewed = bool(req.reviewed)
        project.save(state.project_root)

        # When the user confirms the primary's beep AND the trim is
        # already cached from the auto-detect chain, kick off the
        # gated shot-detect now. No-op when trim hasn't run yet (it
        # will run after, then auto-chain because the gate is open),
        # or for secondaries (no shot timeline of their own).
        if (
            req.reviewed
            and video.role == "primary"
            and video.processed.get("trim")
            and state.jobs.find_active(kind="shot_detect", stage_number=stage_number) is None
        ):
            state.jobs.submit(
                kind="shot_detect",
                stage_number=stage_number,
                fn=lambda h, n=stage_number: _run_shot_detect(h, n),
            )

        return JSONResponse(project.model_dump(mode="json"))

    @app.post("/api/stages/{stage_number}/beep/select")
    def select_beep_candidate(stage_number: int, req: BeepSelectRequest) -> JSONResponse:
        """Backward-compat shim: promote a ranked candidate on the primary.

        Lets the user fix a wrong auto-pick without typing a timestamp.
        Re-uses the auto-detect provenance because the time still came
        from the detector -- we only changed which candidate the project
        trusts. Triggers a re-trim so the cached audit clip lines up.
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
        _select_candidate_on_video(primary, req.time)
        audio_helpers.invalidate_video_audit_trim(
            state.project_root, stage_number, primary, project=project
        )
        project.save(state.project_root)
        _maybe_chain_trim(stage, primary)
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

    def _serve_beep_preview(
        project: MatchProject,
        stage_number: int,
        video: StageVideo,
        t: float | None,
    ) -> FileResponse:
        """Build the ~1 s MP4 around ``t`` (or ``video.beep_time``) and serve it.

        Shared by the legacy primary endpoint and the per-video endpoint
        so both honour the same 404 / 424 / cache semantics.
        """
        source = project.resolve_video_path(state.project_root, video.path)
        _ensure_source_reachable(stage_number, source)
        center = t if t is not None else video.beep_time
        if center is None:
            raise HTTPException(
                status_code=404,
                detail=f"stage {stage_number} video has no beep_time yet",
            )
        if center < 0:
            raise HTTPException(status_code=400, detail="t must be >= 0")
        thumbs_dir = project.thumbs_path(state.project_root)
        try:
            clip = thumbnail_helpers.ensure_clip(
                source,
                cache_dir=thumbs_dir,
                center_time=float(center),
                duration_s=1.0,
                width=480,
            )
        except thumbnail_helpers.ThumbnailError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return FileResponse(clip, media_type="video/mp4", filename=clip.name)

    @app.get("/api/stages/{stage_number}/videos/{video_id}/beep-preview")
    def video_beep_preview(
        stage_number: int, video_id: str, t: float | None = None
    ) -> FileResponse:
        """Serve a ~1 s MP4 around ``video``'s beep (or override ``t``)."""
        project, _stage, video = _resolve_stage_video(stage_number, video_id)
        return _serve_beep_preview(project, stage_number, video, t)

    @app.get("/api/stages/{stage_number}/beep-preview")
    def stage_beep_preview(stage_number: int, t: float | None = None) -> FileResponse:
        """Serve a tiny MP4 around the primary's beep timestamp (#27, #22).

        Default center is the primary's persisted ``beep_time``. The
        optional ``t`` query param overrides it so the BeepCandidates
        picker (#22) can preview alternative ranked candidates without
        promoting them first. Cache keys on (source mtime/size, center
        time, duration), so each distinct ``t`` gets its own cached clip.
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
        return _serve_beep_preview(project, stage_number, primary, t)

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

    @app.get("/api/stages/{stage_number}/anomalies")
    def get_stage_anomalies(stage_number: int) -> JSONResponse:
        """Return structured anomalies for the saved audit JSON (issue #42).

        Single source of truth for ``report.detect_anomalies_structured``:
        the audit screen also runs the same rules client-side for live
        feedback while the user keeps / rejects markers, but consumers
        that only need a snapshot (external tooling, integration tests,
        the report.txt writer) can hit this endpoint instead of reading
        the JSON and re-deriving.

        Returns ``{anomalies: []}`` when the stage has no audit JSON, no
        beep yet, or an empty ``shots[]`` -- the SPA renders these as
        "no anomalies" / "no shots audited yet" depending on context.
        """
        project = state.load()
        try:
            stg = project.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        audit_file = project.audit_path(state.project_root) / f"stage{stage_number}.json"
        if not audit_file.exists():
            return JSONResponse({"anomalies": []})
        try:
            audit_payload = json.loads(audit_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=500, detail=f"audit read failed: {exc}") from exc

        prim = stg.primary()
        beep_time = prim.beep_time if prim is not None and prim.beep_time is not None else 0.0
        shots = export_helpers.audit_shots_to_engine_shots(
            audit_payload, beep_time_in_source=beep_time
        )
        anomalies = report.detect_anomalies_structured(shots, beep_time, stg.time_seconds)
        return JSONResponse({"anomalies": [a.model_dump() for a in anomalies]})

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
        if stage is not None:
            # Prefer the per-video short-GOP trim when one exists. This is
            # the multi-cam path: each angle gets its own scrub-friendly
            # cache cut around its own beep, so dragging the audit
            # playhead doesn't stall on a 4K MOV from a phone.
            trimmed = audio_helpers.trimmed_video_path(
                state.project_root, stage.stage_number, video, project=project
            )
            if trimmed.exists():
                served_path = trimmed.resolve()
        if served_path is None:
            served_path = project.resolve_video_path(state.project_root, video.path).resolve()
            # Same structured shape as detect-beep / trim / preview so
            # the SPA's "reconnect external storage" surface is uniform.
            _ensure_source_reachable(stage.stage_number if stage is not None else None, served_path)

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

        # Auto-queue beep on assignment to a real stage (#67). Skip when
        # unassigning (to_stage_number=None) or marking ignored -- those
        # don't put the video into the pipeline.
        if req.to_stage_number is not None and req.role != "ignored":
            stage = project.stage(req.to_stage_number)
            video = next((v for v in stage.videos if str(v.path) == req.video_path), None)
            if video is not None:
                _auto_queue_beep_if_needed(project, req.to_stage_number, video)

        return JSONResponse(project.model_dump(mode="json"))

    @app.post("/api/assignments/swap-primary")
    def swap_primary(req: SwapPrimaryRequest) -> JSONResponse:
        """Promote ``video_path`` to primary on ``stage_number``.

        Audit-safe: when the stage has shots in its audit JSON, refuses with
        a 409 response unless ``confirm=True`` is passed. On confirm, the
        audit JSON is renamed to ``.bak`` so a bad swap is recoverable, and
        the new primary's processed flags are cleared so detection re-runs.
        """
        project = state.load()
        try:
            stage = project.stage(req.stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        warns = project.primary_swap_warns(state.project_root, stage_number=req.stage_number)
        if warns and not req.confirm:
            existing = stage.primary()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "audit_exists",
                    "message": (
                        f"Stage {req.stage_number} has audit work on its current "
                        "primary. Confirm the swap to back the audit up to .bak "
                        "and re-run detection on the new primary."
                    ),
                    "stage_number": req.stage_number,
                    "current_primary": str(existing.path) if existing else None,
                    "new_primary": req.video_path,
                },
            )
        try:
            project.swap_primary(
                Path(req.video_path),
                root=state.project_root,
                stage_number=req.stage_number,
                backup_audit=warns,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        project.save(state.project_root)

        # Auto-queue beep for the new primary (#67). swap_primary may
        # clear ``processed.beep`` to force re-detection on the new
        # video's audio; the helper picks up the cleared flag and queues
        # accordingly. No-op when the video already had a current beep.
        new_primary = project.stage(req.stage_number).primary()
        if new_primary is not None:
            _auto_queue_beep_if_needed(project, req.stage_number, new_primary)

        return JSONResponse(project.model_dump(mode="json"))

    @app.get("/api/exports/overview")
    def export_overview() -> JSONResponse:
        """Match-overview payload for the Analysis & Export screen.

        Returns one row per stage with audit + export status (shot count,
        pending candidates, file paths, last export time, ready-to-export
        flag). Pure stat: no detection, no rewriting of audit JSON.
        """
        project = state.load()
        rows = project.export_overview(state.project_root)
        return JSONResponse({"stages": [r.model_dump(mode="json") for r in rows]})

    @app.post("/api/stages/{stage_number}/export")
    def export_stage(stage_number: int, req: ExportStageRequest) -> JSONResponse:
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
        project = state.load()
        try:
            stage = project.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        primary = stage.primary()
        if primary is None or primary.beep_time is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"stage {stage_number} has no primary or no beep yet; "
                    "finish ingest + audit before exporting"
                ),
            )
        if stage.time_seconds <= 0 or stage.scorecard_updated_at is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"stage {stage_number} is a placeholder; import a real "
                    "scoreboard before exporting"
                ),
            )
        # Source-reachability surfaces as a structured 424 so the SPA
        # renders the same "reconnect external storage" message used
        # elsewhere -- even if the user only wants CSV/report (those would
        # still work, but the explicit 424 lets them re-try after
        # reconnecting rather than hunting for the partial degradation
        # message in the per-row anomaly list).
        if req.write_trim or req.write_fcpxml:
            _ensure_source_reachable(
                stage_number, project.resolve_video_path(state.project_root, primary.path)
            )

        existing = state.jobs.find_active(kind="export", stage_number=stage_number)
        if existing is not None:
            return JSONResponse(existing.model_dump(mode="json"))
        job = state.jobs.submit(
            kind="export",
            stage_number=stage_number,
            fn=lambda h, n=stage_number, r=req: _run_export_for_stage(h, n, r),
        )
        return JSONResponse(job.model_dump(mode="json"))

    def _run_export_for_stage(
        handle: JobHandle, stage_number: int, req: ExportStageRequest
    ) -> None:
        """Worker for /api/stages/{n}/export. Phases mirror the user's
        mental model so the JobsPanel message is meaningful."""
        from ..config import StageData as EngineStageData

        handle.update(progress=0.05, message="Loading project...")
        proj = state.load()
        stg = proj.stage(stage_number)
        prim = stg.primary()
        if prim is None or prim.beep_time is None:
            raise RuntimeError("primary or beep disappeared mid-flight")

        audit_file = proj.audit_path(state.project_root) / f"stage{stage_number}.json"
        exports_dir = proj.exports_path(state.project_root)
        source_video = proj.resolve_video_path(state.project_root, prim.path)
        engine_stage = EngineStageData(
            stage_number=stg.stage_number,
            stage_name=stg.stage_name,
            time_seconds=stg.time_seconds,
            scorecard_updated_at=stg.scorecard_updated_at,
        )

        # Phase progress is approximate; trim dominates wall time when the
        # source is large, so we hold at 0.4 through the trim phase and
        # then jump on the small writers.
        if req.write_trim:
            handle.update(progress=0.15, message="Trimming source (lossless stream copy)...")
        elif req.write_fcpxml:
            handle.update(progress=0.15, message="Reading audit + preparing FCPXML...")
        else:
            handle.update(progress=0.15, message="Reading audit JSON...")
        handle.check_cancel()

        # Multi-cam (issue #54). Each secondary with a known beep ships
        # alongside the primary so a single Generate click produces the
        # complete multi-cam timeline. Cams without a beep yet -- e.g.
        # registered but ingest didn't finish -- are silently skipped; the
        # SPA's ingest screen flags those rows separately.
        secondaries: list[export_helpers.SecondaryExport] = []
        for sv in stg.videos:
            if sv.role != "secondary":
                continue
            if sv.beep_time is None:
                continue
            sec_source = proj.resolve_video_path(state.project_root, sv.path)
            secondaries.append(
                export_helpers.SecondaryExport(
                    video_id=sv.video_id,
                    source_path=sec_source,
                    beep_time_in_source=sv.beep_time,
                    label=f"Cam {sv.video_id}",
                )
            )

        try:
            result = export_helpers.export_stage(
                request=export_helpers.StageExportRequest(
                    stage_number=stage_number,
                    write_trim=req.write_trim,
                    write_csv=req.write_csv,
                    write_fcpxml=req.write_fcpxml,
                    write_report=req.write_report,
                    write_overlay=req.write_overlay,
                ),
                audit_path=audit_file,
                exports_dir=exports_dir,
                source_video_path=source_video if source_video.exists() else None,
                stage_data=engine_stage,
                beep_time_in_source=prim.beep_time,
                pre_buffer_seconds=proj.trim_pre_buffer_seconds,
                post_buffer_seconds=proj.trim_post_buffer_seconds,
                config=Config(),
                secondaries=secondaries,
            )
        except export_helpers.StageExportError as exc:
            # Surface as a job failure with the exporter's own message so
            # the JobsPanel row reads "audit JSON has no shots in shots[]"
            # rather than a stack trace.
            raise RuntimeError(str(exc)) from exc

        handle.update(progress=0.95, message="Saving project...")
        proj.updated_at = datetime.now(UTC)
        proj.save(state.project_root)

        # Final message summarises what shipped + flags any skipped
        # artefacts (FCPXML without a trim, etc). The full list lives in
        # the report.txt; the user can Reveal it for details.
        bits: list[str] = []
        if result.trimmed_video_path is not None:
            bits.append("trim")
        if result.secondary_trimmed_paths:
            n = len(result.secondary_trimmed_paths)
            bits.append(f"{n} secondary trim{'s' if n != 1 else ''}")
        if result.csv_path is not None:
            bits.append("csv")
        if result.fcpxml_path is not None:
            bits.append("fcpxml")
        if result.report_path is not None:
            bits.append("report")
        if result.overlay_path is not None:
            bits.append("overlay")
        summary = ", ".join(bits) if bits else "nothing written"
        if result.anomalies:
            n = len(result.anomalies)
            word = "anomaly" if n == 1 else "anomalies"
            summary += f" ({n} {word} -- see report.txt)"
        handle.update(progress=1.0, message=f"Done: {summary}")

    @app.post("/api/files/reveal")
    def reveal_file(req: RevealRequest) -> JSONResponse:
        """Reveal a file in the OS file manager.

        Restricted to paths inside the current project root so the endpoint
        can never be coerced into opening arbitrary locations. macOS uses
        ``open -R`` (selects the file in Finder); Linux uses ``xdg-open``
        on the parent dir; Windows uses ``explorer /select``.
        """
        target = Path(req.path).expanduser()
        try:
            resolved = target.resolve(strict=True)
        except (OSError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=f"not found: {target}") from exc
        try:
            resolved.relative_to(state.project_root.resolve())
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="reveal path must be inside the project root",
            ) from exc
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", "-R", str(resolved)], check=False)
            elif sys.platform.startswith("win"):
                subprocess.run(["explorer", f"/select,{resolved}"], check=False)
            else:
                # xdg-open doesn't support file selection; opening the parent
                # is the closest cross-distro behaviour.
                parent = resolved.parent if resolved.is_file() else resolved
                subprocess.run(["xdg-open", str(parent)], check=False)
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"failed to launch file manager: {exc}"
            ) from exc
        return JSONResponse({"revealed": str(resolved)})

    @app.post("/api/stages/{stage_number}/skip")
    def set_stage_skipped(stage_number: int, req: SkipStageRequest) -> JSONResponse:
        """Mark ``stage_number`` as skipped (or un-skip it).

        A skipped stage is excluded from "next step" gating in the ingest
        screen so the user can advance even when the stage has no videos
        (e.g. they didn't film stage 4).
        """
        project = state.load()
        try:
            stage = project.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        stage.skipped = req.skipped
        project.save(state.project_root)
        return JSONResponse(project.model_dump(mode="json"))

    # ----------------------------------------------------------------------
    # Global user config (#75)
    # ----------------------------------------------------------------------
    #
    # Cross-project state lives in ``~/.splitsmith/`` (override via
    # ``SPLITSMITH_HOME``; opt out with ``SPLITSMITH_DISABLE_USER_CONFIG=1``).
    # The endpoints below let the SPA read the recent-projects list for its
    # project picker and read/write the saved SSI Scoreboard identity so the
    # scoreboard import flow can prefill 'me' instead of asking each project.

    @app.get("/api/user/recent-projects")
    def list_recent_projects() -> JSONResponse:
        projects = user_config.get_recent_projects()
        return JSONResponse({"projects": [p.model_dump(mode="json") for p in projects]})

    @app.post("/api/user/recent-projects/forget")
    def forget_recent_project(req: ForgetRecentProjectRequest) -> JSONResponse:
        removed = user_config.remove_recent_project(Path(req.path))
        projects = user_config.get_recent_projects()
        return JSONResponse(
            {
                "removed": removed,
                "projects": [p.model_dump(mode="json") for p in projects],
            }
        )

    @app.get("/api/user/scoreboard-identity")
    def get_scoreboard_identity() -> JSONResponse:
        identity = user_config.load_scoreboard_identity()
        if identity is None:
            raise HTTPException(status_code=404, detail="no scoreboard identity saved")
        return JSONResponse(identity.model_dump(mode="json"))

    @app.put("/api/user/scoreboard-identity")
    def put_scoreboard_identity(req: ScoreboardIdentityRequest) -> JSONResponse:
        identity = user_config.ScoreboardIdentity(
            shooter_id=req.shooter_id,
            display_name=req.display_name,
            division=req.division,
            club=req.club,
            base_url=req.base_url,
        )
        user_config.save_scoreboard_identity(identity)
        return JSONResponse(identity.model_dump(mode="json"))

    @app.delete("/api/user/scoreboard-identity")
    def delete_scoreboard_identity() -> JSONResponse:
        user_config.clear_scoreboard_identity()
        return JSONResponse({"ok": True})

    # ----------------------------------------------------------------------
    # Lab: fixture management + ensemble eval + tuning
    # ----------------------------------------------------------------------
    #
    # End-user-visible only via the /lab route in the SPA. Heavy CLAP/PANN
    # runtime is loaded once on first /api/lab/eval call and cached on the
    # FastAPI app instance so subsequent eval / rescore calls amortise it.

    from .. import lab as lab_module

    _lab_runtime_cache: dict[str, Any] = {}
    _lab_universe_cache: dict[str, Any] = {}  # session: most recent run

    def _get_lab_runtime() -> Any:
        rt = _lab_runtime_cache.get("runtime")
        if rt is None:
            rt = ensemble_module.api.load_ensemble_runtime()
            _lab_runtime_cache["runtime"] = rt
        return rt

    @app.get("/api/lab/fixtures")
    def lab_fixtures() -> JSONResponse:
        return JSONResponse([r.model_dump(mode="json") for r in lab_module.list_fixtures()])

    @app.post("/api/lab/eval")
    def lab_eval(
        payload: dict[str, Any] = Body(default_factory=dict),  # noqa: B008
    ) -> JSONResponse:
        slugs = payload.get("slugs")
        cfg_payload = payload.get("config") or {}
        cfg = lab_module.EvalConfig.model_validate(cfg_payload)
        runtime = _get_lab_runtime()
        run = lab_module.run_eval(
            runtime,
            slugs=slugs if isinstance(slugs, list) else None,
            config=cfg,
        )
        if payload.get("persist", True):
            try:
                lab_module.save_run(run)
            except OSError as exc:
                logger.warning("lab: save_run failed: %s", exc)
        _lab_universe_cache["universe"] = run.universe
        _lab_universe_cache["last_run"] = run
        return JSONResponse(run.model_dump(mode="json"))

    @app.post("/api/lab/rescore")
    def lab_rescore(payload: dict[str, Any] = Body(...)) -> JSONResponse:  # noqa: B008
        cfg = lab_module.EvalConfig.model_validate(payload.get("config") or {})
        universe = _lab_universe_cache.get("universe")
        if universe is None:
            raise HTTPException(
                status_code=409,
                detail="no cached eval universe; call /api/lab/eval first",
            )
        run = lab_module.rescore_universe(universe, cfg)
        _lab_universe_cache["universe"] = run.universe
        _lab_universe_cache["last_run"] = run
        return JSONResponse(run.model_dump(mode="json"))

    @app.post("/api/lab/promote")
    def lab_promote(payload: dict[str, Any] = Body(...)) -> JSONResponse:  # noqa: B008
        stage_n = payload.get("stage_number")
        slug = payload.get("slug")
        overwrite = bool(payload.get("overwrite", False))
        if not isinstance(stage_n, int) or not isinstance(slug, str) or not slug:
            raise HTTPException(
                status_code=400,
                detail="payload must include integer 'stage_number' and string 'slug'",
            )
        project = state.load()
        try:
            stg = project.stage(stage_n)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        audit_json = project.audit_path(state.project_root) / f"stage{stage_n}.json"
        try:
            audit_audio = _resolve_audit_audio(project, stage_n)
        except HTTPException:
            raise
        audit_wav = audit_audio.audio_path
        if not audit_json.exists() or not audit_wav.exists():
            raise HTTPException(
                status_code=409,
                detail="stage has no audit JSON / WAV; run shot-detect first",
            )
        try:
            rec = lab_module.promote_stage_to_fixture(
                lab_module.PromoteRequest(
                    audit_json_path=audit_json,
                    audit_wav_path=audit_wav,
                    fixture_slug=slug,
                    overwrite=overwrite,
                    extra_metadata={
                        "project_root": str(state.project_root),
                        "stage_number": stage_n,
                        "stage_name": getattr(stg, "name", None),
                    },
                )
            )
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse(rec.model_dump(mode="json"))

    @app.post("/api/lab/save-config")
    def lab_save_config(payload: dict[str, Any] = Body(...)) -> JSONResponse:  # noqa: B008
        """Write a finished run's config + provenance to ``configs/<name>.yaml``.

        Body: ``{name, note?, overwrite?}``. The active universe (the
        most recent eval/rescore) provides the run; if no eval has run
        in this server session we 409 so the user can't accidentally
        save an empty config.
        """
        name = payload.get("name")
        note = payload.get("note")
        overwrite = bool(payload.get("overwrite", False))
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=400, detail="payload must include non-empty 'name'")
        universe = _lab_universe_cache.get("universe")
        if universe is None:
            raise HTTPException(
                status_code=409,
                detail="no cached eval universe; run /api/lab/eval first",
            )
        last_run = _lab_universe_cache.get("last_run")
        if last_run is None:
            raise HTTPException(
                status_code=409,
                detail="no cached run; rescore or run eval first",
            )
        try:
            target = lab_module.save_config_yaml(
                run=last_run,
                name=name,
                note=note if isinstance(note, str) else None,
                overwrite=overwrite,
            )
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse({"path": str(target)})

    @app.post("/api/lab/rebuild-calibration")
    def lab_rebuild_calibration(
        payload: dict[str, Any] = Body(default_factory=dict),  # noqa: B008
    ) -> JSONResponse:
        """Submit a job that re-runs ``scripts/build_ensemble_artifacts.py``.

        Long-running (model-bound), so it goes through the JobRegistry
        and the SPA polls /api/jobs/{id} like any other job. After it
        completes the next /api/lab/eval call will pick up the new
        thresholds because the cached EnsembleRuntime is invalidated.
        """
        target_recall = float(payload.get("target_recall", 0.95))
        tolerance_ms = float(payload.get("tolerance_ms", 75.0))
        fixtures = payload.get("fixtures")
        if fixtures is not None and not isinstance(fixtures, list):
            raise HTTPException(
                status_code=400,
                detail="'fixtures' must be a list of slugs or omitted",
            )

        def _run(handle: JobHandle) -> None:
            # Import here to avoid pulling sklearn at server startup.
            scripts_dir = Path(__file__).resolve().parents[3] / "scripts"
            sys.path.insert(0, str(scripts_dir))
            try:
                import build_ensemble_artifacts as build_mod  # type: ignore[import-not-found]
            finally:
                sys.path.pop(0)

            def log(msg: str) -> None:
                handle.check_cancel()
                handle.update(message=msg)

            build_mod.build_artifacts(
                fixtures=fixtures if fixtures else None,
                target_recall=target_recall,
                tolerance_ms=tolerance_ms,
                log=log,
            )
            # Drop the cached runtime so the next eval reloads the new
            # calibration JSON + GBDT model.
            _lab_runtime_cache.pop("runtime", None)
            handle.update(progress=1.0, message="calibration rebuilt")

        job = state.jobs.submit(kind="rebuild_calibration", fn=_run)
        return JSONResponse(job.model_dump(mode="json"))

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


def _suggested_starts(last_scanned_dir: str | None) -> list[dict[str, str]]:
    """Bookmarks the folder picker shows in a sidebar.

    Three groups, in display order:

    1. **Recent** -- the last folder the user scanned (if any). Surfaced
       on top so the cam-over-USB flow ("plug in, scan, repeat") doesn't
       require re-typing the path.
    2. **Home** -- the user's home plus the conventional video folders
       (~/Movies, ~/Videos, ~/Downloads, ~/Desktop). Cross-platform
       sensible defaults.
    3. **Removable & network** -- platform-specific mount discovery,
       best-effort. Each entry is wrapped in a per-path 200ms timeout
       so a stale network mount can't hang the picker open.
    """
    seen: set[str] = set()
    out: list[dict[str, str]] = []

    def add(path: Path | str, label: str, kind: str) -> None:
        p = str(Path(path))
        if p in seen:
            return
        seen.add(p)
        out.append({"path": p, "label": label, "kind": kind})

    if last_scanned_dir:
        p = Path(last_scanned_dir).expanduser()
        if _is_dir_with_timeout(p):
            add(p, p.name or str(p), "recent")

    home = Path.home()
    home_candidates = [
        (home, "Home"),
        (home / "Movies", "Movies"),
        (home / "Videos", "Videos"),
        (home / "Downloads", "Downloads"),
        (home / "Desktop", "Desktop"),
    ]
    for path, label in home_candidates:
        if _is_dir_with_timeout(path):
            add(path, label, "home")

    for path, label, kind in _discover_mounts():
        add(path, label, kind)

    return out


def _is_dir_with_timeout(path: Path, *, timeout: float = 0.2) -> bool:
    """``path.is_dir()`` with a wall-clock cap.

    Stale network mounts can hang ``stat()`` indefinitely. Running the
    check on a worker thread and bailing on timeout keeps the picker
    responsive at the cost of a false negative on slow-but-alive shares
    (the user can still type the path manually).
    """
    import concurrent.futures

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(path.is_dir)
            return future.result(timeout=timeout)
    except (concurrent.futures.TimeoutError, OSError):
        return False


def _discover_mounts() -> list[tuple[Path, str, str]]:
    """Cross-platform best-effort discovery of mounted volumes.

    Returns a list of ``(path, label, kind)`` tuples. Kind is
    ``"network"`` for paths reported by the platform's mount table as a
    network filesystem (cifs/nfs/smbfs/afpfs/fuse.sshfs); everything
    else under a removable-mount root is ``"removable"``.

    No external dependency: psutil would give cleaner fstype info but
    pulling it in for this one feature isn't worth the deploy weight.
    Stdlib reads of ``/proc/mounts`` (Linux) and the ``/Volumes``
    directory (macOS) cover the cases users actually hit.
    """
    out: list[tuple[Path, str, str]] = []
    if sys.platform == "darwin":
        out.extend(_discover_macos_volumes())
    elif sys.platform.startswith("linux"):
        out.extend(_discover_linux_mounts())
    elif sys.platform.startswith("win"):
        out.extend(_discover_windows_drives())
    return out


def _discover_macos_volumes() -> list[tuple[Path, str, str]]:
    volumes_root = Path("/Volumes")
    if not _is_dir_with_timeout(volumes_root):
        return []
    try:
        boot_inode = Path("/").stat().st_ino
    except OSError:
        boot_inode = None
    network_fstypes = _macos_network_volumes()
    out: list[tuple[Path, str, str]] = []
    try:
        for entry in sorted(volumes_root.iterdir()):
            # Hidden mounts (e.g. ``/Volumes/.timemachine``) are
            # platform internals the user never wants to scan; skip.
            if entry.name.startswith("."):
                continue
            try:
                # Skip the boot volume; ``/`` and ``/Volumes/Macintosh HD``
                # share an inode, so we filter the duplicate.
                if boot_inode is not None and entry.stat().st_ino == boot_inode:
                    continue
            except OSError:
                continue
            if not _is_dir_with_timeout(entry):
                continue
            kind = "network" if str(entry) in network_fstypes else "removable"
            out.append((entry, entry.name, kind))
    except OSError:
        return out
    return out


def _macos_network_volumes() -> set[str]:
    """Parse ``mount`` output to flag network-backed volumes under /Volumes.

    macOS prints lines like ``//user@host/share on /Volumes/Share (smbfs, ...)``.
    Best-effort: on parse failure return empty so everything in
    ``/Volumes`` falls through to ``"removable"``.
    """
    try:
        result = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=1.0)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if result.returncode != 0:
        return set()
    network_kinds = {"smbfs", "afpfs", "nfs", "webdav", "ftp", "fuse"}
    out: set[str] = set()
    for line in result.stdout.splitlines():
        # ``<src> on <mountpoint> (<fstype>, ...)``
        try:
            on_split = line.split(" on ", 1)
            if len(on_split) != 2:
                continue
            rest = on_split[1]
            paren = rest.find(" (")
            if paren < 0:
                continue
            mountpoint = rest[:paren]
            attrs = rest[paren + 2 :].rstrip(")")
            fstype = attrs.split(",", 1)[0].strip().lower()
            if fstype in network_kinds:
                out.add(mountpoint)
        except (ValueError, IndexError):
            continue
    return out


def _discover_linux_mounts() -> list[tuple[Path, str, str]]:
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    roots: list[Path] = []
    if user:
        roots.append(Path(f"/media/{user}"))
        roots.append(Path(f"/run/media/{user}"))
    roots.extend([Path("/media"), Path("/mnt")])

    network_fstypes = {"cifs", "nfs", "nfs4", "smbfs", "fuse.sshfs", "fuse.gvfsd-fuse"}
    network_paths = _linux_network_mounts(network_fstypes)

    out: list[tuple[Path, str, str]] = []
    for root in roots:
        if not _is_dir_with_timeout(root):
            continue
        try:
            for entry in sorted(root.iterdir()):
                if not _is_dir_with_timeout(entry):
                    continue
                kind = "network" if str(entry) in network_paths else "removable"
                out.append((entry, entry.name, kind))
        except OSError:
            continue
    return out


def _linux_network_mounts(network_fstypes: set[str]) -> set[str]:
    try:
        with Path("/proc/mounts").open(encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return set()
    out: set[str] = set()
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        mountpoint, fstype = parts[1], parts[2]
        if fstype in network_fstypes:
            out.add(mountpoint)
    return out


def _discover_windows_drives() -> list[tuple[Path, str, str]]:
    out: list[tuple[Path, str, str]] = []
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":  # skip C: (system)
        path = Path(f"{letter}:\\")
        if _is_dir_with_timeout(path):
            out.append((path, f"{letter}:", "removable"))
    return out


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
    """Boot uvicorn synchronously. Used by the ``splitsmith ui`` CLI command.

    Wraps ``uvicorn.Server`` so the first Ctrl-C prints a summary of the
    background jobs that are still running (detect_beep, trim,
    shot_detect) -- otherwise uvicorn's graceful-shutdown wait looks
    like the process hanging. Uvicorn already promotes a second Ctrl-C
    to a force-exit; we just decorate the first press with the job
    inventory + a hint about pressing again.
    """
    import uvicorn

    _ensure_ui_built()

    if reload:
        # Reload mode requires an importable factory; pass the path string and
        # use environment variables to feed the project context. Simpler: just
        # log a warning and run without reload for now. Reload is a dev
        # convenience that we can wire properly when we have a real config.
        logger.warning("reload=True is not supported yet; running without reload")

    app = create_app(project_root=project_root, project_name=project_name)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = _JobAwareServer(config, app)
    server.run()


class _JobAwareServer:
    """Lazy wrapper that builds a ``uvicorn.Server`` subclass on demand.

    The subclass override has to live next to the import to avoid
    pulling uvicorn into module import; we lift it via a closure inside
    :meth:`run` so unit tests don't pay the import cost.
    """

    def __init__(self, config: Any, app: FastAPI) -> None:
        self._config = config
        self._app = app

    def run(self) -> None:
        import uvicorn

        app = self._app

        class _Inner(uvicorn.Server):
            def handle_exit(self, sig: int, frame: Any) -> None:  # type: ignore[override]
                # First press: announce active jobs so the wait is
                # visible; let uvicorn flip should_exit and shut down
                # gracefully. Second press (uvicorn already detects
                # repeat SIGINT) escalates to force_exit, which kills
                # in-flight requests + the job thread pool.
                if not self.should_exit:
                    _print_active_jobs(app)
                uvicorn.Server.handle_exit(self, sig, frame)

        server = _Inner(self._config)
        server.run()


def _print_active_jobs(app: FastAPI) -> None:
    """Dump pending / running jobs to stderr on first Ctrl-C."""
    state = getattr(app.state, "splitsmith_state", None)
    if state is None:
        return
    try:
        jobs = state.jobs.list()
    except Exception:  # pragma: no cover -- defensive: never block shutdown
        logger.warning("could not enumerate jobs on shutdown", exc_info=True)
        return
    from .jobs import JobStatus

    active = [j for j in jobs if j.status in (JobStatus.PENDING, JobStatus.RUNNING)]
    print("", file=sys.stderr, flush=True)
    if not active:
        print("Shutting down (no background jobs running).", file=sys.stderr, flush=True)
        return
    print(
        f"Shutting down -- waiting for {len(active)} background job"
        f"{'' if len(active) == 1 else 's'}:",
        file=sys.stderr,
        flush=True,
    )
    for j in active:
        bits = [j.kind]
        if j.stage_number is not None:
            bits.append(f"stage {j.stage_number}")
        msg = j.message or j.status.value
        bits.append(msg)
        if j.progress is not None:
            bits.append(f"{round(j.progress * 100)}%")
        print(f"  - {' / '.join(bits)}", file=sys.stderr, flush=True)
    print(
        "Press Ctrl-C again to force quit (in-flight ffmpeg / detection " "will be killed).",
        file=sys.stderr,
        flush=True,
    )
