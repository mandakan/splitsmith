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
  POST /api/me/recent-projects/delete   -- delete a project/match + all its resources
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
import atexit
import contextlib
import functools
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from contextlib import AbstractContextManager, asynccontextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from math import isfinite
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    # Hosted-only; imported lazily at runtime inside _apply_hosted_mode_wiring
    # so local mode stays free of the db (procrastinate/psycopg) dependency.
    from ..db import (
        LoopEngines,
    )
    from ..db.share_tokens import ResolvedShare
    from ..db.workers import WorkersStore
    from ..worker_channel import WakeChannelRegistry

import httpx
from fastapi import (
    Body,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from .. import __version__ as splitsmith_version
from .. import account_profile as account_profile_module
from .. import automation as automation_settings
from .. import coach as coach_module
from .. import (
    cross_align,
    derived,
    export_runs,
    match_model,
    report,
    user_config,
    video_probe,
    youtube_sidecar,
)
from .. import ensemble as ensemble_module
from .. import events as events_module
from .. import looks as looks_module
from .. import media_check as media_check_module
from .. import models as model_layer
from .. import shooter_book as shooter_book_module
from .. import shot_detect as shot_detect_module  # noqa: F401  (kept for legacy monkeypatch points)
from .. import thumbnail as thumbnail_helpers
from .. import trim as trim_module
from .. import waveform as waveform_helpers
from ..access import Feature, FeatureRequiredError, access_config, features_for
from ..async_bridge import DbRunner, get_runner, install_runner, run_sync
from ..audit_clock import CLIP_VIDEO_FIELD
from ..audit_data import StageExportError, audit_shots_to_engine_shots
from ..audit_revision import REVISION_FIELD, AuditRevisionConflictError, audit_revision
from ..auth import CompositeAuth, User
from ..beep import cross as beep_cross
from ..beep import detect as beep_detect
from ..beep import tone as beep_tone_module
from ..beep import windows as beep_windows
from ..comment_identity import (
    MAX_AUTHOR_KEY_LEN,
    MIN_AUTHOR_KEY_LEN,
)
from ..compare import cards as compare_cards
from ..compare import mp4_grid, project_loader
from ..composition import BrandMark, uniform_transitions
from ..compute import LocalComputeBackend
from ..config import (
    BeepWindowConfig,
    CoachAutoClassifyConfig,
    Config,
    IntervalClass,
    IntervalClassSource,
    Shot,
    StageEvent,
    VideoMetadata,
)
from ..detection_merge import candidate_dict as _candidate_dict
from ..detection_merge import candidate_high_water as _candidate_high_water  # noqa: F401  (tests import it)
from ..detection_merge import merge_detection_into
from ..detection_merge import reset_deletion_events as _reset_deletion_events  # noqa: F401  (tests import it)
from ..division import competitor_division
from ..export_naming import slugify, stage_file_base
from ..fixture_schema import (
    AgcState,
    AudioSource,
    Camera,
    CameraMount,
    CameraPosition,
    probe_camera_metadata,
)
from ..identity import (
    LOGO_DIR,
    ResolvedIdentity,
    ShooterIdentity,
)
from ..looks import load_look
from ..match_project import (
    STUB_AUDIT_DETECTION,
    MatchProject,
    StageEntry,
    StageStatus,
    StageVideo,
    camera_labels,
    is_stub_audit,
    tone_flag_ms,
)
from ..match_registry import MatchRegistry
from ..mp4_render import RenderStep
from ..observability import StructuredJsonFormatter, init_sentry
from ..overlay.hud import overlay_data_identity, overlay_settings
from ..runtime import runtime as process_runtime
from ..share_card import stage_figures
from ..shot_id import has_usable_id
from ..storage import Storage, StorageObject
from ..sync.auto_state import AutoRunSummary, load_auto_prefs, update_auto_prefs
from ..sync.client import HostedSyncClient, SyncClientError
from ..sync.reconcile import (
    ReconcileStep,
    load_reconcile_inputs,
    plan_reconcile,
    present_sources_for,
    valid_trims_for,
    video_step,
)
from ..sync.run import format_sync_message
from ..sync.run import run_sync as run_bidirectional_sync  # ..async_bridge.run_sync already owns this name
from . import audio as audio_helpers
from . import beep_tone_check, export_storage
from . import exports as export_helpers
from . import match_exports as match_export_helpers
from .access_gate import features_of, require_feature
from .account_backfill import hosted_backfill_source
from .auto_sync import (
    MATCH_SYNC_LOCK_FILE,
    OWNER_LOCK_FILE,
    AutoSyncService,
    FileLock,
    auto_sync_disabled_by_env,
    write_marks_dirty,
)
from .capabilities import (
    MatchOrigin,
    capabilities_for_origin,
    required_capability,
    share_scope_capabilities,
)
from .comments import (
    CommentRateLimiter,
)
from .context import (
    SPLITSMITH_MODE_ENV,
    SPLITSMITH_PUBLIC_URL_ENV,
    TenantContext,
    _hosted_mode_active,
    current_match_capabilities,
    current_match_id,
    current_match_origin,
    current_match_root,
    current_share_cameras,
    current_share_request,
    current_share_viewer,
    current_tenant,
)
from .exports_api import CompareGridRequest, ExportStageRequest, MatchExportRequest
from .http_errors import ensure_source_reachable
from .identity_media import (
    effective_identity,
    ensure_local_event_logo,
    ensure_local_logo,
    grid_identities,
    resolved_identity_for,
)
from .job_journal import JobJournal, default_journal_path, resume_journaled_jobs
from .jobs import (
    Job,
    JobCancelled,
    JobHandle,
    JobRegistry,
    JobStatus,
    ShutdownInProgressError,
)
from .presence import StoragePresence
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
from .state import (
    STATIC_DIR,
    AppState,
)


def _audit_trim_targets(
    project: MatchProject,
    shooter_root: Path,
    stage: StageEntry,
    *,
    presence: StoragePresence,
) -> tuple[list[StageVideo], list[dict[str, Any]]]:
    """Split ``stage``'s angles into (needs an audit trim, skipped-with-reason).

    One rule, two callers: the ``stages_missing_trim`` count that decides
    whether the Rebuild button renders, and the bulk rebuild endpoint that
    the button posts to (#351). They disagreed before only by accident;
    keeping the rule in one place is the #613 lesson applied here.

    The audit-mode cache is keyed per ``video_id`` for every role -- Audit
    plays secondaries alongside the primary, each synced by its own beep
    offset -- so every non-``ignored`` angle is a target, not just the
    primary. Stage-level skips (no primary, no primary beep, no stage
    time) reject the whole stage and carry no ``video_id``: without an
    auditable primary the other angles have nothing to sync against.

    Existence is answered with ``source_present`` / ``trim_available``,
    both metadata-only. Neither this pass nor its callers may fetch source
    bytes -- the count backs ``GET /api/match/shooters``, which is mounted
    on nearly every SPA route including the anonymous share shell (#637).

    ``presence`` is the request's :class:`StoragePresence` (#1180): existence
    comes from one listing per storage prefix instead of two HEADs per
    angle. It is required, not defaulted, so a new caller cannot silently
    bring the per-angle HEADs back; both callers build one per request
    (``StoragePresence(None)`` in local mode, which delegates to the
    project's own local checks).
    """
    primary = next((v for v in stage.videos if v.role == "primary"), None)
    if primary is None:
        return [], [{"stage": stage.stage_number, "reason": "no_primary"}]
    if primary.beep_time is None:
        return [], [{"stage": stage.stage_number, "reason": "no_beep"}]
    if stage.time_seconds <= 0:
        return [], [{"stage": stage.stage_number, "reason": "no_stage_time"}]

    targets: list[StageVideo] = []
    skipped: list[dict[str, Any]] = []

    def _skip(video: StageVideo, reason: str) -> None:
        skipped.append({"stage": stage.stage_number, "video_id": video.video_id, "reason": reason})

    for video in stage.videos:
        # ``ignored`` is the operator saying this angle isn't part of the
        # take. It has no audit surface, so it gets no encode -- and no
        # skip entry either, since it was never a candidate.
        if video.role == "ignored":
            continue
        if video.beep_time is None:
            _skip(video, "no_beep")
            continue
        # The ``try`` wraps only the lookup so the two reasons stay
        # distinct: ``source_unreachable`` means storage raised,
        # ``source_missing`` means it answered no.
        try:
            present = presence.source_present(project, shooter_root, video.path)
        except Exception:  # noqa: BLE001 -- defensive
            _skip(video, "source_unreachable")
            continue
        if not present:
            _skip(video, "source_missing")
            continue
        cache = audio_helpers.trimmed_video_path(shooter_root, stage.stage_number, video, project=project)
        if presence.trim_available(project, cache) or (
            audio_helpers.stores_web_only(project)
            and presence.trim_available(project, trim_module.web_trim_path(cache))
        ):
            # A web-only project's storage holds the rendition in the
            # trim's place (#1683).
            _skip(video, "already_cached")
            continue
        targets.append(video)
    return targets, skipped


def serve_media(
    storage: Storage | None,
    key: str | None,
    local_path: Path,
    *,
    content_type: str,
    filename: str | None = None,
) -> FileResponse | RedirectResponse:
    """Serve a media object - redirect to R2 in hosted mode, FileResponse in local.

    Redirect (storage supports presigned GET + key not None): presigns a
    GET URL with a caller-type-aware TTL (15 min for share requests, 6 h
    for owner sessions) and returns a 307. The browser reads ranged bytes
    directly from R2 - this process never mirrors the object.

    Direct (no storage, no key, or a storage whose bytes aren't
    browser-reachable such as FilesystemStorage): returns FileResponse
    from ``local_path``, range-native. Unchanged from the pre-R2 behavior.

    The caller is responsible for resolving the correct key (proxy,
    trim, or source) and for checking existence before calling when a
    "not found" response needs a specific status code (e.g. 425 for a
    missing proxy vs 404 for a missing trim).
    """
    if storage is not None and key is not None and storage.supports_presigned_get:
        ttl = _SHARE_MEDIA_TTL if current_share_request.get() else _OWNER_MEDIA_TTL
        url = storage.presign_get_url(key, expires_in=ttl, content_type=content_type, disposition="inline")
        return RedirectResponse(url, status_code=307)
    fn = filename or local_path.name
    return FileResponse(local_path, media_type=content_type, filename=fn)


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


#: Lifetime of the presigned URL ffmpeg streams a proxy's source from
#: (#1674). The store checks the signature on every range request, and a
#: reconnect after a dropped connection is a new request, so the URL must
#: outlive the whole transcode: a 52 s 4K60 HEVC clip takes about 30 s on
#: four threads, and six hours covers a clip hundreds of times longer. A
#: read that still fails falls back to the download path.
_PROXY_SOURCE_URL_TTL_S = 6 * 3600


def _proxy_source_url(storage: Storage, raw_path: str) -> str | None:
    """A presigned GET URL ffmpeg can read ``raw_path`` from, or ``None``
    when the store has none or presigning fails (the caller downloads)."""
    if not storage.supports_presigned_get:
        return None
    try:
        return storage.presign_get_url(raw_path, expires_in=_PROXY_SOURCE_URL_TTL_S)
    except Exception:  # noqa: BLE001 -- any presign failure means "download instead"
        logger.warning("proxy for %s: could not presign the source, downloading", raw_path, exc_info=True)
        return None


async def _dispatch_proxy_job(state: AppState, raw_key: str) -> None:
    """Enqueue a ``generate_proxy`` job for a freshly uploaded raw video.

    Hosted-only and best-effort: the proxy is a fast-scrub optimization,
    so a local-mode upload (no storage) is a no-op and any dispatch
    failure is swallowed - it must never fail the upload it follows.
    Deduped by the raw key so a re-upload of the same name doesn't stack
    a second transcode on top of one already in flight.
    """
    if state.storage is None:
        return
    try:
        existing = await state.jobs.find_active(kind="generate_proxy", video_id=raw_key)
        if existing is not None:
            return
        await state.jobs.submit(
            kind="generate_proxy",
            video_id=raw_key,
            args={"raw_path": raw_key},
        )
    except FeatureRequiredError as exc:
        logger.info("generate_proxy for %s not queued: account lacks %s", raw_key, exc.feature.value)
    except Exception:  # noqa: BLE001 - proxy is an optimization; never fail the upload
        logger.exception("failed to dispatch generate_proxy for %s", raw_key)


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


BEEP_BEFORE_CLIP_MAX_S = 2.0


# The engine's ``StageData`` requires a non-None ``scorecard_updated_at``
# (the video-matching heuristic keys off it), but manually-timed stages on
# scoreboard-less matches have none. Export never reads the field, so feed
# this sentinel rather than inventing a real-looking time -- same approach
# as ``splitsmith.mcp.export_tools``.
_PLACEHOLDER_SCORECARD_TIME = datetime(2000, 1, 1, tzinfo=UTC)


def _reject_non_finite(payload: Any, *, what: str) -> None:
    """Refuse a document carrying ``Infinity``, ``-Infinity`` or ``NaN``.

    ``json.loads`` accepts those three bare tokens, but Starlette's
    ``JSONResponse`` encodes with ``allow_nan=False`` -- so an audit PUT
    carrying one persisted the document and *then* 500'd on the way out,
    and every subsequent read of that stage 500'd too. Nothing but a
    later valid PUT could recover it (#843).

    The guard belongs here, at the save boundary, rather than in any one
    field's handling: the same body reaches disk with ``time``,
    ``ms_after_beep``, ``candidate_number`` and whatever numeric field a
    future document shape adds, and each would otherwise need its own
    check. Raising *before* the write is what makes the failure
    recoverable -- the stage keeps its last good document.

    Not reachable from the SPA: ``JSON.stringify(Infinity)`` emits
    ``null``. It takes a hand-crafted request, so this is a
    self-inflicted-damage guard, not a hostile-input one.
    """
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, float) and not isfinite(node):
            raise HTTPException(
                status_code=422,
                detail=f"{what} contains a non-finite number (NaN or Infinity)",
            )
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, (list, tuple)):
            stack.extend(node)


def _reconcile_key(
    match_id: str | None, kind: str, slug: str | None, stage_number: int | None
) -> tuple[str | None, str, str | None, int | None]:
    """Identity a reconcile job shares with its ``Job`` snapshot. The match
    comes from ``current_match_id``, the same ContextVar ``submit`` stamps
    on the job. Per stage, not per video: one reconcile step per (kind,
    stage) is in flight at a time, which ``find_active`` guarantees."""
    return (match_id, kind, slug, stage_number)


def _submit_reconcile_steps(state: AppState, match_root: Path) -> list[ReconcileStep]:
    """Queue every step the reconciler finds missing for ``match_root``.

    Runs on a job worker thread (the tail of a sync), so it bridges to the
    async registry with ``run_sync`` like the trim chain does. The
    caller's ContextVars carry the match, which is what scopes both the
    ``find_active`` dedupe and the submitted jobs.
    """
    projects, audits = load_reconcile_inputs(match_root)
    failures = load_auto_prefs(match_root).reconcile_failures
    steps = plan_reconcile(
        projects,
        audits,
        failures,
        present_sources=present_sources_for(match_root, projects),
        valid_trims=valid_trims_for(match_root, projects),
    )
    queued: list[ReconcileStep] = []
    for step in steps:
        dedupe: dict[str, Any] = {"video_id": step.video_id} if step.kind == "trim" else {}
        existing = run_sync(
            state.jobs.find_active(
                kind=step.kind, stage_number=step.stage_number, shooter_slug=step.slug, **dedupe
            )
        )
        if existing is not None:
            continue
        args: dict[str, Any] = {"slug": step.slug, "stage_number": step.stage_number}
        if step.kind == "trim":
            args["video_id"] = step.video_id
            # A trim healed under an audited stage (its file was gone or
            # invalid, spec 2026-10-12) must not chain detection over real
            # shots; video_step never detects over them either.
            audit_doc = audits.get(step.slug, {}).get(step.stage_number)
            if audit_doc is not None and not is_stub_audit(audit_doc):
                args["chain_shot_detect"] = False
        key = _reconcile_key(current_match_id.get(), step.kind, step.slug, step.stage_number)
        state.reconcile_jobs[key] = (match_root, step)
        run_sync(
            state.jobs.submit(
                kind=step.kind,
                stage_number=step.stage_number,
                shooter_slug=step.slug,
                video_id=step.video_id if step.kind == "trim" else None,
                args=args,
            )
        )
        queued.append(step)
    return queued


def _record_reconcile_outcome(state: AppState, job: Job) -> None:
    """Terminal listener half of the failure memo: a failed reconcile step
    is remembered with its inputs so the next pass skips it; a success
    clears any old entry for the same step."""
    if job.kind not in ("trim", "shot_detect"):
        return
    tracked = state.reconcile_jobs.pop(
        _reconcile_key(job.match_id, job.kind, job.shooter_slug, job.stage_number), None
    )
    if tracked is None:
        return
    match_root, step = tracked
    if job.status == JobStatus.FAILED:
        update_auto_prefs(match_root, lambda p: p.reconcile_failures.__setitem__(step.key, step.input_key))
    elif job.status == JobStatus.SUCCEEDED:
        update_auto_prefs(match_root, lambda p: p.reconcile_failures.pop(step.key, None))


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
def _save_audit_with_remerge(
    state: AppState,
    slug: str,
    stage_number: int,
    *,
    merge: Callable[[dict], dict],
    default: Callable[[], dict],
) -> int:
    """Fold ``merge`` into a stage's audit doc through the storage port and
    return the new version.

    ``merge`` is a pure function from the current doc to the wanted one and
    may run more than once: locally the port applies it under the audit
    lock to the doc as it is on disk (so a manual edit saved while a job ran
    survives, #931); hosted it saves under the optimistic version and, when
    a concurrent writer won, reloads the winner's doc and re-applies
    ``merge`` to that. ``default`` builds a fresh doc when none exists.
    """
    return state.docstore().update_audit(slug, stage_number, merge, default)


def _update_project(state: AppState, slug: str, apply: Callable[[MatchProject], None]) -> int:
    """Apply ``apply`` to the shooter's current project and save it, through
    the storage port's read-modify-write; return the new version.

    For a job that writes a few fields after minutes of work. Saving the
    project it loaded at its start would overwrite every edit made since,
    and hosted, where another job of the same shooter commonly saves in
    between, it fails the job with a version conflict. Here ``apply`` runs
    on the document as it is now, again on the winner's when a concurrent
    save got there first, so it must set the job's own fields and nothing
    else, and be safe to run more than once.
    """

    def merge(doc: dict) -> dict:
        project = MatchProject.model_validate(doc)
        apply(project)
        return project.model_dump(mode="json")

    return state.docstore().update_project(slug, merge)


def _record_export_run(state: AppState, slug: str, run: export_runs.ExportRun) -> None:
    """Append one run to the shooter's export history. Never raises.

    The storage port does the read-modify-write: under
    :attr:`AppState.export_run_lock` on the desktop (the Export page's
    bundle button submits one job per stage against one document, and an
    unlocked read-modify-write lost runs in practice), under an optimistic
    version with a re-merge onto the winner's log when hosted. Every
    failure, a store that is down, a disk that is full, a log from a newer
    build, retries exhausted, logs at WARNING and returns: the deliverables
    are the product and they are already written by the time this runs;
    failing the job over bookkeeping would report a successful export as
    broken.
    """
    try:
        state.docstore().update_export_runs(slug, lambda doc: export_runs.append_run(doc, run))
    except export_runs.NewerLogError as exc:
        logger.warning(
            "export run record: run %s not written for %s, the log is from a newer build: %s",
            run.run_id,
            slug,
            exc,
        )
    except Exception as exc:  # noqa: BLE001 -- see docstring
        logger.warning("export run record: not written for %s: %s", slug, exc)


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


def _count_flagged(docs: Iterable[dict | None]) -> int:
    """Count audit docs whose ``needs_attention.flagged`` is truthy.

    The one flagged-counting rule, shared by ``_build_triage_response``
    (docs it already loaded while assembling cells) and the triage
    summary endpoint (docs loaded without the anomaly/status-walk cost
    of the full grid) - so there's exactly one definition of "flagged"
    across both.
    """
    count = 0
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        na = doc.get("needs_attention")
        if isinstance(na, dict) and na.get("flagged"):
            count += 1
    return count


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


class AccessRequestBody(BaseModel):
    """Body of ``POST /api/v1/access-requests`` (spec 2026-10-03): the
    login page's "Request access" form and the marketing site's waitlist
    form. Module-level for the same reason as :class:`AuthBeginRequest`."""

    email: str = Field(max_length=320)
    note: str | None = Field(default=None, max_length=2000)  # the store keeps 500
    hp: str | None = None  # the marketing form's honeypot
    source: Literal["form", "waitlist"] = "form"


# The one reply to an access request, whatever the email's state: it must
# not tell anyone which addresses have accounts or requests.
INTAKE_MESSAGE = "If you have access, a sign-in link is on its way. Otherwise your request has been noted."


# The client address of the sign-in request being served, so the blocked
# branch of ``begin_login`` (``on_blocked``, which only sees the email) can
# charge the intake rate limits. Set by ``/api/v1/auth/begin``.
_intake_client: ContextVar[str | None] = ContextVar("_intake_client", default=None)


def _intake_origins() -> frozenset[str]:
    """Origins allowed to post the access-request form cross-site (the
    marketing site). Read per request so a test or deploy can set it."""
    raw = os.environ.get(
        "SPLITSMITH_ACCESS_REQUEST_ORIGINS", "https://splitsmith.app,https://www.splitsmith.app"
    )
    return frozenset(o.strip() for o in raw.split(",") if o.strip())


class ShareInfo(BaseModel):
    """One share link as returned by the owner-management routes.

    ``url`` embeds the raw token - it does not appear anywhere else
    in the API response. Module-level so FastAPI resolves it correctly
    under ``from __future__ import annotations``."""

    id: str
    url: str
    created_at: datetime
    revoked_at: datetime | None
    scope: str
    # Per shooter, the camera this link's viewers start on; None follows
    # each shooter's saved default (``compare_camera``).
    cameras: dict[str, str] | None = None


class WorkerRegisterRequest(BaseModel):
    """Body for ``POST /api/workers/register`` (self-hosted worker bring-up)."""

    token: str
    info: dict[str, Any] = Field(default_factory=dict)


class WorkerRegisterResponse(BaseModel):
    """Successful registration: the long-lived worker token (plaintext, shown
    exactly once) plus the connection material the worker daemon persists
    locally (``AppState.worker_credentials``)."""

    worker_id: str
    worker_token: str
    credentials: dict[str, Any]


class WorkerView(BaseModel):
    """One worker as returned by admin routes (Task 7).

    ``status`` is derived at query time - not stored - so it reflects live
    channel state and the platform job's config presence without a separate field."""

    id: str
    name: str
    kind: Literal["self_hosted", "cloud_run"]
    enabled: bool
    priority: int
    status: Literal["online", "offline", "pending", "disabled"]
    registered: bool
    last_seen_at: datetime | None
    last_wake_at: datetime | None
    version: str | None  # semver the worker is running; None until first reported
    info: dict[str, Any] | None


class AdminWorkerListResponse(BaseModel):
    """Response body for ``GET /api/admin/workers``.

    ``server_version`` is this server's own release. The admin UI compares each
    worker's ``version`` against it to flag self-hosted agents that have fallen
    behind the current release (release-please publishes the PyPI package and
    deploys the server together, so "behind the server" == "behind latest")."""

    workers: list[WorkerView]
    server_version: str


class AdminCreateWorkerBody(BaseModel):
    """Request body for ``POST /api/admin/workers``."""

    name: str
    priority: int = 10


class AdminCreateWorkerResponse(BaseModel):
    """Response body for ``POST /api/admin/workers``.

    ``registration_token`` is the plaintext one-time token the operator
    pastes into the worker box's bring-up command. It is shown exactly
    once and not stored in plaintext anywhere."""

    worker: WorkerView
    registration_token: str
    expires_at: str  # ISO-8601
    docker_command: str


class AdminPatchWorkerBody(BaseModel):
    """Request body for ``PATCH /api/admin/workers/{worker_id}``.

    All fields are optional; only supplied fields are updated."""

    enabled: bool | None = None
    priority: int | None = None
    name: str | None = None


#: What an account with no features may still reach, as (method, path):
#: who am I, and signing a credential out (session cookie or desktop token).
#: Method-paired so ``PATCH /api/me`` stays refused.
_DISABLED_ALLOWED_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/me"),
        ("POST", "/api/v1/auth/logout"),
        ("DELETE", "/api/device/session"),
    }
)

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
        # Asking for an account happens before one exists. The route
        # answers the same 202 whatever the email's state (spec 2026-10-03).
        "/api/v1/access-requests",
        # Self-hosted worker bring-up + wake channel: the registration /
        # worker token in the request IS the auth (checked in the handlers,
        # uniform 404 on any failure) - the session gate must not 401 a
        # headless box that has no cookie jar.
        "/api/workers/register",
        "/api/workers/channel",
        # Device-flow bring-up (#719): the desktop install has no cookie
        # jar and, by definition, no bearer yet. Same rationale already
        # recorded above for /api/workers/register - the credential in
        # the request IS the authorization, checked in the handlers, and
        # an unknown device code is indistinguishable from an expired
        # one so there is nothing to probe for.
        "/api/device/authorize",
        "/api/device/token",
    }
)


# GET-only path shapes reachable through /api/share/{token}/ - the read
# half of the anonymous surface. _SHARE_WRITE_ROUTES below is the other
# half, admitted through the same middleware for POST/DELETE. Anything
# else under the prefix, on any method, is a uniform 404. The share
# middleware impersonates the owner's tenant for the request, so this
# whitelist is the containment boundary: extend it only with read-only,
# match-scoped routes, and never let the client supply the match id. The
# pattern is anchored (fullmatch) and case-sensitive by design.
#
# ``[0-9]`` rather than ``\d`` for every stage number, here and in
# _SHARE_WRITE_ROUTES / capabilities._COMMENT_ROUTES. Python's ``\d``
# matches any Unicode decimal digit, but the ``int`` path parameter on
# the route behind it is ASCII-only - so ``stages/U+0663/coach`` passed
# this allowlist and then 422'd at the route, which is a refusal shape
# distinguishable from the uniform 404 (final review, I6; the GET half
# predates the comment work). ``[0-9]`` makes the allowlist admit
# exactly what the route can parse.
_SHARE_PATH_RE = re.compile(
    r"^(?:match/shooters"
    r"|shooters/[^/]+/project"
    r"|shooters/[^/]+/stages/[0-9]+/coach"
    # The comment thread. Readable through ANY scope, including plain
    # "read" - a link already in the wild shows the conversation but
    # cannot join it. Posting is _SHARE_WRITE_ROUTES's business.
    r"|shooters/[^/]+/stages/[0-9]+/comments"
    r"|shooters/[^/]+/coach/distributions"
    r"|shooters/[^/]+/videos/stream"
    # The shooter's logo (#1249): already in every video the share shows.
    r"|shooters/[^/]+/identity/logo"
    # The highlight reel (spec 2026-10-18): the route naming the newest one
    # and that file, in exactly the shape ``export_naming.reel_file_name``
    # writes (``REEL_FILE_RE``). No other export is reachable from a share.
    r"|shooters/[^/]+/highlights/latest"
    r"|shooters/[^/]+/exports/file/[a-z0-9-]+-highlights(?:-short)?\.mp4"
    r"|match/stage/[0-9]+/compare"
    # Same registry as the "shooters/[^/]+/videos/stream" shape above -
    # this alternative also reaches the registered-video branch of
    # stream_shooter_video (raw sources/proxies), not just Compare's
    # logical trim refs. That's deliberate: it's the multi-shooter
    # wrapper the SPA's Compare view streams through, and parity with
    # the already-whitelisted single-shooter shape means it exposes
    # nothing new in-surface.
    r"|match/shooters/[^/]+/videos/stream"
    r"|og\.png"
    # The compare card routes (share_og.share_compare_png /
    # share_compare_meta) ride these two alternatives too - "compare" is
    # just another value for [^/]+ here, admitted deliberately rather than
    # incidentally. Route registration order (compare defined above the
    # {slug} routes in share_og.py) is what makes "compare" dispatch to
    # the compare card instead of a shooter slugged "compare".
    r"|og/[^/]+/[0-9]+\.png"
    r"|og-meta"
    r"|og-meta/[^/]+/[0-9]+)$"
)


# The anonymous WRITE surface - deliberately a second table rather than
# an extension of _SHARE_PATH_RE, whose docstring calls itself GET-only
# and must stay true. Admission requires all three of: a (method, shape)
# pair here, and a resolved token whose scope is write-capable
# (db.share_guard.scope_may_write). Any one missing is the same opaque
# 404 as an unknown token, so the write surface is not discoverable by
# probing.
#
# Method-paired (fix round 1, F5) rather than a single pattern matched
# against a separate method set: that earlier shape took the cross
# product of "any shape here" x "any method in _SHARE_WRITE_METHODS", so
# POST admitted the DELETE-by-id shape too. That POST then reached
# ``required_capability``'s ``_COMMENT_ROUTES`` table (ui/capabilities.py)
# unmapped for POST-on-that-shape, fell through to the EDIT default, and
# refused 403 among otherwise uniform 404s - the exact discriminator this
# allowlist exists to deny. Pairing each shape with its one valid method
# here mirrors the two anonymous entries of ``_COMMENT_ROUTES``, so the
# two tables cannot drift into a gap. ``_COMMENT_ROUTES`` carries one
# extra entry this table deliberately does not - ``DELETE
# match/comments``, the owner's bulk-moderation route, which is
# authenticated-only and must stay unreachable from a share token (final
# review, I4). It is absent here, so an anonymous caller gets the uniform
# 404 on it under any scope.
#
# ``\A``/``\Z`` rather than ``^``/``$`` for the reason _REVIEW_ROUTES
# documents: plain ``$`` also matches just before a single trailing
# newline, and on an allow-list that direction is the unsafe one.
# ``[0-9]`` rather than ``\d`` for the reason _SHARE_PATH_RE documents.
_SHARE_WRITE_ROUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"\Ashooters/[^/]+/stages/[0-9]+/comments\Z")),
    ("DELETE", re.compile(r"\Ashooters/[^/]+/stages/[0-9]+/comments/[A-Za-z0-9]+\Z")),
)


def _share_write_admits(method: str, rest: str) -> bool:
    """Whether (method, path) is an admitted anonymous write shape."""
    return any(m == method and p.match(rest) is not None for m, p in _SHARE_WRITE_ROUTES)


def _json_safe(value: Any) -> Any:
    """Replace non-finite floats with their text form, recursively.

    Starlette renders every JSONResponse with ``json.dumps(...,
    allow_nan=False)``, so a ``nan``/``inf`` anywhere in a response body
    raises ``ValueError`` *while rendering* - which surfaces to the
    caller as a 500 with a stack trace in the log.

    That was reachable anonymously (final review, I1). ``POST
    /api/share/{token}/shooters/{slug}/stages/{n}/comments`` with
    ``{"body":"x","anchor_t":NaN}`` fails validation, and pydantic's
    error record echoes the offending value back in its ``input`` field;
    FastAPI puts that record straight into the 422 body. The request
    dies during validation, before the rate limiter and before the stage
    cap, so an attacker could drive unbounded 500s for free.
    ``Field(allow_inf_nan=False)`` does not help - the raw float is still
    what ``errors()`` reports as ``input``.

    Every other 422 keeps its exact shape: for any structure that is
    already JSON-clean this is the identity function, so the SPA's
    ``detail.code`` matching is untouched.
    """
    if isinstance(value, float) and not isfinite(value):
        # repr, not None: "nan"/"inf"/"-inf" tells the caller what was
        # rejected, and dropping the key would change the record's shape.
        return repr(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


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


def _valid_author_key_len(raw: str) -> bool:
    """Whether ``raw`` is a plausible client-minted author key.

    Bounded on both ends (fix round 1, F7): the client always mints 32
    random bytes hex-encoded (64 chars), well inside ``[MIN, MAX]``. A key
    below the floor is easy to guess or collide, which turns self-delete
    into anyone-delete for that handle - identity, ``mine``, and
    self-delete all key off this value's hash.
    """
    return MIN_AUTHOR_KEY_LEN <= len(raw) <= MAX_AUTHOR_KEY_LEN


def _require_comment_scope(state: AppState, slug: str, stage_number: int) -> None:
    """Bound the (slug, stage_number) path segments before any comment-
    store call (fix round 1, F1/F2).

    ``state.shooter_root`` is the roster-membership check every other
    shooter-scoped route on this surface already goes through (e.g. the
    coach route); the comment routes as first written skipped it, so a
    comment-scoped token could ``POST`` to
    ``shooters/does-not-exist/stages/999/comments`` and get 201.
    ``STAGE_COMMENT_CAP`` does not catch that either - ``count_for_stage``
    filters on slug + stage_number, the two values the caller chooses, so
    varying either resets the cap: one admitted link buys unbounded rows
    at slugs/stages no UI renders and no owner can find to moderate.

    A stage_number large enough additionally overflows the driver's
    integer column before any predicate is even evaluated - ``OverflowError``
    on SQLite, ``NumericValueOutOfRange`` on Postgres, and this test suite
    is SQLite-backed so it cannot see the Postgres class of that bug.
    Checking against the match's own stage list closes both holes at
    once: nothing in a real stage list is ever attacker-sized.
    """
    state.shooter_root(slug)
    known_stages = {s.stage_number for s in state.match().stages}
    if stage_number not in known_stages:
        raise HTTPException(status_code=404, detail="not found")


def _may_mint_shot_ids() -> bool:
    """Whether this request's save boundary may invent a *non-convergent*
    shot id (#631 Task 7).

    False on a ``desktop``-origin mirror -- passed straight through as
    ``ensure_shot_ids``'s ``mint`` argument, see its docstring for exactly
    what ``mint=False`` still derives (a detected shot's ``cand-<n>``,
    convergent by construction) versus suppresses (the time-keyed and
    uuid4 branches). ``derive_shot_id`` keys a candidate-less manual shot
    off its rounded time, so a desktop that nudged such a shot to 6.52 s
    and a phone that accepted the mirror at 6.5 s would mint
    ``manual-t6520`` and ``manual-t6500`` for the same shot. Both sides
    then look fully stamped, the sync merge's unstamped-shot gate passes,
    and one shot unions into two with no note at all. The desktop is
    therefore the sole minter of *that* kind of id for a mirror: it stamps
    legacy documents in the sync run's migration pass and pushes them
    here.

    A shot that arrives carrying a client-supplied id keeps it either way
    -- ``ensure_shot_ids`` never rewrites one -- which is what lets a phone
    add a genuinely new shot to a mirror.

    Reads the origin the alias middleware already pinned for this request,
    the same fact the read-only-mirror gate reads, so this costs no extra
    ``matches_store`` query. ``None`` outside an aliased request (local
    mode, legacy bare-path traffic) -> mint, i.e. today's behaviour.
    """
    return current_match_origin.get() != "desktop"


def _is_mirror() -> bool:
    """Whether the current request is on a desktop-origin mirror. Reads the
    origin the alias middleware pinned, like ``_may_mint_shot_ids``."""
    return current_match_origin.get() == "desktop"


def _serialized_capabilities() -> list[str]:
    """Sorted capability names for the payload ``capabilities`` field.

    ``current_match_capabilities`` is pinned by the alias middleware to a
    (possibly empty, on a read-scoped share) frozenset - ``None`` only
    outside an aliased request (legacy bare-path traffic), which falls
    back to ``capabilities_for_origin(None)``. A plain ``or`` fallback
    would be wrong here: an empty frozenset is falsy in Python, so it
    would silently widen a share response's capabilities.
    """
    caps = current_match_capabilities.get()
    return sorted(caps if caps is not None else capabilities_for_origin(None))


# TTL (seconds) for presigned media GET URLs. Short for share requests so
# that revoking a token takes effect within one window; generous for owner
# sessions that span a long editing or review session.
_SHARE_MEDIA_TTL: int = 15 * 60  # 15 minutes
_OWNER_MEDIA_TTL: int = 6 * 3600  # 6 hours


def user_looks_cache_root() -> Path:
    """Where hosted accounts' Looks are materialized (#1263); one folder per
    account under it, content-named inside that."""
    return process_runtime().cache_dir / "user-looks"


def tenant_looks_provider(tenant: TenantContext) -> looks_module.UserLooksProvider:
    """The ``looks.set_user_looks_provider`` answer for ``tenant``: its
    Looks materialized under the cache, or none at all. Set wherever a
    tenant is pinned (the auth gate, the share alias, the queue task), so a
    renderer resolving a Look by name sees this account's and no other's."""

    def provide() -> Path | None:
        if tenant.looks is None:
            return None
        return tenant.looks.materialized_dir(user_looks_cache_root())

    return provide


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


def _shooter_label(
    match: match_model.Match, match_root: Path, slug: str, *, project: MatchProject | None = None
) -> str:
    """The display label a slug binds to: the shooter's name, or the slug itself.

    Mirrors ``compare/cli.py::_export_from_match``'s ``label = shooter.name
    or slug``. Shared so the compare-grid endpoint's audio-source check and
    its worker's ``audio_label`` resolution can't drift from
    ``_load_compare_bundles``'s own per-shooter labelling.

    Hosted (#755) has no ``shooter.json`` on disk -- the roster's names
    come from the project document, the same field the shooter list
    shows (``competitor_name``, else the slug) -- so a caller that holds
    the store-loaded ``project`` passes it and that is what names the
    tile when the file is absent.
    """
    try:
        shooter = match.load_shooter(match_root, slug)
    except (FileNotFoundError, KeyError):
        if project is None:
            raise
        return project.competitor_name or slug
    return shooter.name or slug


#: The tile inset's width as a share of the cell's, by the request's size.
GRID_INSET_SCALE: dict[str, float] = {"small": 0.22, "medium": 0.30, "large": 0.40}


def _load_compare_bundles(
    match_root: Path,
    match: match_model.Match,
    *,
    cameras: dict[str, str] | None = None,
    inset: str | None = None,
    state: AppState | None = None,
    presence: StoragePresence | None = None,
) -> list[project_loader.CompareShooterBundle]:
    """Load every shooter on ``match`` as a compare-grid bundle.

    Shared by the compare-grid endpoint's up-front validation and its
    worker (``_run_compare_grid``), so both read exactly the same trims --
    the endpoint decides whether to queue from this same read, and the
    worker renders from it. ``cameras`` keys match either a shooter's
    slug or its display name, mirroring ``compare export``'s
    ``--camera SHOOTER=VALUE`` flag. Raises
    :class:`splitsmith.camera_select.CameraResolutionError` when a
    requested camera resolves on no stage of that shooter's project --
    callers map that to a 400.

    ``presence`` is the endpoint's: with it the trims are checked against
    the request's storage listing and never pulled or probed, so a bundle
    carries which stages have a trim and nothing measured from the file
    (a request must not pull media; a full match's trims are gigabytes).
    The worker passes none and pulls each trim to render it.
    """
    cameras = cameras or {}
    bundles: list[project_loader.CompareShooterBundle] = []
    for slug in match.shooters:
        # Hosted (#755): the project document is in the state store and
        # the trims are in object storage, so hand the loader the doc and
        # a hook that mirrors each expected trim down before the loader
        # looks for it. Local: ``project=None`` reads ``project.json`` off
        # disk and ``ensure_trim=None`` checks the disk, as always.
        project: MatchProject | None = None
        ensure_trim: Callable[[Path], object] | None = None
        trim_exists: Callable[[Path], bool] | None = None
        if state is not None:
            project = state.shooter_project(slug)
            bound_project = project

            if presence is not None:

                def _listed(trim: Path, _project: MatchProject = bound_project) -> bool:
                    return export_storage.export_listed(_project, trim, presence)

                trim_exists = _listed
            else:

                def _pull(trim: Path, _project: MatchProject = bound_project) -> bool:
                    # A no-op when the project has no storage scope bound.
                    return export_storage.pull_export_file(_project, trim)

                ensure_trim = _pull
        label = _shooter_label(match, match_root, slug, project=project)
        camera = cameras.get(slug, cameras.get(label))
        bundles.append(
            project_loader.load_shooter_from_match(
                match_root,
                slug,
                label,
                camera=camera,
                inset=inset,
                match=match,
                project=project,
                ensure_trim=ensure_trim,
                trim_exists=trim_exists,
                probe=_unprobed if trim_exists is not None else None,
            )
        )
    return bundles


def _unprobed(_trim: Path) -> VideoMetadata:
    """The probe for a presence-only bundle: the file is not here to open."""
    return VideoMetadata(width=0, height=0, duration_seconds=0.0, frame_rate_num=0, frame_rate_den=1)


def _filter_bundles_to_stages(
    bundles: list[project_loader.CompareShooterBundle], stage_numbers: list[int]
) -> list[project_loader.CompareShooterBundle]:
    """Restrict each bundle to just the requested stage numbers.

    ``CompareGridRequest.stage_numbers`` selects a subset explicitly --
    unlike the CLI's whole-match export, which always renders every
    stage a shooter has a trim for. Rendering unfiltered bundles here
    would grid every stage on disk regardless of what the user picked.
    """
    wanted = set(stage_numbers)
    return [
        replace(b, stages_by_number={n: s for n, s in b.stages_by_number.items() if n in wanted})
        for b in bundles
    ]


#: A compare-grid output name is a bare file stem, not a path.
_COMPARE_OUTPUT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _compare_output_stem(name: str) -> str:
    """Validate ``CompareGridRequest.output_name`` as a plain file stem.

    The worker builds ``exports / f"{name}.mp4"``, so ``"../../x"``
    writes outside the match directory. Local mode on the user's own
    machine and the SPA hardcodes the value, so this is a cheap guard
    rather than a security boundary -- but nothing else stops it, and
    rejecting is a line.
    """
    stem = name.strip()
    if not _COMPARE_OUTPUT_NAME.match(stem):
        raise ValueError(
            f"output_name={name!r} must be a plain file name: it has to start with a letter "
            "or digit and may contain only letters, digits, dots, dashes and underscores "
            "(no path separators)"
        )
    return stem


def _compare_missing_trims(
    bundles: list[project_loader.CompareShooterBundle], stage_numbers: list[int]
) -> list[dict[str, Any]]:
    """Shooter/stage pairs inside the selection whose trim is not on disk.

    ``project_loader`` records these on every bundle it loads and the CLI
    already prints them (``compare/cli.py::_warn_missing_trims``); the
    endpoint used to load them and throw them away, which left a black
    cell indistinguishable from a shooter who skipped the stage (#618).
    Filtered to the selection because ``missing_trims`` covers the whole
    project, and gaps outside the render are noise.
    """
    wanted = set(stage_numbers)
    return [
        {
            "shooter": bundle.label,
            "stage_number": miss.stage_number,
            "stage_name": miss.stage_name,
            "expected_path": str(miss.expected_path),
            "camera": miss.camera,
        }
        for bundle in bundles
        for miss in bundle.missing_trims
        if miss.stage_number in wanted
    ]


def _compare_grid_progress(handle: JobHandle) -> mp4_grid.GridProgress:
    """``render_grid_mp4``'s ``progress`` hook as job updates: one per
    stage as it is encoded or reused from the segment cache, then the
    stitch. Without it the job sits at 5% for the whole multi-minute
    encode and a working render looks exactly like a hung one."""

    def _report(step: mp4_grid.GridRenderStep) -> None:
        if step.plan is None:
            handle.update(progress=0.95, message=f"Stitching {step.total} stage(s)...")
            return
        verb = "Reusing" if step.status == "reused" else "Rendering"
        handle.update(
            progress=0.05 + 0.9 * (step.index / step.total) if step.total else 0.05,
            message=(
                f"{verb} stage {step.plan.stage_number} ({step.plan.stage_name}) "
                f"-- {step.index + 1} of {step.total}..."
            ),
        )

    return _report


def _run_compare_grid(
    handle: JobHandle, req: CompareGridRequest, match_root: str, *, state: AppState | None = None
) -> None:
    """Worker for the match-scoped compare-grid MP4 export (phase 0).

    Mirrors ``compare/cli.py::_render_grid_mp4``'s shape: owns a scratch
    work dir beside the output and always removes it, success or not.
    The result carries the output's name (and, for a local install, its
    on-disk path) plus per-stage outcomes so a partially-successful
    render (some stages failed, others didn't) is reported rather than
    treated as an all-or-nothing failure.

    Hosted (#755): the match and project documents come from the state
    store, every selected stage's trim and audit is mirrored down before
    the render, and the finished grid is pushed to the match-scoped key
    ``matches/<id>/exports/<name>`` on produce -- the single-shooter
    export job's pattern, one level up because the grid has no owning
    shooter. ``GET /api/match/exports/file/{name}`` pulls it back.

    ``stages_total`` counts the stages the *request* asked for, not the
    ones that got planned. ``build_stage_plans`` derives its stage list
    from the shooters' trims, so a selected stage nobody has produces no
    plan at all -- counted against the plans, that read back as
    "rendered all 2 stages" for a three-stage request. Those stages are
    named in ``skipped_stages``, and the shooter/stage pairs behind them
    in ``missing_trims``.
    """
    root = Path(match_root)
    match = state.match() if state is not None else match_model.Match.load(root)
    stem = _compare_output_stem(req.output_name)
    requested = sorted(set(req.stage_numbers))

    handle.update(progress=0.02, message="Loading shooters...")
    with handle.timer.phase("load_shooters"):
        if state is not None:
            # The overlay reads each stage's audit off disk; materialize
            # the selected stages' docs before the loader builds paths (a
            # no-op where the documents are the files).
            for slug in match.shooters:
                for number in requested:
                    state.materialize_audit(slug, number)
        bundles = _load_compare_bundles(root, match, cameras=req.cameras, inset=req.inset_camera, state=state)
        filtered = _filter_bundles_to_stages(bundles, requested)
        audio_project = state.shooter_project(req.audio_from) if state is not None else None
        audio_label = _shooter_label(match, root, req.audio_from, project=audio_project)

    output_dir = root / "exports"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{stem}.mp4"

    # Planned up front purely for the progress messages and the skipped
    # count -- pure planning, no ffmpeg. ``render_grid_mp4`` plans again
    # internally from the same inputs and so sees the same stages.
    plans = mp4_grid.build_stage_plans(
        filtered,
        audio_label=audio_label,
        head_pad_seconds=1.0,
        tail_pad_seconds=0.5,
        layout_2up="horizontal",
    )
    skipped = [number for number in requested if number not in {p.stage_number for p in plans}]

    def _notice(text: str) -> None:
        # Said as it is decided, before any stage encodes: the message is
        # what the job shows while the render runs, and the same text
        # lands on the result as ``degradations`` for the viewer who only
        # reads the end.
        handle.update(message=text)

    handle.update(progress=0.05, message=f"Rendering {len(plans)} stage(s)...")
    with (
        handle.timer.phase("render"),
        tempfile.TemporaryDirectory(dir=output_dir, prefix=".compare-grid-work-") as tmp,
    ):
        account_brand = account_profile_module.load_brand(
            state.account_profile if state is not None else account_profile_module.JsonAccountProfileStore()
        )
        event_logo = ensure_local_event_logo(
            match.branding,
            root,
            storage=state.storage if state is not None else None,
            match_id=match.match_id,
        )
        identities = grid_identities(
            filtered,
            look=load_look(req.overlay_theme),
            book=shooter_book_module.load_snapshot(
                state.shooter_book if state is not None else shooter_book_module.JsonShooterBookStore()
            ),
        )
        title_page, closing = compare_cards.title_cards(
            match,
            compare_cards.CardOptions(
                stage_titles=req.stage_titles,
                title_duration_seconds=req.title_duration_seconds,
                title_page=req.title_page,
                title_info=req.title_info,
                title_division=req.title_division,
                title_page_duration_seconds=req.title_page_duration_seconds,
                closing_card=req.closing_card,
                made_with=req.made_with,
                account_brand=req.account_brand,
                card_variant=req.card_variant,
                title_page_variant=req.title_page_variant,
                closing_card_variant=req.closing_card_variant,
            ),
            divisions=compare_cards.bundle_divisions(filtered),
            brand=account_brand,
            event_logo=event_logo,
        )
        result = mp4_grid.render_grid_mp4(
            filtered,
            audio_label=audio_label,
            output_path=output_path,
            canvas=mp4_grid.GridCanvas(width=req.canvas_width, height=req.canvas_height),
            work_dir=Path(tmp),
            progress=_compare_grid_progress(handle),
            segment_cache=match_export_helpers.render_segment_cache(Config().output),
            title_page=title_page,
            closing=closing,
            stage_titles=req.stage_titles,
            title_duration_seconds=req.title_duration_seconds,
            card_variant=req.stage_card_variant or req.card_variant,
            identities=identities,
            transitions=uniform_transitions(req.transition_kind, req.transition_duration_seconds, len(plans)),
            overlay=req.overlay,
            overlay_theme=req.overlay_theme,
            summary_hold_seconds=req.summary_hold_seconds,
            on_notice=_notice,
            inset=mp4_grid.GridInset(corner=req.inset_corner, scale=GRID_INSET_SCALE[req.inset_size]),
            free_cell=req.free_cell,
            match_name=match.name or "",
            match_date=match.match_date.isoformat() if match.match_date else None,
            match_summary_seconds=req.match_summary_seconds if req.match_summary else 0.0,
            logo_spots=frozenset(req.logo_spots),
            breakdown=req.breakdown,
            brand=account_brand if req.account_brand else None,
        )

    youtube_files: list[Path] = []
    if req.youtube_sidecar:
        youtube_files = _write_grid_youtube_sidecar(
            match,
            req,
            result,
            plans,
            thumbnail_data=_grid_thumbnail_data(
                match,
                req,
                identities=[identities[label] for label in sorted(identities)],
                brand=account_brand if req.account_brand else None,
                event_logo=event_logo,
            ),
        )

    if state is not None and state.storage is not None:
        # A no-op without a match id (local mode has no match-scoped store).
        handle.update(progress=0.98, message="Uploading the grid...")
        with handle.timer.phase("push"):
            export_storage.push_match_export_file(state.storage, current_match_id.get(), result.output_path)
            for extra in youtube_files:
                export_storage.push_match_export_file(state.storage, current_match_id.get(), extra)

    handle.update(progress=1.0, message=f"Wrote {output_path.name}")
    handle.set_result(
        {
            "output_path": str(result.output_path),
            # The deliverable's name, resolvable in either mode through
            # ``GET /api/match/exports/file/{name}`` (#755). An absolute
            # ``output_path`` means nothing to a hosted API container.
            "output_name": result.output_path.name,
            "stages_rendered": len(result.stages) - len(result.failed),
            "stages_total": len(requested),
            "skipped_stages": skipped,
            "missing_trims": _compare_missing_trims(bundles, requested),
            "failed": [
                {"stage_number": s.stage_number, "stage_name": s.stage_name, "error": s.error}
                for s in result.failed
            ],
            # What the render did *not* do (#705): a host with no usable
            # browser or a drawtext-less ffmpeg still renders, and this is
            # how the viewer learns the hold or the clock was skipped.
            "degradations": [{"summary": d.summary, "detail": d.detail} for d in result.degradations],
        }
    )

    # The upload is its own job, as for one shooter (#1000): its own
    # progress and cancel, and a failed upload never fails the render.
    # ``again=True``: the render just rewrote the sidecar.
    if req.youtube_upload and state is not None:
        run_sync(
            state.jobs.submit(
                kind="youtube_upload",
                args={
                    "slug": None,
                    "match_scope": True,
                    "match_root": str(root),
                    "filename": result.output_path.name,
                    "privacy": req.youtube_privacy,
                    "again": True,
                    "playlist": req.youtube_playlist,
                    "playlist_id": req.youtube_playlist_id,
                    "publish_at": req.youtube_publish_at.isoformat() if req.youtube_publish_at else None,
                    "notify_subscribers": req.youtube_notify_subscribers,
                },
            )
        )


def _grid_thumbnail_card(video: Path, at: float, out: Path, data: Any, look_name: str) -> bool:
    """The grid's thumbnail card over its own frame at ``at``; ``False``
    (logged) when the spot is off or the card cannot be drawn, and the
    caller grabs the plain frame."""
    if data is None:
        return False
    from ..overlay.raster import ChromiumRasterizer, RasterizerUnavailableError
    from ..thumbnail_card import ThumbnailError, grab_frame, render_thumbnail_card

    frame = out.with_name(out.stem + "-frame.png")
    try:
        grab_frame(video, at, frame, ffmpeg_binary=process_runtime().ffmpeg_binary)
        with ChromiumRasterizer() as rasterizer:
            render_thumbnail_card(frame, out, data, look=load_look(look_name), rasterizer=rasterizer)
    except (RasterizerUnavailableError, OSError, subprocess.CalledProcessError, ThumbnailError) as exc:
        logger.warning("grid thumbnail card not drawn (%s); using a frame of the video", exc)
        return False
    finally:
        frame.unlink(missing_ok=True)
    return True


def _grid_thumbnail_data(
    match: match_model.Match,
    req: CompareGridRequest,
    *,
    identities: list[ResolvedIdentity],
    brand: BrandMark | None,
    event_logo: Path | None,
) -> Any:
    """What the grid's thumbnail card says (the ``thumbnail`` logo spot):
    the match name over the shooters' names; ``None`` with the spot off."""
    if "thumbnail" not in req.logo_spots:
        return None
    from ..look.brand import brand_mark_json
    from ..thumbnail_card import ThumbnailData

    return ThumbnailData(
        title=match.name or "Match",
        lines=(" \u00b7 ".join(ident.label for ident in identities),) if identities else (),
        shooters=identities,
        brand=brand_mark_json(load_look(req.overlay_theme), brand),
        event_logo=event_logo,
    )


def _write_grid_youtube_sidecar(
    match: match_model.Match,
    req: CompareGridRequest,
    result: mp4_grid.GridRenderResult,
    plans: list[mp4_grid.GridStagePlan],
    thumbnail_data: Any = None,
) -> list[Path]:
    """Title, description with a chapter per stage, tags, a thumbnail and
    the paste text beside the grid, as one shooter's export writes them.
    A thumbnail grab that fails costs only the thumbnail. Returns the
    files written, for the hosted push."""
    output_path = result.output_path
    written: list[Path] = []
    thumbnail: Path | None = youtube_sidecar.thumbnail_path_for(output_path)
    # A frame a few seconds into the first stage: the grid in action, past
    # any slate (the chapter starts at the slate).
    stage_chapters = [c for c in result.chapters if c.title != (match.name or "Intro")] or list(
        result.chapters
    )
    first_start = stage_chapters[0].start_seconds if stage_chapters else 0.0
    slate = req.title_duration_seconds if req.stage_titles == "slate" else 0.0
    at = first_start + slate + min(3.0, (plans[0].duration_seconds / 3) if plans else 3.0)
    try:
        if not _grid_thumbnail_card(output_path, at, thumbnail, thumbnail_data, req.overlay_theme):
            youtube_sidecar.write_thumbnail(
                output_path, at, thumbnail, ffmpeg_binary=process_runtime().ffmpeg_binary
            )
        written.append(thumbnail)
    except (OSError, subprocess.CalledProcessError) as exc:
        logger.warning("grid youtube thumbnail not written: %s", exc)
        thumbnail = None
    sidecar = youtube_sidecar.build_grid_sidecar(
        match_name=match.name or "Match",
        chapters=list(result.chapters),
        description_lead=(req.description_lead or "").strip() or None,
        output_video=Path(output_path.name),
        thumbnail_path=Path(thumbnail.name) if thumbnail is not None else None,
        credits=match_export_helpers.look_credits_for(req.overlay_theme),
    )
    sidecar_path = youtube_sidecar.sidecar_path_for(output_path)
    youtube_sidecar.write_sidecar(sidecar, sidecar_path)
    paste = output_path.with_name(output_path.stem + "-youtube.txt")
    youtube_sidecar.write_paste_text(sidecar, paste)
    written.extend([sidecar_path, paste])
    return written


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
    ``SPLITSMITH_NO_MODEL_PREFETCH=1`` skips it entirely: the subprocess
    tests of the embedded entrypoint boot against an empty config dir
    and must not each start a 430 MB download that the drain then has
    to cancel (that is what made the SIGTERM test time out on CI).
    """
    if os.environ.get("SPLITSMITH_NO_MODEL_PREFETCH", "").lower() in {"1", "true", "yes", "on"}:
        return
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


async def _advance_sequential_chain(
    state: AppState, slug: str, project: MatchProject, video: StageVideo, stage_number: int
) -> None:
    """Submit detect_beep for the next stage in a sequential take.

    Called after a manual beep is set on ``stage_number`` so that
    stage ``i+1`` (the next stage in ``raw.covers_stages`` order
    with no beep and no active job) gets kicked off automatically -
    mirrors the chain-advance in ``_run_detect_beep_for_video`` for
    the auto-detected success path.

    No-op when the video is not part of a sequential take
    (``len(covers_stages) < 2`` or every covered stage has
    ``scorecard_updated_at`` - scoreboard mode derives independent
    windows and doesn't chain).
    """
    raw = project.find_raw_video(str(video.path))
    if raw is None or len(raw.covers_stages) < 2:
        return
    # Scoreboard mode: every covered stage has a scorecard timestamp.
    # Windows are independent; no sequential chain needed.
    try:
        if all(project.stage(n).scorecard_updated_at is not None for n in raw.covers_stages):
            return
    except KeyError:
        pass  # unknown stage -> sequential mode
    try:
        current_idx = raw.covers_stages.index(stage_number)
    except ValueError:
        return  # stage not in the take
    for next_n in raw.covers_stages[current_idx + 1 :]:
        try:
            next_stg = project.stage(next_n)
        except KeyError:
            continue
        next_primary = next_stg.primary()
        if next_primary is None or next_primary.beep_time is not None:
            continue
        if await state.jobs.find_active(kind="detect_beep", stage_number=next_n, shooter_slug=slug) is None:
            await state.jobs.submit(
                kind="detect_beep",
                stage_number=next_n,
                shooter_slug=slug,
                video_id=next_primary.video_id,
                args={
                    "slug": slug,
                    "stage_number": next_n,
                    "video_id": next_primary.video_id,
                },
            )
        break


def _fetch_remote_manifest(prefs: user_config.GlobalPrefs, match_id: str) -> list[dict]:
    """One short-timeout manifest GET for the status endpoint's
    remote-staleness hint. Module-level (not inline in the handler) so
    tests monkeypatch it; raises httpx errors to the caller."""
    with httpx.Client(
        base_url=prefs.hosted_base_url,
        headers={"Authorization": f"Bearer {prefs.hosted_token}"},
        timeout=5.0,
    ) as http:
        client = HostedSyncClient(http=http)
        return client.get_doc_manifest(match_id)


def _same_beep(a: float | None, b: float | None) -> bool:
    """Whether two beep times cut the same trim: equal at the precision the
    trim's params sidecar records (``audio._current_trim_params``)."""
    if a is None or b is None:
        return a is None and b is None
    return round(float(a), 4) == round(float(b), 4)


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
    # picker) when the placement's contrast clears this floor
    # (``beep_cross.MIN_CONTRAST``, #1442): the stage's shots matched over the
    # best alignment more than 50 ms away. On the 2026-10-18 corpus none of
    # the 171 pairs at or above it was placed more than 50 ms off.
    _align_confidence_floor = beep_cross.MIN_CONTRAST
    # A secondary's own detection that disagrees with a confident placement
    # from the primary by more than this is never auto-trusted (#1442).
    _align_disagree_s = 0.06

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
        if primary is None or primary.beep_time is None or primary.video_id == video.video_id:
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
            # The stage's shots, then the timer's tone (#1442): the 4 s
            # envelope landmark ``cross_align`` used was seconds off on 32 of
            # 194 corpus pairs.
            placed = beep_cross.place_beep(
                primary_audio,
                primary_sr,
                primary.beep_time,
                secondary_audio,
                secondary_sr,
            )
        except (OSError, ValueError) as exc:
            logger.info(
                "cross-align skipped for stage %d video %s: %s",
                stage.stage_number,
                video.video_id,
                exc,
            )
            return None
        if placed is None:
            return None
        return cross_align.CrossAlignResult(
            secondary_beep_time=placed.time,
            confidence=placed.contrast,
            peak_correlation=0.0,
            lag_seconds=placed.time - primary.beep_time,
            method="stage_shots",
        )

    def _derive_take_window(
        root: Path,
        proj: MatchProject,
        stage: StageEntry,
        video: StageVideo,
    ) -> tuple[tuple[float, float], str] | None:
        """Resolve the beep search window for a take-covered video.

        Returns (window, source) or None when the video is not part of a
        multi-stage take (raw is None or covers only one stage). Resolution
        order: manual > scoreboard > sequential.
        """
        raw = proj.find_raw_video(str(video.path))
        if raw is None or len(raw.covers_stages) < 2:
            return None
        if video.beep_window_source == "manual" and video.beep_window is not None:
            return video.beep_window, "manual"
        cfg = BeepWindowConfig()
        source_path = proj.resolve_video_path(root, video.path)
        duration = raw.duration_seconds
        if duration is None:
            try:
                duration = video_probe.probe(source_path, cache_dir=proj.probes_path(root)).duration
            except video_probe.ProbeError:
                duration = None
            if duration:
                raw.duration_seconds = float(duration)
        start_dt = raw.recorded_start
        if start_dt is None and duration:
            st = source_path.stat()
            birth = getattr(st, "st_birthtime", None)
            start_dt = (
                datetime.fromtimestamp(birth, tz=UTC)
                if birth is not None
                else datetime.fromtimestamp(st.st_mtime, tz=UTC) - timedelta(seconds=duration)
            )
            raw.recorded_start = start_dt
        if duration is None:
            return None
        if stage.scorecard_updated_at is not None and start_dt is not None:
            prior = beep_windows.StagePrior(
                stage_number=stage.stage_number,
                scorecard_updated_at=stage.scorecard_updated_at,
                time_seconds=stage.time_seconds,
            )
            windows = beep_windows.derive_scoreboard_windows(start_dt, duration, [prior], cfg)
            if windows:
                w = windows[0]
                return (w.start_s, w.end_s), "scoreboard"
        anchor: float | None = None
        for n in raw.covers_stages:
            if n == stage.stage_number:
                break
            try:
                sibling = proj.stage(n)
            except KeyError:
                continue
            sib_primary = next((v for v in sibling.videos if str(v.path) == str(video.path)), None)
            if sib_primary is not None and sib_primary.beep_time is not None:
                cand = sib_primary.beep_time + sibling.time_seconds + cfg.reset_margin_s
                anchor = cand if anchor is None else max(anchor, cand)
        return beep_windows.sequential_window(anchor, duration, cfg), "sequential"

    def _run_detect_beep_for_video(handle: JobHandle, slug: str, stage_number: int, video_id: str) -> None:
        """Worker: detect ``video``'s beep, save it, then queue its trim.

        Generic over role:
          - primary: detect -> (trim job) -> shot_detect.
          - secondary: detect -> (trim job); no shot_detect, the audit
            timeline is anchored to the primary's beep.

        The beep is saved before any trim work (#1680), and the trim is a
        chained ``trim`` job, skipped when the beep did not move and its
        trim is in place. A failed trim fails that job, never this one: the
        beep is valuable on its own.
        """
        handle.update(progress=0.05, message="Loading project...")
        with handle.timer.phase("load_project"):
            proj = state.shooter_project(slug)
            stg = proj.stage(stage_number)
            video = stg.find_video_by_id(video_id)
            if video is None:
                raise RuntimeError(f"video {video_id} disappeared from stage {stage_number} mid-flight")
            source = proj.resolve_video_path(state.shooter_root(slug), video.path)
        # Derive a per-stage search window for multi-stage single-take videos.
        # None for single-stage videos - behavior identical to before this change.
        take_window = _derive_take_window(state.shooter_root(slug), proj, stg, video)
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
                    window=take_window[0] if take_window is not None else None,
                )
        except beep_detect.BeepNotFoundError as exc:
            # Primary failure: fatal when no window was applied (whole-file search
            # failed - the downstream pipeline has nothing to anchor on). Windowed
            # primary failure is soft - the take overview surfaces "none" and the
            # user picks the beep on the waveform.
            if video.role == "primary" and take_window is None:
                raise
            if video.role == "primary":
                # Windowed primary soft-fail: mirror secondary field-wipes but skip
                # cross-align (no sibling audio to align against).
                logger.info(
                    "no beep in window on primary stage %d video %s: %s",
                    stage_number,
                    video.video_id,
                    exc,
                )
                video.beep_peak_amplitude = None
                video.beep_duration_ms = None
                video.beep_candidates = []
                video.beep_ranker_version = None
                video.beep_reviewed = False
                video.processed["beep"] = True
                video.beep_alignment_confidence = None
                video.beep_alignment_delta_ms = None
                handle.update(progress=0.55, message="No beep in window; pick on waveform")
                video.beep_time = None
                video.beep_source = "auto"
                video.beep_auto_detect_failed = True
                video.processed["trim"] = False
                beep = None
            else:
                # Secondary failure is soft: many non-headcam cameras (iPhone
                # tripod, RO position, AGC'd) don't capture the sustained
                # 2-5 kHz tone. Aborting the import on every one would be
                # worse UX than falling back to cross-correlation.
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
                    aligned = _try_align_secondary_to_primary(
                        state.shooter_root(slug), proj, stg, video, handle
                    )
                video.beep_peak_amplitude = None
                video.beep_duration_ms = None
                video.beep_candidates = []
                video.beep_ranker_version = None
                video.beep_reviewed = False
                video.processed["beep"] = True
                # Surface the cross-correlation confidence whenever we got one,
                # even if we don't promote the suggestion. Lets the UI tell the
                # difference between "never tried" and "tried, sub-floor result".
                video.beep_alignment_confidence = aligned.confidence if aligned is not None else None
                # Delta is only meaningful when both in-stream AND cross-align
                # produced timestamps. In-stream failed here, so wipe it.
                video.beep_alignment_delta_ms = None
                video.beep_aligned_time = (
                    aligned.secondary_beep_time
                    if aligned is not None and aligned.confidence >= _align_confidence_floor
                    else None
                )
                if aligned is not None and aligned.confidence >= _align_confidence_floor:
                    handle.update(
                        progress=0.55,
                        message=(f"Aligned to primary (conf {aligned.confidence:.2f}); verify on waveform"),
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
            video.beep_ranker_version = getattr(beep, "ranker_version", None)
            video.beep_auto_detect_failed = False
            video.beep_alignment_confidence = None
            video.beep_alignment_delta_ms = None
            video.beep_aligned_time = None
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
                    video.beep_aligned_time = check.secondary_beep_time
                    # Two answers that disagree are a question for the user,
                    # never an auto-trusted beep (#1442).
                    if abs(beep.time - check.secondary_beep_time) > _align_disagree_s:
                        video.beep_reviewed = False

        # Persist which window was used (for the audit trail and take overview).
        if take_window is not None:
            video.beep_window = take_window[0]
            video.beep_window_source = take_window[1]  # type: ignore[assignment]
        else:
            video.beep_window = None
            video.beep_window_source = None

        # Persist the beep the moment it is known (#1680): the trim is
        # minutes of 4K decode, and the beep is the user's next step. A
        # read-modify-write (``_update_project``): the user may have toggled
        # ``beep_reviewed`` on other stages while detection ran, or another
        # job of this shooter saved. Copying only our targeted fields onto
        # the current project preserves those edits.
        handle.update(progress=0.85, message="Saving beep...")
        # Whether the beep moved from the one the stored trim was cut for. The
        # merge may run again on a concurrent writer's document; the last run
        # is the one that was saved.
        moved: list[bool] = [False]

        def _apply_beep(fresh: MatchProject) -> None:
            moved[0] = False
            try:
                stg_fresh = fresh.stage(stage_number)
            except KeyError:
                stg_fresh = None
            v_fresh = stg_fresh.find_video_by_id(video_id) if stg_fresh is not None else None
            if v_fresh is not None:
                # The trim on disk was cut for the beep the project held: it
                # stays current only while that beep does.
                moved[0] = not _same_beep(v_fresh.beep_time, video.beep_time)
                if moved[0]:
                    v_fresh.processed["trim"] = False
                v_fresh.beep_time = video.beep_time
                v_fresh.beep_source = video.beep_source
                v_fresh.beep_peak_amplitude = video.beep_peak_amplitude
                v_fresh.beep_duration_ms = video.beep_duration_ms
                v_fresh.beep_confidence = video.beep_confidence
                v_fresh.beep_candidates = list(video.beep_candidates)
                v_fresh.beep_ranker_version = video.beep_ranker_version
                v_fresh.beep_reviewed = video.beep_reviewed
                v_fresh.beep_auto_detect_failed = video.beep_auto_detect_failed
                v_fresh.beep_alignment_confidence = video.beep_alignment_confidence
                v_fresh.beep_alignment_delta_ms = video.beep_alignment_delta_ms
                v_fresh.beep_aligned_time = video.beep_aligned_time
                v_fresh.beep_window = video.beep_window
                v_fresh.beep_window_source = video.beep_window_source
                v_fresh.processed["beep"] = True
                # Backfill raw video wall-clock metadata derived during this run so
                # subsequent jobs on the same take don't need to re-probe.
                raw_working = proj.find_raw_video(str(video.path))
                if raw_working is not None:
                    raw_fresh = fresh.find_raw_video(str(video.path))
                    if raw_fresh is not None:
                        if raw_working.duration_seconds is not None and raw_fresh.duration_seconds is None:
                            raw_fresh.duration_seconds = raw_working.duration_seconds
                        if raw_working.recorded_start is not None and raw_fresh.recorded_start is None:
                            raw_fresh.recorded_start = raw_working.recorded_start

        trim_current = False
        with handle.timer.phase("persist"):
            _update_project(state, slug, _apply_beep)
            # The project as saved, for the chaining below.
            fresh = state.shooter_project(slug)
            try:
                stg_fresh = fresh.stage(stage_number)
            except KeyError:
                stg_fresh = None
            v_fresh = stg_fresh.find_video_by_id(video_id) if stg_fresh is not None else None
            if v_fresh is not None:
                if moved[0]:
                    # Here and in storage: a hosted reader keys on the trim's
                    # existence and would play the old window under the new
                    # beep until the chained trim lands.
                    audio_helpers.invalidate_video_audit_trim(
                        state.shooter_root(slug), stage_number, v_fresh, project=fresh, stored=True
                    )
                # Current by the trim's own params (beep, stage time, buffers),
                # the rule the cut reuses a trim by: a stage time edited since
                # the cut does not clear ``processed.trim``.
                trim_current = (
                    not moved[0]
                    and bool(v_fresh.processed.get("trim"))
                    and audio_helpers.trim_params_current(
                        state.shooter_root(slug),
                        stage_number,
                        v_fresh,
                        stg_fresh.time_seconds,
                        project=fresh,
                        source=source,
                    )
                )
        # Then the trim, as its own job (#1680): a confirm that lands while it
        # runs adopts it (``_maybe_chain_trim`` dedupes on the trim kind, which
        # it could not do against a trim inside this job), it also cuts the
        # audit WAV, it runs behind the queue's other beep detections, and it
        # chains shot detection itself for a reviewed primary, reading the
        # flag fresh. A beep that did not move keeps its trim (#1678).
        trim_queued = False
        if v_fresh is not None and video.beep_time is not None and stg.time_seconds > 0:
            if not trim_current:
                if (
                    run_sync(
                        state.jobs.find_active(
                            kind="trim", stage_number=stage_number, shooter_slug=slug, video_id=video_id
                        )
                    )
                    is None
                ):
                    run_sync(
                        state.jobs.submit(
                            kind="trim",
                            stage_number=stage_number,
                            shooter_slug=slug,
                            video_id=video_id,
                            args={"slug": slug, "stage_number": stage_number, "video_id": video_id},
                        )
                    )
                trim_queued = True
            elif video.role == "primary" and v_fresh.beep_reviewed:
                # Shot detection is primary-only AND gated on ``beep_reviewed``
                # (#71): the flag is True because the user confirmed it or the
                # auto-trust gate cleared (#219 layer 3b). Saves the CLAP /
                # GBDT / PANN ensemble work when the beep is wrong. Layered
                # automation gate (#215): users can disable the auto-trigger
                # globally or per project. Worker callbacks run with no event
                # loop; ``run_sync`` bridges to the async JobBackend.
                resolved = automation_settings.resolve_automation(
                    project_override=fresh.automation,
                )
                if (
                    resolved.settings.shot_detect_on_beep_verified
                    and run_sync(
                        state.jobs.find_active(
                            kind="shot_detect", stage_number=stage_number, shooter_slug=slug
                        )
                    )
                    is None
                ):
                    run_sync(
                        state.jobs.submit(
                            kind="shot_detect",
                            stage_number=stage_number,
                            shooter_slug=slug,
                            args={"slug": slug, "stage_number": stage_number},
                        )
                    )
        handle.timer.set_meta(trim_queued=trim_queued)
        # Take chaining: after any windowed detect succeeds, submit detect_beep
        # for the next uncovered stage. The helper self-guards for scoreboard mode
        # (independent windows, no chain) and non-takes.
        # Only chains on success (beep_time is set); soft-fail or manual rescue
        # both go through _advance_sequential_chain via the manual-beep endpoint.
        if take_window is not None and video.beep_time is not None and v_fresh is not None:
            run_sync(_advance_sequential_chain(state, slug, fresh, v_fresh, stage_number))
        # Generate the whole-take envelope peaks for the TakeOverview waveform.
        # This runs on every take-windowed detect job; the WAV and peaks JSON
        # caches make repeated calls cheap (cache-hit path). Wrapped in
        # try/except so a peaks failure never aborts the detect job.
        if take_window is not None:
            try:
                root_for_peaks = state.shooter_root(slug)
                take_wav = audio_helpers.ensure_take_audio(
                    root_for_peaks,
                    str(video.path),
                    source,
                    project=proj,
                    ffmpeg_binary=process_runtime().ffmpeg_binary,
                )
                waveform_helpers.ensure_peaks(take_wav, 3000)
                # Push the peaks JSON to storage so the API process can serve
                # it without re-computing. No-op in local mode (no storage).
                if proj._storage is not None and proj._storage_scope is not None:
                    peaks_path = waveform_helpers.cache_path(take_wav, 3000)
                    peaks_key = f"{proj._storage_scope}/audio/{peaks_path.name}"
                    try:
                        proj._storage.write_bytes(peaks_key, peaks_path.read_bytes())
                    except Exception as push_exc:
                        logger.info("take peaks: storage push failed: %s", push_exc)
            except Exception as peaks_exc:
                logger.info("take peaks: generation failed for %s: %s", video.path, peaks_exc)
        handle.update(progress=1.0, message="Done")

    def _run_trim(
        handle: JobHandle,
        *,
        slug: str,
        stage_number: int,
        video_id: str,
        chain_shot_detect: bool = True,
        force: bool = False,
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
        ``force`` re-cuts even when the cached trim looks current.
        """
        handle.update(progress=0.1, message="Preparing trim...")
        with handle.timer.phase("load_project"):
            proj = state.shooter_project(slug)
            root = state.shooter_root(slug)
            stg = proj.stage(stage_number)
            video = stg.find_video_by_id(video_id)
            if video is None or video.beep_time is None:
                raise RuntimeError("video or beep disappeared mid-flight")
            # A web-only project's storage may already hold this trim's
            # products for the current beep (#1683): a confirm chains a trim
            # job over a cut that is still current. With no full trim in
            # storage to pull, the cut is all the job would do, so it skips
            # the source download too.
            reused = not force and audio_helpers.stored_trim_current(
                root, stage_number, video, stg.time_seconds, project=proj
            )
            source = None if reused else proj.resolve_video_path(root, video.path)
        handle.timer.set_meta(role=video.role, trim_reused=reused)
        handle.check_cancel()
        role_label = "primary" if video.role == "primary" else f"cam {video.video_id[:6]}"
        handle.update(progress=0.3, message=f"Encoding short-GOP MP4 ({role_label})...")

        def _current_beep() -> float | None:
            try:
                now = state.shooter_project(slug).stage(stage_number).find_video_by_id(video_id)
            except KeyError:
                now = None
            return now.beep_time if now is not None else None

        def _still_current() -> bool:
            # The beep the project holds now, against the one this cut is for.
            return _same_beep(_current_beep(), video.beep_time)

        def _trim_for_current_beep() -> None:
            # This job's cut is for a beep the video no longer has: drop it
            # here and in storage (trim jobs run for hosted-native matches
            # and the desktop, never a desktop mirror), and trim again for
            # the beep it has now. The beep's movers adopted this job (they
            # dedupe on the trim kind), so nobody else will. A cleared beep
            # has nothing to trim.
            logger.info("trim: stage %s %s beep moved during the job; trimming again", stage_number, video_id)
            audio_helpers.invalidate_video_audit_trim(root, stage_number, video, project=proj, stored=True)
            if _current_beep() is None:
                return
            handle.update(message="Beep moved; trimming again")
            run_sync(
                state.jobs.submit(
                    kind="trim",
                    stage_number=stage_number,
                    shooter_slug=slug,
                    video_id=video_id,
                    args={
                        "slug": slug,
                        "stage_number": stage_number,
                        "video_id": video_id,
                        "chain_shot_detect": chain_shot_detect,
                    },
                )
            )

        # A beep that moves while ffmpeg runs (an override, or a re-detection
        # now that the beep is saved before its trim, #1680) makes this a cut
        # of the wrong window. Its submitters adopted this job (they dedupe on
        # the trim kind), so it re-queues the trim for the current beep itself
        # and never publishes the stale cut.
        superseded = False
        with handle.timer.phase("trim"):
            try:
                if source is not None:
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
                        force=force,
                        still_current=_still_current,
                    )
            except audio_helpers.TrimSuperseded:
                superseded = True
        outcome: dict[str, bool] = {"reviewed": False, "moved": False}
        if not superseded:
            # The audit WAV is the trim's second product (spec 2026-10-14): the
            # audit screen reads it and never cuts it. Best-effort: a failure
            # here leaves it to a derive_audio job on first read. A reused
            # trim's WAV is already stored.
            handle.update(progress=0.75, message="Cutting audit audio...")
            with handle.timer.phase("audit_wav"):
                try:
                    if source is not None:
                        audio_helpers.ensure_video_audit_wav(
                            root,
                            stage_number,
                            video,
                            project=proj,
                            ffmpeg_binary=process_runtime().ffmpeg_binary,
                        )
                except (audio_helpers.AudioExtractionError, FileNotFoundError) as exc:
                    logger.warning("trim: audit WAV for stage %s %s not cut: %s", stage_number, video_id, exc)
            # Its products are published; on a memory-backed worker the full
            # trim would only crowd the next job.
            audio_helpers.release_local_trim(root, stage_number, video, project=proj)
            handle.update(progress=0.85, message="Saving project...")

            # Read-modify-write (``_update_project``): mark the trim on the
            # document as it is now, and read ``beep_reviewed`` from that same
            # document. A "Mark reviewed" click that lands while ffmpeg runs
            # flips the flag but ``set_beep_reviewed`` no-ops (the trim isn't
            # cached yet), so this is the only place left to honour it.
            def _apply_trim(fresh: MatchProject) -> None:
                outcome["reviewed"] = False
                outcome["moved"] = False
                try:
                    v_fresh = fresh.stage(stage_number).find_video_by_id(video_id)
                except KeyError:
                    v_fresh = None
                if v_fresh is None:
                    return
                if not _same_beep(v_fresh.beep_time, video.beep_time):
                    outcome["moved"] = True
                    return
                v_fresh.processed["trim"] = True
                outcome["reviewed"] = v_fresh.beep_reviewed

            with handle.timer.phase("persist"):
                _update_project(state, slug, _apply_trim)
            superseded = outcome["moved"]
        if superseded:
            _trim_for_current_beep()
            handle.update(progress=1.0, message="Done")
            return
        # Worker callback runs in a ThreadPoolExecutor thread with no
        # event loop; bridge to the async JobBackend via ``run_sync``.
        if (
            chain_shot_detect
            and video.role == "primary"
            and outcome["reviewed"]
            and run_sync(
                state.jobs.find_active(kind="shot_detect", stage_number=stage_number, shooter_slug=slug)
            )
            is None
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
                project_override=state.shooter_project(slug).automation,
            )
            if resolved.settings.shot_detect_on_beep_verified:
                run_sync(
                    state.jobs.submit(
                        kind="shot_detect",
                        stage_number=stage_number,
                        shooter_slug=slug,
                        args={"slug": slug, "stage_number": stage_number},
                    )
                )
        # Last step: a beep moved after the save above still found this job
        # active and adopted it, so it is this job's to trim again.
        if not _still_current():
            _trim_for_current_beep()
        handle.update(progress=1.0, message="Done")

    def _run_derive_audio(
        handle: JobHandle,
        *,
        slug: str,
        target: str,
        stage_number: int | None = None,
        video_id: str | None = None,
        storage_path: str | None = None,
    ) -> None:
        """Make a derived WAV a request asked for and found missing (spec
        2026-10-14: requests read, jobs make). ``target`` is ``audit`` (the
        stage's audit WAV, from the trim, the mirror's rendition or, with
        no trim, the primary's source), ``video`` (one video's full source
        WAV, for the beep picker) or ``take`` (a raw file's overview WAV)."""
        proj = state.shooter_project(slug)
        root = state.shooter_root(slug)
        ffmpeg = process_runtime().ffmpeg_binary
        handle.update(progress=0.2, message="Preparing audio...")
        if target == "take":
            if storage_path is None:
                raise RuntimeError("take audio needs storage_path")
            source = proj.resolve_video_path(root, Path(storage_path))
            audio_helpers.ensure_take_audio(root, storage_path, source, project=proj, ffmpeg_binary=ffmpeg)
        elif target == "video":
            if stage_number is None or video_id is None:
                raise RuntimeError("video audio needs stage_number and video_id")
            video = proj.stage(stage_number).find_video_by_id(video_id)
            if video is None:
                raise RuntimeError(f"video {video_id} is gone from stage {stage_number}")
            source = proj.resolve_video_path(root, video.path)
            audio_helpers.ensure_video_audio(
                root, stage_number, video, source, project=proj, ffmpeg_binary=ffmpeg
            )
            # Onto the project as it is now: ffmpeg ran long enough for an
            # edit (a beep confirmed elsewhere) that the snapshot above lacks.
            fresh = state.shooter_project(slug)
            fresh_video = fresh.stage(stage_number).find_video_by_id(video_id)
            if fresh_video is not None and beep_tone_check.record_tone_offset(
                root, fresh, stage_number, fresh_video
            ):
                fresh.save(root)
        elif target == "audit":
            if stage_number is None:
                raise RuntimeError("audit audio needs stage_number")
            primary = proj.stage(stage_number).primary()
            if primary is None:
                raise RuntimeError(f"stage {stage_number} has no primary video")
            # The trim's cut lock: never read a trim a re-cut is replacing
            # on this disk (review, 2026-10-14).
            trimmed = audio_helpers.trimmed_primary_path(root, stage_number, project=proj)
            if (
                not _is_mirror()
                and primary.beep_time is not None
                and proj.stage(stage_number).time_seconds > 0
                and not trimmed.exists()
                and audio_helpers.web_trim_available(proj, trimmed)
            ):
                # A hosted-native stage whose storage holds the rendition but
                # no trim (#1683): cut the trim again from the source, so the
                # WAV keeps the source's audio. Shot detection reads it, and
                # the rendition's is re-encoded (the mirror's fallback below).
                audio_helpers.ensure_video_audit_trim(
                    root,
                    stage_number,
                    primary,
                    proj.resolve_video_path(root, primary.path),
                    primary.beep_time,
                    proj.stage(stage_number).time_seconds,
                    project=proj,
                    ffmpeg_binary=ffmpeg,
                )
            with derived.locked(trimmed, "cut"):
                audio_helpers.ensure_audit_audio(
                    root,
                    stage_number,
                    lambda: proj.resolve_video_path(root, primary.path),
                    primary.beep_time,
                    project=proj,
                    ffmpeg_binary=ffmpeg,
                    web_fallback=_is_mirror(),
                )
            audio_helpers.release_local_trim(root, stage_number, primary, project=proj)
        else:
            raise RuntimeError(f"unknown audio target {target!r}")
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
            # A web-only project whose storage holds the trim's products for
            # the current beep (#1683) is detected from the published audit
            # WAV: there is no full trim in storage to pull, and cutting one
            # again from the source would cost the whole trim job. Voter E
            # (opt-in) reads the source video, so it keeps the old path.
            stored = None
            if os.environ.get("SPLITSMITH_ENABLE_VOTER_E") != "1" and audio_helpers.stored_trim_current(
                state.shooter_root(slug), stage_number, prim, stg.time_seconds, project=proj
            ):
                stored = audio_helpers.published_audit_audio(
                    state.shooter_root(slug), stage_number, prim.beep_time, project=proj
                )
            if stored is not None and not stored.trimmed:
                stored = None
            source = (
                None if stored is not None else proj.resolve_video_path(state.shooter_root(slug), prim.path)
            )
        handle.timer.set_meta(trim_reused=stored is not None)

        # Re-detect-all on an old project (and the v1->v2 cache migration in
        # #298) leaves stages with no trimmed MP4 cached. Without that file,
        # ensure_audit_audio falls back to the full source WAV -- shot
        # detection still works, but the audit page then streams the raw
        # source clip into <video>, where long-GOP 4K MOVs wedge in buffering
        # on first play. Force the trim here so every shot-detect run leaves
        # the audit cache in the same state the beep-detect path would have.
        if stored is not None:
            audit = stored
        else:
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
                # ``source`` is already resolved above (the trim needs it), so the
                # thunk is a no-op here; ensure_audit_audio only calls it on the
                # no-trim fallback (#592).
                audit = audio_helpers.ensure_audit_audio(
                    state.shooter_root(slug),
                    stage_number,
                    lambda: source,
                    prim.beep_time,
                    project=proj,
                    ffmpeg_binary=process_runtime().ffmpeg_binary,
                )
            # The WAV is what detection reads from here on.
            audio_helpers.release_local_trim(state.shooter_root(slug), stage_number, prim, project=proj)

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
                candidates.append(_candidate_dict(cand))

        handle.update(progress=0.85, message="Saving audit JSON...")

        def _default_audit_doc() -> dict[str, Any]:
            return {
                "stage_number": stg.stage_number,
                "stage_name": stg.stage_name,
                "stage_time_seconds": stg.time_seconds,
                "beep_time": round(beep_in_clip, 4),
                CLIP_VIDEO_FIELD: prim.video_id,
                "shots": [],
            }

        def _merge_detection_into(doc: dict[str, Any]) -> dict[str, Any]:
            """Fold this run's detection results into ``doc``.

            The rule is :func:`splitsmith.detection_merge.merge_detection_into`,
            shared with the MCP ``detect_shots`` tool (#1380); it is
            re-appliable against a freshly-loaded doc, which is what
            ``_save_audit_with_remerge`` relies on.
            """
            return merge_detection_into(
                doc,
                result,
                base=_default_audit_doc(),
                stage_rounds=(
                    stg.stage_rounds.model_dump(mode="json", exclude_none=True)
                    if stg.stage_rounds is not None
                    else None
                ),
                reset=reset,
            )

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
            def _apply_shots(fresh: MatchProject) -> None:
                prim_fresh = fresh.stage(stage_number).primary()
                if prim_fresh is not None:
                    prim_fresh.processed["shot_detect"] = True

            _update_project(state, slug, _apply_shots)
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
                        overlay_variant=req.overlay_variant,
                        overlay_options=req.hud_options(),
                        write_summary_card=req.write_summary_card,
                        summary_hold_seconds=req.summary_hold_seconds,
                        summary_variant=req.summary_variant,
                        breakdown=req.breakdown,
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
                    scorecard=stg.scorecard,
                    shooter_label=proj.competitor_name or proj.name,
                    stage_time_is_manual=stg.time_seconds_manual,
                    segment_cache=match_export_helpers.render_segment_cache(Config().output),
                    hud_extras=export_helpers.hud_extras_for(proj, stg, state.shooter_root(slug)),
                )
        except StageExportError as exc:
            # Surface as a job failure with the exporter's own message so
            # the JobsPanel row reads "audit JSON has no shots in shots[]"
            # rather than a stack trace.
            raise RuntimeError(str(exc)) from exc

        handle.update(progress=0.95, message="Saving project...")
        with handle.timer.phase("persist_project"):
            exported_at = datetime.now(UTC)

            def _apply_export(fresh: MatchProject) -> None:
                fresh.updated_at = exported_at

            _update_project(state, slug, _apply_export)

        # Hosted: push every produced deliverable to storage so the API
        # container can serve the download. No-op in local mode.
        with handle.timer.phase("r2_upload"):
            export_storage.push_stage_export_outputs(proj, result)

        # What the user gets to report: only the anomalies that describe
        # *this* request. Shot findings explain the audit, and a run that
        # asked for a bare trim consumed no shots -- "No shots detected in
        # the stage window" is then the designed state of a trims-only
        # export, not a problem with it, and reporting it turns every clean
        # run into a warning (#616). Anything that failed to write is
        # always relevant. ``match_trims._run_one`` makes the same cut on
        # the CLI side.
        wants_shots = req.write_csv or req.write_fcpxml or req.write_overlay or req.write_report
        reported = result.anomalies if wants_shots else result.export_failures

        # Final message summarises what shipped + flags any skipped
        # artefacts (FCPXML without a trim, etc).
        bits: list[str] = []
        if result.trimmed_video_path is not None:
            bits.append("trim")
        if result.secondary_trimmed_paths:
            n = len(result.secondary_trimmed_paths)
            bits.append(f"{n} secondary trim{'s' if n != 1 else ''}")
        if result.csv_path is not None:
            bits.append("csv")
        if result.events_csv_path is not None:
            bits.append("events")
        if result.fcpxml_path is not None:
            bits.append("fcpxml")
        if result.report_path is not None:
            bits.append("report")
        if result.overlay_path is not None:
            bits.append("overlay")

        # A run asked for a trim that produced no clip of any kind wrote
        # nothing at all -- the endpoint already 424s an unreachable source,
        # so getting here means ffmpeg itself failed. Fail the job: a red
        # row with the reason beats "Done: nothing written", which reads as
        # a no-op the user is meant to shrug at. Matches how the CLI counts
        # the same outcome (``match_trims`` returns no path -> a failure).
        if req.write_trim and result.trimmed_video_path is None and not result.secondary_trimmed_paths:
            raise RuntimeError("; ".join(result.export_failures) or "no trim was written")

        summary = ", ".join(bits) if bits else "nothing written"
        if reported:
            n = len(reported)
            word = "anomaly" if n == 1 else "anomalies"
            # Only point at report.txt when this run actually wrote one --
            # a trims-only export never does.
            where = " -- see report.txt" if result.report_path is not None else ""
            summary += f" ({n} {word}{where})"

        # Record the deliverables on the job so the SPA can offer downloads
        # without a separate overview round-trip. Basenames only: the
        # download endpoint (and the hosted storage pull) key off the name
        # within the shooter's exports/ scope. Persisted to the compute_jobs
        # row in hosted mode, so it survives the cross-process worker.
        def _name(p: Path | None) -> str | None:
            return p.name if p is not None else None

        # Durable record of this run (#629). Everything above is already
        # written and pushed, so a failure here must not fail the job --
        # ``_record_export_run`` swallows and logs. Placed after the
        # "nothing was written" raise so a failed run leaves no history.
        run_artifacts: list[export_runs.ExportArtifact] = []
        for produced, artifact_kind in (
            (result.trimmed_video_path, "trim"),
            (result.csv_path, "csv"),
            # events.csv reuses kind "csv" rather than a new ArtifactKind
            # literal: an unrecognised kind is a ``_record_export_run``
            # ValidationError (swallowed, so the run loses its whole
            # artifact record) and older desktops drop an unknown kind on
            # read -- "csv" is already a safe, understood kind for a
            # small text deliverable beside the splits CSV.
            (result.events_csv_path, "csv"),
            (result.fcpxml_path, "fcpxml"),
            (result.report_path, "report"),
            (result.overlay_path, "overlay"),
            (result.summary_card_path, "summary_card"),
        ):
            if produced is not None:
                run_artifacts.append(export_runs.ExportArtifact(filename=produced.name, kind=artifact_kind))
        # ``.values()``, not the mapping: iterating ``secondary_trimmed_paths``
        # yields video-id strings, which is the exact bug
        # ``test_export_result_reports_secondary_trim_filenames`` pins.
        run_artifacts.extend(
            export_runs.ExportArtifact(filename=p.name, kind="secondary_trim")
            for p in result.secondary_trimmed_paths.values()
        )
        _record_export_run(
            state,
            slug,
            export_runs.ExportRun(
                run_id=export_runs.new_run_id(),
                kind="stage",
                finished_at=datetime.now(UTC),
                # Wall clock for the job body. NOT any duration a result
                # object carries -- see export_runs.ExportRun's docstring.
                duration_seconds=handle.timer.build()["total_ms"] / 1000.0,
                stage_numbers=[stage_number],
                formats=export_runs.stage_run_formats(
                    trim=req.write_trim,
                    csv=req.write_csv,
                    fcpxml=req.write_fcpxml,
                    report=req.write_report,
                    overlay=req.write_overlay,
                    summary_card=req.write_summary_card,
                ),
                anomaly_count=len(reported),
                artifacts=run_artifacts,
            ),
        )

        handle.set_result(
            {
                "stage_number": stage_number,
                "trimmed_video": _name(result.trimmed_video_path),
                "csv": _name(result.csv_path),
                "events_csv": _name(result.events_csv_path),
                "fcpxml": _name(result.fcpxml_path),
                "report": _name(result.report_path),
                "overlay": _name(result.overlay_path),
                "summary_card": _name(result.summary_card_path),
                # ``secondary_trimmed_paths`` maps video_id -> Path; iterate
                # the values, not the mapping (which yields the ids).
                "secondary_trims": [p.name for p in result.secondary_trimmed_paths.values()],
                "anomalies": reported,
            }
        )
        handle.update(progress=1.0, message=f"Done: {summary}")

    def _run_match_export(handle: JobHandle, slug: str, req: MatchExportRequest) -> None:
        """Worker for the shooter's match-export job. Re-runs the per-stage exporter for
        any selected stage missing its lossless trim, then composes the
        match FCPXML.
        """
        from ..config import StageData as EngineStageData

        handle.update(progress=0.02, message="Loading project...")
        with handle.timer.phase("load_project"):
            proj = state.shooter_project(slug)
            exports_dir = proj.exports_path(state.shooter_root(slug))
            audit_dir = proj.audit_path(state.shooter_root(slug))

        n = len(req.stage_numbers)
        handle.timer.set_meta(stage_count=n)
        # The per-stage exports share the bar up to ``compose_start``, the
        # compose step the rest. An XML compose writes a file in a second;
        # an MP4 compose encodes every stage and card, the longest step of
        # the job by far, so it gets most of the bar and a line per step.
        # Stages that already have a trim skip ahead within their slice
        # instead of contributing to the wait.
        renders_mp4 = req.output_format == "mp4"
        compose_start = 0.20 if renders_mp4 else 0.92
        per_stage_share = (compose_start - 0.05) / max(1, n)

        # Stages whose overlay on disk nothing vouches for: its record did not
        # match (style or audit) and the redraw wrote none. The MOV stays on
        # disk (a transient failure never deletes a render) but is not
        # stitched; the value is the reason, surfaced as an anomaly.
        overlay_withheld: dict[int, str] = {}
        with handle.timer.phase("per_stage"):
            for idx, stage_number in enumerate(req.stage_numbers):
                handle.check_cancel()
                stg = proj.stage(stage_number)
                prim = stg.primary()
                if prim is None or prim.beep_time is None:
                    raise RuntimeError(f"stage {stage_number}: primary or beep disappeared mid-flight")

                base = stage_file_base(stage_number, stg.stage_name)
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
                # An overlay on disk is reused only when its record says it was
                # drawn the way this export asks (Look, style, options, format)
                # from the audit as it stands now (materialised above). One
                # without a record cannot say which audit it shows and is drawn
                # again.
                overlay_record = export_helpers.overlay_settings_file(exports_dir, base)
                overlay_reusable = False
                if req.include_overlay:
                    export_storage.pull_export_file(proj, overlay_record)
                    current_revision = export_helpers.overlay_audit_revision(
                        audit_dir / f"stage{stage_number}.json"
                    )
                    wanted = overlay_settings(
                        look=req.overlay_theme,
                        variant=req.overlay_variant,
                        options=req.hud_options(),
                        codec=req.overlay_codec,
                        max_height=req.overlay_max_height,
                        max_fps=req.overlay_max_fps,
                        audit_revision=current_revision,
                        # The trim was pulled above; without it the stage is
                        # cut and drawn again whatever this says.
                        upright=trimmed_path.exists() and export_helpers.overlay_canvas_upright(trimmed_path),
                        short_head=export_helpers.overlay_short_head(
                            prim.beep_time, proj.trim_pre_buffer_seconds
                        ),
                        hud_data=overlay_data_identity(
                            req.overlay_theme,
                            req.overlay_variant,
                            export_helpers.hud_extras_for(proj, stg, state.shooter_root(slug)),
                        ),
                        breakdown=req.breakdown,
                    )
                    overlay_reusable = export_helpers.overlay_record_matches(
                        export_helpers.read_overlay_settings(overlay_record),
                        audit_revision=current_revision,
                        wanted=wanted,
                    )
                    # Pull the MOV only when it would be reused.
                    if overlay_reusable:
                        export_storage.pull_export_file(proj, overlay_target)
                overlay_missing = req.include_overlay and (
                    not overlay_target.exists() or not overlay_reusable
                )

                needs_per_stage = not trimmed_path.exists() or not secondary_trims_present or overlay_missing
                if needs_per_stage:
                    handle.update(
                        progress=0.02 + idx * per_stage_share,
                        message=(f"Stage {stage_number} ({idx + 1} of {n}): running per-stage export..."),
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
                                overlay_variant=req.overlay_variant,
                                overlay_options=req.hud_options(),
                                breakdown=req.breakdown,
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
                            segment_cache=match_export_helpers.render_segment_cache(Config().output),
                            hud_extras=export_helpers.hud_extras_for(proj, stg, state.shooter_root(slug)),
                        )
                    except StageExportError as exc:
                        raise RuntimeError(f"stage {stage_number}: {exc}") from exc
                    # Hosted: a re-cut here produced the per-stage trims/overlay
                    # on this worker -- push them so other workers + the API
                    # download path see them. No-op in local mode.
                    export_storage.push_stage_export_outputs(proj, recut)
                    if overlay_missing and recut.overlay_settings_path is None and overlay_target.exists():
                        reason = next(
                            (r for r in recut.export_failures if r.startswith("overlay not written")),
                            "overlay not written",
                        )
                        overlay_withheld[stage_number] = reason
                else:
                    handle.update(
                        progress=0.02 + idx * per_stage_share,
                        message=(f"Stage {stage_number} ({idx + 1} of {n}): trim already present; skipping"),
                    )

        handle.check_cancel()
        handle.update(
            progress=compose_start,
            message="Composing the match video..." if renders_mp4 else "Writing the match timeline...",
        )

        def _render_step(step: RenderStep) -> None:
            # Between segments is where a cancel can land during a render.
            handle.check_cancel()
            verb = {"encoding": "Encoding", "reused": "Reused", "stitching": "Stitching"}[step.status]
            done = (step.index - 1) / step.total
            handle.update(
                progress=compose_start + (0.98 - compose_start) * done,
                message=f"{verb} {step.label} ({step.index} of {step.total})",
            )

        # Re-load the project: the per-stage worker writes processed flags
        # via project.save() side-effects, so a fresh load picks those up.
        with handle.timer.phase("compose"):
            proj = state.shooter_project(slug)
            # One assembler for the job and the CLI verb (#973), so the
            # two cannot drift on a file name or a beep offset.
            try:
                stages_input = match_export_helpers.stage_inputs_for_project(
                    proj, state.shooter_root(slug), req.stage_numbers
                )
            except ValueError as exc:
                raise RuntimeError(f"{exc} (disappeared mid-flight)") from exc
            stages_input = [
                (
                    replace(stage_in, overlay_path=None)
                    if stage_in.stage_number in overlay_withheld
                    else stage_in
                )
                for stage_in in stages_input
            ]

            project_name = req.project_name or proj.name or "match"
            book = shooter_book_module.load_snapshot(state.shooter_book)
            request_data = match_export_helpers.MatchExportRequestData(
                event_logo=_current_event_logo(state),
                stage_numbers=tuple(req.stage_numbers),
                head_pad_seconds=req.head_pad_seconds,
                tail_pad_seconds=req.tail_pad_seconds,
                include_secondaries=req.include_secondaries,
                include_overlay=req.include_overlay,
                project_name=project_name,
                pip_layout=req.pip_layout,
                main_camera=(
                    (proj.compare_camera or "primary") if req.main_camera == "default" else req.main_camera
                ),
                inset_camera=req.inset_camera,
                inset_corner=req.inset_corner,
                inset_size=req.inset_size,
                output_format=req.output_format,
                transition_kind=req.transition_kind,
                transition_duration_seconds=req.transition_duration_seconds,
                title_kind=req.title_kind,
                title_duration_seconds=req.title_duration_seconds,
                intro_path=Path(req.intro_path).expanduser() if req.intro_path else None,
                outro_path=Path(req.outro_path).expanduser() if req.outro_path else None,
                youtube_sidecar=req.youtube_sidecar,
                description_lead=req.description_lead,
                youtube_preset=req.youtube_preset,
                title_page=req.title_page,
                title_page_info=match_export_helpers.title_info_lines(
                    proj,
                    extra=req.title_info,
                    division=(
                        competitor_division(proj, state.shooter_root(slug)) if req.title_division else None
                    ),
                    book=book,
                ),
                title_page_duration_seconds=req.title_page_duration_seconds,
                closing_card=req.closing_card,
                made_with=req.made_with,
                look_sounds=req.look_sounds,
                grade=req.grade,
                grade_strength=req.grade_strength,
                account_brand=(
                    account_profile_module.load_brand(state.account_profile) if req.account_brand else None
                ),
                logo_spots=frozenset(req.logo_spots),
                watermark_corner="top-right" if req.overlay_position == "top-left" else "top-left",
                card_variant=req.card_variant,
                title_page_variant=req.title_page_variant,
                stage_card_variant=req.stage_card_variant,
                closing_card_variant=req.closing_card_variant,
                summary_variant=req.summary_variant,
                match_summary_variant=req.match_summary_variant,
                breakdown=req.breakdown,
                division=competitor_division(proj, state.shooter_root(slug)),
                overlay_theme=req.overlay_theme,
                summary_hold_seconds=req.summary_hold_seconds,
                match_summary=req.match_summary,
                match_summary_seconds=req.match_summary_seconds,
                shooter_label=proj.competitor_name,
                shooter_identity=resolved_identity_for(
                    proj,
                    state.shooter_root(slug),
                    look=load_look(req.overlay_theme),
                    index=0,
                    label=proj.competitor_name or project_name,
                    book=book,
                ),
            )
            try:
                result = match_export_helpers.export_match(
                    stages=stages_input,
                    request=request_data,
                    exports_dir=exports_dir,
                    config=Config().output,
                    segment_cache=match_export_helpers.render_segment_cache(Config().output),
                    progress=_render_step,
                )
                # A withheld overlay says why in place of the composer's
                # generic "overlay not available" line for that stage.
                result.anomalies[:] = [
                    *(
                        f"stage {n}: overlay left out -- the one on disk was drawn from an older "
                        f"audit or style and could not be drawn again ({why})"
                        for n, why in overlay_withheld.items()
                    ),
                    *(
                        a
                        for a in result.anomalies
                        if not any(
                            a.startswith(f"stage {n}: overlay not available") for n in overlay_withheld
                        )
                    ),
                ]
            except match_export_helpers.MatchExportError as exc:
                raise RuntimeError(str(exc)) from exc

        # The YouTube sidecar JSON is written next to the composed output as
        # "<stem>-youtube.json", not "<stem>.json" -- see the sidecar block
        # in match_exports.export_match (ui/match_exports.py, the
        # ``if request.youtube_sidecar:`` branch). ``with_suffix(".json")``
        # names a file the renderer never writes. One helper for both call
        # sites below so the push line and the record block can't drift
        # apart again (#629 review finding 1: both used to get this wrong).
        def _youtube_sidecar_json(fcpxml_path: Path) -> Path:
            return fcpxml_path.with_name(fcpxml_path.stem + "-youtube.json")

        def _youtube_paste_text(fcpxml_path: Path) -> Path:
            return fcpxml_path.with_name(fcpxml_path.stem + "-youtube.txt")

        def _youtube_thumbnail(fcpxml_path: Path) -> Path:
            return fcpxml_path.with_name(fcpxml_path.stem + "-thumbnail.jpg")

        # Hosted: push the stitched match deliverable (+ optional YouTube
        # sidecars, when youtube_sidecar wrote them) so the API can serve the
        # download. push_export_file skips any that don't exist. No-op local.
        with handle.timer.phase("r2_upload"):
            export_storage.push_export_file(proj, result.fcpxml_path)
            export_storage.push_export_file(proj, result.fcpxml_path.with_suffix(".srt"))
            export_storage.push_export_file(proj, _youtube_sidecar_json(result.fcpxml_path))
            export_storage.push_export_file(proj, _youtube_paste_text(result.fcpxml_path))
            export_storage.push_export_file(proj, _youtube_thumbnail(result.fcpxml_path))

        # Durable record of this run (#629). One run per invocation across
        # every selected stage -- that grouping is precisely what a
        # directory listing cannot reconstruct.
        match_artifacts = [
            export_runs.ExportArtifact(
                filename=result.fcpxml_path.name,
                kind="match_video" if result.fcpxml_path.suffix.lower() == ".mp4" else "fcpxml",
            )
        ]
        match_artifacts.extend(
            export_runs.ExportArtifact(filename=p.name, kind="sidecar")
            for p in (
                result.fcpxml_path.with_suffix(".srt"),
                _youtube_sidecar_json(result.fcpxml_path),
                _youtube_paste_text(result.fcpxml_path),
                _youtube_thumbnail(result.fcpxml_path),
            )
            if p.exists()
        )
        _record_export_run(
            state,
            slug,
            export_runs.ExportRun(
                run_id=export_runs.new_run_id(),
                kind="match",
                finished_at=datetime.now(UTC),
                # Wall clock. ``result.duration_seconds`` is the stitched
                # timeline's length, which is a different number entirely.
                duration_seconds=handle.timer.build()["total_ms"] / 1000.0,
                stage_numbers=list(req.stage_numbers),
                formats=export_runs.match_run_formats(
                    output_format=req.output_format,
                    youtube_sidecar=req.youtube_sidecar,
                ),
                anomaly_count=len(result.anomalies),
                artifacts=match_artifacts,
            ),
        )

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
            message=(f"Done: {result.stage_count} stages, {result.duration_seconds:.1f}s{anom_suffix}"),
        )

        # Issue #1000: the upload is its own job so it has its own
        # progress and cancel, and so a failed upload never marks a
        # finished render as failed. Same thread-to-loop bridge as
        # ``_run_trim``'s shot_detect chain. ``again=True`` because the
        # render just rewrote the sidecar: there is no earlier record.
        if req.youtube_upload and result.fcpxml_path.suffix.lower() == ".mp4":
            run_sync(
                state.jobs.submit(
                    kind="youtube_upload",
                    shooter_slug=slug,
                    args={
                        "slug": slug,
                        "filename": result.fcpxml_path.name,
                        "privacy": req.youtube_privacy,
                        "again": True,
                        "playlist": req.youtube_playlist,
                        "playlist_id": req.youtube_playlist_id,
                        "publish_at": (
                            req.youtube_publish_at.isoformat() if req.youtube_publish_at else None
                        ),
                        "notify_subscribers": req.youtube_notify_subscribers,
                    },
                )
            )

    def _run_generate_proxy(handle: JobHandle, *, raw_path: str) -> None:
        """Worker that builds a low-res scrub proxy for a raw upload.

        Hosted-only: transcodes a dense-GOP preview of the ``raw/<name>``
        object via ``transcode_proxy`` and uploads it under
        ``raw_proxy/<name>.mp4``. ffmpeg reads the source from a presigned
        URL (phase ``stream_transcode``); without one, or when that read
        reports an error, it downloads the object first (``download`` then
        ``transcode``), and ``meta.source_read`` says which ran. The proxy is a fast-
        scrub optimization for the Ingest UI, so both the no-storage
        (local mode) and already-present cases short-circuit as clean
        no-ops rather than erroring.
        """
        import shutil
        import tempfile

        from ..proxy import ProxyConfig, ProxyError, proxy_key_for, transcode_proxy

        storage = state.storage
        if storage is None:
            handle.set_result({"skipped": "no-storage"})
            handle.update(progress=1.0, message="Skipped (local mode)")
            return

        proxy_key = proxy_key_for(raw_path)
        if storage.exists(proxy_key):
            handle.set_result({"proxy_key": proxy_key, "skipped": "exists"})
            handle.update(progress=1.0, message="Proxy already present")
            return

        ffmpeg_binary = process_runtime().ffmpeg_binary
        with tempfile.TemporaryDirectory() as td:
            tmp_in = Path(td) / "src"
            tmp_out = Path(td) / "proxy.mp4"
            # #1674: ffmpeg reads the source from a presigned URL, so decoding
            # starts with the first bytes; the download path is the fallback
            # for a store without presigned URLs or a stream that went wrong.
            streamed = False
            source_url = _proxy_source_url(storage, raw_path)
            if source_url is not None:
                handle.update(progress=0.1, message="Transcoding preview...")
                try:
                    with handle.timer.phase("stream_transcode"):
                        transcode_proxy(
                            source_url,
                            tmp_out,
                            ProxyConfig(),
                            ffmpeg_binary=ffmpeg_binary,
                            runner=_cancellable_runner(handle),
                        )
                    streamed = True
                except ProxyError as exc:
                    # The message carries no URL (``transcode_proxy`` redacts it).
                    logger.warning(
                        "proxy for %s: streamed read failed, downloading instead: %s", raw_path, exc
                    )
                    tmp_out.unlink(missing_ok=True)
            # ``url``: streamed; ``url_failed``: streamed, discarded, downloaded;
            # ``download``: no URL to stream from.
            handle.timer.set_meta(
                source_read="url" if streamed else ("url_failed" if source_url is not None else "download")
            )
            if not streamed:
                handle.update(progress=0.1, message="Fetching source...")
                with handle.timer.phase("download"):
                    with storage.open_stream(raw_path) as src, tmp_in.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                handle.check_cancel()
                handle.update(progress=0.4, message="Transcoding preview...")
                with handle.timer.phase("transcode"):
                    transcode_proxy(
                        tmp_in,
                        tmp_out,
                        ProxyConfig(),
                        ffmpeg_binary=ffmpeg_binary,
                        runner=_cancellable_runner(handle),
                    )
            handle.update(progress=0.85, message="Uploading preview...")
            with handle.timer.phase("upload"):
                with tmp_out.open("rb") as fh:
                    size = storage.upload_stream(proxy_key, fh)
        handle.set_result({"proxy_key": proxy_key, "size_bytes": size})
        handle.update(progress=1.0, message="Preview ready")

    def _do_sync_match(handle: JobHandle, match_root: Path) -> Any:
        """Worker for the ``sync_match`` job (bidirectional sync, #631 +
        the pull-merge-push slice): sync the current match with the
        configured hosted server.

        Takes no args - the match root comes from ``state.match_root``,
        which resolves against the ``current_match_root`` ContextVar that
        ``JobRegistry.submit`` captured from the submitting request and
        replays on the worker thread (mirrors ``_run_match_export``).

        Builds the bearer-authed ``httpx.Client`` from the persisted
        hosted-sync prefs (the ``SsiHttpClient`` idiom) and delegates to
        ``run_sync``, which does the real work: pull hosted changes,
        three-way merge, then push (plan, adopt the mirror, upload
        changed media, upsert docs). ``SyncClientError`` (including its
        ``SyncVersionConflict`` subclass, which ``run_sync`` retries
        internally and only re-raises as a plain ``SyncClientError`` once
        exhausted) and the two httpx error classes a network hiccup or a
        bad token actually raise are caught and re-raised as
        ``RuntimeError`` with a readable message - a bare traceback in
        the jobs panel is not acceptable UX; anything else propagates
        as-is.
        """
        handle.update(progress=0.0, message="Starting sync...")
        prefs = user_config.load_global_prefs()
        if not prefs.hosted_base_url or not prefs.hosted_token:
            raise RuntimeError("hosted sync is not configured - set a base URL and token in Settings")
        http_client = httpx.Client(
            base_url=prefs.hosted_base_url,
            headers={"Authorization": f"Bearer {prefs.hosted_token}"},
            timeout=30.0,
        )
        try:
            client = HostedSyncClient(http=http_client)

            def _progress(p: float, m: str) -> None:
                # Cancellation is cooperative: a quit cancels an auto_sync
                # (embedded._RESUMABLE_JOB_KINDS), and a sync is crash-safe
                # at any progress point.
                handle.check_cancel()
                handle.update(progress=p, message=m)

            try:
                report = run_bidirectional_sync(
                    match_root,
                    client=client,
                    on_progress=_progress,
                    timer=handle.timer,
                    ffmpeg_binary=process_runtime().ffmpeg_binary,
                    audit_lock=state.audit_lock,
                )
            except SyncClientError as exc:
                raise RuntimeError(str(exc)) from exc
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(
                    f"sync failed: {exc.request.method} {exc.request.url} returned "
                    f"{exc.response.status_code} {exc.response.reason_phrase}"
                ) from exc
            except httpx.TransportError as exc:
                raise RuntimeError(f"sync failed: could not reach the hosted server ({exc})") from exc
        finally:
            http_client.close()
        return report

    def _run_sync_match(handle: JobHandle, *, auto: bool = False) -> None:
        """Worker for ``sync_match`` (manual) and ``auto_sync`` (desktop
        auto-sync, spec 2026-09-27): sync, then queue whatever pipeline
        steps the reconciler finds missing, which is how a beep confirmed on
        the phone gets trimmed and detected here. An automatic run also
        records its outcome in ``auto_sync.json`` for the SyncCard."""
        match_root = state.match_root
        # One sync of a match at a time across processes (#1076): the
        # desktop app and a CLI server share ~/.splitsmith, and the
        # registry's one-sync rule only covers its own process.
        match_lock = FileLock(match_root / MATCH_SYNC_LOCK_FILE)
        try:
            try:
                if not match_lock.acquire():
                    raise RuntimeError(
                        "another splitsmith window on this computer is syncing this match; "
                        "sync again once it finishes"
                    )
                report = _do_sync_match(handle, match_root)
            except JobCancelled:
                raise  # a quit or a cancel click, not a failure to report
            except Exception as exc:
                if auto:
                    summary = AutoRunSummary(at=datetime.now(UTC), ok=False, message=str(exc))
                    update_auto_prefs(match_root, lambda p: setattr(p, "last_auto", summary))
                raise
            message = format_sync_message(report)
            steps = _submit_reconcile_steps(state, match_root)
        finally:
            match_lock.release()
        if steps:
            message += f"; queued {len(steps)} pipeline step(s)"
        handle.set_result(report.model_dump())
        handle.update(progress=1.0, message=message)
        if auto:
            summary = AutoRunSummary(
                at=datetime.now(UTC),
                ok=True,
                message=message,
                conflicts=len(report.conflicts),
                notes=len(report.notes),
            )
            update_auto_prefs(match_root, lambda p: setattr(p, "last_auto", summary))

    state.jobs.bodies.register("model_download", _run_model_download_job)
    from . import system_api

    state.jobs.bodies.register(system_api.JOB_KIND, system_api.run_chromium_install)
    state.jobs.bodies.register("detect_beep", _run_detect_beep_for_video)
    state.jobs.bodies.register("trim", _run_trim)
    state.jobs.bodies.register("derive_audio", _run_derive_audio)
    state.jobs.bodies.register("shot_detect", _run_shot_detect)
    state.jobs.bodies.register("export", _run_export_for_stage)
    state.jobs.bodies.register("match_export", _run_match_export)
    from .highlights_api import JOB_KIND as HIGHLIGHT_REEL_KIND
    from .highlights_api import run_highlight_reel

    state.jobs.bodies.register(HIGHLIGHT_REEL_KIND, functools.partial(run_highlight_reel, state=state))

    def _run_render_upload(handle: JobHandle, slug: str, req: MatchExportRequest, command_id: str) -> None:
        """A phone's render-and-upload request (#1100): the match export
        without its chained upload job, then the upload on this job."""
        from .render_upload import run_render_upload
        from .youtube_api import run_youtube_upload

        # The job runs under the match context it was submitted with.
        match_root = current_match_root.get()
        run_render_upload(
            handle,
            render=lambda h: _run_match_export(h, slug, req.model_copy(update={"youtube_upload": False})),
            upload=lambda h, filename: run_youtube_upload(
                h,
                state=state,
                slug=slug,
                filename=filename,
                privacy=req.youtube_privacy,
                again=True,
                playlist=req.youtube_playlist,
                playlist_id=req.youtube_playlist_id,
                publish_at=req.youtube_publish_at.isoformat() if req.youtube_publish_at else None,
                notify_subscribers=req.youtube_notify_subscribers,
                command_id=command_id,
                match_root=match_root,
            ),
        )

    state.jobs.bodies.register("render_upload", _run_render_upload)
    state.jobs.bodies.register("generate_proxy", _run_generate_proxy)
    state.jobs.bodies.register("compare-grid", functools.partial(_run_compare_grid, state=state))
    from .youtube_api import run_youtube_upload

    state.jobs.bodies.register("youtube_upload", functools.partial(run_youtube_upload, state=state))
    from . import footage_sort_api

    state.jobs.bodies.register(
        footage_sort_api.JOB_KIND, functools.partial(footage_sort_api.run_footage_sort_scan, state=state)
    )
    state.jobs.bodies.register(
        footage_sort_api.FRAMES_JOB_KIND,
        functools.partial(footage_sort_api.run_footage_sort_frames, state=state),
    )
    state.jobs.bodies.register("sync_match", _run_sync_match)
    state.jobs.bodies.register("auto_sync", functools.partial(_run_sync_match, auto=True))
    if not _hosted_mode_active():
        state.jobs.add_terminal_listener(functools.partial(_record_reconcile_outcome, state))


class HealthResponse(BaseModel):
    status: str = "ok"
    # A factory, not a default: a default is written into the exported
    # OpenAPI document, which then goes stale on every release bump.
    version: str = Field(default_factory=lambda: splitsmith_version)
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


class NextStep(BaseModel):
    """What the picker's Continue card names for a match (UX PR 8).

    ``audit`` carries the first stage, in shooter then stage order, that is
    neither audited nor skipped; ``footage`` when nothing is attached yet;
    ``export`` when every stage is done. Derived from the same per-stage
    status walk that produces ``stages_audited``.
    """

    kind: Literal["details", "footage", "audit", "export"]
    shooter_slug: str | None = None
    stage_number: int | None = None
    stage_name: str | None = None


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
    # "hosted" | "desktop" | "local" (#631 Task 6): "local" is the
    # filesystem-enricher default (single-operator desktop use); hosted
    # enrichment overrides it from the tenant's ``matches_store`` row so
    # the picker can flag a desktop-synced match as a read-only mirror.
    origin: MatchOrigin = "local"
    # Desktop only: the match has been synced to hosted at least once, so
    # a copy exists there that deleting it here does not touch. The delete
    # confirm says so; hosted and desktop deletes are separate actions.
    synced: bool = False
    # The Continue card's target. ``None`` for unresolved kinds.
    next_step: NextStep | None = None


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


class ReconcileLink(BaseModel):
    """One confirmed local-shooter -> scoreboard-competitor binding."""

    slug: str
    shooter_id: int
    competitor_id: int


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


class StageStatusEntry(BaseModel):
    """One stage's lifecycle status for a single shooter, surfaced on the
    shooters listing so the match Overview can render an aggregate grid
    without fetching every shooter's full project."""

    stage_number: int
    status: StageStatus


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
    # Stages, not skipped, still without a time (no scoreboard data and
    # none typed): drives the Overview's Stage details notice.
    stages_without_time: int = 0
    # The shooter's identity (#1243): accent, logo file name, club line.
    identity: ShooterIdentity = Field(default_factory=ShooterIdentity)
    # Per-stage status for this shooter, one entry per stage in the
    # shooter's own project (same source as ``stages_audited``). The match
    # Overview pivots these across shooters into a per-stage grid.
    stage_statuses: list[StageStatusEntry] = Field(default_factory=list)


class TriageAttentionOut(BaseModel):
    """The ``needs_attention`` object as read back on a triage cell."""

    flagged: bool
    flagged_at: str | None = None
    note: str | None = None
    updated_at: str


class TriageCell(BaseModel):
    """One (shooter, stage) cell of GET /api/match/triage (slice 4)."""

    slug: str
    shooter_name: str
    stage_number: int
    stage_name: str
    status: StageStatus
    beep_confidence: float | None  # min across stage videos that have one
    anomalies: list[dict]  # report.Anomaly.model_dump() records
    needs_attention: TriageAttentionOut | None = None
    # UX PR 3: the Overview's pipeline table reads these instead of a
    # second per-stage walk. All derived from data the builder already
    # holds; the split figures use the same rule as the share card.
    video_count: int = 0
    beep_time: float | None = None
    beep_reviewed: bool = False
    shot_count: int = 0
    draw: float | None = None
    avg_split: float | None = None
    time_seconds: float = 0.0
    # The stage's confirmed beeps' distance from their tone (spec
    # 2026-10-17), the largest in magnitude; None when none is measured.
    beep_tone_offset_ms: float | None = None


class TriageResponse(BaseModel):
    """GET /api/match/triage payload (slice 4): the cross-shooter,
    cross-stage triage grid the mobile audit-triage surface renders."""

    cells: list[TriageCell]
    flagged_count: int
    beep_low_confidence_threshold: float
    # A beep this far or farther from its tone gets Overview's Check beep.
    beep_tone_flag_ms: float = 20.0


class CompareShotPoint(BaseModel):
    """One shot for a shooter on a stage (#328 timeline)."""

    shot_number: int
    time_after_beep: float  # seconds since beep (primary stage clock)
    source: Literal["detected", "manual"]
    # Interval class from the audit doc (#781). Legacy docs are healed
    # in memory on read; junk values degrade to ``None``.
    interval_class: IntervalClass | None = None


class CompareShooterRecord(BaseModel):
    """One shooter's data for a stage (#328 multi-shooter timeline)."""

    slug: str
    name: str
    # Logical ref for the stage's playable trim - ``exports/<name>`` or
    # ``trimmed/<name>``, relative to the shooter's exports/trimmed dirs
    # (local mode) or storage scope (hosted mode). Resolvable by the
    # stream route in both modes; never an absolute path.
    video_ref: str | None
    # Trim is per-shooter and starts at ``beep_offset_in_clip`` seconds
    # before the beep; SPA uses this to sync all clips to time-since-beep.
    beep_offset_in_clip: float | None
    duration_seconds: float | None
    stage_time_seconds: float | None
    shots: list[CompareShotPoint]
    # The shooter's identity (#1249): the roster draws their accent and logo.
    identity: ShooterIdentity | None = None


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
    # Secondaries are queue items too: their beep anchors that angle's trim,
    # so a wrong auto-beep needs the same review surface as a primary's.
    # Only shot detection remains primary-gated.
    role: Literal["primary", "secondary"]
    video_id: str
    video_path: str
    beep_time: float | None
    beep_confidence: float | None
    beep_reviewed: bool
    status: Literal["missing", "low_confidence", "unreviewed", "confirmed"]
    alt_candidates: list[BeepQueueAltCandidate]
    # A secondary's beep found from the primary's (#1442): a candidate of
    # its own in the review, labelled as such. ``None`` on a primary.
    aligned_time: float | None = None
    # True once the low-res proxy exists (or always, in local mode). The
    # beep review video element streams the proxy, so the SPA renders a
    # "preview generating" placeholder instead of a broken player when
    # this is False.
    proxy_ready: bool
    # True once a desktop-pushed beep review snippet (audio + peaks) is
    # available for a mirror's video. Mobile beep review (slice 3) plays
    # this snippet instead of the raw/proxy media, which mirrors never
    # have. False everywhere else - snippets are hosted-mirror only.
    snippet_ready: bool = False
    # True once a beep has been set but the trim step hasn't run against
    # it yet - the clip boundaries on disk no longer match the reviewed
    # beep. Desktop re-trims on its next sync pull; this just flags it.
    trim_stale: bool = False
    # Auto-computed cross-align suggestion for secondaries lives on the
    # shooter's other videos; the SPA fetches them lazily if needed.


class BeepQueueStageGroup(BaseModel):
    """All pending beep items for a single stage, across shooters (#326)."""

    stage_number: int
    stage_name: str
    items: list[BeepQueueItem]
    total_videos: int
    confirmed: int


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
    # Per-camera-class GBDT CV metrics recorded by the build itself
    # (precision/recall/F1 at the picked threshold + tp/fp/fn). None on
    # legacy artifacts. These are the "how did the build do" numbers the
    # Retrain/Validate pages show without needing a separate eval run.
    metrics_by_class: dict[str, dict[str, float]] | None = None
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
    # Shot times checked on this fixture's own audio (#1363), and the
    # inventory's priority and reasons (``scripts/fixture_review_inventory.py``).
    review_status: Literal["needs_review", "reviewed"] = "reviewed"
    priority: float | None = None
    reasons: list[str] = []
    # The fixture's source video when it is on this disk (the external
    # volume mounted); the review walk shows it beside the waveform.
    video_path: str | None = None
    # Decisions the review walk has saved on this fixture (Continue).
    walk_decisions: int = 0


class DevReviewConfirmBody(BaseModel):
    """Optional body for the queue's sign-off: how the fixture was checked."""

    method: str | None = Field(default=None, max_length=40)


class DevReviewQueueResponse(BaseModel):
    pending: list[DevReviewQueueItem]
    flagged: list[DevReviewQueueItem]
    done: list[DevReviewQueueItem]


# Request bodies ----------------------------------------------------------


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
    # Optional metadata the SPA can supply at attach time; backfilled
    # by the detect-beep worker from ffprobe when absent.
    duration_seconds: float | None = None
    recorded_start: AwareDatetime | None = None


class MultipartPart(BaseModel):
    """One finished part: its 1-based number + the ETag R2 returned."""

    part_number: int
    etag: str


class DesktopTokenInfo(BaseModel):
    """One desktop token as the owner-management routes return it (#631).

    Mirrors ``DesktopTokenRecord`` (db layer) field-for-field. Kept as a
    separate model here - not imported directly - because the db layer is
    hosted-only and lazily imported (see ``_apply_hosted_mode_wiring``);
    this module must stay importable without the ``[hosted]`` extra. Same
    split as ``ShareInfo`` next to ``ShareTokenStore``. Never carries the
    token hash or the raw value."""

    id: str
    name: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class HostedAccountInfo(BaseModel):
    """The linked hosted account, as the SPA renders it (#719).

    Cached in ``config.yaml`` from the device-flow poll, then refreshed
    best-effort from ``GET /api/sync/whoami`` on read (#877) -- a
    sync-scoped token still cannot reach ``/api/me``, which is why the
    refresh has its own route on the sync surface.
    """

    id: str
    email: str
    display_name: str | None = None
    device_name: str
    linked_at: datetime


class HostedSyncSettings(BaseModel):
    """Response body for GET/PUT /api/settings/hosted-sync (#631, Task 9).

    ``token_set`` is a boolean, never the raw token - the desktop client
    keeps its own copy from the moment it typed it in; the server has no
    business echoing a bearer credential back over localhost HTTP. Same
    rule applies to ``account``: it carries identity, never a credential.
    """

    base_url: str | None = None
    token_set: bool = False
    account: HostedAccountInfo | None = None


class HostedSyncSettingsRequest(BaseModel):
    """Body for PUT /api/settings/hosted-sync (#631, Task 9).

    ``token: null`` keeps whatever token is already stored (so the SPA can
    resubmit ``base_url`` alone without re-typing the secret); ``token:
    ""`` clears it; any other string replaces it.
    """

    base_url: str
    token: str | None = None


class GlobalAutoSyncRequest(BaseModel):
    """Body for PUT /api/settings/auto-sync: the machine-level switch."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool


class ScrubSettingsRequest(BaseModel):
    """Body for PUT /api/settings/scrub: the Audit full-resolution switch."""

    model_config = ConfigDict(extra="forbid")

    full_res_scrub: bool


class DeviceStartResponse(BaseModel):
    """Response for POST /api/settings/hosted-sync/device/start (#719).

    Deliberately has no ``device_code`` field: the secret stays on this
    process. The SPA only needs the code to show and the URL to open.

    ``resumed`` is True when this call did NOT start a new login: a flow
    was already in flight on this install and is still live, so the same
    ``user_code`` and links come back and ``expires_in`` counts down the
    remaining part of the original window. The SPA says so rather than
    presenting it as a fresh code.
    """

    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int
    resumed: bool = False


class DeviceStatusResponse(BaseModel):
    """Response for GET /api/settings/hosted-sync/device/status (#719).

    ``status`` is ``idle`` (no flow in progress), ``pending``, ``approved``,
    ``denied`` or ``expired``. ``denied`` and ``expired`` are distinct on
    purpose - "you declined this on splitsmith.app" and "the code ran out"
    are different problems and get different copy.
    """

    status: str
    account: HostedAccountInfo | None = None
    device_name: str | None = None


#: A desktop counts as around when it touched hosted this recently
#: (spec 2026-09-28): auto-sync polls every 60 s, 300 s when idle.
DESKTOP_AROUND_SECONDS = 300


class DeviceUnlinkResponse(BaseModel):
    """Response for DELETE /api/settings/hosted-sync/session (#719).

    ``cleared`` is always True - the local prefs are gone either way.
    ``hosted_revoked`` is tri-state: ``None`` when there was nothing to
    revoke (no linked token), ``False`` when the upstream revoke was
    attempted and could not be confirmed - which the UI turns into
    "signed out here; check your account page to be sure" - and ``True``
    when the hosted side confirmed the revoke.
    """

    cleared: bool
    hosted_revoked: bool | None


class ScanRequest(BaseModel):
    """Either ``source_dir`` (folder scan, current behaviour) or
    ``source_paths`` (explicit list of files, USB-cam workflow). Exactly one
    must be provided."""

    source_dir: str | None = None
    source_paths: list[str] | None = None
    auto_assign_primary: bool = True
    link_mode: str = "symlink"


#: Where a ``raw/<name>`` symlink stands, as the relink and link-status routes report it.
LinkStatus = Literal["ok", "broken", "missing_link", "not_a_symlink"]


class RelinkEntryResponse(BaseModel):
    video_id: str
    name: str
    link_path: str
    current_target: str | None
    current_status: LinkStatus
    candidates: list[str]
    chosen_path: str | None
    ambiguous: bool
    found: bool


class RelinkAppliedEntry(BaseModel):
    video_id: str
    name: str
    link_path: str
    previous_target: str | None
    new_target: str


class LinkStatusEntry(BaseModel):
    video_id: str
    name: str
    link_path: str
    current_target: str | None
    status: LinkStatus


class BulkCameraSetItem(BaseModel):
    """One video targeted by POST /api/shooters/{slug}/stages/camera/bulk-set."""

    stage_number: int
    video_id: str


class CalibratedCameraModel(BaseModel):
    """One row in the dropdown the SPA presents on the Ingest screen."""

    key: str
    make: str
    model: str
    amp_floor: float


class RevealRequest(BaseModel):
    """Body for POST /api/files/reveal.

    Opens the OS file manager at ``path``'s parent, selecting the file when
    the platform supports it (``open -R`` on macOS). The path must resolve
    inside the project root for safety -- we don't expose a generic shell
    out.
    """

    path: str


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
    expected_version: int | None = None
    """Audit-doc version the client read before composing this patch.

    ``shot_number`` is positional, so an insert elsewhere in the stage
    renumbers the target. When supplied, a version that no longer matches
    the stored document is refused rather than applied to whatever now sits
    at that index.

    Only meaningful on the by-number route, and only hosted. Local mode has
    no locking on plain files, so ``AppState.load_audit`` /
    ``save_audit`` hard-code version 0 on every read and write: a local
    client sends 0, the stored version is 0, and the guard never bites. The
    by-id route ignores it in effect -- an id does not renumber -- and a
    client should not send it there, since a version that has legitimately
    moved on (an Undo re-patch, say) would 409 for no reason.

    Clients read the version from the coach payload's own ``version``
    field, which every coach response carries as of the save it just made
    (#844).
    """


class StageEventIn(StageEvent):
    """One region as the events PUT accepts it: the stored ``StageEvent``
    ignores unknown keys so a newer version's doc loads, but a request body
    that carries one is a client bug and is refused."""

    model_config = ConfigDict(extra="forbid")


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
    if (
        _hosted_mode_active()
        and match.match_id is not None
        and not match_model.is_valid_match_id(match.match_id)
    ):
        # The id becomes a folder name hosted; an imported match.json can
        # carry any string.
        raise HTTPException(status_code=400, detail=f"invalid match id in {resolved}: {match.match_id!r}")
    if match.match_id:
        doc, _ = state.docstore_for(match.match_id, resolved).load_match()
        if doc is not None:
            match = match_model.Match.model_validate(doc)
    if not match.shooters:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "match_empty",
                "message": (f"Match {resolved} has no shooters yet. Add a shooter or recreate the match."),
            },
        )
    name = match.name or fallback_name
    # Record the match_id so the hosted picker/bind can resolve this match
    # through Postgres on a later open, even after the ephemeral on-disk
    # path is gone (a redeploy wiped it). Local mode stores it too; the
    # filesystem flow there just doesn't need it.
    await state.recent_projects.record_open(resolved, name, kind="match", match_id=match.match_id)
    # Local only: a match folder's .env files configure the operator's own
    # process. Hosted serves every account from one process, and the folder
    # may have arrived in an uploaded archive.
    if not _hosted_mode_active():
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


def _hosted_projects_root(user_id: str | None = None) -> Path:
    """The caller's on-disk match prefix in hosted mode.

    ``<SPLITSMITH_PROJECTS_DIR>/users/<user_id>/projects``. ``user_id``
    defaults to the request's bound tenant; the auth gate pins
    ``current_tenant`` for every authenticated hosted request before the
    handler runs, so reaching here without one means an unauthenticated
    request slipped through. Failing loud is preferable to silently
    writing to a shared / wrong prefix.
    """
    if not user_id:
        tenant = current_tenant.get()
        user_id = tenant.user_id if tenant is not None else None
    if not user_id:
        raise HTTPException(
            status_code=500,
            detail="hosted mode active but no authenticated tenant is bound",
        )
    root = Path(os.environ.get(SPLITSMITH_PROJECTS_DIR_ENV, "").strip() or SPLITSMITH_PROJECTS_DIR_DEFAULT)
    return root / "users" / user_id / "projects"


def _hosted_match_work_root(user_id: str, match_id: str) -> Path:
    """The container-side working folder for one account's match (hosted).

    ``<SPLITSMITH_PROJECTS_DIR>/users/<user_id>/matches/<match_id>``. Keyed
    by account as well as id: ``matches`` is unique per ``(user_id,
    match_id)``, so two accounts can each own a match with the same id
    (a desktop mirror's id comes from the desktop). The folder is a cache
    of storage and the state store, rebuilt on demand.
    """
    if not match_model.is_valid_match_id(match_id):
        raise ValueError(f"invalid match id: {match_id!r}")
    root = Path(os.environ.get(SPLITSMITH_PROJECTS_DIR_ENV, "").strip() or SPLITSMITH_PROJECTS_DIR_DEFAULT)
    return root / "users" / user_id / "matches" / match_id


def _confined_target(target: Path, root: Path) -> Path:
    """Return ``target`` resolved, or 400 unless it lies strictly inside ``root``.

    Both sides are resolved (symlinks followed), so a link under ``root``
    that points elsewhere is refused rather than written through.
    """
    resolved = target.resolve()
    base = root.resolve()
    if resolved == base or not resolved.is_relative_to(base):
        raise HTTPException(status_code=400, detail="match folder is outside this account's storage")
    return resolved


def _resolve_create_target(
    state: AppState,
    *,
    project_folder: str | None,
    name: str,
) -> Path:
    """Resolve the on-disk match folder for ``POST /api/match/create-*``.

    - **Local mode**: ``project_folder`` is required (the user picks
      where the match lands on their disk). 400 if missing/blank.
    - **Hosted mode**: the server always uses
      ``<SPLITSMITH_PROJECTS_DIR>/users/<user_id>/projects/<slug>/``
      (#425) and ignores any ``project_folder`` in the request: the
      layout on the container's disk is the server's, not the caller's.
      The resolved folder must stay inside that prefix.

    Duplicate-name dedupe is handled by the existing
    ``match_already_exists`` check downstream, so this function only
    produces a candidate path.
    """
    if not _hosted_mode_active():
        if project_folder and project_folder.strip():
            return Path(project_folder).expanduser()
        raise HTTPException(
            status_code=400,
            detail="project_folder is required in local mode",
        )

    tenant_root = _hosted_projects_root()
    return _confined_target(tenant_root / match_model._slugify(name), tenant_root)


def _next_step_from_statuses(
    per_shooter: list[tuple[str, dict[int, StageStatus]]],
    stage_names: dict[int, str],
    video_total: int,
    untimed: Sequence[str] = (),
) -> NextStep | None:
    """The Continue card's target from each shooter's stage statuses.

    ``per_shooter`` is in match shooter order, so the first shooter with an
    open stage wins; within a shooter the lowest stage number does.
    ``untimed`` names the shooters with a stage still lacking a time (no
    scoreboard data): before any footage, their stage details come first,
    since the shooting order is what lets Add footage place clips.
    """
    if not per_shooter:
        return None
    if video_total == 0:
        if untimed:
            return NextStep(kind="details", shooter_slug=untimed[0])
        return NextStep(kind="footage")
    # A stage that can be audited now (footage and a time, not saved yet)
    # beats one that first needs footage or scores: the card names work
    # the operator can do from the Audit page.
    for slug, statuses in per_shooter:
        for n in sorted(statuses):
            if statuses[n] in (StageStatus.ready, StageStatus.in_progress):
                return NextStep(
                    kind="audit",
                    shooter_slug=slug,
                    stage_number=n,
                    stage_name=stage_names.get(n, f"Stage {n}"),
                )
    for slug, statuses in per_shooter:
        for n in sorted(statuses):
            if statuses[n] in (StageStatus.todo, StageStatus.partial):
                return NextStep(
                    # Footage without a time: the stage details are missing.
                    kind="details" if statuses[n] == StageStatus.partial else "footage",
                    shooter_slug=slug,
                    stage_number=n,
                    stage_name=stage_names.get(n, f"Stage {n}"),
                )
    return NextStep(kind="export", shooter_slug=per_shooter[0][0])


def _stages_audited_across(per_shooter: list[tuple[str, dict[int, StageStatus]]]) -> int:
    """How many of the match's stages are finished, for the picker's progress.

    A stage counts when at least one shooter audited it and every shooter
    filmed on it (any status but ``todo``) is audited or skipped. A shooter
    with no footage on a stage is not filmed there and has no say, the same
    rule the Overview applies; a plain average of per-shooter counts over
    every shooter sank when a squadmate was added before their footage.
    """
    done = 0
    for n in {n for _, statuses in per_shooter for n in statuses}:
        filmed = [
            statuses[n]
            for _, statuses in per_shooter
            if statuses.get(n, StageStatus.todo) != StageStatus.todo
        ]
        if any(st == StageStatus.audited for st in filmed) and all(
            st in (StageStatus.audited, StageStatus.skipped) for st in filmed
        ):
            done += 1
    return done


def _nothing_left_to_do(next_step: NextStep | None) -> bool:
    """The picker's "exported" state: the status walk found no stage to audit
    or film for any shooter. Read from the walk, not from a count against the
    stage total, so a squadmate still waiting for footage keeps the match in
    progress."""
    return next_step is not None and next_step.kind == "export"


def _hosted_detail_from_docs(
    rp: user_config.RecentProject,
    match_doc: dict,
    *,
    origin: str | None,
    project_docs: dict[str, dict],
    audit_docs: dict[str, dict[int, dict]],
) -> RecentProjectDetail:
    """One picker card from docs already in hand; no I/O.

    The derivation the per-match enricher did, kept as a pure function so
    the batch loader above and any future caller derive the same card:
    counts from the match doc, names and footage from the project docs,
    the audited count averaged across shooters, ``next_step`` from the same
    per-stage status walk as ``stages_audited``. ``origin=None`` leaves the
    model's default in place (no match registry bound).
    """
    detail = RecentProjectDetail(
        path=rp.path,
        name=match_doc.get("name") or rp.name,
        last_opened_at=rp.last_opened_at,
        kind="match",
    )
    detail.match_id = rp.match_id
    if origin is not None:
        detail.origin = origin
    shooters = match_doc.get("shooters") or []
    detail.shooter_count = len(shooters)
    detail.stage_count = len(match_doc.get("stages") or [])
    detail.match_date = match_doc.get("match_date")  # JSON doc -> ISO str | None
    detail.manual = match_doc.get("scoreboard_match_id") is None

    # Every audit doc for the match, grouped by shooter, so the card's
    # audited count is accurate in hosted mode. This used to be hard-coded
    # to 0 ("not worth loading for a list view"), which meant a fully-
    # audited match still read as in-progress on the picker -- the same
    # disk-vs-state_docs blind spot the sidebar had. A shooter with no
    # audits gets an empty dict -> audited_count 0 (docs mode: every stage
    # reads "ready").
    audit_by_slug = audit_docs

    names: list[str] = []
    video_total = 0
    per_shooter: list[tuple[str, dict[int, StageStatus]]] = []
    untimed: list[str] = []
    stage_names = {
        int(s["stage_number"]): str(s.get("stage_name") or "") for s in (match_doc.get("stages") or [])
    }
    for slug in shooters:
        pdoc = project_docs.get(slug)
        if pdoc is None:
            names.append(slug)
            continue
        try:
            proj = MatchProject.model_validate(pdoc)
            names.append(proj.competitor_name or slug)
            video_total += len(proj.all_videos())
            statuses = proj.stage_statuses(audit_docs=audit_by_slug.get(slug, {}))
            per_shooter.append((slug, statuses))
            if any(s.time_seconds <= 0 and not s.skipped for s in proj.stages):
                untimed.append(slug)
            for st_entry in proj.stages:
                stage_names.setdefault(st_entry.stage_number, st_entry.stage_name)
        except Exception:  # noqa: BLE001 -- never break the listing on one bad doc
            names.append(slug)
    detail.shooter_names = names
    detail.video_count = video_total
    detail.stages_audited = _stages_audited_across(per_shooter)
    detail.next_step = _next_step_from_statuses(per_shooter, stage_names, video_total, untimed)
    # Mirror the local enricher's status derivation (minus "archived",
    # which needs an on-disk mtime we don't have here): nothing left to
    # audit or film -> exported; no footage -> awaiting_footage; otherwise
    # in_progress.
    if _nothing_left_to_do(detail.next_step):
        detail.status = "exported"
    elif video_total == 0:
        detail.status = "awaiting_footage"
    else:
        detail.status = "in_progress"
    return detail


SPLITSMITH_DATABASE_URL_ENV = "SPLITSMITH_DATABASE_URL"
# The URL a self-hosted agent receives at registration: a separate
# least-privilege role (``splitsmith_agent``) so one box's credential can be
# revoked without rotating the API's. Unset means the agent gets the API's
# own URL, which is what a single-role docker / self-host stack wants.
SPLITSMITH_AGENT_DATABASE_URL_ENV = "SPLITSMITH_AGENT_DATABASE_URL"
# Comma-separated list of email addresses granted admin access. Comparison
# is case-insensitive. Unset or empty means nobody is admin (the default).
# Parsed only in hosted mode by ``_apply_hosted_mode_wiring``; local mode
# always has an empty frozenset and no admin path is exercised.
SPLITSMITH_ADMIN_EMAILS_ENV = "SPLITSMITH_ADMIN_EMAILS"
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
# Container image used in the docker_command hint returned by POST
# /api/admin/workers.  Override via env to pin a specific tag or use a
# private registry.
SPLITSMITH_AGENT_IMAGE_ENV = "SPLITSMITH_AGENT_IMAGE"
SPLITSMITH_AGENT_IMAGE_DEFAULT = "ghcr.io/mandakan/splitsmith:latest"


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


def _youtube_worker_credentials() -> dict[str, str] | None:
    """The YouTube material a self-hosted worker needs, or ``None``."""
    from ..youtube import oauth as youtube_oauth
    from ..youtube import sealed as youtube_sealed

    client = youtube_oauth.OAuthClient.configured()
    key = youtube_sealed.token_key_from_env()
    if not client.is_configured or key is None:
        return None
    return {"client_id": client.client_id, "client_secret": client.client_secret, "token_key": key}


_VIDEO_MEDIA_TYPES = {".mp4": "video/mp4", ".m4v": "video/x-m4v", ".mov": "video/quicktime"}


def video_media_type(path: str | Path) -> str:
    """The Content-Type a ``<video>`` element plays a camera file by.

    An iPhone ``.MOV`` served as ``application/octet-stream`` (with an
    attachment disposition) plays in Chromium, which sniffs, but not in
    Safari: the beep picker showed "Preview unavailable" and no sound
    (2026-10-02). Streams also go out ``inline`` for the same reason.
    """
    return _VIDEO_MEDIA_TYPES.get(Path(path).suffix.lower(), "application/octet-stream")


#: How long this process waits between upstream refresh *attempts* for
#: the cached hosted-account snapshot (#877). The timer is on the last
#: attempt, not on the age of the stored copy, and a failed attempt
#: consumes the window too -- so a stale copy is not retried for another
#: full TTL. It is held in ``AppState``, i.e. in memory, so a restart
#: refreshes. The chip calls ``GET /api/settings/hosted-sync`` on every
#: mount, and GlobalBar plus the mobile drawer each render one, so an
#: untimed refresh would be a steady trickle of upstream calls for a
#: label.
HOSTED_ACCOUNT_REFRESH_TTL_S = 300.0

#: Connect/read budget for that refresh. Much shorter than the 30s the
#: device-flow calls use: this one sits in front of a UI paint, and its
#: failure mode is "keep showing the cached name", which costs nothing.
HOSTED_ACCOUNT_REFRESH_TIMEOUT_S = 5.0


def _build_device_client(
    base_url: str, *, token: str | None = None, timeout: float = 30.0
) -> HostedSyncClient:
    """Build a ``HostedSyncClient`` for the device-flow calls (#719).

    ``token=None`` gives the unauthenticated client the two public device
    routes need - there is no bearer to send before the flow completes.
    The one seam tests monkeypatch to script a hosted side.

    ``timeout`` defaults to the device flow's 30s. The account refresh
    (#877) passes something much shorter: it runs in front of a UI paint
    and can afford to give up.
    """
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return HostedSyncClient(http=httpx.Client(base_url=base_url, headers=headers, timeout=timeout))


#: Hosted only: the process's ``LoopEngines``, one per database URL (#1178).
#: Process-scoped together with the ``DbRunner``, whose ``on_start`` adopts
#: the runner loop into the engines object it was built with. The self-hosted
#: agent wires hosted mode once per drain in one long-lived process; a fresh
#: ``LoopEngines`` per drain would leave the runner loop un-adopted from the
#: second drain on (every job-thread ``run_sync`` on the NullPool fallback)
#: and orphan the first drain's pool. One URL per process is the real case.
_process_engines: dict[str, LoopEngines] = {}
#: The ``LoopEngines`` the running process ``DbRunner`` adopted its loop into.
_runner_engines: LoopEngines | None = None
_runner_atexit_registered = False


def _stop_process_runner() -> None:
    """``atexit``: stop the process ``DbRunner`` so its ``on_stop`` disposes
    the runner loop's pool and each pooled connection closes with a Postgres
    ``Terminate`` rather than an unexpected EOF (#1197). ``atexit`` runs after
    the interpreter has joined the non-daemon threads (uvicorn's threadpool,
    the job executor), so nothing is still using the runner."""
    runner = get_runner()
    if runner is not None:
        install_runner(None)
        runner.stop()


def _process_loop_engines(url: str, *, application_name: str | None = None) -> LoopEngines:
    """The process's ``LoopEngines`` for ``url``, created on first use, with
    the process ``DbRunner`` started (bound to it) when none is running.

    ``application_name`` applies when the engines are created; a later call
    for the same URL reuses them whatever it passes."""
    global _runner_engines, _runner_atexit_registered
    from ..db import LoopEngines

    engines = _process_engines.get(url)
    created = engines is None
    if engines is None:
        engines = LoopEngines(url, application_name=application_name)
        _process_engines[url] = engines
    runner = get_runner()
    if runner is None or not runner.is_running:
        bound = engines
        runner = DbRunner(
            on_start=lambda: bound.adopt_current_loop("runner"),
            on_close=bound.close_loop_resources,
            on_stop=bound.dispose_current_loop,
        )
        runner.start()
        install_runner(runner)
        _runner_engines = engines
        if not _runner_atexit_registered:
            atexit.register(_stop_process_runner)
            _runner_atexit_registered = True
    elif created and _runner_engines is not None and _runner_engines is not engines:
        # The runner loop stays adopted into the first URL's engines only, so
        # every sync-path query for this one runs on the NullPool fallback
        # while the boot line still says pooled (#1201). Drivers only: the
        # URLs carry passwords.
        logger.warning(
            "a second database URL (driver=%s) reuses the DbRunner bound to another URL's "
            "engines (driver=%s); its sync-path queries will not be pooled",
            engines.driver,
            _runner_engines.driver,
        )
    return engines


def _reset_process_db_state() -> None:
    """Tests only: stop and uninstall the process ``DbRunner`` (its
    ``on_stop`` disposes the runner loop's engine) and forget the cached
    ``LoopEngines``, so no test inherits another's database state."""
    global _runner_engines
    runner = get_runner()
    if runner is not None:
        install_runner(None)
        runner.stop()
    _process_engines.clear()
    _runner_engines = None


def _apply_hosted_mode_wiring(
    state: AppState, *, worker: bool = False, process_name: str | None = None
) -> None:
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
    from sqlalchemy import select

    from ..db import (
        MagicLinkAuth,
        PostgresAccountProfileStore,
        PostgresExportPresetStore,
        PostgresJobBackend,
        PostgresLookStore,
        PostgresMatchStore,
        PostgresProfileStore,
        PostgresRecentProjectsStore,
        PostgresScoreboardIdentityStore,
        PostgresShooterBookStore,
        PostgresWhatsNewStore,
        PostgresYouTubeConnectionStore,
        ProjectStateStore,
        build_email_sender,
        build_signup_policy,
        loop_sessionmaker,
        tenant_session_factory,
    )
    from ..db import User as UserRow
    from ..db.comments import CommentStore
    from ..db.desktop_commands import DesktopCommandStore
    from ..db.desktop_tokens import DesktopTokenAuth, DesktopTokenStore
    from ..db.device_auth import DeviceAuthStore
    from ..db.share_tokens import ShareTokenStore
    from ..db.share_tokens import resolve_share_token as _resolve_share_token_fn
    from ..db.workers import WorkersStore
    from ..queue import make_deferrer
    from ..worker_channel import WakeChannelRegistry
    from ..worker_trigger import (
        ENV_WAKE_CHANNEL,
        WAKE_MARKER_KEY,
        WAKE_POLL_SECONDS,
        WakeMarker,
        build_worker_dispatcher,
        make_boot_retrigger,
        make_live_workers_counter,
        make_pending_jobs_counter,
        make_worker_active_checker,
        wake_channel,
        wrap_deferrer,
    )

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

    raw_admin = os.environ.get(SPLITSMITH_ADMIN_EMAILS_ENV, "")
    state.admin_emails = frozenset(e.strip().lower() for e in raw_admin.split(",") if e.strip())
    state.access = access_config()

    # One pooled engine per long-lived loop (#1178), replacing the NullPool
    # that #423 introduced to survive asyncpg's loop binding. The app loop
    # adopts itself in ``_hosted_boot_lifespan`` (the worker's in
    # ``queue.run_worker``); every sync caller lands on the process
    # ``DbRunner`` through ``run_sync``; any other loop gets the shared
    # NullPool fallback. See ``splitsmith.db.engine.LoopEngines``.
    # The engines object is process-scoped, like the runner: see
    # ``_process_loop_engines`` for why a re-wiring must reuse it.
    # ``process_name`` is the connections' ``application_name`` prefix in
    # ``pg_stat_activity`` (#1198); the self-hosted agent passes its own.
    process_name = process_name or ("splitsmith-worker" if worker else "splitsmith-serve")
    engines = _process_loop_engines(url, application_name=process_name)
    state.db_engines = engines
    # The scheme only (never the URL: it carries the password), so a URL
    # that silently lands on the NullPool fallback is visible in the log.
    logger.info("hosted database: driver=%s pooled=%s", engines.driver, engines.pooled)
    session_factory = loop_sessionmaker(engines)

    # Auth resolves identity from ``users`` / ``sessions`` / ``magic_link_tokens``
    # -- none under RLS -- so it holds the raw (non-tenant) session factory.
    # The email transport is pluggable: console by default (docker / self-host),
    # an HTTP provider when ``SPLITSMITH_EMAIL_BACKEND`` selects one.
    email_sender = build_email_sender(os.environ.get(SPLITSMITH_EMAIL_BACKEND_ENV))
    # Signup gating (anti-spam): open by default; the hosted deploy closes
    # signups to all but an allowlist via SPLITSMITH_SIGNUPS_OPEN=false +
    # SPLITSMITH_SIGNUP_ALLOWLIST. Returning users always sign in.
    signup_policy = build_signup_policy()

    from ..db.access_requests import (
        AccessRequestStore,
        AccountAccessStore,
        normalize_email,
        normalize_note,
        valid_request_email,
    )

    state.email_sender = email_sender
    state.access_requests = AccessRequestStore(session_factory)
    state.accounts = AccountAccessStore(session_factory)

    async def _alert_admins(email: str, note: str | None, source: str) -> None:
        admin_url = f"{(state.public_base_url or '').rstrip('/')}/admin/access"
        for admin in sorted(state.admin_emails):
            try:
                await state.email_sender.send_access_request_alert(
                    to=admin, email=email, note=note, source=source, admin_url=admin_url
                )
            except Exception:
                logger.exception("access request alert to an admin failed")

    def _alert_done(task: asyncio.Task) -> None:
        state.background_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("access request alert task failed", exc_info=task.exception())

    # Intake bounds, per app (a module-level limiter would share its counts
    # across every app in the process and every test in one xdist worker),
    # in-process like the comment limiter, and shared by both writers that
    # reach a stranger's request: the form route and sign-in's blocked
    # branch. A sign-in for an existing or allowlisted account never gets
    # here, so its mail is never limited.
    #
    # - 5 an hour per client address, keyed on ``request.client.host``.
    #   The ``uvicorn.Config`` in ``run`` (this module) keeps uvicorn's
    #   defaults: ``proxy_headers=True`` with ``forwarded_allow_ips`` from
    #   ``FORWARDED_ALLOW_IPS`` (else 127.0.0.1). The hosted deploy sets
    #   ``FORWARDED_ALLOW_IPS`` on the serve service so uvicorn takes the
    #   client address from the proxy's X-Forwarded-For; without it, the
    #   host is the proxy's address and every caller shares one key. We
    #   never read X-Forwarded-For by hand: anyone can send it.
    # - 50 an hour in total: the bound that holds whatever the client
    #   address turns out to be.
    #
    # Over either limit nothing is recorded and nobody is alerted; the
    # callers answer exactly as they would otherwise.
    intake_ip_limiter = CommentRateLimiter(limit=5, window_s=3600.0)
    intake_global_limiter = CommentRateLimiter(limit=50, window_s=3600.0)

    async def _record_and_alert(
        email: str, *, client: str | None, source: str = "login", note: str | None = None
    ) -> None:
        """Record an access request; alert each admin when it is new. The
        requester is never mailed. A malformed email or a caller over the
        intake bounds records nothing. The alert runs as a background task,
        so neither the form nor sign-in waits on mail (which would also let
        response time tell a new request from a known one); a failed alert
        is logged and never fails the request. Reads ``state.email_sender``
        when the alert runs so a swapped transport (tests) applies."""
        if not valid_request_email(email):
            return
        now = time.monotonic()
        if not (
            intake_ip_limiter.allow(f"ip:{client or 'unknown'}", now=now)
            and intake_global_limiter.allow("global", now=now)
        ):
            return
        if not await state.access_requests.record(email, source=source, note=note):
            return
        # The alert names the request as stored (lower-cased email, note
        # stripped and capped), not as typed.
        task = asyncio.get_running_loop().create_task(
            _alert_admins(normalize_email(email), normalize_note(note), source)
        )
        state.background_tasks.add(task)
        task.add_done_callback(_alert_done)

    state.record_access_request = _record_and_alert

    # Composite: a magic-link session cookie (browser) or a desktop bearer
    # token (sync push, #631) either resolve to a normal tenant user, so
    # current_tenant and RLS treat them identically. The auth gate DOES
    # distinguish which backend answered, via User.token_scope (#719):
    # a sync-scoped bearer is confined to /api/sync/*. Session cookie is
    # tried first (the common case).
    state.auth = CompositeAuth(
        MagicLinkAuth(
            session_factory,
            email_sender,
            signup_policy=signup_policy,
            default_tier=state.access.default_tier,
            on_blocked=lambda email: state.record_access_request(email, client=_intake_client.get()),
        ),
        DesktopTokenAuth(session_factory),
    )

    # Anonymous resolver for the share-token public path. Uses the RAW
    # session_factory (same as auth above) - share_tokens is not RLS-scoped
    # and the call arrives before any tenant GUC is set.
    #
    # A link follows its owner's account: an owner with no features at all
    # (or a deleted one) has nothing reachable through a link, so the token
    # resolves to None and ``_share_alias`` answers its uniform 404. Any
    # feature keeps links working (``sharing`` exists for exactly that);
    # minting a new one is ``share``'s gate on the management routes.
    async def _resolve_share_token(token: str) -> ResolvedShare | None:
        resolved = await _resolve_share_token_fn(session_factory, token)
        if resolved is None:
            return None
        async with session_factory() as session:
            owner = (
                await session.execute(
                    select(UserRow.access_tier, UserRow.email, UserRow.deleted_at).where(
                        UserRow.id == resolved.owner_user_id
                    )
                )
            ).one_or_none()
        if owner is None or owner.deleted_at is not None:
            return None
        if not features_for(owner.access_tier, owner.email, state.access, state.admin_emails):
            return None
        return resolved

    state.resolve_share_token = _resolve_share_token

    # Process-level, tenant-agnostic resources shared by every
    # ``TenantContext``. The deferrer routes per-user inside ``_defer``
    # (queue name from ``user_id``); the S3 client is stateless w.r.t. the
    # tenant (only the key prefix is per-user, see ``_tenant_s3_storage``).
    # Keeps one open Procrastinate App per long-lived loop -- the same loops
    # ``engines`` adopted -- and closes it with that loop's engine (#1199).
    deferrer = make_deferrer(url, application_name=f"{process_name}-queue", loops=engines)
    # Operator-scoped worker registry over the RAW session factory (not a
    # tenant factory): one fleet shared by the operator, no user_id, no RLS.
    state.workers_store = WorkersStore(session_factory)
    # Device-flow authorizations (#719): raw session factory, same
    # rationale as workers_store above (see DeviceAuthStore's docstring).
    state.device_auth = DeviceAuthStore(session_factory)
    # Scale-to-zero worker: only the API process is the enqueuer and the only
    # process that should be able to redeploy the worker. The worker must never
    # acquire launcher capabilities - redeploying itself would be circular and
    # today is only prevented by the env vars being absent on the worker service.
    # The guard below makes that invariant explicit in code.
    s3_client, s3_bucket = _build_hosted_s3_client()
    # SPLITSMITH_WAKE_CHANNEL=r2: self-hosted boxes poll a marker in the
    # bucket instead of holding an SSE channel to serve, so a scale-to-zero
    # host (Cloud Run) is not kept awake by them (WakeMarker).
    wake_marker: WakeMarker | None = None
    wake_kind = wake_channel()
    if wake_kind == "r2" and not worker:
        if s3_client is None or s3_bucket is None:
            raise RuntimeError(f"{ENV_WAKE_CHANNEL}=r2 needs the storage bucket ({SPLITSMITH_S3_BUCKET_ENV})")
        wake_marker = WakeMarker(s3_client, s3_bucket)
        state.wake_marker = wake_marker

    on_running_cancel: Callable[[], None] | None = None
    if not worker:
        # Wakes a worker through the priority dispatcher after each defer,
        # gated on the procrastinate_workers heartbeat so a live drain is
        # never redeployed out from under its job, and re-checks for
        # stranded jobs on every boot (see splitsmith.worker_trigger).
        # Disabled via SPLITSMITH_WORKER_LAUNCHER=none - local /
        # docker-compose runs an always-on worker instead.
        state.wake_channels = WakeChannelRegistry()
        worker_launcher = build_worker_dispatcher(
            state.workers_store,
            state.wake_channels,
            worker_active=make_worker_active_checker(session_factory),
            pending_jobs=make_pending_jobs_counter(session_factory),
            wake_marker=wake_marker,
            live_workers=make_live_workers_counter(session_factory),
        )
        if worker_launcher is not None:
            deferrer = wrap_deferrer(deferrer, worker_launcher)
            state.boot_retrigger = make_boot_retrigger(session_factory, worker_launcher)
            # A cancel on a running row whose worker is dead is ended by the
            # next drain's stalled sweep; the wake starts that drain (#1677).
            # The dispatcher's heartbeat gate skips it while a worker lives.
            on_running_cancel = worker_launcher.schedule
    if not worker:
        # Connection material handed to a self-hosted worker by the register
        # endpoint. The S3 sub-dict mirrors _build_hosted_s3_client's env
        # normalization (which already failed loud above on a half-configured
        # bucket), so the box connects to the same storage the API uses.
        state.worker_credentials = {
            "database_url": os.environ.get(SPLITSMITH_AGENT_DATABASE_URL_ENV, "").strip() or url,
            "public_url": state.public_base_url,
            "s3": (
                {
                    "bucket": s3_bucket,
                    "endpoint_url": os.environ.get(SPLITSMITH_S3_ENDPOINT_URL_ENV, "").strip() or None,
                    "region": os.environ.get(SPLITSMITH_S3_REGION_ENV, "").strip() or "auto",
                    "access_key_id": os.environ.get(SPLITSMITH_S3_ACCESS_KEY_ID_ENV, "").strip(),
                    "secret_access_key": os.environ.get(SPLITSMITH_S3_SECRET_ACCESS_KEY_ENV, "").strip(),
                }
                if s3_bucket is not None
                else None
            ),
            # Issue #1000, phase 2: the upload job refreshes the account's
            # access token on the worker, so the box needs the OAuth
            # client and the key the refresh token is sealed under. None
            # until all three are set on the API; the agent then leaves
            # the variables alone and an upload job fails naming them.
            "youtube": _youtube_worker_credentials(),
            # How the box is woken. Absent (an older server) or None means
            # the SSE channel.
            "wake": (
                {"kind": "r2", "key": WAKE_MARKER_KEY, "interval_seconds": WAKE_POLL_SECONDS}
                if wake_marker is not None
                else None
            ),
        }

    def _build_tenant(user_id: str) -> TenantContext:
        # Every per-user store opens its sessions through a tenant-scoped
        # factory that sets the ``app.user_id`` GUC the RLS policies key on
        # (no-op on SQLite). ``sweep_on_boot=False``: a per-request /
        # per-job backend must never fail the in-flight jobs of the user it
        # was just built for. The shared ``state.job_bodies`` registry is
        # injected so a fresh backend resolves the same kinds without
        # re-registration.
        tenant_factory = tenant_session_factory(session_factory, user_id)

        async def _may_submit() -> bool:
            # Read the tier per submit, not per tenant build: a downgrade
            # applies to the next job, and a job body that chains another
            # job (trim -> shot_detect) is checked again at that point.
            # The raw factory: ``users`` is not under RLS.
            async with session_factory() as session:
                row = (
                    await session.execute(
                        select(UserRow.access_tier, UserRow.email, UserRow.deleted_at).where(
                            UserRow.id == user_id
                        )
                    )
                ).one_or_none()
            if row is None or row.deleted_at is not None:
                return False
            return Feature.hosted_compute in features_for(
                row.access_tier, row.email, state.access, state.admin_emails
            )

        tenant_storage = _tenant_s3_storage(s3_client, s3_bucket, user_id)
        tenant_matches = PostgresMatchStore(tenant_factory, user_id=user_id)
        tenant_state = ProjectStateStore(tenant_factory, user_id=user_id)
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
                submit_allowed=_may_submit,
                on_running_cancel=on_running_cancel,
            ),
            matches_store=tenant_matches,
            project_state=tenant_state,
            storage=tenant_storage,
            share_tokens=ShareTokenStore(tenant_factory, user_id=user_id),
            desktop_tokens=DesktopTokenStore(tenant_factory, user_id=user_id),
            comments=CommentStore(tenant_factory, user_id=user_id),
            profile=PostgresProfileStore(tenant_factory, user_id=user_id),
            export_presets=PostgresExportPresetStore(tenant_factory, user_id=user_id),
            looks=PostgresLookStore(tenant_factory, user_id=user_id),
            whats_new=PostgresWhatsNewStore(tenant_factory, user_id=user_id),
            youtube=PostgresYouTubeConnectionStore(tenant_factory, user_id=user_id),
            desktop_commands=DesktopCommandStore(tenant_factory, user_id=user_id),
            # The account's shooter book and brand (spec 2026-10-08): rows
            # under RLS, files in the tenant's own storage prefix, mirrored
            # into this container's cache by content name.
            shooter_book=PostgresShooterBookStore(
                tenant_factory,
                user_id=user_id,
                storage=tenant_storage,
                cache_dir=process_runtime().cache_dir,
                backfill_source=hosted_backfill_source(tenant_matches, tenant_state, tenant_storage),
            ),
            account_profile=PostgresAccountProfileStore(
                tenant_factory, user_id=user_id, storage=tenant_storage, cache_dir=process_runtime().cache_dir
            ),
        )

    state._build_tenant = _build_tenant
    # The process-level registry stays as the body map and as what
    # ``state.jobs`` returns when no tenant is pinned. In hosted mode a job
    # must have an owner (and that owner's account check), so the fallback
    # refuses to run one in-process.
    if isinstance(state._jobs, JobRegistry):
        state._jobs.submit_refusal = (
            "hosted mode: no tenant is pinned, so there is no account to own this job; "
            "submit through the request's or job's tenant"
        )
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
            if store is None or not match_model.is_valid_match_id(match_id):
                return None
            row = run_sync(store.get(match_id))
            if row is None:
                return None
            root = _hosted_match_work_root(row.user_id, match_id)
            root.mkdir(parents=True, exist_ok=True)
            return root

        # Not remembered: the same match id can belong to two accounts, and
        # each job resolves under its own tenant.
        state.matches = MatchRegistry(miss_resolver=_resolve_match_for_worker, remember_resolved=False)


def build_worker_state(*, process_name: str = "splitsmith-worker") -> AppState:
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
    _apply_hosted_mode_wiring(state, worker=True, process_name=process_name)
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


async def _worker_channel_events(
    store: WorkersStore,
    registry: WakeChannelRegistry,
    worker_id: str,
    *,
    disabled: bool,
    boot_retrigger: Callable[[], Awaitable[None]] | None,
    version: str | None = None,
    keepalive_seconds: float = 20.0,
) -> AsyncIterator[str]:
    """SSE frames for one worker's wake-channel connection.

    Module-level (not a closure in create_app) so tests can drive it with a
    fake store / short keepalive without streaming through the ASGI stack.

    Contract with the worker daemon (Task 8): event names are exactly
    ``wake`` / ``disabled`` / ``enabled`` / ``replaced``, data is always
    ``{}``, and a ``: ka`` comment goes out after ``keepalive_seconds`` of
    silence so proxies do not idle the connection out. When the worker's
    row is disabled at connect time, ``disabled`` is sent first so the
    daemon can park immediately instead of waiting for a wake that will
    never come.
    """
    queue = registry.connect(worker_id)
    try:
        await store.touch_seen(worker_id, version=version)
        if boot_retrigger is not None:
            # A connect often ends an offline stretch; the (internally
            # throttled) pending-jobs re-check closes the "jobs queued while
            # the box was offline" gap without waiting for a fresh enqueue.
            _keep_task(asyncio.create_task(boot_retrigger()))
        if disabled:
            yield "event: disabled\ndata: {}\n\n"
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=keepalive_seconds)
            except TimeoutError:
                yield ": ka\n\n"
                continue
            yield f"event: {event}\ndata: {{}}\n\n"
    finally:
        registry.disconnect(worker_id, queue)

        async def goodbye() -> None:
            try:
                await store.touch_seen(worker_id)
            except Exception:
                # Best-effort goodbye stamp: the client is already gone and
                # the DB may be too.
                pass

        # Its own task, shielded (#1288): a reconnecting agent ends this
        # request by cancelling it, and Starlette's cancel scope keeps
        # cancelling every await in this ``finally``. Awaited in place, the
        # stamp's pooled connection was cut mid-checkout and left to the
        # garbage collector ("non-checked-in connection ... terminated"),
        # one per reconnect. As a task it finishes and checks the
        # connection back in; the await below still ends with the request.
        await asyncio.shield(_keep_task(asyncio.create_task(goodbye())))


#: Strong references to fire-and-forget tasks: the event loop keeps only a
#: weak one, so an unreferenced task can be collected before it runs.
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _keep_task(task: asyncio.Task[Any]) -> asyncio.Task[Any]:
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


def _derive_worker_status(record: Any, connected_ids: frozenset[str]) -> str:
    """Derive the display status for a WorkerRecord.

    The disabled state is authoritative: a disabled worker that happens to
    have a live channel still shows as 'disabled' so the admin cannot
    accidentally treat it as active. Order:
    1. disabled (not enabled)
    2. cloud_run: online iff load_cloud_run_config() names the job
    3. self_hosted: online if id in connected_ids
    4. self_hosted: pending if not yet registered
    5. offline
    """
    if not record.enabled:
        return "disabled"
    if record.kind == "cloud_run":
        from ..worker_trigger import load_cloud_run_config

        return "online" if load_cloud_run_config() is not None else "offline"
    if record.id in connected_ids:
        return "online"
    if not record.registered:
        return "pending"
    return "offline"


async def _worker_liveness(state: Any, records: list[Any]) -> tuple[frozenset[str], dict[str, Any]]:
    """The ids the admin view shows online, and each box's heartbeat.

    A box on the SSE channel is online while connected. On the "r2" channel
    no box connects, so a fresh bucket heartbeat counts instead (#1672). An
    unreadable bucket leaves the heartbeats out; it never fails the page.
    """
    registry = state.wake_channels
    live = set(registry.connected_ids() if registry is not None else ())
    beats: dict[str, Any] = {}
    marker = state.wake_marker
    boxes = [r.id for r in records if r.kind == "self_hosted"]
    if marker is not None and boxes:
        try:
            beats = {i: b for i, b in (await marker.read_heartbeats(boxes)).items() if b is not None}
        except Exception:  # noqa: BLE001 - liveness is display-only here
            logger.warning("admin workers: heartbeat read failed", exc_info=True)
        live.update(i for i, beat in beats.items() if beat.fresh())
    return frozenset(live), beats


def _to_worker_view(record: Any, connected_ids: frozenset[str], beat: Any = None) -> WorkerView:
    """Convert a WorkerRecord to the admin-facing WorkerView shape.

    ``beat`` is the box's bucket heartbeat ("r2" channel): when newer than
    the row's ``last_seen_at`` it is what the view shows, with its version.
    """
    last_seen_at, version = record.last_seen_at, record.version
    if beat is not None and (last_seen_at is None or _aware_utc(last_seen_at) < beat.at):
        last_seen_at, version = beat.at, beat.version or version
    return WorkerView(
        id=record.id,
        name=record.name,
        kind=record.kind,
        enabled=record.enabled,
        priority=record.priority,
        status=_derive_worker_status(record, connected_ids),
        registered=record.registered,
        last_seen_at=last_seen_at,
        last_wake_at=record.last_wake_at,
        version=version,
        info=record.info,
    )


def _aware_utc(value: datetime) -> datetime:
    """SQLite drops tzinfo; compare stored timestamps as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _hosted_boot_lifespan(state: Any) -> Any | None:
    """Return a startup lifespan for the hosted API process, or None.

    Three boot duties, all hosted-only:

    - adopt the app's event loop in :attr:`AppState.db_engines` so it gets
      its own pooled engine (#1178), disposed when the lifespan exits (the
      process ``DbRunner`` is shared and is not stopped here);
    - seed the platform's singleton worker row (``kind='cloud_run'``) when
      the worker job is configured - a fresh deploy otherwise has an empty ``workers``
      table and the wake path is silently dead (idempotent; an existing
      row's operator-set enabled/priority is preserved);
    - fire the boot pending-jobs re-check (see ``make_boot_retrigger``).

    Returns None only when none of them applies, so callers can pass the
    result directly to FastAPI(lifespan=...) without branching.
    """
    from ..worker_trigger import platform_worker_kind

    platform_kind = platform_worker_kind()
    workers_store = state.workers_store if platform_kind is not None else None
    retrigger = state.boot_retrigger
    engines = state.db_engines
    if workers_store is None and retrigger is None and engines is None:
        return None

    @asynccontextmanager
    async def _lifespan(_app: Any) -> AsyncIterator[None]:
        app_engine = None
        if engines is not None:
            # The app loop is long-lived: give it its pooled engine (#1178),
            # before either boot duty touches the database.
            app_engine = engines.adopt_current_loop()
        try:
            if app_engine is not None:
                # A superuser / BYPASSRLS role would skip every tenant policy.
                from ..db.roles import assert_runtime_role

                await assert_runtime_role(app_engine)
            if workers_store is not None and platform_kind is not None:
                await workers_store.ensure_platform_row(platform_kind, version=splitsmith_version)
            if retrigger is not None:
                # serve scales to zero, so every cold start re-checks and a
                # stranded queue job recovers on the next visit instead of
                # waiting for the 6-hourly safety cron.
                await retrigger()
            yield
        finally:
            if engines is not None:
                await engines.dispose_current_loop()

    return _lifespan


_MATCH_WRITE_RE = re.compile(r"\A/api/matches/([^/]+)/(.*)\Z")


def _local_boot_lifespan(state: Any) -> Any | None:
    """Run desktop auto-sync (spec 2026-09-27) for the app's lifetime;
    None when there is no service (hosted, or SPLITSMITH_AUTO_SYNC=0)."""
    service = getattr(state, "auto_sync", None)
    if service is None:
        return None

    @asynccontextmanager
    async def _lifespan(_app: Any) -> AsyncIterator[None]:
        task = asyncio.create_task(service.run())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            service.close()

    return _lifespan


def _resolve_compare_trim(
    legacy: MatchProject,
    shooter_root: Path,
    stage_number: int,
    stage_name: str,
    primary: StageVideo,
    storage: Storage | None,
) -> str | None:
    """Logical ref for the stage's playable trim: ``exports/<name>`` or
    ``trimmed/<name>``, lossless export preferred. Local mode checks the
    actual dirs on disk; hosted checks object storage under the
    established key conventions. Returns None when nothing is playable.

    Hosted resolution (storage bound + a per-project scope set) is
    storage-only and never falls through to a disk check - the app
    container's disk is not authoritative for hosted trims.
    """
    base = stage_file_base(stage_number, stage_name)
    lossless_name = f"{base}_trimmed.mp4"
    cache_name = f"stage{stage_number}_cam_{primary.video_id}_trimmed.mp4"
    web_name = f"stage{stage_number}_cam_{primary.video_id}_web.mp4"

    hosted = storage is not None and storage.supports_presigned_get
    if hosted:
        scope = legacy._storage_scope
        if scope:
            # The streaming rendition (#1031) first: a Compare grid pulls
            # N of these at once, and the lossless export is source bitrate.
            if storage.exists(f"{scope}/trimmed/{web_name}"):
                return f"trimmed/{web_name}"
            if storage.exists(f"{scope}/exports/{lossless_name}"):
                return f"exports/{lossless_name}"
            if storage.exists(f"{scope}/trimmed/{cache_name}"):
                return f"trimmed/{cache_name}"
            return None

    # Local mode (or hosted storage with no scope yet, which never
    # happens in practice - state.shooter_project always binds one
    # alongside a match id): existence checks against the actual dirs,
    # which may be absolute overrides outside the shooter root.
    exports = Path(legacy.exports_dir).expanduser() if legacy.exports_dir else shooter_root / "exports"
    if not exports.is_absolute():
        exports = shooter_root / exports
    trimmed = Path(legacy.trimmed_dir).expanduser() if legacy.trimmed_dir else shooter_root / "trimmed"
    if not trimmed.is_absolute():
        trimmed = shooter_root / trimmed
    lossless = exports / lossless_name
    audit_cache = trimmed / cache_name
    if lossless.exists():
        return f"exports/{lossless_name}"
    if audit_cache.exists():
        return f"trimmed/{cache_name}"
    return None


def _stage_region_figures(doc: dict, stage_number: int) -> dict:
    """Region figures for ``stages[].figures`` (spec 2026-10-08, part 2):
    confirmed regions only (``events.confirmed_from_doc``), all four
    ``None`` when the stage has none. ``exposed_reload_s`` is the reloads'
    time no confirmed movement covered (a standing reload's whole
    duration), a number whenever there is a confirmed reload and ``None``
    without one, as ``reload_avg_s``. ``regions`` counts the confirmed
    regions of every kind, the Breakdown nav row's quiet count (#1371). No
    capacity: the capacity warning is a Coach-page hint, not a shared
    figure."""
    confirmed = events_module.confirmed_from_doc(doc, log_context=f"stage {stage_number}")
    if not confirmed:
        return {
            "moving_shots": None,
            "reloads": None,
            "reload_avg_s": None,
            "exposed_reload_s": None,
            "regions": None,
        }
    summary = events_module.stage_event_summary(events_module.shot_times_from_doc(doc), confirmed, None)
    return {
        "moving_shots": summary.moving_shots,
        "reloads": summary.reloads,
        "reload_avg_s": summary.reload_avg_s,
        "exposed_reload_s": summary.exposed_reload_s if summary.reloads else None,
        "regions": len(confirmed),
    }


def _trim_version_for(root: Path, stage_number: int, video: StageVideo, project: MatchProject) -> str | None:
    """Identity of the audit trim ``stream_video`` serves locally, or None.

    The SPA puts it in a pinned ``kind=trim`` URL. A re-cut deletes the
    trim before encoding the new one, so a player that seeks during the
    encode gets a 404 and stays in error; the new trim's version is a new
    URL, which remounts the player when the page refetches the project
    after the job. Same resolver as the byte server, so the version always
    names the bytes behind the URL. Local files only: no storage pull.
    """
    # The resolver checks exists() then stat()s; a re-cut can delete the
    # trim in between, which is the very window this version is for.
    try:
        trim = audio_helpers.resolve_trim_for_read(root, stage_number, video, project=project)
        if trim is None:
            return None
        st = trim.stat()
    except OSError:
        return None
    return f"{st.st_mtime_ns:x}-{st.st_size:x}"


def _storage_fact(obj: StorageObject | None) -> tuple[int, float] | None:
    """``(size, mtime seconds)`` of a storage object for
    :func:`audio.fresh_rendition`; ``None`` when absent or undated."""
    if obj is None or obj.last_modified is None:
        return None
    return (obj.size, obj.last_modified.timestamp())


def _scrub_version_for(root: Path, stage_number: int, video: StageVideo, project: MatchProject) -> str | None:
    """Identity of the trim's fresh 720p rendition on local disk, or None (#1192).

    The Audit players ask for ``kind=scrub`` with this in the URL when it is
    set, and keep ``kind=trim`` otherwise. Same trim resolver as
    :func:`_trim_version_for` and the same freshness rule as
    ``stream_video``'s local ``kind=web`` branch
    (:func:`audio.fresh_web_trim`), so the version names the bytes behind
    the URL. Local files only: no storage call; a storage-backed project
    goes through :func:`_hosted_scrub_version_for`.
    """
    try:
        trim = audio_helpers.resolve_trim_for_read(root, stage_number, video, project=project)
        if trim is None:
            return None
        web = audio_helpers.fresh_web_trim(trim)
        if web is None:
            return None
        st = web.stat()
    except OSError:
        return None
    return f"{st.st_mtime_ns:x}-{st.st_size:x}"


def _frame_rate_for(root: Path, stage_number: int, video: StageVideo, project: MatchProject) -> float | None:
    """The local audit trim's frame rate from its own boxes (``media_check``,
    cached per file version), or None. The rendition is cut from the trim at
    the same rate. Local files only, like :func:`_trim_version_for`: a trim
    that is not on this disk (hosted, a mirror) has none and the SPA uses 30.
    """
    try:
        trim = audio_helpers.resolve_trim_for_read(root, stage_number, video, project=project)
    except OSError:
        return None
    if trim is None:
        return None
    return media_check_module.valid_mp4_cached(trim).frame_rate


def _hosted_scrub_version_for(
    presence: StoragePresence, root: Path, stage_number: int, video: StageVideo, project: MatchProject
) -> str | None:
    """``scrub_version`` on a storage-backed project (#1209): the fresh
    rendition from the request's presence listing, same rule as the
    ``kind=scrub`` route. ``None`` when stale, absent or the listing failed
    (the payload never fails on it)."""
    local_mp4 = audio_helpers.trimmed_video_path(root, stage_number, video, project=project)
    trim_key = audio_helpers._storage_trim_key(project, local_mp4)
    web_key = audio_helpers._storage_trim_key(project, trim_module.web_trim_path(local_mp4))
    if trim_key is None or web_key is None:
        return None
    try:
        trim_obj = presence.object(trim_key)
        web_obj = presence.object(web_key)
    except OSError:
        return None
    if web_obj is None or web_obj.last_modified is None:
        return None
    if not audio_helpers.fresh_rendition(
        _storage_fact(trim_obj),
        _storage_fact(web_obj),
        trim_required=not audio_helpers.stores_web_only(project),
    ):
        return None
    return f"{int(web_obj.last_modified.timestamp() * 1e9):x}-{web_obj.size:x}"


def _proxy_ready_for(storage: Storage | None, proxy_keys: set[str], path_str: str) -> bool:
    """One honest answer for every endpoint (#821). Local mode streams
    the source directly (ready). Hosted: only ``raw/`` uploads ever get
    a proxy object; a desktop-pushed mirror path has nothing to stream,
    and saying otherwise mounts a player the server errors on."""
    from ..proxy import proxy_key_for

    if storage is None:
        return True
    if not path_str.startswith("raw/"):
        return False
    return proxy_key_for(path_str) in proxy_keys


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


def _club_for(match_data: object, competitor_id: int | None) -> str | None:
    """The scoreboard club of ``competitor_id`` in ``match_data``, if any."""
    if competitor_id is None:
        return None
    for competitor in getattr(match_data, "competitors", None) or []:
        if competitor.id == competitor_id:
            return competitor.club
    return None


def _current_event_logo(state: AppState) -> Path | None:
    """The bound match's event logo on this disk, for a render (the
    branding work); ``None`` without a match, a logo, or the file, which
    is a card without the corner mark, never a failed render."""
    try:
        match_root = state.match_root
        match = state.match()
    except HTTPException:
        return None
    return ensure_local_event_logo(match.branding, match_root, storage=state.storage, match_id=match.match_id)


# ---------------------------------------------------------------------------
# Handler helpers. These were closures inside create_app; they now take the
# AppState explicitly so routers outside this module can call them.
# ---------------------------------------------------------------------------


def _set_session_cookie(state: AppState, response: Response, secret: str, expires_at: datetime) -> None:
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


def _intake_cors(request: Request, response: Response) -> Response:
    origin = request.headers.get("origin")
    if origin and origin in _intake_origins():
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "content-type"
        response.headers["Vary"] = "Origin"
    return response


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
    # Anonymous since ssi-scoreboard#554 (#1016): construction never
    # fails on a missing token; a 401 mid-request still routes through
    # ``_raise_scoreboard_http``.
    http = SsiHttpClient()
    cache_dir = root / "scoreboard" / "cache"
    cached = CachingScoreboardClient(http, cache_dir)
    return _ScoreboardClientCtx(cached, owns_close=True, inner_http=http)


def _register_response(state: AppState, root: Path, name: str, match_id: str | None) -> HealthResponse:
    """Build a HealthResponse for a freshly-registered match.

    Tier 1 step 4 of doc 10 retired the bound-state concept on
    the server, but the picker / create-match flow still need a
    way to learn the resolved ``match_id`` + default shooter so
    the SPA can navigate to ``/match/<match_id>``. That's what
    this response is for; the live ``/api/health`` route never
    returns these fields anymore.
    """
    # The authoritative roster through the port: hosted that is Postgres
    # (the on-disk match.json is the stale creation shell), locally the
    # file.
    if match_id is not None:
        doc, _ = state.docstore_for(match_id, root).load_match()
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


def _workers_not_found() -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "not found"})


def _require_storage(state: AppState) -> Storage:
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


def _raw_video_owners(state: AppState) -> dict[str, list[str]]:
    """Map each claimed ``storage_path`` to the shooters holding it.

    The single source of truth for hosted raw-object ownership (#562).
    On desktop a raw video is a file under one shooter's project dir,
    so the filesystem enforced one owner for free; the hosted port put
    the bytes in a shared per-user ``raw/`` pool and kept attachment as
    a manifest entry, losing that invariant. The settled model is
    option C -- shared pool, attachment is an explicit single-owner
    claim, and ``move-shooter`` is the only thing that reassigns it.

    The list values are almost always 0 or 1 slugs. They are lists
    rather than a single slug because matches predating the claim
    guard can genuinely hold one object on two shooters -- the prod
    failure #562 was filed about -- and collapsing that to one owner
    here would let the caller read "I am *an* owner" as permission and
    silently normalise the corruption.

    Slugs follow ``match.shooters`` order, so the first entry is the
    stable first claimant. Best-effort by design: outside a match
    context (legacy single-shooter layouts, bare/legacy calls) there
    are no siblings to conflict with, and the map is empty.
    """
    try:
        _, match = _resolve_match_context(state)
    except HTTPException:
        return {}
    owners: dict[str, list[str]] = {}
    for sh_slug in match.shooters:
        for rv in state.shooter_project(sh_slug).raw_videos:
            owners.setdefault(rv.storage_path, []).append(sh_slug)
    return owners


def _queue_take_detects(state: AppState, slug: str, project: MatchProject, created: list[StageVideo]) -> None:
    """Enqueue detect_beep for newly created take StageVideos.

    Mode selection (keyed off ``rv.covers_stages`` and per-stage
    ``scorecard_updated_at``):

    - Scoreboard-mode (every covered stage has ``scorecard_updated_at``):
      one detect_beep per created video - the window derivation is
      independent for each so they can all run in parallel.
    - Sequential-mode (at least one covered stage lacks
      ``scorecard_updated_at``): one initial detect_beep for the
      first unprocessed covered stage in ``covers_stages`` order.
      The detect job chains to the next stage after success (Task 5
      / ``_run_detect_beep_for_video``).

    Skips videos already in ``processed["beep"]`` or with a manual
    beep. Uses ``run_sync`` because sync FastAPI endpoints run
    in a threadpool with no active event loop. Does NOT call
    ``resolve_video_path`` - in hosted mode that would mirror a
    multi-GB object into the API container; reachability is the
    worker's problem.
    """
    if not created:
        return
    storage_path = str(created[0].path)
    raw = project.find_raw_video(storage_path)
    if raw is None:
        return

    def _stage_has_scorecard(n: int) -> bool:
        try:
            return project.stage(n).scorecard_updated_at is not None
        except KeyError:
            return False

    is_scoreboard = len(raw.covers_stages) >= 2 and all(_stage_has_scorecard(n) for n in raw.covers_stages)

    # Skip enqueue when the database URL is not Postgres. This covers two
    # cases: (a) SQLite-backed hosted mode in smoke/CI tests, where
    # Procrastinate's DSN converter would raise ValueError on the first
    # submit call; (b) local-mode (no SPLITSMITH_DATABASE_URL), which has
    # no Procrastinate wiring at all.
    #
    # We gate explicitly on the URL rather than catching ValueError because
    # the submit path writes a compute_jobs row before calling the deferrer;
    # a bare except ValueError would swallow a real deferrer error in
    # Postgres mode after that row is committed, leaving a PENDING job that
    # never runs. The predicate matches _hosted_mode_active() + Postgres so
    # that Postgres-mode errors propagate like _submit_detect_beep does.
    if _hosted_mode_active():
        db_url = os.environ.get(SPLITSMITH_DATABASE_URL_ENV, "")
        if not db_url.startswith("postgresql"):
            logger.debug("_queue_take_detects: skipping enqueue (SQLite URL)")
            return

    if is_scoreboard or len(raw.covers_stages) < 2:
        # One detect_beep per created video.
        for sv in created:
            if sv.processed.get("beep") or sv.beep_source == "manual":
                continue
            existing = run_sync(
                state.jobs.find_active(
                    kind="detect_beep",
                    stage_number=sv.stage_number,
                    shooter_slug=slug,
                    video_id=sv.video_id,
                )
            )
            if existing is None:
                try:
                    run_sync(
                        state.jobs.submit(
                            kind="detect_beep",
                            stage_number=sv.stage_number,
                            shooter_slug=slug,
                            video_id=sv.video_id,
                            args={
                                "slug": slug,
                                "stage_number": sv.stage_number,
                                "video_id": sv.video_id,
                            },
                        )
                    )
                except FeatureRequiredError as exc:
                    # The account answer is the same for every video.
                    _log_chain_skipped("detect_beep", slug, sv.stage_number, exc)
                    return
    else:
        # Sequential-mode: submit only the first unprocessed covered stage.
        for n in raw.covers_stages:
            try:
                stg = project.stage(n)
            except KeyError:
                continue
            primary = stg.primary()
            if primary is None:
                continue
            if primary.processed.get("beep") or primary.beep_source == "manual":
                continue
            if primary.beep_time is not None:
                continue
            existing = run_sync(
                state.jobs.find_active(
                    kind="detect_beep",
                    stage_number=n,
                    shooter_slug=slug,
                    video_id=primary.video_id,
                )
            )
            if existing is not None:
                break  # chain already started
            try:
                run_sync(
                    state.jobs.submit(
                        kind="detect_beep",
                        stage_number=n,
                        shooter_slug=slug,
                        video_id=primary.video_id,
                        args={"slug": slug, "stage_number": n, "video_id": primary.video_id},
                    )
                )
            except FeatureRequiredError as exc:
                _log_chain_skipped("detect_beep", slug, n, exc)
            break


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
            project.merge_competitor_division(match_data)
        except ScoreboardError:
            pass
    merged = project.merge_stage_times(results)
    project.save(root)
    return merged


def _resolve_stage_video(
    state: AppState, slug: str, stage_number: int, video_id: str
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


async def _submit_detect_beep(
    state: AppState, slug: str, stage_number: int, video: StageVideo
) -> JSONResponse:
    """Validate + dedupe + queue a detect-beep job for ``video``.

    Shared by the per-video endpoint and the primary-only legacy
    endpoint so both honour the same reachability + manual-override
    pre-flight checks.
    """
    existing = await state.jobs.find_active(
        kind="detect_beep", stage_number=stage_number, shooter_slug=slug, video_id=video.video_id
    )
    if existing is not None:
        return JSONResponse(existing.model_dump(mode="json"))
    job = await state.jobs.submit(
        kind="detect_beep",
        stage_number=stage_number,
        shooter_slug=slug,
        video_id=video.video_id,
        args={"slug": slug, "stage_number": stage_number, "video_id": video.video_id},
    )
    return JSONResponse(job.model_dump(mode="json"))


def _apply_beep_override(
    state: AppState,
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
        video.beep_ranker_version = None
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
        video.beep_ranker_version = None
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


async def _maybe_chain_trim(state: AppState, slug: str, stage: StageEntry, video: StageVideo) -> None:
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
            kind="trim", stage_number=stage.stage_number, shooter_slug=slug, video_id=video.video_id
        )
        is not None
    ):
        return
    try:
        await state.jobs.submit(
            kind="trim",
            stage_number=stage.stage_number,
            shooter_slug=slug,
            video_id=video.video_id,
            args={
                "slug": slug,
                "stage_number": stage.stage_number,
                "video_id": video.video_id,
            },
        )
    except FeatureRequiredError as exc:
        _log_chain_skipped("trim", slug, stage.stage_number, exc)


def _log_chain_skipped(kind: str, slug: str, stage_number: int, exc: FeatureRequiredError) -> None:
    """A job chained after a committed write was refused by the account backstop.

    The write the request made stands and the route answers with its
    normal success; the chained job is simply not queued. Only the
    API-side chains catch this: a job body chaining on the worker
    lets the refusal fail the job.
    """
    logger.info(
        "%s for %s stage %d not queued: account lacks %s", kind, slug, stage_number, exc.feature.value
    )


def _check_beep_time(video: StageVideo, beep_time: float) -> None:
    """A beep before its clip is allowed for a secondary camera only, and
    only a little before (2026-10-15): one that started recording during
    the beep. A primary's shots are measured from its beep, which must
    be in its clip."""
    if beep_time >= 0.0:
        return
    if video.role != "secondary":
        raise HTTPException(status_code=400, detail="beep_time must be >= 0 for a primary camera")
    if beep_time < -BEEP_BEFORE_CLIP_MAX_S:
        raise HTTPException(
            status_code=400,
            detail=f"a beep more than {BEEP_BEFORE_CLIP_MAX_S:g} s before the clip is not one it cut off",
        )


async def _replace_secondary_beeps(state: AppState, slug: str, stage_number: int) -> None:
    """A confirmed primary beep is the best reference there is for the
    stage's other cameras (#1442): run beep detection again for every
    secondary whose beep nobody confirmed or typed, so its proposal and
    its "from primary" candidate come from the confirmed beep. A
    confirmed or hand-set secondary beep is never touched."""
    stage = state.shooter_project(slug).stage(stage_number)
    for other in stage.videos:
        if other.role == "primary" or other.beep_reviewed or other.beep_source == "manual":
            continue
        if (
            await state.jobs.find_active(
                kind="detect_beep", stage_number=stage_number, shooter_slug=slug, video_id=other.video_id
            )
            is not None
        ):
            continue
        try:
            await state.jobs.submit(
                kind="detect_beep",
                stage_number=stage_number,
                shooter_slug=slug,
                video_id=other.video_id,
                args={"slug": slug, "stage_number": stage_number, "video_id": other.video_id},
            )
        except FeatureRequiredError as exc:
            _log_chain_skipped("detect_beep", slug, stage_number, exc)


async def _after_beep_reviewed(state: AppState, slug: str, stage_number: int, video: StageVideo) -> None:
    """Everything that must follow ``beep_reviewed`` flipping to True.

    Shared by the per-video review endpoint and the cross-shooter
    queue so a beep confirmed from either place leaves the same state
    behind: a stub audit document (status surfaces and the lab read a
    document, never infer from absence; never overwrite a real one --
    a re-confirm on an audited stage must not wipe shot data) and,
    for a primary whose trim is already cached, a queued
    ``shot_detect``. That submit is the explicit unblock point for the
    downstream pipeline (auto-detect leaves the flag False so the
    ensemble doesn't burn cycles on an unconfirmed beep). A video
    whose trim is not cached gets the trim submitted instead (when
    the stage time and beep allow it, same rule as
    ``_maybe_chain_trim``); the trim job chains detection itself for
    a reviewed primary. Before UX PR 5 only a manual override chained
    the trim, so confirming the detector's time as-is on an untrimmed
    stage left it untrimmed with no shots. Secondaries get their trim
    and no detection (no shot timeline of their own).

    Mirrors mark state only, like the beep override endpoint: a
    ``desktop``-origin match has no raw media hosted-side to detect
    against and the desktop owns its audit documents, so neither the
    stub nor the job is written. Desktop re-derives on its next sync
    pull.
    """
    # The beep's distance from its tone (spec 2026-10-17), when its
    # source WAV is published here; the derive job measures it otherwise.
    proj_now = state.shooter_project(slug)
    target = proj_now.stage(stage_number).find_video_by_id(video.video_id)
    if target is not None and beep_tone_check.record_tone_offset(
        state.shooter_root(slug), proj_now, stage_number, target
    ):
        proj_now.save(state.shooter_root(slug))
    if current_match_origin.get() == "desktop":
        return
    if video.role == "primary":
        await _replace_secondary_beeps(state, slug, stage_number)

    def stub_if_missing(doc: dict[str, Any] | None, version: int) -> dict[str, Any]:
        if doc is not None:
            raise _AuditUnchanged
        return {"shots": [], "detection": STUB_AUDIT_DETECTION}

    try:
        state.update_audit_at(slug, stage_number, stub_if_missing)
    except _AuditUnchanged:
        pass
    # The reconciler's rule with explicit=True (spec 2026-09-27 s3): a
    # trim when none is cached, else detection for a trimmed primary. A
    # phone confirm pulled into this desktop reaches the same rule
    # through plan_reconcile, so the two cannot leave different state.
    stage = state.shooter_project(slug).stage(stage_number)
    step = video_step(stage, video, None, explicit=True)
    if step == "trim":
        await _maybe_chain_trim(state, slug, stage, video)
        return
    if (
        step == "shot_detect"
        and await state.jobs.find_active(kind="shot_detect", stage_number=stage_number, shooter_slug=slug)
        is None
    ):
        try:
            await state.jobs.submit(
                kind="shot_detect",
                stage_number=stage_number,
                shooter_slug=slug,
                args={"slug": slug, "stage_number": stage_number},
            )
        except FeatureRequiredError as exc:
            _log_chain_skipped("shot_detect", slug, stage_number, exc)


def _identity_logo_dir(state: AppState, slug: str) -> Path:
    return state.shooter_root(slug) / LOGO_DIR


async def _write_book(state: AppState, project: MatchProject, slug: str, sid: int) -> None:
    logo_bytes: bytes | None = None
    if project.identity.logo is not None:
        local = ensure_local_logo(project, state.shooter_root(slug))
        try:
            logo_bytes = local.read_bytes() if local is not None else None
        except OSError as exc:
            logger.warning("shooter book: could not read %s's logo (%s); saved without it", slug, exc)
    await shooter_book_module.save_identity(
        state.shooter_book,
        shooter_id=sid,
        identity=project.identity,
        label=project.competitor_name or project.name,
        logo_bytes=logo_bytes,
    )


def _roster_book(state: AppState) -> shooter_book_module.BookSnapshot:
    """The shooter book the roster reads (the shooters list, Compare's
    payload, the logo route), so the ring shows the look the video draws.
    A share request reads none: the book is the owner's account data and
    no share route reads it."""
    if current_share_request.get():
        return shooter_book_module.EMPTY_BOOK
    return shooter_book_module.load_snapshot(state.shooter_book)


def _audio_pending(
    state: AppState,
    slug: str,
    target: str,
    *,
    stage_number: int | None = None,
    video_id: str | None = None,
    storage_path: str | None = None,
) -> JSONResponse:
    """The WAV a request wants is not published yet: submit the one job
    that makes it (deduplicated on kind, stage, shooter and video) and
    answer 202, the shape the hosted raw-file peaks already use. The SPA
    shows a preparing line and asks again."""
    dedupe_id = {"audit": "audit", "take": f"take:{storage_path}"}.get(target, video_id)
    if target == "audit" and stage_number is not None:
        # A trim running for the stage makes the audit WAV itself; a
        # derive job beside it could cut from the old window (review,
        # 2026-10-14). Wait on the trim.
        trimming = run_sync(state.jobs.find_active(kind="trim", stage_number=stage_number, shooter_slug=slug))
        if trimming is not None:
            return JSONResponse({"pending": True, "active_job": True, "job_id": trimming.id}, status_code=202)
    existing = run_sync(
        state.jobs.find_active(
            kind="derive_audio", stage_number=stage_number, shooter_slug=slug, video_id=dedupe_id
        )
    )
    job_id = existing.id if existing is not None else None
    if existing is None:
        try:
            job = run_sync(
                state.jobs.submit(
                    kind="derive_audio",
                    stage_number=stage_number,
                    shooter_slug=slug,
                    video_id=dedupe_id,
                    args={
                        "slug": slug,
                        "target": target,
                        "stage_number": stage_number,
                        "video_id": video_id,
                        "storage_path": storage_path,
                    },
                )
            )
        except FeatureRequiredError:
            # An account without hosted compute (the sharing tier) runs
            # no jobs: a mirror's WAV arrives with the desktop's next
            # push. Pending with no job, never a 403 (review, 2026-10-14).
            return JSONResponse({"pending": True, "active_job": False, "job_id": None}, status_code=202)
        job_id = job.id
    return JSONResponse({"pending": True, "active_job": True, "job_id": job_id}, status_code=202)


def _resolve_audit_audio(
    state: AppState, slug: str, project: MatchProject, stage_number: int
) -> audio_helpers.AuditAudioResult | JSONResponse:
    """Shared resolver for /audio + /peaks: the published audit WAV
    (trimmed clip's, else the full primary's), or a 202 pending while
    a job makes it. Never extracts here (spec 2026-10-14)."""
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
    if primary.beep_time is None and _is_mirror():
        # A desktop mirror has no source to cut from and the primary was
        # never trimmed (#1496): a derive_audio job could only fail. Say why
        # instead of queueing it, so the page shows the fix, not a spinner.
        raise HTTPException(
            status_code=409,
            detail={
                "code": "primary_not_ready",
                "message": (
                    f"Stage {stage_number}'s primary camera has no confirmed beep, so it has no clip "
                    "to play here. Confirm its beep on the desktop, or make the camera you audited on "
                    "the primary again."
                ),
            },
        )
    published = audio_helpers.published_audit_audio(
        state.shooter_root(slug),
        stage_number,
        primary.beep_time,
        project=project,
    )
    if published is not None:
        return published
    return _audio_pending(state, slug, "audit", stage_number=stage_number)


def _local_source(state: AppState, project: MatchProject, slug: str, video: StageVideo) -> Path | None:
    """``video``'s source when it is a file on this disk, for the
    freshness check; never mirrors a remote source (that is a job's)."""
    if state.storage is not None:
        return None
    try:
        source = project.resolve_video_path(state.shooter_root(slug), video.path)
    except Exception:  # noqa: BLE001 -- an unresolvable source just skips the check
        return None
    return source if source.exists() else None


def _serve_beep_preview(
    state: AppState,
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
    ensure_source_reachable(stage_number, source)
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


def _video_clip_anchor(
    state: AppState,
    slug: str,
    project: MatchProject,
    stage_number: int,
    video: StageVideo,
) -> tuple[float | None, str]:
    """Where the beep falls inside the clip the SPA will receive
    from ``/api/videos/stream`` for ``video``.

    When a per-video trimmed MP4 exists, the beep sits at
    ``min(beep_time, pre_buffer)`` inside it (the trim starts at
    ``max(0, beep_time - pre_buffer)``). When there's no trim we
    fall through to the source clip and the beep is at ``beep_time``
    directly. No ffmpeg / no audio extraction; in hosted mode this
    pays only a cheap ``storage.exists`` HEAD so the reported
    position agrees with the clip ``stream_video`` will actually
    serve (which pulls the worker-cut trim on demand).

    Uses the SAME fallback-aware resolver as ``stream_video``'s
    ``pull_trimmed_video`` (``resolve_trim_for_read``) so a
    pre-take-spec legacy-keyed trim counts here too, and reads the
    pre_buffer from the resolved trim's params sidecar - otherwise
    the anchor stays source-based while the served clip is
    trim-based and every marker lands offset by beep - pre_buffer.

    Returns (anchor, kind) where kind names the clip the SPA should
    stream: "web" (the trim's streaming rendition, hosted only),
    "trim" or "source". The web rendition shares the trim's anchor.
    """
    anchor, kind, trim_path = _video_trim_anchor(state, slug, project, stage_number, video)
    if kind == "trim" and trim_path is not None and _presigned_hosted(state, video):
        # The streaming rendition (#1031) covers the trim's window, so
        # the anchor carries over; only the file the SPA names changes.
        # Hosted-only by design: local playback streams the full-res
        # trim from disk and has no reason to downgrade.
        if audio_helpers.web_trim_available(project, trim_path):
            return (anchor, "web")
    return (anchor, kind)


def _presigned_hosted(state: AppState, video: StageVideo) -> bool:
    """Whether ``stream_video`` would answer for ``video`` with a
    presigned redirect (the same test it applies itself)."""
    storage = state.storage
    return storage is not None and storage.supports_presigned_get and not video.path.is_absolute()


def _video_trim_anchor(
    state: AppState,
    slug: str,
    project: MatchProject,
    stage_number: int,
    video: StageVideo,
) -> tuple[float | None, str, Path | None]:
    """``_video_clip_anchor`` before the web-rendition step: the
    anchor, ``"trim"`` or ``"source"``, and the new-keyed trim path
    the kind was measured against (``None`` for source)."""
    # One roster lookup (a state_docs SELECT hosted) for both resolvers.
    root = state.shooter_root(slug)
    resolved = audio_helpers.resolve_trim_for_read(root, stage_number, video, project=project)
    if resolved is not None:
        if video.beep_time is None:
            return (None, "trim", resolved)
        pre_buffer = audio_helpers.trim_pre_buffer_seconds_for(
            resolved, default=project.trim_pre_buffer_seconds
        )
        return (min(video.beep_time, pre_buffer), "trim", resolved)
    # Hosted: the trim may exist only in the storage cache (worker-
    # cut, not yet mirrored); stream_video pulls it on demand, so the
    # anchor must agree. New-keyed only by design: a storage-only
    # legacy-keyed trim is missed by BOTH the pull and this check, so
    # bytes and anchor degrade consistently to the source path, and
    # the next worker trim job re-cuts under the new key.
    trimmed = audio_helpers.trimmed_video_path(root, stage_number, video, project=project)
    # A hosted-native match stores the rendition in the trim's place (#1683),
    # cut with this project's buffers like a stored trim: one existence check,
    # no params read. A legacy trim with no rendition is the next check's.
    native_web = (
        not _is_mirror()
        and audio_helpers.stores_web_only(project)
        and audio_helpers.web_trim_available(project, trimmed)
    )
    if native_web or audio_helpers.trim_available(project, trimmed):
        if video.beep_time is None:
            return (None, "trim", trimmed)
        return (min(video.beep_time, project.trim_pre_buffer_seconds), "trim", trimmed)
    if _is_mirror() and audio_helpers.web_trim_available(project, trimmed):
        # Web-only mirror (spec 2026-09-27 v1.1): the rendition + the
        # pushed params stand in for the trim; _video_clip_anchor then
        # promotes the kind to "web". Only the small params sidecar is
        # fetched here, never the rendition itself.
        audio_helpers.refresh_trim_params(project, trimmed)
        if video.beep_time is None:
            return (None, "trim", trimmed)
        pre_buffer = audio_helpers.trim_pre_buffer_seconds_for(
            trimmed, default=project.trim_pre_buffer_seconds
        )
        return (min(video.beep_time, pre_buffer), "trim", trimmed)
    return (video.beep_time, "source", None)


def _video_beep_in_clip(
    state: AppState,
    slug: str,
    project: MatchProject,
    stage_number: int,
    video: StageVideo,
) -> float | None:
    return _video_clip_anchor(state, slug, project, stage_number, video)[0]


def _coach_video_entries(state: AppState, slug: str, project: MatchProject, stg: Any) -> list[dict[str, Any]]:
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
    labels = camera_labels(ordered_videos)
    root = state.shooter_root(slug)
    # One listing of this shooter's trimmed/ prefix per request, as in
    # ``get_project`` (#1209).
    _storage = state.storage
    presence = StoragePresence(_storage) if _storage is not None and _storage.supports_presigned_get else None
    out: list[dict[str, Any]] = []
    for v, label in zip(ordered_videos, labels, strict=True):
        anchor, kind = _video_clip_anchor(state, slug, project, stg.stage_number, v)
        out.append(
            {
                "path": str(v.path),
                "role": v.role,
                "beep_in_clip": anchor,
                "kind": kind,
                "label": label,
                # The mount and role are what a camera choice keys on
                # across stages (``camera_select``): a video id names a
                # file on one stage, not a camera.
                "mount": v.camera_mount,
                # The same pins ``get_project`` gives the Audit players:
                # None for both when no trim exists (``kind == "source"``).
                "trim_version": _trim_version_for(root, stg.stage_number, v, project),
                "scrub_version": (
                    _hosted_scrub_version_for(presence, root, stg.stage_number, v, project)
                    if presence is not None and not v.path.is_absolute()
                    else _scrub_version_for(root, stg.stage_number, v, project)
                ),
                # Frame steps and the pill's frame number (spec 2026-10-17):
                # null when the trim is not on this disk; the SPA uses 30.
                "fps": _frame_rate_for(root, stg.stage_number, v, project),
            }
        )
    return out


def _coach_events(payload: dict[str, Any], stage_number: int) -> list[StageEvent]:
    """The doc's stage events; a corrupt list is a 422, not a 500."""
    try:
        return events_module.events_from_doc(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"stage {stage_number}: invalid events: {exc}") from exc


def _classify_doc(payload: dict[str, Any], stage_number: int, cfg: CoachAutoClassifyConfig) -> None:
    """Run the auto-classifier over the doc's shots against the doc's own
    *confirmed* stage events (spec 2026-10-08), in place.

    The one classification path for every audit-doc writer: a writer
    that classified region-blind would flip a reload region's gap back
    to ``movement`` on the next unrelated save. Only confirmed regions
    (``source == "manual"``) hint the classifier: an auto proposal never
    touches a stored class, whatever save happens next. A corrupt events list is
    the coach GET's 422, raised before the caller saves anything --
    also on a doc with no shots to classify (#1330): the coach routes
    build their response from the saved doc, and a corrupt list that
    slipped past here was a 422 *after* the save went through.
    """
    stage_events = events_module.confirmed(_coach_events(payload, stage_number))
    shots = payload.get("shots")
    if not isinstance(shots, list):
        return
    coach_module.classify_intervals_in_dicts(
        [s for s in shots if isinstance(s, dict)],
        cfg,
        events=stage_events,
    )


def _coach_capacity(state: AppState, slug: str, project: MatchProject) -> int | None:
    """The magazine capacity of the shooter's division, or None.

    ``competitor_division`` reads the shooter directory only for a
    scoreboard-pinned project with no stored division; resolving it
    otherwise would cost a roster lookup (a state_docs SELECT hosted)
    for nothing.
    """
    needs_dir = (
        not project.competitor_division
        and project.selected_competitor_id is not None
        and bool(project.scoreboard_match_id)
    )
    project_dir = state.shooter_root(slug) if needs_dir else None
    return events_module.capacity_for(competitor_division(project, project_dir))


def _coach_stage(state: AppState, slug: str, stage_number: int) -> tuple[Any, MatchProject, float | None]:
    """The stage a coach route works on: (stage, project, the primary's
    beep inside the clip the SPA streams). 404 for an unknown stage. Reads
    the project only, so a writer calls it before its read-modify-write."""
    project = state.shooter_project(slug)
    try:
        stg = project.stage(stage_number)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    prim = stg.primary()
    primary_beep_in_clip = (
        _video_beep_in_clip(state, slug, project, stage_number, prim) if prim is not None else None
    )
    return stg, project, primary_beep_in_clip


def _require_audit_doc(doc: dict[str, Any] | None, stage_number: int) -> dict[str, Any]:
    """The audit doc a coach write needs to exist: 404 when the stage has
    none yet. Called inside a merge, so the 404 is the read-modify-write's
    answer and nothing is written."""
    if doc is None:
        raise HTTPException(status_code=404, detail=f"no audit JSON yet for stage {stage_number}")
    return doc


def _load_audit_for_coach(
    state: AppState,
    slug: str,
    stage_number: int,
) -> tuple[dict[str, Any], int, float | None, Any, MatchProject]:
    """Shared loader for a coach *read*: validates the stage, reads the
    audit doc, returns (payload, version, primary_beep_in_clip, stage,
    project). 404 when no audit doc exists yet. A coach write does the
    same through ``_coach_stage`` and a ``state.update_audit_at`` merge
    that calls ``_require_audit_doc``."""
    stg, project, primary_beep_in_clip = _coach_stage(state, slug, stage_number)
    payload, version = state.load_audit(slug, stage_number)
    return _require_audit_doc(payload, stage_number), version, primary_beep_in_clip, stg, project


class _AuditUnchanged(Exception):  # noqa: N818 -- a merge's signal, not an error
    """Raised by an ``update_audit_at`` merge that found nothing to write.

    The port writes whatever the merge returns and writes nothing when it
    raises (spec 2026-10-19 s2, pinned by the contract suite), so this is
    how a handler whose write is conditional declines: the beep-confirm
    stub when a real document already exists, the coach read when the heal
    touched nothing. The handler catches it; nothing else does.
    """


def _audit_rmw(state: AppState) -> AbstractContextManager[object]:
    """The guard for a local audit read-modify-write that spans more than
    one document (#1075). One site: the shooter move, which reads and
    rewrites two shooters' audit docs as one operation. A single-document
    read-modify-write goes through ``state.update_audit_at`` instead,
    which holds this lock itself.

    A sync pull's audit merge holds ``state.audit_lock`` (spec
    2026-09-27 s5); a writer that loads, edits and saves outside it
    can lose the pulled change or have its own edit lost. Hosted
    returns a no-op: the lock is process-global there, and the
    ``state_docs`` version already optimistic-locks every save.
    """
    return state.audit_lock if state.docstore().documents_on_disk else nullcontext()


def _note_digest(text: str | None) -> dict[str, Any] | None:
    """A note as an audit event records it: its length and a short hash,
    never the text. Notes autosave after every pause and the journal
    syncs, so the text itself would be copied once per pause."""
    if text is None:
        return None
    return {"length": len(text), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]}


def _stage_note(payload: dict[str, Any]) -> str | None:
    """The doc's stage note, or None for a missing or non-string one
    (audit docs are hand-editable JSON)."""
    note = payload.get(coach_module.FIELD_STAGE_NOTE)
    return note if isinstance(note, str) and note else None


def _build_coach_response(
    state: AppState,
    slug: str,
    payload: dict[str, Any],
    primary_beep_in_clip: float | None,
    stg: Any,
    project: MatchProject,
    cfg: CoachAutoClassifyConfig,
    version: int,
    *,
    capacity: int | None,
    stored_revision: str | None = None,
) -> dict[str, Any]:
    # ``capacity`` is the caller's ``_coach_capacity`` (a roster lookup
    # for an older scoreboard-pinned project), computed once per request.
    # ``stored_revision`` is the stored doc's ``_version`` when ``payload``
    # carries in-memory changes that were not saved (a mirror's seed, a
    # lost save race); otherwise ``payload`` is what is stored.
    # Coach plays the same clip the audit screen serves -- typically
    # the project-local trimmed MP4 -- so every "absolute" time must
    # be in the served clip's coordinate system. Anchoring on
    # ``primary_beep_in_clip`` keeps Coach working when the source
    # SSD is unplugged. Falls back to 0.0 when the project hasn't
    # set a beep yet (older / partial audits).
    clip_anchor = primary_beep_in_clip if primary_beep_in_clip is not None else 0.0
    raw_shots = payload.get("shots") or []
    # A shot whose time is missing or unreadable (a string, NaN: a
    # hand-edited doc) is left out of the view rather than 500ing every
    # read of the stage (``events.shot_ms``).
    ordered = sorted(
        ((ms, s) for s in raw_shots if (ms := events_module.shot_ms(s)) is not None),
        key=lambda pair: pair[0],
    )
    stage_events = _coach_events(payload, stg.stage_number)
    # Every figure here reads confirmed regions only, as every output does
    # (``events.confirmed``): ``stale``, per-shot ``moving`` and the
    # summary Coach draws (its capacity warning included), so a figure
    # never moves because Breakdown was opened and proposed something.
    # The response's ``events`` keep the proposals for Breakdown's lanes.
    confirmed_events = events_module.confirmed(stage_events)
    coach_shots: list[dict[str, Any]] = []
    prev_ms: float | None = None
    for ms, s in ordered:
        time_from_beep = ms / 1000.0
        gap_s: float | None
        if prev_ms is None:
            gap_s = None
            split = time_from_beep  # draw
            overlap = False
        else:
            gap_s = (ms - prev_ms) / 1000.0
            split = gap_s
            # Confirmed regions only, as ``_classify_doc``: a proposal
            # never makes a stored class stale.
            overlap = coach_module.gap_overlaps_reload(prev_ms / 1000.0, time_from_beep, confirmed_events)
        prev_ms = ms
        stale = coach_module.is_classification_stale(s, gap_s=gap_s, config=cfg, reload_overlap=overlap)
        reload_hint = coach_module.reload_hinted(gap_s, cfg)
        coach_shots.append(
            {
                "shot_number": int(s.get("shot_number", 0)),
                # #844: the stable id, so a client can address the by-id
                # PATCH instead of the positional one. ``null`` for a
                # shot whose id is missing or unusable (an int from a
                # hand-edited doc) -- the by-id route matches on a
                # string, so anything else would only ever 404, and the
                # client is meant to fall back to the positional route
                # plus ``expected_version`` for exactly these.
                "id": s["id"] if has_usable_id(s) else None,
                "ms_after_beep": int(ms),
                "time_from_beep": time_from_beep,
                # In the served clip's coordinate system: where the
                # SPA must seek the primary <video> for this shot.
                "time_absolute": clip_anchor + time_from_beep,
                "split": split,
                "interval_class": s.get("interval_class"),
                "interval_class_source": s.get("interval_class_source"),
                "improvement_flag": (
                    False if current_share_request.get() else bool(s.get("improvement_flag", False))
                ),
                "coaching_note": (None if current_share_request.get() else s.get("coaching_note")),
                "stale": stale,
                "reload_hint": reload_hint,
            }
        )
    for shot_out in coach_shots:
        shot_out["moving"] = events_module.shot_is_moving(shot_out["time_from_beep"], confirmed_events)
    summary = events_module.stage_event_summary(
        [shot_out["time_from_beep"] for shot_out in coach_shots],
        confirmed_events,
        capacity,
    )
    return {
        "stage_number": stg.stage_number,
        "stage_name": stg.stage_name,
        # Where the beep falls in the served primary clip. Used by
        # the SPA's beep row + as the anchor for offsetting synced
        # secondaries (each ``videos[i].beep_in_clip`` mirrors this
        # field for that camera's served clip).
        "beep_time": clip_anchor,
        # The audit doc's version as of this response (#844) -- what a
        # client sends as ``expected_version`` on the positional shot
        # PATCH. Always 0 in local mode, where nothing locks a file, so
        # the guard is hosted-only in practice.
        "version": version,
        "videos": _coach_video_entries(state, slug, project, stg),
        # The camera the stage and Compare pages start on (a mount or
        # role, ``camera_select``): a share link's own choice for this
        # shooter when the link has one, else the shooter's saved one.
        "compare_camera": (current_share_cameras.get() or {}).get(slug, project.compare_camera),
        "shots": coach_shots,
        # A region's ``note`` is private text, like ``coaching_note``: the
        # share surface reaches this route (``_SHARE_PATH_RE``), so strip it.
        "events": [
            e.model_dump(exclude_none=True, exclude={"note"} if current_share_request.get() else None)
            for e in stage_events
        ],
        "event_summary": summary.model_dump(),
        # The stage note (#1376): private text, like ``coaching_note``,
        # so a share read gets none.
        "stage_note": None if current_share_request.get() else _stage_note(payload),
        # The stored audit doc's content revision, what the events PUT
        # compares against (the audit route's ``_version``).
        REVISION_FIELD: stored_revision if stored_revision is not None else audit_revision(payload),
    }


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


def _serve_proxy(state: AppState, base_root: Path, video: StageVideo) -> FileResponse | None:
    """Return a FileResponse for the proxy object if it exists, else None.

    Callers fall through to source resolution on None - proxy absent,
    path not a raw/ key, or no storage configured.

    Local cache is checked first - proxy objects are immutable per key (delete
    cleanup removes them; a re-upload regenerates), and _mirror_from_storage uses
    temp+atomic-rename so a present local file is always complete and never stale.
    This avoids redundant R2 pulls on every HTTP Range request during video scrubbing.
    """
    from ..proxy import proxy_key_for

    _raw_str = str(video.path)
    _storage = state.storage
    if not _raw_str.startswith("raw/") or _storage is None:
        return None
    _proxy_key = proxy_key_for(_raw_str)
    _local_proxy = base_root / _proxy_key
    if _local_proxy.exists():  # warm cache - no network at all
        return FileResponse(
            _local_proxy.resolve(),
            media_type="video/mp4",
            filename=_local_proxy.name,
        )
    if not _storage.exists(_proxy_key):
        return None
    # cold - mirror once, then serve
    MatchProject._mirror_from_storage(_storage, _proxy_key, _local_proxy)
    return FileResponse(
        _local_proxy.resolve(),
        media_type="video/mp4",
        filename=_local_proxy.name,
    )


def _hosted_web_redirect(
    storage: Storage,
    project: MatchProject,
    root: Path,
    stage: Any,
    video: StageVideo,
) -> RedirectResponse | None:
    """Presigned redirect to the trim's streaming rendition (#1031) when
    the object exists, else ``None`` so the caller falls back to the
    trim and then the source: an explicit ``kind=web`` never 404s,
    because a match synced before the rendition existed still has to
    play."""
    local_mp4 = audio_helpers.trimmed_video_path(root, stage.stage_number, video, project=project)
    web_local = trim_module.web_trim_path(local_mp4)
    web_key = audio_helpers._storage_trim_key(project, web_local)
    if web_key is not None and storage.exists(web_key):
        return serve_media(storage, web_key, web_local, content_type="video/mp4")
    return None


def _resolve_match_context(state: AppState) -> tuple[Path, match_model.Match]:
    """Return ``(match_root, match)`` for the bound project.

    Raises 409 ``not_a_match`` for legacy single-shooter layouts (the
    shooter-listing / merge / compare endpoints are match-only).
    """
    match_root = state.match_root
    return match_root, state.match()


def _classify_shooter(
    state: AppState,
    shooter_root: Path,
    match: match_model.Match,
    presence: StoragePresence | None,
    book: shooter_book_module.BookSnapshot = shooter_book_module.EMPTY_BOOK,
) -> ShooterListEntry:
    """Build a list entry for a single shooter directory.

    ``presence`` is the request's storage index (#1180), or ``None`` on
    a share request: the ``stages_missing_trim`` count only gates the
    Rebuild button, which the anonymous share shell never renders, so a
    share request reports 0 and makes no storage calls at all.
    """
    # ``shooter_root.name`` is the slug; the accessor loads the project
    # doc from Postgres (hosted) or disk (local) + binds storage.
    legacy = state.shooter_project(shooter_root.name)
    # ``audited`` requires the operator to have hit Save & next on
    # the stage (see :func:`stage_audit_status`). Set-up-but-not-
    # touched stages used to count here; they no longer do.
    # One pass: derive both the per-stage status list and the audited
    # count from a single ``stage_statuses`` walk rather than also
    # calling ``audited_count`` (which repeats the same audit-dir read).
    stage_status_map = legacy.stage_statuses(audit_docs=state.load_audit_docs(shooter_root.name))
    stages_audited = sum(1 for st in stage_status_map.values() if st == StageStatus.audited)
    # A stage counts once however many of its angles are uncached --
    # the field is a stage count, and the CTA it drives rebuilds the
    # whole stage in one click.
    stages_missing_trim = (
        0
        if presence is None
        else sum(
            1 for s in legacy.stages if _audit_trim_targets(legacy, shooter_root, s, presence=presence)[0]
        )
    )
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
    # Attached-but-unassigned footage counts too. A hosted upload that
    # was attached without stage coverage (or any video the operator
    # dropped before assigning it to a stage) lives in
    # ``unassigned_videos``, disjoint from ``stages[].videos`` (see
    # ``MatchProject.move_video``). Without this the overview reported
    # "NO FOOTAGE YET" for a shooter that already had N videos attached.
    # These carry no camera metadata yet, so they only bump the total,
    # not the per-camera ``groups`` breakdown.
    for video in legacy.unassigned_videos:
        if video.role == "ignored":
            continue
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
        stages_without_time=sum(1 for s in legacy.stages if s.time_seconds <= 0 and not s.skipped),
        identity=effective_identity(legacy, book),
        stage_statuses=[
            StageStatusEntry(stage_number=n, status=st) for n, st in sorted(stage_status_map.items())
        ],
    )


def _build_triage_response(state: AppState) -> TriageResponse:
    """Assemble the cross-shooter, cross-stage triage grid (slice 4).

    Enumerates shooters exactly as :func:`list_match_shooters` does,
    with every shooter's audit docs loaded once through the match
    bundle (hosted: one query; local: the audit files) and derives
    statuses via the same ``stage_statuses`` walk used by the project
    GET. Per non-placeholder stage, the anomaly computation mirrors
    :func:`get_stage_anomalies` exactly: same beep-time fallback, same
    ``audit_shots_to_engine_shots`` + ``report.detect_anomalies_structured``
    call. Both the accept and attention endpoints return this so the
    SPA never has to re-fetch after a write (the confirm-returns-
    fresh-list contract from slice 3). ``beep_low_confidence_threshold``
    is resolved from the first loadable shooter's project override
    exactly the way ``get_hitl_queue`` resolves it (#823) - the gate
    is global to the match, not per-shooter.
    """
    _match_root, match = _resolve_match_context(state)
    # The whole match in one query (#1181): every shooter's project doc
    # and audit docs, instead of two queries per shooter.
    bundle = state.match_bundle(match)
    cells: list[TriageCell] = []
    docs: list[dict | None] = []
    threshold: float | None = None
    for slug in match.shooters:
        legacy = bundle.projects.get(slug)
        if legacy is None:
            continue
        if threshold is None:
            resolved = automation_settings.resolve_automation(project_override=legacy.automation)
            threshold = resolved.settings.beep_low_confidence_threshold
        name = legacy.competitor_name or slug
        audit_docs = bundle.audit_docs(slug)
        status_map = legacy.stage_statuses(audit_docs=audit_docs)
        for stg in legacy.stages:
            if stg.placeholder:
                continue
            prim = stg.primary()
            beep = prim.beep_time if prim is not None and prim.beep_time is not None else 0.0
            doc = audit_docs.get(stg.stage_number)
            docs.append(doc)
            anomalies: list[dict] = []
            engine_shots: list[Shot] = []
            if doc is not None:
                engine_shots = audit_shots_to_engine_shots(doc, beep_time_in_source=beep)
                anomalies = [
                    a.model_dump()
                    for a in report.detect_anomalies_structured(engine_shots, beep, stg.time_seconds)
                ]
            figures = (
                stage_figures(
                    engine_shots,
                    regions=events_module.confirmed_from_doc(doc, log_context=f"stage {stg.stage_number}"),
                )
                if doc is not None
                else None
            )
            confs = [v.beep_confidence for v in stg.videos if v.beep_confidence is not None]
            na_raw = doc.get("needs_attention") if isinstance(doc, dict) else None
            needs_attention: TriageAttentionOut | None = None
            if isinstance(na_raw, dict):
                try:
                    needs_attention = TriageAttentionOut(**na_raw)
                except ValidationError:
                    needs_attention = None
            tones = [o for o in (tone_flag_ms(v) for v in stg.videos if v.role != "ignored") if o is not None]
            cells.append(
                TriageCell(
                    slug=slug,
                    shooter_name=name,
                    stage_number=stg.stage_number,
                    stage_name=stg.stage_name,
                    status=str(status_map[stg.stage_number]),
                    beep_confidence=min(confs) if confs else None,
                    anomalies=anomalies,
                    needs_attention=needs_attention,
                    video_count=len([v for v in stg.videos if v.role != "ignored"]),
                    beep_time=prim.beep_time if prim is not None else None,
                    beep_reviewed=bool(prim.beep_reviewed) if prim is not None else False,
                    shot_count=len(engine_shots),
                    draw=figures.draw if figures is not None else None,
                    avg_split=figures.avg_split if figures is not None else None,
                    time_seconds=float(stg.time_seconds),
                    beep_tone_offset_ms=max(tones, key=abs) if tones else None,
                )
            )
    cells.sort(key=lambda c: (c.stage_number, c.shooter_name.lower()))
    if threshold is None:
        threshold = automation_settings.resolve_automation().settings.beep_low_confidence_threshold
    return TriageResponse(
        cells=cells,
        flagged_count=_count_flagged(docs),
        beep_low_confidence_threshold=threshold,
        beep_tone_flag_ms=beep_tone_module.beep_tone_config().flag_ms,
    )


def _account_info(ref: user_config.HostedAccountRef | None) -> HostedAccountInfo | None:
    if ref is None:
        return None
    return HostedAccountInfo(
        id=ref.id,
        email=ref.email,
        display_name=ref.display_name,
        device_name=ref.device_name,
        linked_at=ref.linked_at,
    )


async def _refresh_hosted_account(state: AppState, prefs: user_config.GlobalPrefs) -> user_config.GlobalPrefs:
    """Best-effort upstream refresh of the cached account snapshot (#877).

    The snapshot is written once, at device-link time, when
    ``display_name`` is typically still NULL. Without this the chip
    renders the operator's email address forever -- the one thing
    the design says it will never publish -- and the only repair is
    to unlink and re-link.

    Best-effort in the strong sense: every failure path returns the
    cached value with no error surfaced. The chip reads a missing
    account as "not signed in" (#738), so a network blip must not be
    able to produce that.

    The TTL is consumed before the call, not after, so a dead host
    costs one timeout per window rather than one per chip mount.

    This is a WRITER, on the one route the SPA fires by itself, and
    the ``await`` in the middle of it is long (up to
    ``HOSTED_ACCOUNT_REFRESH_TIMEOUT_S``). The other writers -- the
    settings PUT, the device-flow approval and the sign-out DELETE
    -- can all land inside that window. So the object handed in is
    used only to decide *what to ask*; the write re-reads from disk
    immediately before saving, with no ``await`` in between, and
    merges the two account fields alone. Saving the object we
    loaded before the call would persist ``hosted_token`` too, and
    a sign-out in the window would come back from the dead: a
    revoked bearer and a live-looking chip (#877 review).
    """
    ref = prefs.hosted_account
    if ref is None or not prefs.hosted_base_url or not prefs.hosted_token:
        return prefs

    now = time.monotonic()
    last = state.hosted_account_refreshed_at
    if last is not None and now - last < HOSTED_ACCOUNT_REFRESH_TTL_S:
        return prefs
    state.hosted_account_refreshed_at = now

    client: HostedSyncClient | None = None
    try:
        # Local import: same lazy idiom sync_api.py itself uses to
        # stay import-safe on a local-slim install (see the comment
        # at its router registration, above). Inside the guard, so
        # the "best-effort in the strong sense" claim covers it too.
        from .sync_api import SyncWhoAmIResponse

        client = _build_device_client(
            prefs.hosted_base_url,
            token=prefs.hosted_token,
            timeout=HOSTED_ACCOUNT_REFRESH_TIMEOUT_S,
        )
        raw = await run_in_threadpool(client.whoami)
        payload = SyncWhoAmIResponse.model_validate(raw)
    except Exception:
        # Deliberately broad, per the docstring's "best-effort in the
        # strong sense": this route's entire job is to report the
        # cached snapshot, so degrading to it beats a 500 for ANY
        # failure here, not just the ones a narrower tuple happens to
        # name. Concretely this has to catch, at minimum:
        #   - SyncClientError / httpx.HTTPError -- non-2xx, including
        #     401 (a revoked token is not grounds to unlink here: an
        #     upstream outage would then sign every install out;
        #     revocation surfaces on the next sync instead)
        #   - httpx.InvalidURL -- a malformed hosted_base_url; it is
        #     NOT an httpx.HTTPError subclass, and hosted_base_url is
        #     an unvalidated str a hand-edited config.yaml can corrupt
        #   - pydantic.ValidationError -- a 200 whose body isn't the
        #     shape SyncWhoAmIResponse declares (not even a dict)
        # A previous narrower tuple here caught the first bullet but
        # missed the last two, turning either into an unhandled 500
        # on a route whose whole job is to report a cached value
        # (#877 review).
        #
        # Logged because the visible symptom of a NameError or an
        # AttributeError in here is identical to a hosted outage --
        # "the chip still shows my email address", i.e. the bug this
        # code exists to fix -- and nothing else would record it.
        logger.debug("hosted account refresh failed", exc_info=True)
        return prefs
    finally:
        if client is not None:
            client.close()

    if payload.email == ref.email and payload.display_name == ref.display_name:
        return prefs

    # Re-read, then write, with no await in between: see the
    # docstring. ``fresh`` is also what we return, so the response
    # reports what is actually on disk rather than the snapshot this
    # request started from.
    fresh = user_config.load_global_prefs()
    cur = fresh.hosted_account
    if cur is None or cur.id != ref.id:
        # Unlinked, or re-linked to another account, while we were
        # away. The answer in hand describes a link that no longer
        # exists; discard it rather than stamp it onto the new one.
        return fresh
    fresh.hosted_account = cur.model_copy(
        update={"email": payload.email, "display_name": payload.display_name}
    )
    user_config.save_global_prefs(fresh)
    return fresh


async def _desktop_presence(state: AppState) -> dict[str, Any]:
    """Newest contact from any of the user's live desktop tokens.
    ``around`` means seen within DESKTOP_AROUND_SECONDS."""
    store = state.desktop_tokens
    records = await store.list() if store is not None else []
    seen = [r.last_used_at for r in records if r.revoked_at is None and r.last_used_at is not None]
    live = any(r.revoked_at is None for r in records)
    last = max(seen) if seen else None
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    around = last is not None and (datetime.now(UTC) - last).total_seconds() <= DESKTOP_AROUND_SECONDS
    return {
        "linked": live,
        "last_seen_at": last.isoformat() if last is not None else None,
        "around": around,
    }


def _command_store(state: AppState) -> Any:
    if not _hosted_mode_active():
        raise HTTPException(status_code=404, detail="not found")
    store = state.desktop_commands
    if store is None:
        raise HTTPException(status_code=500, detail="desktop command store unavailable")
    return store


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
        user = await request.app.state.splitsmith_state.auth.authenticate_request(request)
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


async def _auto_queue_beep_if_needed(
    state: AppState, slug: str, project: MatchProject, stage_number: int, video: StageVideo
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
    # ``source_present``, not ``resolve_video_path``: this only decides
    # whether to queue, and two of this helper's callers await it in the
    # request path, so mirroring the raw source here made a video
    # assignment block on a full download (#638). ``shooter_root`` is
    # hoisted because it is a blocking Postgres read in hosted mode and
    # the scan loop calls this helper once per video.
    root = state.shooter_root(slug)
    if not project.source_present(root, video.path):
        logger.info(
            "auto-beep skipped for stage %d video %s: source not reachable (%s)",
            stage_number,
            video.video_id,
            root / video.path,
        )
        return False
    try:
        await _submit_detect_beep(state, slug, stage_number, video)
    except FeatureRequiredError as exc:
        _log_chain_skipped("detect_beep", slug, stage_number, exc)
        return False
    return True


def create_app(
    *,
    project_root: Path | None = None,
    project_name: str | None = None,
    lab_enabled: bool = False,
    lab_token: str | None = None,
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

    ``lab_token`` admits only requests carrying it (``ui/lab_token``); the
    CLI requires one when the server binds beyond loopback.
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
    local_journal: JobJournal | None = None
    if _hosted_mode_active():
        _apply_hosted_mode_wiring(state)
    else:
        # Local mode mirrors active jobs into a SQLite crash-recovery
        # journal (issue #665) so a killed process's queue is re-enqueued
        # below, after ``register_job_bodies``. ``None`` (user config
        # disabled / uncreatable) keeps the in-memory-only behaviour.
        journal_path = default_journal_path()
        if journal_path is not None:
            local_journal = JobJournal(journal_path)
            journaled_registry = JobRegistry(journal=local_journal)
            # Re-point at the shared process-level body map, exactly as
            # ``AppState.__post_init__`` does for the default registry -
            # otherwise ``state.job_bodies`` (read by the dev routes and
            # the take-detect tests) diverges from the registry that
            # actually dispatches.
            journaled_registry.bodies = state.job_bodies
            state._jobs = journaled_registry
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
        # async store call is wrapped in ``run_sync`` so the
        # ``--project`` startup hook still drops the match into
        # recent-projects + the alias middleware.
        run_sync(
            _register_match_at(
                state,
                project_root,
                fallback_name=project_name or project_root.name or "match",
            )
        )

    if not _hosted_mode_active() and not auto_sync_disabled_by_env():

        async def _submit_auto_sync(match_id: str, match_root: Path) -> None:
            id_token = current_match_id.set(match_id)
            root_token = current_match_root.set(match_root)
            try:
                await state.jobs.submit(kind="auto_sync")
            finally:
                current_match_root.reset(root_token)
                current_match_id.reset(id_token)

        async def _start_render_upload(
            match_id: str, match_root: Path, command: dict
        ) -> tuple[str | None, str | None]:
            """Start a phone's render-and-upload request (#1100): the local
            match-export route's pre-flight, reported to the phone instead of
            raised, then one ``render_upload`` job."""
            from .exports_api import check_match_export, http_detail_text

            slug = command["slug"]
            try:
                req = MatchExportRequest.model_validate((command.get("args") or {}).get("request"))
            except ValidationError:
                return None, "the render settings are not valid on this desktop; update the desktop app"
            id_token = current_match_id.set(match_id)
            root_token = current_match_root.set(match_root)
            try:
                try:
                    check_match_export(state, slug, req)
                except HTTPException as exc:
                    return None, http_detail_text(exc)
                for busy in ("match_export", "render_upload"):
                    if await state.jobs.find_active(kind=busy, shooter_slug=slug) is not None:
                        return None, "a match export is already running for this shooter on the desktop"
                job = await state.jobs.submit(
                    kind="render_upload",
                    shooter_slug=slug,
                    args={"slug": slug, "req": req, "command_id": command["id"]},
                )
                return job.id, None
            except Exception as exc:  # noqa: BLE001 - reported to the phone, not raised
                return None, f"could not start on the desktop: {exc}"
            finally:
                current_match_root.reset(root_token)
                current_match_id.reset(id_token)

        async def _start_desktop_command(
            match_id: str, match_root: Path, command: dict
        ) -> tuple[str | None, str | None]:
            """Run a command the phone queued for this desktop (#1100). The
            runner has already applied the revision guard; this starts the
            local job the way the local route would."""
            kind = command.get("kind")
            if kind == "render_upload":
                return await _start_render_upload(match_id, match_root, command)
            if kind != "shot_detect":
                return None, f"this desktop cannot run {kind!r} requests yet"
            slug, stage_number = command["slug"], command["stage_number"]
            reset = bool((command.get("args") or {}).get("reset", True))
            id_token = current_match_id.set(match_id)
            root_token = current_match_root.set(match_root)
            try:
                existing = await state.jobs.find_active(
                    kind="shot_detect", stage_number=stage_number, shooter_slug=slug
                )
                if existing is not None:
                    return None, "a detection is already running on this stage on the desktop"
                job = await state.jobs.submit(
                    kind="shot_detect",
                    stage_number=stage_number,
                    shooter_slug=slug,
                    args={"slug": slug, "stage_number": stage_number, "reset": reset},
                )
                return job.id, None
            except Exception as exc:  # noqa: BLE001 - reported to the phone, not raised
                return None, f"could not start on the desktop: {exc}"
            finally:
                current_match_root.reset(root_token)
                current_match_id.reset(id_token)

        state.auto_sync = AutoSyncService(
            jobs=state.jobs,
            matches=state.matches,
            submit_auto_sync=_submit_auto_sync,
            owner_lock=FileLock(user_config.user_config_dir() / OWNER_LOCK_FILE),
            start_command=_start_desktop_command,
        )
        state.jobs.add_terminal_listener(state.auto_sync.on_job_terminal)

    app = FastAPI(
        title="splitsmith UI",
        description="Production UI backend (issue #11/#12).",
        version="0.1.0",
        lifespan=_hosted_boot_lifespan(state) or _local_boot_lifespan(state),
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
    # JobBackend submission is wrapped in ``run_sync`` here.
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
        # Re-enqueue whatever a dead process left in the crash-recovery
        # journal (issue #665). Must follow ``register_job_bodies`` so
        # kinds resolve; rows for still-unregistered dev-only kinds are
        # dropped with a warning.
        if local_journal is not None:
            run_sync(resume_journaled_jobs(state.jobs, local_journal))
        run_sync(_maybe_submit_model_download(state))
        from .system_api import maybe_install_renderer

        try:
            run_sync(maybe_install_renderer(state.jobs))
        except ShutdownInProgressError:
            pass

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

    @app.post("/api/v1/auth/begin")
    async def _auth_begin(payload: AuthBeginRequest, request: Request) -> JSONResponse:
        """Start a magic-link sign-in: e-mail a link to ``payload.email``.

        Always 200 regardless of whether the address has an account -- the
        account is created on redemption and we never reveal existence.
        """
        if not _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        email = payload.email.strip()
        if not email or "@" not in email:
            raise HTTPException(status_code=400, detail="a valid email is required")
        # begin_login/complete_login/end_session aren't in the AuthBackend
        # Protocol (see auth.py) - they're magic-link-specific, so reach
        # through the composite to backends[0]. Safe: these routes 404
        # above unless hosted mode is active, and hosted mode always
        # installs MagicLinkAuth as backends[0].
        # The blocked branch records an access request and charges the
        # intake bounds against this caller (see ``_intake_client``).
        client_token = _intake_client.set(request.client.host if request.client else None)
        try:
            await state.auth.backends[0].begin_login(email, base_url=state.public_base_url)
        finally:
            _intake_client.reset(client_token)
        return JSONResponse({"ok": True})

    @app.options("/api/v1/access-requests")
    async def _access_request_preflight(request: Request) -> Response:
        if not _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        return _intake_cors(request, Response(status_code=204))

    @app.post("/api/v1/access-requests", status_code=202)
    async def _access_request(payload: AccessRequestBody, request: Request) -> Response:
        """Ask for a hosted account. Always the same 202 whatever the email's
        state, so the route cannot tell anyone which emails have accounts.
        Nothing is ever mailed to the requester; a new request alerts the
        admins."""
        if not _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        # Lazy: splitsmith.db is hosted-only (a slim local install lacks it).
        from ..db.access_requests import valid_request_email

        if not valid_request_email(payload.email):
            raise HTTPException(status_code=400, detail="a valid email is required")
        email = payload.email.strip()
        # Over the intake bounds (5 an hour per client address, 50 in total,
        # shared with sign-in; see ``_record_and_alert``) nothing is recorded
        # and the reply is the same.
        if not payload.hp:
            try:
                await state.record_access_request(
                    email,
                    client=request.client.host if request.client else None,
                    source=payload.source,
                    note=payload.note,
                )
            except Exception:
                # A failed write must not answer differently either.
                logger.exception("recording an access request failed")
        return _intake_cors(request, JSONResponse({"ok": True, "message": INTAKE_MESSAGE}, status_code=202))

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
            issued = await state.auth.backends[0].complete_login(
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
        _set_session_cookie(state, response, issued.secret, issued.expires_at)
        return response

    @app.post("/api/v1/auth/logout")
    async def _auth_logout(request: Request, user: User = Depends(get_current_user)) -> JSONResponse:
        """Revoke the current session + clear the cookie. Auth-gated, so an
        anonymous caller 401s before reaching here."""
        from ..db import SESSION_COOKIE_NAME

        cookie = request.cookies.get(SESSION_COOKIE_NAME)
        if cookie:
            await state.auth.backends[0].end_session(cookie)
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

    # ----------------------------------------------------------------------
    # Share-link management routes (issue #349)
    # Hosted-only: raise 404 in local mode (same mechanism as auth routes).
    # Reached via the /api/matches/{match_id}/match/shares alias prefix so
    # current_match_id is already set by the alias middleware.
    # ----------------------------------------------------------------------

    # ----------------------------------------------------------------------
    # Timestamped comments (public share surface, #comments)
    # Reached via the /api/matches/{match_id}/... alias prefix, same as
    # /api/match/shares - both the owner (authenticated) and an anonymous
    # caller (through _share_alias's rewrite) hit the same three routes.
    # ----------------------------------------------------------------------

    # Constructed once per app, not per request: a per-request limiter
    # would reset on every call and limit nothing. Each test builds a
    # fresh app (see hosted_app in tests/hosted_helpers.py), which is
    # what keeps rate-limit state from leaking between test cases.
    _comment_limiter = CommentRateLimiter()
    app.state.comment_limiter = _comment_limiter

    # ----------------------------------------------------------------------
    # Match sync trigger/status (desktop-to-hosted sync MVP, #631 Task 9)
    # Local-only: raise 404 in hosted mode (same inverse guard as the
    # settings routes above). Reached via the /api/matches/{match_id}/
    # alias prefix, same as /api/match/shares, so current_match_root is
    # already set by the time these run.
    # ----------------------------------------------------------------------

    @app.put("/api/settings/auto-sync")
    async def put_global_auto_sync(req: GlobalAutoSyncRequest) -> JSONResponse:
        """The machine-level switch in the hosted-sync settings dialog."""
        if _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        prefs = user_config.load_global_prefs()
        prefs.auto_sync_enabled = req.enabled
        user_config.save_global_prefs(prefs)
        return JSONResponse({"global_enabled": prefs.auto_sync_enabled})

    @app.get("/api/settings/scrub")
    async def get_scrub_settings() -> JSONResponse:
        """Whether the Audit screen scrubs the full-resolution trim (#1192)."""
        if _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        return JSONResponse({"full_res_scrub": user_config.load_global_prefs().full_res_scrub})

    @app.put("/api/settings/scrub")
    async def put_scrub_settings(req: ScrubSettingsRequest) -> JSONResponse:
        """The Audit transport menu's "Full-resolution video" switch."""
        if _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        prefs = user_config.load_global_prefs()
        prefs.full_res_scrub = req.full_res_scrub
        user_config.save_global_prefs(prefs)
        return JSONResponse({"full_res_scrub": prefs.full_res_scrub})

    @app.exception_handler(ShutdownInProgressError)
    async def _shutdown_in_progress_handler(request: Request, exc: ShutdownInProgressError) -> JSONResponse:
        """Map ShutdownInProgressError to 503 across every submit() callsite."""
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": "shutting_down", "message": str(exc)}},
        )

    @app.exception_handler(FeatureRequiredError)
    async def _feature_required_handler(_request: Request, exc: FeatureRequiredError) -> JSONResponse:
        """Map the job backend's account-feature backstop to the gate's 403 body."""
        return JSONResponse(
            status_code=403, content={"detail": {"code": "feature_required", "feature": exc.feature.value}}
        )

    @app.exception_handler(RequestValidationError)
    async def _worker_validation_gate(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Return the uniform 404 for malformed /api/workers/register bodies.

        Any other path gets the 422 FastAPI would have produced, with one
        difference: the error detail is passed through ``_json_safe``
        first. See that helper for why an anonymous caller could
        otherwise force a 500 out of this handler.
        """
        if request.url.path == "/api/workers/register":
            return JSONResponse(status_code=404, content={"detail": "not found"})
        return JSONResponse(status_code=422, content={"detail": _json_safe(jsonable_encoder(exc.errors()))})

    @app.exception_handler(AuditRevisionConflictError)
    async def _audit_revision_conflict_handler(request: Request, exc: Exception) -> JSONResponse:
        """Same body as the hosted optimistic-lock 409, so every client
        handles both with one branch."""
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "code": "version_conflict",
                    "message": "this match state changed since you loaded it; reload and try again",
                }
            },
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
                        "message": ("this match state changed since you loaded it; reload and try again"),
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
        if owner_store is not None and not match_model.is_valid_match_id(match_id):
            return JSONResponse(
                status_code=404,
                content={
                    "detail": {
                        "code": "match_not_found",
                        "message": f"unknown match_id {match_id!r}",
                    }
                },
            )
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
            owner_row = await owner_store.get(match_id)
            if owner_row is None:
                return JSONResponse(
                    status_code=404,
                    content={
                        "detail": {
                            "code": "match_not_found",
                            "message": f"unknown match_id {match_id!r}",
                        }
                    },
                )
            # Capability gate (#756, formerly the read-only mirror gate,
            # #631 Task 6). One table decides both this 403 and the
            # ``capabilities`` payload field - see capabilities.py. On a
            # share request the set comes from the token's scope instead
            # of the origin (#779). Share traffic is no longer GET-only:
            # a comment-scoped token's admitted POST/DELETE reach this
            # gate too, and ``_COMMENT_ROUTES`` in capabilities.py maps
            # them to COMMENT_WRITE - unmapped, they would refuse with a
            # 403 that enumerates the write allowlist against the share
            # alias's uniform 404s.
            if current_share_request.get():
                from ..db.share_guard import current_share_scope

                match_capabilities = share_scope_capabilities(current_share_scope.get())
            else:
                match_capabilities = capabilities_for_origin(
                    owner_row.origin, merges_events=bool(getattr(owner_row, "desktop_merges_events", False))
                )
            needed = required_capability(request.method, rest)
            if needed is not None and needed not in match_capabilities:
                return JSONResponse(status_code=403, content={"detail": "read_only_mirror"})
            work_root = _hosted_match_work_root(owner_row.user_id, match_id)
            work_root.mkdir(parents=True, exist_ok=True)
            state.matches.register(match_id, work_root)
            match_root = work_root.resolve()
            match_origin = owner_row.origin
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
            match_origin = "local"
            match_capabilities = capabilities_for_origin("local")
        rewritten = "/api/" + rest
        request.scope["path"] = rewritten
        request.scope["raw_path"] = rewritten.encode("utf-8")
        root_token = current_match_root.set(match_root)
        id_token = current_match_id.set(match_id)
        origin_token = current_match_origin.set(match_origin)
        capabilities_token = current_match_capabilities.set(match_capabilities)
        try:
            return await call_next(request)
        finally:
            current_match_capabilities.reset(capabilities_token)
            current_match_origin.reset(origin_token)
            current_match_id.reset(id_token)
            current_match_root.reset(root_token)

    # Desktop auto-sync dirty tracking (spec 2026-09-27 s2): a successful
    # write under /api/matches/{id}/ means that match has something to push.
    # Registered after ``_match_id_alias`` so it runs outside it and sees
    # the prefixed path. Local only (the service is None hosted).
    if state.auto_sync is not None:
        auto_sync_service = state.auto_sync

        @app.middleware("http")
        async def _auto_sync_dirty(request, call_next):
            # Read the path first: the alias middleware inside rewrites the
            # shared scope, so after call_next it no longer has the prefix.
            m = _MATCH_WRITE_RE.match(request.url.path)
            response = await call_next(request)
            if m and request.method in ("POST", "PUT", "PATCH", "DELETE") and response.status_code < 400:
                if write_marks_dirty(m.group(2)):
                    auto_sync_service.mark_dirty(m.group(1))
            return response

    # ----------------------------------------------------------------------
    # Share-link anonymous read middleware (issue #349)
    # ----------------------------------------------------------------------
    #
    # Required execution order: ``_auth_gate`` (outer) -> ``_share_alias``
    # (this) -> ``_match_id_alias`` (inner). Starlette runs later-registered
    # ``@app.middleware("http")`` OUTER, so this must be *defined* between
    # ``_match_id_alias`` (above) and ``_auth_gate`` (below). Rationale:
    #   - It runs INSIDE the gate so the gate's ``/api/share/`` exemption
    #     lets an anonymous request through, but a garbage token still 404s
    #     here rather than 401ing at the gate.
    #   - It runs OUTSIDE ``_match_id_alias`` so the path rewrite this
    #     middleware performs (onto ``/api/matches/{match_id}/...``) is what
    #     the alias then validates + strips, and the owner tenant pinned
    #     here is in scope for the alias's RLS-scoped ownership check.
    #
    # Security invariants: the match id comes ONLY from the resolved token
    # row (never the URL); every non-token / non-whitelisted-shape /
    # disallowed-method / local-mode / write-capability-missing case
    # returns the same opaque 404, so unknown, revoked, and expired
    # tokens - and a read-scoped token probing the write surface - are
    # all indistinguishable. GET admits through _SHARE_PATH_RE; POST and
    # DELETE admit only through the separate method-paired
    # _SHARE_WRITE_ROUTES table (via _share_write_admits), and only when
    # the resolved token's scope is write-capable.
    @app.middleware("http")
    async def _share_alias(request, call_next):
        path = request.url.path
        prefix = "/api/share/"
        if not path.startswith(prefix):
            return await call_next(request)
        not_found = JSONResponse(status_code=404, content={"detail": "not found"})
        resolver = state.resolve_share_token
        if resolver is None:
            # Local mode: no share surface at all.
            return not_found
        token, sep, rest = path[len(prefix) :].partition("/")
        if not sep or not token:
            return not_found
        method = request.method
        if method == "GET":
            if not _SHARE_PATH_RE.fullmatch(rest):
                return not_found
            needs_write_scope = False
        elif method in ("POST", "DELETE"):
            if not _share_write_admits(method, rest):
                return not_found
            needs_write_scope = True
        else:
            return not_found
        resolved = await resolver(token)
        if resolved is None:
            return not_found
        if needs_write_scope:
            # Lazy import for the same reason the share_guard import below
            # is lazy: this middleware runs on every request in local mode
            # too, where splitsmith.db may not be installed.
            from ..db.share_guard import scope_may_write

            if not scope_may_write(resolved.scope):
                return not_found
        # Rewrite onto the match-alias prefix with the match id from the
        # token row - never from the URL - and pin the owner's tenant so
        # the downstream ownership check + stores resolve as the owner.
        rewritten = f"/api/matches/{resolved.match_id}/{rest}"
        request.scope["path"] = rewritten
        request.scope["raw_path"] = rewritten.encode("utf-8")
        # Lazy import: this middleware runs for every request in both local
        # and hosted mode (local returns 404 above via ``resolver is None``),
        # so a module-level import here would force ``splitsmith.db`` - and
        # its sqlalchemy dependency - onto local-mode installs that don't
        # have the ``hosted`` extra. By this point the
        # resolver already came from ``_apply_hosted_mode_wiring``, which
        # has imported the db package, so this is a free (cached) import.
        from ..db.share_guard import current_share_scope

        # Best-effort only: an absent, expired or malformed session is an
        # anonymous visitor, never an error. Wrapped because the auth
        # backend may raise on a garbage cookie and a share page must not
        # 500 for it.
        viewer = None
        if needs_write_scope:
            try:
                viewer = await state.auth.authenticate_request(request)
            except Exception:  # noqa: BLE001
                viewer = None
            # A credential confined to some other surface (#719's
            # token_scope - a sync-scoped desktop token today, and any
            # future scope tomorrow) must not name a comment either, for
            # the same reason _auth_gate confines it: the share token is
            # what authorizes the write, and a bearer token that isn't
            # even allowed onto this surface must not get to pick who it
            # posts as. Same spelling _auth_gate uses at "user.token_scope
            # not in (None, "full")" - not a second one.
            if viewer is not None and viewer.token_scope not in (None, "full"):
                viewer = None
        viewer_token = current_share_viewer.set(viewer)

        owner_tenant = state.build_tenant(resolved.owner_user_id)
        tenant_token = current_tenant.set(owner_tenant)
        looks_token = looks_module.set_user_looks_provider(tenant_looks_provider(owner_tenant))
        share_token = current_share_request.set(True)
        cameras_token = current_share_cameras.set(resolved.cameras)
        scope_token = current_share_scope.set(resolved.scope)
        # Stashed on request.state (not a ContextVar) because only the two
        # OG PNG handlers need it and they already take ``request`` -- same
        # precedent as ``_auth_gate``'s ``request.state.user``. The card
        # PNG cache key is scoped to this token (share_card_render.storage_key),
        # and the raw token is gone from the URL by the time a route handler
        # runs: this middleware rewrites onto /api/matches/{match_id}/{rest},
        # then _match_id_alias rewrites again onto /api/{rest}, which is the
        # path every route in _SHARE_PATH_RE (og.png included) is actually
        # registered under.
        request.state.share_token = token
        request.state.share_token_id = resolved.share_token_id
        try:
            response = await call_next(request)
            # Uniform-404 seam: a live token whose underlying resource no
            # longer exists (e.g. match row deleted) must return the same
            # opaque 404 as an invalid token - not the downstream's own
            # error body which may distinguish the cases.
            if response.status_code == 404:
                return not_found
            # The allowlist matches the path only, so a malformed query
            # (``videos/stream`` with no ``path``, ``stage=abc``) still
            # reaches the route and FastAPI answers 422 - for a live token
            # only, while a bogus one gets the 404 above. On GET no 422 is
            # an answer a share viewer can use, so it joins the seam. The
            # write surface keeps its body-validation 422s for the compose
            # box: they need a comment-scoped token, which a 201 for a
            # well-formed post would reveal just as surely.
            if method == "GET" and response.status_code == 422:
                return not_found
            return response
        finally:
            current_share_viewer.reset(viewer_token)
            current_share_scope.reset(scope_token)
            current_share_cameras.reset(cameras_token)
            current_share_request.reset(share_token)
            looks_module.reset_user_looks_provider(looks_token)
            current_tenant.reset(tenant_token)

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
        if path.startswith("/api/share/"):
            # Anonymous share reads: authorization is the token itself,
            # resolved by the _share_alias middleware inside this gate. No
            # session is required (and none is consulted), so the gate hands
            # off without pinning current_tenant - _share_alias pins the
            # owner's tenant from the token row instead.
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
        # Account gate (spec 2026-10-03): an account with no features keeps
        # only "who am I" and sign-out. Read per request from the users row,
        # so a downgrade applies to the next request.
        if (
            hosted
            and not features_of(request, user)
            and (request.method, path) not in _DISABLED_ALLOWED_ROUTES
        ):
            return JSONResponse(status_code=403, content={"detail": {"code": "account_disabled"}})
        # Scope gate (#719). Allowlist, not a denylist: only None (session
        # cookie, loopback user) and "full" (legacy pasted token) are
        # unrestricted. Everything else - "sync" today, and any value a
        # future migration or scope introduces tomorrow - is confined to
        # the sync surface plus its own sign-out route. A typo'd or
        # unrecognized scope is denied on purpose rather than falling
        # open: an unrestricted-by-default reading of an unknown value
        # would be the wrong failure mode for a credential's reach.
        if (
            user.token_scope not in (None, "full")
            and not path.startswith("/api/sync/")
            and path != "/api/device/session"
        ):
            return JSONResponse(status_code=403, content={"detail": "token scope"})
        if not hosted:
            return await call_next(request)
        tenant = state.build_tenant(user.id)
        tenant_token = current_tenant.set(tenant)
        looks_token = looks_module.set_user_looks_provider(tenant_looks_provider(tenant))
        try:
            return await call_next(request)
        finally:
            looks_module.reset_user_looks_provider(looks_token)
            current_tenant.reset(tenant_token)

    # ----------------------------------------------------------------------
    # API
    # ----------------------------------------------------------------------

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Process-level health. Tier 1 step 4 of doc 10 retired
        the bound-state concept on the server -- match identity is
        per-request via the URL prefix, not per-process. The SPA
        relies on the URL for navigation; ``/api/health`` is now
        purely a "the server is up" check (plus version + status).
        """
        # The 6-hourly safety-net workflow curls this endpoint. When serve is
        # already awake (e.g. an open SSE tab) the lifespan does not re-run,
        # so this is the wake channel's awake-path: fire the boot re-check
        # without blocking the response. The internal 5-minute cooldown on
        # make_boot_retrigger prevents health pings from hammering the DB.
        if state.boot_retrigger is not None:
            asyncio.create_task(state.boot_retrigger())
        return HealthResponse(bound=False)

    # ------------------------------------------------------------------
    # Self-hosted workers: registration + SSE wake channel (issue: worker
    # fleet self-hosting). Both paths are in _PUBLIC_API_PATHS - the token
    # in the request is the auth, and every failure (local mode, unknown /
    # expired / replayed token, bad Authorization header) returns the same
    # opaque 404, the uniform-404 precedent from the share surface.
    # ------------------------------------------------------------------

    @app.post("/api/workers/register", response_model=WorkerRegisterResponse)
    async def workers_register(body: WorkerRegisterRequest) -> Any:
        """Single-use registration-token exchange.

        The box posts the registration token the operator created; on
        success it gets the long-lived worker token plus the connection
        material (DB URL, public URL, S3 credentials) it persists locally.
        Unknown, expired, and replayed tokens are indistinguishable.
        """
        store = state.workers_store
        if store is None or state.worker_credentials is None:
            return _workers_not_found()
        result = await store.register(body.token, body.info)
        if result is None:
            return _workers_not_found()
        record, worker_token = result
        return WorkerRegisterResponse(
            worker_id=record.id,
            worker_token=worker_token,
            credentials=state.worker_credentials,
        )

    @app.get("/api/workers/channel")
    async def workers_channel(request: Request) -> Response:
        """Long-lived SSE stream a registered worker holds open for wakes.

        Auth is the ``Authorization: Bearer <worker_token>`` header, parsed
        manually (the session gate exempts this path). On connect the
        worker's ``last_seen_at`` is stamped and the throttled boot
        re-check fires so jobs queued while the box was offline get
        dispatched immediately. Frame contract: see
        :func:`_worker_channel_events`.
        """
        store = state.workers_store
        registry = state.wake_channels
        if store is None or registry is None:
            return _workers_not_found()
        scheme, _, bearer = request.headers.get("authorization", "").partition(" ")
        token = bearer.strip()
        if scheme.lower() != "bearer" or not token:
            return _workers_not_found()
        record = await store.authenticate(token)
        if record is None:
            return _workers_not_found()
        reported_version = request.headers.get("x-splitsmith-agent-version") or None
        return StreamingResponse(
            _worker_channel_events(
                store,
                registry,
                record.id,
                disabled=not record.enabled,
                boot_retrigger=state.boot_retrigger,
                version=reported_version,
            ),
            media_type="text/event-stream",
            # ``X-Accel-Buffering: no`` asks nginx/edge proxies not to buffer
            # the stream; without it a buffering intermediary can coalesce the
            # 20s ``: ka`` keepalive and let the connection idle out, which
            # shows up agent-side as the "incomplete chunked read" reconnect
            # churn. A lost wake during such a flap is recovered by the
            # owed-wake replay on reconnect (see WakeChannelRegistry).
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

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

    # ------------------------------------------------------------------
    # Presigned multipart upload (#467): direct browser -> R2 for large
    # files. The single-shot endpoint above proxies bytes through this
    # process, which Cloud Run caps at 32 MiB per request. Here the
    # client PUTs parts straight to R2 via presigned URLs; serve only
    # mints the upload, signs parts, and finalizes. No bytes (and so no
    # server-side sha256) pass through serve -- attach treats sha256 as
    # optional, and R2's per-part ETags + the completed size are the
    # integrity signal. Hosted-only (503 when storage is unwired).
    # ------------------------------------------------------------------

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

    @app.get("/api/scoreboard/search")
    def scoreboard_search_unbound(q: str = Query("", min_length=0)) -> JSONResponse:
        """Search the live scoreboard without a bound shooter (#322).

        The create-match-from-scoreboard flow runs *before* a project
        exists, so there is no shooter root to scope a cache to. Search
        results aren't cached anyway -- ``CachingScoreboardClient`` only
        memoises ``get_match`` -- so we skip the cache wrapper entirely
        and go straight to the HTTP client.
        """
        client = SsiHttpClient()
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
        client = SsiHttpClient()
        with client:
            try:
                data = client.get_match(content_type, match_id)
            except MatchNotFound as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ScoreboardError as exc:
                _raise_scoreboard_http(exc)
        return JSONResponse(data.model_dump(mode="json"))

    # Routers outside this closure (footage_sort_api) queue beeps through
    # the same hook rather than a second copy of its rules.
    app.state.auto_queue_beep = functools.partial(_auto_queue_beep_if_needed, state)

    # ----------------------------------------------------------------------
    # Admin workers API (Task 7)
    #
    # Operator CRUD for the self-hosted worker fleet. All routes require
    # admin (403 non-admin). In local mode workers_store is None so the
    # combined gate returns 404 before the admin check fires.
    # ----------------------------------------------------------------------

    async def _gate_admin_workers(user: User = Depends(get_current_user)) -> User:
        """Combined gate: 404 in local mode (no workers surface), 403 for non-admin.

        Checks workers_store first so local mode returns 404 before the admin
        check has a chance to return 403. Hosted non-admins still see 403.
        """
        if state.workers_store is None:
            raise HTTPException(status_code=404, detail="not found")
        if user.email.lower() not in state.admin_emails:
            raise HTTPException(status_code=403, detail="admin access required")
        return user.model_copy(update={"is_admin": True})

    @app.get("/api/admin/workers", response_model=AdminWorkerListResponse)
    async def _admin_list_workers(
        _admin: User = Depends(_gate_admin_workers),
    ) -> AdminWorkerListResponse:
        """List all workers ordered by priority asc, name asc."""
        store = state.workers_store
        assert store is not None  # gate guarantees this
        records = await store.list()
        live, beats = await _worker_liveness(state, records)
        return AdminWorkerListResponse(
            workers=[_to_worker_view(r, live, beats.get(r.id)) for r in records],
            server_version=splitsmith_version,
        )

    @app.post("/api/admin/workers", response_model=AdminCreateWorkerResponse)
    async def _admin_create_worker(
        body: AdminCreateWorkerBody,
        _admin: User = Depends(_gate_admin_workers),
    ) -> AdminCreateWorkerResponse:
        """Create a pending self-hosted worker row and return the one-time registration token.

        409 when a worker with the same name already exists. The
        ``docker_command`` field is a ready-to-paste ``docker run`` invocation
        for the operator; the ``agent`` subcommand is implemented in Task 8.
        """
        from sqlalchemy.exc import IntegrityError

        store = state.workers_store
        assert store is not None
        try:
            record, reg_token = await store.create_self_hosted(body.name, priority=body.priority)
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="worker name already exists") from exc
        registry = state.wake_channels
        connected = registry.connected_ids() if registry is not None else frozenset()
        image = os.environ.get(SPLITSMITH_AGENT_IMAGE_ENV, SPLITSMITH_AGENT_IMAGE_DEFAULT)
        public_url = state.public_base_url or ""
        # ``--name splitsmith-agent`` so the ``docker logs -f splitsmith-agent``
        # step the admin UI shows next to this command resolves the container.
        # ``--stop-timeout``: on SIGTERM the agent finishes the job in flight
        # before exiting (#1033); Docker's 10 s default would SIGKILL it first.
        docker_command = (
            f"docker run -d --restart unless-stopped --stop-timeout 900 --name splitsmith-agent "
            f"-v splitsmith-agent:/data "
            f"{image} agent --server-url {public_url} --token {reg_token}"
        )
        expires_at = record.token_expires_at.isoformat() if record.token_expires_at is not None else ""
        return AdminCreateWorkerResponse(
            worker=_to_worker_view(record, connected),
            registration_token=reg_token,
            expires_at=expires_at,
            docker_command=docker_command,
        )

    @app.patch("/api/admin/workers/{worker_id}", response_model=WorkerView)
    async def _admin_patch_worker(
        worker_id: str,
        body: AdminPatchWorkerBody,
        _admin: User = Depends(_gate_admin_workers),
    ) -> WorkerView:
        """Update mutable worker fields (enabled, priority, name).

        When ``enabled`` changes value, pushes the corresponding event
        (``"enabled"`` or ``"disabled"``) to a connected channel so the
        worker daemon can react without waiting for the next keepalive.
        """
        store = state.workers_store
        assert store is not None
        current = await store.get(worker_id)
        if current is None:
            raise HTTPException(status_code=404, detail="not found")
        updated = await store.update(worker_id, enabled=body.enabled, priority=body.priority, name=body.name)
        if updated is None:
            raise HTTPException(status_code=404, detail="not found")
        registry = state.wake_channels
        if registry is not None and body.enabled is not None and body.enabled != current.enabled:
            event = "enabled" if body.enabled else "disabled"
            registry.push(worker_id, event)  # best-effort; returns False if not connected
        live, beats = await _worker_liveness(state, [updated])
        return _to_worker_view(updated, live, beats.get(updated.id))

    @app.delete("/api/admin/workers/{worker_id}", status_code=204)
    async def _admin_delete_worker(
        worker_id: str,
        _admin: User = Depends(_gate_admin_workers),
    ) -> Response:
        """Delete a self-hosted worker row.

        Pushes ``"disabled"`` to a connected channel first (best-effort) so
        the daemon parks before the row vanishes. Returns 400 for the
        platform worker row (cannot be deleted), 404 for an unknown id.
        """
        store = state.workers_store
        assert store is not None
        existing = await store.get(worker_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="not found")
        registry = state.wake_channels
        if registry is not None:
            registry.push(worker_id, "disabled")  # best-effort
        deleted = await store.delete(worker_id)
        if not deleted:
            # store.delete returns False only for the platform row
            raise HTTPException(status_code=400, detail="cannot delete the platform worker")
        return Response(status_code=204)

    # --- the event's logo (the branding work) -------------------------------
    #
    # The match's own logo, the centrepiece of the title page and the closing card.
    # Kept as ``<match>/identity/event-<hash>.<ext>``; hosted, also under
    # ``matches/<id>/identity/`` (the push writes the same key from the
    # desktop). An edit, so a desktop mirror refuses it like any other.

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

        Reads the calibration JSON directly rather than going through
        ``_get_ensemble_runtime`` (issue #667): this is a metadata read,
        and building the full runtime would make a dropdown fetch pay
        for the CLAP/PANN/voter-C session loads it never touches -- and
        would turn a missing model artifact into a 500 on a camera list
        that does not depend on any model.
        """
        calibration = ensemble_module.load_calibration()
        floors = calibration.amp_floor_by_camera_model or {}
        displays = calibration.camera_model_metadata or {}
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

    # ----------------------------------------------------------------------
    # Coach endpoints (#161). Read-only on shot timestamps -- all writes go
    # to the coaching annotation fields owned by ``splitsmith.coach``.
    # Storage is the same audit JSON Audit reads/writes. The coach is lazy:
    # GET surfaces the current rule's verdict + a stale flag without
    # mutating; reclassify persists auto entries; PATCH writes one shot's
    # coach fields.
    # ----------------------------------------------------------------------

    # ----------------------------------------------------------------------
    # Fixture-review endpoints (closes #19 -- the old splitsmith.review_server
    # standalone). The /review SPA route reads a single audit fixture (JSON
    # + sibling WAV + optional video) and edits it in place. No project
    # context, no stages, no jobs. Localhost-only convention applies; paths
    # are passed by the user via CLI / query string.
    # ----------------------------------------------------------------------

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
        # #843: same trap as the stage audit PUT, and worse here -- an
        # unguarded ``json.dumps`` writes the bare token, so the fixture on
        # disk stops being standard JSON for every other reader of it.
        _reject_non_finite(payload, what="fixture document")
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
        # Up to 1 ms bins on a two-minute fixture: the lab review zooms
        # until the shot's rising edge is a few pixels wide.
        bins: int = Query(default=1200, ge=16, le=131_072),
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

    @app.get("/api/fixture/review-signals")
    def get_fixture_review_signals(path: str = Query(...)) -> JSONResponse:
        """Where the Audit page's fixture review should stop (spec 2026-10-14,
        ``lab.review_signals``): suggested moves from the review inventory,
        snapped shots at their window's edge, kept sounds with no shot near
        them, and for a snap the shots it was snapped with. Missing inputs
        (no report, the inventory not built) are empty signals, not errors."""
        from ..lab.review_signals import review_signals

        target = _resolve_fixture_path(path)

        def load(p: Path) -> dict[str, Any] | None:
            try:
                body = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            return body if isinstance(body, dict) else None

        fixture = load(target)
        if fixture is None:
            raise HTTPException(status_code=500, detail=f"fixture read failed: {target.name}")
        report = load(target.with_name(f"{target.stem}-promotion-report.json"))
        inventory_body = load(target.parent.parent.parent / "build" / "fixture_review_inventory.json") or {}
        entry = next(
            (
                e
                for e in inventory_body.get("fixtures") or []
                if isinstance(e, dict) and e.get("slug") == target.stem
            ),
            None,
        )
        # The fixture's own audio at 1 ms, for the echo check (a published
        # file, read and enveloped in milliseconds; spec 2026-10-14).
        envelope = None
        wav = target.with_suffix(".wav")
        if wav.exists():
            from ..rise_foot import peak_envelope

            try:
                audio, sr = beep_detect.load_audio(wav)
                envelope = peak_envelope(audio, sr)
            except Exception as exc:  # noqa: BLE001 -- no audio is no echo check, never an error
                logger.info("review signals: could not read %s: %s", wav.name, exc)
        signals = review_signals(fixture, report=report, inventory=entry, envelope=envelope)
        return JSONResponse(signals.model_dump(mode="json"))

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
        return FileResponse(
            target,
            media_type=video_media_type(target),
            filename=target.name,
            content_disposition_type="inline",
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
        Permission errors surface as 403. Local mode only
        (``route_scope.LOCAL_ONLY_ROUTES``).
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

    # ----------------------------------------------------------------------
    # Global user config (#75)
    # ----------------------------------------------------------------------
    #
    # Cross-project state lives in ``~/.splitsmith/`` (override via
    # ``SPLITSMITH_HOME``; opt out with ``SPLITSMITH_DISABLE_USER_CONFIG=1``).
    # The endpoints below let the SPA read the recent-projects list for its
    # project picker and read/write the saved SSI Scoreboard identity so the
    # scoreboard import flow can prefill 'me' instead of asking each project.

    # ----------------------------------------------------------------------
    # Shooters management (#324)
    # ----------------------------------------------------------------------

    # ----------------------------------------------------------------------
    # Match merge (#332) -- consolidate N legacy single-shooter projects
    # into one redesign-era match folder via the SPA. Wraps the existing
    # match_model.plan_merge + execute_merge that the `splitsmith match
    # merge` CLI uses.
    # ----------------------------------------------------------------------

    # ----------------------------------------------------------------------
    # Stage compare (#328)
    # ----------------------------------------------------------------------

    # ----------------------------------------------------------------------
    # Cross-shooter beep review queue (#326)
    # ----------------------------------------------------------------------

    # ``/api/me/recent-projects/unbind`` was deleted in Tier 1
    # step 4 of doc 10. There is no server-side bound state to
    # clear; the SPA picker navigates between matches by URL.

    # ----------------------------------------------------------------------
    # Hosted-sync settings (desktop-to-hosted sync MVP, #631 Task 9)
    # Local-only: raise 404 in hosted mode (the inverse of the desktop-token
    # guard below - a hosted install has no "push to hosted" target of its
    # own). Not match-scoped: the hosted target is a per-install setting,
    # not a per-match one, so this stays outside the /api/matches/{id}/
    # alias.
    # ----------------------------------------------------------------------

    @app.get("/api/settings/hosted-sync", response_model=HostedSyncSettings)
    async def get_hosted_sync_settings() -> HostedSyncSettings:
        if _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        prefs = user_config.load_global_prefs()
        prefs = await _refresh_hosted_account(state, prefs)
        return HostedSyncSettings(
            base_url=prefs.hosted_base_url,
            token_set=bool(prefs.hosted_token),
            account=_account_info(prefs.hosted_account),
        )

    @app.put("/api/settings/hosted-sync", response_model=HostedSyncSettings)
    async def put_hosted_sync_settings(req: HostedSyncSettingsRequest) -> HostedSyncSettings:
        if _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        prefs = user_config.load_global_prefs()
        old_base_url = prefs.hosted_base_url
        old_token = prefs.hosted_token
        base_url_changed = old_base_url != req.base_url
        prefs.hosted_base_url = req.base_url
        # A token minted by one host is dead weight - and a live
        # credential - once this install points elsewhere (#737).
        # Best-effort revoke against the OLD host, then drop the local
        # copy. The ``token: null`` keeps-the-token contract is about
        # same-host resubmits and is untouched by this.
        if base_url_changed and old_token:
            if old_base_url:
                client = _build_device_client(old_base_url, token=old_token)
                try:
                    await run_in_threadpool(client.device_revoke_session)
                except (SyncClientError, httpx.HTTPError):
                    # Best-effort by design: the PUT response carries no
                    # revoke outcome, so an unreachable old host means the
                    # credential may stay live there with no in-app signal.
                    # Surfacing this in the settings dialog is a known gap
                    # noted on #737.
                    pass
                finally:
                    client.close()
            prefs.hosted_token = None
        # None keeps the stored token (the SPA resubmits base_url alone
        # without re-typing the secret); "" clears it; anything else
        # replaces it.
        if req.token is not None:
            prefs.hosted_token = req.token or None
        # The cached account belongs to the credential that earned it
        # (#719). Pasting a token through the Advanced disclosure, or
        # repointing this install from prod to staging, invalidates that
        # identity - and a chip that keeps naming the old account while
        # pushes go somewhere else is worse than no chip at all. Clear
        # it; the next device login (or a GET after one) fills it back in.
        if req.token is not None or base_url_changed:
            prefs.hosted_account = None
        user_config.save_global_prefs(prefs)
        # A device login in flight belongs to the host it was started
        # against. Repointing the install strands it: its device_code
        # only means something on the old host, so polling it on would
        # at best waste requests and at worst link this install to an
        # account on the host we just stopped pushing to. Drop it, so
        # the next start mints a code against the new target.
        if base_url_changed:
            async with state.device_flow_lock:
                state.device_flow = None
        return HostedSyncSettings(
            base_url=prefs.hosted_base_url,
            token_set=bool(prefs.hosted_token),
            account=_account_info(prefs.hosted_account),
        )

    @app.post("/api/settings/hosted-sync/device/start", response_model=DeviceStartResponse)
    async def start_device_login() -> DeviceStartResponse:
        """Begin a browser-assisted link. Local-only.

        Names the device after the host so the operator can tell two
        installs apart on the hosted account page. Holds
        ``device_flow_lock`` for the whole body (including the hosted
        round trip): a second start racing this one must not silently
        clobber the first's ``device_code`` while it's still live -
        instead it gets that same flow handed back, with
        ``resumed=True``.

        Resuming rather than rejecting is what keeps "cancel, then sign
        in again" from being a dead end: the local process is the only
        holder of the live ``device_code``, so a second start that
        refused would leave the operator with no code, no approve link
        and no way out short of waiting out the 10-minute TTL. The
        secret still never leaves this process - only the ``user_code``
        and the two verification URLs, which the hosted side prints on
        the approval screen anyway.
        """
        if _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        prefs = user_config.load_global_prefs()
        if not prefs.hosted_base_url:
            raise HTTPException(status_code=409, detail="hosted_base_url_not_set")
        async with state.device_flow_lock:
            existing = state.device_flow
            now = time.monotonic()
            if existing is not None and now < float(existing["expires_at"]):
                return DeviceStartResponse(
                    user_code=str(existing["user_code"]),
                    verification_uri=str(existing["verification_uri"]),
                    verification_uri_complete=str(existing["verification_uri_complete"]),
                    expires_in=max(0, int(float(existing["expires_at"]) - now)),
                    interval=int(float(existing["interval"])),
                    resumed=True,
                )
            client = _build_device_client(prefs.hosted_base_url)
            try:
                started = await run_in_threadpool(client.device_authorize, socket.gethostname())
            except (SyncClientError, httpx.HTTPError) as exc:
                raise HTTPException(
                    status_code=502, detail=f"could not reach the hosted server: {exc}"
                ) from exc
            finally:
                client.close()
            state.device_flow = {
                "device_code": started["device_code"],
                "user_code": started["user_code"],
                "verification_uri": started["verification_uri"],
                "verification_uri_complete": started["verification_uri_complete"],
                "interval": int(started.get("interval", 5)),
                "expires_at": time.monotonic() + int(started.get("expires_in", 600)),
                "last_polled_at": None,
                "last_status": "pending",
                "base_url": prefs.hosted_base_url,
            }
        return DeviceStartResponse(
            user_code=started["user_code"],
            verification_uri=started["verification_uri"],
            verification_uri_complete=started["verification_uri_complete"],
            expires_in=int(started.get("expires_in", 600)),
            interval=int(started.get("interval", 5)),
            resumed=False,
        )

    @app.get("/api/settings/hosted-sync/device/status", response_model=DeviceStatusResponse)
    async def get_device_status() -> DeviceStatusResponse:
        """Forward one poll upstream, at most once per hosted interval.

        The SPA polls faster than the hosted side wants; this throttle is
        what stops a fast-refreshing tab from tripping ``slow_down``. In
        between forwards it replays the cached verdict. The whole body
        runs under ``device_flow_lock`` (held across the hosted round
        trip) so two concurrent polls can't both pass the throttle check
        before either records ``last_polled_at`` - without it, both would
        forward to the hosted side, and whichever request's caller reads
        second would silently discard the approval the other one saw.
        """
        if _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        async with state.device_flow_lock:
            flow = state.device_flow
            if flow is None:
                return DeviceStatusResponse(status="idle")
            now = time.monotonic()
            if now >= float(flow["expires_at"]):
                state.device_flow = None
                return DeviceStatusResponse(status="expired")
            last = flow["last_polled_at"]
            if last is not None and now - float(last) < float(flow["interval"]):
                return DeviceStatusResponse(status=str(flow["last_status"]))

            client = _build_device_client(str(flow["base_url"]))
            try:
                verdict = await run_in_threadpool(client.device_poll, str(flow["device_code"]))
            except (SyncClientError, httpx.HTTPError) as exc:
                raise HTTPException(
                    status_code=502, detail=f"could not reach the hosted server: {exc}"
                ) from exc
            finally:
                client.close()
            flow["last_polled_at"] = now
            status = str(verdict.get("status", "pending"))
            # slow_down means we polled upstream too fast despite the
            # throttle (clock skew, a restarted hosted process). Nothing
            # has changed for the operator, so report it as pending
            # rather than inventing a sixth UI state.
            if status == "slow_down":
                status = "pending"
            flow["last_status"] = status

            if status == "approved":
                account = verdict.get("account") or {}
                prefs = user_config.load_global_prefs()
                prefs.hosted_token = verdict.get("token")
                prefs.hosted_account = user_config.HostedAccountRef(
                    id=str(account.get("id", "")),
                    email=str(account.get("email", "")),
                    # The initial snapshot. display_name is typically
                    # NULL at this moment -- an account sets its name on
                    # the web afterwards -- so this is a starting point,
                    # not the last word. GET /api/settings/hosted-sync
                    # refreshes it from /api/sync/whoami (#877).
                    display_name=account.get("display_name"),
                    device_name=str(verdict.get("device_name") or socket.gethostname()),
                    linked_at=datetime.now(UTC),
                )
                user_config.save_global_prefs(prefs)
                state.device_flow = None
                return DeviceStatusResponse(
                    status="approved",
                    account=_account_info(prefs.hosted_account),
                    device_name=prefs.hosted_account.device_name,
                )
            if status in ("denied", "expired"):
                state.device_flow = None
            return DeviceStatusResponse(status=status)

    @app.delete("/api/settings/hosted-sync/session", response_model=DeviceUnlinkResponse)
    async def unlink_hosted_account() -> DeviceUnlinkResponse:
        """Unlink this install: revoke upstream, then clear local prefs.

        The local copy is cleared even when the hosted call fails
        (offline, or the token was already revoked from the account
        page). Leaving a dead token in config.yaml because the network
        was down is the worse failure; ``hosted_revoked=False`` is what
        lets the UI say the local copy is gone and point at the account
        page for certainty. ``hosted_base_url`` survives - it is how the
        operator points an install at staging.
        """
        if _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        prefs = user_config.load_global_prefs()
        revoked: bool | None = None
        if prefs.hosted_base_url and prefs.hosted_token:
            client = _build_device_client(prefs.hosted_base_url, token=prefs.hosted_token)
            try:
                await run_in_threadpool(client.device_revoke_session)
                revoked = True
            except (SyncClientError, httpx.HTTPError):
                revoked = False
            finally:
                client.close()
        prefs.hosted_token = None
        prefs.hosted_account = None
        user_config.save_global_prefs(prefs)
        async with state.device_flow_lock:
            state.device_flow = None
        return DeviceUnlinkResponse(cleared=True, hosted_revoked=revoked)

    # ----------------------------------------------------------------------
    # Desktop command queue, phone side (#1100, spec 2026-09-28). Hosted
    # only. Requests are for mirrors: a hosted-native match has hosted
    # compute. The desktop side lives in sync_api.py under /api/sync.
    # ----------------------------------------------------------------------

    # ----------------------------------------------------------------------
    # Desktop-token management routes (desktop-to-hosted sync MVP, #631)
    # Hosted-only: raise 404 in local mode (same mechanism as share links -
    # desktop sync has no meaning against an ephemeral local install). The
    # store is resolved per-request from state.desktop_tokens, which reads
    # the authenticated user's TenantContext (built with the tenant-scoped
    # session factory - see _apply_hosted_mode_wiring / TenantContext).
    # ----------------------------------------------------------------------

    @app.get("/api/server/features")
    def server_features() -> JSONResponse:
        """Surface server-side feature flags to the SPA on first load.

        Fields:

        - ``lab`` -- the developer-facing dev-mode Lab surfaces (corpus,
          fixture detail, validate, retrain); off by default, opt-in
          via ``splitsmith ui --lab``. The SPA hides the Lab nav entry
          when this is False so end users don't trip into a
          multi-second model-loading workflow they didn't ask for.
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
            """Corpus listing, augmented with calibration membership.

            ``in_calibration`` compares each slug against the active
            artifact's ``calibration_fixtures`` (respects
            ``SPLITSMITH_ARTIFACTS_DIR``), so the Corpus page can show
            which fixtures the shipped model was actually built from
            vs. the ones still waiting on a retrain.
            """
            try:
                calibrated = set(ensemble_module.load_calibration().calibration_fixtures)
            except Exception:
                calibrated = set()
            records = []
            for r in lab_module.list_fixtures():
                d = r.model_dump(mode="json")
                d["in_calibration"] = r.slug in calibrated
                records.append(d)
            return JSONResponse(records)

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
                cached = _lab_universe_cache.get("last_run")
                if wanted_slugs is not None and cached is not None and cached.config_hash == run.config_hash:
                    # Scoped re-eval of a subset: fold it into the cached run so a
                    # full Validate universe survives per-fixture labeling evals.
                    fresh = {f.slug for f in run.universe.fixtures}
                    keep = [f for f in cached.universe.fixtures if f.slug not in fresh]
                    merged = cached.universe.model_copy(
                        update={"fixtures": keep + list(run.universe.fixtures)}
                    )
                    run = lab_module.rescore_universe(merged, cfg)
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

        @app.post("/api/lab/promote-drift")
        def lab_promote_drift(payload: dict[str, Any] = Body(...)) -> JSONResponse:  # noqa: B008
            """For each ``{shooter_slug, stage_number, slug}`` row of the
            Promote stages panel: how the existing fixture ``slug`` compares
            with the stage's audit now (``lab.core.audit_drift``), or
            ``null`` when there is no fixture or no audit to compare."""
            rows = payload.get("rows")
            if not isinstance(rows, list):
                raise HTTPException(status_code=400, detail="payload must include a list 'rows'")
            root = lab_module.core.DEFAULT_FIXTURES_ROOT
            out: list[dict[str, Any] | None] = []
            for row in rows:
                drift = None
                if isinstance(row, dict):
                    slug, shooter, n = row.get("slug"), row.get("shooter_slug"), row.get("stage_number")
                    fixture = root / f"{slug}.json" if isinstance(slug, str) and "/" not in slug else None
                    if (
                        fixture is not None
                        and fixture.exists()
                        and isinstance(shooter, str)
                        and isinstance(n, int)
                    ):
                        try:
                            audit_path = state.materialize_audit(shooter, n)
                            fixture_doc = json.loads(fixture.read_text(encoding="utf-8"))
                            audit_doc = json.loads(audit_path.read_text(encoding="utf-8"))
                            drift = lab_module.core.audit_drift(fixture_doc, audit_doc).model_dump()
                        except (HTTPException, OSError, ValueError, KeyError):
                            drift = None
                out.append(drift)
            return JSONResponse({"drift": out})

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
            # ``slug`` names the fixture (the SPA sends the
            # ``stage-shots-...`` convention); ``shooter_slug`` resolves
            # the project. They were one field when a "project" and a
            # "shooter" were the same thing -- post Tier 1 the shooter
            # lookup is strict, so a fixture slug in the lookup position
            # 404s. The fallback keeps old single-slug clients (where
            # both strings coincide) working.
            shooter_slug = payload.get("shooter_slug")
            if shooter_slug is not None and not isinstance(shooter_slug, str):
                raise HTTPException(status_code=400, detail="'shooter_slug' must be a string")
            shooter_slug = shooter_slug or slug
            project = state.shooter_project(shooter_slug)
            try:
                stg = project.stage(stage_n)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            audit_json = state.materialize_audit(shooter_slug, stage_n)
            audit_audio = _resolve_audit_audio(state, shooter_slug, project, stage_n)
            if isinstance(audit_audio, JSONResponse):
                # Being made by a job (it reads, never cuts): try again shortly.
                raise HTTPException(
                    status_code=409, detail="the stage's audit WAV is being prepared; try again"
                )
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
                        f"stage {stage_n} primary has no beep_time; detect or set the beep before promoting"
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
                    detail=(f"stage {stage_n} has no time_seconds; cannot compute the trim window."),
                )
            source_video_path = project.resolve_video_path(state.shooter_root(shooter_slug), primary.path)
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

                def _cal_snapshot() -> dict[str, Any] | None:
                    # Summarise the currently-active calibration. Taken
                    # before AND after the build so the Retrain page can
                    # show a truthful previous-vs-new strip -- the build
                    # overwrites the shipped artifacts in place, so
                    # "before" exists nowhere else once it finishes.
                    try:
                        cal = ensemble_module.load_calibration()
                    except Exception:
                        return None
                    return {
                        "built_at": cal.built_at,
                        "fixture_count": len(cal.calibration_fixtures),
                        "target_recall": cal.voter_c_target_recall,
                        "metrics_by_class": cal.voter_c_metrics_by_camera_class,
                    }

                before = _cal_snapshot()
                build_mod.build_artifacts(
                    fixtures=fixtures if fixtures else None,
                    target_recall=target_recall,
                    tolerance_ms=tolerance_ms,
                    log=log,
                )
                # Drop the cached runtime so the next eval reloads the new
                # calibration JSON + GBDT model.
                _lab_runtime_cache.pop("runtime", None)
                handle.set_result({"before": before, "after": _cal_snapshot()})
                handle.update(progress=1.0, message="calibration rebuilt")

            state.jobs.bodies.register("rebuild_calibration", _run)
            job = await state.jobs.submit(kind="rebuild_calibration")
            return JSONResponse(job.model_dump(mode="json"))

        # Promote-from-anchor (issue #125); the body model is module-level so the
        # OpenAPI document can resolve it (a class inside this function cannot be).
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
            project, stage, video = _resolve_stage_video(state, slug, stage_number, video_id)
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
                    detail=("this project has no SSI shooter pinned; cannot resolve anchor fixture slug."),
                )
            anchor_token = lab_module.shooter_token(project.selected_shooter_id)
            name_slug = slugify(project.name, fallback="stage")
            primary_slug = f"stage-shots-{name_slug}-stage{stage_number}-{anchor_token}"
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
            ensure_source_reachable(stage_number, source)

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
                    detail=(f"fixture already exists: {target_json.name} (set overwrite=true to replace)"),
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

                # Clip-local, like every primary fixture: without the trim
                # the audit screen renders the entire raw recording.
                handle.update(progress=0.88, message="trimming clip audio...")
                lab_module.write_promoted_fixture(
                    fixture_data=result.fixture_data,
                    promotion_report=result.promotion_report,
                    secondary_wav=secondary_wav_path,
                    fixtures_root=fixtures_root,
                    slug=fixture_slug,
                    trim_wav=_trim_wav_to_clip,
                    overwrite=True,
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
            project, _stage, video = _resolve_stage_video(state, slug, stage_number, video_id)
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
            ensure_source_reachable(stage_number, source)

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
                    detail=(f"fixture already exists: {target_json.name} (set overwrite=true to replace)"),
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

                handle.update(progress=0.88, message="trimming clip audio...")
                lab_module.write_promoted_fixture(
                    fixture_data=result.fixture_data,
                    promotion_report=result.promotion_report,
                    secondary_wav=secondary_wav_path,
                    fixtures_root=fixtures_root,
                    slug=slug,
                    trim_wav=_trim_wav_to_clip,
                    overwrite=True,
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
        # "review" bucket counts fixtures that entered the corpus without
        # a human label pass -- promote-from-anchor (anchor_slug set) and
        # batch promote-stages (promoted_at, no labels yet) both qualify.
        # See FixtureRecord.needs_review for the exact rule.
        review_count = sum(1 for f in fixtures if f.needs_review)
        return DeveloperModelInfo(
            active_version=version,
            recall=cal.voter_c_target_recall,
            precision=None,
            f1=None,
            fixture_count=len(cal.calibration_fixtures),
            built_at=cal.built_at,
            metrics_by_class=cal.voter_c_metrics_by_camera_class,
            step_counts=DeveloperStepCounts(
                corpus=len(fixtures),
                review=review_count,
                validate_runs=0,
                retrain=0,
            ),
        )

    @app.get("/api/dev/models")
    def dev_models(model: str = Query("shipped", max_length=200)) -> dict[str, Any]:
        """The lab's Models page: every fixture, the model it feeds, its
        parameters, the held-out splits and the shipped versions
        (``lab.model_map``). ``model`` is ``shipped`` or a bundle under
        ``build/models`` (the payload's ``models`` lists them); any other
        value is a 404. Read-only, assembled from files on disk."""
        from ..lab.model_map import load_model_map
        from ..runtime import runtime as _artifact_runtime

        try:
            payload = load_model_map(_artifact_runtime().artifact("ensemble_calibration.json"), model)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"no model {model!r}") from None
        return payload.model_dump()

    @app.get("/api/dev/review-queue", response_model=DevReviewQueueResponse)
    def dev_review_queue() -> DevReviewQueueResponse:
        """Bucket fixtures into pending / flagged / done for the review queue.

        Pending is ``FixtureRecord.needs_review``: anchor-promoted
        fixtures (await the diff-confirm screen) and batch-promoted
        fixtures with no human labels yet (await a label pass on the
        fixture detail page). Explicit ``review_flagged`` field on the
        fixture JSON = flagged; all else = done. The flagged bucket is
        rarely populated today but the shape is here so the UI doesn't
        need a follow-up wire.
        """
        fixtures = _lab_for_dev.list_fixtures()
        now = datetime.now(UTC).timestamp()
        pending: list[DevReviewQueueItem] = []
        flagged: list[DevReviewQueueItem] = []
        done: list[DevReviewQueueItem] = []
        for fx in fixtures:
            age = int(now - fx.audit_mtime) if fx.audit_mtime else None
            promoted = fx.anchor_slug is not None or fx.promoted_at is not None
            item = DevReviewQueueItem(
                slug=fx.slug,
                audit_path=fx.audit_path,
                source="match" if promoted else "ad-hoc",
                source_label="Promoted from match" if promoted else "Audited",
                status="pending" if fx.needs_review else "done",
                n_shots=fx.n_shots,
                n_disagreements=0,
                promoted_at=fx.promoted_at,
                venue=_venue_from_slug(fx.slug),
                stage_number=_stage_from_slug(fx.slug),
                shooter=_shooter_from_slug(fx.slug),
                age_seconds=age,
                review_status="needs_review" if fx.review_status == "needs_review" else "reviewed",
                priority=fx.review_priority,
                reasons=fx.review_reasons,
                video_path=(fx.source_video if fx.source_video and Path(fx.source_video).is_file() else None),
                walk_decisions=fx.walk_decisions,
            )
            if item.status == "pending":
                pending.append(item)
            else:
                done.append(item)
        # Pending: shot times that need checking first, most doubtful first
        # (the inventory's priority), then label passes by age (newest
        # first). Done alphabetically so the corpus is browsable.
        pending.sort(
            key=lambda x: (
                x.review_status != "needs_review",
                -(x.priority or 0.0),
                x.age_seconds or 0,
            )
        )
        done.sort(key=lambda x: x.slug)
        return DevReviewQueueResponse(pending=pending, flagged=flagged, done=done)

    @app.post("/api/dev/review-queue/{slug}/confirm")
    def dev_review_confirm(slug: str, body: DevReviewConfirmBody | None = None) -> JSONResponse:
        """The queue's "Approve to corpus": stamp ``review.confirmed_at``
        on the fixture JSON so ``needs_review`` clears without a label
        pass. Registered alongside the other always-available /api/dev
        endpoints -- the queue itself renders without ``--lab``. ``method``
        (the review walk's version) is recorded with the sign-off.
        """
        fx = next((f for f in _lab_for_dev.list_fixtures() if f.slug == slug), None)
        if fx is None:
            raise HTTPException(status_code=404, detail=f"unknown fixture: {slug}")
        stamp = _lab_for_dev.confirm_review(Path(fx.audit_path), method=body.method if body else None)
        return JSONResponse({"slug": slug, "confirmed_at": stamp, "status": "done"})

    # Desktop-to-hosted sync router (#631). Included after every middleware
    # above is registered so /api/sync/* passes through the same auth gate
    # as the rest of /api/*. Its own imports are lazy where they'd pull in
    # the hosted-only db deps, so registering it here is safe on a local-
    # slim install too - every route just 404s outside hosted mode (see
    # sync_api._hosted_gate).
    from .sync_api import router as sync_router

    app.include_router(sync_router, dependencies=[Depends(require_feature(Feature.sync))])

    # Browser-assisted device authorization router (#719). Same lazy-import
    # / always-registered idiom as sync_router above: db imports stay
    # inside device_auth_api.py, so a local-slim install still imports and
    # registers this router safely, and every route 404s outside hosted
    # mode (see device_auth_api._hosted_gate).
    from .device_auth_api import router as device_router

    app.include_router(device_router)

    # Admin routes for access requests and account tiers (spec
    # 2026-10-03). Same lazy-import / always-registered idiom as
    # sync_router above: db imports stay inside admin_access_api.py, and
    # every route 404s outside hosted mode (see admin_access_api._admin_gate).
    from .admin_access_api import router as admin_access_router

    app.include_router(admin_access_router)

    # Issue #1000: the local YouTube surface (settings, connect, upload).
    # Gated the other way round from device_router: ``_local_gate`` inside
    # youtube_api works in both modes: the file under the user config dir
    # locally, the account's row hosted (issue #1000, phase 2).
    from .youtube_api import router as youtube_router

    app.include_router(youtube_router)

    # Chromium probe + install for the desktop app (local only; hosted
    # answers 404). The job body is registered in register_job_bodies.
    from .system_api import router as system_router

    app.include_router(system_router)

    # Export presets (spec 2026-09-15 s1): one router for both modes; the
    # store behind ``state.export_presets`` is what differs.
    from .export_presets_api import router as export_presets_router
    from .me_identity_api import router as me_identity_router
    from .palette_api import router as palette_router
    from .whats_new_api import router as whats_new_router

    app.include_router(export_presets_router)
    app.include_router(me_identity_router)
    app.include_router(whats_new_router)
    app.include_router(palette_router)

    # Sort a shared footage folder across shooters (spec 2026-10-01).
    # Local only: every route 404s hosted.
    from .footage_sort_api import router as footage_sort_router

    app.include_router(footage_sort_router)

    # The rail's real-match preview (spec 2026-09-15 s3): a PNG per card.
    from .export_preview_api import router as export_preview_router

    app.include_router(export_preview_router)

    # The Look catalog and its preview files (spec 2026-10-06 s4, #1246).
    from .looks_api import router as looks_router

    app.include_router(looks_router)

    # The colour grade gallery: presets, your LUTs and their thumbnails.
    from .grades_api import router as grades_router

    app.include_router(grades_router)

    # The highlight reel: readiness, the moment menu and the render job's
    # submit route (spec 2026-10-18-highlight-reel-design, part 2).
    from .highlights_api import router as highlights_router

    app.include_router(highlights_router)

    # Share-link OG card PNGs (spec 2026-08-09). Same lazy-import,
    # always-registered idiom as sync_router and device_router: every
    # route 404s outside hosted mode (see share_og._hosted_gate).
    from .share_og import router as share_og_router

    app.include_router(share_og_router)

    # Export routes (#629 / #919's lift-as-you-go rule). Included here so
    # /api/shooters/{slug}/exports/* passes through the same middleware as
    # the routes that stayed behind.
    from .exports_api import router as exports_router

    app.include_router(exports_router)

    # Hosted mode serves none of the routes in route_scope.LOCAL_ONLY_ROUTES.
    # Applied here, after every router above is included and before the SPA
    # fallback, so the one table covers routes from every module.
    from .stages_api import router as stages_router

    app.include_router(stages_router)

    from .stage_details_api import router as stage_details_router

    app.include_router(stage_details_router)

    from .shooters_api import router as shooters_api_router

    app.include_router(shooters_api_router)

    from .match_api import router as match_api_router

    app.include_router(match_api_router)

    from .me_api import router as me_api_router

    app.include_router(me_api_router)

    from .route_scope import enforce_local_only

    enforce_local_only(app)

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

    # Registered last, so it is the outermost middleware: nothing answers a
    # request without the token, not even a static file.
    if lab_token:
        from . import lab_token as _lab_token

        app.middleware("http")(_lab_token.make_middleware(lab_token))

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


def serve(
    *,
    project_root: Path | None = None,
    project_name: str | None = None,
    host: str = "127.0.0.1",
    port: int = 5174,
    reload: bool = False,
    lab_enabled: bool = False,
    skip_system_check: bool = False,
    lab_token: str | None = None,
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
        lab_token=lab_token,
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
        # async JobBackend.list() call with ``run_sync``.
        jobs = run_sync(state.jobs.list())
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
        f"Shutting down -- waiting for {len(active)} background job{'' if len(active) == 1 else 's'}:",
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
