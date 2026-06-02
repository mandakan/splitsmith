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
  GET  /api/videos/link-status      -- per-video raw/<name> symlink state (ok/broken/...)
  POST /api/videos/relink/scan      -- recursive dry-run: candidates per video
  POST /api/videos/relink/apply     -- apply chosen symlink rewrites
  GET  /api/project/cleanup/plan    -- preview disk-cleanup plan
  POST /api/project/cleanup         -- apply disk-cleanup (refuses while jobs run)
  POST /api/assignments/move        -- set role / unassign / move between stages
  POST /api/project/settings        -- update raw/audio/trimmed/exports dir overrides
  GET  /api/fs/probe?path=...       -- probe + thumbnail one source file on demand
  GET  /api/thumbnails/{key}.jpg    -- serve cached thumbnail
  POST /api/stages/{n}/detect-beep  -- submit a beep-detection job (runs in pool)
  POST /api/stages/{n}/beep         -- manual beep_time override (synchronous)
  POST /api/stages/{n}/beep/select  -- promote one ranked candidate (synchronous)
  POST /api/stages/{n}/time         -- manual stage duration (no-scoreboard path)
  POST /api/stages/{n}/trim         -- submit an audit-mode trim job
  POST /api/stages/{n}/shot-detect  -- submit shot detection on the audit clip
  POST /api/stages/shot-detect      -- bulk shot detection on every eligible stage
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
  GET  /api/me                      -- current operator (LoopbackAuth user in local mode)
  GET  /api/user/recent-projects    -- recently-opened MatchProject roots (#75)
  POST /api/user/recent-projects/forget -- drop one entry from the list
  POST /api/user/recent-projects/bind   -- switch the in-memory project
  POST /api/user/recent-projects/unbind -- drop the bound project (back to picker)
  GET  /api/user/scoreboard-identity -- saved SSI identity (404 if none)
  PUT  /api/user/scoreboard-identity -- write the SSI identity

Design notes:
- Localhost only. No auth, no CORS configuration beyond what Vite needs in dev.
- The server holds at most one ``MatchProject`` open at a time. The
  binding can be set at startup (``--project``) or chosen at runtime via
  the SPA picker (POST /api/user/recent-projects/bind). When unbound,
  every project-bound endpoint returns 409 ``no_project`` and the SPA
  redirects to its picker route. Multi-project orchestration lives in
  the SPA.
- All on-disk mutations go through the project model's atomic save.
- The server re-loads the project from disk for every request (no caching), so
  external edits are visible without restart.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Literal

if TYPE_CHECKING:
    # Hosted-only; imported lazily at runtime inside _apply_hosted_mode_wiring
    # so local mode stays free of the db (procrastinate/psycopg) dependency.
    from ..db import PostgresMatchStore, ProjectStateStore

from fastapi import (
    Body,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

from .. import __version__ as splitsmith_version
from .. import automation as automation_settings
from .. import backup as backup_mod
from .. import beep_detect, cross_align, match_model, report, user_config, video_probe
from .. import cleanup as cleanup_module
from .. import coach as coach_module
from .. import coach_distributions as coach_distributions_module
from .. import ensemble as ensemble_module
from .. import models as model_layer
from .. import shot_detect as shot_detect_module  # noqa: F401  (kept for legacy monkeypatch points)
from .. import thumbnail as thumbnail_helpers
from .. import waveform as waveform_helpers
from ..async_bridge import run_sync
from ..auth import AuthBackend, LoopbackAuth, User
from ..compute import ComputeBackend, LocalComputeBackend
from ..config import (
    BeepDetectConfig,
    CoachAutoClassifyConfig,
    Config,
    IntervalClass,
    IntervalClassSource,
    StageRounds,
)
from ..fixture_schema import (
    AgcState,
    AudioSource,
    Camera,
    CameraMount,
    CameraPosition,
    probe_camera_metadata,
)
from ..match_registry import MatchRegistry
from ..observability import StructuredJsonFormatter, init_sentry
from ..runtime import runtime as process_runtime
from ..storage import Storage
from . import audio as audio_helpers
from . import export_storage
from . import exports as export_helpers
from . import match_exports as match_export_helpers
from .jobs import (
    Job,
    JobBackend,
    JobBodyRegistry,
    JobCancelled,
    JobHandle,
    JobRegistry,
    ShutdownInProgressError,
)
from .project import (
    VIDEO_EXTENSIONS,
    MatchProject,
    RawVideo,
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


def _configure_app_logging(stream: Any | None = None) -> None:
    """Route ``splitsmith.*`` INFO logs to stdout.

    Without this the app's own log lines are invisible in a deployed
    container: ``uvicorn.Config(log_level="info")`` configures only
    uvicorn's loggers, never the root logger, so ``splitsmith.*`` records
    propagate to a root logger left at its WARNING default and INFO is
    dropped. The most visible casualty is the console e-mail backend's
    ``MAGIC_LINK <to> <link>`` line (``splitsmith.db.email``) -- the whole
    point of that transport is that the operator can read the sign-in link
    from the logs, which silently never happened in hosted deploys.

    Attaches one stdout ``StreamHandler`` to the ``splitsmith`` package
    logger and raises that logger's level to INFO so its records reach the
    handler. Propagation is left ON (the default): severing it would cut
    ``splitsmith.*`` records off from the root logger, breaking pytest's
    ``caplog`` and the file-logging sidecar (which both attach at root).
    In a deployed serve process root carries no stdout handler, so there is
    no double-emit; uvicorn's own loggers (propagate off) are untouched and
    keep emitting access logs. Idempotent.
    """
    pkg_logger = logging.getLogger("splitsmith")
    if any(getattr(h, "_splitsmith_stdout", False) for h in pkg_logger.handlers):
        return
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    if _hosted_mode_active():
        # Hosted stdout is captured by the platform as structured logs: emit
        # one JSON line per record (folding in the job.completed/job.failed
        # observability extras). Local file logging stays plain text.
        handler.setFormatter(StructuredJsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
    handler._splitsmith_stdout = True  # type: ignore[attr-defined]  # idempotence marker
    pkg_logger.addHandler(handler)
    if pkg_logger.level == logging.NOTSET or pkg_logger.level > logging.INFO:
        pkg_logger.setLevel(logging.INFO)


def _state_conflict_excs() -> tuple[type[BaseException], ...]:
    """Resolve the optimistic-lock conflict type for the audit re-merge
    retry, lazily so ``import splitsmith.ui.server`` never pulls the hosted
    db deps (local-mode invariant; guarded by
    ``test_local_mode_no_hosted_imports``). Empty tuple on a slim local
    install without the db extras -- ``except ():`` then catches nothing,
    which is correct: local file saves never raise a conflict."""
    try:
        from ..db import StateConflictError

        return (StateConflictError,)
    except Exception:  # pragma: no cover - slim local install without db extras
        return ()


# How many times the worker re-loads + re-merges a stage's audit doc when a
# concurrent writer wins the optimistic-lock race before its save. Small:
# real contention is a manual SPA edit landing while shot_detect runs, not a
# thundering herd. Exhausting it re-raises -> the job fails loudly.
_AUDIT_SAVE_MAX_ATTEMPTS = 4

# Chunk size the multipart-upload client splits a file into. 16 MiB is
# comfortably above S3/R2's 5 MiB non-final-part minimum and keeps the part
# count modest (a 2 GB file = 128 parts), so per-part presign round-trips
# stay cheap.
_RAW_UPLOAD_PART_SIZE = 16 * 1024 * 1024

# The engine's ``StageData`` requires a non-None ``scorecard_updated_at``
# (the video-matching heuristic keys off it), but manually-timed stages on
# scoreboard-less matches have none. Export never reads the field, so feed
# this sentinel rather than inventing a real-looking time -- same approach
# as ``splitsmith.mcp.export_tools``.
_PLACEHOLDER_SCORECARD_TIME = datetime(2000, 1, 1, tzinfo=UTC)


def _save_audit_with_remerge(
    state: AppState,
    slug: str,
    stage_number: int,
    *,
    doc: dict,
    version: int,
    merge: Callable[[dict], dict],
    default: Callable[[], dict],
) -> int:
    """Save an audit doc under optimistic locking with a bounded re-merge.

    ``merge`` folds the caller's results into a doc; the first attempt
    applies it to ``doc`` (loaded at ``version``). If the hosted save loses
    the version race (a concurrent writer bumped it), re-load the winner's
    doc and re-apply ``merge`` to *that* -- so the concurrent edit survives
    instead of being clobbered -- then save again. ``default`` rebuilds a
    fresh doc if the row vanished between attempts. Returns the new version.
    Local file saves never raise, so the loop runs exactly once there.
    """
    conflict_excs = _state_conflict_excs()
    for attempt in range(_AUDIT_SAVE_MAX_ATTEMPTS):
        try:
            return state.save_audit(slug, stage_number, merge(doc), version=version)
        except conflict_excs:
            if attempt + 1 >= _AUDIT_SAVE_MAX_ATTEMPTS:
                raise  # bounded retries exhausted -> fail the job loudly
            reloaded, version = state.load_audit(slug, stage_number)
            doc = reloaded if reloaded is not None else default()
    raise AssertionError("unreachable: loop returns or raises")  # pragma: no cover


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
        logger.warning("ui_static/dist appears stale but npm is not on PATH; serving whatever is in dist/")
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


def _load_env_files(project_root: Path | None = None) -> list[Path]:
    """Pick up ``.env.local`` then ``.env`` from the project root, the cwd
    the user launched the server from, and the global user-config dir.

    ``project_root`` is optional so the unbound boot path (picker /
    create-match flow) can still pull ``SPLITSMITH_SSI_TOKEN`` from
    ``<cwd>/.env.local`` or ``<user_config_dir>/.env.local`` -- the
    scoreboard search endpoint needs it before any project exists.

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
    bases: list[Path] = []
    if project_root is not None:
        bases.append(project_root.resolve())
    bases.append(Path.cwd().resolve())
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


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


class AuthBeginRequest(BaseModel):
    """Body of ``POST /api/v1/auth/begin`` -- the email to send a magic
    link to. Module-level (not nested in ``create_app``) so FastAPI's
    type-hint resolution recognises it as a request body under
    ``from __future__ import annotations``."""

    email: str


# /api/* paths the auth gate lets through without resolving a user.
# Anything else under /api/* requires ``state.auth.authenticate_request``
# to return a non-None User -- see the ``_auth_gate`` middleware inside
# ``create_app``. Non-/api/* paths (SPA static, /docs) are exempt by
# prefix, not by this list.
_PUBLIC_API_PATHS: frozenset[str] = frozenset(
    {
        "/api/health",
        "/api/server/features",
        "/api/shutdown",
        # Magic-link sign-in start: must be reachable before a user exists.
        # (The ``/auth/callback`` redemption is not under /api/*, so the
        # auth gate skips it by prefix.)
        "/api/v1/auth/begin",
    }
)


def _is_loopback(request: Request) -> bool:
    """Reject /api/shutdown from non-loopback callers.

    ``testclient`` is included so the TestClient ASGI shim works in
    pytest without a real socket; it's a value set by the server side
    (the ASGI scope) so a remote caller can't spoof it.
    """
    client = request.client
    if client is None:
        return False
    return client.host in _LOOPBACK_HOSTS


def _no_project_error() -> HTTPException:
    """Raised by :class:`AppState` when an endpoint that needs a bound
    project is hit while the server is in unbound (picker) mode.

    The SPA inspects ``detail.code == "no_project"`` to redirect to the
    picker route instead of rendering a generic error.
    """
    return HTTPException(
        status_code=409,
        detail={
            "code": "no_project",
            "message": (
                "No project is currently open. Pick one from the project "
                "list (POST /api/user/recent-projects/bind) or restart "
                "with `splitsmith ui --project <path>`."
            ),
        },
    )


# Per-request match root resolved by the ``/api/matches/{match_id}/...``
# alias middleware (#353 Phase 3 PR C). When set, ``AppState.shooter_root``
# reads from here instead of the process singleton -- which means two
# concurrent requests carrying different match ids resolve to their own
# match roots without fighting over ``state._bound_root``. The singleton
# is kept as a fallback for legacy bare-path traffic + picker UX; it can
# go away entirely once every endpoint is exercised under the new prefix.
current_match_root: ContextVar[Path | None] = ContextVar("splitsmith_current_match_root", default=None)
current_match_id: ContextVar[str | None] = ContextVar("splitsmith_current_match_id", default=None)


@dataclass
class TenantContext:
    """The per-user stores resolved for the lifetime of one request / job.

    In hosted mode every ``/api/*`` request and every worker job runs as a
    distinct user, so the per-user stores cannot be process singletons --
    a singleton bound to user A would serve user B's request with A's
    Row-Level-Security GUC. The hosted wiring builds one of these per
    request (in the auth-gate middleware, from the resolved ``User``) and
    per job (in the queue task, from the queued ``user_id``); the matching
    :class:`AppState` properties read it via the :data:`current_tenant`
    ContextVar. In local mode there is exactly one operator, so this is
    never populated and the properties fall back to the process singletons.
    """

    user_id: str
    recent_projects: user_config.RecentProjectsStore
    scoreboard_identity: user_config.ScoreboardIdentityStore
    jobs: JobBackend
    matches_store: PostgresMatchStore | None
    project_state: ProjectStateStore | None
    storage: Storage | None


# Per-request / per-job tenant resolved by the hosted-mode auth gate
# (API) and the ``run_compute_job`` queue task (worker). When set, the
# per-user ``AppState`` properties (``jobs``, ``recent_projects``,
# ``scoreboard_identity``, ``matches_store``, ``storage``) read from it
# instead of the local-mode singleton -- the same pattern
# ``current_match_root`` uses for match scoping. ``None`` in local mode
# and on boot/non-request code paths.
current_tenant: ContextVar[TenantContext | None] = ContextVar("splitsmith_current_tenant", default=None)


@dataclass
class AppState:
    """Process state. One bound Match folder at a time.

    The bound project is always a Match folder; the legacy single-
    shooter layout was retired in Tier 1 step 3 of doc 10.

    - Match-level operations (list shooters, merge, export the whole
      match) read :attr:`match_root`, which resolves from the per-
      request ``current_match_root`` ContextVar set by the
      ``/api/matches/{match_id}/`` alias middleware.
    - Shooter-scoped operations require a slug from the URL path and
      resolve via :meth:`shooter_root` / :meth:`shooter_project`,
      which also read the same ContextVar.

    There is no notion of an "active" or "current" shooter on the
    server. Every shooter-scoped request must carry the slug in its
    path; the SPA's URL is the single source of truth.

    There is no notion of a "bound" project on the server -- the URL
    prefix ``/api/matches/{match_id}/`` carries match identity per
    request, and the alias middleware resolves it via
    :class:`MatchRegistry`. ``splitsmith ui --project <path>`` and
    the SPA picker register matches in :attr:`matches` and emit a
    URL the browser navigates to; the server itself never has a
    "current open project" state.
    """

    # The ``JobBackend`` Protocol (see ``splitsmith.ui.jobs``) is
    # what handlers depend on; ``JobRegistry`` is the local in-memory
    # implementation. In hosted mode the per-request / per-job tenant
    # backend resolves through the ``jobs`` property below; this backing
    # slot stays the local ``JobRegistry`` and is consulted only when no
    # tenant is pinned (boot-time body registration), never for queries.
    _jobs: JobBackend = field(default_factory=JobRegistry)
    # Process-level ``kind`` -> body registry, shared by the local backend
    # and every per-request hosted backend. ``register_job_bodies`` (and the
    # dev routes) register here via ``state.jobs.bodies``; ``__post_init__``
    # points the backing ``_jobs`` at it, and ``_apply_hosted_mode_wiring``
    # injects it into each tenant backend. Decoupling the map from any one
    # backend is what lets hosted mode build backends per request without
    # re-registering bodies -- and what lets the loopback boot user go away.
    job_bodies: JobBodyRegistry = field(default_factory=JobBodyRegistry)
    matches: MatchRegistry = field(default_factory=MatchRegistry)
    # Per-user ``matches`` table store (PR-delta). ``None`` in local mode;
    # in hosted mode resolved per request / per job via the property below.
    # See ``splitsmith.db.matches.PostgresMatchStore``.
    _matches_store: PostgresMatchStore | None = None
    # Per-user ``state_docs`` store (state refactor). Holds the match /
    # per-shooter project / per-stage audit JSON docs in Postgres with
    # optimistic locking. ``None`` in local mode (state stays file-based);
    # in hosted mode resolved per request / per job via the property below.
    # See ``splitsmith.db.project_state.ProjectStateStore``.
    _project_state: ProjectStateStore | None = None
    # Per-user preference stores (Tier 3 of doc 10). Local mode
    # delegates to the ``~/.splitsmith/*.json`` module functions;
    # in hosted mode resolved per request from the authenticated user
    # via the properties below. See ``splitsmith.user_config``.
    _recent_projects: user_config.RecentProjectsStore = field(
        default_factory=user_config.JsonRecentProjectsStore
    )
    _scoreboard_identity: user_config.ScoreboardIdentityStore = field(
        default_factory=user_config.JsonScoreboardIdentityStore
    )
    # Hosted-mode factory: build a :class:`TenantContext` for a ``user_id``.
    # ``None`` in local mode. Set by ``_apply_hosted_mode_wiring``; called
    # per request (auth gate) and per job (queue task).
    _build_tenant: Callable[[str], TenantContext] | None = None
    # Auth backend. ``LoopbackAuth`` in local mode (every request
    # resolves to the same sentinel user); a hosted-mode backend will
    # be injected here when SaaS lands. See ``splitsmith.auth``.
    auth: AuthBackend = field(default_factory=LoopbackAuth)
    # Compute backend. ``LocalComputeBackend`` runs the shot-detection
    # ensemble in this process; a hosted ``RemoteComputeBackend`` will
    # ship audio to a cloud worker. See ``splitsmith.compute`` and
    # ``docs/saas-readiness/04-compute-backends.md``. The default uses
    # ``load_ensemble_runtime`` directly; ``create_app`` overrides this
    # with one wired to the local ``_get_ensemble_runtime`` so test
    # monkeypatches and the shared model cache still work.
    compute: ComputeBackend = field(default_factory=LocalComputeBackend)
    # Tenant-scoped object storage. ``None`` in local mode -- the
    # desktop codepaths read/write the user's chosen project folder
    # directly via ``pathlib.Path`` and never consult this slot. In
    # hosted mode (``SPLITSMITH_MODE=hosted``) ``_apply_hosted_mode_wiring``
    # constructs a per-user :class:`S3Storage` scoped to
    # ``users/<user_id>/`` so the upload endpoints can stream raw
    # footage into R2 / MinIO without ever touching the API
    # container's filesystem. Resolved per request / per job via the
    # property below. See ``docs/saas-readiness/03-storage-layer.md``.
    _storage: Storage | None = None
    # Stop callback registered by :func:`splitsmith.ui.embedded.run_embedded`
    # so the /api/shutdown route can ask uvicorn to exit. None under the
    # ``splitsmith ui`` CLI path -- Ctrl-C via _JobAwareServer.handle_exit
    # is the stop mechanism there.
    shutdown_handler: Callable[[], None] | None = None
    # Idempotency latch for /api/shutdown: first call kicks the drain
    # thread, subsequent calls return 202 without rescheduling.
    shutdown_initiated: bool = False

    # Hosted-mode auth config, set by ``_apply_hosted_mode_wiring`` from
    # ``SPLITSMITH_PUBLIC_URL``. ``public_base_url`` is the origin the
    # magic-link callback lives under; ``cookie_secure`` is True iff that
    # origin is https (so the session cookie's Secure flag round-trips
    # over docker-compose http but is set in production). Both stay at
    # their defaults in local mode (no auth, no cookies).
    public_base_url: str | None = None
    cookie_secure: bool = False

    def __post_init__(self) -> None:
        # Point the backing local backend's body map at the shared
        # process-level registry so ``state.jobs.bodies.register(...)`` (used
        # by ``register_job_bodies`` + the dev routes, all at boot with no
        # tenant pinned) lands on the same registry the hosted per-tenant
        # backends read from. In local mode this is also the registry the
        # JobRegistry executes against.
        self._jobs.bodies = self.job_bodies

    # ------------------------------------------------------------------
    # Per-user stores -- tenant-resolving in hosted mode, singletons local
    # ------------------------------------------------------------------
    #
    # Each getter returns the request/job's :class:`TenantContext` store
    # when ``current_tenant`` is set (hosted mode), else the process
    # singleton in the backing field (local mode + boot/non-request code).
    # The setters write the backing field so tests + the local path can
    # inject fakes; hosted requests never assign these (they set
    # ``current_tenant`` instead). This mirrors ``shooter_root`` reading
    # ``current_match_root``.

    @property
    def jobs(self) -> JobBackend:
        tenant = current_tenant.get()
        return tenant.jobs if tenant is not None else self._jobs

    @jobs.setter
    def jobs(self, value: JobBackend) -> None:
        self._jobs = value

    @property
    def recent_projects(self) -> user_config.RecentProjectsStore:
        tenant = current_tenant.get()
        return tenant.recent_projects if tenant is not None else self._recent_projects

    @recent_projects.setter
    def recent_projects(self, value: user_config.RecentProjectsStore) -> None:
        self._recent_projects = value

    @property
    def scoreboard_identity(self) -> user_config.ScoreboardIdentityStore:
        tenant = current_tenant.get()
        return tenant.scoreboard_identity if tenant is not None else self._scoreboard_identity

    @scoreboard_identity.setter
    def scoreboard_identity(self, value: user_config.ScoreboardIdentityStore) -> None:
        self._scoreboard_identity = value

    @property
    def matches_store(self) -> PostgresMatchStore | None:
        tenant = current_tenant.get()
        return tenant.matches_store if tenant is not None else self._matches_store

    @matches_store.setter
    def matches_store(self, value: PostgresMatchStore | None) -> None:
        self._matches_store = value

    @property
    def project_state(self) -> ProjectStateStore | None:
        tenant = current_tenant.get()
        return tenant.project_state if tenant is not None else self._project_state

    @project_state.setter
    def project_state(self, value: ProjectStateStore | None) -> None:
        self._project_state = value

    @property
    def storage(self) -> Storage | None:
        tenant = current_tenant.get()
        return tenant.storage if tenant is not None else self._storage

    @storage.setter
    def storage(self, value: Storage | None) -> None:
        self._storage = value

    def build_tenant(self, user_id: str) -> TenantContext:
        """Build the :class:`TenantContext` for ``user_id`` (hosted mode).

        Raises if called in local mode -- there is no per-user tenancy
        there, and a caller reaching this in local mode is a bug. The
        hosted wiring sets :attr:`_build_tenant`.
        """
        if self._build_tenant is None:
            raise RuntimeError(
                "AppState.build_tenant called without a hosted-mode tenant "
                "factory; this is only valid when SPLITSMITH_MODE=hosted "
                "wiring has run."
            )
        return self._build_tenant(user_id)

    @property
    def match_root(self) -> Path:
        """The match folder resolved from the request URL's
        ``/api/matches/{match_id}/`` prefix.

        Tier 1 of the singleton-elimination work (doc 10): match-level
        operations are addressable only by URL, never via a process-
        level bind. The alias middleware sets ``current_match_root``
        after validating ``match_id``; this accessor reads only from
        there. A bare-path request (no prefix) gets 409 ``no_project``.
        Legacy single-shooter projects don't have a ``match_id`` and
        are unreachable via match-level routes by construction.
        """
        scoped = current_match_root.get()
        if scoped is None:
            raise _no_project_error()
        return scoped

    def shooter_root(self, slug: str) -> Path:
        """Resolve ``slug`` to the shooter's project directory on disk.

        The path is always ``<match_root>/shooters/<slug>``. The
        match root comes from the per-request ``current_match_root``
        ContextVar set by the ``/api/matches/{match_id}/`` alias
        middleware -- there is no process-level singleton fallback,
        and there is no legacy single-shooter layout to support
        (Tier 1 step 3 of doc 10 retired both). Bare-path requests
        without the URL prefix get 409 ``no_project``.
        """
        scoped_root = current_match_root.get()
        if scoped_root is None:
            raise _no_project_error()
        # Roster / ownership check. Hosted: the match doc lives in Postgres
        # (state_docs), so read the shooter list from there -- a worker or a
        # post-redeploy replica has no match.json on disk. Local: load the
        # on-disk match.json. Either way the returned value is the on-disk
        # path, which is where *media* (S3-mirrored) lives for ffmpeg.
        mid = current_match_id.get()
        store = self.project_state
        if store is not None and mid is not None:
            doc, _ = run_sync(store.load_match(mid))
            if doc is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"match {mid!r} has no state document",
                )
            shooters = doc.get("shooters", [])
        else:
            shooters = match_model.Match.load(scoped_root).shooters
        if slug not in shooters:
            raise HTTPException(
                status_code=404,
                detail=f"shooter {slug!r} is not registered on this match",
            )
        return match_model.Match.shooter_root(scoped_root, slug)

    def _audit_file(self, slug: str, stage_number: int) -> Path:
        """The on-disk path for a stage's audit JSON. Used only by the
        *local-mode* branches of ``load_audit`` / ``save_audit`` /
        ``materialize_audit`` -- hosted mode keeps audit docs in
        ``state_docs`` and never touches this file."""
        scoped_root = current_match_root.get()
        if scoped_root is None:
            raise _no_project_error()
        return match_model.Match.shooter_root(scoped_root, slug) / "audit" / f"stage{stage_number}.json"

    # ------------------------------------------------------------------
    # State-doc accessors (state refactor)
    # ------------------------------------------------------------------

    def match(self) -> match_model.Match:
        """Load the match for the current ``/api/matches/{match_id}/`` scope.

        Hosted: from the ``state_docs`` table, with the state store bound so
        a subsequent ``.save()`` round-trips back under optimistic locking
        (carrying the loaded version). Local: from the on-disk ``match.json``
        (``Match.load`` may self-assign + save a pre-v4 ``match_id`` -- that
        path is file-only and never reached for a store-backed match, whose
        docs are born with an id).
        """
        scoped_root = current_match_root.get()
        if scoped_root is None:
            raise _no_project_error()
        mid = current_match_id.get()
        store = self.project_state
        if store is not None and mid is not None:
            doc, version = run_sync(store.load_match(mid))
            if doc is None:
                raise HTTPException(status_code=404, detail=f"match {mid!r} has no state document")
            match = match_model.Match.model_validate(doc)
            match.bind_state(store, match_id=mid, version=version)
            return match
        return match_model.Match.load(scoped_root)

    def load_audit(self, slug: str, stage_number: int) -> tuple[dict | None, int]:
        """Load a stage's audit doc + its version. ``(None, 0)`` when none
        exists yet. Hosted: from ``state_docs``. Local: from the on-disk
        ``audit/stage<N>.json`` (version always 0 -- no locking on files)."""
        mid = current_match_id.get()
        store = self.project_state
        if store is not None and mid is not None:
            return run_sync(store.load_audit(mid, slug, stage_number))
        audit_file = self._audit_file(slug, stage_number)
        if not audit_file.exists():
            return None, 0
        try:
            return json.loads(audit_file.read_text(encoding="utf-8")), 0
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=500, detail=f"audit read failed: {exc}") from exc

    def save_audit(self, slug: str, stage_number: int, doc: dict, *, version: int) -> int:
        """Persist a stage's audit doc; return the new version.

        Hosted: ``state_docs`` under optimistic locking -- ``version==0``
        INSERTs, ``>0`` UPDATEs at that version, a stale version raises
        ``StateConflictError`` (-> 409). Local: atomic ``.tmp`` -> ``.bak``
        rotate -> rename file write (returns 0)."""
        mid = current_match_id.get()
        store = self.project_state
        if store is not None and mid is not None:
            return run_sync(store.save_audit(mid, slug, stage_number, doc, expected_version=version))
        audit_file = self._audit_file(slug, stage_number)
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = audit_file.with_suffix(audit_file.suffix + ".tmp")
        backup = audit_file.with_suffix(audit_file.suffix + ".bak")
        try:
            tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            if audit_file.exists():
                if backup.exists():
                    backup.unlink()
                audit_file.replace(backup)
            tmp.replace(audit_file)
        except OSError as exc:
            if tmp.exists():
                tmp.unlink()
            raise HTTPException(status_code=500, detail=f"audit write failed: {exc}") from exc
        return 0

    def materialize_audit(self, slug: str, stage_number: int) -> Path:
        """Ensure the stage's audit doc is present at its on-disk path and
        return that path.

        For file-path consumers that hand the audit *path* to downstream
        readers (the FCPXML/report exporters, the lab). Hosted: the doc
        lives in ``state_docs``, so write it to the local file first.
        Local: the file is already on disk, return its path. When no audit
        doc exists the returned path simply won't exist -- the caller's
        existing "missing audit" handling fires, same as before."""
        audit_file = self._audit_file(slug, stage_number)
        if self.project_state is not None:
            doc, _ = self.load_audit(slug, stage_number)
            if doc is not None:
                audit_file.parent.mkdir(parents=True, exist_ok=True)
                audit_file.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        return audit_file

    def shooter_project(self, slug: str) -> MatchProject:
        """Load the per-shooter ``MatchProject`` (each shooter slot
        inside a Match folder owns its own ``project.json``).

        Binds :attr:`storage` onto the project so
        :meth:`MatchProject.resolve_video_path` can mirror hosted-mode
        raw videos into the local cache on first access. The bound
        ``scope`` is ``matches/<match_id>/shooters/<slug>`` -- used as
        the prefix for derived-artifact caches (audio WAVs today,
        trim outputs later) so two shooters in different matches
        can't collide on the same ``video_id``. Local mode leaves
        ``storage`` at ``None``; the bind is a no-op and the legacy
        disk-only resolvers win.
        """
        shooter_root = self.shooter_root(slug)
        match_id = current_match_id.get()
        scope = f"matches/{match_id}/shooters/{slug}" if match_id is not None else None
        store = self.project_state
        if store is not None and match_id is not None:
            # Hosted: the project doc lives in Postgres. Load it, bind the
            # store so save() round-trips back under optimistic locking
            # (carrying the version we just read), and bind storage for
            # media mirroring. No project.json on disk is involved.
            doc, version = run_sync(store.load_project(match_id, slug))
            if doc is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"shooter {slug!r} has no project document",
                )
            project = MatchProject.model_validate(doc)
            project.bind_state(store, match_id=match_id, slug=slug, version=version)
        else:
            project = MatchProject.load(shooter_root)
        project.bind_storage(self.storage, scope=scope)
        return project


async def _any_active_job(state: AppState) -> Job | None:
    """Return the first PENDING/RUNNING job, or None.

    Used by destructive endpoints (cleanup) to refuse running while
    workers are mid-flight. ``find_active`` on the registry is
    kind-scoped; this helper scans every kind so a beep job blocks a
    cleanup just as a trim does.
    """
    from .jobs import JobStatus

    for job in await state.jobs.list():
        if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
            return job
    return None


# Module-level cache for the 4-voter ensemble runtime (issue #31). Heavy
# (CLAP ~600 MB, PANN ~80 MB), so it is loaded on the first shot-detect
# call and reused for subsequent calls. The threading lock guards against
# two shot-detect jobs colliding on first init when the model weights
# haven't downloaded yet.
_ENSEMBLE_RUNTIME: ensemble_module.EnsembleRuntime | None = None
_ENSEMBLE_RUNTIME_LOCK = threading.Lock()


def _trim_wav_to_clip(src_wav: Path, dst_wav: Path, start_s: float, end_s: float) -> None:
    """Cut ``src_wav`` to ``[start_s, end_s)`` and write to ``dst_wav``.

    Used by promote-secondary so the derived fixture's audio matches the
    "fixture is clip-local" convention shared with primary fixtures.
    Re-encodes (PCM s16 / mono / 48 kHz) so the output is a stable WAV
    regardless of the source codec.
    """
    ffmpeg_bin = process_runtime().ffmpeg_binary
    if not shutil.which(ffmpeg_bin):
        raise RuntimeError(f"ffmpeg binary not found: {ffmpeg_bin}")
    duration = max(0.0, end_s - start_s)
    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start_s:.3f}",
        "-i",
        str(src_wav),
        "-t",
        f"{duration:.3f}",
        "-ac",
        "1",
        "-ar",
        "48000",
        "-c:a",
        "pcm_s16le",
        str(dst_wav),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"ffmpeg trim failed (exit {exc.returncode}): {exc.stderr or exc.stdout!r}"
        ) from exc


def _get_ensemble_runtime() -> ensemble_module.EnsembleRuntime:
    """Lazy-load + cache the ensemble runtime; thread-safe.

    Test code monkeypatches this function (and
    ``ensemble_module.detect_shots_ensemble``) to avoid pulling the heavy
    model weights into the test process.

    Voter E (issue #183) adds a CLIP image-encoder load on top of the
    existing CLAP/PANN/GBDT models. Skip it unless the operator has
    opted in via ``SPLITSMITH_ENABLE_VOTER_E=1`` so the default install
    doesn't pay ~600 MB of memory and the first-call download.
    """
    global _ENSEMBLE_RUNTIME
    if _ENSEMBLE_RUNTIME is None:
        with _ENSEMBLE_RUNTIME_LOCK:
            if _ENSEMBLE_RUNTIME is None:
                with_voter_e = os.environ.get("SPLITSMITH_ENABLE_VOTER_E") == "1"
                _ENSEMBLE_RUNTIME = ensemble_module.load_ensemble_runtime(with_voter_e=with_voter_e)
    return _ENSEMBLE_RUNTIME


def _ensemble_runtime_is_warm() -> bool:
    """``True`` when the ensemble singleton is already loaded in this process.

    Cheap, lock-free read of the module global. Used by ``_run_shot_detect``
    to decide whether the upcoming model resolution pays a cold load (timed
    as the ``cold_model_load`` phase) or hits the warm cache. The worker's
    boot-time warmup populates the singleton so the steady-state path reports
    warm.
    """
    return _ENSEMBLE_RUNTIME is not None


def warm_ensemble_runtime() -> None:
    """Force the ensemble singleton to load now (worker-boot warmup).

    Thin public wrapper over :func:`_get_ensemble_runtime` so the worker
    entrypoint (``splitsmith.queue.run_worker``) can populate
    ``_ENSEMBLE_RUNTIME`` before the first ``shot_detect`` job runs, keeping
    the underscore-private loader internal. Returns nothing -- callers warm
    the cache for its side effect, not its value. The model load happens on
    a worker thread (``asyncio.to_thread``) so it never blocks the event
    loop; a failure here is non-fatal and is re-attempted (and timed as the
    ``cold_model_load`` phase) on the first shot-detect job.
    """
    _get_ensemble_runtime()


def _run_model_download_job(handle: JobHandle) -> None:
    """Prefetch every missing slim ONNX artifact from R2.

    Submitted by :func:`_maybe_submit_model_download` at server startup
    when ``registry.status()`` reports anything other than ``present``
    so the audit hot path doesn't pay a ~440 MB stall on the user's
    first shot-detect click.

    Progress feeds the job snapshot as a single 0..1 fraction across the
    sum of pending-artifact sizes; the SPA's Jobs panel renders it from
    there. The download itself is locked via the registry's cache lock,
    so a concurrent ``registry.resolve()`` from an early shot-detect
    will block on this job rather than racing it.
    """
    registry = model_layer.get_default_registry()
    if registry is None:
        return
    pending = [s for s in registry.status() if s.state != "present"]
    if not pending:
        return
    total_bytes = sum(s.size_bytes for s in pending)
    completed_bytes = 0
    for i, status in enumerate(pending, start=1):
        handle.check_cancel()
        slug_bytes = status.size_bytes
        slug_label = f"({i}/{len(pending)}) {status.slug}"
        handle.update(
            progress=completed_bytes / total_bytes if total_bytes else None,
            message=f"Downloading {slug_label}",
        )

        def _progress(
            seen: int,
            _total: int | None,
            *,
            base: int = completed_bytes,
            cap: int = slug_bytes,
        ) -> None:
            # Fires once per ~1 MB chunk (see models.download). Checking
            # cancel here lets an in-flight artifact abort within a chunk
            # instead of only at artifact boundaries -- the difference
            # between a sub-second shutdown and waiting out a ~150 MB
            # download. The prefetch is resumable on next boot, so
            # abandoning it mid-stream loses nothing.
            handle.check_cancel()
            if total_bytes:
                handle.update(progress=(base + min(seen, cap)) / total_bytes)

        try:
            registry.resolve(status.slug, progress=_progress)
        except model_layer.NetworkUnreachable as exc:
            raise RuntimeError(
                f"network unreachable while fetching {status.slug}: {exc}. "
                "Reconnect and the next first-detect will retry."
            ) from exc
        except model_layer.HashMismatch as exc:
            raise RuntimeError(
                f"integrity check failed for {status.slug}: {exc}. "
                "The mirror may have been updated -- run `uv tool upgrade splitsmith`."
            ) from exc
        completed_bytes += slug_bytes
    handle.update(progress=1.0, message="Models ready")
    handle.set_result(
        {
            "artifacts": [s.slug for s in pending],
            "bytes": total_bytes,
        }
    )


async def _maybe_submit_model_download(state: AppState) -> None:
    """Kick off the slim-artifact prefetch on startup when anything is missing.

    No-op when the registry is unavailable (older calibrations without
    a ``model_artifacts`` block, or the torch dev install), when every
    artifact is already cached + verified, or when a previous job is
    still active (idempotent across reloads of an embedded host).
    """
    try:
        registry = model_layer.get_default_registry()
    except Exception:  # pragma: no cover -- defensive; never block boot on this
        logger.exception("model registry unavailable; skipping startup prefetch")
        return
    if registry is None:
        return
    missing = [s for s in registry.status() if s.state != "present"]
    if not missing:
        return
    if await state.jobs.find_active(kind="model_download") is not None:
        return
    try:
        await state.jobs.submit(kind="model_download")
    except ShutdownInProgressError:
        # Server is already winding down; nothing to do.
        return


def register_job_bodies(state: AppState) -> None:
    """Register the production job-body closures on ``state.jobs.bodies``.

    Shared by ``create_app`` (local + embedded hosts) and the
    Procrastinate worker bootstrap so the same ``kind`` -> body mapping
    backs every transport. The bodies close over ``state``; a hosted
    worker builds its own ``state`` and calls this against it. Dev-only
    lab kinds register at their callsites in ``create_app`` since they're
    never deferred to a hosted worker.
    """
    # Cross-cam alignment is accepted as a *suggestion* (beep_source="aligned",
    # beep_reviewed=False so the SPA forces the user to verify on the waveform
    # picker) when the peak-to-runner-up ratio of the cross-correlation clears
    # this threshold. 1.0 means the landmark wasn't distinctive at all.
    # Empirically calibrated on tallmilan-2026 secondaries: same-stage pairs
    # where in-stream detection independently succeeded land at 1.23-1.34, and
    # plausible alignments on the in-stream-failed pairs land at 1.15-1.31.
    # Same-mic non-overlapping clips sit at ~1.00. 1.10 leaves a slim margin
    # over the floor while still surfacing real alignments. False positives
    # are caught by the user in the picker before "Mark reviewed"; false
    # negatives mean the user starts from the auto-detect-failed state and
    # places the marker by hand, which is the same UX as before this change.
    _align_confidence_floor = 1.10

    def _try_align_secondary_to_primary(
        root: Path,
        proj: MatchProject,
        stage: StageEntry,
        video: StageVideo,
        handle: JobHandle,
    ) -> cross_align.CrossAlignResult | None:
        """Attempt cross-correlation alignment of ``video`` against the stage's
        primary. Returns the raw result whenever cross-correlation could be
        computed (so the caller can save ``confidence`` as a diagnostic even
        below the auto-accept floor); returns ``None`` only when alignment
        isn't possible at all (no primary, no primary beep_time, audio not
        cached, landmark window too narrow). Threshold comparison happens at
        the call site so a low-confidence result can still inform the UI.

        Run only on the secondary soft-fail path -- when in-stream beep
        detection has already raised ``BeepNotFoundError``. Cheap (envelope
        cross-correlation at 200 Hz; sub-second on a 60 s clip), but not free,
        so we skip it on the primary path entirely.
        """
        primary = stage.primary()
        if primary is None or primary.beep_time is None:
            return None
        primary_audio_path = audio_helpers.primary_audio_path(root, stage.stage_number, project=proj)
        secondary_audio_path = audio_helpers.video_audio_path(root, stage.stage_number, video, project=proj)
        if not primary_audio_path.exists() or not secondary_audio_path.exists():
            # Either side missing means the user hasn't run beep-detect on
            # the primary yet, or the secondary's own audio extract failed
            # before we even got here. Both are dealt with by the existing
            # soft-fail UX -- alignment can be retried later.
            return None
        try:
            primary_audio, primary_sr = beep_detect.load_audio(primary_audio_path)
            secondary_audio, secondary_sr = beep_detect.load_audio(secondary_audio_path)
            handle.update(progress=0.45, message="Aligning to primary...")
            result = cross_align.align_secondary_to_primary(
                primary_audio,
                primary_sr,
                primary.beep_time,
                secondary_audio,
                secondary_sr,
            )
        except cross_align.CrossAlignError as exc:
            logger.info(
                "cross-align skipped for stage %d video %s: %s",
                stage.stage_number,
                video.video_id,
                exc,
            )
            return None
        return result

    def _run_detect_beep_for_video(handle: JobHandle, slug: str, stage_number: int, video_id: str) -> None:
        """Worker: detect ``video``'s beep, then auto-chain trim.

        Generic over role:
          - primary: detect -> trim -> shot_detect (existing pipeline).
          - secondary: detect -> trim (no shot_detect; the audit timeline
            is anchored to the primary's beep).

        Trim is treated as a soft failure: the beep is valuable on its
        own and the SPA can re-trim later.
        """
        handle.update(progress=0.05, message="Loading project...")
        with handle.timer.phase("load_project"):
            proj = state.shooter_project(slug)
            stg = proj.stage(stage_number)
            video = stg.find_video_by_id(video_id)
            if video is None:
                raise RuntimeError(f"video {video_id} disappeared from stage {stage_number} mid-flight")
            source = proj.resolve_video_path(state.shooter_root(slug), video.path)
        handle.timer.set_meta(role=video.role)
        role_label = "primary" if video.role == "primary" else f"cam {video.video_id[:6]}"
        handle.update(
            progress=0.15,
            message=f"Extracting audio + detecting beep ({role_label})...",
        )
        try:
            with handle.timer.phase("beep_detect"):
                beep = audio_helpers.detect_video_beep(
                    state.shooter_root(slug),
                    stage_number,
                    video,
                    source,
                    project=proj,
                    ffmpeg_binary=process_runtime().ffmpeg_binary,
                )
        except beep_detect.BeepNotFoundError as exc:
            # Primary failure is fatal -- the entire downstream pipeline
            # (trim window, shot timeline) hangs off the primary beep.
            # Surfacing as a job error gets the user looking at the audio
            # right away rather than silently falling through to a wrong
            # alignment. Secondary failure is soft-handled below: many
            # non-headcam cameras (iPhone tripod, RO position, AGC'd) just
            # don't capture a sustained 2-5 kHz tone, and aborting the
            # import on every one of them is the worse UX.
            if video.role == "primary":
                raise
            logger.info(
                "no beep on secondary stage %d video %s: %s",
                stage_number,
                video.video_id,
                exc,
            )
            # Cross-correlation fallback: when the primary already has a
            # beep, try aligning the secondary's audio against the
            # primary's landmark window. Same buzzer + first shots in the
            # same room means the loudness envelopes line up modulo a
            # constant time offset, even when the secondary's mic missed
            # the sustained 2-5 kHz tone the in-stream detector wants.
            with handle.timer.phase("cross_align"):
                aligned = _try_align_secondary_to_primary(state.shooter_root(slug), proj, stg, video, handle)
            video.beep_peak_amplitude = None
            video.beep_duration_ms = None
            video.beep_candidates = []
            video.beep_reviewed = False
            video.processed["beep"] = True
            # Surface the cross-correlation confidence whenever we got one,
            # even if we don't promote the suggestion. Lets the UI tell the
            # difference between "never tried" and "tried, sub-floor result".
            video.beep_alignment_confidence = aligned.confidence if aligned is not None else None
            # Delta is only meaningful when both in-stream AND cross-align
            # produced timestamps. In-stream failed here, so wipe it.
            video.beep_alignment_delta_ms = None
            if aligned is not None and aligned.confidence >= _align_confidence_floor:
                handle.update(
                    progress=0.55,
                    message=(f"Aligned to primary (conf {aligned.confidence:.2f}); " "verify on waveform"),
                )
                video.beep_time = aligned.secondary_beep_time
                video.beep_source = "aligned"
                video.beep_auto_detect_failed = False
                # Treat as "we have a usable beep_time": fall through to
                # the trim block below.
                beep = aligned
            else:
                if aligned is not None:
                    logger.info(
                        "cross-align below floor for stage %d video %s: conf %.2f < %.2f",
                        stage_number,
                        video.video_id,
                        aligned.confidence,
                        _align_confidence_floor,
                    )
                handle.update(progress=0.55, message="No beep detected; pick on waveform")
                video.beep_time = None
                video.beep_source = "auto"
                video.beep_auto_detect_failed = True
                video.processed["trim"] = False
                beep = None
        else:
            handle.update(progress=0.55, message="Saving beep...")
            video.beep_time = beep.time
            video.beep_source = "auto"
            video.beep_peak_amplitude = beep.peak_amplitude
            video.beep_duration_ms = beep.duration_ms
            video.beep_confidence = beep.confidence
            video.beep_candidates = list(beep.candidates)
            video.beep_auto_detect_failed = False
            video.beep_alignment_confidence = None
            video.beep_alignment_delta_ms = None
            video.processed["beep"] = True
            # Auto-detected beeps need explicit user review (#71) UNLESS
            # the calibrated confidence (#220 layer 3a) clears the
            # ``beep_low_confidence_threshold`` automation gate (#219).
            # Above the threshold we auto-trust: the detector is right
            # ~95 % of the time in that band, so making the user click
            # to confirm every high-confidence beep adds friction
            # without catching real problems. Below it the user has to
            # review -- the HITL queue (``GET /api/hitl-queue``) lists
            # exactly these. Resets to False on re-detection so the
            # prior approval doesn't carry over to a fresh run.
            resolved_auto = automation_settings.resolve_automation(
                project_override=proj.automation,
            )
            threshold = resolved_auto.settings.beep_low_confidence_threshold
            video.beep_reviewed = beep.confidence >= threshold
            # Sanity check: when in-stream succeeded on a secondary, ALSO
            # run cross-correlation against the primary. If the two
            # methods disagree by more than ~250 ms it usually means the
            # in-stream detector locked onto something that wasn't the
            # buzzer (a steel-strike that resembles a tone, an early
            # range-officer command, etc.). The delta is surfaced in the
            # SPA so the user can compare both candidates on the
            # waveform before marking reviewed. Never overrides the
            # in-stream result -- in-stream has frequency-domain
            # information cross-align doesn't, so when they agree we
            # trust the in-stream answer; when they disagree we let the
            # user decide.
            if video.role != "primary":
                with handle.timer.phase("cross_align"):
                    check = _try_align_secondary_to_primary(
                        state.shooter_root(slug), proj, stg, video, handle
                    )
                if check is not None and check.confidence >= _align_confidence_floor:
                    video.beep_alignment_confidence = check.confidence
                    video.beep_alignment_delta_ms = (beep.time - check.secondary_beep_time) * 1000.0

        trimmed_ok = False
        if beep is not None and stg.time_seconds > 0:
            handle.check_cancel()
            handle.update(progress=0.55, message=f"Trimming audit clip ({role_label})...")
            try:
                with handle.timer.phase("trim"):
                    audio_helpers.ensure_video_audit_trim(
                        state.shooter_root(slug),
                        stage_number,
                        video,
                        source,
                        video.beep_time,
                        stg.time_seconds,
                        project=proj,
                        runner=_cancellable_runner(handle),
                        ffmpeg_binary=process_runtime().ffmpeg_binary,
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
        with handle.timer.phase("persist"):
            fresh = state.shooter_project(slug)
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
                v_fresh.beep_confidence = video.beep_confidence
                v_fresh.beep_candidates = list(video.beep_candidates)
                v_fresh.beep_reviewed = video.beep_reviewed
                v_fresh.beep_auto_detect_failed = video.beep_auto_detect_failed
                v_fresh.beep_alignment_confidence = video.beep_alignment_confidence
                v_fresh.beep_alignment_delta_ms = video.beep_alignment_delta_ms
                v_fresh.processed["beep"] = True
                if trimmed_ok:
                    v_fresh.processed["trim"] = True
                fresh.save(state.shooter_root(slug))
        handle.timer.set_meta(trimmed=trimmed_ok)
        if trimmed_ok and video.role == "primary" and video.beep_reviewed:
            # Shot detection is primary-only AND gated on
            # ``beep_reviewed`` (#71). That flag is True either because
            # the user explicitly clicked "Mark reviewed" (which
            # re-triggers chaining via ``set_beep_reviewed``) or because
            # the auto-trust gate cleared (#219 layer 3b: confidence at
            # or above ``beep_low_confidence_threshold``). Saves the
            # heavy CLAP / GBDT / PANN ensemble work when the beep
            # timestamp is wrong, since everything downstream of it
            # would be garbage anyway.
            #
            # Layered automation gate (#215): users can disable the
            # auto-trigger globally or per project. ``cli_override`` is
            # always None for the server -- the daemon doesn't take
            # CLI flags at this entry point.
            resolved = automation_settings.resolve_automation(
                project_override=fresh.automation,
            )
            # Worker callback runs in a ThreadPoolExecutor thread with
            # no event loop; bridge to the async JobBackend via
            # ``asyncio.run``.
            if (
                resolved.settings.shot_detect_on_beep_verified
                and asyncio.run(state.jobs.find_active(kind="shot_detect", stage_number=stage_number)) is None
            ):
                asyncio.run(
                    state.jobs.submit(
                        kind="shot_detect",
                        stage_number=stage_number,
                        args={"slug": slug, "stage_number": stage_number},
                    )
                )
        handle.update(progress=1.0, message="Done")

    def _run_trim(
        handle: JobHandle,
        *,
        slug: str,
        stage_number: int,
        video_id: str,
        chain_shot_detect: bool = True,
    ) -> None:
        """Worker for the audit-mode trim of a specific video.

        Resolves the shooter via ``slug`` (so the body is portable to a
        hosted worker process -- the bulk path used to pass an explicit
        ``shooter_root`` Path, which can't cross a process boundary).

        Auto-chains shot detection only when the trimmed video is the
        reviewed primary AND ``chain_shot_detect`` is set; secondaries
        align to the primary's audit timeline by their own beep, so they
        do not need their own shot-detection run. The bulk maintenance
        pass passes ``chain_shot_detect=False`` -- it regenerates derived
        trim caches without re-running detection over the whole match.
        """
        handle.update(progress=0.1, message="Preparing trim...")
        with handle.timer.phase("load_project"):
            proj = state.shooter_project(slug)
            root = state.shooter_root(slug)
            stg = proj.stage(stage_number)
            video = stg.find_video_by_id(video_id)
            if video is None or video.beep_time is None:
                raise RuntimeError("video or beep disappeared mid-flight")
            source = proj.resolve_video_path(root, video.path)
        handle.timer.set_meta(role=video.role)
        handle.check_cancel()
        role_label = "primary" if video.role == "primary" else f"cam {video.video_id[:6]}"
        handle.update(progress=0.3, message=f"Encoding short-GOP MP4 ({role_label})...")
        with handle.timer.phase("trim"):
            audio_helpers.ensure_video_audit_trim(
                root,
                stage_number,
                video,
                source,
                video.beep_time,
                stg.time_seconds,
                project=proj,
                runner=_cancellable_runner(handle),
                ffmpeg_binary=process_runtime().ffmpeg_binary,
            )
        handle.update(progress=0.85, message="Saving project...")
        with handle.timer.phase("persist"):
            # Read-modify-write to avoid stomping concurrent edits made
            # while ffmpeg was running (e.g. another stage's beep review).
            fresh = state.shooter_project(slug)
            stg_fresh = fresh.stage(stage_number)
            v_fresh = stg_fresh.find_video_by_id(video_id)
            if v_fresh is not None:
                v_fresh.processed["trim"] = True
                fresh.save(root)
        # Worker callback runs in a ThreadPoolExecutor thread with no
        # event loop; bridge to the async JobBackend via ``asyncio.run``.
        #
        # Read ``beep_reviewed`` from the freshly reloaded video, NOT the
        # snapshot captured at job start: a "Mark reviewed" click that
        # lands while ffmpeg is running flips the flag on disk but
        # ``set_beep_reviewed`` no-ops (the trim isn't cached yet), so
        # this is the only place left to honor it. Falls back to the
        # snapshot when the video vanished mid-flight.
        beep_reviewed_now = v_fresh.beep_reviewed if v_fresh is not None else video.beep_reviewed
        if (
            chain_shot_detect
            and video.role == "primary"
            and beep_reviewed_now
            and asyncio.run(state.jobs.find_active(kind="shot_detect", stage_number=stage_number)) is None
        ):
            # Same gate as the detect-then-trim path (#71): don't burn
            # CLAP / GBDT / PANN cycles on a beep the user hasn't
            # confirmed. Manual entries pre-set ``beep_reviewed`` to True
            # so this still runs; the auto-detect / select-candidate path
            # leaves it False until the user's explicit "Mark reviewed"
            # click -- which fires shot_detect itself when the trim is
            # already cached, or relies on this fresh read when it isn't.
            #
            # Layered automation gate (#215): a global / project
            # opt-out can suppress this auto-trigger.
            resolved = automation_settings.resolve_automation(
                project_override=fresh.automation,
            )
            if resolved.settings.shot_detect_on_beep_verified:
                asyncio.run(
                    state.jobs.submit(
                        kind="shot_detect",
                        stage_number=stage_number,
                        args={"slug": slug, "stage_number": stage_number},
                    )
                )
        handle.update(progress=1.0, message="Done")

    def _run_shot_detect(handle: JobHandle, slug: str, stage_number: int, reset: bool = False) -> None:
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
        with handle.timer.phase("load_project"):
            proj = state.shooter_project(slug)
            stg = proj.stage(stage_number)
            prim = stg.primary()
            if prim is None or prim.beep_time is None:
                raise RuntimeError(f"stage {stage_number} has no primary or no beep yet")
            if stg.time_seconds <= 0:
                raise RuntimeError(
                    f"stage {stage_number} has time_seconds=0; import a "
                    "scoreboard before running shot detection"
                )
            source = proj.resolve_video_path(state.shooter_root(slug), prim.path)

        # Re-detect-all on an old project (and the v1->v2 cache migration in
        # #298) leaves stages with no trimmed MP4 cached. Without that file,
        # ensure_audit_audio falls back to the full source WAV -- shot
        # detection still works, but the audit page then streams the raw
        # source clip into <video>, where long-GOP 4K MOVs wedge in buffering
        # on first play. Force the trim here so every shot-detect run leaves
        # the audit cache in the same state the beep-detect path would have.
        handle.update(progress=0.05, message="Ensuring audit trim...")
        try:
            with handle.timer.phase("ensure_trim"):
                audio_helpers.ensure_video_audit_trim(
                    state.shooter_root(slug),
                    stage_number,
                    prim,
                    source,
                    prim.beep_time,
                    stg.time_seconds,
                    project=proj,
                    runner=_cancellable_runner(handle),
                    ffmpeg_binary=process_runtime().ffmpeg_binary,
                )
                trim_fresh = state.shooter_project(slug)
                try:
                    v_fresh = trim_fresh.stage(stage_number).find_video_by_id(prim.video_id)
                except KeyError:
                    v_fresh = None
                if v_fresh is not None and not v_fresh.processed.get("trim"):
                    v_fresh.processed["trim"] = True
                    trim_fresh.save(state.shooter_root(slug))
        except (FileNotFoundError, audio_helpers.AudioExtractionError) as exc:
            # Soft failure: detection can still proceed against the source
            # WAV. ensure_audit_audio's fallback handles the missing trim,
            # so we log and continue rather than abort the whole job.
            logger.warning(
                "shot_detect could not (re)build trim for stage %d: %s",
                stage_number,
                exc,
            )

        handle.update(progress=0.1, message="Preparing audio...")
        with handle.timer.phase("prepare_audio"):
            audit = audio_helpers.ensure_audit_audio(
                state.shooter_root(slug),
                stage_number,
                source,
                prim.beep_time,
                project=proj,
                ffmpeg_binary=process_runtime().ffmpeg_binary,
            )
        beep_in_clip = audit.beep_in_clip if audit.beep_in_clip is not None else prim.beep_time

        # Read existing audit JSON up-front: we need ``stage_rounds.expected``
        # before running detection (it changes voter C's mode and the
        # apriori boost) and we'll merge results back into the same dict.
        # Hosted: pull the existing audit fresh first so a prior API/worker
        # write isn't lost when we merge + push back.
        with handle.timer.phase("load_audit_state"):
            existing_json, audit_version = state.load_audit(slug, stage_number)
            if existing_json is None:
                existing_json = {
                    "stage_number": stg.stage_number,
                    "stage_name": stg.stage_name,
                    "stage_time_seconds": stg.time_seconds,
                    "beep_time": round(beep_in_clip, 4),
                    "shots": [],
                }
            # Carry stage_rounds (min_rounds + target breakdown) from the
            # project state into the audit JSON so the ensemble's adaptive
            # voter C + apriori boost can consume ``stage_rounds.expected``.
            # Project value wins over a stale audit-JSON copy: re-running
            # detection after a scoreboard refresh should pick up the new
            # round count without manual edits.
            if stg.stage_rounds is not None:
                existing_json["stage_rounds"] = stg.stage_rounds.model_dump(mode="json", exclude_none=True)
            expected_rounds: int | None = None
            sr_block = existing_json.get("stage_rounds")
            if isinstance(sr_block, dict):
                raw = sr_block.get("expected")
                if isinstance(raw, int) and raw > 0:
                    expected_rounds = raw

        handle.update(progress=0.2, message="Loading ensemble models...")
        with handle.timer.phase("audio_load"):
            audio_array, sr = beep_detect.load_audio(audit.audio_path)
        # Camera-class dispatch (issue #143). When the primary's mount
        # is set, look up handheld vs. headcam thresholds; otherwise
        # the ensemble falls back to the default class (headcam).
        cam_class = ensemble_module.camera_class_from_mount(prim.camera_mount)
        # Voter E opt-in (issue #183). Off by default for the first
        # release; flip via env var until corpus growth justifies
        # default-on. Requires the calibration to ship a probe head
        # AND the source video to be reachable.
        enable_e = os.environ.get("SPLITSMITH_ENABLE_VOTER_E") == "1"
        ensemble_cfg = ensemble_module.EnsembleConfig(enable_voter_e=enable_e)

        # Cold-model-load is timed as its own phase ONLY when the singleton
        # isn't already populated (a worker that warmed at boot, or a prior
        # detect in this process, reports warm and skips it). Forcing the
        # resolution here so ``detect_stage`` below then hits the warm cache
        # means the heavy weight load never hides inside ``ensemble_infer``.
        cold = not _ensemble_runtime_is_warm()
        handle.timer.set_meta(cold_model_load=cold, expected_rounds=expected_rounds)
        if cold:
            with handle.timer.phase("cold_model_load"):
                _get_ensemble_runtime()

        handle.update(progress=0.4, message="Detecting shots (4-voter ensemble)...")
        with handle.timer.phase("ensemble_infer"):
            result = state.compute.detect_stage(
                audio=audio_array,
                sample_rate=sr,
                beep_time_in_clip=beep_in_clip,
                stage_time_seconds=stg.time_seconds,
                expected_rounds=expected_rounds,
                ensemble_config=ensemble_cfg,
                camera_class=cam_class,
                camera_make=prim.camera_make,
                camera_model=prim.camera_model,
                video_path=source if enable_e else None,
                source_beep_time=prim.beep_time if enable_e else None,
            )

        candidates: list[dict[str, Any]] = []
        with handle.timer.phase("build_candidates"):
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
                        "vote_e": cand.vote_e,
                        "vote_total": cand.vote_total,
                        "apriori_boost": cand.apriori_boost,
                        "ensemble_score": cand.ensemble_score,
                        "score_c": cand.score_c,
                        "clap_diff": cand.clap_diff,
                        "gunshot_prob": cand.gunshot_prob,
                        "voter_e_signal": cand.voter_e_signal,
                    }
                )

        handle.update(progress=0.85, message="Saving audit JSON...")

        def _default_audit_doc() -> dict[str, Any]:
            return {
                "stage_number": stg.stage_number,
                "stage_name": stg.stage_name,
                "stage_time_seconds": stg.time_seconds,
                "beep_time": round(beep_in_clip, 4),
                "shots": [],
            }

        def _merge_detection_into(doc: dict[str, Any]) -> dict[str, Any]:
            """Fold this run's detection results into ``doc``.

            Re-appliable against a freshly-loaded doc so a lost
            optimistic-lock race re-merges into the winner's document
            instead of clobbering it: project ``stage_rounds`` wins, the
            candidate block is rewritten, ``shots[]`` is seeded only when
            empty (or on ``reset``) so a concurrent manual edit survives,
            and the run is appended to the ``audit_events`` log.
            """
            if stg.stage_rounds is not None:
                doc["stage_rounds"] = stg.stage_rounds.model_dump(mode="json", exclude_none=True)
            doc["_candidates_pending_audit"] = {
                "_note": (
                    "3-voter ensemble (PANN gunshot folded into voter C). "
                    "vote_a/b/c=1 means the voter kept the candidate; "
                    "ensemble_score = vote_total + apriori_boost. shots[] is "
                    "seeded from candidates with ensemble_score >= consensus."
                ),
                "consensus": result.consensus,
                "expected_rounds": result.expected_rounds,
                "candidates": candidates,
            }
            if reset:
                doc["shots"] = []
            seeded_shots = False
            if not doc.get("shots"):
                kept = [c for c in result.candidates if c.kept]
                doc["shots"] = [
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
            events = list(doc.get("audit_events") or [])
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
            doc["audit_events"] = events
            return doc

        # Persist the merged audit with a bounded re-load + re-merge retry.
        # Hosted state_docs saves are optimistic-locked: a concurrent manual
        # SPA edit (or another job) can bump the version between our load and
        # save -> StateConflictError. Re-loading and re-merging into the
        # winner's doc preserves that edit (this job *merges*, never blindly
        # overwrites). Local file saves never raise, so this runs once.
        with handle.timer.phase("save_audit"):
            _save_audit_with_remerge(
                state,
                slug,
                stage_number,
                doc=existing_json,
                version=audit_version,
                merge=_merge_detection_into,
                default=_default_audit_doc,
            )

        with handle.timer.phase("persist_project"):
            # Read-modify-write the project file: a long-running shot_detect
            # job must not save the snapshot it loaded at start, because the
            # user may have toggled ``beep_reviewed`` on other stages in the
            # interim (the review action kicks shot_detect, so concurrent
            # bursts are the common case). Reloading from disk and mutating
            # only the targeted field preserves those concurrent edits.
            fresh = state.shooter_project(slug)
            stg_fresh = fresh.stage(stage_number)
            prim_fresh = stg_fresh.primary()
            if prim_fresh is not None:
                prim_fresh.processed["shot_detect"] = True
                fresh.save(state.shooter_root(slug))
        handle.timer.set_meta(
            candidate_count=len(candidates),
            kept_count=sum(1 for c in result.candidates if c.kept),
            consensus=result.consensus,
        )
        handle.update(progress=1.0, message=f"Done -- {len(candidates)} candidates")

    def _run_export_for_stage(
        handle: JobHandle, slug: str, stage_number: int, req: ExportStageRequest
    ) -> None:
        """Worker for /api/stages/{n}/export. Phases mirror the user's
        mental model so the JobsPanel message is meaningful."""
        from ..config import StageData as EngineStageData

        handle.update(progress=0.05, message="Loading project...")
        with handle.timer.phase("load_project"):
            proj = state.shooter_project(slug)
            stg = proj.stage(stage_number)
            prim = stg.primary()
            if prim is None or prim.beep_time is None:
                raise RuntimeError("primary or beep disappeared mid-flight")

        # Hosted: the audit doc was written by the detect job and lives in
        # state_docs, not this container's FS. Materialize it to the local
        # path the exporter reads (no-op in local mode -- the file is
        # already there). Without this a cross-process export reads a stale
        # or absent audit file.
        with handle.timer.phase("materialize_audit"):
            audit_file = state.materialize_audit(slug, stage_number)
        exports_dir = proj.exports_path(state.shooter_root(slug))
        source_video = proj.resolve_video_path(state.shooter_root(slug), prim.path)
        engine_stage = EngineStageData(
            stage_number=stg.stage_number,
            stage_name=stg.stage_name,
            time_seconds=stg.time_seconds,
            scorecard_updated_at=stg.scorecard_updated_at or _PLACEHOLDER_SCORECARD_TIME,
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
        # SPA's ingest screen flags those rows separately. The Export page
        # may also pass ``secondary_video_ids`` to restrict the roster to a
        # subset (None = include every cam with a beep, the legacy default).
        allowed_ids: set[str] | None = (
            set(req.secondary_video_ids) if req.secondary_video_ids is not None else None
        )
        with handle.timer.phase("prepare_inputs"):
            secondaries: list[export_helpers.SecondaryExport] = []
            for sv in stg.videos:
                if sv.role != "secondary":
                    continue
                if sv.beep_time is None:
                    continue
                if allowed_ids is not None and sv.video_id not in allowed_ids:
                    continue
                sec_source = proj.resolve_video_path(state.shooter_root(slug), sv.path)
                secondaries.append(
                    export_helpers.SecondaryExport(
                        video_id=sv.video_id,
                        source_path=sec_source,
                        beep_time_in_source=sv.beep_time,
                        label=f"Cam {sv.video_id}",
                    )
                )
        handle.timer.set_meta(secondary_count=len(secondaries))

        try:
            with handle.timer.phase("export_stage"):
                result = export_helpers.export_stage(
                    request=export_helpers.StageExportRequest(
                        stage_number=stage_number,
                        write_trim=req.write_trim,
                        write_csv=req.write_csv,
                        write_fcpxml=req.write_fcpxml,
                        write_report=req.write_report,
                        write_overlay=req.write_overlay,
                        overlay_codec=req.overlay_codec,
                        overlay_max_height=req.overlay_max_height,
                        overlay_max_fps=req.overlay_max_fps,
                        overlay_theme=req.overlay_theme,
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
        with handle.timer.phase("persist_project"):
            proj.updated_at = datetime.now(UTC)
            proj.save(state.shooter_root(slug))

        # Hosted: push every produced deliverable to storage so the API
        # container can serve the download. No-op in local mode.
        with handle.timer.phase("r2_upload"):
            export_storage.push_stage_export_outputs(proj, result)

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

        # Record the deliverables on the job so the SPA can offer downloads
        # without a separate overview round-trip. Basenames only: the
        # download endpoint (and the hosted storage pull) key off the name
        # within the shooter's exports/ scope. Persisted to the compute_jobs
        # row in hosted mode, so it survives the cross-process worker.
        def _name(p: Path | None) -> str | None:
            return p.name if p is not None else None

        handle.set_result(
            {
                "stage_number": stage_number,
                "trimmed_video": _name(result.trimmed_video_path),
                "csv": _name(result.csv_path),
                "fcpxml": _name(result.fcpxml_path),
                "report": _name(result.report_path),
                "overlay": _name(result.overlay_path),
                "secondary_trims": [p.name for p in result.secondary_trimmed_paths],
                "anomalies": result.anomalies,
            }
        )
        handle.update(progress=1.0, message=f"Done: {summary}")

    def _run_match_export(handle: JobHandle, slug: str, req: MatchExportRequest) -> None:
        """Worker for the shooter's match-export job. Re-runs the per-stage exporter for
        any selected stage missing its lossless trim, then composes the
        match FCPXML.
        """
        from ..config import StageData as EngineStageData
        from . import exports as exports_mod

        handle.update(progress=0.02, message="Loading project...")
        with handle.timer.phase("load_project"):
            proj = state.shooter_project(slug)
            exports_dir = proj.exports_path(state.shooter_root(slug))
            audit_dir = proj.audit_path(state.shooter_root(slug))

        n = len(req.stage_numbers)
        handle.timer.set_meta(stage_count=n)
        # Reserve the last 10% for the match compose step; the rest is
        # split evenly across per-stage trims (the dominant wall time).
        # Stages that already have a trim skip ahead within their slice
        # instead of contributing to the wait.
        per_stage_share = 0.85 / max(1, n)

        with handle.timer.phase("per_stage"):
            for idx, stage_number in enumerate(req.stage_numbers):
                handle.check_cancel()
                stg = proj.stage(stage_number)
                prim = stg.primary()
                if prim is None or prim.beep_time is None:
                    raise RuntimeError(f"stage {stage_number}: primary or beep disappeared mid-flight")

                base = f"stage{stage_number}_{exports_mod._slugify(stg.stage_name)}"
                trimmed_path = exports_dir / f"{base}_trimmed.mp4"
                overlay_target = exports_dir / f"{base}_overlay.mov"

                # Hosted: per-stage artifacts may have been produced by an
                # earlier job on a different worker. Materialize the audit doc
                # (state_docs -> local file) so the composer + per-stage
                # exporter can read it, and pull the cached trim so the
                # existence checks below reuse it instead of re-cutting. No-op
                # in local mode.
                state.materialize_audit(slug, stage_number)
                export_storage.pull_export_file(proj, trimmed_path)

                # Decide what's missing. The match composer needs the
                # lossless trim + per-cam trims (when include_secondaries) +
                # optional overlay. Re-run the per-stage exporter for any
                # stage where any required artefact isn't on disk.
                wanted_secondary_ids: set[str] = set()
                if req.include_secondaries:
                    for sv in stg.videos:
                        if sv.role == "secondary" and sv.beep_time is not None:
                            wanted_secondary_ids.add(sv.video_id)
                for vid in wanted_secondary_ids:
                    export_storage.pull_export_file(proj, exports_dir / f"{base}_cam_{vid}_trimmed.mp4")
                secondary_trims_present = all(
                    (exports_dir / f"{base}_cam_{vid}_trimmed.mp4").exists() for vid in wanted_secondary_ids
                )
                # Treat any non-default overlay format option as "force re-render"
                # so the dialog's codec / max-height / max-fps choices actually
                # apply when a stale overlay sits on disk. With all defaults we
                # keep the legacy "skip if present" behaviour for fast re-stitching.
                overlay_format_overridden = (
                    req.overlay_codec != "auto"
                    or req.overlay_max_height is not None
                    or req.overlay_max_fps is not None
                    or req.overlay_theme != "splitsmith"
                )
                # Pull a cached overlay only when we'd actually reuse it -- a
                # format override forces a re-render, so don't waste the download.
                if req.include_overlay and not overlay_format_overridden:
                    export_storage.pull_export_file(proj, overlay_target)
                overlay_missing = req.include_overlay and (
                    not overlay_target.exists() or overlay_format_overridden
                )

                needs_per_stage = not trimmed_path.exists() or not secondary_trims_present or overlay_missing
                if needs_per_stage:
                    handle.update(
                        progress=0.02 + idx * per_stage_share,
                        message=(f"Stage {stage_number} ({idx + 1} of {n}): " "running per-stage export..."),
                    )
                    # Build the secondaries list for the per-stage exporter --
                    # mirrors the single-stage endpoint's logic.
                    secondaries_in: list[export_helpers.SecondaryExport] = []
                    if req.include_secondaries:
                        for sv in stg.videos:
                            if sv.role != "secondary" or sv.beep_time is None:
                                continue
                            sec_source = proj.resolve_video_path(state.shooter_root(slug), sv.path)
                            secondaries_in.append(
                                export_helpers.SecondaryExport(
                                    video_id=sv.video_id,
                                    source_path=sec_source,
                                    beep_time_in_source=sv.beep_time,
                                    label=f"Cam {sv.video_id}",
                                )
                            )
                    source_video = proj.resolve_video_path(state.shooter_root(slug), prim.path)
                    engine_stage = EngineStageData(
                        stage_number=stg.stage_number,
                        stage_name=stg.stage_name,
                        time_seconds=stg.time_seconds,
                        scorecard_updated_at=stg.scorecard_updated_at or _PLACEHOLDER_SCORECARD_TIME,
                    )
                    try:
                        recut = export_helpers.export_stage(
                            request=export_helpers.StageExportRequest(
                                stage_number=stage_number,
                                write_trim=True,
                                write_csv=True,
                                write_fcpxml=True,
                                write_report=True,
                                write_overlay=req.include_overlay,
                                overlay_codec=req.overlay_codec,
                                overlay_max_height=req.overlay_max_height,
                                overlay_max_fps=req.overlay_max_fps,
                                overlay_theme=req.overlay_theme,
                            ),
                            audit_path=audit_dir / f"stage{stage_number}.json",
                            exports_dir=exports_dir,
                            source_video_path=source_video if source_video.exists() else None,
                            stage_data=engine_stage,
                            beep_time_in_source=prim.beep_time,
                            pre_buffer_seconds=proj.trim_pre_buffer_seconds,
                            post_buffer_seconds=proj.trim_post_buffer_seconds,
                            config=Config(),
                            secondaries=secondaries_in,
                        )
                    except export_helpers.StageExportError as exc:
                        raise RuntimeError(f"stage {stage_number}: {exc}") from exc
                    # Hosted: a re-cut here produced the per-stage trims/overlay
                    # on this worker -- push them so other workers + the API
                    # download path see them. No-op in local mode.
                    export_storage.push_stage_export_outputs(proj, recut)
                else:
                    handle.update(
                        progress=0.02 + idx * per_stage_share,
                        message=(
                            f"Stage {stage_number} ({idx + 1} of {n}): " "trim already present; skipping"
                        ),
                    )

        handle.check_cancel()
        handle.update(progress=0.92, message="Stitching match FCPXML...")

        # Re-load the project: the per-stage worker writes processed flags
        # via project.save() side-effects, so a fresh load picks those up.
        with handle.timer.phase("compose"):
            proj = state.shooter_project(slug)
            stages_input: list[match_export_helpers.MatchStageInput] = []
            for stage_number in req.stage_numbers:
                stg = proj.stage(stage_number)
                prim = stg.primary()
                assert prim is not None and prim.beep_time is not None
                base = f"stage{stage_number}_{exports_mod._slugify(stg.stage_name)}"
                trimmed_path = exports_dir / f"{base}_trimmed.mp4"
                audit_path = audit_dir / f"stage{stage_number}.json"
                overlay_path = exports_dir / f"{base}_overlay.mov"
                secondaries: list[match_export_helpers.MatchSecondaryInput] = []
                for sv in stg.videos:
                    if sv.role != "secondary" or sv.beep_time is None:
                        continue
                    sec_clip_beep = min(proj.trim_pre_buffer_seconds, sv.beep_time)
                    secondaries.append(
                        match_export_helpers.MatchSecondaryInput(
                            video_id=sv.video_id,
                            trimmed_path=exports_dir / f"{base}_cam_{sv.video_id}_trimmed.mp4",
                            beep_offset_seconds=sec_clip_beep,
                            label=f"Cam {sv.video_id}",
                        )
                    )
                primary_clip_beep = min(proj.trim_pre_buffer_seconds, prim.beep_time)
                stages_input.append(
                    match_export_helpers.MatchStageInput(
                        stage_number=stage_number,
                        stage_name=stg.stage_name,
                        audit_path=audit_path,
                        trimmed_path=trimmed_path,
                        beep_offset_seconds=primary_clip_beep,
                        secondaries=tuple(secondaries),
                        overlay_path=overlay_path,
                    )
                )

            project_name = req.project_name or proj.name or "match"
            request_data = match_export_helpers.MatchExportRequestData(
                stage_numbers=tuple(req.stage_numbers),
                head_pad_seconds=req.head_pad_seconds,
                tail_pad_seconds=req.tail_pad_seconds,
                include_secondaries=req.include_secondaries,
                include_overlay=req.include_overlay,
                project_name=project_name,
                pip_layout=req.pip_layout,
                output_format=req.output_format,
                transition_kind=req.transition_kind,
                transition_duration_seconds=req.transition_duration_seconds,
                title_kind=req.title_kind,
                title_duration_seconds=req.title_duration_seconds,
                intro_path=Path(req.intro_path).expanduser() if req.intro_path else None,
                outro_path=Path(req.outro_path).expanduser() if req.outro_path else None,
                youtube_sidecar=req.youtube_sidecar,
                youtube_preset=req.youtube_preset,
            )
            try:
                result = match_export_helpers.export_match(
                    stages=stages_input,
                    request=request_data,
                    exports_dir=exports_dir,
                    config=Config().output,
                )
            except match_export_helpers.MatchExportError as exc:
                raise RuntimeError(str(exc)) from exc

        # Hosted: push the stitched match deliverable (+ optional YouTube
        # sidecars, when youtube_sidecar wrote them) so the API can serve the
        # download. push_export_file skips any that don't exist. No-op local.
        with handle.timer.phase("r2_upload"):
            export_storage.push_export_file(proj, result.fcpxml_path)
            export_storage.push_export_file(proj, result.fcpxml_path.with_suffix(".srt"))
            export_storage.push_export_file(proj, result.fcpxml_path.with_suffix(".json"))

        handle.set_result(
            {
                "fcpxml_path": str(result.fcpxml_path),
                "stage_count": result.stage_count,
                "duration_seconds": result.duration_seconds,
                "anomalies": result.anomalies,
            }
        )
        anom_word = "anomaly" if len(result.anomalies) == 1 else "anomalies"
        anom_suffix = f" ({len(result.anomalies)} {anom_word})" if result.anomalies else ""
        handle.update(
            progress=1.0,
            message=(f"Done: {result.stage_count} stages, " f"{result.duration_seconds:.1f}s{anom_suffix}"),
        )

    state.jobs.bodies.register("model_download", _run_model_download_job)
    state.jobs.bodies.register("detect_beep", _run_detect_beep_for_video)
    state.jobs.bodies.register("trim", _run_trim)
    state.jobs.bodies.register("shot_detect", _run_shot_detect)
    state.jobs.bodies.register("export", _run_export_for_stage)
    state.jobs.bodies.register("match_export", _run_match_export)


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = splitsmith_version
    bound: bool
    project_name: str | None = None
    project_root: str | None = None
    # Stable match identifier. ``None`` when unbound.
    match_id: str | None = None
    # ``"match"`` when bound, ``None`` when unbound. The field is kept
    # as a discriminator for forward compatibility (visibility ==
    # 'public' / squad-shared projects per doc 02 v2/v3) even though
    # the only live value today is ``"match"``.
    kind: str | None = None
    # Alphabetically-first registered shooter slug, or ``None`` when
    # the match is empty. Lets the SPA build /audit/<slug>/... links
    # from slugless surfaces without forcing the user to pick when
    # there's only one sensible default.
    default_shooter_slug: str | None = None


class PromoteSecondaryBody(BaseModel):
    """Body for the project-aware promote-secondary endpoint.

    Defined at module scope (not inside the lab-route factory) so FastAPI
    can resolve the forward reference under ``from __future__ import
    annotations``.
    """

    mount: str
    position: str
    audio_source: str = "internal"
    agc_state: str = "unknown"
    snap_window_ms: float = 60.0
    min_spacing_ms: float = 80.0
    slug: str | None = None
    camera_id: str | None = None
    overwrite: bool = False


class PromoteAgainstFixtureBody(BaseModel):
    """Body for ``/api/lab/projects/.../promote-against-fixture`` (issue #149 follow-up).

    Like :class:`PromoteSecondaryBody` but the anchor comes from an
    explicit fixture slug rather than being derived from the project's
    name + stage number. Lets the user anchor *any* project video
    (primary or secondary) against any existing fixture -- the typical
    case is a phone-cam-only project anchored against a previously
    audited headcam fixture.
    """

    anchor_slug: str
    mount: str
    position: str
    audio_source: str = "internal"
    agc_state: str = "unknown"
    snap_window_ms: float = 60.0
    min_spacing_ms: float = 80.0
    slug: str | None = None
    camera_id: str | None = None
    overwrite: bool = False


class RecentProjectDetail(BaseModel):
    """Enriched RecentProject for the match picker (#322).

    Mirrors :class:`user_config.RecentProject` plus derived metadata read
    from disk at listing time. ``kind`` is ``"match"`` for a Match folder,
    ``"missing"`` when the path no longer exists, ``"unknown"`` for an
    existing path that isn't a Match folder (a stale legacy entry from
    pre-Tier-1 days; the user can prune it from the picker).

    Derived fields are best-effort: a stale or unreadable project still
    shows up with ``kind="unknown"`` so the user can find and remove it
    rather than silently disappearing.
    """

    path: str
    name: str
    last_opened_at: datetime
    kind: Literal["match", "missing", "unknown"]
    # Stable match identifier. Populated for ``kind="match"`` via the
    # load-time migration; ``None`` otherwise.
    match_id: str | None = None
    # The five fields below only populate for resolved kinds.
    shooter_count: int = 0
    stage_count: int = 0
    stages_audited: int = 0
    # Total raw videos attached across all shooters. Drives the
    # "awaiting footage" status the picker renders before the operator
    # has uploaded anything (#425).
    video_count: int = 0
    match_date: str | None = None
    club: str | None = None
    last_modified_at: datetime | None = None
    status: Literal["awaiting_footage", "in_progress", "exported", "archived", "unknown"] = "unknown"
    # ``True`` for matches the user created without scoreboard data --
    # surfaces the "manual" pill in the polished picker.
    manual: bool = False
    # Human-readable shooter names in match order so the picker can
    # render real initials in avatar stacks instead of stubs.
    shooter_names: list[str] = []


class CreateMatchStageDraft(BaseModel):
    """One row of the manual-create stage editor."""

    stage_number: int
    stage_name: str
    expected_rounds: int | None = None
    target_type: str | None = None


class CreateMatchPrimaryShooter(BaseModel):
    """Primary-shooter section of the manual-create form."""

    name: str
    division: str | None = None


class CreateMatchManualRequest(BaseModel):
    """Body for POST /api/match/create-manual (#322).

    Scaffolds a Match folder with the supplied stages + the primary shooter
    registered. The first shooter's directory is bound on success so the
    rest of the server (which still speaks legacy ``MatchProject``) keeps
    working unchanged.
    """

    name: str
    # Optional in hosted mode: when omitted (or empty/null) the server
    # synthesizes ``<SPLITSMITH_PROJECTS_DIR>/users/<user_id>/projects/<slug>/``.
    # Local mode still requires the field so the user controls where the
    # match lands on their disk. See ``_resolve_create_target``.
    project_folder: str | None = None
    match_date: date | None = None
    club: str | None = None
    match_type: str | None = None
    default_division: str | None = None
    stages: list[CreateMatchStageDraft]
    primary_shooter: CreateMatchPrimaryShooter


class CreateMatchCompetitorPick(BaseModel):
    """One competitor the user is bringing into the new match (#322).

    ``selected_competitor_id`` is the per-match id (needed for the
    stage-times lookup). ``selected_shooter_id`` is the global stable
    SSI id; both are kept so refresh-by-name works later. Picks are
    treated equally -- there is no "me" or "primary" flag because the
    operator running the app may be coaching and not a shooter at all
    (see issue #350 / ``feedback_operator_vs_shooter``).
    """

    name: str
    division: str | None = None
    selected_shooter_id: int | None = None
    selected_competitor_id: int


class CreateMatchScoreboardRequest(BaseModel):
    """Body for POST /api/match/create-from-scoreboard (#322).

    Scaffolds a Match folder named after the upstream match, creates
    one shooter per :class:`CreateMatchCompetitorPick`, populates
    stage definitions from the upstream ``MatchData``, pins each
    competitor, and merges stage times -- all in one round trip so
    the create flow lands the user on a fully-populated match.
    """

    # Optional in hosted mode (see :class:`CreateMatchManualRequest`).
    project_folder: str | None = None
    name: str
    match_id: int
    content_type: int
    competitors: list[CreateMatchCompetitorPick]


class ShooterCameraInfo(BaseModel):
    """One camera entry for a shooter, derived from per-stage primaries (#324)."""

    # Stable id grouping = ``camera_make|camera_model|camera_mount``; the
    # SPA renders ordered labels (Camera A / B / ...) per shooter.
    group_key: str
    make: str | None
    model: str | None
    mount: str | None
    role: Literal["primary", "secondary"]
    video_count: int
    stage_numbers: list[int]


class ShooterListEntry(BaseModel):
    """One row of GET /api/match/shooters (#324)."""

    slug: str
    name: str
    selected_shooter_id: int | None
    selected_competitor_id: int | None
    stages_audited: int
    stages_total: int
    video_count: int
    cameras: list[ShooterCameraInfo]
    # Stages where the primary has a beep + stage time set but no audit-mode
    # trim cache MP4 on disk. Drives the "Rebuild trim caches (N)" CTA on
    # the Shooters page (#351). Counts only stages where rebuild is possible
    # (primary + beep + stage_time + reachable source); stages missing those
    # prerequisites are excluded from the count.
    stages_missing_trim: int = 0


class ShooterListResponse(BaseModel):
    """GET /api/match/shooters payload (#324)."""

    match_root: str
    match_name: str
    shooters: list[ShooterListEntry]


class AddShooterRequest(BaseModel):
    """POST /api/match/shooters body (#324)."""

    name: str
    division: str | None = None


class CompareShotPoint(BaseModel):
    """One shot for a shooter on a stage (#328 timeline)."""

    shot_number: int
    time_after_beep: float  # seconds since beep (primary stage clock)
    source: Literal["detected", "manual"]


class CompareShooterRecord(BaseModel):
    """One shooter's data for a stage (#328 multi-shooter timeline)."""

    slug: str
    name: str
    # Path to the lossless trim clip on disk; the SPA streams via
    # GET /api/match/shooters/{slug}/videos/stream?path=...
    video_path: str | None
    # Trim is per-shooter and starts at ``beep_offset_in_clip`` seconds
    # before the beep; SPA uses this to sync all clips to time-since-beep.
    beep_offset_in_clip: float | None
    duration_seconds: float | None
    stage_time_seconds: float | None
    shots: list[CompareShotPoint]


class CompareStageResponse(BaseModel):
    """GET /api/match/stage/{stage_number}/compare payload (#328)."""

    stage_number: int
    stage_name: str
    shooters: list[CompareShooterRecord]


class BeepQueueAltCandidate(BaseModel):
    """One alternative beep candidate carried on a queue item (#326)."""

    time: float
    confidence: float | None


class BeepQueueItem(BaseModel):
    """One pending beep review item (#326)."""

    slug: str
    shooter_name: str
    stage_number: int
    stage_name: str
    video_id: str
    video_path: str
    beep_time: float | None
    beep_confidence: float | None
    beep_reviewed: bool
    status: Literal["missing", "low_confidence", "unreviewed", "confirmed"]
    alt_candidates: list[BeepQueueAltCandidate]
    # Auto-computed cross-align suggestion for secondaries lives on the
    # shooter's other videos; the SPA fetches them lazily if needed.


class BeepQueueStageGroup(BaseModel):
    """All pending beep items for a single stage, across shooters (#326)."""

    stage_number: int
    stage_name: str
    items: list[BeepQueueItem]
    total_primaries: int
    confirmed: int


class BeepQueueResponse(BaseModel):
    """GET /api/match/beep-queue payload (#326)."""

    total_items: int
    pending_count: int
    confirmed_count: int
    stages: list[BeepQueueStageGroup]


class BeepQueueConfirmRequest(BaseModel):
    """POST /api/match/beep-queue/confirm body (#326)."""

    slug: str
    stage_number: int
    video_id: str
    # The user-confirmed beep time. When None the existing detected beep
    # is kept and only ``reviewed`` flips to True.
    time: float | None = None
    source: Literal["detected", "manual", "alt"] = "detected"


class MergePlanShooterMove(BaseModel):
    """One source legacy project routed into a shooter slot in the planned
    merge. Mirrors :class:`match_model.ShooterMove` with paths as strings
    for the JSON wire."""

    source_root: str
    slug: str
    destination_root: str
    competitor_name: str


class MergePlanStage(BaseModel):
    """One reconciled stage in the planned merge."""

    stage_number: int
    stage_name: str
    expected_rounds: int | None = None
    placeholder: bool = False


class MergePlanRequest(BaseModel):
    """POST /api/match/merge/plan body (#332)."""

    inputs: list[str]
    output: str | None = None
    name: str | None = None


class MergePlanResponse(BaseModel):
    """Successful response from /api/match/merge/plan. Conflicts come back
    as HTTP 409 with the conflict message in ``detail``; this model only
    describes the *valid* outcome the user is being asked to confirm."""

    output_root: str
    name: str
    scoreboard_match_id: str | None
    scoreboard_content_type: int | None
    match_date: str | None
    stages: list[MergePlanStage]
    shooter_moves: list[MergePlanShooterMove]


class MergeExecuteRequest(BaseModel):
    """POST /api/match/merge/execute body."""

    inputs: list[str]
    output: str
    name: str | None = None
    move: bool = False


class DeveloperStepCounts(BaseModel):
    """Counts shown on each step of the dev-mode workflow stepper.

    The ``validate_runs`` field is named to avoid shadowing
    ``BaseModel.validate``; the SPA exposes it as ``step_counts.validate_runs``.
    """

    corpus: int
    review: int
    validate_runs: int
    retrain: int


class DeveloperModelInfo(BaseModel):
    """Active ensemble + workflow status. Drives the dev-shell model chip
    and the per-step counter badges on the sidebar."""

    active_version: str
    recall: float
    precision: float | None
    f1: float | None
    fixture_count: int
    built_at: str | None = None
    step_counts: DeveloperStepCounts


class DevReviewQueueItem(BaseModel):
    """One queue row for the dev review surface."""

    slug: str
    audit_path: str
    source: Literal["match", "github", "ad-hoc"]
    source_label: str
    status: Literal["pending", "flagged", "done"]
    n_shots: int
    n_disagreements: int
    promoted_at: str | None = None
    venue: str | None = None
    stage_number: int | None = None
    shooter: str | None = None
    age_seconds: int | None = None


class DevReviewQueueResponse(BaseModel):
    pending: list[DevReviewQueueItem]
    flagged: list[DevReviewQueueItem]
    done: list[DevReviewQueueItem]


class BindRecentProjectRequest(BaseModel):
    """Body for POST /api/user/recent-projects/bind.

    The server binds in-memory only; on next launch the user reopens via
    the same picker. ``name`` is optional -- the server reads the
    on-disk display name from ``project.json`` when available.
    ``create=true`` opts into scaffolding a brand-new project at ``path``
    (mkdir + MatchProject.init); the default of ``false`` keeps the
    existing strict "open only" behaviour so a typo can't silently
    create an empty project under a wrong path.
    """

    path: str
    name: str | None = None
    create: bool = False


# Request bodies ----------------------------------------------------------


class ScoreboardImportRequest(BaseModel):
    data: dict[str, Any]
    overwrite: bool = False


class ForgetRecentProjectRequest(BaseModel):
    """Body for POST /api/user/recent-projects/forget (#75)."""

    path: str


class AttachRawVideoRequest(BaseModel):
    """Body for POST /api/shooters/{slug}/raw-videos/attach (doc 05).

    The SPA passes back the shape ``POST /api/me/raw/upload`` returned
    plus an optional ``covers_stages``. ``filename`` round-trips through
    ``_sanitize_raw_filename`` so a malicious request body can't escape
    the user's ``raw/`` prefix. ``covers_stages`` is optional -- callers
    can leave it null and run auto-match later, or pre-declare it when
    they already know which stages the recording spans.
    """

    filename: str
    sha256: str | None = None
    size_bytes: int | None = None
    covers_stages: list[int] | None = None


class MultipartCreateRequest(BaseModel):
    """Body for POST /api/me/raw/upload/multipart/create (#467).

    Only the filename is needed; the server sanitizes it, mints the R2
    multipart upload, and tells the client the part size to chunk with.
    Everything downstream keys off ``filename`` + ``upload_id`` (the
    server re-derives the storage key), so a request body can't point the
    upload outside the user's ``raw/`` prefix.
    """

    filename: str


class MultipartPartUrlRequest(BaseModel):
    """Body for POST /api/me/raw/upload/multipart/part-url (#467)."""

    filename: str
    upload_id: str
    part_number: int


class MultipartPart(BaseModel):
    """One finished part: its 1-based number + the ETag R2 returned."""

    part_number: int
    etag: str


class MultipartCompleteRequest(BaseModel):
    """Body for POST /api/me/raw/upload/multipart/complete (#467)."""

    filename: str
    upload_id: str
    parts: list[MultipartPart]


class MultipartAbortRequest(BaseModel):
    """Body for POST /api/me/raw/upload/multipart/abort (#467)."""

    filename: str
    upload_id: str


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


class RelinkEntryResponse(BaseModel):
    video_id: str
    name: str
    link_path: str
    current_target: str | None
    current_status: str
    candidates: list[str]
    chosen_path: str | None
    ambiguous: bool
    found: bool


class RelinkScanResponse(BaseModel):
    search_root: str
    entries: list[RelinkEntryResponse]


class RelinkAppliedEntry(BaseModel):
    video_id: str
    name: str
    link_path: str
    previous_target: str | None
    new_target: str


class RelinkApplyResponse(BaseModel):
    applied: list[RelinkAppliedEntry]


class LinkStatusEntry(BaseModel):
    video_id: str
    name: str
    link_path: str
    current_target: str | None
    status: str


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


class BeepOverrideRequest(BaseModel):
    beep_time: float | None  # None clears the override


class StageTimeRequest(BaseModel):
    """Body for POST /api/stages/{n}/time.

    Manual stage-duration entry for projects without scoreboard data.
    ``time_seconds = None`` clears back to placeholder (0.0). Positive
    values are taken as authoritative and stamped with
    ``time_seconds_manual=True`` so a later scoreboard sync won't
    clobber them.
    """

    time_seconds: float | None


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


class CameraMountRequest(BaseModel):
    """Body for PATCH /api/stages/{n}/videos/{vid}/camera-mount (#143).

    Override the heuristic ``camera_mount`` stamped at register time.
    Drives the per-camera-class threshold dispatch in the 4-voter
    ensemble. ``mount`` accepts any fixture-schema ``CameraMount``
    string ("head", "chest", "helmet", "belt", "hand", "gimbal",
    "tripod", "monopod") or ``None`` to clear back to the heuristic
    default.
    """

    mount: str | None


class CameraModelRequest(BaseModel):
    """Body for PATCH /api/stages/{n}/videos/{vid}/camera-model (#303-followup).

    Override the ffprobed ``camera_make`` / ``camera_model``. Both must
    be supplied together or both ``None`` (clearing back to the probe
    default). Drives the per-camera-model within-stage amplitude floor
    dispatch (#304) -- known calibrated models get their tuned floor,
    unknown values fall back to the generic-headcam default.
    """

    make: str | None
    model: str | None


class CalibratedCameraModel(BaseModel):
    """One row in the dropdown the SPA presents on the Ingest screen."""

    key: str
    make: str
    model: str
    amp_floor: float


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
    # the render is per-frame PIL + ffmpeg -- non-trivially slower than
    # the other writers. The Analysis & Export checkbox opts-in per stage.
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


class CoachShotPatchRequest(BaseModel):
    """Body for PATCH /api/stages/{n}/shots/{shot_number}/coach (issue #161).

    Each field is independently optional. Use ``clear_class`` / ``clear_note``
    to drop a previously-set value. ``interval_class`` and
    ``interval_class_source`` must be set together when present.
    """

    interval_class: IntervalClass | None = None
    interval_class_source: IntervalClassSource | None = None
    clear_class: bool = False
    improvement_flag: bool | None = None
    coaching_note: str | None = None
    clear_note: bool = False


class CleanupRequest(BaseModel):
    """Body for POST /api/project/cleanup.

    The server re-plans server-side from this list rather than trusting a
    client-supplied path list -- a malicious or buggy client cannot ask
    us to delete arbitrary files outside the project's known buckets.
    """

    categories: list[cleanup_module.CleanupCategory]


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


async def _register_match_at(
    state: AppState,
    root: Path,
    fallback_name: str,
) -> tuple[str, str | None]:
    """Validate ``root`` is a Match folder, record its open in
    ``~/.splitsmith/projects.json``, load its env files, and pin its
    ``match_id`` into :attr:`AppState.matches`. Returns
    ``(display_name, match_id)``.

    Tier 1 step 4 of doc 10: this used to bind the match on the
    process singleton. The singleton is gone -- a request resolves
    its match via the URL prefix instead, and the SPA navigates to
    ``/match/<match_id>`` after this call returns.

    ``root`` must be a Match folder (carries ``match.json``). The
    legacy single-shooter layout was retired in step 3. Non-match
    paths return 400 ``not_a_match``.
    """
    resolved = root.resolve()
    if not match_model.is_match_folder(resolved):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "not_a_match",
                "message": (
                    f"{resolved} is not a Match folder. Use the "
                    "create-match flow (POST /api/match/create-manual or "
                    "/api/match/create-from-scoreboard) to scaffold one."
                ),
            },
        )
    try:
        match = match_model.Match.load(resolved)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=400,
            detail=f"failed to load match at {resolved}: {exc}",
        ) from exc
    # Hosted: the on-disk match.json is the stale shell Match.init wrote
    # at creation -- the authoritative doc (with stages + shooters) lives
    # in Postgres because the creation saves were store-bound. Reload it
    # so the ``match_empty`` check and the registration name see the real
    # roster. ``match_id`` itself is correct in the file (assigned before
    # binding), so it keys the lookup.
    if state.project_state is not None and match.match_id:
        doc, _ = await state.project_state.load_match(match.match_id)
        if doc is not None:
            match = match_model.Match.model_validate(doc)
    if not match.shooters:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "match_empty",
                "message": (f"Match {resolved} has no shooters yet. " "Add a shooter or recreate the match."),
            },
        )
    name = match.name or fallback_name
    # Record the match_id so the hosted picker/bind can resolve this match
    # through Postgres on a later open, even after the ephemeral on-disk
    # path is gone (a redeploy wiped it). Local mode stores it too; the
    # filesystem flow there just doesn't need it.
    await state.recent_projects.record_open(resolved, name, kind="match", match_id=match.match_id)
    loaded_env = _load_env_files(resolved)
    if loaded_env:
        logger.info("Loaded env from %s", ", ".join(str(p) for p in loaded_env))
    if match.match_id:
        state.matches.register(match.match_id, resolved)
        # Hosted: record the match in Postgres so a separate worker can
        # resolve this ``match_id``. A job is only submittable from an open
        # match, so this runs before any worker touches it. The match /
        # project / audit docs themselves now live in ``state_docs`` (the
        # creation saves were store-bound), so the old S3 JSON seeding is
        # gone -- the worker reads the docs from Postgres via its tenant
        # ``project_state``.
        if state.matches_store is not None:
            await state.matches_store.upsert(match.match_id, name, f"matches/{match.match_id}")
    return name, match.match_id


def _resolve_create_target(
    state: AppState,
    *,
    project_folder: str | None,
    name: str,
) -> Path:
    """Resolve the on-disk match folder for ``POST /api/match/create-*``.

    - **Local mode**: ``project_folder`` is required (the user picks
      where the match lands on their disk). 400 if missing/blank.
    - **Hosted mode**: ``project_folder`` may be omitted; the server
      synthesises ``<SPLITSMITH_PROJECTS_DIR>/users/<user_id>/projects/<slug>/``
      so the SPA never has to expose a host filesystem picker (#425).
      If a hosted client *does* send a path, it's honoured -- the
      hosted UI just doesn't expose the input.

    Duplicate-name dedupe is handled by the existing
    ``match_already_exists`` check downstream, so this function only
    produces a candidate path.
    """
    if project_folder and project_folder.strip():
        return Path(project_folder).expanduser()

    if not _hosted_mode_active():
        raise HTTPException(
            status_code=400,
            detail="project_folder is required in local mode",
        )

    root = Path(os.environ.get(SPLITSMITH_PROJECTS_DIR_ENV, "").strip() or SPLITSMITH_PROJECTS_DIR_DEFAULT)
    tenant = current_tenant.get()
    user_id = tenant.user_id if tenant is not None else None
    if not user_id:
        # The auth gate pins ``current_tenant`` for every authenticated
        # hosted request before the handler runs; reaching here without one
        # means an unauthenticated request slipped through. Failing loud is
        # preferable to silently writing to a shared / wrong prefix.
        raise HTTPException(
            status_code=500,
            detail="hosted mode active but no authenticated tenant is bound",
        )
    slug = match_model._slugify(name)
    return root / "users" / user_id / "projects" / slug


def _enrich_recent_project(rp: user_config.RecentProject) -> RecentProjectDetail:
    """Read on-disk metadata for one recent-project entry.

    Returns a :class:`RecentProjectDetail` that the redesigned match
    picker (#322) renders directly. Best-effort: anything we can't
    answer leaves the corresponding field at its default. Errors never
    propagate -- a stale entry shows up as ``kind="missing"`` so the
    user can prune it.
    """
    detail = RecentProjectDetail(
        path=rp.path,
        name=rp.name,
        last_opened_at=rp.last_opened_at,
        kind="unknown",
    )
    path = Path(rp.path)
    if not path.exists():
        detail.kind = "missing"
        return detail

    try:
        kind, _ = match_model.from_path(path)
    except FileNotFoundError:
        detail.kind = "unknown"
        return detail

    try:
        if kind == "match":
            match = match_model.Match.load(path)
            detail.kind = "match"
            detail.name = match.name or rp.name
            detail.match_id = match.match_id
            detail.shooter_count = len(match.shooters)
            detail.stage_count = len(match.stages)
            detail.match_date = match.match_date.isoformat() if match.match_date else None
            detail.manual = match.scoreboard_match_id is None
            # Audited = average audited-stage count across shooters.
            # "Audited" comes from :func:`stage_audit_status` -- the
            # operator has to hit Save & next at least once on the
            # stage for it to count. Skipped stages don't qualify.
            audited_total = 0
            video_total = 0
            shooter_names: list[str] = []
            for slug in match.shooters:
                shooter_root = match_model.Match.shooter_root(path, slug)
                try:
                    shooter = match.load_shooter(path, slug)
                except (FileNotFoundError, KeyError):
                    shooter_names.append(slug)
                    continue
                try:
                    legacy = MatchProject.load(shooter_root)
                except FileNotFoundError:
                    shooter_names.append(shooter.name or slug)
                    continue
                audited_total += legacy.audited_count(shooter_root)
                video_total += len(legacy.all_videos())
                shooter_names.append(shooter.name or slug)
            detail.shooter_names = shooter_names
            detail.stages_audited = audited_total // max(len(match.shooters), 1) if match.shooters else 0
            detail.video_count = video_total
            metadata_path = path / match_model.MATCH_FILE
        else:
            # Path exists but is not a Match folder -- a pre-Tier-1
            # legacy single-shooter project, or a stale entry. Leave
            # ``kind="unknown"`` and skip the metadata block so the
            # user can prune it from the picker.
            return detail

        if metadata_path.exists():
            mtime = metadata_path.stat().st_mtime
            detail.last_modified_at = datetime.fromtimestamp(mtime, tz=UTC)
    except Exception:  # noqa: BLE001 -- defensive: never break listing
        return detail

    # Derive status. "exported" = every stage audited (heuristic);
    # "archived" = no modification in 180 days;
    # "awaiting_footage" = the operator hasn't attached any footage yet
    # (drives the picker pill + footage-gated menu treatment per #425);
    # "in_progress" otherwise.
    if detail.stage_count > 0 and detail.stages_audited >= detail.stage_count:
        detail.status = "exported"
    elif detail.last_modified_at is not None and (datetime.now(UTC) - detail.last_modified_at).days > 180:
        detail.status = "archived"
    elif detail.kind == "match" and detail.video_count == 0:
        detail.status = "awaiting_footage"
    else:
        detail.status = "in_progress"
    return detail


async def _enrich_recent_project_hosted(
    state: AppState, rp: user_config.RecentProject
) -> RecentProjectDetail | None:
    """Hosted picker detail, read from Postgres instead of the filesystem.

    The recorded ``path`` is an ephemeral container working root a redeploy
    wipes, so the filesystem enricher reports the match as ``missing`` even
    though its state is safe in ``state_docs``. When the row carries a
    ``match_id`` and the match doc is present, build the detail from the
    match + per-shooter project docs. Returns ``None`` to fall back to the
    filesystem path (local mode, or no stored match_id / doc -- a genuinely
    gone match).
    """
    if state.project_state is None or not rp.match_id:
        return None
    match_doc, _ = await state.project_state.load_match(rp.match_id)
    if match_doc is None:
        return None
    detail = RecentProjectDetail(
        path=rp.path,
        name=match_doc.get("name") or rp.name,
        last_opened_at=rp.last_opened_at,
        kind="match",
    )
    detail.match_id = rp.match_id
    shooters = match_doc.get("shooters") or []
    detail.shooter_count = len(shooters)
    detail.stage_count = len(match_doc.get("stages") or [])
    detail.match_date = match_doc.get("match_date")  # JSON doc -> ISO str | None
    detail.manual = match_doc.get("scoreboard_match_id") is None

    names: list[str] = []
    video_total = 0
    for slug in shooters:
        pdoc, _ = await state.project_state.load_project(rp.match_id, slug)
        if pdoc is None:
            names.append(slug)
            continue
        try:
            proj = MatchProject.model_validate(pdoc)
            names.append(proj.competitor_name or slug)
            video_total += len(proj.all_videos())
        except Exception:  # noqa: BLE001 -- never break the listing on one bad doc
            names.append(slug)
    detail.shooter_names = names
    detail.video_count = video_total
    # Per-stage audited counts live in separate state_docs rows; loading
    # them all for a list view isn't worth it, so leave stages_audited at 0
    # (the Audit screen shows the real per-stage status).
    detail.status = "awaiting_footage" if video_total == 0 else "in_progress"
    return detail


SPLITSMITH_MODE_ENV = "SPLITSMITH_MODE"
SPLITSMITH_DATABASE_URL_ENV = "SPLITSMITH_DATABASE_URL"
# Public origin the deployment is reached at (e.g. https://splitsmith.app,
# or http://localhost:5174 for docker-compose). Required in hosted mode: it
# is the base of the magic-link callback URL and decides the session
# cookie's Secure flag (Secure iff the scheme is https).
SPLITSMITH_PUBLIC_URL_ENV = "SPLITSMITH_PUBLIC_URL"
# Magic-link e-mail transport selector. Unset / "console" -> log the link
# (docker / self-host). A provider name selects its HTTP sender. See
# ``splitsmith.db.email.build_email_sender``.
SPLITSMITH_EMAIL_BACKEND_ENV = "SPLITSMITH_EMAIL_BACKEND"
# S3 / R2 / MinIO wiring. ``SPLITSMITH_S3_BUCKET`` is the single
# switch: when set in hosted mode, ``_apply_hosted_mode_wiring``
# constructs an ``S3Storage``. The remaining vars must accompany it
# (the boot fails loud if the bucket is set but creds are missing).
SPLITSMITH_S3_BUCKET_ENV = "SPLITSMITH_S3_BUCKET"
SPLITSMITH_S3_ENDPOINT_URL_ENV = "SPLITSMITH_S3_ENDPOINT_URL"
SPLITSMITH_S3_REGION_ENV = "SPLITSMITH_S3_REGION"
SPLITSMITH_S3_ACCESS_KEY_ID_ENV = "SPLITSMITH_S3_ACCESS_KEY_ID"
SPLITSMITH_S3_SECRET_ACCESS_KEY_ENV = "SPLITSMITH_S3_SECRET_ACCESS_KEY"
# Hosted-mode project-metadata root. ``match.json`` + per-shooter
# JSON still lives on the container's filesystem (raw videos are the
# part that ships to S3 today; persisting match metadata to Postgres
# is doc 09 work). Defaults to the splitsmith user's home so a fresh
# container is writable; production deployments mount a volume at
# this path. The directory layout under here is
# ``users/<user_id>/projects/<slug>/`` -- the same multi-tenant
# prefix S3 storage uses.
SPLITSMITH_PROJECTS_DIR_ENV = "SPLITSMITH_PROJECTS_DIR"
SPLITSMITH_PROJECTS_DIR_DEFAULT = "/home/splitsmith/data"


class _HashingReader:
    """Wrap a binary stream so we hash the bytes as they're consumed.

    boto3's ``upload_fileobj`` reads from this in chunks; we forward
    the chunks to the inner hash object so the server can return a
    sha256 without a second pass. Pairs with ``storage.upload_stream``.
    """

    def __init__(self, inner: BinaryIO, digest: Any) -> None:
        self._inner = inner
        self._digest = digest

    def read(self, size: int = -1) -> bytes:
        chunk = self._inner.read(size)
        if chunk:
            self._digest.update(chunk)
        return chunk


def _sanitize_raw_filename(name: str | None) -> str:
    """Validate a user-supplied upload filename.

    Strict: anything that isn't a clean basename is a 400. Browsers
    pass the result of the OS file picker which is always a basename;
    a request that includes path separators is almost always a client
    bug, and failing loud surfaces it before the storage layer guard
    rewrites the surprise into a confusing 500.
    """
    if not name:
        raise HTTPException(status_code=400, detail="filename is required")
    stripped = name.strip()
    if stripped in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail=f"invalid filename: {name!r}")
    if "/" in stripped or "\\" in stripped:
        raise HTTPException(status_code=400, detail=f"invalid filename: {name!r}")
    # ``Path(name).name`` would silently strip path parts; double-check
    # the identity holds so we don't accept a weirdly-encoded traversal
    # the storage guard would later reject mid-write.
    if Path(stripped).name != stripped:
        raise HTTPException(status_code=400, detail=f"invalid filename: {name!r}")
    return stripped


def _hosted_mode_active() -> bool:
    """Return True when ``SPLITSMITH_MODE=hosted`` is in the environment.

    The single switch that picks Postgres-backed stores + the hosted
    auth bootstrap over the local file-system defaults. Local-mode
    (``splitsmith ui``) leaves the env var unset; ``splitsmith serve``
    sets it before constructing the app.
    """
    return os.environ.get(SPLITSMITH_MODE_ENV, "").strip().lower() == "hosted"


def _apply_hosted_mode_wiring(state: AppState, *, worker: bool = False) -> None:
    """Wire AppState for hosted mode: a per-tenant store factory + the
    process-level resources it needs.

    Runs only when :func:`_hosted_mode_active` is True. Requires
    ``SPLITSMITH_DATABASE_URL`` to point at a Postgres (or SQLite)
    engine the migrations have already applied.

    This does **not** bind the per-user stores to one user at boot. It
    installs :meth:`AppState.build_tenant`, which constructs a fresh
    :class:`TenantContext` for a given ``user_id`` -- the auth gate calls
    it per request, the queue task per job. Each context's stores open
    sessions through :func:`tenant_session_factory`, so the ``app.user_id``
    GUC the RLS policies key on is set per transaction for the right user.

    Auth is :class:`MagicLinkAuth`: real per-user accounts created on first
    sign-in. There is no boot user -- identity is resolved per request from
    the session cookie. The job-body registry lives on
    :attr:`AppState.job_bodies` (process-level, shared by the backing local
    backend and every per-tenant backend), so a per-request backend resolves
    the same ``kind`` -> body map without re-registration and without a boot
    backend to hold it.

    Process-level resources are built once and shared across tenants: the
    engine + session factory, the queue deferrer, and the S3 client (the
    per-user isolation is purely the S3 key prefix + the per-transaction GUC).
    """
    from ..db import (
        MagicLinkAuth,
        PostgresJobBackend,
        PostgresMatchStore,
        PostgresRecentProjectsStore,
        PostgresScoreboardIdentityStore,
        ProjectStateStore,
        build_email_sender,
        build_signup_policy,
        create_engine,
        sessionmaker,
        tenant_session_factory,
    )
    from ..queue import make_deferrer

    url = os.environ.get(SPLITSMITH_DATABASE_URL_ENV)
    if not url:
        raise RuntimeError(
            f"{SPLITSMITH_MODE_ENV}=hosted requires {SPLITSMITH_DATABASE_URL_ENV} "
            "to be set (e.g. postgresql+asyncpg://user:pass@host/db)"
        )
    public_url = os.environ.get(SPLITSMITH_PUBLIC_URL_ENV, "").strip()
    if not public_url:
        raise RuntimeError(
            f"{SPLITSMITH_MODE_ENV}=hosted requires {SPLITSMITH_PUBLIC_URL_ENV} "
            "to be set to the public origin (e.g. https://splitsmith.app, or "
            "http://localhost:5174 for docker-compose). It is the base of the "
            "magic-link callback URL and decides the session cookie's Secure "
            "flag (Secure iff the scheme is https)."
        )
    state.public_base_url = public_url.rstrip("/")
    # Secure cookies require HTTPS; a Secure cookie over http://localhost
    # never round-trips. Derive it from the public URL scheme so prod
    # (https) gets Secure and docker / self-host on http does not -- one
    # source of truth, no separate knob to contradict it.
    state.cookie_secure = public_url.lower().startswith("https://")

    # ``pool_disabled=True`` because the hosted-mode boot path runs
    # multiple short-lived event loops (each worker thread's asyncio.run,
    # the model-prefetch submit). asyncpg connections are loop-bound; a
    # pooled connection from one loop would crash on first reuse from the
    # FastAPI request loop with "attached to a different loop". See
    # ``splitsmith.db.engine.create_engine`` for the full rationale.
    engine = create_engine(url, pool_disabled=True)
    session_factory = sessionmaker(engine)

    # Auth resolves identity from ``users`` / ``sessions`` / ``magic_link_tokens``
    # -- none under RLS -- so it holds the raw (non-tenant) session factory.
    # The email transport is pluggable: console by default (docker / self-host),
    # an HTTP provider when ``SPLITSMITH_EMAIL_BACKEND`` selects one.
    email_sender = build_email_sender(os.environ.get(SPLITSMITH_EMAIL_BACKEND_ENV))
    # Signup gating (anti-spam): open by default; the hosted deploy closes
    # signups to all but an allowlist via SPLITSMITH_SIGNUPS_OPEN=false +
    # SPLITSMITH_SIGNUP_ALLOWLIST. Returning users always sign in.
    signup_policy = build_signup_policy()
    state.auth = MagicLinkAuth(session_factory, email_sender, signup_policy=signup_policy)

    # Process-level, tenant-agnostic resources shared by every
    # ``TenantContext``. The deferrer routes per-user inside ``_defer``
    # (queue name from ``user_id``); the S3 client is stateless w.r.t. the
    # tenant (only the key prefix is per-user, see ``_tenant_s3_storage``).
    deferrer = make_deferrer(url)
    s3_client, s3_bucket = _build_hosted_s3_client()

    def _build_tenant(user_id: str) -> TenantContext:
        # Every per-user store opens its sessions through a tenant-scoped
        # factory that sets the ``app.user_id`` GUC the RLS policies key on
        # (no-op on SQLite). ``sweep_on_boot=False``: a per-request /
        # per-job backend must never fail the in-flight jobs of the user it
        # was just built for. The shared ``state.job_bodies`` registry is
        # injected so a fresh backend resolves the same kinds without
        # re-registration.
        tenant_factory = tenant_session_factory(session_factory, user_id)
        return TenantContext(
            user_id=user_id,
            recent_projects=PostgresRecentProjectsStore(tenant_factory, user_id=user_id),
            scoreboard_identity=PostgresScoreboardIdentityStore(tenant_factory, user_id=user_id),
            jobs=PostgresJobBackend(
                tenant_factory,
                user_id=user_id,
                deferrer=deferrer,
                sweep_on_boot=False,
                bodies=state.job_bodies,
            ),
            matches_store=PostgresMatchStore(tenant_factory, user_id=user_id),
            project_state=ProjectStateStore(tenant_factory, user_id=user_id),
            storage=_tenant_s3_storage(s3_client, s3_bucket, user_id),
        )

    state._build_tenant = _build_tenant
    # No boot job backend / no boot restart sweep: with MagicLinkAuth there
    # is no boot user to bind one to, and an out-of-process Procrastinate
    # worker (not this API process) now owns running jobs, so failing
    # PENDING/RUNNING rows on an API restart would be wrong. The backing
    # ``state._jobs`` stays the local ``JobRegistry`` (bodies shared via
    # ``__post_init__``) and is consulted only for boot-time body
    # registration, never for queries -- every request / job pins a tenant
    # first. Per-tenant restart hygiene (sweep a user's stranded rows on
    # next sign-in) is a tracked follow-up.

    if worker:
        worker_root = Path(
            os.environ.get(SPLITSMITH_PROJECTS_DIR_ENV, "").strip() or SPLITSMITH_PROJECTS_DIR_DEFAULT
        )

        def _resolve_match_for_worker(match_id: str) -> Path | None:
            """Map a queued ``match_id`` to a worker-local working root.

            Reads the job's tenant ``matches_store`` (``current_tenant`` is
            pinned by the queue task before ``run_job``), so resolution is
            scoped to the job's owner. Returns ``None`` (->
            ``MatchNotRegisteredError``, surfaced as a clean job failure)
            when the match isn't in that user's table -- no fallback to the
            local recent-projects scan, which a separate worker process
            can't satisfy anyway. The match's metadata + inputs are mirrored
            down from S3 lazily by the ``state`` accessors once
            ``current_match_root`` points here.
            """
            store = state.matches_store
            if store is None:
                return None
            row = asyncio.run(store.get(match_id))
            if row is None:
                return None
            root = worker_root / match_id
            root.mkdir(parents=True, exist_ok=True)
            return root

        state.matches = MatchRegistry(miss_resolver=_resolve_match_for_worker)


def build_worker_state() -> AppState:
    """Build the hosted ``AppState`` a ``splitsmith worker`` runs jobs against.

    The headless counterpart to ``create_app``'s state wiring: no FastAPI
    app and no routes -- just the compute backend (local ensemble, loaded
    once per process via the ``_ENSEMBLE_RUNTIME`` singleton), the
    hosted-mode Postgres/S3 wiring (execute-only job backend, no boot
    sweep), and the shared job-body registry the Procrastinate
    ``run_compute_job`` task dispatches against. Requires hosted-mode env
    (``SPLITSMITH_DATABASE_URL``); ``_apply_hosted_mode_wiring`` raises
    otherwise.
    """
    state = AppState()
    state.compute = LocalComputeBackend(runtime_loader=lambda: _get_ensemble_runtime())
    _apply_hosted_mode_wiring(state, worker=True)
    register_job_bodies(state)
    return state


def _build_hosted_s3_client() -> tuple[Any, str | None]:
    """Build the process-level S3 client + bucket from ``SPLITSMITH_S3_*``,
    or ``(None, None)`` if the bucket is unset.

    Built once at hosted-mode wiring and shared across tenants -- a boto3
    client is thread-safe for calls and stateless w.r.t. the tenant (the
    per-user isolation is purely the key prefix, applied per request by
    :func:`_tenant_s3_storage`). Constructing a client per request would
    add tens of milliseconds (boto3 parses service models on creation) to
    every ``/api/*`` call.

    Hosted mode without S3 wiring is a valid configuration -- a developer
    iterating on the Postgres-backed stores doesn't need MinIO running
    just to hit ``/api/me/recent-projects``. The upload endpoint guards on
    ``state.storage is None`` and returns a 503, so the contract is "set
    the bucket to get upload support, leave it unset to disable it".

    When the bucket *is* set, the rest of the credentials must be present
    too -- a half-configured S3 wiring would crash on the first request,
    which is worse than failing loud at boot.
    """
    bucket = os.environ.get(SPLITSMITH_S3_BUCKET_ENV, "").strip()
    if not bucket:
        return None, None
    endpoint = os.environ.get(SPLITSMITH_S3_ENDPOINT_URL_ENV, "").strip() or None
    region = os.environ.get(SPLITSMITH_S3_REGION_ENV, "").strip() or "auto"
    access_key = os.environ.get(SPLITSMITH_S3_ACCESS_KEY_ID_ENV, "").strip()
    secret_key = os.environ.get(SPLITSMITH_S3_SECRET_ACCESS_KEY_ENV, "").strip()
    if not access_key or not secret_key:
        raise RuntimeError(
            f"{SPLITSMITH_S3_BUCKET_ENV} is set but "
            f"{SPLITSMITH_S3_ACCESS_KEY_ID_ENV} / "
            f"{SPLITSMITH_S3_SECRET_ACCESS_KEY_ENV} are missing"
        )

    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    return client, bucket


def _tenant_s3_storage(client: Any, bucket: str | None, user_id: str) -> Storage | None:
    """Wrap the shared S3 client in a per-user :class:`S3Storage`, or
    ``None`` when S3 is unconfigured.

    The per-user prefix is the multi-tenant isolation boundary: every
    caller gets their own ``users/<id>/`` namespace and can't construct a
    key that escapes it (the path-traversal guard inside ``S3Storage._key``
    enforces this). Cheap -- it only wraps an already-built client.
    """
    if client is None or bucket is None:
        return None
    from ..storage import S3Storage

    return S3Storage(bucket=bucket, prefix=f"users/{user_id}/", client=client)


def create_app(
    *,
    project_root: Path | None = None,
    project_name: str | None = None,
    lab_enabled: bool = False,
) -> FastAPI:
    """Create the FastAPI app, optionally pre-bound to one match project.

    When ``project_root`` is omitted the server boots **unbound**: the
    picker endpoints (recent projects, fs/list, project bind) work; every
    other project-bound endpoint returns 409 ``no_project`` so the SPA
    can redirect to its picker route. The user binds a project at runtime
    via POST /api/user/recent-projects/bind.

    When bound, the project is initialised on first call (idempotent),
    then the app keeps the root path and re-loads on every request. We
    avoid caching the model in memory so external edits to
    ``project.json`` are visible without restart.

    ``lab_enabled`` gates the ``/api/lab/*`` routes (and the SPA reads
    ``/api/server/features`` on mount to know whether to show the Lab
    nav entry). Hidden by default; opt in via ``splitsmith ui --lab``.
    """
    # Initialise Sentry before constructing FastAPI so its integrations can
    # hook the app. No-op unless ``SENTRY_DSN`` is set (local installs and the
    # test suite stay untouched); see ``splitsmith.observability.init_sentry``.
    init_sentry(component="web")
    state = AppState()
    # Route the compute backend through the module-level lazy loader so
    # the shared cache + existing test monkeypatches on
    # ``_get_ensemble_runtime`` still apply. The lambda re-resolves the
    # name on every call so ``monkeypatch.setattr(server, "_get_ensemble_runtime", ...)``
    # in tests takes effect even though the backend was constructed
    # before the patch ran. When the hosted backend lands this swap
    # point is where the picker would inject a ``RemoteComputeBackend``.
    state.compute = LocalComputeBackend(runtime_loader=lambda: _get_ensemble_runtime())
    # Hosted-mode swap: when ``SPLITSMITH_MODE=hosted``, replace the
    # local-mode auth + per-user stores + job backend with their
    # Postgres-backed equivalents. Must happen before the recent-
    # projects refresh below -- that call goes through ``state.matches``
    # which itself reads from ``state.recent_projects`` (the local
    # JSON file in local mode; Postgres in hosted mode).
    if _hosted_mode_active():
        _apply_hosted_mode_wiring(state)
    # Populate the match-id registry from recent projects so id -> path
    # lookups resolve without waiting for a picker bind. Cheap (one
    # match.json read per recent entry; legacy projects are skipped).
    # Pre-v4 match.json files get the id assigned + persisted here on
    # the fly via Match.load's load-time migration.
    state.matches.refresh_from_recent_projects()
    # Load .env / .env.local before binding so SPLITSMITH_SSI_TOKEN (and
    # friends) are available on the unbound boot path too -- the
    # create-match-from-scoreboard flow needs the token before any
    # project exists. Bound binds re-call this with their project root
    # so per-project overrides still win.
    _load_env_files(project_root)
    if project_root is not None:
        # ``create_app`` runs at boot, outside any event loop. The
        # async store call is wrapped in ``asyncio.run`` so the
        # ``--project`` startup hook still drops the match into
        # recent-projects + the alias middleware.
        asyncio.run(
            _register_match_at(
                state,
                project_root,
                fallback_name=project_name or project_root.name or "match",
            )
        )

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

    # Kick off the slim ONNX prefetch in the background if any artifact
    # is missing. Runs whether or not a project is bound, so the first
    # shot-detect after the user picks a match finds the cache primed.
    # ``create_app`` runs at boot outside any event loop, so the async
    # JobBackend submission is wrapped in ``asyncio.run`` here.
    #
    # All job bodies register here, after any hosted-mode backend swap
    # above, so ``submit(kind=...)`` resolves against whichever backend is
    # bound. In hosted mode ``state.jobs`` (no ``current_tenant`` at boot)
    # falls back to the backing backend that holds the shared body
    # registry, so every per-request backend resolves these kinds. Must
    # precede the prefetch submit below. ``register_job_bodies`` is shared
    # with the Procrastinate worker bootstrap.
    register_job_bodies(state)
    # The slim-ONNX prefetch is a local-mode convenience: it submits a
    # ``model_download`` job when artifacts are missing. Hosted mode bakes
    # the slim models into the image (nothing to fetch) and has no boot
    # tenant to own the job -- model prefetch is a process concern, not a
    # per-user one -- so skip it there entirely rather than route an
    # ownerless job through the per-tenant ``state.jobs`` property.
    if not _hosted_mode_active():
        asyncio.run(_maybe_submit_model_download(state))

    async def get_current_user(request: Request) -> User:
        """FastAPI dependency: resolve the operator behind a request.

        Every ``/api/me/*`` handler depends on this so the auth gate
        lives in the request pipeline, not in handler bodies. In local
        mode ``LoopbackAuth`` always returns the sentinel user (no 401
        path is ever exercised); in hosted mode an unauthenticated
        request short-circuits here before any handler runs.
        """
        # The auth gate already resolved + stashed the user for gated
        # ``/api/*`` requests; reuse it to avoid a second
        # ``authenticate_request`` (a session + user DB lookup in hosted
        # mode). Fall back to resolving for any caller not behind the gate.
        user = getattr(request.state, "user", None)
        if user is None:
            user = await state.auth.authenticate_request(request)
        if user is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        return user

    # ----------------------------------------------------------------------
    # Magic-link auth routes (hosted mode only)
    # ----------------------------------------------------------------------
    #
    # The passwordless sign-in surface. All three 404 in local mode --
    # ``LoopbackAuth`` has no login flow and the SPA renders no login UI
    # there. The session cookie is httpOnly + SameSite=Lax + Secure (the
    # last derived from the public URL scheme, so it round-trips over
    # docker-compose http but is set in production https). SameSite=Lax is
    # the CSRF baseline -- the cookie isn't sent on cross-site POSTs;
    # double-submit CSRF tokens are a tracked follow-up.

    def _set_session_cookie(response: Response, secret: str, expires_at: datetime) -> None:
        max_age = max(0, int((expires_at - datetime.now(UTC)).total_seconds()))
        from ..db import SESSION_COOKIE_NAME

        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=secret,
            max_age=max_age,
            httponly=True,
            secure=state.cookie_secure,
            samesite="lax",
            path="/",
        )

    @app.post("/api/v1/auth/begin")
    async def _auth_begin(payload: AuthBeginRequest) -> JSONResponse:
        """Start a magic-link sign-in: e-mail a link to ``payload.email``.

        Always 200 regardless of whether the address has an account -- the
        account is created on redemption and we never reveal existence.
        """
        if not _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        email = payload.email.strip()
        if not email or "@" not in email:
            raise HTTPException(status_code=400, detail="a valid email is required")
        await state.auth.begin_login(email, base_url=state.public_base_url)
        return JSONResponse({"ok": True})

    @app.get("/auth/callback")
    async def _auth_callback(token: str, request: Request) -> Response:
        """Redeem a magic-link token: set the session cookie + redirect.

        On any invalid token (unknown / expired / already used) redirects
        to ``/login?error=invalid_link`` with one generic reason -- the
        backend never discloses which links exist.
        """
        if not _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        from ..db import InvalidMagicLinkError

        try:
            issued = await state.auth.complete_login(
                token,
                user_agent=request.headers.get("user-agent"),
                ip=request.client.host if request.client else None,
            )
        except InvalidMagicLinkError:
            return RedirectResponse(url="/login?error=invalid_link", status_code=303)
        except Exception:
            # ``/auth/callback`` is a top-level browser navigation, so an
            # unhandled error here is a bare 500 page mid sign-in (and the
            # token may already be consumed). Redirect to the login surface
            # with a generic reason instead -- the user can request a new
            # link. Logged for diagnosis; the reason isn't disclosed.
            logger.exception("magic-link callback failed")
            return RedirectResponse(url="/login?error=invalid_link", status_code=303)
        response = RedirectResponse(url="/", status_code=303)
        _set_session_cookie(response, issued.secret, issued.expires_at)
        return response

    @app.post("/api/v1/auth/logout")
    async def _auth_logout(request: Request, user: User = Depends(get_current_user)) -> JSONResponse:
        """Revoke the current session + clear the cookie. Auth-gated, so an
        anonymous caller 401s before reaching here."""
        from ..db import SESSION_COOKIE_NAME

        cookie = request.cookies.get(SESSION_COOKIE_NAME)
        if cookie:
            await state.auth.end_session(cookie)
        response = JSONResponse({"ok": True})
        # Clear with the same attributes the cookie was set with -- stricter
        # browsers match name + path + Secure/SameSite when deleting, and a
        # mismatch can leave a Secure cookie in place. (The session is already
        # revoked server-side, so a stale cookie is benign, but clear cleanly.)
        response.delete_cookie(
            SESSION_COOKIE_NAME,
            path="/",
            httponly=True,
            secure=state.cookie_secure,
            samesite="lax",
        )
        return response

    @app.exception_handler(ShutdownInProgressError)
    async def _shutdown_in_progress_handler(request: Request, exc: ShutdownInProgressError) -> JSONResponse:
        """Map ShutdownInProgressError to 503 across every submit() callsite."""
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": "shutting_down", "message": str(exc)}},
        )

    # Optimistic-locking conflict on a hosted state_docs save -> 409 so the
    # SPA can reload + retry. Registered only when the db layer imports
    # (hosted, or any dev env with the deps); local slim installs never
    # raise it -- ``project_state`` is None and every save is file-based.
    try:
        from ..db import StateConflictError

        _have_state_conflict = True
    except Exception:  # noqa: BLE001 -- slim local install lacks the db deps
        _have_state_conflict = False
    if _have_state_conflict:

        @app.exception_handler(StateConflictError)
        async def _state_conflict_handler(request: Request, exc: Exception) -> JSONResponse:
            """Map a lost optimistic-lock race to 409 ``version_conflict``."""
            return JSONResponse(
                status_code=409,
                content={
                    "detail": {
                        "code": "version_conflict",
                        "message": ("this match state changed since you loaded it; " "reload and try again"),
                    }
                },
            )

    # ----------------------------------------------------------------------
    # /api/matches/{match_id}/... alias middleware (#353 Phase 3 PR B/C)
    # ----------------------------------------------------------------------
    #
    # The SPA's URLs live under ``/match/:matchId/`` so each tab can pin
    # its own match. The corresponding API prefix is accepted by this
    # one middleware:
    #
    # 1. Validate ``match_id`` against the registry (404 ``match_not_found``
    #    when unknown).
    # 2. Set the ``current_match_root`` / ``current_match_id`` ContextVars
    #    for the lifetime of the request so ``state.shooter_root`` can
    #    resolve against the URL's match rather than the singleton.
    # 3. Strip ``matches/{match_id}/`` and forward to the existing route
    #    table -- 69 endpoint decorators are untouched.
    #
    # Two concurrent requests with different match ids stay isolated
    # because each runs in its own contextvar scope. The singleton
    # ``state._bound_root`` is only consulted for legacy bare-path
    # traffic that hasn't migrated to the new prefix.
    @app.middleware("http")
    async def _match_id_alias(request, call_next):
        path = request.url.path
        prefix = "/api/matches/"
        if not path.startswith(prefix):
            return await call_next(request)
        remainder = path[len(prefix) :]
        match_id, sep, rest = remainder.partition("/")
        if not sep or not match_id:
            return JSONResponse(
                status_code=400,
                content={
                    "detail": {
                        "code": "match_id_required",
                        "message": "expected /api/matches/{match_id}/...",
                    }
                },
            )
        # Tenant ownership gate (hosted mode). The in-memory ``MatchRegistry``
        # below is process-global and keyed by ULID, so without this a user
        # could resolve another user's match path by guessing its id. The
        # per-tenant ``matches_store`` is RLS-scoped, so a match the current
        # tenant doesn't own returns None here -> the same 404 an unknown id
        # gets (existence and ownership are deliberately indistinguishable).
        # ``current_tenant`` is pinned by the outer auth gate before this
        # middleware runs. Local mode (no ``matches_store``) skips the check
        # -- one operator, nothing to isolate.
        owner_store = state.matches_store
        if owner_store is not None:
            # Hosted: a match's authoritative state is Postgres (this row) +
            # S3 (its files). The in-memory ``MatchRegistry`` is process-local
            # and empty after a redeploy or on a second replica, so it can't
            # be the source of truth for existence -- relying on it 404'd
            # every match URL after a deploy until the picker re-registered.
            # Instead: confirm ownership in the RLS-scoped store, then
            # establish a deterministic local working root the ``state``
            # accessors mirror match.json / project.json down into (S3 is
            # authoritative). This makes match resolution stateless across
            # restarts + replicas. A match the tenant doesn't own returns
            # None -> the same 404 an unknown id gets (existence and
            # ownership stay indistinguishable).
            if await owner_store.get(match_id) is None:
                return JSONResponse(
                    status_code=404,
                    content={
                        "detail": {
                            "code": "match_not_found",
                            "message": f"unknown match_id {match_id!r}",
                        }
                    },
                )
            work_root = (
                Path(
                    os.environ.get(SPLITSMITH_PROJECTS_DIR_ENV, "").strip() or SPLITSMITH_PROJECTS_DIR_DEFAULT
                )
                / match_id
            )
            work_root.mkdir(parents=True, exist_ok=True)
            state.matches.register(match_id, work_root)
            match_root = work_root.resolve()
        else:
            # Local mode: one operator, no tenancy. Resolve via the local
            # recent-projects scan (the registry's miss_resolver is None).
            try:
                match_root = state.matches.resolve(match_id)
            except KeyError:
                return JSONResponse(
                    status_code=404,
                    content={
                        "detail": {
                            "code": "match_not_found",
                            "message": f"unknown match_id {match_id!r}",
                        }
                    },
                )
        rewritten = "/api/" + rest
        request.scope["path"] = rewritten
        request.scope["raw_path"] = rewritten.encode("utf-8")
        root_token = current_match_root.set(match_root)
        id_token = current_match_id.set(match_id)
        try:
            return await call_next(request)
        finally:
            current_match_root.reset(root_token)
            current_match_id.reset(id_token)

    # ----------------------------------------------------------------------
    # Auth gate middleware (saas-readiness step)
    # ----------------------------------------------------------------------
    #
    # Every ``/api/*`` request is resolved through ``state.auth`` before
    # the route handler runs. ``LoopbackAuth`` always returns a user, so
    # in local mode this middleware never 401s -- the wiring exists so a
    # hosted-mode backend can swap in and the 401 path activates without
    # touching any route. Non-``/api/*`` paths (SPA static, ``/docs``,
    # ``/openapi.json``) pass through untouched -- auth happens at the
    # API layer, not the asset layer. The allowlist below names the few
    # ``/api/*`` endpoints that must stay anonymous: process health and
    # the feature flag the SPA reads on mount (both are needed before a
    # user is established) plus ``/api/shutdown`` (which has its own
    # loopback gate that's stricter than auth).
    #
    # In hosted mode this gate also pins ``current_tenant`` for the
    # resolved user before the handler runs, so the per-user ``AppState``
    # store properties resolve to that user's Postgres/S3 stores. The set
    # happens *before* ``call_next`` (same forward-propagation contract the
    # ``_match_id_alias`` middleware relies on) and is reset in ``finally``.
    # Local mode leaves it unset -- there is one operator and the
    # properties fall back to the process singletons.
    hosted = _hosted_mode_active()

    @app.middleware("http")
    async def _auth_gate(request, call_next):
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        if path in _PUBLIC_API_PATHS:
            return await call_next(request)
        user = await state.auth.authenticate_request(request)
        if user is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "not authenticated"},
            )
        # Stash the resolved user on the request so ``get_current_user``
        # (depended on by ~20 handlers) reuses it instead of issuing a
        # second ``authenticate_request`` -- a redundant session + user
        # lookup per request in hosted mode. The scope is shared with the
        # endpoint, so this propagates downstream.
        request.state.user = user
        if not hosted:
            return await call_next(request)
        tenant_token = current_tenant.set(state.build_tenant(user.id))
        try:
            return await call_next(request)
        finally:
            current_tenant.reset(tenant_token)

    # ----------------------------------------------------------------------
    # API
    # ----------------------------------------------------------------------

    def _local_match_path(root: Path) -> Path:
        """Resolve ``<root>/scoreboard/match.json`` (offline source path)."""
        return root / DEFAULT_SCOREBOARD_DIRNAME / DEFAULT_MATCH_FILENAME

    def _resolve_scoreboard_client(root: Path) -> ScoreboardClient:
        """Pick the concrete ``ScoreboardClient`` for this shooter root.

        Local JSON wins when present so the user can stay fully offline by
        dropping a file. Otherwise we wrap the HTTP client in the project-
        local cache so a second open of the same match is a cache hit. The
        caller is responsible for ``close()`` -- both implementations are
        context managers (Local is a no-op; HTTP closes the httpx Client).
        """
        local_path = _local_match_path(root)
        if local_path.exists():
            local = LocalJsonScoreboard(local_path)
            return _ScoreboardClientCtx(local, owns_close=False)
        try:
            http = SsiHttpClient()
        except ScoreboardAuthError as exc:
            _raise_scoreboard_http(exc)
        cache_dir = root / "scoreboard" / "cache"
        cached = CachingScoreboardClient(http, cache_dir)
        return _ScoreboardClientCtx(cached, owns_close=True, inner_http=http)

    def _register_response(root: Path, name: str, match_id: str | None) -> HealthResponse:
        """Build a HealthResponse for a freshly-registered match.

        Tier 1 step 4 of doc 10 retired the bound-state concept on
        the server, but the picker / create-match flow still need a
        way to learn the resolved ``match_id`` + default shooter so
        the SPA can navigate to ``/match/<match_id>``. That's what
        this response is for; the live ``/api/health`` route never
        returns these fields anymore.
        """
        # Hosted: read the authoritative roster from Postgres (the on-disk
        # match.json is the stale creation shell). Local: from disk.
        if state.project_state is not None and match_id is not None:
            doc, _ = run_sync(state.project_state.load_match(match_id))
            shooters = sorted(doc.get("shooters", [])) if doc is not None else []
        else:
            shooters = sorted(match_model.Match.load(root).shooters)
        default_slug = shooters[0] if shooters else None
        return HealthResponse(
            bound=True,
            project_name=name,
            project_root=str(root),
            kind="match",
            default_shooter_slug=default_slug,
            match_id=match_id,
        )

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        """Process-level health. Tier 1 step 4 of doc 10 retired
        the bound-state concept on the server -- match identity is
        per-request via the URL prefix, not per-process. The SPA
        relies on the URL for navigation; ``/api/health`` is now
        purely a "the server is up" check (plus version + status).
        """
        return HealthResponse(bound=False)

    @app.get("/api/models/status")
    async def models_status() -> dict[str, Any]:
        """Slim model layer status (issue #377 -- doc 03).

        The SPA polls this on mount + after each detect to know whether
        to render the "downloading models" overlay. Wheels that don't
        ship the ``model_artifacts`` calibration block (today's torch
        path) return ``available=False`` so the frontend doesn't draw
        the overlay at all.

        ``active_job`` carries the background prefetch job submitted by
        :func:`_maybe_submit_model_download` when artifacts are missing
        at startup. The SPA uses ``progress`` + ``message`` to render a
        non-blocking indicator while the user ingests; the Jobs panel
        still shows the same job through the regular ``/api/me/jobs``
        feed.
        """
        registry = model_layer.get_default_registry()
        if registry is None:
            return {
                "available": False,
                "artifacts": [],
                "missing": [],
                "mismatched": [],
                "active_job": None,
            }
        statuses = registry.status()
        missing = [s.slug for s in statuses if s.state == "missing"]
        mismatched = [s.slug for s in statuses if s.state == "mismatched"]
        active_job = await state.jobs.find_active(kind="model_download")
        return {
            "available": True,
            "cache_root": str(registry.root),
            "artifacts": [
                {
                    "slug": s.slug,
                    "state": s.state,
                    "sha256": s.expected_sha256,
                    "size_bytes": s.size_bytes,
                }
                for s in statuses
            ],
            "missing": missing,
            "mismatched": mismatched,
            "active_job": (
                {
                    "id": active_job.id,
                    "status": active_job.status,
                    "progress": active_job.progress,
                    "message": active_job.message,
                }
                if active_job is not None
                else None
            ),
        }

    @app.post("/api/shutdown", status_code=202)
    def shutdown(request: Request) -> dict[str, Any]:
        """Drain in-flight jobs and ask uvicorn to exit (issue #369).

        Loopback-only -- the embedded sidecar trusts only its own host.
        Idempotent: the first call kicks a daemon drain thread; later
        calls return 202 without rescheduling.

        Under the CLI's ``splitsmith ui`` path the registered stop
        callback is None, so this drains jobs but does not stop the
        server -- the operator still uses Ctrl-C there.
        """
        if not _is_loopback(request):
            raise HTTPException(
                status_code=403,
                detail={"code": "loopback_only", "message": "shutdown is loopback-only"},
            )
        if state.shutdown_initiated:
            return {"status": "shutting_down", "already": True}
        state.shutdown_initiated = True
        state.jobs.begin_shutdown()
        handler = state.shutdown_handler
        drain_timeout = 30.0

        def _drain_then_stop() -> None:
            try:
                state.jobs.wait_for_drain(drain_timeout)
            finally:
                if handler is not None:
                    handler()

        threading.Thread(target=_drain_then_stop, name="splitsmith-shutdown", daemon=True).start()
        return {"status": "shutting_down", "already": False}

    @app.get("/api/shooters/{slug}/project")
    def get_project(slug: str) -> JSONResponse:
        project = state.shooter_project(slug)
        root = state.shooter_root(slug)
        statuses = project.stage_statuses(root)
        payload = project.model_dump(mode="json")
        # Enrich each stage's serialized dict with its computed status
        # so the SPA never recomputes "is this audited?" client-side.
        # The status field is read-only (not on the StageEntry model);
        # PUTting it back is a no-op since model parsing ignores
        # unknown keys via Pydantic v2's default behavior.
        for stage_dict in payload.get("stages", []):
            n = stage_dict.get("stage_number")
            if n is None:
                continue
            status = statuses.get(int(n))
            if status is not None:
                stage_dict["status"] = status.value
        return JSONResponse(payload)

    @app.get("/api/shooters/{slug}/project/export")
    def export_project_endpoint(
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

    @app.post("/api/me/projects/import")
    async def import_project_endpoint(
        archive: UploadFile = File(...),
        dest_root: str = Form(...),
        overwrite: bool = Form(False),
        bind: bool = Form(False),
        user: User = Depends(get_current_user),
    ) -> JSONResponse:
        """Restore an archive produced by ``GET /api/project/export``.

        Extracts under ``dest_root``. When ``bind`` is true, the newly
        imported project is bound and added to the recent-projects list
        so the SPA can navigate straight into it.
        """
        dest = Path(dest_root).expanduser()
        tmp = Path(tempfile.mkdtemp(prefix="splitsmith-import-"))
        staged = tmp / "upload.tar.gz"
        try:
            with staged.open("wb") as out:
                while True:
                    chunk = await archive.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
            try:
                result = backup_mod.import_project(staged, dest, overwrite=overwrite)
            except backup_mod.BackupError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        if bind:
            await _register_match_at(state, result.project_root, fallback_name=result.project_name)

        return JSONResponse(
            {
                "project_root": str(result.project_root),
                "project_name": result.project_name,
                "manifest": result.manifest,
            }
        )

    @app.post("/api/me/raw/upload")
    async def upload_raw_video(
        file: UploadFile = File(...),
        x_content_sha256: str | None = Header(default=None),
        user: User = Depends(get_current_user),
    ) -> JSONResponse:
        """Stream a raw video into hosted-mode object storage.

        Hosted-only: returns 503 when ``state.storage`` is unwired,
        which is the local-mode default. The desktop ``splitsmith ui``
        flow writes raw videos to disk via ``raw/<name>`` symlinks and
        never calls this endpoint -- the existing path-based codepaths
        are unchanged.

        Robustness model (doc 05 calls the full tus answer out as the
        v2 goal; this is the v1 idempotent single-shot):

        - The write is atomic. S3 PUT (or boto3's multipart) only
          publishes the object on completion; a connection drop
          leaves no torn object visible. Aborted multiparts get
          swept by R2's lifecycle rule.
        - Re-uploads are safe. A successful retry overwrites the
          previous object atomically; clients that hit a transient
          error can just POST the same file again.
        - The server computes sha256 during streaming and returns it
          in the response so the client can detect transit corruption
          end-to-end. If the client knows the hash up front it can
          send ``X-Content-SHA256`` -- mismatch rolls the upload back
          (deletes the just-written object) and returns 422 so the
          retry path is "POST again with the corrected bytes".

        Resume-from-byte-N (tus) is deliberately not in this PR; see
        ``docs/saas-readiness/05-uploads-and-streaming.md``.
        """
        storage = state.storage
        if storage is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "raw video upload is hosted-mode only; "
                    "local mode writes raw/<name> symlinks via the desktop UI"
                ),
            )

        name = _sanitize_raw_filename(file.filename)
        key = f"raw/{name}"

        # Wrap the spooled body so we count bytes + hash in one pass
        # while boto3 streams to S3. UploadFile.file is the underlying
        # sync SpooledTemporaryFile that boto3 can ``.read()`` from
        # directly -- starlette has already spooled the multipart body
        # by the time this handler runs.
        source: BinaryIO = file.file
        source.seek(0)
        digest = hashlib.sha256()
        hashing = _HashingReader(source, digest)

        try:
            size = storage.upload_stream(key, hashing)
        except Exception as exc:
            # boto3's TransferManager aborts the multipart on its
            # own; re-raise as a clean 500 so the client sees a
            # retryable failure instead of a half-typed exception.
            raise HTTPException(status_code=500, detail=f"upload failed: {exc}") from exc

        sha256_hex = digest.hexdigest()

        if x_content_sha256 and x_content_sha256.lower() != sha256_hex:
            # The bytes that hit S3 don't match what the client said
            # they were sending. Roll the object back so a subsequent
            # GET doesn't serve corrupted content.
            try:
                storage.delete(key)
            except Exception:
                # Best-effort: if delete fails, R2's lifecycle still
                # has the object; surfacing the original mismatch is
                # more useful than the cleanup failure.
                pass
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "sha256_mismatch",
                    "expected": x_content_sha256,
                    "actual": sha256_hex,
                },
            )

        return JSONResponse(
            {
                "path": key,
                "size": size,
                "sha256": sha256_hex,
                "filename": name,
            }
        )

    # ------------------------------------------------------------------
    # Presigned multipart upload (#467): direct browser -> R2 for large
    # files. The single-shot endpoint above proxies bytes through this
    # process, which 502s past a few hundred MB on Railway. Here the
    # client PUTs parts straight to R2 via presigned URLs; serve only
    # mints the upload, signs parts, and finalizes. No bytes (and so no
    # server-side sha256) pass through serve -- attach treats sha256 as
    # optional, and R2's per-part ETags + the completed size are the
    # integrity signal. Hosted-only (503 when storage is unwired).
    # ------------------------------------------------------------------

    def _require_storage() -> Storage:
        storage = state.storage
        if storage is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "raw video upload is hosted-mode only; "
                    "local mode writes raw/<name> symlinks via the desktop UI"
                ),
            )
        return storage

    @app.post("/api/me/raw/upload/multipart/create")
    def create_multipart_upload(
        req: MultipartCreateRequest,
        user: User = Depends(get_current_user),
    ) -> JSONResponse:
        """Begin a multipart upload; return the upload id + chunk size.

        The client splits the file into ``part_size``-byte parts, asks
        ``/part-url`` for a presigned URL per part, PUTs each straight to
        R2, then calls ``/complete`` with the parts' ETags.
        """
        storage = _require_storage()
        name = _sanitize_raw_filename(req.filename)
        key = f"raw/{name}"
        try:
            upload_id = storage.create_multipart_upload(key)
        except Exception as exc:  # noqa: BLE001 -- surface as a clean 500
            raise HTTPException(status_code=500, detail=f"could not start upload: {exc}") from exc
        return JSONResponse(
            {
                "upload_id": upload_id,
                "filename": name,
                "key": key,
                "part_size": _RAW_UPLOAD_PART_SIZE,
            }
        )

    @app.post("/api/me/raw/upload/multipart/part-url")
    def sign_multipart_part(
        req: MultipartPartUrlRequest,
        user: User = Depends(get_current_user),
    ) -> JSONResponse:
        """Return a presigned URL the client PUTs one part to."""
        storage = _require_storage()
        if req.part_number < 1:
            raise HTTPException(status_code=422, detail="part_number must be >= 1")
        key = f"raw/{_sanitize_raw_filename(req.filename)}"
        try:
            url = storage.presign_upload_part(key, req.upload_id, req.part_number)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"could not sign part: {exc}") from exc
        return JSONResponse({"url": url})

    @app.post("/api/me/raw/upload/multipart/complete")
    def complete_multipart_upload(
        req: MultipartCompleteRequest,
        user: User = Depends(get_current_user),
    ) -> JSONResponse:
        """Finalize the upload. Returns the same shape the single-shot
        endpoint echoes (minus ``sha256``, which serve never computed)."""
        storage = _require_storage()
        if not req.parts:
            raise HTTPException(status_code=422, detail="parts must not be empty")
        name = _sanitize_raw_filename(req.filename)
        key = f"raw/{name}"
        try:
            size = storage.complete_multipart_upload(
                key, req.upload_id, [(p.part_number, p.etag) for p in req.parts]
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"could not complete upload: {exc}") from exc
        return JSONResponse({"path": key, "size": size, "sha256": None, "filename": name})

    @app.post("/api/me/raw/upload/multipart/abort")
    def abort_multipart_upload(
        req: MultipartAbortRequest,
        user: User = Depends(get_current_user),
    ) -> JSONResponse:
        """Discard an in-progress upload (client cancelled / failed)."""
        storage = _require_storage()
        key = f"raw/{_sanitize_raw_filename(req.filename)}"
        try:
            storage.abort_multipart_upload(key, req.upload_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"could not abort upload: {exc}") from exc
        return JSONResponse({"ok": True})

    @app.get("/api/me/raw/list")
    def list_raw_uploads(user: User = Depends(get_current_user)) -> JSONResponse:
        """List every object the operator has uploaded under their
        ``raw/`` prefix.

        Hosted-only -- 503 in local mode (no storage backend wired;
        local users put videos on disk via the desktop ingest flow).

        Returned shape mirrors what ``POST /api/me/raw/upload`` echoes
        back so the SPA can render an "uploaded files" surface without
        a second lookup:

        - ``filename`` -- the bare leaf name, what the operator picked
          on disk (``_sanitize_raw_filename`` already stripped any
          directory parts at upload time).
        - ``path`` -- the storage key relative to the user's prefix
          (e.g. ``raw/clip.mp4``). The SPA echoes this back to any
          register-into-project endpoint we add later.
        - ``size`` -- bytes.
        - ``last_modified`` -- ISO-8601 UTC, what S3 / MinIO reports
          as the object's mtime. ``null`` when the backend doesn't
          surface one (some test doubles).
        - ``etag`` -- S3 etag when present (useful for the SPA to
          detect a re-upload of the same filename).

        Sorted newest-first so the picker surfaces the most recent
        upload at the top without the SPA having to re-sort.
        """
        storage = state.storage
        if storage is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "raw video list is hosted-mode only; "
                    "local mode keeps videos on disk under the project root"
                ),
            )
        entries: list[dict[str, Any]] = []
        for obj in storage.list("raw/"):
            entries.append(
                {
                    "filename": obj.path.split("/", 1)[1] if "/" in obj.path else obj.path,
                    "path": obj.path,
                    "size": obj.size,
                    "last_modified": (
                        obj.last_modified.isoformat() if obj.last_modified is not None else None
                    ),
                    "etag": obj.etag,
                }
            )
        # Newest first. ``last_modified`` is None on backends that don't
        # report one; those entries sort last (stable secondary key by
        # filename so the SPA gets a deterministic order).
        entries.sort(
            key=lambda e: (
                e["last_modified"] is not None,
                e["last_modified"] or "",
                e["filename"],
            ),
            reverse=True,
        )
        return JSONResponse({"uploads": entries})

    @app.delete("/api/me/raw/{filename:path}")
    def delete_raw_upload(filename: str, user: User = Depends(get_current_user)) -> JSONResponse:
        """Remove one uploaded file from object storage.

        Hosted-only (503 in local mode). Idempotent -- deleting an
        already-gone object returns 200 with ``{"ok": true}`` so the
        SPA can retry without special-casing.

        The ``filename`` segment is run through ``_sanitize_raw_filename``
        so a malicious caller can't traverse out of their ``raw/`` prefix
        (the underlying ``S3Storage._key`` also rejects ``..`` but we
        belt-and-braces it at the route layer too).
        """
        storage = state.storage
        if storage is None:
            raise HTTPException(
                status_code=503,
                detail="raw video delete is hosted-mode only",
            )
        name = _sanitize_raw_filename(filename)
        key = f"raw/{name}"
        try:
            storage.delete(key)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"delete failed: {exc}") from exc
        return JSONResponse({"ok": True, "path": key})

    @app.post("/api/shooters/{slug}/raw-videos/attach")
    def attach_raw_video(
        slug: str,
        body: AttachRawVideoRequest,
        user: User = Depends(get_current_user),
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
        storage = state.storage
        if storage is None:
            raise HTTPException(
                status_code=503,
                detail="raw video attach is hosted-mode only",
            )

        name = _sanitize_raw_filename(body.filename)
        storage_path = f"raw/{name}"
        stat = storage.stat(storage_path)
        if stat is None:
            raise HTTPException(
                status_code=404,
                detail=f"no upload at {storage_path!r}; upload first via POST /api/me/raw/upload",
            )

        project = state.shooter_project(slug)
        root = state.shooter_root(slug)

        covers = sorted(set(body.covers_stages or []))
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
        )
        canonical = project.attach_raw_video(rv)

        # Decide where the per-stage StageVideo entries land:
        #   - covers_stages given -> one StageVideo per named stage
        #     (the doc-05 "one raw covers N stages" shape).
        #   - covers_stages empty -> a single entry in unassigned_videos
        #     so the ingest tray UI picks it up the same way local-mode
        #     scan does. The user then drags to stages or runs
        #     auto-match. Without this, attach + no coverage would
        #     leave the project's raw_videos manifest populated but
        #     nothing for the worker to detect against.
        video_path = Path(storage_path)
        already_registered = project.find_video(video_path) is not None
        if covers:
            for stage_number in covers:
                stage = project.stage(stage_number)
                if any(str(v.path) == storage_path for v in stage.videos):
                    continue
                role: VideoRole = "secondary" if stage.primary() is not None else "primary"
                stage.videos.append(StageVideo(path=video_path, role=role))
        elif not already_registered:
            project.unassigned_videos.append(StageVideo(path=video_path))

        project.save(root)

        return JSONResponse(canonical.model_dump(mode="json"))

    @app.get("/api/shooters/{slug}/automation")
    def get_automation(slug: str) -> JSONResponse:
        """Resolved automation settings + per-field provenance (#215 / #216).

        Combines the global YAML / env-var defaults with the bound
        project's override. The SPA renders provenance badges next to
        each toggle so users see which layer set the current value.
        ``cli_value`` is always None on the daemon path -- the server
        doesn't take per-call CLI flags.
        """
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

    @app.post("/api/shooters/{slug}/project/nudges/dismiss")
    def dismiss_nudge(slug: str, req: NudgeDismissRequest) -> JSONResponse:
        """Persist a per-project nudge dismissal (#218 phase 4).

        Adds ``stage_number`` to ``MatchProject.nudges_dismissed_stages``
        when ``dismissed=True`` (idempotent), removes it otherwise.
        Returns the full project dump so the SPA can replace its
        cached state without a separate refetch.
        """
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

    @app.get("/api/shooters/{slug}/hitl-queue")
    def get_hitl_queue(slug: str) -> JSONResponse:
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

    @app.get("/api/shooters/{slug}/project/match-analysis")
    def get_match_analysis(slug: str) -> JSONResponse:
        """Run the canonical video-match heuristic over the project and return
        per-stage windows + per-video classification.

        Single source of truth for the SPA's match-window timeline:
        tolerance, window edges, and per-video classification are all
        produced by :mod:`splitsmith.video_match`. Future heuristic
        improvements (per-stage tolerance, ML-based scoring, confidence
        bands) extend this endpoint rather than adding policy to the SPA.
        """
        project = state.shooter_project(slug)
        return JSONResponse(project.match_analysis().model_dump(mode="json"))

    @app.post("/api/shooters/{slug}/scoreboard/import")
    def import_scoreboard(slug: str, req: ScoreboardImportRequest) -> JSONResponse:
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

    @app.get("/api/shooters/{slug}/scoreboard/source")
    def scoreboard_source(slug: str) -> JSONResponse:
        """Report whether the offline JSON or the live API will serve requests.

        The SPA renders a "loaded from local JSON, no network used" indicator
        when ``mode == "local"`` so the user can verify the offline path
        without watching dev tools.
        """
        local_path = _local_match_path(state.shooter_root(slug))
        local = local_path.exists()
        http_ready = bool(os.environ.get("SPLITSMITH_SSI_TOKEN"))
        return JSONResponse(
            {
                "mode": "local" if local else "online",
                "local_match_json_path": str(local_path) if local else None,
                "http_token_set": http_ready,
            }
        )

    @app.post("/api/shooters/{slug}/scoreboard/upload")
    def scoreboard_upload(slug: str, req: ScoreboardUploadRequest) -> JSONResponse:
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
        root = state.shooter_root(slug)
        local_path = _local_match_path(root)
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
        project.save(root)
        return JSONResponse({**project.model_dump(mode="json"), "stage_times_merged": merged})

    @app.get("/api/shooters/{slug}/scoreboard/search")
    def scoreboard_search(slug: str, q: str = Query("", min_length=0)) -> JSONResponse:
        """Search the active scoreboard source for matches by free-text query."""
        with _resolve_scoreboard_client(state.shooter_root(slug)) as client:
            try:
                refs = client.search_matches(q)
            except ScoreboardError as exc:
                _raise_scoreboard_http(exc)
        return JSONResponse([ref.model_dump(mode="json") for ref in refs])

    @app.get("/api/scoreboard/search")
    def scoreboard_search_unbound(q: str = Query("", min_length=0)) -> JSONResponse:
        """Search the live scoreboard without a bound shooter (#322).

        The create-match-from-scoreboard flow runs *before* a project
        exists, so there is no shooter root to scope a cache to. Search
        results aren't cached anyway -- ``CachingScoreboardClient`` only
        memoises ``get_match`` -- so we skip the cache wrapper entirely
        and go straight to the HTTP client.
        """
        try:
            client = SsiHttpClient()
        except ScoreboardAuthError as exc:
            _raise_scoreboard_http(exc)
        with client:
            try:
                refs = client.search_matches(q)
            except ScoreboardError as exc:
                _raise_scoreboard_http(exc)
        return JSONResponse([ref.model_dump(mode="json") for ref in refs])

    @app.get("/api/scoreboard/matches/{content_type}/{match_id}")
    def scoreboard_match_data_unbound(content_type: int, match_id: int) -> JSONResponse:
        """Fetch full match data (incl. competitor list) without binding.

        The create-from-scoreboard flow needs the competitor roster
        *before* a project exists so the user can pick multiple
        shooters in one step. Mirrors the unbound search endpoint:
        skip the per-project cache and call the HTTP client directly.
        Hitting this twice for the same match in the same session is
        cheap on scoreboard.urdr.dev (CDN-cached) and avoids hauling
        a per-session in-memory cache around just for this flow.
        """
        try:
            client = SsiHttpClient()
        except ScoreboardAuthError as exc:
            _raise_scoreboard_http(exc)
        with client:
            try:
                data = client.get_match(content_type, match_id)
            except MatchNotFound as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ScoreboardError as exc:
                _raise_scoreboard_http(exc)
        return JSONResponse(data.model_dump(mode="json"))

    @app.post("/api/shooters/{slug}/scoreboard/fetch")
    def scoreboard_fetch(slug: str, req: ScoreboardFetchRequest) -> JSONResponse:
        """Fetch a full match (cache-first) and populate the project.

        When the project already has a pinned competitor (carried over from
        a previous session), auto-merge their stage times in the same
        round-trip. New picks (no pin yet) still need ``/select-shooter``
        afterwards -- the SPA flow stays the same.
        """
        root = state.shooter_root(slug)
        with _resolve_scoreboard_client(root) as client:
            try:
                match_data = client.get_match(req.content_type, req.match_id)
            except MatchNotFound as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ScoreboardError as exc:
                _raise_scoreboard_http(exc)
        project = state.shooter_project(slug)
        try:
            project.populate_from_match_data(match_data, overwrite=req.overwrite)
        except ScoreboardImportConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        merged = 0
        if project.selected_competitor_id is not None:
            try:
                merged = _fetch_and_merge_stage_times(
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

    @app.get("/api/shooters/{slug}/scoreboard/match-data")
    def scoreboard_match_data(slug: str) -> JSONResponse:
        """Return the resolved ``MatchData`` for the project's loaded match.

        The SPA needs this to map a picked ``shooterId`` to the per-match
        ``competitor_id`` before calling ``/select-shooter``. We don't
        denormalise that mapping onto the ``MatchProject`` because it
        drifts when upstream re-fetches; serving on demand keeps the
        cache as the single source of truth.
        """
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
        with _resolve_scoreboard_client(root) as client:
            try:
                match_data = client.get_match(ct, mid)
            except MatchNotFound as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ScoreboardError as exc:
                _raise_scoreboard_http(exc)
        return JSONResponse(match_data.model_dump(mode="json"))

    @app.get("/api/shooters/{slug}/scoreboard/shooter/search")
    def scoreboard_shooter_search(slug: str, q: str = Query("", min_length=0)) -> JSONResponse:
        """Find shooters by name. Offline mode searches this match's
        competitor list only; online mode hits the live shooter index."""
        if not q.strip():
            return JSONResponse([])
        with _resolve_scoreboard_client(state.shooter_root(slug)) as client:
            try:
                refs = client.find_shooter(q)
            except ScoreboardError as exc:
                _raise_scoreboard_http(exc)
        return JSONResponse([ref.model_dump(mode="json") for ref in refs])

    @app.post("/api/shooters/{slug}/scoreboard/select-shooter")
    def scoreboard_select_shooter(slug: str, req: SelectShooterRequest) -> JSONResponse:
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
        with _resolve_scoreboard_client(root) as client:
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
                        f"competitor {req.competitor_id} isn't in this match. " "Pick a different shooter."
                    ),
                },
            )

        project.selected_shooter_id = req.shooter_id
        project.selected_competitor_id = req.competitor_id
        # Persist the human-readable name so the SPA's collapsed
        # scoreboard summary doesn't have to re-fetch MatchData just to
        # render "pinned: Mathias Rinaldo" instead of an integer id.
        project.competitor_name = picked.name
        project.save(root)

        merged = _fetch_and_merge_stage_times(root, project, ct, mid, req.competitor_id)
        return JSONResponse({**project.model_dump(mode="json"), "stage_times_merged": merged})

    @app.post("/api/shooters/{slug}/scoreboard/refresh-times")
    def scoreboard_refresh_times(slug: str) -> JSONResponse:
        """Re-pull and re-merge stage times for the pinned competitor.

        Invalidates every cached stage-times entry for the match (not
        just the pinned competitor) because in-progress matches often
        update multiple shooters at once and a fresh pull is cheap. Use
        this after the user knows the upstream has new scorecards.
        """
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

        merged = _fetch_and_merge_stage_times(root, project, ct, mid, project.selected_competitor_id)
        return JSONResponse({**project.model_dump(mode="json"), "stage_times_merged": merged})

    def _fetch_and_merge_stage_times(
        root: Path, project: MatchProject, ct: int, mid: int, competitor_id: int
    ) -> int:
        """Shared helper -- get_stage_times + merge_stage_times, with the
        right error mapping. Persists the project on success.

        Also opportunistically backfills ``stage_rounds`` from the cached
        ``MatchData`` so existing projects pick up ``min_rounds`` /
        target counts on the existing "refresh times" SPA action without
        requiring a full overwrite-import that would orphan video
        assignments.
        """
        with _resolve_scoreboard_client(root) as client:
            try:
                results = client.get_stage_times(ct, mid, competitor_id)
            except ScoreboardError as exc:
                _raise_scoreboard_http(exc)
            except KeyError as exc:
                raise HTTPException(
                    status_code=404,
                    detail=str(exc),
                ) from exc
            # Best-effort backfill of stage_rounds. Failure is non-fatal
            # -- stage times are the user-visible action; rounds are an
            # additive bonus that the next real scoreboard refresh can
            # still cover.
            try:
                match_data = client.get_match(ct, mid)
                project.merge_stage_rounds(match_data)
            except ScoreboardError:
                pass
        merged = project.merge_stage_times(results)
        project.save(root)
        return merged

    @app.post("/api/shooters/{slug}/project/placeholder-stages")
    def create_placeholder_stages(slug: str, req: PlaceholderStagesRequest) -> JSONResponse:
        """Create N placeholder stages so source-first ingest works without a
        scoreboard. A real scoreboard import later overlays the placeholders
        and preserves video assignments by ``stage_number``."""
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

    @app.post("/api/shooters/{slug}/videos/scan", response_model=ScanResponse)
    async def scan_videos(slug: str, req: ScanRequest) -> ScanResponse:
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
        project = state.shooter_project(slug)
        registered: list[str] = []
        skipped: list[str] = []
        for entry in candidates:
            try:
                video = project.register_video(
                    entry,
                    root,
                    link_mode=req.link_mode,  # type: ignore[arg-type]
                )
            except (FileNotFoundError, ValueError) as exc:
                skipped.append(f"{entry.name}: {exc}")
                continue
            registered.append(str(video.path))

        auto_assigned: dict[int, str] = {}
        if req.auto_assign_primary:
            suggestions = project.auto_match(root)
            for stage_num, video_path in suggestions.items():
                stage = project.stage(stage_num)
                # Only auto-assign primary when the stage has no primary yet.
                if stage.primary() is not None:
                    continue
                project.assign_video(video_path, to_stage_number=stage_num, role="primary")
                auto_assigned[stage_num] = str(video_path)

        if last_dir is not None:
            project.last_scanned_dir = str(last_dir)

        project.save(root)

        # Queue auto-beep for every freshly-primaried video (#67). Done
        # after save so the persisted state reflects the assignment when
        # the worker re-loads the project.
        for stage_num, video_path in auto_assigned.items():
            stage = project.stage(stage_num)
            video = next((v for v in stage.videos if str(v.path) == video_path), None)
            if video is not None:
                await _auto_queue_beep_if_needed(slug, project, stage_num, video)

        return ScanResponse(
            registered=registered,
            auto_assigned=auto_assigned,
            skipped=skipped,
        )

    @app.get("/api/shooters/{slug}/videos/link-status", response_model=LinkStatusResponse)
    def get_link_status(slug: str) -> LinkStatusResponse:
        """Per-video status of ``raw/<name>`` symlinks.

        Lets the SPA badge broken / missing entries on the project page
        without an extra walk on every project fetch.
        """
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

    @app.post("/api/shooters/{slug}/videos/relink/scan", response_model=RelinkScanResponse)
    def relink_scan(slug: str, req: RelinkScanRequest) -> RelinkScanResponse:
        """Recursive dry-run: walk ``search_root`` and report per-video
        candidates without touching the filesystem.
        """
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

    @app.post("/api/shooters/{slug}/videos/relink/apply", response_model=RelinkApplyResponse)
    def relink_apply(slug: str, req: RelinkApplyRequest) -> RelinkApplyResponse:
        """Apply a user-confirmed set of symlink rewrites.

        ``project.json`` is not modified -- only the symlinks under
        ``raw/`` are repointed. The video_id is preserved so the SPA
        can match the response back to its rows.
        """
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
                    previous_target=(
                        str(entry.previous_target) if entry.previous_target is not None else None
                    ),
                    new_target=str(entry.new_target),
                )
                for entry in applied
            ]
        )

    @app.post("/api/shooters/{slug}/project/settings")
    def update_settings(slug: str, req: SettingsRequest) -> JSONResponse:
        """Update storage path overrides. Any None field is left unchanged;
        pass an empty string to clear back to the project-root default.

        If a path field is changing and the *old* directory contains files,
        return 409 with a structured ``non_empty_old_dirs`` payload unless
        ``confirm=True`` is sent. Existing files are not auto-migrated --
        the warning lets the caller surface "you'll be leaving these behind".
        """
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

    def _resolve_stage_video(
        slug: str, stage_number: int, video_id: str
    ) -> tuple[MatchProject, StageEntry, StageVideo]:
        """Load the project + stage + video for a per-video endpoint.

        Returns the trio so the caller doesn't have to re-read state. Raises
        404 when stage / video doesn't exist; pre-flight check on the
        source-on-disk lives at the call site so endpoints that don't need
        a reachable file (clear, manual override) can skip it.
        """
        project = state.shooter_project(slug)
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

    async def _submit_detect_beep(slug: str, stage_number: int, video: StageVideo) -> JSONResponse:
        """Validate + dedupe + queue a detect-beep job for ``video``.

        Shared by the per-video endpoint and the primary-only legacy
        endpoint so both honour the same reachability + manual-override
        pre-flight checks.
        """
        existing = await state.jobs.find_active(
            kind="detect_beep", stage_number=stage_number, video_id=video.video_id
        )
        if existing is not None:
            return JSONResponse(existing.model_dump(mode="json"))
        job = await state.jobs.submit(
            kind="detect_beep",
            stage_number=stage_number,
            video_id=video.video_id,
            args={"slug": slug, "stage_number": stage_number, "video_id": video.video_id},
        )
        return JSONResponse(job.model_dump(mode="json"))

    async def _auto_queue_beep_if_needed(
        slug: str, project: MatchProject, stage_number: int, video: StageVideo
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
        source = project.resolve_video_path(state.shooter_root(slug), video.path)
        if not source.exists():
            logger.info(
                "auto-beep skipped for stage %d video %s: source not reachable (%s)",
                stage_number,
                video.video_id,
                source,
            )
            return False
        await _submit_detect_beep(slug, stage_number, video)
        return True

    @app.post("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/detect-beep")
    async def detect_beep_for_video(
        slug: str, stage_number: int, video_id: str, force: bool = False
    ) -> JSONResponse:
        """Submit a beep-detection job for ``video_id`` on ``stage_number``.

        Generic over role (primary or secondary): each video gets its own
        detect job, its own beep timestamp, its own short-GOP trim, and
        its own dedupe slot in the registry so the user can run primary +
        Cam 2 + Cam 3 in parallel. Shot detection auto-chains only for
        primary results; secondaries align to the primary timeline by
        their own beep so they don't need their own shot timeline.
        """
        project, _stage, video = _resolve_stage_video(slug, stage_number, video_id)
        _ensure_source_reachable(
            stage_number, project.resolve_video_path(state.shooter_root(slug), video.path)
        )
        if video.beep_source == "manual" and not force:
            raise HTTPException(
                status_code=409,
                detail=(
                    "video has a manual beep override; pass ?force=true to "
                    "replace it with auto-detected output"
                ),
            )
        return await _submit_detect_beep(slug, stage_number, video)

    @app.post("/api/shooters/{slug}/stages/{stage_number}/detect-beep")
    async def detect_beep(slug: str, stage_number: int, force: bool = False) -> JSONResponse:
        """Submit a beep-detection job for the stage's primary.

        Backward-compat shim that resolves the primary's id and forwards to
        the per-video pipeline. Returns a Job snapshot immediately; the SPA
        polls ``/api/jobs/{id}`` for progress and refetches ``/api/project``
        on completion.
        """
        project = state.shooter_project(slug)
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
            stage_number, project.resolve_video_path(state.shooter_root(slug), primary.path)
        )
        if primary.beep_source == "manual" and not force:
            raise HTTPException(
                status_code=409,
                detail=(
                    "stage has a manual beep override; pass ?force=true to "
                    "replace it with auto-detected output"
                ),
            )
        return await _submit_detect_beep(slug, stage_number, primary)

    @app.post("/api/shooters/{slug}/stages/{stage_number}/trim")
    async def trim_stage(slug: str, stage_number: int) -> JSONResponse:
        """Submit an audit-mode short-GOP trim job for the stage's primary.

        Backward-compat shim: forwards to the per-video pipeline. Returns
        a Job snapshot. Idempotent on the worker side: when the cached
        MP4 is newer than the source, the job completes near-instantly
        without re-encoding.
        """
        project = state.shooter_project(slug)
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
        return await _submit_trim(slug, stage_number, stage, primary, project)

    async def _submit_trim(
        slug: str,
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
            stage_number, project.resolve_video_path(state.shooter_root(slug), video.path)
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
        existing = await state.jobs.find_active(
            kind="trim", stage_number=stage_number, video_id=video.video_id
        )
        if existing is not None:
            return JSONResponse(existing.model_dump(mode="json"))
        job = await state.jobs.submit(
            kind="trim",
            stage_number=stage_number,
            video_id=video.video_id,
            args={"slug": slug, "stage_number": stage_number, "video_id": video.video_id},
        )
        return JSONResponse(job.model_dump(mode="json"))

    @app.post("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/trim")
    async def trim_for_video(slug: str, stage_number: int, video_id: str) -> JSONResponse:
        """Submit an audit-mode trim job for ``video_id`` on ``stage_number``."""
        project, stage, video = _resolve_stage_video(slug, stage_number, video_id)
        return await _submit_trim(slug, stage_number, stage, video, project)

    @app.post("/api/shooters/{slug}/stages/shot-detect")
    async def shot_detect_all_endpoint(slug: str, reset: bool = False) -> JSONResponse:
        """Submit shot-detection on every eligible stage in the project.

        A stage is eligible when it has a primary video with a confirmed
        ``beep_time`` and ``time_seconds > 0`` -- i.e. the same gates the
        per-stage endpoint checks. Stages that don't qualify are silently
        skipped and reported in ``skipped`` so the SPA can surface them.

        Idempotent dedupe: stages with an already-active shot-detect job
        adopt the existing job instead of starting a second.

        ``reset=true`` clears each affected stage's ``shots[]`` before
        running, matching the per-stage endpoint's semantics.
        """
        project = state.shooter_project(slug)
        jobs: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for stage in project.stages:
            stage_number = stage.stage_number
            primary = stage.primary()
            if primary is None:
                skipped.append({"stage_number": stage_number, "reason": "no_primary"})
                continue
            if primary.beep_time is None:
                skipped.append({"stage_number": stage_number, "reason": "no_beep"})
                continue
            if stage.time_seconds <= 0:
                skipped.append({"stage_number": stage_number, "reason": "no_time_seconds"})
                continue
            existing = await state.jobs.find_active(kind="shot_detect", stage_number=stage_number)
            if existing is not None:
                jobs.append(existing.model_dump(mode="json"))
                continue
            job = await state.jobs.submit(
                kind="shot_detect",
                stage_number=stage_number,
                args={"slug": slug, "stage_number": stage_number, "reset": reset},
            )
            jobs.append(job.model_dump(mode="json"))
        return JSONResponse({"jobs": jobs, "skipped": skipped})

    @app.post("/api/shooters/{slug}/stages/{stage_number}/shot-detect")
    async def shot_detect_endpoint(slug: str, stage_number: int, reset: bool = False) -> JSONResponse:
        """Submit a shot-detection job for the stage's audit clip.

        Returns a Job snapshot. Idempotent dedupe via the registry: a second
        click while one is running adopts the existing job. The candidate
        list lands in the audit JSON's ``_candidates_pending_audit`` block,
        which is what the audit screen reads to render markers.

        ``reset=true`` wipes ``shots[]`` first so the user can start over.
        """
        project = state.shooter_project(slug)
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

        existing = await state.jobs.find_active(kind="shot_detect", stage_number=stage_number)
        if existing is not None:
            return JSONResponse(existing.model_dump(mode="json"))
        job = await state.jobs.submit(
            kind="shot_detect",
            stage_number=stage_number,
            args={"slug": slug, "stage_number": stage_number, "reset": reset},
        )
        return JSONResponse(job.model_dump(mode="json"))

    @app.get("/api/me", response_model=User)
    def get_me(user: User = Depends(get_current_user)) -> User:
        """Return the operator behind this request.

        Local mode always resolves to the ``LoopbackAuth`` sentinel
        user (id ``"local"``); hosted mode will resolve cookies to a
        real database user. The SPA reads this once on mount so the
        same code path works in both modes.
        """
        return user

    @app.get("/api/me/jobs", response_model=list[Job])
    async def list_jobs(user: User = Depends(get_current_user)) -> list[Job]:
        """Snapshot of all retained jobs (active + recently finished)."""
        return await state.jobs.list()

    @app.get("/api/me/jobs/{job_id}", response_model=Job)
    async def get_job(job_id: str, user: User = Depends(get_current_user)) -> Job:
        """Poll a single job. SPA polls ~1 Hz while a job is active."""
        job = await state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        return job

    @app.post("/api/me/jobs/acknowledge-failures", response_model=list[Job])
    async def acknowledge_all_failures(user: User = Depends(get_current_user)) -> list[Job]:
        """Mark every currently-unacknowledged FAILED job as seen (issue #73).

        Used by the JobsPanel "Dismiss all failures" header action. Returns
        the snapshots that actually flipped to acknowledged so the SPA can
        diff against its in-memory list without an extra refetch.
        """
        return await state.jobs.acknowledge_all_failures()

    @app.post("/api/me/jobs/{job_id}/acknowledge", response_model=Job)
    async def acknowledge_job(job_id: str, user: User = Depends(get_current_user)) -> Job:
        """Mark a single failed job as seen (issue #73).

        No-op for jobs that aren't failed or are already acknowledged --
        the snapshot is returned unchanged so the SPA can still pin its
        local state to the server response.
        """
        job = await state.jobs.acknowledge(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        return job

    @app.post("/api/me/jobs/{job_id}/cancel", response_model=Job)
    async def cancel_job(job_id: str, user: User = Depends(get_current_user)) -> Job:
        """Request cooperative cancellation of a running or pending job.

        The registry sets ``cancel_requested=True`` and (for trim jobs)
        terminates the running ffmpeg subprocess. The worker then bails
        out at its next phase boundary, ending the job in
        ``status=cancelled``. Idempotent: cancelling a finished job
        returns the existing snapshot unchanged.
        """
        job = await state.jobs.cancel(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        return job

    def _apply_beep_override(
        slug: str,
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
            video.beep_confidence = None
            video.beep_candidates = []
            video.beep_auto_detect_failed = False
            video.beep_alignment_confidence = None
            video.beep_alignment_delta_ms = None
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
            # Manual entry pins confidence at 1.0 -- the user told us
            # where the beep is, so the auto-trust gate (#219) opens.
            video.beep_confidence = 1.0
            video.beep_candidates = []
            video.beep_auto_detect_failed = False
            video.beep_alignment_confidence = None
            video.beep_alignment_delta_ms = None
            video.processed["beep"] = True
            video.processed["trim"] = False
            # Manual beep entry implies the user looked at the
            # waveform to type the value -- skip the review pill (#71).
            video.beep_reviewed = True
            if video.role == "primary":
                video.processed["shot_detect"] = False

        audio_helpers.invalidate_video_audit_trim(
            state.shooter_root(slug), stage.stage_number, video, project=project
        )

    async def _maybe_chain_trim(slug: str, stage: StageEntry, video: StageVideo) -> None:
        """Auto-fire a trim job for ``video`` when conditions allow.

        Used after a beep override / candidate select: if the user just
        gave us a beep and the stage time is known, the next thing they
        want is a fresh short-GOP trim. Dedupes through ``find_active``
        so a still-running job adopts instead of racing.
        """
        if video.beep_time is None or stage.time_seconds <= 0:
            return
        if (
            await state.jobs.find_active(
                kind="trim", stage_number=stage.stage_number, video_id=video.video_id
            )
            is not None
        ):
            return
        await state.jobs.submit(
            kind="trim",
            stage_number=stage.stage_number,
            video_id=video.video_id,
            args={
                "slug": slug,
                "stage_number": stage.stage_number,
                "video_id": video.video_id,
            },
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
        video.beep_confidence = chosen.confidence
        video.beep_auto_detect_failed = False
        video.beep_alignment_confidence = None
        video.beep_alignment_delta_ms = None
        video.processed["beep"] = True
        video.processed["trim"] = False
        # Switching candidate is a fresh claim about which moment is
        # the beep -- prior review approval doesn't carry over (#71).
        video.beep_reviewed = False
        if video.role == "primary":
            video.processed["shot_detect"] = False

    @app.post("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/beep")
    async def override_beep_for_video(
        slug: str, stage_number: int, video_id: str, req: BeepOverrideRequest
    ) -> JSONResponse:
        """Manually set or clear ``video``'s beep timestamp.

        ``req.beep_time = None`` clears back to "no beep yet"; otherwise
        the value (in seconds, must be >= 0) is taken as authoritative
        with ``beep_source="manual"``. Same dedupe + auto-trim chain as
        the legacy primary endpoint, just keyed per video.
        """
        project, stage, video = _resolve_stage_video(slug, stage_number, video_id)
        if req.beep_time is not None and req.beep_time < 0.0:
            raise HTTPException(status_code=400, detail="beep_time must be >= 0")
        _apply_beep_override(slug, project, stage, video, req.beep_time)
        project.save(state.shooter_root(slug))
        if req.beep_time is not None:
            await _maybe_chain_trim(slug, stage, video)
        return JSONResponse(project.model_dump(mode="json"))

    @app.post("/api/shooters/{slug}/stages/{stage_number}/beep")
    async def override_beep(slug: str, stage_number: int, req: BeepOverrideRequest) -> JSONResponse:
        """Backward-compat shim: manually set / clear the primary's beep."""
        project = state.shooter_project(slug)
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
        _apply_beep_override(slug, project, stage, primary, req.beep_time)
        project.save(state.shooter_root(slug))
        if req.beep_time is not None:
            await _maybe_chain_trim(slug, stage, primary)
        return JSONResponse(project.model_dump(mode="json"))

    @app.post("/api/shooters/{slug}/stages/{stage_number}/time")
    def set_stage_time(slug: str, stage_number: int, req: StageTimeRequest) -> JSONResponse:
        """Manually set or clear the stage duration.

        For projects without scoreboard data (the only source that
        normally populates ``time_seconds``). Setting a positive value
        stamps ``time_seconds_manual=True`` so a later scoreboard sync
        won't clobber it. ``time_seconds = None`` clears back to 0.0
        (and clears the manual flag), which re-blocks trim / shot
        detection.

        Does NOT auto-chain a trim job -- the user clicks Trim
        themselves once they're satisfied with the duration.
        """
        project = state.shooter_project(slug)
        try:
            stage = project.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if req.time_seconds is None:
            stage.time_seconds = 0.0
            stage.time_seconds_manual = False
        else:
            if req.time_seconds <= 0.0:
                raise HTTPException(
                    status_code=400,
                    detail="time_seconds must be > 0 (use null to clear)",
                )
            stage.time_seconds = float(req.time_seconds)
            stage.time_seconds_manual = True
        project.save(state.shooter_root(slug))
        return JSONResponse(project.model_dump(mode="json"))

    @app.post("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/beep/snap")
    def snap_beep_for_video(
        slug: str, stage_number: int, video_id: str, req: BeepSnapRequest
    ) -> JSONResponse:
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
        project, _stage, video = _resolve_stage_video(slug, stage_number, video_id)
        source = project.resolve_video_path(state.shooter_root(slug), video.path)
        _ensure_source_reachable(stage_number, source)
        if req.hint_time < 0.0:
            raise HTTPException(status_code=400, detail="hint_time must be >= 0")
        if req.window_s <= 0.0:
            raise HTTPException(status_code=400, detail="window_s must be > 0")

        audio_path = audio_helpers.ensure_video_audio(
            state.shooter_root(slug),
            stage_number,
            video,
            source,
            project=project,
            ffmpeg_binary=process_runtime().ffmpeg_binary,
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

    @app.post("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/beep/select")
    async def select_beep_candidate_for_video(
        slug: str, stage_number: int, video_id: str, req: BeepSelectRequest
    ) -> JSONResponse:
        """Promote one of ``video``'s ranked candidates as authoritative."""
        project, stage, video = _resolve_stage_video(slug, stage_number, video_id)
        _select_candidate_on_video(video, req.time)
        audio_helpers.invalidate_video_audit_trim(
            state.shooter_root(slug), stage_number, video, project=project
        )
        project.save(state.shooter_root(slug))
        await _maybe_chain_trim(slug, stage, video)
        return JSONResponse(project.model_dump(mode="json"))

    @app.post("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/beep/review")
    async def set_beep_reviewed(
        slug: str, stage_number: int, video_id: str, req: BeepReviewRequest
    ) -> JSONResponse:
        """Flip ``video.beep_reviewed`` (issue #71).

        Setting True requires ``beep_time`` to be set; setting False is
        always allowed (e.g. user wants to re-review). For a primary
        whose trim is already cached, marking True kicks off shot
        detection -- this is the explicit unblock point for the
        downstream pipeline (auto-detect leaves the flag False so the
        ensemble doesn't burn cycles on an unconfirmed beep, and we
        finally fire it here once the user has listened and approved).
        """
        project, stage, video = _resolve_stage_video(slug, stage_number, video_id)
        if req.reviewed and video.beep_time is None:
            raise HTTPException(
                status_code=400,
                detail="cannot mark a beep reviewed before one has been detected",
            )
        video.beep_reviewed = bool(req.reviewed)
        project.save(state.shooter_root(slug))

        # When the user confirms the primary's beep AND the trim is
        # already cached from the auto-detect chain, kick off the
        # gated shot-detect now. No-op when trim hasn't run yet (it
        # will run after, then auto-chain because the gate is open),
        # or for secondaries (no shot timeline of their own).
        if (
            req.reviewed
            and video.role == "primary"
            and video.processed.get("trim")
            and await state.jobs.find_active(kind="shot_detect", stage_number=stage_number) is None
        ):
            await state.jobs.submit(
                kind="shot_detect",
                stage_number=stage_number,
                args={"slug": slug, "stage_number": stage_number},
            )

        return JSONResponse(project.model_dump(mode="json"))

    @app.patch("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/camera-mount")
    def set_camera_mount(
        slug: str, stage_number: int, video_id: str, req: CameraMountRequest
    ) -> JSONResponse:
        """Override the heuristic ``camera_mount`` (issue #143).

        Validated against the fixture-schema ``CameraMount`` enum so
        downstream code can trust the stored value. ``None`` clears the
        override -- the next shot-detect run will use the artifact's
        default class instead of a per-class threshold.
        """
        project, _stage, video = _resolve_stage_video(slug, stage_number, video_id)
        from ..fixture_schema import CameraMount

        if req.mount is not None:
            try:
                CameraMount(req.mount)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"unknown camera mount {req.mount!r}; expected one of "
                        + ", ".join(m.value for m in CameraMount)
                    ),
                ) from exc
        video.camera_mount = req.mount
        project.save(state.shooter_root(slug))
        return JSONResponse(project.model_dump(mode="json"))

    @app.patch("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/camera-model")
    def set_camera_model(
        slug: str, stage_number: int, video_id: str, req: CameraModelRequest
    ) -> JSONResponse:
        """Override the ffprobed camera make + model (#303-followup).

        Used when ffprobe couldn't read the QuickTime tag (e.g. Meta
        Vanguard glasses) or guessed wrong. The SPA's Ingest screen
        presents a dropdown sourced from
        ``GET /api/calibrated-camera-models`` plus an "Other (generic
        headcam)" sentinel that clears back to ``None`` / ``None`` --
        the runtime then falls back to the engine-side generic-headcam
        amp floor (#304).

        Both fields must be supplied together or both ``None``: a
        half-filled pair would produce no usable lookup key but isn't
        what the UI ever sends, so we refuse it as a 400.
        """
        if (req.make is None) != (req.model is None):
            raise HTTPException(
                status_code=400,
                detail="camera-model: 'make' and 'model' must be supplied together or both null",
            )
        project, _stage, video = _resolve_stage_video(slug, stage_number, video_id)
        video.camera_make = req.make
        video.camera_model = req.model
        project.save(state.shooter_root(slug))
        return JSONResponse(project.model_dump(mode="json"))

    @app.get("/api/calibrated-camera-models")
    def list_calibrated_camera_models() -> JSONResponse:
        """Enumerate the camera models present in the shipped calibration.

        The SPA shows these as the dropdown options on the Ingest screen.
        Unknown models still get the runtime's generic-headcam fallback
        floor, but offering an explicit pick lets the user steer a
        Vanguard-shaped fixture to its tuned threshold even when
        ffprobe yielded nothing.

        Result order is descending floor (most aggressive cut first)
        so the most-trusted model sorts to the top; ties broken
        alphabetically.
        """
        runtime = _get_ensemble_runtime()
        floors = runtime.calibration.amp_floor_by_camera_model or {}
        displays = runtime.calibration.camera_model_metadata or {}
        rows: list[dict[str, Any]] = []
        for key, floor in floors.items():
            meta = displays.get(key) or {}
            rows.append(
                {
                    "key": key,
                    "make": meta.get("make", key.split(" ", 1)[0].title()),
                    "model": meta.get("model", key.split(" ", 1)[1].title() if " " in key else key.title()),
                    "amp_floor": float(floor),
                }
            )
        rows.sort(key=lambda r: (-r["amp_floor"], r["key"]))
        return JSONResponse({"models": rows})

    @app.post("/api/shooters/{slug}/stages/{stage_number}/beep/select")
    async def select_beep_candidate(slug: str, stage_number: int, req: BeepSelectRequest) -> JSONResponse:
        """Backward-compat shim: promote a ranked candidate on the primary.

        Lets the user fix a wrong auto-pick without typing a timestamp.
        Re-uses the auto-detect provenance because the time still came
        from the detector -- we only changed which candidate the project
        trusts. Triggers a re-trim so the cached audit clip lines up.
        """
        project = state.shooter_project(slug)
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
            state.shooter_root(slug), stage_number, primary, project=project
        )
        project.save(state.shooter_root(slug))
        await _maybe_chain_trim(slug, stage, primary)
        return JSONResponse(project.model_dump(mode="json"))

    def _resolve_audit_audio(
        slug: str, project: MatchProject, stage_number: int
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
                state.shooter_root(slug),
                stage_number,
                project.resolve_video_path(state.shooter_root(slug), primary.path),
                primary.beep_time,
                project=project,
                ffmpeg_binary=process_runtime().ffmpeg_binary,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except audio_helpers.AudioExtractionError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    def _resolve_video_audio(
        slug: str, project: MatchProject, stage_number: int, video: StageVideo
    ) -> audio_helpers.AuditAudioResult:
        """Per-video version of :func:`_resolve_audit_audio`.

        Always returns the full source WAV, regardless of role. The picker
        is where the user *questions* the current beep -- if it shows the
        trimmed audit clip, a beep that fell outside the trim window is
        invisible and can't be moved onto. The audit screen still reaches
        for the trimmed clip via the per-stage `/audio` + `/peaks`
        endpoints, which is the right consumer for that cache.

        ``beep_in_clip`` is just ``video.beep_time``; the WAV is in source
        time so no trim offset applies.
        """
        source = project.resolve_video_path(state.shooter_root(slug), video.path)
        try:
            audio_path = audio_helpers.ensure_video_audio(
                state.shooter_root(slug),
                stage_number,
                video,
                source,
                project=project,
                ffmpeg_binary=process_runtime().ffmpeg_binary,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except audio_helpers.AudioExtractionError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return audio_helpers.AuditAudioResult(
            audio_path=audio_path,
            beep_in_clip=video.beep_time,
            trimmed=False,
        )

    def _serve_beep_preview(
        slug: str,
        project: MatchProject,
        stage_number: int,
        video: StageVideo,
        t: float | None,
    ) -> FileResponse:
        """Build the ~1 s MP4 around ``t`` (or ``video.beep_time``) and serve it.

        Shared by the legacy primary endpoint and the per-video endpoint
        so both honour the same 404 / 424 / cache semantics.
        """
        source = project.resolve_video_path(state.shooter_root(slug), video.path)
        _ensure_source_reachable(stage_number, source)
        center = t if t is not None else video.beep_time
        if center is None:
            raise HTTPException(
                status_code=404,
                detail=f"stage {stage_number} video has no beep_time yet",
            )
        if center < 0:
            raise HTTPException(status_code=400, detail="t must be >= 0")
        thumbs_dir = project.thumbs_path(state.shooter_root(slug))
        try:
            clip = thumbnail_helpers.ensure_clip(
                source,
                cache_dir=thumbs_dir,
                center_time=float(center),
                duration_s=1.0,
                width=480,
                ffmpeg_binary=process_runtime().ffmpeg_binary,
            )
        except thumbnail_helpers.ThumbnailError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return FileResponse(clip, media_type="video/mp4", filename=clip.name)

    @app.get("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/beep-preview")
    def video_beep_preview(
        slug: str, stage_number: int, video_id: str, t: float | None = None
    ) -> FileResponse:
        """Serve a ~1 s MP4 around ``video``'s beep (or override ``t``)."""
        project, _stage, video = _resolve_stage_video(slug, stage_number, video_id)
        return _serve_beep_preview(slug, project, stage_number, video, t)

    @app.get("/api/shooters/{slug}/stages/{stage_number}/beep-preview")
    def stage_beep_preview(slug: str, stage_number: int, t: float | None = None) -> FileResponse:
        """Serve a tiny MP4 around the primary's beep timestamp (#27, #22).

        Default center is the primary's persisted ``beep_time``. The
        optional ``t`` query param overrides it so the BeepCandidates
        picker (#22) can preview alternative ranked candidates without
        promoting them first. Cache keys on (source mtime/size, center
        time, duration), so each distinct ``t`` gets its own cached clip.
        """
        project = state.shooter_project(slug)
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
        return _serve_beep_preview(slug, project, stage_number, primary, t)

    @app.get("/api/shooters/{slug}/stages/{stage_number}/audio")
    def stage_audio(slug: str, stage_number: int) -> FileResponse:
        """Serve the audit-clip WAV for ``stage_number``.

        Prefers the primary's audit WAV (extracted from the short-GOP trimmed
        MP4 produced by Sub 5 / #16) so the waveform timeline matches what
        the user is auditing. Falls back to the primary's full source WAV
        when no trimmed clip exists yet -- the SPA surfaces this with a
        "trim required" hint.
        """
        project = state.shooter_project(slug)
        result = _resolve_audit_audio(slug, project, stage_number)
        return FileResponse(
            result.audio_path,
            media_type="audio/wav",
            filename=result.audio_path.name,
        )

    @app.get("/api/shooters/{slug}/stages/{stage_number}/peaks")
    def stage_peaks(
        slug: str,
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
        project = state.shooter_project(slug)
        audit = _resolve_audit_audio(slug, project, stage_number)
        peaks = waveform_helpers.ensure_peaks(audit.audio_path, bins)
        payload = peaks.model_dump(mode="json")
        payload["beep_time"] = audit.beep_in_clip
        payload["trimmed"] = audit.trimmed
        return JSONResponse(payload)

    @app.get("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/audio")
    def video_audio(slug: str, stage_number: int, video_id: str) -> FileResponse:
        """Serve ``video``'s WAV for the per-video beep picker.

        Primary forwards to the legacy stage audio resolver so the
        trimmed audit clip is preferred. Secondary returns the full
        per-cam WAV (``stage<N>_cam_<vid>.wav``) -- no per-secondary
        trim clip exists yet, and the picker needs the entire clip so
        the user can find the buzzer / first-shot regardless of where
        the current beep estimate is.
        """
        project, _stage, video = _resolve_stage_video(slug, stage_number, video_id)
        result = _resolve_video_audio(slug, project, stage_number, video)
        return FileResponse(
            result.audio_path,
            media_type="audio/wav",
            filename=result.audio_path.name,
        )

    @app.get("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/peaks")
    def video_peaks(
        slug: str,
        stage_number: int,
        video_id: str,
        bins: int = Query(default=1200, ge=16, le=8192),
    ) -> JSONResponse:
        """Return ``bins`` peak magnitudes for ``video``'s WAV.

        Same shape as ``/api/stages/{n}/peaks`` so the SPA's waveform
        picker can take the same path for primary + secondary -- the
        only thing that varies between roles is the URL.
        """
        project, _stage, video = _resolve_stage_video(slug, stage_number, video_id)
        audit = _resolve_video_audio(slug, project, stage_number, video)
        peaks = waveform_helpers.ensure_peaks(audit.audio_path, bins)
        payload = peaks.model_dump(mode="json")
        payload["beep_time"] = audit.beep_in_clip
        payload["trimmed"] = audit.trimmed
        return JSONResponse(payload)

    @app.get("/api/shooters/{slug}/stages/{stage_number}/audit")
    def get_stage_audit(slug: str, stage_number: int) -> JSONResponse:
        """Return the stage's audit JSON (issue #15) if one has been written.

        Lives at ``<project>/audit/stage<N>.json`` -- the same path the
        existing audit-prep / audit-apply flow uses. Returns ``200 null``
        when no audit file exists yet (the audit screen treats this as
        "fresh -- start from candidates if any, otherwise empty
        markers"); 404 is reserved for genuinely-unknown stages so the
        SPA can distinguish.
        """
        project = state.shooter_project(slug)
        try:
            project.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        # Hosted: from state_docs (a worker's shot_detect result); local:
        # from disk. 200 null when no audit exists yet.
        payload, _ = state.load_audit(slug, stage_number)
        if payload is None:
            return JSONResponse(None)
        return JSONResponse(payload)

    @app.put("/api/shooters/{slug}/stages/{stage_number}/audit")
    def put_stage_audit(slug: str, stage_number: int, payload: dict[str, Any]) -> JSONResponse:
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
        project = state.shooter_project(slug)
        try:
            project.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        # Read the current version so the save is optimistic-locked. The
        # SPA PUT doesn't carry a version (it assumes last-writer-wins), so
        # this load-then-save has a tiny race window: if a concurrent
        # worker bumps the version in between, save_audit raises
        # StateConflictError -> 409 and the SPA re-fetches. Local: version
        # is always 0 and this is a plain atomic file write.
        _, version = state.load_audit(slug, stage_number)
        state.save_audit(slug, stage_number, payload, version=version)
        return JSONResponse(payload)

    @app.get("/api/shooters/{slug}/stages/{stage_number}/anomalies")
    def get_stage_anomalies(slug: str, stage_number: int) -> JSONResponse:
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
        project = state.shooter_project(slug)
        try:
            stg = project.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        audit_payload, _ = state.load_audit(slug, stage_number)
        if audit_payload is None:
            return JSONResponse({"anomalies": []})

        prim = stg.primary()
        beep_time = prim.beep_time if prim is not None and prim.beep_time is not None else 0.0
        shots = export_helpers.audit_shots_to_engine_shots(audit_payload, beep_time_in_source=beep_time)
        anomalies = report.detect_anomalies_structured(shots, beep_time, stg.time_seconds)
        return JSONResponse({"anomalies": [a.model_dump() for a in anomalies]})

    # ----------------------------------------------------------------------
    # Coach endpoints (#161). Read-only on shot timestamps -- all writes go
    # to the coaching annotation fields owned by ``splitsmith.coach``.
    # Storage is the same audit JSON Audit reads/writes. The coach is lazy:
    # GET surfaces the current rule's verdict + a stale flag without
    # mutating; reclassify persists auto entries; PATCH writes one shot's
    # coach fields.
    # ----------------------------------------------------------------------

    def _video_beep_in_clip(
        slug: str,
        project: MatchProject,
        stage_number: int,
        video: StageVideo,
    ) -> float | None:
        """Where the beep falls inside the clip the SPA will receive
        from ``/api/videos/stream`` for ``video``.

        When a per-video trimmed MP4 exists, the beep sits at
        ``min(beep_time, trim_pre_buffer_seconds)`` inside it (the trim
        starts at ``max(0, beep_time - pre_buffer)``). When there's no
        trim we fall through to the source clip and the beep is at
        ``beep_time`` directly. No ffmpeg / no audio extraction; in hosted
        mode this pays only a cheap ``storage.exists`` HEAD so the reported
        position agrees with the clip ``stream_video`` will actually serve
        (which pulls the worker-cut trim on demand).
        """
        if video.beep_time is None:
            return None
        trimmed = audio_helpers.trimmed_video_path(
            state.shooter_root(slug), stage_number, video, project=project
        )
        if audio_helpers.trim_available(project, trimmed):
            return min(video.beep_time, project.trim_pre_buffer_seconds)
        return video.beep_time

    def _coach_video_entries(slug: str, project: MatchProject, stg: Any) -> list[dict[str, Any]]:
        """Per-video metadata the SPA needs to seek every synced camera.

        Order mirrors VideoPanel's expectation: primary first, then
        secondaries by ``added_at``. ``beep_in_clip`` is the position
        inside the clip the SPA will actually be playing -- the source
        clip when no trim exists, the trimmed clip when one does.
        """
        primary = stg.primary()
        secondaries = sorted(
            (v for v in stg.videos if v.role == "secondary"),
            key=lambda v: v.added_at,
        )
        ordered_videos = ([primary] if primary is not None else []) + secondaries
        out: list[dict[str, Any]] = []
        for v in ordered_videos:
            out.append(
                {
                    "path": str(v.path),
                    "role": v.role,
                    "beep_in_clip": _video_beep_in_clip(slug, project, stg.stage_number, v),
                }
            )
        return out

    def _load_audit_for_coach(
        slug: str,
        stage_number: int,
    ) -> tuple[dict[str, Any], int, float | None, Any, MatchProject]:
        """Shared loader: validates the stage, reads the audit doc,
        returns (payload, version, primary_beep_in_clip, stage, project).
        404 when no audit doc exists yet. The version rides back so a
        coach mutation saves under the same optimistic lock."""
        project = state.shooter_project(slug)
        try:
            stg = project.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        payload, version = state.load_audit(slug, stage_number)
        if payload is None:
            raise HTTPException(
                status_code=404,
                detail=f"no audit JSON yet for stage {stage_number}",
            )
        prim = stg.primary()
        primary_beep_in_clip = (
            _video_beep_in_clip(slug, project, stage_number, prim) if prim is not None else None
        )
        return payload, version, primary_beep_in_clip, stg, project

    def _coach_save(slug: str, stage_number: int, payload: dict[str, Any], version: int) -> None:
        """Persist a coach-mutated audit doc under optimistic locking
        (hosted) / atomically to disk (local). A lost race -> 409."""
        state.save_audit(slug, stage_number, payload, version=version)

    def _build_coach_response(
        slug: str,
        payload: dict[str, Any],
        primary_beep_in_clip: float | None,
        stg: Any,
        project: MatchProject,
        cfg: CoachAutoClassifyConfig,
    ) -> dict[str, Any]:
        # Coach plays the same clip the audit screen serves -- typically
        # the project-local trimmed MP4 -- so every "absolute" time must
        # be in the served clip's coordinate system. Anchoring on
        # ``primary_beep_in_clip`` keeps Coach working when the source
        # SSD is unplugged. Falls back to 0.0 when the project hasn't
        # set a beep yet (older / partial audits).
        clip_anchor = primary_beep_in_clip if primary_beep_in_clip is not None else 0.0
        raw_shots = payload.get("shots") or []
        ordered = sorted(
            (s for s in raw_shots if isinstance(s, dict) and "ms_after_beep" in s),
            key=lambda s: float(s.get("ms_after_beep", 0)),
        )
        coach_shots: list[dict[str, Any]] = []
        prev_ms: float | None = None
        for s in ordered:
            ms = float(s["ms_after_beep"])
            time_from_beep = ms / 1000.0
            gap_s: float | None
            if prev_ms is None:
                gap_s = None
                split = time_from_beep  # draw
            else:
                gap_s = (ms - prev_ms) / 1000.0
                split = gap_s
            prev_ms = ms
            stale = coach_module.is_classification_stale(s, gap_s=gap_s, config=cfg)
            reload_hint = coach_module.reload_hinted(gap_s, cfg)
            coach_shots.append(
                {
                    "shot_number": int(s.get("shot_number", 0)),
                    "ms_after_beep": int(ms),
                    "time_from_beep": time_from_beep,
                    # In the served clip's coordinate system: where the
                    # SPA must seek the primary <video> for this shot.
                    "time_absolute": clip_anchor + time_from_beep,
                    "split": split,
                    "interval_class": s.get("interval_class"),
                    "interval_class_source": s.get("interval_class_source"),
                    "improvement_flag": bool(s.get("improvement_flag", False)),
                    "coaching_note": s.get("coaching_note"),
                    "stale": stale,
                    "reload_hint": reload_hint,
                }
            )
        return {
            "stage_number": stg.stage_number,
            "stage_name": stg.stage_name,
            # Where the beep falls in the served primary clip. Used by
            # the SPA's beep row + as the anchor for offsetting synced
            # secondaries (each ``videos[i].beep_in_clip`` mirrors this
            # field for that camera's served clip).
            "beep_time": clip_anchor,
            "videos": _coach_video_entries(slug, project, stg),
            "shots": coach_shots,
        }

    @app.get("/api/shooters/{slug}/stages/{stage_number}/coach")
    def get_stage_coach(slug: str, stage_number: int) -> JSONResponse:
        """Return the per-shot coach view for a stage.

        Read-only: the stored ``interval_class`` is surfaced as-is, plus a
        ``stale`` flag indicating whether the current rule disagrees. The
        client can call ``POST /coach/reclassify`` to persist the rule's
        verdict onto unset/auto entries. Returns ``200 null`` when the
        stage has no audit JSON yet (a normal pre-audit state); 404 is
        reserved for genuinely-unknown stage numbers.
        """
        project = state.shooter_project(slug)
        try:
            stg = project.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        audit_payload, _ = state.load_audit(slug, stage_number)
        if audit_payload is None:
            return JSONResponse(None)
        payload, _version, beep_in_clip, stg, project = _load_audit_for_coach(slug, stage_number)
        cfg = CoachAutoClassifyConfig()
        return JSONResponse(_build_coach_response(slug, payload, beep_in_clip, stg, project, cfg))

    @app.post("/api/shooters/{slug}/stages/{stage_number}/coach/reclassify")
    def reclassify_stage_coach(slug: str, stage_number: int) -> JSONResponse:
        """Force the auto-classifier to (re)write ``interval_class`` for
        every shot whose source is unset or ``"auto"``. Manual entries are
        preserved. Idempotent.
        """
        payload, version, beep_in_clip, stg, project = _load_audit_for_coach(slug, stage_number)
        cfg = CoachAutoClassifyConfig()
        shots = payload.get("shots") or []
        if not isinstance(shots, list):
            raise HTTPException(status_code=500, detail="audit shots is not a list")
        coach_module.classify_intervals_in_dicts(shots, cfg)
        events = list(payload.get("audit_events") or [])
        events.append(
            {
                "ts": _now_iso(),
                "kind": "coach_reclassify",
                "payload": {"shot_count": len(shots)},
            }
        )
        payload["audit_events"] = events
        _coach_save(slug, stage_number, payload, version)
        return JSONResponse(_build_coach_response(slug, payload, beep_in_clip, stg, project, cfg))

    @app.patch("/api/shooters/{slug}/stages/{stage_number}/shots/{shot_number}/coach")
    def patch_stage_shot_coach(
        slug: str,
        stage_number: int,
        shot_number: int,
        body: CoachShotPatchRequest,
    ) -> JSONResponse:
        """Patch the coaching annotation fields on one shot. Returns the
        updated coach response for the stage so the client can refresh.
        """
        payload, version, beep_in_clip, stg, project = _load_audit_for_coach(slug, stage_number)
        cfg = CoachAutoClassifyConfig()
        shots = payload.get("shots") or []
        if not isinstance(shots, list):
            raise HTTPException(status_code=500, detail="audit shots is not a list")
        target = next(
            (s for s in shots if isinstance(s, dict) and int(s.get("shot_number", -1)) == shot_number),
            None,
        )
        if target is None:
            raise HTTPException(
                status_code=404,
                detail=f"shot {shot_number} not found in stage {stage_number}",
            )
        try:
            coach_module.write_coach_fields(
                target,
                interval_class=body.interval_class,
                interval_class_source=body.interval_class_source,
                clear_class=body.clear_class,
                improvement_flag=body.improvement_flag,
                coaching_note=body.coaching_note,
                clear_note=body.clear_note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        events = list(payload.get("audit_events") or [])
        events.append(
            {
                "ts": _now_iso(),
                "kind": "coach_patch",
                "payload": {
                    "shot_number": shot_number,
                    "fields": coach_module.read_coach_fields(target),
                },
            }
        )
        payload["audit_events"] = events
        _coach_save(slug, stage_number, payload, version)
        return JSONResponse(_build_coach_response(slug, payload, beep_in_clip, stg, project, cfg))

    @app.get("/api/shooters/{slug}/stages/{stage_number}/coach/distributions")
    def get_stage_coach_distributions(slug: str, stage_number: int) -> JSONResponse:
        """Histograms + summary stats for one stage's coach annotations.

        Unset interval classes are computed in memory; nothing persists.
        Empty classes still appear in the response with count=0 so the
        UI can render an empty histogram without a special case.
        """
        payload, _version, _beep_in_clip, stg, _project = _load_audit_for_coach(slug, stage_number)
        cfg = CoachAutoClassifyConfig()
        shots = payload.get("shots") or []
        if not isinstance(shots, list):
            raise HTTPException(status_code=500, detail="audit shots is not a list")
        result = coach_distributions_module.stage_distributions(
            stage_number=stg.stage_number,
            stage_name=stg.stage_name,
            shots=shots,
            config=cfg,
        )
        return JSONResponse(result.model_dump())

    @app.get("/api/shooters/{slug}/coach/distributions")
    def get_match_coach_distributions(slug: str) -> JSONResponse:
        """Match-level distributions across every stage with an audit
        JSON. Stages without an audit are silently skipped -- they
        haven't been audited yet so they'd just dilute the average.
        """
        project = state.shooter_project(slug)
        cfg = CoachAutoClassifyConfig()
        triples: list[tuple[int, str, list[dict[str, Any]]]] = []
        for stg in project.stages:
            stage_payload, _ = state.load_audit(slug, stg.stage_number)
            if stage_payload is None:
                continue
            shots = stage_payload.get("shots") or []
            if not isinstance(shots, list):
                continue
            triples.append((stg.stage_number, stg.stage_name, shots))
        result = coach_distributions_module.match_distributions(stages=triples, config=cfg)
        return JSONResponse(result.model_dump())

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

    @app.get("/api/shooters/{slug}/videos/stream")
    def stream_video(
        slug: str,
        path: str = Query(...),
        kind: Literal["auto", "trim", "source"] = Query("auto"),
    ) -> FileResponse:
        """Serve a registered video file with HTTP Range support.

        ``kind`` selects which file backs the response:

        - ``trim``: per-video short-GOP MP4 produced by Sub 5 / #16
          (``<trimmed>/stage<N>_cam_<video_id>_trimmed.mp4``); 404 if not
          built yet. The re-encoded clip seeks frame-accurately, which
          is what makes the audit screen's drag-scrubbing feel
          responsive.
        - ``source``: the original camera file. Used while the trim is
          still building.
        - ``auto`` (default, for back-compat with non-audit callers):
          trim if present, source otherwise.

        The audit screen always passes an explicit ``kind`` so the file
        bound to a ``<video>`` element can't change mid-session when a
        background trim job completes -- a switch from source bytes to
        trim bytes mid-Range-request wedges the player ("source not
        found") and forced a full reload to recover.

        Validates that ``path`` matches a video registered to the project
        (any stage, any role, or unassigned) so the endpoint cannot be
        used as a generic file-read primitive.
        """
        root = state.shooter_root(slug)
        project = state.shooter_project(slug)
        located = project.find_video(Path(path))
        if located is None:
            raise HTTPException(
                status_code=404,
                detail=f"video not registered with project: {path}",
            )
        stage, video = located

        served_path: Path | None = None
        if kind in ("auto", "trim") and stage is not None:
            # Per-video short-GOP trim is keyed per role: each angle has
            # its own scrub clip cut around its own beep, so dragging
            # the audit playhead doesn't stall on a 4K MOV from a phone.
            # Hosted: a worker cut this trim into its own filesystem and
            # pushed it to storage; pull it down before serving the bytes.
            # Never invokes ffmpeg -- a missing trim falls through to the
            # source clip (kind=auto) or 404s (kind=trim), as before.
            trimmed = audio_helpers.pull_trimmed_video(root, stage.stage_number, video, project=project)
            if trimmed.exists():
                served_path = trimmed.resolve()
        if served_path is None:
            if kind == "trim":
                raise HTTPException(
                    status_code=404,
                    detail=f"trimmed clip not built yet for {path}",
                )
            served_path = project.resolve_video_path(root, video.path).resolve()
            # Same structured shape as detect-beep / trim / preview so
            # the SPA's "reconnect external storage" surface is uniform.
            _ensure_source_reachable(stage.stage_number if stage is not None else None, served_path)

        media_type = "video/mp4" if served_path.suffix.lower() == ".mp4" else "application/octet-stream"
        return FileResponse(served_path, media_type=media_type, filename=served_path.name)

    @app.get("/api/shooters/{slug}/fs/list", response_model=FsListing)
    def fs_list(
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
        """
        project = state.shooter_project(slug)
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
        suggested = _suggested_starts(project.last_scanned_dir)

        return FsListing(
            path=str(target),
            parent=parent,
            entries=entries,
            suggested_starts=suggested,
        )

    @app.get("/api/fs/list-dirs", response_model=FsListing)
    def fs_list_dirs(path: str | None = Query(default=None)) -> FsListing:
        """List a directory's child directories without a bound project.

        Used by the create-match folder picker, which needs to browse
        the filesystem *before* any project exists (so the shooter-
        scoped ``/api/shooters/{slug}/fs/list`` isn't reachable). The
        response shape mirrors :class:`FsListing` so the SPA can share
        rendering code, but ``entries`` is directories only -- no video
        probing, no thumbnails -- because this picker selects a *parent*
        folder for a new project, not media to ingest.

        Hidden entries (dot-prefixed) and broken symlinks are skipped.
        Permission errors surface as 403.
        """
        target = Path(path).expanduser() if path else _default_start(None)
        try:
            target = target.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise HTTPException(status_code=404, detail=f"path not found: {target}") from exc
        if not target.is_dir():
            raise HTTPException(status_code=400, detail=f"not a directory: {target}")

        try:
            children = sorted(target.iterdir(), key=lambda p: p.name.lower())
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        entries: list[FsEntry] = []
        for child in children:
            if child.name.startswith("."):
                continue
            try:
                if not child.is_dir():
                    continue
            except (PermissionError, OSError):
                continue
            entries.append(FsEntry(name=child.name, kind="dir"))

        parent = str(target.parent) if target.parent != target else None
        suggested = _suggested_starts(None)
        return FsListing(
            path=str(target),
            parent=parent,
            entries=entries,
            suggested_starts=suggested,
        )

    @app.get("/api/shooters/{slug}/fs/probe")
    def fs_probe(slug: str, path: str = Query(...)) -> JSONResponse:
        """Probe a single video file on demand: ffprobe + thumbnail extraction.

        Used by the SPA when a picker row came back with null fields (the
        list-time budget was exhausted, or ``probe=true`` wasn't passed).
        Cached results are returned without re-running the binaries. Also
        surfaces resolution/codec/size so the unassigned-tray rows can show
        enough metadata for the user to identify which clip is which.
        """
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

    @app.get("/api/shooters/{slug}/thumbnails/{cache_key}.jpg", include_in_schema=False)
    def serve_thumbnail(slug: str, cache_key: str) -> FileResponse:
        """Serve a cached thumbnail by its content-addressed key.

        Keys are 16-char hex from :func:`video_probe.source_cache_key`. We
        validate the key shape so we never accept an arbitrary path that
        could escape the thumbs directory.
        """
        if not cache_key.isalnum() or len(cache_key) > 32:
            raise HTTPException(status_code=400, detail="invalid thumbnail key")
        project = state.shooter_project(slug)
        thumbs_dir = project.thumbs_path(state.shooter_root(slug))
        candidate = thumbs_dir / f"{cache_key}.jpg"
        if not candidate.exists():
            raise HTTPException(status_code=404, detail="thumbnail not cached")
        return FileResponse(candidate, media_type="image/jpeg", filename=candidate.name)

    @app.post("/api/shooters/{slug}/videos/auto-match")
    def auto_match(slug: str) -> JSONResponse:
        project = state.shooter_project(slug)
        suggestions = project.auto_match(state.shooter_root(slug))
        return JSONResponse({str(stage_num): str(path) for stage_num, path in suggestions.items()})

    @app.post("/api/shooters/{slug}/videos/remove")
    def remove_video(slug: str, req: RemoveVideoRequest) -> JSONResponse:
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
        root = state.shooter_root(slug)
        project = state.shooter_project(slug)
        try:
            plan = project.remove_video(
                Path(req.video_path),
                root,
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

    @app.get("/api/shooters/{slug}/project/cleanup/plan")
    def cleanup_plan(
        slug: str,
        categories: str = Query("", description="Comma-separated category list."),
    ) -> JSONResponse:
        """Preview a cleanup plan without deleting anything.

        Empty / unknown categories yield an empty plan rather than 400 so
        the SPA can debounce-fetch as the user toggles checkboxes
        without worrying about partial selections.
        """
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
        plan = cleanup_module.plan_cleanup(project, state.shooter_root(slug), cats)
        return JSONResponse(plan.model_dump(mode="json"))

    @app.post("/api/shooters/{slug}/project/cleanup")
    async def cleanup_apply(slug: str, req: CleanupRequest) -> JSONResponse:
        """Apply a cleanup. Refuses while jobs are pending or running.

        Re-plans server-side: the client only sends categories, never
        paths. Any active job is treated as a hard block (409) -- mid-
        flight ffmpeg writes into ``trimmed/`` or audit JSON saves into
        ``audit/`` would race with the deletes and corrupt state.
        """
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
        plan = cleanup_module.plan_cleanup(project, root, cats)
        result = cleanup_module.apply_cleanup(plan, root=root)
        return JSONResponse(
            {
                "plan": plan.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
            }
        )

    @app.post("/api/shooters/{slug}/assignments/move")
    async def move_assignment(slug: str, req: MoveRequest) -> JSONResponse:
        root = state.shooter_root(slug)
        project = state.shooter_project(slug)
        try:
            project.assign_video(
                Path(req.video_path),
                to_stage_number=req.to_stage_number,
                role=req.role,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        project.save(root)

        # Auto-queue beep on assignment to a real stage (#67). Skip when
        # unassigning (to_stage_number=None) or marking ignored -- those
        # don't put the video into the pipeline.
        if req.to_stage_number is not None and req.role != "ignored":
            stage = project.stage(req.to_stage_number)
            video = next((v for v in stage.videos if str(v.path) == req.video_path), None)
            if video is not None:
                await _auto_queue_beep_if_needed(slug, project, req.to_stage_number, video)

        return JSONResponse(project.model_dump(mode="json"))

    @app.post("/api/shooters/{slug}/assignments/swap-primary")
    async def swap_primary(slug: str, req: SwapPrimaryRequest) -> JSONResponse:
        """Promote ``video_path`` to primary on ``stage_number``.

        Audit-safe: when the stage has shots in its audit JSON, refuses with
        a 409 response unless ``confirm=True`` is passed. On confirm, the
        audit JSON is renamed to ``.bak`` so a bad swap is recoverable, and
        the new primary's processed flags are cleared so detection re-runs.
        """
        root = state.shooter_root(slug)
        project = state.shooter_project(slug)
        try:
            stage = project.stage(req.stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        warns = project.primary_swap_warns(root, stage_number=req.stage_number)
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
                root=root,
                stage_number=req.stage_number,
                backup_audit=warns,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        project.save(root)

        # Auto-queue beep for the new primary (#67). swap_primary may
        # clear ``processed.beep`` to force re-detection on the new
        # video's audio; the helper picks up the cleared flag and queues
        # accordingly. No-op when the video already had a current beep.
        new_primary = project.stage(req.stage_number).primary()
        if new_primary is not None:
            await _auto_queue_beep_if_needed(slug, project, req.stage_number, new_primary)

        return JSONResponse(project.model_dump(mode="json"))

    @app.get("/api/shooters/{slug}/exports/overview")
    def export_overview(slug: str) -> JSONResponse:
        """Match-overview payload for the Analysis & Export screen.

        Returns one row per stage with audit + export status (shot count,
        pending candidates, file paths, last export time, ready-to-export
        flag). Pure stat: no detection, no rewriting of audit JSON.
        """
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
        rows = project.export_overview(state.shooter_root(slug), audit_docs=audit_docs)
        return JSONResponse({"stages": [r.model_dump(mode="json") for r in rows]})

    @app.get("/api/shooters/{slug}/exports/file/{filename:path}")
    def download_export_file(slug: str, filename: str) -> FileResponse:
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

    @app.post("/api/shooters/{slug}/stages/{stage_number}/export")
    async def export_stage(slug: str, stage_number: int, req: ExportStageRequest) -> JSONResponse:
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
        project = state.shooter_project(slug)
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
        # A stage is exportable once it has a real duration: either a
        # scoreboard import (``scorecard_updated_at`` set) OR a manually
        # entered time (``set_stage_time`` stamps ``time_seconds_manual``
        # for exactly the no-scoreboard flow). Blocking the manual case
        # left manual matches able to detect but not export -- the gate
        # only meant to reject untouched placeholders.
        if stage.time_seconds <= 0 or (stage.scorecard_updated_at is None and not stage.time_seconds_manual):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"stage {stage_number} is a placeholder; set a stage time "
                    "or import a scoreboard before exporting"
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
                stage_number, project.resolve_video_path(state.shooter_root(slug), primary.path)
            )

        existing = await state.jobs.find_active(kind="export", stage_number=stage_number)
        if existing is not None:
            return JSONResponse(existing.model_dump(mode="json"))
        job = await state.jobs.submit(
            kind="export",
            stage_number=stage_number,
            args={"slug": slug, "stage_number": stage_number, "req": req},
        )
        return JSONResponse(job.model_dump(mode="json"))

    @app.get("/api/match/templates")
    def list_match_templates() -> JSONResponse:
        """List export templates from built-in + user dirs (issue #198).

        Used by the export dialog to populate a "Template" dropdown.
        Each entry carries the parsed template body so the client can
        apply it client-side without a second request.
        """
        from .. import templates as templates_mod

        entries = templates_mod.list_templates()
        payload = [
            {
                "id": e.id,
                "source": e.source,
                "template": e.template.model_dump(exclude_none=True),
            }
            for e in entries
        ]
        return JSONResponse({"templates": payload})

    @app.post("/api/shooters/{slug}/export/match")
    async def export_match(slug: str, req: MatchExportRequest) -> JSONResponse:
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
            if not project.resolve_video_path(state.shooter_root(slug), primary.path).exists():
                _ensure_source_reachable(
                    stage_number,
                    project.resolve_video_path(state.shooter_root(slug), primary.path),
                )

        existing = await state.jobs.find_active(kind="match_export")
        if existing is not None:
            return JSONResponse(existing.model_dump(mode="json"))
        job = await state.jobs.submit(
            kind="match_export",
            args={"slug": slug, "req": req},
        )
        return JSONResponse(job.model_dump(mode="json"))

    def _reveal_in_file_manager(resolved: Path) -> None:
        """Launch the OS file manager for ``resolved``, surfacing failures.

        Without surfacing, a headless / minimal Linux install (no
        ``xdg-open``) or a Wayland session without DBUS silently
        swallows the click. Raising on nonzero exit lets the SPA toast.
        ``explorer /select`` is opted out because it returns 1 even on
        a successful selection -- treating that as failure would always
        toast on Windows.
        """
        if sys.platform == "darwin":
            cmd = ["open", "-R", str(resolved)]
            check_exit = True
        elif sys.platform.startswith("win"):
            cmd = ["explorer", f"/select,{resolved}"]
            check_exit = False
        else:
            # xdg-open doesn't support file selection; opening the parent
            # is the closest cross-distro behaviour.
            parent = resolved.parent if resolved.is_file() else resolved
            cmd = ["xdg-open", str(parent)]
            check_exit = True
        try:
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"failed to launch file manager: {exc}") from exc
        if check_exit and proc.returncode != 0:
            stderr = (proc.stderr or "").strip() or f"exit {proc.returncode}"
            raise HTTPException(
                status_code=500,
                detail=f"file manager refused to open {resolved}: {stderr}",
            )

    @app.post("/api/files/reveal")
    def reveal_file(req: RevealRequest) -> JSONResponse:
        """Reveal a file in the OS file manager.

        Restricted to paths inside the request-scoped match root so
        the endpoint can never be coerced into opening arbitrary
        locations. macOS uses ``open -R``; Linux uses ``xdg-open``
        on the parent dir; Windows uses ``explorer /select``.

        Tier 1 step 4 of doc 10 dropped the singleton bound root --
        callers must hit this through ``/api/matches/{match_id}/files/reveal``
        so the alias middleware sets ``current_match_root``; bare-path
        callers 409 ``no_project``.
        """
        target = Path(req.path).expanduser()
        try:
            resolved = target.resolve(strict=True)
        except (OSError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=f"not found: {target}") from exc
        try:
            resolved.relative_to(state.match_root.resolve())
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="reveal path must be inside the match folder",
            ) from exc
        _reveal_in_file_manager(resolved)
        return JSONResponse({"revealed": str(resolved)})

    @app.post("/api/shooters/{slug}/videos/reveal")
    def reveal_video(slug: str, req: RevealRequest) -> JSONResponse:
        """Reveal a registered project video in the OS file manager.

        The generic ``/api/files/reveal`` requires the resolved path to be
        inside the project root, which excludes registered videos whose
        symlinks under ``raw/`` point at the original on USB / external
        storage. This endpoint looks the path up in project state and
        reveals the symlink target -- the user already consented by
        registering the source via the picker, so revealing the original
        location is the natural action for "open containing folder".
        """
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
        _reveal_in_file_manager(resolved)
        return JSONResponse({"revealed": str(resolved)})

    @app.post("/api/shooters/{slug}/stages/{stage_number}/skip")
    def set_stage_skipped(slug: str, stage_number: int, req: SkipStageRequest) -> JSONResponse:
        """Mark ``stage_number`` as skipped (or un-skip it).

        A skipped stage is excluded from "next step" gating in the ingest
        screen so the user can advance even when the stage has no videos
        (e.g. they didn't film stage 4).
        """
        root = state.shooter_root(slug)
        project = state.shooter_project(slug)
        try:
            stage = project.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        stage.skipped = req.skipped
        project.save(root)
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

    @app.get("/api/me/recent-projects")
    async def list_recent_projects(
        detail: bool = Query(False),
        user: User = Depends(get_current_user),
    ) -> JSONResponse:
        """Return the recent-projects list.

        ``detail=false`` (default) returns the raw
        :class:`user_config.RecentProject` entries -- compatible with the
        legacy picker. ``detail=true`` enriches each entry with on-disk
        metadata (kind / shooters / stages / status) for the redesigned
        match picker (#322). The detailed shape is slightly slower; the
        picker route is the only caller that needs it.
        """
        projects = await state.recent_projects.list()
        if detail:
            enriched: list[RecentProjectDetail] = []
            for p in projects:
                # Hosted: resolve detail from Postgres so a redeploy-wiped
                # working dir doesn't show the match as "missing". Falls
                # back to the filesystem enricher (local mode, or no stored
                # match_id / a genuinely-gone match).
                hosted = await _enrich_recent_project_hosted(state, p)
                enriched.append(hosted if hosted is not None else _enrich_recent_project(p))
            return JSONResponse({"projects": [p.model_dump(mode="json") for p in enriched]})
        return JSONResponse({"projects": [p.model_dump(mode="json") for p in projects]})

    @app.post("/api/me/recent-projects/forget")
    async def forget_recent_project(
        req: ForgetRecentProjectRequest,
        user: User = Depends(get_current_user),
    ) -> JSONResponse:
        removed = await state.recent_projects.remove(Path(req.path))
        projects = await state.recent_projects.list()
        return JSONResponse(
            {
                "removed": removed,
                "projects": [p.model_dump(mode="json") for p in projects],
            }
        )

    @app.post("/api/me/recent-projects/bind")
    async def bind_recent_project(
        req: BindRecentProjectRequest,
        user: User = Depends(get_current_user),
    ) -> HealthResponse:
        """Switch the in-memory project. Used by the SPA picker route.

        Accepts a path that already exists on disk (a previously-opened
        project) or a fresh path -- ``MatchProject.init`` is idempotent
        and will scaffold the subdirs if missing. The ``last_opened_at``
        timestamp is bumped so the picker re-orders to the top.
        """
        target = Path(req.path).expanduser()
        resolved_str = str(target.resolve())

        # Hosted: resolve the match through Postgres, not the filesystem.
        # The recorded path is an ephemeral container working root that a
        # redeploy wipes, so requiring it to exist (below) 404'd every
        # reopen of an existing match. If the recent-projects row carries a
        # match_id and the tenant still owns it, bind succeeds regardless of
        # local disk -- the alias middleware re-establishes the working root
        # on the next /api/matches/{id}/ request.
        if state.matches_store is not None:
            entry = next(
                (p for p in await state.recent_projects.list() if p.path == resolved_str),
                None,
            )
            if entry is not None and entry.match_id:
                if await state.matches_store.get(entry.match_id) is None:
                    # Unknown / not owned -> same 404 an unknown id gets.
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "code": "match_not_found",
                            "message": f"unknown match_id {entry.match_id!r}",
                        },
                    )
                name = entry.name or req.name or target.name or "match"
                await state.recent_projects.record_open(
                    target, name, kind=entry.kind or "match", match_id=entry.match_id
                )
                return _register_response(target, name, entry.match_id)

        if not target.exists():
            if not req.create:
                # Conservative default: don't silently scaffold a brand-new
                # project on a typo. The "Create new project" flow on the
                # picker route passes ``create=true``.
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "project_path_missing",
                        "message": f"Project path does not exist: {target}",
                    },
                )
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            recorded, match_id = await _register_match_at(
                state,
                target,
                fallback_name=req.name or target.name or "match",
            )
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _register_response(target.resolve(), recorded, match_id)

    @app.post("/api/match/create-manual")
    async def create_match_manual(req: CreateMatchManualRequest) -> HealthResponse:
        """Scaffold a Match folder from the manual create-match form (#322).

        Creates ``<project_folder>/match.json`` with the supplied stages,
        registers the primary shooter under
        ``<project_folder>/shooters/<slug>/``, and binds the shooter's
        directory as the active legacy project so existing endpoints keep
        working. Folder is created if missing; refuses if a ``match.json``
        is already present (use bind to open existing matches).

        ``project_folder`` is optional in hosted mode -- the server
        synthesises ``users/<user_id>/projects/<slug>/`` under
        ``$SPLITSMITH_PROJECTS_DIR`` so the SPA doesn't have to expose a
        host filesystem picker (#425).
        """
        target = _resolve_create_target(state, project_folder=req.project_folder, name=req.name)
        if (target / match_model.MATCH_FILE).exists():
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "match_already_exists",
                    "message": f"{target} already contains match.json. Open it instead.",
                },
            )
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        match = match_model.Match.init(target, name=req.name)
        # Hosted: bind the state store so every save() from here INSERTs /
        # UPDATEs the match doc in Postgres (the ephemeral disk file
        # Match.init just wrote is abandoned). version=0 -> the save below
        # is the INSERT. Local: no store, saves stay file-based.
        if state.project_state is not None and match.match_id is not None:
            match.bind_state(state.project_state, match_id=match.match_id, version=0)
        match.match_date = req.match_date
        match.stages = [
            match_model.MatchStageDefinition(
                stage_number=draft.stage_number,
                stage_name=draft.stage_name or f"Stage {draft.stage_number}",
                stage_rounds=(
                    StageRounds(expected=draft.expected_rounds)
                    if draft.expected_rounds is not None and draft.expected_rounds > 0
                    else None
                ),
                placeholder=False,
            )
            for draft in req.stages
        ]
        match.save(target)

        shooter_slug = match_model.mint_shooter_slug()
        shooter = match_model.Shooter(
            slug=shooter_slug,
            name=req.primary_shooter.name,
            stages=[match_model.ShooterStageData(stage_number=draft.stage_number) for draft in req.stages],
        )
        try:
            match.add_shooter(target, shooter)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        shooter_root = match_model.Match.shooter_root(target, shooter_slug)
        # The legacy MatchProject inside the shooter dir needs to mirror the
        # match-level stage definitions so the existing audit/ingest endpoints
        # have stages to operate on. We seed it directly rather than relying
        # on the user re-importing scoreboard data.
        legacy = MatchProject.init(shooter_root, name=req.name)
        if state.project_state is not None and match.match_id is not None:
            legacy.bind_state(state.project_state, match_id=match.match_id, slug=shooter_slug, version=0)
        legacy.competitor_name = req.primary_shooter.name
        legacy.match_date = req.match_date
        if not legacy.stages:
            for draft in req.stages:
                legacy.stages.append(
                    StageEntry(
                        stage_number=draft.stage_number,
                        stage_name=draft.stage_name or f"Stage {draft.stage_number}",
                        time_seconds=0.0,
                        stage_rounds=(
                            StageRounds(expected=draft.expected_rounds)
                            if draft.expected_rounds is not None and draft.expected_rounds > 0
                            else None
                        ),
                        placeholder=False,
                    )
                )
        legacy.save(shooter_root)

        # Register the match folder so the alias middleware can
        # resolve its id immediately; record it in recent-projects
        # so the picker has the entry pinned to the top.
        try:
            recorded, match_id = await _register_match_at(state, target, fallback_name=req.name)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _register_response(target.resolve(), recorded, match_id)

    @app.post("/api/match/create-from-scoreboard")
    async def create_match_from_scoreboard(
        req: CreateMatchScoreboardRequest,
    ) -> HealthResponse:
        """Scaffold a Match folder for a scoreboard-imported match (#322).

        Creates the folder, fetches the upstream ``MatchData`` once, and
        materialises one shooter per :class:`CreateMatchCompetitorPick`:
        each gets a populated legacy ``MatchProject`` with stage shells
        from the match, the competitor pinned, and stage times merged.
        Then binds the match. The user lands on a match where every
        shooter is already ready for the audit/ingest flow.
        """
        if not req.competitors:
            raise HTTPException(
                status_code=400,
                detail="at least one competitor must be selected",
            )
        comp_ids_seen: set[int] = set()
        for pick in req.competitors:
            if pick.selected_competitor_id in comp_ids_seen:
                raise HTTPException(
                    status_code=400,
                    detail=(f"duplicate competitor {pick.selected_competitor_id} " "in picks"),
                )
            comp_ids_seen.add(pick.selected_competitor_id)

        target = _resolve_create_target(state, project_folder=req.project_folder, name=req.name)
        if (target / match_model.MATCH_FILE).exists():
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "match_already_exists",
                    "message": f"{target} already contains match.json. Open it instead.",
                },
            )
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        match = match_model.Match.init(target, name=req.name)
        if state.project_state is not None and match.match_id is not None:
            match.bind_state(state.project_state, match_id=match.match_id, version=0)
        match.scoreboard_match_id = str(req.match_id)
        match.scoreboard_content_type = req.content_type
        match.save(target)

        # Fetch the match shell once -- we'll reuse it for every shooter
        # via the project-local cache so subsequent get_match calls hit
        # disk rather than the network.
        with _resolve_scoreboard_client(target) as client:
            try:
                match_data = client.get_match(req.content_type, req.match_id)
            except MatchNotFound as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ScoreboardError as exc:
                _raise_scoreboard_http(exc)

        # Carry the stage shell onto the match itself BEFORE adding shooters
        # (mirrors the manual-create order). Without this match.stages stays
        # empty: a shooter added later -- whose project is mirrored from
        # match.stages -- would get zero stages, and coverage counts that
        # divide by len(match.stages) read 0. Per-shooter projects below are
        # populated independently via populate_from_match_data.
        match.stages_from_match_data(match_data)
        match.save(target)

        # Stable alphabetical materialisation so reruns / re-imports
        # land in the same shooter order. No "primary" shooter -- the
        # operator may not be in the roster at all (#350).
        ordered = sorted(req.competitors, key=lambda c: c.name.lower())
        for pick in ordered:
            shooter_slug = match_model.mint_shooter_slug(taken=set(match.shooters))
            shooter = match_model.Shooter(
                slug=shooter_slug,
                name=pick.name,
                selected_shooter_id=pick.selected_shooter_id,
                selected_competitor_id=pick.selected_competitor_id,
            )
            try:
                match.add_shooter(target, shooter)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            shooter_root = match_model.Match.shooter_root(target, shooter_slug)
            legacy = MatchProject.init(shooter_root, name=req.name)
            if state.project_state is not None and match.match_id is not None:
                legacy.bind_state(state.project_state, match_id=match.match_id, slug=shooter_slug, version=0)
            legacy.competitor_name = pick.name
            legacy.scoreboard_match_id = str(req.match_id)
            legacy.scoreboard_content_type = req.content_type
            legacy.selected_shooter_id = pick.selected_shooter_id
            legacy.selected_competitor_id = pick.selected_competitor_id
            try:
                legacy.populate_from_match_data(match_data, overwrite=False)
            except ScoreboardImportConflictError as exc:
                # Fresh project -- shouldn't happen, but surface clearly.
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            legacy.save(shooter_root)

            # Pull stage times. Failures here aren't fatal -- the user
            # can refresh later from the shooters page -- but we record
            # the failure on the per-shooter project so the caller knows
            # which ones came up short.
            try:
                _fetch_and_merge_stage_times(
                    shooter_root,
                    legacy,
                    req.content_type,
                    req.match_id,
                    pick.selected_competitor_id,
                )
            except HTTPException as exc:
                # Don't abort the whole create on one shooter's stage
                # times failing -- log and continue. The user can hit
                # "refresh times" once the upstream issue resolves.
                logger.warning(
                    "stage-times merge failed for %s (%s): %s",
                    pick.name,
                    shooter_slug,
                    exc.detail,
                )

        # Register the match folder so recent-projects gets a
        # kind="match" entry and the alias middleware resolves the id.
        try:
            recorded, match_id = await _register_match_at(state, target, fallback_name=req.name)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _register_response(target.resolve(), recorded, match_id)

    # ----------------------------------------------------------------------
    # Shooters management (#324)
    # ----------------------------------------------------------------------

    def _resolve_match_context() -> tuple[Path, match_model.Match]:
        """Return ``(match_root, match)`` for the bound project.

        Raises 409 ``not_a_match`` for legacy single-shooter layouts (the
        shooter-listing / merge / compare endpoints are match-only).
        """
        match_root = state.match_root
        return match_root, state.match()

    def _classify_shooter(shooter_root: Path, match: match_model.Match) -> ShooterListEntry:
        """Build a list entry for a single shooter directory."""
        # ``shooter_root.name`` is the slug; the accessor loads the project
        # doc from Postgres (hosted) or disk (local) + binds storage.
        legacy = state.shooter_project(shooter_root.name)
        # ``audited`` requires the operator to have hit Save & next on
        # the stage (see :func:`stage_audit_status`). Set-up-but-not-
        # touched stages used to count here; they no longer do.
        stages_audited = legacy.audited_count(shooter_root)
        stages_missing_trim = 0
        for s in legacy.stages:
            prim = next((v for v in s.videos if v.role == "primary"), None)
            if prim is None or prim.beep_time is None or s.time_seconds <= 0:
                continue
            try:
                source = legacy.resolve_video_path(shooter_root, prim.path)
                if not source.exists():
                    continue
            except Exception:  # noqa: BLE001 -- defensive
                continue
            cache = audio_helpers.trimmed_video_path(shooter_root, s.stage_number, prim, project=legacy)
            if not audio_helpers.trim_available(legacy, cache):
                stages_missing_trim += 1
        # Camera grouping: ``(make, model, mount)`` -> [(role, count, stages)].
        groups: dict[tuple[str | None, str | None, str | None], dict[str, Any]] = {}
        total_videos = 0
        for stage in legacy.stages:
            for video in stage.videos:
                if video.role == "ignored":
                    continue
                key = (video.camera_make, video.camera_model, video.camera_mount)
                g = groups.setdefault(
                    key,
                    {
                        "role": video.role,
                        "video_count": 0,
                        "stages": set(),
                    },
                )
                # Primary wins over secondary if any video on this camera is
                # primary on any stage (shooters tend to keep camera roles
                # consistent across stages).
                if video.role == "primary":
                    g["role"] = "primary"
                g["video_count"] += 1
                g["stages"].add(stage.stage_number)
                total_videos += 1
        cameras = [
            ShooterCameraInfo(
                group_key=f"{k[0] or ''}|{k[1] or ''}|{k[2] or ''}",
                make=k[0],
                model=k[1],
                mount=k[2],
                role=g["role"],
                video_count=g["video_count"],
                stage_numbers=sorted(g["stages"]),
            )
            for k, g in groups.items()
        ]
        return ShooterListEntry(
            slug=shooter_root.name,
            name=legacy.competitor_name or shooter_root.name,
            selected_shooter_id=legacy.selected_shooter_id,
            selected_competitor_id=legacy.selected_competitor_id,
            stages_audited=stages_audited,
            # Denominator must match the numerator's source: stages_audited
            # is counted over the shooter's OWN project (legacy.stages), so
            # the total has to be len(legacy.stages) too. Using
            # len(match.stages) breaks the ratio when the match-level stage
            # list wasn't populated (e.g. stages arrived via a per-shooter
            # scoreboard import, not match creation) -- that produced a "0
            # stages" card next to a 12-stage match.
            stages_total=len(legacy.stages),
            video_count=total_videos,
            cameras=cameras,
            stages_missing_trim=stages_missing_trim,
        )

    # ----------------------------------------------------------------------
    # Match merge (#332) -- consolidate N legacy single-shooter projects
    # into one redesign-era match folder via the SPA. Wraps the existing
    # match_model.plan_merge + execute_merge that the `splitsmith match
    # merge` CLI uses.
    # ----------------------------------------------------------------------

    def _plan_response_from_model(plan: match_model.MergePlan) -> MergePlanResponse:
        return MergePlanResponse(
            output_root=str(plan.output_root),
            name=plan.name,
            scoreboard_match_id=plan.scoreboard_match_id,
            scoreboard_content_type=plan.scoreboard_content_type,
            match_date=plan.match_date.isoformat() if plan.match_date else None,
            stages=[
                MergePlanStage(
                    stage_number=s.stage_number,
                    stage_name=s.stage_name,
                    expected_rounds=(s.stage_rounds.expected if s.stage_rounds is not None else None),
                    placeholder=s.placeholder,
                )
                for s in plan.stages
            ],
            shooter_moves=[
                MergePlanShooterMove(
                    source_root=str(mv.source_root),
                    slug=mv.slug,
                    destination_root=str(mv.destination_root),
                    competitor_name=mv.competitor_name,
                )
                for mv in plan.shooter_moves
            ],
        )

    @app.post("/api/match/merge/plan", response_model=MergePlanResponse)
    def merge_plan(req: MergePlanRequest) -> MergePlanResponse:
        """Dry-run a merge of N legacy single-shooter projects into one
        match folder. Returns the reconciled stage definitions + per-shooter
        slug assignments so the SPA can show the user what *would* happen
        before they commit.

        409 with the conflict message when stage definitions, scoreboard
        ids, or names disagree across inputs. The user reconciles those at
        the source before retrying.
        """
        if len(req.inputs) < 2:
            raise HTTPException(
                status_code=400,
                detail="merge requires at least two input projects",
            )
        inputs = [Path(p).expanduser() for p in req.inputs]
        for src in inputs:
            if not src.exists():
                raise HTTPException(status_code=400, detail=f"input not found: {src}")
            if not match_model.is_legacy_project_folder(src):
                raise HTTPException(
                    status_code=400,
                    detail=(f"{src} is not a legacy single-shooter project " "(no project.json)"),
                )
        # output is optional for plan -- the user may not have picked a
        # destination yet. Fall back to "(unset)" so the response still
        # validates; the SPA prompts for a real path before execute.
        output = Path(req.output).expanduser() if req.output else Path("(unset)")
        try:
            plan = match_model.plan_merge(inputs, output, name=req.name)
        except match_model.MergeConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _plan_response_from_model(plan)

    @app.post("/api/match/merge/execute", response_model=HealthResponse)
    async def merge_execute(req: MergeExecuteRequest) -> HealthResponse:
        """Execute a merge and bind the new match's first shooter as the
        active project so the user lands on a working session immediately.

        Refuses if the destination already contains ``match.json`` (the
        plan validation does the same check, but the user could race
        between plan + execute). Records the new match in the recent-
        projects index so the picker picks it up.
        """
        if len(req.inputs) < 2:
            raise HTTPException(
                status_code=400,
                detail="merge requires at least two input projects",
            )
        inputs = [Path(p).expanduser() for p in req.inputs]
        output = Path(req.output).expanduser()
        try:
            plan = match_model.plan_merge(inputs, output, name=req.name)
        except match_model.MergeConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            match = match_model.execute_merge(plan, move=req.move)
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        # Register the new match so recent-projects gets a kind="match"
        # entry and the alias middleware can resolve the id immediately.
        try:
            recorded, match_id = await _register_match_at(state, output, fallback_name=match.name)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return _register_response(output.resolve(), recorded, match_id)

    @app.get("/api/match/shooters", response_model=ShooterListResponse)
    def list_match_shooters() -> ShooterListResponse:
        """List every shooter in the currently-bound match with coverage."""
        match_root, match = _resolve_match_context()
        entries: list[ShooterListEntry] = []
        for slug in match.shooters:
            shooter_root = match_model.Match.shooter_root(match_root, slug)
            try:
                entries.append(_classify_shooter(shooter_root, match))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping shooter %s: %s", slug, exc)
                continue
        return ShooterListResponse(
            match_root=str(match_root),
            match_name=match.name,
            shooters=entries,
        )

    @app.post("/api/match/shooters", response_model=ShooterListResponse)
    def add_match_shooter(req: AddShooterRequest) -> ShooterListResponse:
        """Add a new shooter to the bound match.

        Creates the ``<match>/shooters/<slug>/`` tree, mirrors the match's
        stage definitions into the shooter's ``project.json``, and saves a
        ``shooter.json`` next to it. Returns the refreshed list. Slugs are
        opaque random ids (``s_<hex>``) so URLs / disk paths don't leak
        competitor names.
        """
        match_root, match = _resolve_match_context()
        if not req.name.strip():
            raise HTTPException(status_code=400, detail="name is required")
        slug = match_model.mint_shooter_slug(set(match.shooters))
        shooter = match_model.Shooter(
            slug=slug,
            name=req.name,
            stages=[match_model.ShooterStageData(stage_number=s.stage_number) for s in match.stages],
        )
        try:
            match.add_shooter(match_root, shooter)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        shooter_root = match_model.Match.shooter_root(match_root, slug)
        legacy = MatchProject.init(shooter_root, name=match.name)
        # Hosted: bind the new project doc (version=0 -> the save below
        # INSERTs it into state_docs). ``match.add_shooter`` above already
        # persisted the updated roster to the match doc.
        if state.project_state is not None and match.match_id is not None:
            legacy.bind_state(state.project_state, match_id=match.match_id, slug=slug, version=0)
        legacy.competitor_name = req.name
        legacy.match_date = match.match_date
        legacy.scoreboard_match_id = match.scoreboard_match_id
        legacy.scoreboard_content_type = match.scoreboard_content_type
        if not legacy.stages:
            for sd in match.stages:
                legacy.stages.append(
                    StageEntry(
                        stage_number=sd.stage_number,
                        stage_name=sd.stage_name,
                        time_seconds=0.0,
                        stage_rounds=sd.stage_rounds,
                        placeholder=sd.placeholder,
                    )
                )
        legacy.save(shooter_root)
        return list_match_shooters()

    @app.delete("/api/match/shooters/{slug}", response_model=ShooterListResponse)
    def remove_match_shooter(slug: str) -> ShooterListResponse:
        """Remove a shooter from the bound match.

        Drops the slug from ``match.json`` and deletes
        ``<match>/shooters/<slug>/`` from disk.
        """
        match_root, match = _resolve_match_context()
        if slug not in match.shooters:
            raise HTTPException(status_code=404, detail="shooter not found")
        shooter_root = match_model.Match.shooter_root(match_root, slug)
        if shooter_root.exists():
            shutil.rmtree(shooter_root, ignore_errors=True)
        match.shooters = [s for s in match.shooters if s != slug]
        match.save(match_root)
        # The store-bound match.save above persists the dropped roster.
        # (The shooter's now-orphaned project/audit state_docs rows are
        # unreachable -- shooter_root rejects the slug -- and are left for a
        # future cascade-delete; harmless meanwhile.)
        return list_match_shooters()

    @app.post("/api/match/shooters/{slug}/build-trim-caches")
    async def build_shooter_trim_caches(slug: str) -> JSONResponse:
        """Submit trim-cache jobs for every stage in ``slug``'s project
        where the audit-mode short-GOP MP4 is missing (#351).

        Operates against the shooter's project root directly rather than
        the bound ``state``, so the user can regenerate Mathias's caches
        while staying on Anton's audit screen. Each stage with a primary
        + beep + stage_time + reachable source becomes one trim job in
        the existing JobRegistry; the SPA's JobsPanel surfaces them.
        Stages already cached are skipped silently (the underlying
        ``ensure_video_audit_trim`` is idempotent, but skipping early
        avoids the queue churn).
        """
        match_root, match = _resolve_match_context()
        if slug not in match.shooters:
            raise HTTPException(status_code=404, detail="shooter not found")
        shooter_root = match_model.Match.shooter_root(match_root, slug)
        try:
            proj = state.shooter_project(shooter_root.name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        jobs_submitted: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for stage in proj.stages:
            primary = next((v for v in stage.videos if v.role == "primary"), None)
            if primary is None:
                skipped.append({"stage": stage.stage_number, "reason": "no_primary"})
                continue
            if primary.beep_time is None:
                skipped.append({"stage": stage.stage_number, "reason": "no_beep"})
                continue
            if stage.time_seconds <= 0:
                skipped.append({"stage": stage.stage_number, "reason": "no_stage_time"})
                continue
            try:
                source = proj.resolve_video_path(shooter_root, primary.path)
            except Exception:  # noqa: BLE001 -- defensive
                skipped.append({"stage": stage.stage_number, "reason": "source_unreachable"})
                continue
            if not source.exists():
                skipped.append({"stage": stage.stage_number, "reason": "source_missing"})
                continue
            cache = audio_helpers.trimmed_video_path(shooter_root, stage.stage_number, primary, project=proj)
            if audio_helpers.trim_available(proj, cache):
                skipped.append({"stage": stage.stage_number, "reason": "already_cached"})
                continue
            # video_id is a hash of the source path so it's unique across
            # shooters -- the JobRegistry dedup key (kind, stage, video_id)
            # won't collide with the same stage on a different shooter.
            existing = await state.jobs.find_active(
                kind="trim",
                stage_number=stage.stage_number,
                video_id=primary.video_id,
            )
            if existing is not None:
                jobs_submitted.append(existing.model_dump(mode="json"))
                continue
            job = await state.jobs.submit(
                kind="trim",
                stage_number=stage.stage_number,
                video_id=primary.video_id,
                args={
                    "slug": slug,
                    "stage_number": stage.stage_number,
                    "video_id": primary.video_id,
                    "chain_shot_detect": False,
                },
            )
            jobs_submitted.append(job.model_dump(mode="json"))

        return JSONResponse(
            {
                "shooter_slug": slug,
                "shooter_name": proj.competitor_name,
                "jobs_submitted": jobs_submitted,
                "skipped": skipped,
            }
        )

    # ----------------------------------------------------------------------
    # Stage compare (#328)
    # ----------------------------------------------------------------------

    @app.get(
        "/api/match/stage/{stage_number}/compare",
        response_model=CompareStageResponse,
    )
    def get_stage_compare(stage_number: int) -> CompareStageResponse:
        """Per-shooter compare data for a stage.

        Each shooter contributes their lossless trim path (if built),
        ``beep_offset_in_clip`` so the SPA can align all clips to time-
        since-beep, and the shot list as a list of ``time_after_beep``
        scalars. Shooters with no trim still appear so the SPA can render
        empty tiles instead of silently dropping the slot.
        """
        match_root, match = _resolve_match_context()
        try:
            stage_def = match.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        records: list[CompareShooterRecord] = []
        for slug in match.shooters:
            shooter_root = match_model.Match.shooter_root(match_root, slug)
            try:
                legacy = state.shooter_project(shooter_root.name)
            except (FileNotFoundError, HTTPException):
                continue
            stage = next(
                (s for s in legacy.stages if s.stage_number == stage_number),
                None,
            )
            primary = (
                next((v for v in stage.videos if v.role == "primary"), None) if stage is not None else None
            )

            beep_offset: float | None = None
            duration_seconds: float | None = None
            video_path: str | None = None
            if stage is not None and primary is not None and primary.beep_time is not None:
                # Prefer the lossless export (same file FCPXML references);
                # fall back to the audit-mode short-GOP cache produced
                # during trim+audit. Both are cuts of the same source at
                # ``beep_time - pre_buffer``, so beep alignment is identical;
                # the audit cache is the H.264 version the browser already
                # streams during scrubbing. Either is fine for sync playback,
                # and the fallback lets compare just work after audit
                # without requiring a separate lossless export pass.
                stage_name = stage_def.stage_name
                base = f"stage{stage_number}_{match_export_helpers._slugify(stage_name)}"
                exports = (
                    Path(legacy.exports_dir).expanduser() if legacy.exports_dir else shooter_root / "exports"
                )
                if not exports.is_absolute():
                    exports = shooter_root / exports
                trimmed = (
                    Path(legacy.trimmed_dir).expanduser() if legacy.trimmed_dir else shooter_root / "trimmed"
                )
                if not trimmed.is_absolute():
                    trimmed = shooter_root / trimmed
                lossless = exports / f"{base}_trimmed.mp4"
                audit_cache = trimmed / f"stage{stage_number}_cam_{primary.video_id}_trimmed.mp4"
                resolved_trim = (
                    lossless if lossless.exists() else (audit_cache if audit_cache.exists() else None)
                )
                if resolved_trim is not None:
                    video_path = str(resolved_trim)
                    beep_offset = min(legacy.trim_pre_buffer_seconds, primary.beep_time)

            # Shots come from audit; convert each shot.time to time-after-beep.
            shots: list[CompareShotPoint] = []
            if stage is not None and primary is not None and primary.beep_time is not None:
                # ``shooter_root.name`` is the slug; load the audit doc from
                # state_docs (hosted) or disk (local).
                audit_data, _ = state.load_audit(shooter_root.name, stage_number)
                if isinstance(audit_data, dict):
                    for shot in audit_data.get("shots") or []:
                        t = shot.get("time")
                        if t is None:
                            continue
                        # Audit stores time in the trim's local clock;
                        # subtract the trim's beep offset to get
                        # time-since-beep.
                        audit_beep = audit_data.get("beep_time")
                        if audit_beep is None:
                            continue
                        shots.append(
                            CompareShotPoint(
                                shot_number=int(shot.get("shot_number", 0)),
                                time_after_beep=float(t) - float(audit_beep),
                                source=("manual" if shot.get("source") == "manual" else "detected"),
                            )
                        )

            records.append(
                CompareShooterRecord(
                    slug=slug,
                    name=legacy.competitor_name or slug,
                    video_path=video_path,
                    beep_offset_in_clip=beep_offset,
                    duration_seconds=duration_seconds,
                    stage_time_seconds=(stage.time_seconds if stage is not None else None),
                    shots=shots,
                )
            )

        return CompareStageResponse(
            stage_number=stage_number,
            stage_name=stage_def.stage_name,
            shooters=records,
        )

    @app.get("/api/match/shooters/{slug}/videos/stream")
    def stream_shooter_video(
        slug: str,
        path: str = Query(...),
    ) -> FileResponse:
        """Serve a video registered to any shooter in the bound match (#328).

        The Compare view streams from up to N shooters at once; the
        regular /api/videos/stream endpoint only sees the active
        shooter's registry. This thin wrapper validates ``path`` against
        the named shooter's project then returns a FileResponse on the
        resolved trim/source.
        """
        match_root, match = _resolve_match_context()
        if slug not in match.shooters:
            raise HTTPException(status_code=404, detail="shooter not found")
        shooter_root = match_model.Match.shooter_root(match_root, slug)
        try:
            shooter_project = state.shooter_project(shooter_root.name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"shooter project missing: {exc}") from exc

        target = Path(path)
        # Allow either a registered raw/source path OR a trim file we wrote
        # ourselves: the lossless export under exports/ (FCPXML-grade) or
        # the audit-mode short-GOP cache under trimmed/ (compare's fallback
        # when no lossless export exists yet).
        located = shooter_project.find_video(target)
        served_path: Path | None = None
        if located is not None:
            stage, video = located
            served_path = shooter_project.resolve_video_path(shooter_root, video.path).resolve()
        else:
            resolved = target.expanduser().resolve()
            exports_dir = (
                Path(shooter_project.exports_dir).expanduser().resolve()
                if shooter_project.exports_dir
                else (shooter_root / "exports").resolve()
            )
            trimmed_dir = (
                Path(shooter_project.trimmed_dir).expanduser().resolve()
                if shooter_project.trimmed_dir
                else (shooter_root / "trimmed").resolve()
            )
            in_allowed_dir = False
            for allowed in (exports_dir, trimmed_dir):
                try:
                    resolved.relative_to(allowed)
                    in_allowed_dir = True
                    break
                except ValueError:
                    continue
            if not in_allowed_dir:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"path {path} is neither a registered video nor inside "
                        f"the shooter's exports/ or trimmed/ dirs"
                    ),
                )
            if not resolved.exists():
                raise HTTPException(status_code=404, detail=f"file not found: {resolved}")
            served_path = resolved

        media_type = "video/mp4"
        return FileResponse(served_path, media_type=media_type)

    # ----------------------------------------------------------------------
    # Cross-shooter beep review queue (#326)
    # ----------------------------------------------------------------------

    @app.get("/api/match/beep-queue", response_model=BeepQueueResponse)
    def get_beep_queue(include_confirmed: bool = Query(default=False)) -> BeepQueueResponse:
        """Pending beep items across every shooter in the bound match.

        Surfaces three pending states per primary video:
          - ``missing``: detector hasn't run or didn't find a beep
          - ``low_confidence``: detector found one but below the project's
            auto-trust threshold
          - ``unreviewed``: detector found one above threshold but the
            user hasn't yet listened + approved

        When ``include_confirmed`` is true the response also includes
        items whose beep has been reviewed, with ``status="confirmed"``.
        The SPA uses this for the "Show confirmed" toggle so the user
        can revisit a settled beep without having to clear-and-redo it.

        Items are grouped by stage; per-stage shot detection is gated on
        every shooter's primary in that stage being ``beep_reviewed``.
        """
        match_root, match = _resolve_match_context()
        # Resolve the low-confidence threshold from any shooter's automation
        # settings; per-shooter thresholds aren't supported, the gate is
        # global to the match.
        threshold = 0.5
        for first_slug in match.shooters:
            try:
                proj_for_threshold = state.shooter_project(first_slug)
                resolved = automation_settings.resolve_automation(proj_for_threshold)
                threshold = resolved.settings.beep_low_confidence_threshold
                break
            except Exception:  # noqa: BLE001
                continue

        stage_lookup = {s.stage_number: s.stage_name for s in match.stages}
        groups: dict[int, BeepQueueStageGroup] = {}
        total_pending = 0
        total_confirmed = 0
        total_primaries = 0

        for slug in match.shooters:
            shooter_root = match_model.Match.shooter_root(match_root, slug)
            try:
                proj = state.shooter_project(shooter_root.name)
            except (FileNotFoundError, HTTPException):
                continue
            for stage in proj.stages:
                if stage.skipped:
                    continue
                primary = next((v for v in stage.videos if v.role == "primary"), None)
                if primary is None:
                    continue
                total_primaries += 1
                grp = groups.setdefault(
                    stage.stage_number,
                    BeepQueueStageGroup(
                        stage_number=stage.stage_number,
                        stage_name=stage_lookup.get(stage.stage_number, stage.stage_name),
                        items=[],
                        total_primaries=0,
                        confirmed=0,
                    ),
                )
                grp.total_primaries += 1
                # Status classification.
                if primary.beep_time is None:
                    status = "missing"
                elif primary.beep_reviewed:
                    grp.confirmed += 1
                    total_confirmed += 1
                    if not include_confirmed:
                        continue
                    status = "confirmed"
                elif primary.beep_confidence is not None and primary.beep_confidence < threshold:
                    status = "low_confidence"
                else:
                    status = "unreviewed"
                if status != "confirmed":
                    total_pending += 1
                alts = [
                    BeepQueueAltCandidate(
                        time=cand.time,
                        confidence=cand.confidence,
                    )
                    for cand in (primary.beep_candidates or [])[:3]
                ]
                grp.items.append(
                    BeepQueueItem(
                        slug=slug,
                        shooter_name=proj.competitor_name or slug,
                        stage_number=stage.stage_number,
                        stage_name=stage_lookup.get(stage.stage_number, stage.stage_name),
                        video_id=primary.video_id,
                        video_path=primary.path.as_posix(),
                        beep_time=primary.beep_time,
                        beep_confidence=primary.beep_confidence,
                        beep_reviewed=primary.beep_reviewed,
                        status=status,
                        alt_candidates=alts,
                    )
                )

        ordered_stages = sorted(groups.values(), key=lambda g: g.stage_number)
        return BeepQueueResponse(
            total_items=total_primaries,
            pending_count=total_pending,
            confirmed_count=total_confirmed,
            stages=ordered_stages,
        )

    @app.post("/api/match/beep-queue/confirm", response_model=BeepQueueResponse)
    def confirm_beep_in_queue(req: BeepQueueConfirmRequest) -> BeepQueueResponse:
        """Confirm a beep on any shooter without changing the bound state.

        Writes through to the named shooter's ``project.json`` directly so
        the rest of the SPA's state (active shooter) is unaffected.
        When ``time`` is supplied, it overrides ``beep_time``; the call
        always sets ``beep_reviewed = True``.
        """
        match_root, match = _resolve_match_context()
        if req.slug not in match.shooters:
            raise HTTPException(status_code=404, detail="shooter not found")
        shooter_root = match_model.Match.shooter_root(match_root, req.slug)
        try:
            proj = state.shooter_project(shooter_root.name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        target_stage = next(
            (s for s in proj.stages if s.stage_number == req.stage_number),
            None,
        )
        if target_stage is None:
            raise HTTPException(status_code=404, detail="stage not found")
        target_video = next((v for v in target_stage.videos if v.video_id == req.video_id), None)
        if target_video is None:
            raise HTTPException(status_code=404, detail="video not found")
        if req.time is not None:
            target_video.beep_time = float(req.time)
            target_video.beep_source = "manual" if req.source == "manual" else "detected"
        if target_video.beep_time is None:
            raise HTTPException(
                status_code=400,
                detail="cannot mark a beep reviewed before one has been detected",
            )
        target_video.beep_reviewed = True
        proj.save(shooter_root)
        return get_beep_queue()

    # ``/api/me/recent-projects/unbind`` was deleted in Tier 1
    # step 4 of doc 10. There is no server-side bound state to
    # clear; the SPA picker navigates between matches by URL.

    @app.get("/api/me/scoreboard-identity")
    async def get_scoreboard_identity(user: User = Depends(get_current_user)) -> JSONResponse:
        # "Not pinned yet" is a normal state, not an error -- return a
        # 200 with a null body so the SPA doesn't have to catch a 404
        # on every page load and DevTools doesn't log a failed request.
        identity = await state.scoreboard_identity.load()
        if identity is None:
            return JSONResponse(None)
        return JSONResponse(identity.model_dump(mode="json"))

    @app.put("/api/me/scoreboard-identity")
    async def put_scoreboard_identity(
        req: ScoreboardIdentityRequest,
        user: User = Depends(get_current_user),
    ) -> JSONResponse:
        identity = user_config.ScoreboardIdentity(
            shooter_id=req.shooter_id,
            display_name=req.display_name,
            division=req.division,
            club=req.club,
            base_url=req.base_url,
        )
        await state.scoreboard_identity.save(identity)
        return JSONResponse(identity.model_dump(mode="json"))

    @app.delete("/api/me/scoreboard-identity")
    async def delete_scoreboard_identity(user: User = Depends(get_current_user)) -> JSONResponse:
        await state.scoreboard_identity.clear()
        return JSONResponse({"ok": True})

    @app.get("/api/server/features")
    def server_features() -> JSONResponse:
        """Surface server-side feature flags to the SPA on first load.

        Fields:

        - ``lab`` -- the developer-facing Algorithm Lab page; off by
          default, opt-in via ``splitsmith ui --lab``. The SPA hides
          the Lab nav entry when this is False so end users don't trip
          into a multi-second model-loading workflow they didn't ask
          for.
        - ``mode`` -- ``"local"`` (default ``splitsmith ui``) or
          ``"hosted"`` (``splitsmith serve`` + ``SPLITSMITH_MODE=hosted``).
          The SPA branches on this to suppress filesystem-picker UX
          that's meaningless against an ephemeral hosted container.
        """
        mode = "hosted" if _hosted_mode_active() else "local"
        return JSONResponse({"lab": lab_enabled, "mode": mode})

    # ----------------------------------------------------------------------
    # Lab: fixture management + ensemble eval + tuning
    # ----------------------------------------------------------------------
    #
    # End-user-visible only via the /lab route in the SPA. Heavy CLAP/PANN
    # runtime is loaded once on first /api/lab/eval call and cached on the
    # FastAPI app instance so subsequent eval / rescore calls amortise it.
    # The whole block is gated on ``lab_enabled`` so end-user installs
    # don't expose the developer-only routes (or carry the import cost).

    def _setup_lab() -> None:
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

        @app.get("/api/lab/last-run")
        def lab_last_run() -> JSONResponse:
            """Return the most recent eval/rescore in this server session.

            Lets the SPA hydrate after a navigation away from /lab so
            clicking around doesn't wipe the eval state. 404 when no eval
            has been run yet (or when /api/lab/labels invalidated the
            cache).
            """
            last_run = _lab_universe_cache.get("last_run")
            if last_run is None:
                raise HTTPException(status_code=404, detail="no eval has been run yet")
            return JSONResponse(last_run.model_dump(mode="json"))

        @app.post("/api/lab/eval")
        async def lab_eval(
            payload: dict[str, Any] = Body(default_factory=dict),  # noqa: B008
        ) -> JSONResponse:
            """Submit a lab-eval job. Returns a ``Job`` snapshot immediately;
            the SPA polls ``/api/jobs/{id}`` and then fetches the result via
            ``/api/lab/last-run`` once the job succeeds. Multi-second on the
            12 fixtures, so doing this inline blocked the request and
            deprived the JobsPanel of progress visibility.
            """
            slugs = payload.get("slugs")
            cfg_payload = payload.get("config") or {}
            cfg = lab_module.EvalConfig.model_validate(cfg_payload)
            persist = bool(payload.get("persist", True))
            wanted_slugs = slugs if isinstance(slugs, list) else None

            def _run(handle: JobHandle) -> None:
                handle.update(progress=0.0, message="Loading ensemble runtime...")
                runtime = _get_lab_runtime()

                def progress(i: int, total: int, slug: str) -> None:
                    handle.check_cancel()
                    handle.update(
                        progress=i / total if total else 1.0,
                        message=f"{slug} ({i}/{total})",
                    )

                run = lab_module.run_eval(
                    runtime,
                    slugs=wanted_slugs,
                    config=cfg,
                    progress=progress,
                )
                if persist:
                    try:
                        lab_module.save_run(run)
                    except OSError as exc:
                        logger.warning("lab: save_run failed: %s", exc)
                _lab_universe_cache["universe"] = run.universe
                _lab_universe_cache["last_run"] = run
                handle.update(progress=1.0, message=f"done ({len(run.universe.fixtures)} fixtures)")

            # Lab kinds are dev-only and never deferred to a hosted worker,
            # so the body closes over this request's params and is
            # registered just-in-time. submit() resolves the body
            # synchronously before any await, so a concurrent lab request
            # re-registering the same kind can't steal this submission.
            state.jobs.bodies.register("lab_eval", _run)
            job = await state.jobs.submit(kind="lab_eval")
            return JSONResponse(job.model_dump(mode="json"))

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
            project = state.shooter_project(slug)
            try:
                stg = project.stage(stage_n)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            audit_json = state.materialize_audit(slug, stage_n)
            try:
                audit_audio = _resolve_audit_audio(slug, project, stage_n)
            except HTTPException:
                raise
            audit_wav = audit_audio.audio_path
            if not audit_json.exists() or not audit_wav.exists():
                raise HTTPException(
                    status_code=409,
                    detail="stage has no audit JSON / WAV; run shot-detect first",
                )
            # Refuse to promote without an explicit shooter pin (#149
            # follow-up). Fixture training data is only useful when we
            # can attribute it to a known shooter; the legacy ``self``
            # sentinel is for migrating pre-existing corpora, not for
            # new promotions. Pin via the SSI search on the Ingest page.
            if project.selected_shooter_id is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "this project has no SSI shooter pinned; "
                        "promote refuses to land a fixture with an "
                        "unknown shooter. Pin yourself (or the shooter "
                        "whose run this is) via the Ingest page first."
                    ),
                )
            # PII-free shooter stamp: just the deterministic token.
            # SSI ID + competitor name stay in the private project file.
            token = lab_module.shooter_token(project.selected_shooter_id)
            shooter_payload: dict[str, Any] = {"id": token}
            # Defence in depth: ensure the fixture slug carries the
            # token even when an older client builds a slug from
            # project.name alone. The shooter lookup slug stays
            # untouched -- it's the URL identity, not the fixture
            # filename.
            fixture_slug = slug if token in slug else f"{slug}-{token}"

            # Visual/source provenance (#220 follow-up). The audit JSON
            # the SPA writes only carries shot/beep data; the calibrator
            # also wants source_video, the trim window, and the camera
            # block. Derive them from the project so the published
            # fixture is calibration-ready instead of needing a manual
            # backfill later (which is what landed s36ed6e4e + the
            # tallmilan iPhone secondaries with empty provenance).
            primary = stg.primary()
            if primary is None:
                raise HTTPException(
                    status_code=409,
                    detail=f"stage {stage_n} has no primary video; cannot promote",
                )
            if primary.beep_time is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"stage {stage_n} primary has no beep_time; "
                        "detect or set the beep before promoting"
                    ),
                )
            if not primary.camera_mount:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"stage {stage_n} primary has no camera_mount; "
                        "set it on the Ingest screen so the fixture lands "
                        "in the right per-camera-class bucket."
                    ),
                )
            stage_time_seconds = getattr(stg, "time_seconds", None)
            if stage_time_seconds is None:
                raise HTTPException(
                    status_code=409,
                    detail=(f"stage {stage_n} has no time_seconds; cannot " "compute the trim window."),
                )
            source_video_path = project.resolve_video_path(state.shooter_root(slug), primary.path)
            trim_start = max(0.0, float(primary.beep_time) - float(project.trim_pre_buffer_seconds))
            trim_end = (
                float(primary.beep_time) + float(stage_time_seconds) + float(project.trim_post_buffer_seconds)
            )
            # Camera make/model (#303): use the values cached on the
            # StageVideo at register time -- those came from ffprobe and
            # the user may have overridden them via the videos PATCH
            # endpoint. Both are ``None`` when ffprobe couldn't read the
            # QuickTime tag (Meta Vanguard glasses are the present
            # example -- no tag is exposed). The per-model amplitude
            # floor lookup (#304) handles the ``None`` case by falling
            # back to the generic-headcam floor.
            camera_payload: dict[str, Any] = {
                "id": "unknown",
                "make": primary.camera_make,
                "model": primary.camera_model,
                "mount": str(primary.camera_mount),
                "position": "shooter",
                "audio_source": "internal",
                "agc_state": "unknown",
                "sample_rate": None,
                "bit_depth": None,
                "audio_codec": None,
            }

            try:
                rec = lab_module.promote_stage_to_fixture(
                    lab_module.PromoteRequest(
                        audit_json_path=audit_json,
                        audit_wav_path=audit_wav,
                        fixture_slug=fixture_slug,
                        overwrite=overwrite,
                        extra_metadata={
                            "stage_number": stage_n,
                            "stage_name": getattr(stg, "name", None),
                        },
                        shooter=shooter_payload,
                        source_video=source_video_path,
                        fixture_window_in_source=(trim_start, trim_end),
                        camera=camera_payload,
                    )
                )
            except FileExistsError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return JSONResponse(rec.model_dump(mode="json"))

        @app.post("/api/lab/labels")
        def lab_labels(payload: dict[str, Any] = Body(...)) -> JSONResponse:  # noqa: B008
            """Apply categorical labels to a fixture's audit JSON (issue #86).

            Body shape::

                {
                  "audit_path": "...stage-shots-foo-2026-stage4.json",
                  "labels": [
                    {"candidate_number": 7,  "time": 2.812, "reason": "cross_bay"},
                    {"candidate_number": 14, "time": 4.364, "reason": null},
                    {"candidate_number": 22, "time": 5.220, "subclass": "steel"}
                  ]
                }

            ``time`` is the storage key (1 ms resolution) and is required:
            ``candidate_number`` shifts across detector reshuffles, time
            does not. Patches in place with a ``.bak`` backup; returns counts
            changed plus a freshly relabeled ``run`` so the SPA can update
            without firing a full ``/api/lab/eval``. ``run`` is ``None`` when
            no cached run exists yet (the SPA falls back to a real eval in
            that case).
            """
            audit_path = payload.get("audit_path")
            labels_payload = payload.get("labels")
            if not isinstance(audit_path, str) or not audit_path:
                raise HTTPException(
                    status_code=400,
                    detail="payload must include 'audit_path' (string)",
                )
            if not isinstance(labels_payload, list):
                raise HTTPException(
                    status_code=400,
                    detail="payload must include 'labels' (list)",
                )
            try:
                target = Path(audit_path).expanduser().resolve(strict=True)
            except (FileNotFoundError, OSError) as exc:
                raise HTTPException(
                    status_code=404,
                    detail=f"fixture not found: {audit_path}",
                ) from exc
            if not target.is_file():
                raise HTTPException(status_code=400, detail=f"not a file: {target}")
            try:
                labels = [lab_module.CandidateLabel.model_validate(item) for item in labels_payload]
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"invalid labels: {exc}") from exc
            try:
                counts = lab_module.apply_labels(target, labels)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            # Relabel the cached run in place (no model calls). Sub-100 ms,
            # so the SPA can ditch its post-label runEval and use this.
            cached_run = _lab_universe_cache.get("last_run")
            run_payload: Any = None
            if cached_run is not None:
                relabeled = lab_module.relabel_run(cached_run)
                _lab_universe_cache["last_run"] = relabeled
                _lab_universe_cache["universe"] = relabeled.universe
                run_payload = relabeled.model_dump(mode="json")
            return JSONResponse(
                {"path": str(target), "counts": counts, "run": run_payload},
            )

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
        async def lab_rebuild_calibration(
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

            state.jobs.bodies.register("rebuild_calibration", _run)
            job = await state.jobs.submit(kind="rebuild_calibration")
            return JSONResponse(job.model_dump(mode="json"))

        # ------------------------------------------------------------------
        # Promote-from-anchor (issue #125)
        # ------------------------------------------------------------------

        class PromoteFromAnchorBody(BaseModel):
            anchor_path: str
            secondary_wav_path: str
            slug: str
            camera_id: str
            mount: str
            position: str
            audio_source: str = "internal"
            agc_state: str = "unknown"
            snap_window_ms: float = 60.0
            min_spacing_ms: float = 80.0
            overwrite: bool = False

        @app.post("/api/lab/promote-from-anchor")
        async def lab_promote_from_anchor(body: PromoteFromAnchorBody) -> JSONResponse:
            """Submit a promote-from-anchor job (issue #125).

            Runs cross-align + ensemble detection + snap on the secondary
            audio, writes the pre-filled fixture JSON / WAV / promotion
            report to the fixtures directory, and returns the job ID.
            """
            anchor_path = Path(body.anchor_path).resolve()
            secondary_wav_path = Path(body.secondary_wav_path).resolve()

            if not anchor_path.exists():
                raise HTTPException(status_code=404, detail=f"anchor not found: {anchor_path}")
            if not secondary_wav_path.exists():
                raise HTTPException(
                    status_code=404,
                    detail=f"secondary WAV not found: {secondary_wav_path}",
                )

            anchor_wav_path = anchor_path.with_suffix(".wav")
            if not anchor_wav_path.exists():
                raise HTTPException(
                    status_code=404,
                    detail=f"anchor WAV not found: {anchor_wav_path}",
                )

            fixtures_root = lab_module.core.DEFAULT_FIXTURES_ROOT
            target_json = fixtures_root / f"{body.slug}.json"
            if target_json.exists() and not body.overwrite:
                raise HTTPException(
                    status_code=409,
                    detail=f"fixture already exists: {target_json} (set overwrite=true to replace)",
                )

            try:
                camera = Camera(
                    id=body.camera_id,
                    mount=CameraMount(body.mount),
                    position=CameraPosition(body.position),
                    audio_source=AudioSource(body.audio_source),
                    agc_state=AgcState(body.agc_state),
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            def _run(handle: JobHandle) -> None:
                import shutil as _shutil

                handle.update(progress=0.05, message="loading audio...")
                primary_audio, primary_sr = beep_detect.load_audio(anchor_wav_path)
                secondary_audio, secondary_sr = beep_detect.load_audio(secondary_wav_path)

                handle.update(progress=0.10, message="loading ensemble models...")
                runtime = _get_ensemble_runtime()

                handle.check_cancel()
                handle.update(progress=0.20, message="aligning + detecting shots...")

                anchor_data = __import__("json").loads(anchor_path.read_text(encoding="utf-8"))
                result = lab_module.promote_from_anchor(
                    lab_module.PromoteFromAnchorRequest(
                        anchor_data=anchor_data,
                        primary_audio=primary_audio,
                        primary_sr=primary_sr,
                        secondary_audio=secondary_audio,
                        secondary_sr=secondary_sr,
                        secondary_source_desc=str(secondary_wav_path),
                        camera=camera,
                        slug=body.slug,
                        snap_window_ms=body.snap_window_ms,
                        min_spacing_ms=body.min_spacing_ms,
                    ),
                    runtime=runtime,
                )
                for w in result.warnings:
                    handle.update(message=f"WARNING: {w}")

                handle.check_cancel()
                handle.update(progress=0.85, message="writing fixture...")

                fixtures_root.mkdir(parents=True, exist_ok=True)
                tmp = target_json.with_suffix(".json.tmp")
                tmp.write_text(
                    __import__("json").dumps(result.fixture_data, indent=2, ensure_ascii=True) + "\n",
                    encoding="utf-8",
                )
                tmp.replace(target_json)

                target_wav = fixtures_root / f"{body.slug}.wav"
                _shutil.copy2(secondary_wav_path, target_wav)

                report_path = fixtures_root / f"{body.slug}-promotion-report.json"
                report_path.write_text(
                    __import__("json").dumps(result.promotion_report, indent=2, ensure_ascii=True) + "\n",
                    encoding="utf-8",
                )

                handle.update(
                    progress=1.0,
                    message=(
                        f"done -- {result.promotion_report['counts']['snapped']}/"
                        f"{result.promotion_report['counts']['anchor_shots']} snapped"
                    ),
                )

            state.jobs.bodies.register("promote_from_anchor", _run)
            job = await state.jobs.submit(kind="promote_from_anchor")
            # Return the resolved fixture + anchor paths alongside the job so the
            # SPA can navigate to the review page once the job succeeds without
            # needing a separate result-fetch endpoint.
            return JSONResponse(
                {
                    "job": job.model_dump(mode="json"),
                    "fixture_path": str(target_json),
                    "anchor_path": str(anchor_path),
                    "slug": body.slug,
                }
            )

        @app.get("/api/lab/promote-report")
        def lab_promote_report(slug: str) -> JSONResponse:
            """Return the promotion report for a completed promote-from-anchor run."""
            fixtures_root = lab_module.core.DEFAULT_FIXTURES_ROOT
            report_path = fixtures_root / f"{slug}-promotion-report.json"
            if not report_path.exists():
                raise HTTPException(
                    status_code=404,
                    detail=f"promotion report not found for slug '{slug}'",
                )
            try:
                data = __import__("json").loads(report_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"failed to read report: {exc}") from exc
            return JSONResponse(data)

        @app.delete("/api/lab/fixture")
        def lab_delete_fixture(slug: str) -> JSONResponse:
            """Delete a derived fixture (JSON + WAV + sibling artifacts).

            Refuses to delete primary fixtures (those without an
            ``anchor`` block) so a user can't accidentally nuke a
            ground-truth headcam fixture by clicking the wrong button.
            Use the file system directly if you really want to remove
            a primary -- forcing that explicitness is intentional.
            """
            fixtures_root = lab_module.core.DEFAULT_FIXTURES_ROOT
            json_path = fixtures_root / f"{slug}.json"
            if not json_path.exists():
                raise HTTPException(status_code=404, detail=f"no fixture: {slug}")
            try:
                payload = __import__("json").loads(json_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"failed to read fixture: {exc}") from exc
            anchor_block = payload.get("anchor")
            if not isinstance(anchor_block, dict) or not anchor_block.get("fixture_slug"):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"refusing to delete '{slug}': not a derived fixture "
                        "(no anchor block). Remove primary fixtures from disk directly."
                    ),
                )
            # Sibling artifacts to clean up alongside the fixture JSON.
            removed: list[str] = []
            for sibling in [
                json_path,
                json_path.with_suffix(".wav"),
                fixtures_root / f"{slug}-promotion-report.json",
                json_path.with_suffix(".json.bak"),
            ]:
                if sibling.exists():
                    try:
                        sibling.unlink()
                        removed.append(sibling.name)
                    except OSError as exc:
                        raise HTTPException(
                            status_code=500, detail=f"failed to remove {sibling.name}: {exc}"
                        ) from exc
            # Also remove peaks-* JSON cache files for this fixture.
            for cache in fixtures_root.glob(f"{slug}.peaks-*.json"):
                try:
                    cache.unlink()
                    removed.append(cache.name)
                except OSError:
                    pass
            return JSONResponse({"removed": removed})

        # ------------------------------------------------------------------
        # Project-aware secondary promotion (issue #125 follow-up)
        # ------------------------------------------------------------------

        @app.post("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/promote-secondary")
        async def promote_secondary(
            slug: str,
            stage_number: int,
            video_id: str,
            body: PromoteSecondaryBody = Body(...),  # noqa: B008
        ) -> JSONResponse:
            """Promote a project-mapped secondary video to a derived fixture.

            Resolves the anchor (primary) fixture path, the cached secondary
            WAV, and probes the source video for camera metadata so the
            caller only has to supply ``mount`` + ``position``. Anchor
            fixture must already exist (run the primary promote first).
            """
            project, stage, video = _resolve_stage_video(slug, stage_number, video_id)
            if video.role != "secondary":
                raise HTTPException(
                    status_code=400,
                    detail=f"video {video_id!r} is not a secondary on stage {stage_number}",
                )
            if video.beep_time is None:
                raise HTTPException(
                    status_code=409,
                    detail="secondary has no beep_time; detect or align beep first",
                )

            # Anchor lookup needs the shooter token suffix because the
            # primary fixture was promoted with it. Without a pinned
            # shooter we can't construct the slug -- bail with the same
            # message used by lab_promote.
            if project.selected_shooter_id is None:
                raise HTTPException(
                    status_code=400,
                    detail=("this project has no SSI shooter pinned; " "cannot resolve anchor fixture slug."),
                )
            anchor_token = lab_module.shooter_token(project.selected_shooter_id)
            primary_slug = (
                f"stage-shots-{export_helpers._slugify(project.name)}-stage{stage_number}" f"-{anchor_token}"
            )
            fixtures_root = lab_module.core.DEFAULT_FIXTURES_ROOT
            anchor_path = fixtures_root / f"{primary_slug}.json"
            if not anchor_path.exists():
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"anchor fixture not found: {anchor_path.name}. "
                        "Promote the primary stage to a fixture first."
                    ),
                )
            anchor_wav_path = anchor_path.with_suffix(".wav")
            if not anchor_wav_path.exists():
                raise HTTPException(
                    status_code=409,
                    detail=f"anchor WAV missing: {anchor_wav_path.name}",
                )

            source = project.resolve_video_path(state.shooter_root(slug), video.path)
            _ensure_source_reachable(stage_number, source)

            try:
                secondary_wav_path = audio_helpers.ensure_video_audio(
                    state.shooter_root(slug),
                    stage_number,
                    video,
                    source,
                    project=project,
                    ffmpeg_binary=process_runtime().ffmpeg_binary,
                )
            except (FileNotFoundError, audio_helpers.AudioExtractionError) as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            probe = probe_camera_metadata(source)
            camera_id = (body.camera_id or probe.suggested_id or f"cam-{video.video_id[:8]}").strip()
            if not camera_id:
                raise HTTPException(
                    status_code=400,
                    detail="camera_id could not be derived; please supply one",
                )

            # ``slug`` (URL path param) names the shooter; ``fixture_slug``
            # below names the derived fixture being written. Same string
            # convention but different identities -- don't rebind ``slug``.
            fixture_slug = (body.slug or f"{primary_slug}-{camera_id}").strip()
            target_json = fixtures_root / f"{fixture_slug}.json"
            if target_json.exists() and not body.overwrite:
                raise HTTPException(
                    status_code=409,
                    detail=(f"fixture already exists: {target_json.name}" " (set overwrite=true to replace)"),
                )

            try:
                camera = Camera(
                    id=camera_id,
                    mount=CameraMount(body.mount),
                    position=CameraPosition(body.position),
                    audio_source=AudioSource(body.audio_source),
                    agc_state=AgcState(body.agc_state),
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            # Strip home-dir prefix so the published fixture carries no
            # OS-username PII; ``scrub_local_path`` drops the
            # ``/Users/<name>/matches/<match>/`` head and keeps the
            # meaningful tail (``raw/<file>``).
            secondary_source_desc = lab_module.scrub_local_path(str(source)) or source.name
            # Use the audited in-stream beep time from the project so the
            # promote engine can skip cross-correlation entirely. The
            # ingest screen owns this value (auto + manual + review); we
            # treat it as ground truth on the project flow.
            known_secondary_beep = float(video.beep_time)

            def _run(handle: JobHandle) -> None:
                handle.update(progress=0.05, message="loading audio...")
                primary_audio, primary_sr = beep_detect.load_audio(anchor_wav_path)
                secondary_audio, secondary_sr = beep_detect.load_audio(secondary_wav_path)

                handle.update(progress=0.10, message="loading ensemble models...")
                runtime = _get_ensemble_runtime()

                handle.check_cancel()
                handle.update(progress=0.20, message="detecting shots...")

                anchor_data = __import__("json").loads(anchor_path.read_text(encoding="utf-8"))
                result = lab_module.promote_from_anchor(
                    lab_module.PromoteFromAnchorRequest(
                        anchor_data=anchor_data,
                        primary_audio=primary_audio,
                        primary_sr=primary_sr,
                        secondary_audio=secondary_audio,
                        secondary_sr=secondary_sr,
                        secondary_source_desc=secondary_source_desc,
                        camera=camera,
                        slug=fixture_slug,
                        snap_window_ms=body.snap_window_ms,
                        min_spacing_ms=body.min_spacing_ms,
                        secondary_beep_time=known_secondary_beep,
                    ),
                    runtime=runtime,
                )
                for w in result.warnings:
                    handle.update(message=f"WARNING: {w}")

                handle.check_cancel()
                handle.update(progress=0.85, message="writing fixture...")

                # Trim secondary WAV to a clip-local window around the beep,
                # mirroring the convention used by primary fixtures (the
                # /api/fixture/peaks endpoint marks fixture audio as
                # ``trimmed=true``). Without this the audit screen renders
                # the entire raw recording with shots clustered far down
                # the timeline.
                fixture_data = dict(result.fixture_data)
                secondary_beep = float(fixture_data.get("beep_time") or 0.0)
                shot_times = [
                    float(s["time"]) for s in fixture_data.get("shots", []) if s.get("time") is not None
                ]
                trim_buffer = 5.0
                trim_tail = 5.0
                clip_start = max(0.0, secondary_beep - trim_buffer)
                clip_end_floor = secondary_beep + trim_buffer
                clip_end = max(shot_times) + trim_tail if shot_times else clip_end_floor
                clip_end = max(clip_end, clip_end_floor)

                fixtures_root.mkdir(parents=True, exist_ok=True)
                target_wav = fixtures_root / f"{fixture_slug}.wav"
                handle.update(progress=0.88, message="trimming clip audio...")
                _trim_wav_to_clip(secondary_wav_path, target_wav, clip_start, clip_end)

                # Rebase time-axis fields to clip-local coordinates.
                fixture_data["beep_time"] = round(secondary_beep - clip_start, 4)
                fixture_data["fixture_window_in_source"] = [
                    round(clip_start, 4),
                    round(clip_end, 4),
                ]
                rebased_shots = []
                for s in fixture_data.get("shots", []):
                    s = dict(s)
                    if s.get("time") is not None:
                        s["time"] = round(float(s["time"]) - clip_start, 4)
                    rebased_shots.append(s)
                fixture_data["shots"] = rebased_shots
                cands = (fixture_data.get("_candidates_pending_audit") or {}).get("candidates")
                if cands:
                    rebased_cands = []
                    for c in cands:
                        c = dict(c)
                        if c.get("time") is not None:
                            c["time"] = round(float(c["time"]) - clip_start, 4)
                        rebased_cands.append(c)
                    fixture_data["_candidates_pending_audit"] = {
                        **fixture_data["_candidates_pending_audit"],
                        "candidates": rebased_cands,
                    }

                tmp = target_json.with_suffix(".json.tmp")
                tmp.write_text(
                    __import__("json").dumps(fixture_data, indent=2, ensure_ascii=True) + "\n",
                    encoding="utf-8",
                )
                tmp.replace(target_json)

                report_path = fixtures_root / f"{fixture_slug}-promotion-report.json"
                report_path.write_text(
                    __import__("json").dumps(result.promotion_report, indent=2, ensure_ascii=True) + "\n",
                    encoding="utf-8",
                )

                handle.update(
                    progress=1.0,
                    message=(
                        f"done -- {result.promotion_report['counts']['snapped']}/"
                        f"{result.promotion_report['counts']['anchor_shots']} snapped"
                    ),
                )

            state.jobs.bodies.register("promote_from_anchor", _run)
            job = await state.jobs.submit(kind="promote_from_anchor")
            return JSONResponse(
                {
                    "job": job.model_dump(mode="json"),
                    "fixture_path": str(target_json),
                    "anchor_path": str(anchor_path),
                    "slug": fixture_slug,
                    "camera_id": camera_id,
                    "anchor_slug": primary_slug,
                }
            )

        # ------------------------------------------------------------------
        # Promote a project video against an arbitrary fixture anchor
        # (issue #149 follow-up). Used when the headcam ground truth
        # already lives as a fixture in ``tests/fixtures/`` and the user
        # has a phone-cam project they want to anchor against it -- no
        # in-project primary required, any video on the stage works.
        # ------------------------------------------------------------------

        @app.post("/api/lab/projects/{slug}/{stage_number}/videos/{video_id}/promote-against-fixture")
        async def lab_promote_against_fixture(
            slug: str,
            stage_number: int,
            video_id: str,
            body: PromoteAgainstFixtureBody = Body(...),  # noqa: B008
        ) -> JSONResponse:
            """Anchor a project video against an existing fixture.

            Skips the role=='secondary' gate that the in-project
            ``promote-secondary`` endpoint enforces -- the typical use
            case here is a phone-cam project where the iPhone is the
            primary and there's no in-project anchor video. Anchor
            comes from ``body.anchor_slug`` (any fixture in
            ``tests/fixtures/``).
            """
            project, _stage, video = _resolve_stage_video(slug, stage_number, video_id)
            if video.beep_time is None:
                raise HTTPException(
                    status_code=409,
                    detail="video has no beep_time; detect or align beep first",
                )

            fixtures_root = lab_module.core.DEFAULT_FIXTURES_ROOT
            anchor_slug = body.anchor_slug.strip()
            if not anchor_slug:
                raise HTTPException(status_code=400, detail="anchor_slug is required")
            anchor_path = fixtures_root / f"{anchor_slug}.json"
            if not anchor_path.exists():
                raise HTTPException(
                    status_code=404,
                    detail=f"anchor fixture not found: {anchor_path.name}",
                )
            anchor_wav_path = anchor_path.with_suffix(".wav")
            if not anchor_wav_path.exists():
                raise HTTPException(
                    status_code=409,
                    detail=f"anchor WAV missing: {anchor_wav_path.name}",
                )

            source = project.resolve_video_path(state.shooter_root(slug), video.path)
            _ensure_source_reachable(stage_number, source)

            try:
                secondary_wav_path = audio_helpers.ensure_video_audio(
                    state.shooter_root(slug),
                    stage_number,
                    video,
                    source,
                    project=project,
                    ffmpeg_binary=process_runtime().ffmpeg_binary,
                )
            except (FileNotFoundError, audio_helpers.AudioExtractionError) as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            probe = probe_camera_metadata(source)
            camera_id = (body.camera_id or probe.suggested_id or f"cam-{video.video_id[:8]}").strip()
            if not camera_id:
                raise HTTPException(
                    status_code=400,
                    detail="camera_id could not be derived; please supply one",
                )

            slug = (body.slug or f"{anchor_slug}-{camera_id}").strip()
            target_json = fixtures_root / f"{slug}.json"
            if target_json.exists() and not body.overwrite:
                raise HTTPException(
                    status_code=409,
                    detail=(f"fixture already exists: {target_json.name}" " (set overwrite=true to replace)"),
                )

            try:
                camera = Camera(
                    id=camera_id,
                    mount=CameraMount(body.mount),
                    position=CameraPosition(body.position),
                    audio_source=AudioSource(body.audio_source),
                    agc_state=AgcState(body.agc_state),
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            # Strip home-dir prefix so the published fixture carries no
            # OS-username PII; ``scrub_local_path`` drops the
            # ``/Users/<name>/matches/<match>/`` head and keeps the
            # meaningful tail (``raw/<file>``).
            secondary_source_desc = lab_module.scrub_local_path(str(source)) or source.name
            known_secondary_beep = float(video.beep_time)

            def _run(handle: JobHandle) -> None:
                handle.update(progress=0.05, message="loading audio...")
                primary_audio, primary_sr = beep_detect.load_audio(anchor_wav_path)
                secondary_audio, secondary_sr = beep_detect.load_audio(secondary_wav_path)

                handle.update(progress=0.10, message="loading ensemble models...")
                runtime = _get_ensemble_runtime()

                handle.check_cancel()
                handle.update(progress=0.20, message="detecting shots...")

                anchor_data = json.loads(anchor_path.read_text(encoding="utf-8"))
                result = lab_module.promote_from_anchor(
                    lab_module.PromoteFromAnchorRequest(
                        anchor_data=anchor_data,
                        primary_audio=primary_audio,
                        primary_sr=primary_sr,
                        secondary_audio=secondary_audio,
                        secondary_sr=secondary_sr,
                        secondary_source_desc=secondary_source_desc,
                        camera=camera,
                        slug=slug,
                        snap_window_ms=body.snap_window_ms,
                        min_spacing_ms=body.min_spacing_ms,
                        secondary_beep_time=known_secondary_beep,
                    ),
                    runtime=runtime,
                )
                for w in result.warnings:
                    handle.update(message=f"WARNING: {w}")

                handle.check_cancel()
                handle.update(progress=0.85, message="writing fixture...")

                fixture_data = dict(result.fixture_data)
                secondary_beep = float(fixture_data.get("beep_time") or 0.0)
                shot_times = [
                    float(s["time"]) for s in fixture_data.get("shots", []) if s.get("time") is not None
                ]
                trim_buffer = 5.0
                trim_tail = 5.0
                clip_start = max(0.0, secondary_beep - trim_buffer)
                clip_end_floor = secondary_beep + trim_buffer
                clip_end = max(shot_times) + trim_tail if shot_times else clip_end_floor
                clip_end = max(clip_end, clip_end_floor)

                fixtures_root.mkdir(parents=True, exist_ok=True)
                target_wav = fixtures_root / f"{slug}.wav"
                handle.update(progress=0.88, message="trimming clip audio...")
                _trim_wav_to_clip(secondary_wav_path, target_wav, clip_start, clip_end)

                fixture_data["beep_time"] = round(secondary_beep - clip_start, 4)
                fixture_data["fixture_window_in_source"] = [
                    round(clip_start, 4),
                    round(clip_end, 4),
                ]
                rebased_shots = []
                for s in fixture_data.get("shots", []):
                    s = dict(s)
                    if s.get("time") is not None:
                        s["time"] = round(float(s["time"]) - clip_start, 4)
                    rebased_shots.append(s)
                fixture_data["shots"] = rebased_shots
                cands = (fixture_data.get("_candidates_pending_audit") or {}).get("candidates")
                if cands:
                    rebased_cands = []
                    for c in cands:
                        c = dict(c)
                        if c.get("time") is not None:
                            c["time"] = round(float(c["time"]) - clip_start, 4)
                        rebased_cands.append(c)
                    fixture_data["_candidates_pending_audit"] = {
                        **fixture_data["_candidates_pending_audit"],
                        "candidates": rebased_cands,
                    }

                tmp = target_json.with_suffix(".json.tmp")
                tmp.write_text(
                    json.dumps(fixture_data, indent=2, ensure_ascii=True) + "\n",
                    encoding="utf-8",
                )
                tmp.replace(target_json)

                report_path = fixtures_root / f"{slug}-promotion-report.json"
                report_path.write_text(
                    json.dumps(result.promotion_report, indent=2, ensure_ascii=True) + "\n",
                    encoding="utf-8",
                )

                handle.update(
                    progress=1.0,
                    message=(
                        f"done -- {result.promotion_report['counts']['snapped']}/"
                        f"{result.promotion_report['counts']['anchor_shots']} snapped"
                    ),
                )

            state.jobs.bodies.register("promote_from_anchor", _run)
            job = await state.jobs.submit(kind="promote_from_anchor")
            return JSONResponse(
                {
                    "job": job.model_dump(mode="json"),
                    "fixture_path": str(target_json),
                    "anchor_path": str(anchor_path),
                    "slug": slug,
                    "camera_id": camera_id,
                    "anchor_slug": anchor_slug,
                }
            )

        # ------------------------------------------------------------------
        # Lab: ensemble parameter sweeps (read-only dashboard)
        # ------------------------------------------------------------------
        #
        # Backed by ``build/sweeps/runs.parquet`` + ``build/sweeps/<run_id>/``
        # written by ``scripts/run_sweep.py`` + ``scripts/plot_sweep.py``.
        # No launch endpoint yet -- sweeps stay on the CLI for now.

        from .. import lab as _lab_module_for_sweeps  # noqa: F401  (silences mypy)
        from ..lab import sweeps as _sweeps_module

        @app.get("/api/lab/sweeps")
        def lab_sweeps_list() -> JSONResponse:
            """List one row per ``run_id`` with best-F1 highlights."""
            return JSONResponse([s.model_dump(mode="json") for s in _sweeps_module.list_runs()])

        @app.get("/api/lab/sweeps/{run_id}")
        def lab_sweeps_detail(run_id: str) -> JSONResponse:
            """Return the full payload for one run: every combo + per-fixture rows."""
            detail = _sweeps_module.get_run(run_id)
            if detail is None:
                raise HTTPException(status_code=404, detail=f"no sweep run {run_id!r}")
            return JSONResponse(detail.model_dump(mode="json"))

        @app.get("/api/lab/sweeps/{run_id}/plot/{plot_name}.png")
        def lab_sweeps_plot(run_id: str, plot_name: str) -> FileResponse:
            """Serve one PNG from ``build/sweeps/<run_id>/``.

            The plot_name is restricted to alnum + underscore by the
            ``sweeps`` helper so a malicious caller can't traverse
            outside the run dir. Returns 404 when the plot doesn't
            exist (e.g. a 2D plot requested for a 1D sweep).
            """
            path = _sweeps_module.plot_path(run_id, plot_name)
            if path is None:
                raise HTTPException(status_code=404, detail="plot not found")
            return FileResponse(path, media_type="image/png", filename=path.name)

    if lab_enabled:
        _setup_lab()

    # ----------------------------------------------------------------------
    # Developer mode -- read-only metadata for the dev-shell model chip and
    # the review-queue rollup. Always available; lighter than the heavy
    # /api/lab/* runtime endpoints so the dev SPA shell can mount without
    # forcing ``--lab`` on the launcher.
    # ----------------------------------------------------------------------
    from .. import lab as _lab_for_dev  # local import to avoid module top
    from ..ensemble import load_calibration as _load_dev_calibration

    def _venue_from_slug(slug: str) -> str | None:
        # Slugs look like ``stage-shots-<venue>-2026-stage<n>-s<hash>...``.
        # The venue is the chunk between ``stage-shots-`` and the year.
        parts = slug.split("-")
        if len(parts) >= 4 and parts[0] == "stage" and parts[1] == "shots":
            venue_bits: list[str] = []
            for tok in parts[2:]:
                if tok.isdigit() and len(tok) == 4:
                    break
                venue_bits.append(tok)
            return "-".join(venue_bits) if venue_bits else None
        return None

    def _stage_from_slug(slug: str) -> int | None:
        for tok in slug.split("-"):
            if tok.startswith("stage") and tok[5:].isdigit():
                return int(tok[5:])
        return None

    def _shooter_from_slug(slug: str) -> str | None:
        # Shooter is encoded as ``s<8-hex-chars>`` (the slug hash).
        for tok in slug.split("-"):
            if len(tok) == 9 and tok.startswith("s") and all(c in "0123456789abcdef" for c in tok[1:]):
                return tok
        return None

    @app.get("/api/dev/model", response_model=DeveloperModelInfo)
    def dev_model_info() -> DeveloperModelInfo:
        """Return active ensemble metadata + workflow step counters.

        Drives the dev shell's model chip and the workflow stepper
        badges. ``recall`` is the GBDT target-recall picked at
        calibration time; precision/f1 are not stored on the artifact
        so they come back as ``None`` until a /dev/validate run writes
        them. The version string is derived from ``built_at`` so users
        can match it back to a calibration build.
        """
        cal = _load_dev_calibration()
        fixtures = _lab_for_dev.list_fixtures()
        # Version = vYYYY.MM.DD from built_at. Stable enough for the
        # chip; the calibration JSON also carries the full ISO.
        try:
            built_dt = datetime.fromisoformat(cal.built_at.replace("Z", "+00:00"))
            version = built_dt.strftime("v%Y.%m.%d")
        except (ValueError, AttributeError):
            version = "v0.0.0"
        # "review" bucket counts fixtures that came in via promote-from-
        # anchor (anchor_slug set) -- those need human confirmation
        # before they're allowed into the calibration set.
        review_count = sum(1 for f in fixtures if f.anchor_slug is not None)
        return DeveloperModelInfo(
            active_version=version,
            recall=cal.voter_c_target_recall,
            precision=None,
            f1=None,
            fixture_count=len(cal.calibration_fixtures),
            built_at=cal.built_at,
            step_counts=DeveloperStepCounts(
                corpus=len(fixtures),
                review=review_count,
                validate_runs=0,
                retrain=0,
            ),
        )

    @app.get("/api/dev/review-queue", response_model=DevReviewQueueResponse)
    def dev_review_queue() -> DevReviewQueueResponse:
        """Bucket fixtures into pending / flagged / done for the review queue.

        v1 heuristic: anchor_slug present = pending (came in via
        promote-from-anchor and needs human confirmation); explicit
        ``review_flagged`` field on the fixture JSON = flagged; all
        else = done. The flagged bucket is rarely populated today but
        the shape is here so the UI doesn't need a follow-up wire.
        """
        fixtures = _lab_for_dev.list_fixtures()
        now = datetime.now(UTC).timestamp()
        pending: list[DevReviewQueueItem] = []
        flagged: list[DevReviewQueueItem] = []
        done: list[DevReviewQueueItem] = []
        for fx in fixtures:
            age = int(now - fx.audit_mtime) if fx.audit_mtime else None
            item = DevReviewQueueItem(
                slug=fx.slug,
                audit_path=fx.audit_path,
                source="match" if fx.anchor_slug else "ad-hoc",
                source_label="Promoted from match" if fx.anchor_slug else "Audited",
                status="pending" if fx.anchor_slug else "done",
                n_shots=fx.n_shots,
                n_disagreements=0,
                promoted_at=None,
                venue=_venue_from_slug(fx.slug),
                stage_number=_stage_from_slug(fx.slug),
                shooter=_shooter_from_slug(fx.slug),
                age_seconds=age,
            )
            if item.status == "pending":
                pending.append(item)
            else:
                done.append(item)
        # Sort pending by age (newest first); done alphabetically so the
        # corpus is browsable.
        pending.sort(key=lambda x: x.age_seconds or 0)
        done.sort(key=lambda x: x.slug)
        return DevReviewQueueResponse(pending=pending, flagged=flagged, done=done)

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
        #
        # index.html must NOT be browser-cached: a fresh build emits a new
        # content-hashed bundle filename (e.g. index-DBM1MaSD.js), and only
        # the freshly-served index.html knows about it. A cached index.html
        # points at the old bundle, so a plain refresh after a rebuild
        # would keep serving stale React code until the user hard-reloads.
        # The /assets/* mount stays freely cacheable because those URLs
        # are content-addressed -- a new build emits new filenames.
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
            return FileResponse(index, headers={"Cache-Control": "no-cache"})

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


# GetDriveTypeW return values (winbase.h). REMOVABLE/FIXED/CDROM/RAMDISK
# all collapse to ``"removable"`` for picker purposes; only REMOTE gets
# its own bucket and only UNKNOWN / NO_ROOT_DIR are filtered out.
_DRIVE_UNKNOWN = 0
_DRIVE_NO_ROOT_DIR = 1
_DRIVE_REMOTE = 4


def _discover_windows_drives() -> list[tuple[Path, str, str]]:
    """Enumerate Windows drives via Win32 + classify type & label.

    Replaces the old D-Z directory-existence scan with kernel32 calls so
    we can: (a) skip drives that aren't actually present, (b) classify
    mapped network drives (``DRIVE_REMOTE``) as ``"network"`` instead of
    lumping everything under ``"removable"``, and (c) read the volume
    label so the picker shows ``"INSTA360 (D:)"`` rather than just
    ``"D:"``.

    UNC shares without a drive letter (``\\\\nas\\share``) aren't
    enumerated -- the typical Windows workflow maps network drives to a
    letter via "Map Network Drive", which this picks up.
    """
    drives = _query_windows_drives()
    out: list[tuple[Path, str, str]] = []
    for letter, (drive_type, label) in sorted(drives.items()):
        if letter == "C":  # skip system drive, matching prior behaviour
            continue
        if drive_type in (_DRIVE_UNKNOWN, _DRIVE_NO_ROOT_DIR):
            continue
        kind = "network" if drive_type == _DRIVE_REMOTE else "removable"
        display = f"{label} ({letter}:)" if label else f"{letter}:"
        out.append((Path(f"{letter}:\\"), display, kind))
    return out


def _query_windows_drives() -> dict[str, tuple[int, str | None]]:
    """Return ``{letter: (drive_type, volume_label)}`` for present drives.

    Empty on non-Windows or any kernel32 failure -- the caller falls back
    to "no removable drives discovered". Volume label is skipped for
    ``DRIVE_REMOTE`` because ``GetVolumeInformationW`` can hang on a
    stale share; the drive letter is still reported so the user can
    still navigate to it manually.
    """
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return {}

    try:
        # ``ctypes.windll`` is only defined on Windows; on Linux/macOS the
        # AttributeError below short-circuits to {}.
        kernel32: Any = ctypes.windll.kernel32  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return {}

    try:
        bitmask = int(kernel32.GetLogicalDrives())
    except OSError:
        return {}
    if not bitmask:
        return {}

    out: dict[str, tuple[int, str | None]] = {}
    for i in range(26):
        if not (bitmask >> i) & 1:
            continue
        letter = chr(ord("A") + i)
        root = f"{letter}:\\"
        try:
            drive_type = int(kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)))
        except OSError:
            continue
        label: str | None = None
        if drive_type != _DRIVE_REMOTE:
            label_buf = ctypes.create_unicode_buffer(261)  # MAX_PATH + 1
            fs_buf = ctypes.create_unicode_buffer(261)
            try:
                ok = kernel32.GetVolumeInformationW(
                    ctypes.c_wchar_p(root),
                    label_buf,
                    wintypes.DWORD(len(label_buf)),
                    None,
                    None,
                    None,
                    fs_buf,
                    wintypes.DWORD(len(fs_buf)),
                )
            except OSError:
                ok = 0
            if ok:
                label = label_buf.value or None
        out[letter] = (drive_type, label)
    return out


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


def serve(
    *,
    project_root: Path | None = None,
    project_name: str | None = None,
    host: str = "127.0.0.1",
    port: int = 5174,
    reload: bool = False,
    lab_enabled: bool = False,
    skip_system_check: bool = False,
) -> None:
    """Boot uvicorn synchronously. Used by the ``splitsmith ui`` CLI command.

    Wraps ``uvicorn.Server`` so the first Ctrl-C prints a summary of the
    background jobs that are still running (detect_beep, trim,
    shot_detect) -- otherwise uvicorn's graceful-shutdown wait looks
    like the process hanging. Uvicorn already promotes a second Ctrl-C
    to a force-exit; we just decorate the first press with the job
    inventory + a hint about pressing again.

    The first thing we do is check ffmpeg / ffprobe are invocable
    (issue #377 -- doc 04). The slim install ships them as documented
    system deps; a missing binary surfaces a copy-pasteable install
    hint here rather than a cryptic ``FileNotFoundError`` mid-trim.
    ``skip_system_check=True`` bypasses the probe (used by tests).
    """
    import uvicorn

    # Make the app's own INFO logs (incl. the console magic-link line)
    # visible -- uvicorn only configures its own loggers, leaving ours
    # muted at the root WARNING default. See _configure_app_logging.
    _configure_app_logging()

    if not skip_system_check:
        from ..system_check import check_ffmpeg

        outcome = check_ffmpeg()
        if not outcome.ok:
            sys.stderr.write(outcome.hint + "\n")
            sys.exit(2)

    _ensure_ui_built()

    if reload:
        # Reload mode requires an importable factory; pass the path string and
        # use environment variables to feed the project context. Simpler: just
        # log a warning and run without reload for now. Reload is a dev
        # convenience that we can wire properly when we have a real config.
        logger.warning("reload=True is not supported yet; running without reload")

    app = create_app(
        project_root=project_root,
        project_name=project_name,
        lab_enabled=lab_enabled,
    )
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
        asyncio.get_running_loop()
    except RuntimeError:
        loop_running = False
    else:
        loop_running = True
    if loop_running:
        # Hosted ``serve`` under uvicorn: SIGTERM (a redeploy) is handled
        # while uvicorn's event loop is still running, so ``asyncio.run``
        # would raise "cannot be called from a running event loop". This
        # job dump is a local Ctrl-C nicety, not a hosted concern -- skip
        # it rather than spam a traceback on every redeploy.
        return
    try:
        # Local ``splitsmith ui`` Ctrl-C: no loop is running, so drive the
        # async JobBackend.list() call with ``asyncio.run``.
        jobs = asyncio.run(state.jobs.list())
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
        f"Shutting down -- waiting for {len(active)} background job" f"{'' if len(active) == 1 else 's'}:",
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
        "Press Ctrl-C again to force quit (in-flight ffmpeg / detection will be killed).",
        file=sys.stderr,
        flush=True,
    )
