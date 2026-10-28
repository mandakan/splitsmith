"""The match-level routes: ``/api/match/...``.

Lifted out of ``server.create_app`` (#680). Handlers read the process state
from ``request.app.state.splitsmith_state``; helpers shared with other route
families are called through the ``server`` module so a test that patches
``server.<helper>`` still reaches them. Those helpers and the request models
move here, or to their own domain modules, as the split continues.
"""

from __future__ import annotations

import logging
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from .. import automation as automation_settings
from .. import camera_select, match_model, user_config
from .. import coach as coach_module
from ..access import Feature
from ..audit_revision import audit_revision
from ..compare.project_loader import CompareShooterBundle
from ..config import StageRounds
from ..identity import EVENT_LOGO_DIR, LOGO_MAX_BYTES, event_logo_name, sniff_logo
from ..match_project import MatchProject, ScoreboardImportConflictError, StageEntry
from ..sync.auto_state import load_auto_prefs, update_auto_prefs
from ..sync.base import BASE_DIR as SYNC_BASE_DIR
from ..sync.client import SyncClientError
from ..sync.plan import build_push_plan
from ..sync.pull import plan_pull
from ..sync.state import SyncState, load_sync_state, save_sync_state
from . import export_storage, stage_edit
from . import server as _server
from . import shooter_move as shooter_move_module
from .access_gate import require_feature
from .capabilities import MatchCapability, MatchOrigin
from .comments import CommentAuthorListResponse, CommentAuthorOut
from .context import (
    _hosted_mode_active,
    current_match_id,
    current_match_origin,
    current_match_root,
    current_share_request,
)
from .export_slot import export_slot
from .exports_api import CompareGridRequest, MatchExportRequest
from .http_errors import ensure_source_reachable
from .identity_media import effective_identity, ensure_local_event_logo, event_logo_storage_key, logo_response
from .jobs import Job
from .presence import StoragePresence
from .project_view import ProjectView, build_project_view
from .scoreboard import MatchNotFound, ScoreboardError
from .scoreboard.reconcile import CompetitorRef, LocalShooter, propose_shooter_links
from .server import (
    BeepQueueAltCandidate,
    BeepQueueItem,
    BeepQueueStageGroup,
    CompareShooterRecord,
    CompareShotPoint,
    CreateMatchCompetitorPick,
    CreateMatchManualRequest,
    HealthResponse,
    MergePlanResponse,
    MergePlanShooterMove,
    MergePlanStage,
    ReconcileLink,
    ShareInfo,
    ShooterListEntry,
    TriageResponse,
)
from .state import _no_project_error

logger = logging.getLogger(__name__)

router = APIRouter()


class ShareCreateRequest(BaseModel):
    """Body for minting a link.

    ``scope`` is fixed for the token's whole life. There is deliberately
    no route that changes it: an owner should be able to reason about a
    link from the moment they send it, and a toggle would mean a link
    already in someone's inbox could gain the ability to post.
    """

    scope: Literal["read", "comment"] = "read"


class ShareCamerasRequest(BaseModel):
    """Body for ``PATCH /api/match/shares/{id}/cameras``: ``{slug: selector}``
    (a camera mount or role), or null / empty to follow the defaults."""

    cameras: dict[str, str] | None = None


class ShareListResponse(BaseModel):
    """Response body for ``GET /api/match/shares``."""

    shares: list[ShareInfo]


class MatchStagesResponse(BaseModel):
    """Body of ``GET /api/match/stages`` (#521).

    The canonical stage list, read straight off ``Match.stages`` -- the
    same document ``PUT /api/match/stages`` diffs a submission against.
    The stage editor needs this rather than a shooter's ``project.stages``:
    a scoreboard link rewrites ``match.stages`` with the scoreboard's names
    and ``stage_rounds`` while leaving the shooter projects on their
    placeholder names, and nothing (``merge_stage_times`` included) ever
    closes that gap. Diffing a shooter's copy against the match then reads
    every untouched stage as renamed.

    Deliberately no ``next_stage_number``: allocation is server-side (see
    ``stage_edit.diff_stage_list``) and a client that can see the counter is
    a client that will guess with it.
    """

    stages: list[match_model.MatchStageDefinition]


class StageEditRequest(BaseModel):
    """Body for ``PUT /api/match/stages`` (#521).

    The full desired stage list, not a patch. The server diffs it against
    the match's current list, so the SPA does not have to track which rows
    it changed. ``stage_number: null`` marks a newly added row.
    """

    stages: list[stage_edit.SubmittedStage]


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


class ConnectMatchRequest(BaseModel):
    """Body for POST /api/match/scoreboard/connect.

    Attaches an existing (possibly manually-created) match to an SSI
    Scoreboard match after the fact. Sets the link on the match container
    and every shooter's project, then returns name-based link proposals
    for the operator to confirm.
    """

    match_id: int
    content_type: int


class ReconcileRequest(BaseModel):
    """Body for POST /api/match/scoreboard/reconcile."""

    links: list[ReconcileLink]


class ShooterListResponse(BaseModel):
    """GET /api/match/shooters payload (#324)."""

    match_root: str
    match_name: str
    shooters: list[ShooterListEntry]
    # "hosted" | "desktop" | "local" (#631 Task 6). Lets the SPA tell a
    # desktop-synced read-only mirror apart from a natively-hosted or
    # local match so it can gate write affordances client-side.
    origin: MatchOrigin = "local"
    # Sorted capability names (#756) - the same set the server-side guard
    # enforces, so the SPA renders write affordances from data instead of
    # re-deriving them from origin.
    capabilities: list[MatchCapability] = []


class AddShooterRequest(BaseModel):
    """POST /api/match/shooters body (#324)."""

    name: str
    division: str | None = None
    selected_shooter_id: int | None = None
    selected_competitor_id: int | None = None


class MoveShooterRequest(BaseModel):
    """POST /api/match/videos/move-shooter request body."""

    source_slug: str
    target_slug: str
    video_paths: list[str]


class MoveShooterResponse(BaseModel):
    """POST /api/match/videos/move-shooter response body."""

    outcome: shooter_move_module.MoveShooterOutcome
    source_project: ProjectView


class TriageSummaryResponse(BaseModel):
    """GET /api/match/triage/summary payload (#823): just the flagged
    count, cheap enough to poll without the anomaly/status-walk cost
    of the full triage grid."""

    flagged_count: int


class CompareStageResponse(BaseModel):
    """GET /api/match/stage/{stage_number}/compare payload (#328)."""

    stage_number: int
    stage_name: str
    shooters: list[CompareShooterRecord]


class BeepQueueResponse(BaseModel):
    """GET /api/match/beep-queue payload (#326)."""

    total_items: int
    pending_count: int
    confirmed_count: int
    stages: list[BeepQueueStageGroup]
    # "desktop" on a hosted mirror, "local" everywhere else - lets the SPA
    # pick the honest media surface (snippet vs proxy) without a second
    # round trip.
    origin: MatchOrigin = "local"
    # Sorted capability names (#756) - the same set the server-side guard
    # enforces, so the SPA renders write affordances from data instead of
    # re-deriving them from origin.
    capabilities: list[MatchCapability] = []


class BeepQueueConfirmRequest(BaseModel):
    """POST /api/match/beep-queue/confirm body (#326)."""

    slug: str
    stage_number: int
    video_id: str
    # The user-confirmed beep time. When None the existing detected beep
    # is kept and only ``reviewed`` flips to True.
    time: float | None = None
    source: Literal["detected", "manual", "alt"] = "detected"


class MergePlanRequest(BaseModel):
    """POST /api/match/merge/plan body (#332)."""

    inputs: list[str]
    output: str | None = None
    name: str | None = None


class MergeExecuteRequest(BaseModel):
    """POST /api/match/merge/execute body."""

    inputs: list[str]
    output: str
    name: str | None = None
    move: bool = False


class AutoSyncSettingRequest(BaseModel):
    """Body for PUT /api/match/sync/auto and /api/settings/auto-sync. On the
    per-match route only the fields sent change: ``enabled: null`` restores
    the default, an absent ``enabled`` leaves it alone."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    full_media: bool | None = None


class DesktopCommandRequest(BaseModel):
    """Body for ``POST /api/match/desktop-commands`` (#1100)."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    slug: str | None = None
    stage_number: int | None = None
    args: dict[str, Any] = Field(default_factory=dict)


class SyncStatusResponse(BaseModel):
    """Response body for GET /api/match/sync/status (#631, Task 9).

    Built from ``load_sync_state`` + ``build_push_plan`` - media is a fast
    size/mtime-only check and docs are a cheap small-JSON sha256 (#797),
    so this stays cheap enough to poll.
    """

    configured: bool
    last_synced_at: datetime | None = None
    stale: bool
    pending_media: int
    #: Docs (match, projects, audits) the next push would send (#1074).
    pending_docs: int = 0
    errors: list[str] = Field(default_factory=list)
    #: Count of hosted docs newer than what this desktop last synced
    #: (manifest version diff). None = unknown: sync unconfigured or the
    #: hosted side unreachable right now - offline is a normal desktop
    #: condition, so the card just omits the hint rather than erroring.
    remote_changes: int | None = None


def _adopt_club_from_scoreboard(
    project: MatchProject, client_root: Path, content_type: int, match_id: int, competitor_id: int
) -> None:
    """Fill a shooter's empty club line from their scoreboard competitor,
    best effort: a link never fails because the club could not be read."""
    if (project.identity.club or "").strip():
        return
    try:
        with _server._resolve_scoreboard_client(client_root) as client:
            match_data = client.get_match(content_type, match_id)
    except (ScoreboardError, OSError):
        logger.info("scoreboard club for competitor %s could not be read", competitor_id, exc_info=True)
        return
    project.adopt_scoreboard_club(_server._club_for(match_data, competitor_id))


def _event_logo_response(match: match_model.Match) -> JSONResponse:
    return JSONResponse({"event_logo": match.branding.event_logo})


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


def _command_match_id() -> str:
    match_id = current_match_id.get()
    if match_id is None:
        raise HTTPException(status_code=404, detail="not found")
    return match_id


@router.get("/api/match/shares", response_model=ShareListResponse)
async def _list_match_shares(request: Request) -> ShareListResponse:
    """List all share tokens (including revoked) for the current match."""
    state = request.app.state.splitsmith_state
    if not _hosted_mode_active():
        raise HTTPException(status_code=404, detail="not found")
    mid = current_match_id.get()
    if mid is None:
        raise _no_project_error()
    store = state.share_tokens
    if store is None:
        raise HTTPException(status_code=500, detail="share store unavailable")
    shares = await store.list_for_match(mid)
    return ShareListResponse(
        shares=[
            ShareInfo(
                id=s.id,
                url=f"{state.public_base_url}/share/{s.token}",
                created_at=s.created_at,
                revoked_at=s.revoked_at,
                scope=s.scope,
                cameras=s.cameras,
            )
            for s in shares
        ]
    )


@router.post(
    "/api/match/shares",
    response_model=ShareInfo,
    status_code=201,
    dependencies=[Depends(require_feature(Feature.share))],
)
async def _create_match_share(request: Request, req: ShareCreateRequest = ShareCreateRequest()) -> ShareInfo:
    """Create a new share token for the current match. Returns 201."""
    state = request.app.state.splitsmith_state
    if not _hosted_mode_active():
        raise HTTPException(status_code=404, detail="not found")
    mid = current_match_id.get()
    if mid is None:
        raise _no_project_error()
    store = state.share_tokens
    if store is None:
        raise HTTPException(status_code=500, detail="share store unavailable")
    s = await store.create(mid, scope=req.scope)
    # Best-effort, timeout-bounded: warm the match card cache so the
    # link the owner immediately pastes previews without a cold
    # Chromium render on first fetch. All of the "never cost the
    # owner their share link" handling -- the worker-thread hop
    # (Playwright's sync API can't run on this coroutine's
    # event-loop thread), the wall-time bound, and swallowing any
    # failure -- lives in ``warm_match_card_bounded`` itself; see its
    # docstring for what the bound does and does not guarantee. This
    # try/except is defense in depth, not the primary guard.
    try:
        from .share_og import warm_match_card_bounded

        await warm_match_card_bounded(state, s.token)
    except Exception:
        logger.warning("failed to warm match card for share %s", s.id, exc_info=True)
    return ShareInfo(
        id=s.id,
        url=f"{state.public_base_url}/share/{s.token}",
        created_at=s.created_at,
        revoked_at=s.revoked_at,
        scope=s.scope,
    )


@router.patch(
    "/api/match/shares/{share_id}/cameras",
    response_model=ShareInfo,
    dependencies=[Depends(require_feature(Feature.share))],
)
async def _set_match_share_cameras(request: Request, share_id: str, req: ShareCamerasRequest) -> ShareInfo:
    """Set which camera each shooter starts on for one share link.

    Each entry is validated like the saved default (a mount or role some
    stage of that shooter resolves), so a typo fails here rather than
    quietly opening every viewer on the primary. A slug that is not a
    shooter of the match is a 400. 404 when the link is unknown or not
    this user's.
    """
    state = request.app.state.splitsmith_state
    if not _hosted_mode_active():
        raise HTTPException(status_code=404, detail="not found")
    mid = current_match_id.get()
    if mid is None:
        raise _no_project_error()
    store = state.share_tokens
    if store is None:
        raise HTTPException(status_code=500, detail="share store unavailable")
    cameras = {k: v for k, v in (req.cameras or {}).items() if v} or None
    for slug, camera in (cameras or {}).items():
        try:
            project = await state.shooter_project_async(slug)
        except HTTPException as exc:
            raise HTTPException(status_code=400, detail=f"no shooter {slug!r} in this match") from exc
        try:
            camera_select.validate_camera(
                [stage.videos for stage in project.stages if not stage.skipped], camera
            )
        except camera_select.CameraResolutionError as exc:
            raise HTTPException(status_code=400, detail=f"{slug}: {exc}") from exc
    s = await store.set_cameras(share_id, match_id=mid, cameras=cameras)
    if s is None:
        raise HTTPException(status_code=404, detail="not found")
    return ShareInfo(
        id=s.id,
        url=f"{state.public_base_url}/share/{s.token}",
        created_at=s.created_at,
        revoked_at=s.revoked_at,
        scope=s.scope,
        cameras=s.cameras,
    )


@router.delete(
    "/api/match/shares/{share_id}",
    status_code=204,
    dependencies=[Depends(require_feature(Feature.share))],
)
async def _delete_match_share(request: Request, share_id: str) -> Response:
    """Revoke a share token. 204 on success (including already-revoked),
    404 when the share_id is unknown or not owned by this user."""
    state = request.app.state.splitsmith_state
    if not _hosted_mode_active():
        raise HTTPException(status_code=404, detail="not found")
    mid = current_match_id.get()
    if mid is None:
        raise _no_project_error()
    store = state.share_tokens
    if store is None:
        raise HTTPException(status_code=500, detail="share store unavailable")
    ok = await store.revoke(share_id, match_id=mid)
    if not ok:
        raise HTTPException(status_code=404, detail="not found")
    return Response(status_code=204)


@router.get("/api/match/comment-authors", response_model=CommentAuthorListResponse)
async def list_match_comment_authors(request: Request) -> CommentAuthorListResponse:
    """Per-author detail for the owner's moderation view.

    Owner-only by construction: the shape is absent from
    ``_SHARE_PATH_RE``, so an anonymous share caller gets the same
    uniform 404 as any unadmitted path. No capability entry is
    needed -- ``required_capability`` returns ``None`` for GET.

    Match-scoped on purpose. Aggregating an author across matches
    would reveal that they commented on other people's share links,
    which is a disclosure they never opted into.
    """
    state = request.app.state.splitsmith_state
    store = state.comments
    mid = current_match_id.get()
    if store is None or mid is None:
        raise HTTPException(status_code=404, detail="not found")
    summaries = await store.author_summaries(mid)
    return CommentAuthorListResponse(
        authors=[
            CommentAuthorOut(
                author_code=s.author_code,
                author_kind=s.author_kind,
                first_comment_at=s.first_comment_at,
                comment_count=s.comment_count,
                handles=list(s.handles),
            )
            for s in summaries
        ]
    )


@router.delete("/api/match/comments")
async def delete_match_comments(
    request: Request,
    share_token_id: str | None = None,
    author_key_hash: str | None = None,
) -> dict[str, int]:
    """Bulk moderation. Exactly one selector, because the two mean
    very different things and a call with neither would read as
    'delete everything' - which is not an action this offers."""
    state = request.app.state.splitsmith_state
    if (share_token_id is None) == (author_key_hash is None):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "one_selector_required",
                "message": "pass exactly one of share_token_id or author_key_hash",
            },
        )
    store = state.comments
    mid = current_match_id.get()
    if store is None or mid is None:
        raise HTTPException(status_code=404, detail="not found")
    if share_token_id is not None:
        deleted = await store.delete_by_share_token(mid, share_token_id)
    else:
        assert author_key_hash is not None
        deleted = await store.delete_by_author_key_hash(mid, author_key_hash)
    return {"deleted": deleted}


@router.post("/api/match/sync", response_model=Job)
async def post_match_sync(request: Request) -> Job:
    state = request.app.state.splitsmith_state
    if _hosted_mode_active():
        raise HTTPException(status_code=404, detail="not found")
    prefs = user_config.load_global_prefs()
    if not prefs.hosted_base_url or not prefs.hosted_token:
        raise HTTPException(status_code=409, detail="sync_not_configured")
    return await state.jobs.submit(kind="sync_match")


@router.post("/api/match/sync/republish", response_model=Job)
async def post_match_sync_republish(request: Request) -> Job:
    """Upload a match deleted on hosted as a new copy.

    The sync that found the mirror gone set ``hosted_deleted_at`` and
    stopped, so nothing recreated it behind the user's back. This is
    the explicit way back: forget every version, hash and upload the
    old mirror had (the next sync then pushes the whole match, at
    ``expected_version`` 0) and start it.
    """
    state = request.app.state.splitsmith_state
    if _hosted_mode_active():
        raise HTTPException(status_code=404, detail="not found")
    prefs = user_config.load_global_prefs()
    if not prefs.hosted_base_url or not prefs.hosted_token:
        raise HTTPException(status_code=409, detail="sync_not_configured")
    match_root = state.match_root
    if load_auto_prefs(match_root).hosted_deleted_at is None:
        raise HTTPException(status_code=409, detail="match_not_deleted_on_hosted")
    if await state.jobs.find_active(kind="sync_match") or await state.jobs.find_active(kind="auto_sync"):
        raise HTTPException(status_code=409, detail="sync_in_progress")

    def _reset() -> None:
        save_sync_state(match_root, SyncState())
        shutil.rmtree(match_root / SYNC_BASE_DIR, ignore_errors=True)

        def _clear(p: Any) -> None:
            p.hosted_deleted_at = None
            p.last_auto = None

        update_auto_prefs(match_root, _clear)

    await run_in_threadpool(_reset)
    return await state.jobs.submit(kind="sync_match")


@router.get("/api/match/sync/status", response_model=SyncStatusResponse)
async def get_match_sync_status(request: Request) -> SyncStatusResponse:
    """Cheap sync status: size/mtime-only planning for media, no media
    hashing (#797's per-doc hashing is cheap - small JSON, not
    multi-gigabyte trims - so it still runs here on every poll).

    ``stale`` is True whenever a push would have real work to do -
    the match has never synced, there's unpushed media or doc changes,
    or the plan itself can't run (e.g. a legacy project without a
    match id).

    ``remote_changes`` is a best-effort staleness hint: one short
    manifest GET against the hosted side, diffed against
    ``sync_state`` via ``plan_pull``. Left ``None`` (never an error
    response) when sync isn't configured, the match has never been
    pushed (hosted 404), or the hosted side is unreachable right
    now - offline is a normal desktop condition, not a failure.
    """
    state = request.app.state.splitsmith_state
    if _hosted_mode_active():
        raise HTTPException(status_code=404, detail="not found")
    prefs = user_config.load_global_prefs()
    configured = bool(prefs.hosted_base_url and prefs.hosted_token)
    match_root = state.match_root
    sync_state = load_sync_state(match_root)
    plan = build_push_plan(
        match_root, sync_state=sync_state, full_media=load_auto_prefs(match_root).full_media
    )
    stale = sync_state.last_synced_at is None or bool(plan.media) or bool(plan.docs) or bool(plan.errors)

    remote_changes: int | None = None
    if configured:
        match_id = plan.match_id or None
        if match_id:
            try:
                manifest = await run_in_threadpool(_server._fetch_remote_manifest, prefs, match_id)
                remote_changes = len(plan_pull(manifest, sync_state))
            # a best-effort hint must never break the status poll - malformed manifest degrades to unknown
            except (httpx.HTTPError, SyncClientError, KeyError, ValueError, TypeError):
                remote_changes = None

    return SyncStatusResponse(
        configured=configured,
        last_synced_at=sync_state.last_synced_at,
        stale=stale,
        pending_media=len(plan.media),
        pending_docs=len(plan.docs),
        errors=plan.errors,
        remote_changes=remote_changes,
    )


@router.get("/api/match/sync/auto")
async def get_match_auto_sync(request: Request) -> JSONResponse:
    """Per-match auto-sync state for the SyncCard (spec 2026-09-27)."""
    state = request.app.state.splitsmith_state
    if _hosted_mode_active() or state.auto_sync is None:
        raise HTTPException(status_code=404, detail="not found")
    return JSONResponse(state.auto_sync.status_for(state.match_root))


@router.put("/api/match/sync/auto")
async def put_match_auto_sync(request: Request, req: AutoSyncSettingRequest) -> JSONResponse:
    """``enabled: null`` restores the default (on once synced)."""
    state = request.app.state.splitsmith_state
    if _hosted_mode_active() or state.auto_sync is None:
        raise HTTPException(status_code=404, detail="not found")
    sent = req.model_fields_set

    def _apply(p: Any) -> None:
        if "enabled" in sent:
            p.enabled = req.enabled
        if req.full_media is not None:
            p.full_media = req.full_media

    update_auto_prefs(state.match_root, _apply)
    return JSONResponse(state.auto_sync.status_for(state.match_root))


@router.get("/api/match/stages", response_model=MatchStagesResponse)
def get_match_stages(request: Request) -> MatchStagesResponse:
    """The bound match's canonical stage list (#521).

    The read half of the stage editor: ``PUT /api/match/stages`` diffs a
    submission against ``Match.stages``, so the editor has to load
    ``Match.stages`` too. A shooter's ``project.stages`` is not a
    substitute -- see :class:`MatchStagesResponse`.
    """
    state = request.app.state.splitsmith_state
    return MatchStagesResponse(stages=list(state.match().stages))


@router.put("/api/match/stages", response_model=stage_edit.StageEditSummary)
async def edit_match_stages(request: Request, req: StageEditRequest) -> stage_edit.StageEditSummary:
    """Add, remove, and rename stages on an existing match (#521).

    The stage list is a property of the match, so an edit fans out to
    every shooter -- their stage lists have to stay aligned. Removing
    a stage releases its videos to ``unassigned_videos`` and deletes
    its audit doc and derived caches; stages the user did not touch
    keep everything, which is the whole point of never renumbering.
    A stage the user adds is numbered from the match's persisted
    ``next_stage_number`` counter, so it can never land on a number a
    removed stage used to hold.

    A ``StateConflictError`` raised by ``save_match()`` (hosted mode,
    optimistic-locking loss) is not caught here -- it propagates to the
    app-level ``exception_handler`` registered near the top of
    ``create_app``, which already maps it to 409 ``version_conflict``.

    An empty ``shooters`` roster is allowed, not an error: the stage
    list is a property of the match, so editing it before anyone is
    added is a legitimate thing to do and refusing would block it. Such
    a request updates ``match.stages`` and fans out to nobody, so it
    returns 200 with ``shooters: []``. Read that field rather than
    assuming a shooter was touched -- ``Home.tsx`` carries a
    ``shooters.length || 1`` fallback (for legacy single-shooter
    projects, which report an empty roster), so its confirm dialog says
    "for 1 shooter" on a genuinely empty match while the server touches
    zero.
    """
    state = request.app.state.splitsmith_state
    root = current_match_root.get()
    if root is None:
        raise _no_project_error()
    match = state.match()
    slugs = list(match.shooters)

    async def _cancel(stage_numbers: set[int]) -> int:
        """No-op: ``compute_jobs`` carries no match id, and ``Job`` has
        no match/slug field, so a filter on ``stage_number`` alone (the
        only key ``apply_stage_edit`` gives us) cannot be scoped to this
        match. Removing stage 3 here would reach into every OTHER
        match's PENDING/RUNNING stage-3 job too, which is worse than not
        cancelling at all. Cancellation was belt-and-braces on top of
        the design's real guarantee: the match's persisted
        ``next_stage_number`` counter never reissues a freed stage
        number, so a late worker write to ``stage3_*`` can never be read
        back as a live stage -- it's inert. Precise (match-scoped)
        cancellation is tracked in #645; until then this stays a no-op
        rather than a plausible-looking bug for the next reader to
        "restore".
        """
        return 0

    def _save_project(slug: str, project: MatchProject) -> None:
        project.save(state.shooter_root(slug))

    try:
        return await stage_edit.apply_stage_edit(
            match=match,
            submitted=req.stages,
            shooter_slugs=slugs,
            load_project=state.shooter_project,
            save_project=_save_project,
            save_match=lambda: match.save(root),
            delete_audit=state.delete_audit,
            # Per-shooter, not the match root: derived caches live at
            # ``<match_root>/shooters/<slug>/{audio,trimmed}``, the same
            # root ``_save_project`` above writes the project doc to.
            shooter_root=state.shooter_root,
            cancel_jobs=_cancel,
        )
    except stage_edit.StageEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/match/branding/event-logo")
async def upload_event_logo(request: Request, file: UploadFile = File(...)) -> JSONResponse:
    """Store the event's logo: a PNG, JPEG, WebP or checked SVG, sniffed and capped
    like a shooter's (``identity.sniff_logo``), never trusted by name;
    the previous file is removed."""
    state = request.app.state.splitsmith_state
    match_root, match = _server._resolve_match_context(state)
    data = await file.read(LOGO_MAX_BYTES + 1)
    try:
        ext = sniff_logo(data)
    except ValueError as exc:
        status = 413 if len(data) > LOGO_MAX_BYTES else 422
        raise HTTPException(status_code=status, detail=str(exc)) from None
    name = event_logo_name(data, ext)
    previous = match.branding.event_logo
    folder = match_root / EVENT_LOGO_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    if state.storage is not None and match.match_id:
        state.storage.write_bytes(event_logo_storage_key(match.match_id, name), data)
    match.branding = match.branding.model_copy(update={"event_logo": name})
    match.save(match_root)
    if previous and previous != name:
        (folder / previous).unlink(missing_ok=True)
    return _event_logo_response(match)


@router.get("/api/match/branding/event-logo")
def get_event_logo(request: Request) -> FileResponse:
    """The event's logo, for the Branding row. Content-named and sniffed
    on upload; served with ``nosniff``."""
    state = request.app.state.splitsmith_state
    match_root, match = _server._resolve_match_context(state)
    path = ensure_local_event_logo(match.branding, match_root, storage=state.storage, match_id=match.match_id)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="no event logo")
    return logo_response(path, cache_control="private, no-cache")


@router.delete("/api/match/branding/event-logo")
def remove_event_logo(request: Request) -> JSONResponse:
    """Clear the event's logo and remove the local file; a hosted copy is
    swept by the next push's gc."""
    state = request.app.state.splitsmith_state
    match_root, match = _server._resolve_match_context(state)
    previous = match.branding.event_logo
    match.branding = match.branding.model_copy(update={"event_logo": None})
    match.save(match_root)
    if previous:
        (match_root / EVENT_LOGO_DIR / previous).unlink(missing_ok=True)
    return _event_logo_response(match)


@router.get("/api/match/templates")
def list_match_templates(request: Request) -> JSONResponse:
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


@router.post("/api/match/create-manual", dependencies=[Depends(require_feature(Feature.create_match))])
async def create_match_manual(request: Request, req: CreateMatchManualRequest) -> HealthResponse:
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
    state = request.app.state.splitsmith_state
    target = _server._resolve_create_target(state, project_folder=req.project_folder, name=req.name)
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
    # Bind the storage port so every save() from here goes through it:
    # hosted, version=0 makes the save below the INSERT into state_docs
    # (the disk file Match.init just wrote is abandoned); locally it is
    # the same match.json write.
    assert match.match_id is not None  # Match.init assigns one
    docs = state.docstore_for(match.match_id, target)
    match.bind_state(docs, version=0)
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
    legacy.bind_state(docs, slug=shooter_slug, version=0)
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
        recorded, match_id = await _server._register_match_at(state, target, fallback_name=req.name)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _server._register_response(state, target.resolve(), recorded, match_id)


@router.post(
    "/api/match/create-from-scoreboard", dependencies=[Depends(require_feature(Feature.create_match))]
)
async def create_match_from_scoreboard(
    request: Request,
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
    state = request.app.state.splitsmith_state
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
                detail=(f"duplicate competitor {pick.selected_competitor_id} in picks"),
            )
        comp_ids_seen.add(pick.selected_competitor_id)

    target = _server._resolve_create_target(state, project_folder=req.project_folder, name=req.name)
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
    assert match.match_id is not None  # Match.init assigns one
    docs = state.docstore_for(match.match_id, target)
    match.bind_state(docs, version=0)
    match.scoreboard_match_id = str(req.match_id)
    match.scoreboard_content_type = req.content_type
    match.save(target)

    # Fetch the match shell once -- we'll reuse it for every shooter
    # via the project-local cache so subsequent get_match calls hit
    # disk rather than the network.
    with _server._resolve_scoreboard_client(target) as client:
        try:
            match_data = client.get_match(req.content_type, req.match_id)
        except MatchNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ScoreboardError as exc:
            _server._raise_scoreboard_http(exc)

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
        legacy.bind_state(docs, slug=shooter_slug, version=0)
        legacy.competitor_name = pick.name
        legacy.scoreboard_match_id = str(req.match_id)
        legacy.scoreboard_content_type = req.content_type
        legacy.selected_shooter_id = pick.selected_shooter_id
        legacy.selected_competitor_id = pick.selected_competitor_id
        legacy.adopt_scoreboard_club(_server._club_for(match_data, pick.selected_competitor_id))
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
            _server._fetch_and_merge_stage_times(
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
        recorded, match_id = await _server._register_match_at(state, target, fallback_name=req.name)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _server._register_response(state, target.resolve(), recorded, match_id)


@router.post("/api/match/merge/plan", response_model=MergePlanResponse)
def merge_plan(request: Request, req: MergePlanRequest) -> MergePlanResponse:
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
                detail=(f"{src} is not a legacy single-shooter project (no project.json)"),
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


@router.post("/api/match/merge/execute", response_model=HealthResponse)
async def merge_execute(request: Request, req: MergeExecuteRequest) -> HealthResponse:
    """Execute a merge and bind the new match's first shooter as the
    active project so the user lands on a working session immediately.

    Refuses if the destination already contains ``match.json`` (the
    plan validation does the same check, but the user could race
    between plan + execute). Records the new match in the recent-
    projects index so the picker picks it up.
    """
    state = request.app.state.splitsmith_state
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
        recorded, match_id = await _server._register_match_at(state, output, fallback_name=match.name)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _server._register_response(state, output.resolve(), recorded, match_id)


@router.get("/api/match/shooters", response_model=ShooterListResponse)
def list_match_shooters(request: Request) -> ShooterListResponse:
    """List every shooter in the currently-bound match with coverage."""
    state = request.app.state.splitsmith_state
    match_root, match = _server._resolve_match_context(state)
    # One storage index for the whole request (#1180): the raw-upload
    # listing is shared by every shooter, the trim listing is per
    # shooter. A share request gets none: its viewer never sees the
    # count the index exists to compute.
    presence = None if current_share_request.get() else StoragePresence(state.storage)
    book = _server._roster_book(state)
    entries: list[ShooterListEntry] = []
    for slug in match.shooters:
        shooter_root = match_model.Match.shooter_root(match_root, slug)
        try:
            entries.append(_server._classify_shooter(state, shooter_root, match, presence, book))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping shooter %s: %s", slug, exc)
            continue
    return ShooterListResponse(
        match_root="" if current_share_request.get() else str(match_root),
        match_name=match.name,
        shooters=entries,
        origin=current_match_origin.get() or "local",
        capabilities=_server._serialized_capabilities(),
    )


@router.get("/api/match/triage", response_model=TriageResponse)
def get_match_triage(request: Request) -> TriageResponse:
    """Cross-shooter, cross-stage triage grid for the mobile audit-
    triage surface (slice 4). Placeholder stages are excluded; skipped
    stages are included with status ``skipped`` (the SPA collapses
    them client-side)."""
    state = request.app.state.splitsmith_state
    return _server._build_triage_response(state)


@router.get("/api/match/triage/summary", response_model=TriageSummaryResponse)
def get_match_triage_summary(request: Request) -> TriageSummaryResponse:
    """Just the flagged count, without the anomaly computation or
    status walk that :func:`_build_triage_response` does (#823) - a
    cheap poll target for the mobile triage surface's badge."""
    state = request.app.state.splitsmith_state
    _, match = _server._resolve_match_context(state)
    bundle = state.match_bundle(match)
    docs: list[dict | None] = []
    for slug in match.shooters:
        legacy = bundle.projects.get(slug)
        if legacy is None:
            continue
        audit_docs = bundle.audit_docs(slug)
        for stg in legacy.stages:
            if stg.placeholder:
                continue
            docs.append(audit_docs.get(stg.stage_number))
    return TriageSummaryResponse(flagged_count=_server._count_flagged(docs))


@router.post("/api/match/shooters", response_model=ShooterListResponse)
def add_match_shooter(request: Request, req: AddShooterRequest) -> ShooterListResponse:
    """Add a new shooter to the bound match.

    Creates the ``<match>/shooters/<slug>/`` tree, mirrors the match's
    stage definitions into the shooter's ``project.json``, and saves a
    ``shooter.json`` next to it. Returns the refreshed list. Slugs are
    opaque random ids (``s_<hex>``) so URLs / disk paths don't leak
    competitor names.
    """
    state = request.app.state.splitsmith_state
    match_root, match = _server._resolve_match_context(state)
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
    # Bind the new project doc to the port (version=0 -> the save below
    # INSERTs it into state_docs hosted). ``match.add_shooter`` above
    # already persisted the updated roster to the match doc.
    legacy.bind_state(state.docstore(), slug=slug, version=0)
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
                    start=sd.start,
                )
            )
    if req.selected_competitor_id is not None:
        # Bind the scoreboard pick in the same request instead of
        # forcing a follow-up call to select-shooter -- mirrors
        # create-from-scoreboard's per-competitor handling. Note
        # legacy.competitor_name is already req.name from above.
        legacy.selected_shooter_id = req.selected_shooter_id
        legacy.selected_competitor_id = req.selected_competitor_id
        if legacy.scoreboard_match_id and legacy.scoreboard_content_type is not None:
            _adopt_club_from_scoreboard(
                legacy,
                match_root,
                legacy.scoreboard_content_type,
                int(legacy.scoreboard_match_id),
                req.selected_competitor_id,
            )
    legacy.save(shooter_root)
    if (
        req.selected_competitor_id is not None
        and legacy.scoreboard_match_id
        and legacy.scoreboard_content_type is not None
    ):
        try:
            _server._fetch_and_merge_stage_times(
                shooter_root,
                legacy,
                legacy.scoreboard_content_type,
                int(legacy.scoreboard_match_id),
                req.selected_competitor_id,
            )
        except HTTPException as exc:
            # Non-fatal: the shooter <-> competitor link succeeds even
            # if stage times aren't fetchable yet (mirrors
            # create-from-scoreboard's per-shooter merge failure
            # handling above).
            logger.warning(
                "stage-times merge failed for new shooter %s (%s): %s",
                req.name,
                slug,
                exc.detail,
            )
    return list_match_shooters(request)


@router.post("/api/match/scoreboard/connect")
def scoreboard_connect_match(request: Request, req: ConnectMatchRequest) -> JSONResponse:
    """Link an existing match to an SSI Scoreboard match after creation.

    Persists ``scoreboard_match_id`` + ``scoreboard_content_type`` on the
    match container and mirrors them onto every shooter's project so the
    per-shooter merge/refresh paths can pull scores. Stage shells are
    adopted from the scoreboard ONLY when the local match carries no real
    stages yet (all placeholders) and the stage counts line up -- a
    mismatch never rewrites local stages, trims, or audits; scores attach
    by stage number and the caller gets ``stage_mismatch=True`` to warn.

    Returns name-based ``proposals`` for the operator to confirm via the
    reconcile endpoint; this route never auto-applies a shooter link.
    """
    state = request.app.state.splitsmith_state
    match_root, match = _server._resolve_match_context(state)
    with _server._resolve_scoreboard_client(match_root) as client:
        try:
            match_data = client.get_match(req.content_type, req.match_id)
        except MatchNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ScoreboardError as exc:
            _server._raise_scoreboard_http(exc)

    # Measure BEFORE any adoption so the response reports what the operator
    # had locally, which is what the mismatch warning is about.
    local_stage_count = len(match.stages)
    sb_stage_count = len(match_data.stages)
    # A warning is warranted only when the operator has real local stages
    # whose count differs -- an empty match adopts cleanly below.
    stage_mismatch = local_stage_count > 0 and sb_stage_count != local_stage_count

    match.scoreboard_match_id = str(req.match_id)
    match.scoreboard_content_type = req.content_type
    # Adopt stage names/rounds only when it can't clobber real structure:
    # an empty stage list, or an all-placeholder list whose count matches.
    placeholders_only = all(s.placeholder for s in match.stages)
    if not match.stages or (placeholders_only and not stage_mismatch):
        match.stages_from_match_data(match_data)
    match.save(match_root)

    competitors = [
        CompetitorRef(
            competitor_id=c.id,
            shooter_id=c.shooterId,
            name=c.name,
            division=c.division,
        )
        for c in match_data.competitors
    ]
    local_shooters: list[LocalShooter] = []
    for slug in match.shooters:
        legacy = state.shooter_project(slug)
        legacy.scoreboard_match_id = str(req.match_id)
        legacy.scoreboard_content_type = req.content_type
        legacy.save(state.shooter_root(slug))
        local_shooters.append(LocalShooter(slug=slug, name=legacy.competitor_name or slug, division=None))
    proposals = propose_shooter_links(local_shooters, competitors)
    return JSONResponse(
        {
            "stage_mismatch": stage_mismatch,
            "local_stage_count": local_stage_count,
            "scoreboard_stage_count": sb_stage_count,
            "proposals": [p.model_dump() for p in proposals],
        }
    )


@router.post("/api/match/scoreboard/reconcile", response_model=ShooterListResponse)
def scoreboard_reconcile(request: Request, req: ReconcileRequest) -> ShooterListResponse:
    """Apply confirmed shooter -> competitor links for a linked match.

    Mirrors the per-shooter select-shooter endpoint for each link: pins
    the ids on the shooter's project and merges stage times so detection
    picks up its expected-rounds prior. A merge failure is non-fatal (the
    link is still persisted); the operator can refresh later. Local names
    are left untouched -- reconcile binds identity, it doesn't rename.
    """
    state = request.app.state.splitsmith_state
    _server._resolve_match_context(state)
    for link in req.links:
        legacy = state.shooter_project(link.slug)
        shooter_root = state.shooter_root(link.slug)
        legacy.selected_shooter_id = link.shooter_id
        legacy.selected_competitor_id = link.competitor_id
        if legacy.scoreboard_match_id and legacy.scoreboard_content_type is not None:
            _adopt_club_from_scoreboard(
                legacy,
                shooter_root.parent.parent,
                legacy.scoreboard_content_type,
                int(legacy.scoreboard_match_id),
                link.competitor_id,
            )
        legacy.save(shooter_root)
        if legacy.scoreboard_match_id and legacy.scoreboard_content_type is not None:
            try:
                _server._fetch_and_merge_stage_times(
                    shooter_root,
                    legacy,
                    legacy.scoreboard_content_type,
                    int(legacy.scoreboard_match_id),
                    link.competitor_id,
                )
            except HTTPException as exc:
                # Non-fatal: the link persists even if stage times aren't
                # fetchable yet (mirrors create-from-scoreboard).
                logger.warning(
                    "stage-times merge failed for %s: %s",
                    link.slug,
                    exc.detail,
                )
    return list_match_shooters(request)


@router.delete("/api/match/shooters/{slug}", response_model=ShooterListResponse)
async def remove_match_shooter(request: Request, slug: str) -> ShooterListResponse:
    """Remove a shooter from the bound match.

    Drops the slug from ``match.json``, deletes
    ``<match>/shooters/<slug>/`` from disk, and (hosted) sweeps that
    shooter's project + audit ``state_docs`` so no orphaned rows are
    left behind.
    """
    state = request.app.state.splitsmith_state
    match_root, match = _server._resolve_match_context(state)
    if slug not in match.shooters:
        raise HTTPException(status_code=404, detail="shooter not found")
    shooter_root = match_model.Match.shooter_root(match_root, slug)
    if shooter_root.exists():
        shutil.rmtree(shooter_root, ignore_errors=True)
    match.shooters = [s for s in match.shooters if s != slug]
    match.save(match_root)
    # The port-bound match.save above persists the dropped roster. The
    # shooter's own documents go through the port too: hosted that sweeps
    # the project and per-stage audit rows, which the rmtree never touched
    # and which are otherwise unreachable (shooter_root rejects the slug).
    state.docstore().delete_project(slug)
    return list_match_shooters(request)


@router.post("/api/match/videos/move-shooter", response_model=MoveShooterResponse)
async def move_shooter_endpoint(request: Request, req: MoveShooterRequest) -> JSONResponse:
    """Relocate footage from one shooter to another (wrong-shooter correction).

    Moves each video in ``video_paths`` from ``source_slug`` to
    ``target_slug``, carrying all human-reviewed state (beep timestamps,
    audited shot splits) verbatim. Machine-generated caches are relocated
    or regenerated by background jobs -- the user never needs to re-review.

    Returns the outcome (moved + blocked) and the refreshed source project
    so the SPA can update its view without a second fetch.

    Collision rules (applied per video):
    - Occupied-stage block: if the target stage already has a primary AND
      audited shots, the move is refused with ``code="occupied_stage"``.
      Other videos in the batch still move. The user resolves manually.
    - Primary-collision demotion: if the moved video is ``primary`` and
      the target stage has an existing primary but no audited shots, the
      moved video lands as ``secondary`` and ``demoted_to_secondary=True``
      is set on the result.
    """
    state = request.app.state.splitsmith_state
    match_root, match = _server._resolve_match_context(state)
    if req.source_slug == req.target_slug:
        raise HTTPException(status_code=400, detail="source and target shooter must differ")
    for slug in (req.source_slug, req.target_slug):
        if slug not in match.shooters:
            raise HTTPException(status_code=404, detail=f"shooter {slug!r} not in this match")

    source_root = state.shooter_root(req.source_slug)
    target_root = state.shooter_root(req.target_slug)
    source_project = state.shooter_project(req.source_slug)
    target_project = state.shooter_project(req.target_slug)

    def _load_target_audit(n: int) -> dict | None:
        doc, _ = state.load_audit(req.target_slug, n)
        return doc

    def _load_source_audit(n: int) -> dict | None:
        doc, _ = state.load_audit(req.source_slug, n)
        return doc

    def _save_target_audit(n: int, doc: dict) -> None:
        # Load current version first so optimistic-lock version is correct.
        _, version = state.load_audit(req.target_slug, n)
        state.save_audit(req.target_slug, n, doc, version=version)

    def _clear_source_audit(n: int) -> None:
        state.delete_audit(req.source_slug, n)

    # Both shooters' audit docs are read, merged and rewritten: hold the
    # audit lock across the whole move so a sync pull cannot land in
    # between (#1075).
    with _server._audit_rmw(state):
        outcome = shooter_move_module.move_shooter(
            source_project=source_project,
            source_root=source_root,
            target_project=target_project,
            target_root=target_root,
            video_paths=req.video_paths,
            load_target_audit=_load_target_audit,
            save_target_audit=_save_target_audit,
            load_source_audit=_load_source_audit,
            clear_source_audit=_clear_source_audit,
            storage=state.storage,
        )

    # Persist target first (durable-on-target-first under crash).
    target_project.save(target_root)
    source_project.save(source_root)

    # Auto-queue beep for moved primaries that landed on assigned stages.
    for item in outcome.moved:
        if item.stage_number is None or item.demoted_to_secondary:
            continue
        try:
            t_stage = target_project.stage(item.stage_number)
        except KeyError:
            continue
        prim = t_stage.primary()
        if prim is not None and prim.path.as_posix() == item.video_path:
            # Best-effort: the move is already persisted. A queue hiccup
            # must not turn a completed move into a 500 -- the jobs rail
            # (and a manual "Detect beep") remain as the recovery path.
            try:
                await _server._auto_queue_beep_if_needed(
                    state, req.target_slug, target_project, item.stage_number, prim
                )
            except Exception:
                logger.exception(
                    "move-shooter: auto-queue beep failed for stage %s (move already saved)",
                    item.stage_number,
                )

    # The SPA keeps this as the open project (no reload), so it carries the
    # same computed fields GET project does.
    return JSONResponse(
        {
            "outcome": outcome.model_dump(mode="json"),
            "source_project": build_project_view(state, req.source_slug, source_project),
        }
    )


@router.post("/api/match/shooters/{slug}/build-trim-caches")
async def build_shooter_trim_caches(request: Request, slug: str) -> JSONResponse:
    """Submit trim-cache jobs for every angle in ``slug``'s project
    whose audit-mode short-GOP MP4 is missing (#351).

    Operates against the shooter's project root directly rather than
    the bound ``state``, so the user can regenerate Mathias's caches
    while staying on Albin's audit screen. Eligibility is
    :func:`_audit_trim_targets` -- one job per uncached angle, not one
    per stage, because Audit plays secondaries alongside the primary
    and each has its own cache keyed on ``video_id``. Jobs go through
    the existing JobRegistry; the SPA's JobsPanel surfaces them.

    Already-cached angles are reported under ``skipped`` rather than
    re-queued: the underlying ``ensure_video_audit_trim`` is
    idempotent, but skipping early avoids the queue churn.
    """
    state = request.app.state.splitsmith_state
    match_root, match = _server._resolve_match_context(state)
    if slug not in match.shooters:
        raise HTTPException(status_code=404, detail="shooter not found")
    shooter_root = match_model.Match.shooter_root(match_root, slug)
    try:
        proj = state.shooter_project(shooter_root.name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    jobs_submitted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    presence = StoragePresence(state.storage)
    for stage in proj.stages:
        targets, stage_skips = _server._audit_trim_targets(proj, shooter_root, stage, presence=presence)
        skipped.extend(stage_skips)
        for video in targets:
            existing = await state.jobs.find_active(
                kind="trim",
                stage_number=stage.stage_number,
                shooter_slug=slug,
                video_id=video.video_id,
            )
            if existing is not None:
                jobs_submitted.append(existing.model_dump(mode="json"))
                continue
            job = await state.jobs.submit(
                kind="trim",
                stage_number=stage.stage_number,
                shooter_slug=slug,
                video_id=video.video_id,
                args={
                    "slug": slug,
                    "stage_number": stage.stage_number,
                    "video_id": video.video_id,
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


@router.get(
    "/api/match/stage/{stage_number}/compare",
    response_model=CompareStageResponse,
)
def get_stage_compare(request: Request, stage_number: int) -> CompareStageResponse:
    """Per-shooter compare data for a stage.

    Each shooter contributes their lossless trim path (if built),
    ``beep_offset_in_clip`` so the SPA can align all clips to time-
    since-beep, and the shot list as a list of ``time_after_beep``
    scalars. Shooters with no trim still appear so the SPA can render
    empty tiles instead of silently dropping the slot.
    """
    state = request.app.state.splitsmith_state
    match_root, match = _server._resolve_match_context(state)
    try:
        stage_def = match.stage(stage_number)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    records: list[CompareShooterRecord] = []
    book = _server._roster_book(state)
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
        primary = next((v for v in stage.videos if v.role == "primary"), None) if stage is not None else None

        beep_offset: float | None = None
        duration_seconds: float | None = None
        video_ref: str | None = None
        if stage is not None and primary is not None and primary.beep_time is not None:
            # Prefer the lossless export (same file FCPXML references);
            # fall back to the audit-mode short-GOP cache produced
            # during trim+audit. Both are cuts of the same source at
            # ``beep_time - pre_buffer``, so beep alignment is identical;
            # the audit cache is the H.264 version the browser already
            # streams during scrubbing. Either is fine for sync playback,
            # and the fallback lets compare just work after audit
            # without requiring a separate lossless export pass.
            video_ref = _server._resolve_compare_trim(
                legacy, shooter_root, stage_number, stage_def.stage_name, primary, state.storage
            )
            if video_ref is not None:
                beep_offset = min(legacy.trim_pre_buffer_seconds, primary.beep_time)

        # Shots come from audit; convert each shot.time to time-after-beep.
        shots: list[CompareShotPoint] = []
        if stage is not None and primary is not None and primary.beep_time is not None:
            # ``shooter_root.name`` is the slug; load the audit doc from
            # state_docs (hosted) or disk (local).
            audit_data, _ = state.load_audit(shooter_root.name, stage_number)
            if isinstance(audit_data, dict):
                audit_beep = audit_data.get("beep_time")
                raw_shots = [s for s in (audit_data.get("shots") or []) if isinstance(s, dict)]
                if audit_beep is not None:
                    # #781: legacy docs predate #775's classify-on-save;
                    # heal in memory only. Never persisted - share
                    # requests impersonate the owner tenant and
                    # ``current_share_request`` is the only write
                    # defense (#778), so this read path stays write-free.
                    # The return value is deliberately ignored: only the
                    # coach GET persists a heal (#780).
                    coach_module.heal_unclassified(raw_shots)
                    for shot in raw_shots:
                        t = shot.get("time")
                        if t is None:
                            continue
                        cls = shot.get(coach_module.FIELD_INTERVAL_CLASS)
                        shots.append(
                            CompareShotPoint(
                                shot_number=int(shot.get("shot_number", 0)),
                                time_after_beep=float(t) - float(audit_beep),
                                source=("manual" if shot.get("source") == "manual" else "detected"),
                                interval_class=(cls if cls in coach_module.COACH_INTERVAL_CLASSES else None),
                            )
                        )

        records.append(
            CompareShooterRecord(
                slug=slug,
                name=legacy.competitor_name or slug,
                video_ref=video_ref,
                beep_offset_in_clip=beep_offset,
                duration_seconds=duration_seconds,
                stage_time_seconds=(stage.time_seconds if stage is not None else None),
                shots=shots,
                identity=effective_identity(legacy, book),
            )
        )

    return CompareStageResponse(
        stage_number=stage_number,
        stage_name=stage_def.stage_name,
        shooters=records,
    )


def _stage_list(numbers: list[int]) -> str:
    ordered = sorted(set(numbers))
    word = "stage" if len(ordered) == 1 else "stages"
    return f"{word} {', '.join(str(n) for n in ordered)}"


def no_trims_refusal(bundles: list[CompareShooterBundle], stage_numbers: list[int], *, hosted: bool) -> str:
    """The compare-grid 400 when no selected stage has a trim to cut from.

    Read in the app (the Export page renders ``detail`` as its error line),
    so it names who is missing what and the in-app action that fixes it
    (Export, the Quick trims preset), not a CLI. The terminal command is
    offered only on a local install, where one exists; a hosted user has none. A
    selection where nobody even has a confirmed beep is not an export
    problem, so it points at Audit instead.

    Hosted needs no export (#1681): the render job cuts a missing stage
    trim from the source, so a stage is missing there only for want of a
    confirmed beep, a stage time or the source, and that is what it says.
    """
    wanted = set(stage_numbers)
    missing: dict[str, list[int]] = {}
    for bundle in bundles:
        for miss in bundle.missing_trims:
            if miss.stage_number in wanted:
                missing.setdefault(bundle.label, []).append(miss.stage_number)
    if not missing:
        then = "" if hosted else " export their trims,"
        return (
            f"Nothing to put in the grid: no shooter has a confirmed beep on {_stage_list(stage_numbers)}. "
            f"Confirm each shooter's beep in Audit,{then} then render the grid again."
        )
    who = "; ".join(f"{label} ({_stage_list(numbers)})" for label, numbers in sorted(missing.items()))
    if hosted:
        # Hosted needs no exported trim (#1681): the render cuts a missing
        # one from the footage, given a confirmed beep, a stage time and the
        # footage itself. Only those can be missing here.
        return (
            "The grid cuts a stage from each shooter's footage when it has no exported trim, which needs "
            "a confirmed beep, the stage time and the footage itself, and none of the selected stages has "
            f"all three. Missing: {who}. Confirm each of those beeps in Audit and check the stage time and "
            "footage on Footage, then render the grid again."
        )
    text = (
        "The grid is cut from each shooter's exported trims, or from their audit trims when those were "
        "cut for the current beep and stage time, and none of the selected stages has either. "
        f"Missing: {who}. Export them first: on Export, for each of those shooters, pick the Quick trims "
        "preset and press Export trims, then render the grid again."
    )
    return text + ' From a terminal, run "splitsmith match trims" to export every shooter\'s trims at once.'


@router.post("/api/match/compare-export")
async def export_compare_grid(request: Request, req: CompareGridRequest) -> JSONResponse:
    """Render the match's shooters as one grid MP4 (phase 0).

    Job-queued: a full-match grid re-encode runs for minutes. Hosted
    (#755), the job pulls the trims from storage and pushes the grid
    to the match-scoped key ``GET /api/match/exports/file/{name}``
    serves; no export-history row yet.

    Validation up-front so the SPA shows a clear error before
    queueing: empty selection, an unknown audio shooter, an
    unresolvable camera selector, and the no-trims-at-all case all
    400 rather than producing a grid of black tiles.
    """
    state = request.app.state.splitsmith_state
    match_root, match = _server._resolve_match_context(state)
    if not req.stage_numbers:
        raise HTTPException(status_code=400, detail="stage_numbers cannot be empty")
    try:
        _server._compare_output_stem(req.output_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if req.audio_from not in match.shooters:
        raise HTTPException(
            status_code=400,
            detail=(
                f"audio_from={req.audio_from!r} matches no shooter on this match. "
                f"Slugs available: {', '.join(match.shooters)}"
            ),
        )
    if req.summary_hold_seconds > 0 and not req.overlay:
        raise HTTPException(
            status_code=400,
            detail=(
                "summary_hold_seconds needs overlay=true: the end-of-stage summary is drawn "
                "from the overlay's own shot data and typography, so a hold on a clean grid "
                "would freeze on a blurred still with nothing written on it."
            ),
        )

    try:
        # Presence only: the render job pulls the trims; a request must not.
        bundles = _server._load_compare_bundles(
            match_root,
            match,
            cameras=req.cameras,
            inset=req.inset_camera,
            state=state,
            presence=StoragePresence(state.storage) if state.storage is not None else None,
            stage_numbers=req.stage_numbers,
        )
    except camera_select.CameraResolutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    present = sum(
        1 for bundle in bundles for number in req.stage_numbers if number in bundle.stages_by_number
    )
    if present == 0:
        raise HTTPException(
            status_code=400,
            detail=no_trims_refusal(bundles, req.stage_numbers, hosted=_hosted_mode_active()),
        )

    # A shooter other than the audio source can cover the selection --
    # ``present`` above only proves *someone* has a trim, not that the
    # chosen audio source does. Queueing that would burn a many-minute
    # 4K render before the user learns the result has no sound at all;
    # catch it here instead, naming the shooter and the fix (a
    # different audio source, or a different stage selection).
    audio_project = state.shooter_project(req.audio_from)
    audio_label = _server._shooter_label(match, match_root, req.audio_from, project=audio_project)
    audio_bundle = next((bundle for bundle in bundles if bundle.label == audio_label), None)
    if audio_bundle is None or not any(
        number in audio_bundle.stages_by_number for number in req.stage_numbers
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"audio_from={req.audio_from!r} ({audio_label}) has no trim on any of the "
                f"selected stages ({', '.join(str(n) for n in req.stage_numbers)}); the "
                "render would have no audio. Pick a different audio source, or select "
                f"stages {audio_label} has a trim for."
            ),
        )

    # #1691: hosted, every stage is its own job, queued here so serve's
    # wake fans them out to workers; the grid job (claimed first, by
    # priority) plans, publishes the plan for them, renders any stage still
    # queued itself, then stitches. Locally one process renders it all.
    planned = sorted({n for bundle in bundles for n in bundle.stages_by_number if n in req.stage_numbers})
    # One export (the limit counts it once) and one wake after the last
    # submit, so it counts every stage job and starts a worker for each
    # at once (export_slot holds the wakes).
    stage_jobs: dict[str, str] = {}
    grid_run = uuid.uuid4().hex
    # Each job says at once what it is (its canvas, its footage) so the
    # export panel's first estimate is the right size (spec 2026-10-25).
    split = state.storage is not None and len(planned) > 1
    stage_parts, grid_parts = _server._grid_queued_parts(req, bundles, planned) if split else ({}, None)
    async with export_slot(request):
        if split:
            for number in planned:
                stage_job = await state.jobs.submit(
                    kind="compare-grid-stage",
                    parts=stage_parts[number],
                    stage_number=number,
                    args={
                        "req": req,
                        "match_root": str(match_root),
                        "stage_number": number,
                        "grid_run": grid_run,
                    },
                )
                stage_jobs[str(number)] = stage_job.id
        job = await state.jobs.submit(
            kind="compare-grid",
            parts=grid_parts,
            args={"req": req, "match_root": str(match_root), "stage_jobs": stage_jobs, "grid_run": grid_run},
        )
    return JSONResponse(job.model_dump(mode="json"))


@router.get("/api/match/exports/file/{filename:path}")
def download_match_export_file(request: Request, filename: str) -> FileResponse:
    """Serve a match-scoped deliverable (a compare grid, #755).

    The match analogue of ``download_export_file``: local mode reads
    ``<match>/exports/<name>``; hosted mode mirrors it down from the
    match-scoped storage key first, since the worker that rendered it
    ran in another container. ``filename`` is confined to the match's
    ``exports/`` dir, so ``..`` traversal is a 400.
    """
    state = request.app.state.splitsmith_state
    match_root, _match = _server._resolve_match_context(state)
    exports_dir = (match_root / "exports").resolve()
    target = (exports_dir / filename).resolve()
    try:
        target.relative_to(exports_dir)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="download path must be inside the exports folder"
        ) from exc
    if not (target.exists() and target.is_file()):
        export_storage.pull_match_export_file(state.storage, current_match_id.get(), target)
    if not (target.exists() and target.is_file()):
        raise HTTPException(status_code=404, detail=f"export not found: {filename}")
    media_type = "video/mp4" if target.suffix.lower() == ".mp4" else "application/octet-stream"
    return FileResponse(target, media_type=media_type, filename=target.name)


@router.get("/api/match/shooters/{slug}/videos/stream", response_model=None)
def stream_shooter_video(
    request: Request,
    slug: str,
    path: str = Query(...),
    kind: Literal["auto", "trim", "source", "proxy", "web"] = Query("auto"),
    stage: int | None = Query(None),
) -> FileResponse | RedirectResponse:
    """Serve a video registered to any shooter in the bound match (#328).

    The Compare view streams from up to N shooters at once; the
    regular /api/videos/stream endpoint only sees the active
    shooter's registry. This thin wrapper validates ``path`` against
    the named shooter's project then redirects (hosted) or serves
    (local) the resolved video.

    ``kind=proxy`` follows the same semantics as the primary stream
    endpoint: in hosted mode returns 425 when the proxy is absent;
    in local mode falls back to source.

    Non-registered paths must match the logical ref grammar
    ``(exports|trimmed)/<name>.mp4`` (the shape ``_resolve_compare_trim``
    hands back for a Compare trim) - absolute paths, traversal shapes,
    non-.mp4 suffixes, and anything else are rejected with 404. Hosted
    mode serves a matching ref via a presigned redirect (storage.exists
    + serve_media); local mode serves it from disk under the shooter's
    exports/ or trimmed/ dir.
    """
    state = request.app.state.splitsmith_state
    match_root, match = _server._resolve_match_context(state)
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
    located = shooter_project.find_video(target, stage_number=stage)
    if located is not None:
        stage, video = located
        storage = state.storage
        raw_str = video.path.as_posix()
        # "hosted" here means "redirect the browser to a presigned URL". A
        # storage whose bytes aren't browser-reachable (FilesystemStorage)
        # takes the local byte-serving path below, same as no storage at all.
        is_hosted = storage is not None and storage.supports_presigned_get and not video.path.is_absolute()

        if kind == "proxy":
            if is_hosted and raw_str.startswith("raw/"):
                # hosted mode: redirect to proxy key; 425 if not yet generated
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
                return _server.serve_media(
                    storage,
                    proxy_key,
                    shooter_root / proxy_key,
                    content_type="video/mp4",
                )
            else:
                # local mode: try cached proxy, fall through to source on miss
                _resp = _server._serve_proxy(state, shooter_root, video)
                if _resp is not None:
                    return _resp
                # kind=proxy + local + no proxy - fall through to source

        if is_hosted:
            if kind == "web" and stage is not None:
                # the trim's streaming rendition (#1031); source when absent
                web_resp = _server._hosted_web_redirect(
                    storage,  # type: ignore[arg-type]
                    shooter_project,
                    shooter_root,
                    stage,
                    video,
                )
                if web_resp is not None:
                    return web_resp
            # hosted mode: redirect to source key; no mirroring
            return _server.serve_media(
                storage, raw_str, shooter_root / raw_str, content_type=_server.video_media_type(raw_str)
            )

        # local mode: mirror-then-serve (existing behavior)
        served_path = shooter_project.resolve_video_path(shooter_root, video.path).resolve()
        ensure_source_reachable(stage.stage_number if stage is not None else None, served_path)
        return FileResponse(served_path, media_type=_server.video_media_type(served_path))

    # Non-registered path: a Compare-produced trim, addressed by the
    # logical ref grammar ``(exports|trimmed)/<name>.mp4`` - the same
    # shape ``_resolve_compare_trim`` hands back. Absolute paths and
    # traversal shapes never reach a dir derivation at all; they just
    # fail the match.
    ref_match = re.fullmatch(r"(exports|trimmed)/([^/]+\.mp4)", path)
    if ref_match is None:
        raise HTTPException(status_code=404, detail=f"not a valid trim ref: {path}")
    dirkind, name = ref_match.group(1), ref_match.group(2)

    storage = state.storage
    scope = shooter_project._storage_scope
    if storage is not None and storage.supports_presigned_get and scope:
        key = f"{scope}/{dirkind}/{name}"
        if not storage.exists(key):
            raise HTTPException(status_code=404, detail=f"file not found: {key}")
        return _server.serve_media(storage, key, shooter_root / dirkind / name, content_type="video/mp4")

    # Local mode: resolve against the actual dirs (which may be
    # absolute overrides outside the shooter root); containment check
    # kept as defense-in-depth even though the ref grammar above
    # already rules out a filename with a path separator in it.
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
    target_dir = exports_dir if dirkind == "exports" else trimmed_dir
    resolved = (target_dir / name).resolve()
    try:
        resolved.relative_to(target_dir)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"file not found: {path}") from None
    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"file not found: {resolved}")
    return FileResponse(resolved, media_type="video/mp4")


@router.get("/api/match/beep-queue", response_model=BeepQueueResponse)
def get_beep_queue(request: Request, include_confirmed: bool = Query(default=False)) -> BeepQueueResponse:
    """Pending beep items across every shooter in the bound match.

    Surfaces three pending states per video (primary and secondary -
    every angle's beep anchors its own trim; ``ignored`` videos are
    skipped):
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
    Secondary items never gate anything - they exist so a wrong
    secondary auto-beep has a review surface at all.
    """
    state = request.app.state.splitsmith_state
    match_root, match = _server._resolve_match_context(state)
    # The whole match in one query (#1181); the threshold below and the
    # per-shooter loop both read from it.
    bundle = state.match_bundle(match)
    # Resolve the low-confidence threshold from any shooter's automation
    # settings; per-shooter thresholds aren't supported, the gate is
    # global to the match.
    threshold = 0.5
    for first_slug in match.shooters:
        proj_for_threshold = bundle.projects.get(first_slug)
        if proj_for_threshold is None:
            continue
        resolved = automation_settings.resolve_automation(
            project_override=proj_for_threshold.automation,
        )
        threshold = resolved.settings.beep_low_confidence_threshold
        break

    stage_lookup = {s.stage_number: s.stage_name for s in match.stages}
    groups: dict[int, BeepQueueStageGroup] = {}
    total_pending = 0
    total_confirmed = 0
    total_videos = 0

    # Proxy readiness drives the beep review "preview generating"
    # placeholder. Raw uploads and their proxies live in the tenant-root
    # pool shared across the match's shooters, so one list covers every
    # item. Local mode (no storage) reports ready - source streams direct.
    _storage = state.storage
    _proxy_keys: set[str] = set()
    if _storage is not None:
        _proxy_keys = {obj.path for obj in _storage.list("raw_proxy/")}

    # Snippet artifacts pushed by desktop for unconfirmed videos
    # (slice 3). One list per shooter, scoped to beep_review/ - the
    # shooters/ prefix as a whole is dominated by trimmed clips that
    # this endpoint never reads (#821).
    _match_id = current_match_id.get()
    _snippet_keys: set[str] = set()
    if _storage is not None and _match_id:
        for _slug in match.shooters:
            _snippet_keys.update(
                obj.path for obj in _storage.list(f"matches/{_match_id}/shooters/{_slug}/beep_review/")
            )

    def _snippet_ready(slug: str, video_id: str) -> bool:
        if not _snippet_keys:
            return False
        base = f"matches/{_match_id}/shooters/{slug}/beep_review/{video_id}"
        return f"{base}.m4a" in _snippet_keys and f"{base}.peaks.json" in _snippet_keys

    for slug in match.shooters:
        proj = bundle.projects.get(slug)
        if proj is None:
            continue
        for stage in proj.stages:
            if stage.skipped:
                continue
            # Primary first, then secondaries in the order they were
            # added - the same ordering the Audit page's cam column
            # uses, so the queue reads top-to-bottom like the tiles.
            primary = next((v for v in stage.videos if v.role == "primary"), None)
            secondaries = sorted(
                (v for v in stage.videos if v.role == "secondary"),
                key=lambda v: v.added_at,
            )
            stage_videos = ([primary] if primary is not None else []) + secondaries
            if not stage_videos:
                continue
            grp = groups.setdefault(
                stage.stage_number,
                BeepQueueStageGroup(
                    stage_number=stage.stage_number,
                    stage_name=stage_lookup.get(stage.stage_number, stage.stage_name),
                    items=[],
                    total_videos=0,
                    confirmed=0,
                ),
            )
            for video in stage_videos:
                total_videos += 1
                grp.total_videos += 1
                # Status classification.
                if video.beep_time is None:
                    status = "missing"
                elif video.beep_reviewed:
                    grp.confirmed += 1
                    total_confirmed += 1
                    if not include_confirmed:
                        continue
                    status = "confirmed"
                elif video.beep_confidence is not None and video.beep_confidence < threshold:
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
                    for cand in (video.beep_candidates or [])[:3]
                ]
                grp.items.append(
                    BeepQueueItem(
                        slug=slug,
                        shooter_name=proj.competitor_name or slug,
                        stage_number=stage.stage_number,
                        stage_name=stage_lookup.get(stage.stage_number, stage.stage_name),
                        role=video.role,
                        video_id=video.video_id,
                        video_path=video.path.as_posix(),
                        beep_time=video.beep_time,
                        beep_confidence=video.beep_confidence,
                        beep_reviewed=video.beep_reviewed,
                        status=status,
                        alt_candidates=alts,
                        aligned_time=video.beep_aligned_time if video.role != "primary" else None,
                        proxy_ready=_server._proxy_ready_for(_storage, _proxy_keys, video.path.as_posix()),
                        snippet_ready=_snippet_ready(slug, video.video_id),
                        trim_stale=(video.beep_time is not None and not video.processed.get("trim", False)),
                    )
                )

    ordered_stages = sorted(groups.values(), key=lambda g: g.stage_number)
    return BeepQueueResponse(
        total_items=total_videos,
        pending_count=total_pending,
        confirmed_count=total_confirmed,
        stages=ordered_stages,
        origin=current_match_origin.get() or "local",
        capabilities=_server._serialized_capabilities(),
    )


@router.post("/api/match/beep-queue/confirm", response_model=BeepQueueResponse)
async def confirm_beep_in_queue(request: Request, req: BeepQueueConfirmRequest) -> BeepQueueResponse:
    """Confirm a beep on any shooter without changing the bound state.

    Writes through to the named shooter's ``project.json`` directly so
    the rest of the SPA's state (active shooter) is unaffected.
    When ``time`` is supplied, it overrides ``beep_time``; the call
    always sets ``beep_reviewed = True`` and then runs the same
    post-review chain as the per-video endpoint (stub audit doc,
    ``shot_detect`` for a primary with a cached trim). Before that
    was shared, a low-confidence primary confirmed here stayed
    trimmed with no shots until someone opened Audit and pressed Run.
    """
    state = request.app.state.splitsmith_state
    match_root, match = _server._resolve_match_context(state)
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
        _server._check_beep_time(target_video, float(req.time))
        target_video.beep_time = float(req.time)
        target_video.beep_source = "manual" if req.source == "manual" else "detected"
    if target_video.beep_time is None:
        raise HTTPException(
            status_code=400,
            detail="cannot mark a beep reviewed before one has been detected",
        )
    target_video.beep_reviewed = True
    proj.save(shooter_root)
    await _server._after_beep_reviewed(state, req.slug, req.stage_number, target_video)
    return get_beep_queue(request)


@router.post("/api/match/desktop-commands")
async def request_desktop_command(request: Request, req: DesktopCommandRequest) -> JSONResponse:
    """Ask the desktop to run something for this match (#1100).

    201 with the new command, 200 with the active one when the same
    request is already waiting or running. 409 ``not_a_mirror`` for a
    hosted-native match. For ``shot_detect`` the stage is checked the
    way the local route checks it, so the phone hears "no beep yet"
    now rather than from the desktop later, and the stage audit's
    revision is recorded: the desktop refuses a reset against a stage
    that changed after this request. For ``render_upload`` the render
    settings are validated as a ``MatchExportRequest`` that uploads an
    MP4; the pads and sources are the desktop's to check.
    """
    state = request.app.state.splitsmith_state
    from ..db.desktop_commands import COMMAND_KINDS

    store = _server._command_store(state)
    match_id = _command_match_id()
    row = await state.matches_store.get(match_id) if state.matches_store is not None else None
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    if row.origin != "desktop":
        raise HTTPException(status_code=409, detail="not_a_mirror")
    if req.kind not in COMMAND_KINDS:
        raise HTTPException(status_code=422, detail=f"unknown command kind {req.kind!r}")
    expected_revision: str | None = None
    args: dict[str, Any] = dict(req.args)
    if req.kind == "shot_detect":
        if req.slug is None or req.stage_number is None:
            raise HTTPException(status_code=422, detail="shot_detect needs slug and stage_number")
        project = state.shooter_project(req.slug)
        try:
            stage = project.stage(req.stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        primary = stage.primary()
        if primary is None:
            raise HTTPException(status_code=400, detail=f"stage {req.stage_number} has no primary video")
        if primary.beep_time is None:
            raise HTTPException(
                status_code=400, detail=f"stage {req.stage_number} primary has no beep_time yet"
            )
        if stage.time_seconds <= 0:
            raise HTTPException(status_code=400, detail=f"stage {req.stage_number} has no stage time yet")
        stored, _ = state.load_audit(req.slug, req.stage_number)
        expected_revision = audit_revision(stored)
        args = {"reset": bool(args.get("reset", True))}
    elif req.kind == "render_upload":
        if req.slug is None or req.stage_number is not None:
            raise HTTPException(status_code=422, detail="render_upload needs slug and no stage_number")
        state.shooter_project(req.slug)  # an unknown shooter 404s here, as for shot_detect
        try:
            export_req = MatchExportRequest.model_validate(args.get("request"))
        except ValidationError as exc:
            first = exc.errors()[0]
            raise HTTPException(
                status_code=422, detail=f"render settings are not valid: {first.get('msg', 'invalid')}"
            ) from exc
        if not export_req.youtube_upload:
            raise HTTPException(status_code=422, detail="render_upload needs youtube_upload")
        # The pads and sources are checked on the desktop: a mirror has
        # no sources here, and the desktop's project owns the buffers.
        args = {"request": export_req.model_dump(mode="json")}
    command, created = await store.request(
        match_id=match_id,
        kind=req.kind,
        slug=req.slug,
        stage_number=req.stage_number,
        args=args,
        expected_revision=expected_revision,
    )
    return JSONResponse(command.model_dump(mode="json"), status_code=201 if created else 200)


@router.get("/api/match/desktop-commands")
async def list_desktop_commands(request: Request) -> JSONResponse:
    """This match's recent requests plus the desktop presence, so the
    phone's status poll is one request."""
    state = request.app.state.splitsmith_state
    store = _server._command_store(state)
    commands = await store.list_for_match(_command_match_id())
    return JSONResponse(
        {
            "commands": [c.model_dump(mode="json") for c in commands],
            "presence": await _server._desktop_presence(state),
        }
    )


@router.post("/api/match/desktop-commands/{command_id}/cancel")
async def cancel_desktop_command(request: Request, command_id: str) -> JSONResponse:
    state = request.app.state.splitsmith_state
    store = _server._command_store(state)
    command = await store.cancel(command_id, match_id=_command_match_id())
    if command is None:
        raise HTTPException(status_code=404, detail="not found")
    return JSONResponse(command.model_dump(mode="json"))
