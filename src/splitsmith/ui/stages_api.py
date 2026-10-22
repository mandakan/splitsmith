"""The per-stage routes: ``/api/shooters/{slug}/stages/...``.

Lifted out of ``server.create_app`` (#680). Handlers read the process state
from ``request.app.state.splitsmith_state``; helpers shared with other route
families are called through the ``server`` module so a test that patches
``server.<helper>`` still reaches them. Those helpers and the request models
move here, or to their own domain modules, as the split continues.
"""

from __future__ import annotations

import copy
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import coach as coach_module
from .. import coach_distributions as coach_distributions_module
from .. import events as events_module
from .. import report, stage_details
from .. import waveform as waveform_helpers
from ..access import Feature, FeatureRequiredError
from ..audit_clock import rebase_to_clip, stamp_clip_beep
from ..audit_data import audit_shots_to_engine_shots, is_kept_shot
from ..audit_revision import REVISION_FIELD, AuditRevisionConflictError, audit_revision
from ..beep import detect as beep_detect
from ..comment_identity import (
    MAX_AUTHOR_KEY_LEN,
    MIN_AUTHOR_KEY_LEN,
    author_code_for,
    derive_handle,
    hash_author_key,
)
from ..config import BeepDetectConfig
from ..detection_merge import new_event_id as _new_event_id
from ..detection_merge import now_iso as _now_iso
from ..fixture_schema import CameraMount
from ..match_project import STUB_AUDIT_DETECTION, MatchProject, StageEntry, StageVideo
from ..shot_id import ensure_shot_ids
from . import audio as audio_helpers
from . import beep_tone_check
from . import server as _server
from .access_gate import require_feature
from .comments import (
    AUTHOR_KEY_HEADER,
    STAGE_COMMENT_CAP,
    CommentCreateRequest,
    CommentListResponse,
    CommentOut,
    CommentOwnerListResponse,
    to_out,
)
from .context import current_match_id, current_match_origin, current_share_request, current_share_viewer
from .http_errors import ensure_source_reachable, source_unreachable
from .server import BulkCameraSetItem, CoachShotPatchRequest, StageEventIn
from .state import AppState

logger = logging.getLogger(__name__)

router = APIRouter()


def _kept_audit_shots(shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Kept shots from an audit doc's ``shots[]``.

    Thin wrapper over :func:`splitsmith.audit_data.is_kept_shot` -- the
    same filter :func:`audit_shots_to_engine_shots` applies -- so "kept"
    has exactly one definition project-wide, not a parallel one here.
    """
    return [s for s in shots if is_kept_shot(s)]


def _caller_author_key_hash(request: Request) -> str | None:
    """Hashed author key from the request header, or None.

    Optional on reads: a caller who sends none (or an implausible one)
    gets ``mine=False`` everywhere, which is right for a first-time
    reader.
    """
    raw = request.headers.get(AUTHOR_KEY_HEADER, "").strip()
    if not _server._valid_author_key_len(raw):
        return None
    return hash_author_key(raw)


def _require_author_key(request: Request) -> str:
    """Author key for a write. 422 rather than 404 when missing or
    implausible: the caller already passed the scope gate, so an honest
    error is right."""
    raw = request.headers.get(AUTHOR_KEY_HEADER, "").strip()
    if not _server._valid_author_key_len(raw):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "author_key_required",
                "message": (
                    f"{AUTHOR_KEY_HEADER} must carry an opaque client key between "
                    f"{MIN_AUTHOR_KEY_LEN} and {MAX_AUTHOR_KEY_LEN} characters"
                ),
            },
        )
    return raw


class StageAttentionBody(BaseModel):
    """POST .../attention body (triage slice 4)."""

    flagged: bool
    note: str | None = Field(default=None, max_length=280)


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


class BulkCameraSetRequest(BaseModel):
    """Body for POST /api/shooters/{slug}/stages/camera/bulk-set.

    Applies ``camera_mount`` / ``camera_make`` / ``camera_model`` overrides
    to every listed (stage_number, video_id) pair in a single project save.
    The ``set_*`` flags distinguish "leave unchanged" from "set to null / (auto)",
    so clearing an override is different from a no-op.

    At least one of ``set_mount`` or ``set_model`` must be True; an all-false
    body is rejected with 400.
    """

    items: list[BulkCameraSetItem]
    set_mount: bool = False
    mount: str | None = None  # CameraMount value or None; honoured only when set_mount is True
    set_model: bool = False
    make: str | None = None  # must be supplied together with model, or both None
    model: str | None = None


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


class BeepWindowRequest(BaseModel):
    """Body for PUT /api/shooters/{slug}/stages/{n}/videos/{vid}/beep-window."""

    start_s: float
    end_s: float


class SkipStageRequest(BaseModel):
    """Body for POST /api/stages/{n}/skip. Toggles ``stage.skipped``."""

    skipped: bool


class StageEventsPutRequest(BaseModel):
    """The whole event list for one stage (spec 2026-10-08) plus the audit
    revision the client loaded; a stale one is a 409 like the audit PUT."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    events: list[StageEventIn]
    revision: str | None = Field(default=None, alias=REVISION_FIELD)


class StageNotePatchRequest(BaseModel):
    """Body for PATCH .../stages/{n}/stage-note (#1376): the stage note's new
    text (empty or null clears it) and the audit revision the client loaded;
    a stale one is a 409 like the events PUT."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    stage_note: str | None = Field(default=None, max_length=10_000)
    revision: str | None = Field(default=None, alias=REVISION_FIELD)


async def _submit_trim(
    state: AppState,
    slug: str,
    stage_number: int,
    stage: StageEntry,
    video: StageVideo,
    project: MatchProject,
    *,
    force: bool = False,
) -> JSONResponse:
    """Validate + dedupe + queue a trim job for ``video``. ``force``
    re-cuts a trim the cache would reuse.

    Pre-flight checks (reachability, beep present, stage_time set)
    apply equally to primary and secondary -- both need a beep_time
    and a non-zero stage_time to define the trim window.
    """
    # ``source_present``, not ``resolve_video_path``: ffmpeg runs in the
    # trim job, not here, so this preflight has no use for the bytes
    # (#638).
    root = state.shooter_root(slug)
    if not project.source_present(root, video.path):
        raise source_unreachable(stage_number, root / video.path)
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
        kind="trim", stage_number=stage_number, shooter_slug=slug, video_id=video.video_id
    )
    if existing is not None:
        return JSONResponse(existing.model_dump(mode="json"))
    job = await state.jobs.submit(
        kind="trim",
        stage_number=stage_number,
        shooter_slug=slug,
        video_id=video.video_id,
        args={
            "slug": slug,
            "stage_number": stage_number,
            "video_id": video.video_id,
            **({"force": True} if force else {}),
        },
    )
    return JSONResponse(job.model_dump(mode="json"))


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


def _resolve_video_audio(
    state: AppState, slug: str, project: MatchProject, stage_number: int, video: StageVideo
) -> audio_helpers.AuditAudioResult | JSONResponse:
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
    audio_path = audio_helpers.published_video_audio(
        state.shooter_root(slug),
        stage_number,
        video,
        project=project,
        source=_server._local_source(state, project, slug, video),
    )
    if audio_path is None:
        return _server._audio_pending(
            state, slug, "video", stage_number=stage_number, video_id=video.video_id
        )
    return audio_helpers.AuditAudioResult(
        audio_path=audio_path,
        beep_in_clip=video.beep_time,
        trimmed=False,
    )


def _parse_band(raw: str | None) -> tuple[float, float] | None:
    """``LO-HI`` in Hz, 100 <= LO < HI <= 12000, or None for the full range."""
    if raw is None:
        return None
    try:
        lo_s, hi_s = raw.split("-")
        lo, hi = float(lo_s), float(hi_s)
    except ValueError:
        raise HTTPException(status_code=422, detail="band is LO-HI in Hz") from None
    if not (100 <= lo < hi <= 12000):
        raise HTTPException(status_code=422, detail="band is LO-HI in Hz, 100 <= LO < HI <= 12000")
    return (lo, hi)


def _beep_snippet_key(state: AppState, slug: str, video_id: str, suffix: str) -> str | None:
    """Build the desktop-pushed snippet key, or None with no match/storage bound."""
    match_id = current_match_id.get()
    if state.storage is None or not match_id:
        return None
    return f"matches/{match_id}/shooters/{slug}/beep_review/{video_id}{suffix}"


def _set_needs_attention(payload: dict[str, Any], *, flagged: bool, note: str | None = None) -> None:
    """Write the triage flag as a full object so sync LWW always has a
    timestamp to compare - clears keep the object with flagged=False."""
    now = _now_iso()
    payload["needs_attention"] = {
        "flagged": flagged,
        "flagged_at": now if flagged else None,
        "note": (note or None) if flagged else None,
        "updated_at": now,
    }


def _apply_shot_coach_patch(
    state: AppState,
    slug: str,
    stage_number: int,
    body: CoachShotPatchRequest,
    match_shot: Callable[[dict[str, Any]], bool],
    describe: str,
) -> JSONResponse:
    """Shared body for the by-number and by-id coach PATCH routes."""
    stg, project, beep_in_clip = _server._coach_stage(state, slug, stage_number)
    cfg = coach_module.auto_classify_config()

    def merge(stored: dict[str, Any] | None, version: int) -> dict[str, Any]:
        payload = _server._require_audit_doc(stored, stage_number)
        if body.expected_version is not None and body.expected_version != version:
            raise HTTPException(status_code=409, detail="version_conflict")
        shots = payload.get("shots") or []
        if not isinstance(shots, list):
            raise HTTPException(status_code=500, detail="audit shots is not a list")
        target = next((s for s in shots if isinstance(s, dict) and match_shot(s)), None)
        if target is None:
            raise HTTPException(
                status_code=404,
                detail=f"{describe} not found in stage {stage_number}",
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

        # #775: a ``clear_class: true`` patch drops the shot's class, which
        # would re-open the partial-classification state the invariant
        # rules out. Re-running the classifier heals it back to the rule's
        # auto verdict. It also applies the rule to every other unset/auto
        # entry (manual ones are kept), against confirmed regions only, so
        # a note typed on Coach can move an auto class only where the
        # stored one disagrees with the current rule (``stale``); an auto
        # proposal never moves one.
        _server._classify_doc(payload, stage_number, cfg)

        events = list(payload.get("audit_events") or [])
        events.append(
            {
                "id": _new_event_id(),
                "ts": _now_iso(),
                "kind": "coach_patch",
                "payload": {
                    "shot_id": target.get("id"),
                    "shot_number": target.get("shot_number"),
                    "fields": {
                        **coach_module.read_coach_fields(target),
                        **(
                            {
                                coach_module.FIELD_COACHING_NOTE: _server._note_digest(
                                    target[coach_module.FIELD_COACHING_NOTE]
                                )
                            }
                            if isinstance(target.get(coach_module.FIELD_COACHING_NOTE), str)
                            else {}
                        ),
                    },
                },
            }
        )
        payload["audit_events"] = events
        return payload

    # The response carries the version the save produced, never the one
    # read (#844): the client uses it as ``expected_version`` on its next
    # positional patch.
    payload, version = state.update_audit_at(slug, stage_number, merge)
    return JSONResponse(
        _server._build_coach_response(
            state,
            slug,
            payload,
            beep_in_clip,
            stg,
            project,
            cfg,
            version,
            capacity=_server._coach_capacity(state, slug, project),
        )
    )


@router.get(
    "/api/shooters/{slug}/stages/{stage_number}/comments",
    # No single response_model: an owner_view caller gets
    # CommentOwnerListResponse, everyone else gets CommentListResponse.
    # See CommentOwnerOut's docstring (fix round 1, F4) for why this is
    # two types rather than one model plus exclude_none.
    response_model=None,
)
async def list_stage_comments(
    slug: str,
    stage_number: int,
    request: Request,
) -> CommentListResponse | CommentOwnerListResponse:
    """The comment thread. Readable through any share scope, and by
    the owner on their own routes."""
    state = request.app.state.splitsmith_state
    store = state.comments
    mid = current_match_id.get()
    if store is None or mid is None:
        raise HTTPException(status_code=404, detail="not found")
    _server._require_comment_scope(state, slug, stage_number)
    caller_hash = _caller_author_key_hash(request)
    owner_view = not current_share_request.get()
    comments = await store.list_for_stage(mid, slug, stage_number)
    if owner_view:
        # to_out's declared return type is CommentOut (the narrower,
        # anonymous-safe type); at runtime, owner_view=True makes it
        # return the CommentOwnerOut subtype this field actually needs.
        return CommentOwnerListResponse(
            comments=[
                to_out(c, author_key_hash=caller_hash, owner_view=True)  # type: ignore[misc]
                for c in comments
            ]
        )
    return CommentListResponse(
        comments=[to_out(c, author_key_hash=caller_hash, owner_view=False) for c in comments]
    )


@router.post(
    "/api/shooters/{slug}/stages/{stage_number}/comments",
    response_model=CommentOut,
    status_code=201,
)
async def create_stage_comment(
    slug: str,
    stage_number: int,
    req: CommentCreateRequest,
    request: Request,
) -> CommentOut:
    """Post a comment. Reachable only through a comment-scoped share
    link: the write allowlist plus the scope gate in ``_share_alias``
    are what admit this, and an owner posting on their own footage is
    deliberately not a use case v1 serves."""
    state = request.app.state.splitsmith_state
    store = state.comments
    mid = current_match_id.get()
    share_token_id = getattr(request.state, "share_token_id", None)
    if store is None or mid is None or share_token_id is None:
        raise HTTPException(status_code=404, detail="not found")
    _server._require_comment_scope(state, slug, stage_number)
    author_key = _require_author_key(request)
    # Both keys, token first: the author key is a client-chosen
    # header, so it bounds nothing on its own (final review, I5).
    # share_token_id is not attacker-chosen - it is the link they
    # were given - so it is the bound that holds under rotation.
    if not request.app.state.comment_limiter.allow(
        f"token:{share_token_id}",
        f"key:{hash_author_key(author_key)}",
        now=time.monotonic(),
    ):
        raise HTTPException(
            status_code=429,
            detail={"code": "comment_rate_limited", "message": "too many comments, slow down"},
        )
    if await store.count_for_stage(mid, slug, stage_number) >= STAGE_COMMENT_CAP:
        raise HTTPException(
            status_code=429,
            detail={"code": "comment_stage_cap", "message": "this stage has too many comments"},
        )
    viewer = current_share_viewer.get()
    if viewer is not None and isinstance(viewer.display_name, str) and viewer.display_name.strip():
        author_kind = "account"
        author_user_id = viewer.id
        author_handle = viewer.display_name.strip()
    else:
        # Includes a signed-in account that never set a display name:
        # an email address is not a name they chose to publish, and an
        # empty string is not a name at all.
        author_kind = "handle"
        author_user_id = None
        author_handle = derive_handle(author_key)
    author_code = author_code_for(
        author_kind=author_kind,
        author_user_id=author_user_id,
        author_key_hash=hash_author_key(author_key),
    )
    created = await store.create(
        match_id=mid,
        slug=slug,
        stage_number=stage_number,
        anchor_t=req.anchor_t,
        anchor_kind=req.anchor_kind,
        anchor_shot_id=req.anchor_shot_id,
        author_kind=author_kind,
        author_user_id=author_user_id,
        author_handle=author_handle,
        author_key_hash=hash_author_key(author_key),
        author_code=author_code,
        share_token_id=share_token_id,
        body=req.body,
    )
    return to_out(created, author_key_hash=created.author_key_hash, owner_view=False)


@router.delete(
    "/api/shooters/{slug}/stages/{stage_number}/comments/{comment_id}",
    status_code=204,
)
async def delete_stage_comment(
    slug: str,
    stage_number: int,
    comment_id: str,
    request: Request,
) -> Response:
    """Delete one comment. An anonymous caller may delete only their
    own, matched on the hashed author key; the owner may delete any.
    Both refusals are the same 404."""
    state = request.app.state.splitsmith_state
    store = state.comments
    mid = current_match_id.get()
    if store is None or mid is None:
        raise HTTPException(status_code=404, detail="not found")
    _server._require_comment_scope(state, slug, stage_number)
    if current_share_request.get():
        author_key = _require_author_key(request)
        ok = await store.delete_own(
            comment_id,
            match_id=mid,
            slug=slug,
            stage_number=stage_number,
            author_key_hash=hash_author_key(author_key),
        )
    else:
        ok = await store.delete_as_owner(comment_id, match_id=mid, slug=slug, stage_number=stage_number)
    if not ok:
        raise HTTPException(status_code=404, detail="not found")
    return Response(status_code=204)


# Gated up front, not left to the backstop: the route writes the window
# and then queues the detection that is its whole point, so a refusal
# after the write would answer 403 for a change that committed.
@router.put(
    "/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/beep-window",
    dependencies=[Depends(require_feature(Feature.hosted_compute))],
)
async def set_beep_window(
    request: Request, slug: str, stage_number: int, video_id: str, body: BeepWindowRequest
) -> JSONResponse:
    """Persist a manual beep search window and re-run detection.

    The take overview's drag-to-adjust lands here. Setting a window
    wipes the current beep (a new window is a new claim about where
    the beep lives) and invalidates the trim cache, mirroring the
    manual beep-time endpoint's semantics.
    """
    state = request.app.state.splitsmith_state
    if body.start_s < 0 or body.end_s <= body.start_s:
        raise HTTPException(status_code=422, detail="end_s must be greater than start_s >= 0")
    in_flight = await state.jobs.find_active(
        kind="detect_beep", stage_number=stage_number, shooter_slug=slug, video_id=video_id
    )
    if in_flight is not None:
        raise HTTPException(
            status_code=409,
            detail="detection already running for this video - wait for it to finish, then set the window",  # noqa: E501
        )
    project, stage, video = _server._resolve_stage_video(state, slug, stage_number, video_id)
    root = state.shooter_root(slug)
    video.beep_window = (body.start_s, body.end_s)
    video.beep_window_source = "manual"
    video.beep_time = None
    video.beep_source = None
    video.beep_confidence = None
    video.beep_peak_amplitude = None
    video.beep_duration_ms = None
    video.beep_candidates = []
    video.beep_ranker_version = None
    video.beep_reviewed = False
    video.beep_auto_detect_failed = False
    video.processed["beep"] = False
    video.processed["trim"] = False
    audio_helpers.invalidate_video_audit_trim(root, stage_number, video, project=project)
    project.save(root)
    return await _server._submit_detect_beep(state, slug, stage_number, video)


@router.post("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/detect-beep")
async def detect_beep_for_video(
    request: Request, slug: str, stage_number: int, video_id: str, force: bool = False
) -> JSONResponse:
    """Submit a beep-detection job for ``video_id`` on ``stage_number``.

    Generic over role (primary or secondary): each video gets its own
    detect job, its own beep timestamp, its own short-GOP trim, and
    its own dedupe slot in the registry so the user can run primary +
    Cam 2 + Cam 3 in parallel. Shot detection auto-chains only for
    primary results; secondaries align to the primary timeline by
    their own beep so they don't need their own shot timeline.
    """
    state = request.app.state.splitsmith_state
    project, _stage, video = _server._resolve_stage_video(state, slug, stage_number, video_id)
    # ``source_present``, not ``resolve_video_path``: the bytes belong to
    # the queued job, so resolving here downloaded the raw source into
    # the API container just to decide whether to queue (#638).
    root = state.shooter_root(slug)
    if not project.source_present(root, video.path):
        raise source_unreachable(stage_number, root / video.path)
    if video.beep_source == "manual" and not force:
        raise HTTPException(
            status_code=409,
            detail=(
                "video has a manual beep override; pass ?force=true to "
                "replace it with auto-detected output"
            ),
        )
    return await _server._submit_detect_beep(state, slug, stage_number, video)


@router.post("/api/shooters/{slug}/stages/{stage_number}/detect-beep")
async def detect_beep(request: Request, slug: str, stage_number: int, force: bool = False) -> JSONResponse:
    """Submit a beep-detection job for the stage's primary.

    Backward-compat shim that resolves the primary's id and forwards to
    the per-video pipeline. Returns a Job snapshot immediately; the SPA
    polls ``/api/jobs/{id}`` for progress and refetches ``/api/project``
    on completion.
    """
    state = request.app.state.splitsmith_state
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
    # ``source_present``, not ``resolve_video_path`` -- see the per-video
    # detect-beep endpoint above (#638).
    root = state.shooter_root(slug)
    if not project.source_present(root, primary.path):
        raise source_unreachable(stage_number, root / primary.path)
    if primary.beep_source == "manual" and not force:
        raise HTTPException(
            status_code=409,
            detail=(
                "stage has a manual beep override; pass ?force=true to "
                "replace it with auto-detected output"
            ),
        )
    return await _server._submit_detect_beep(state, slug, stage_number, primary)


@router.post("/api/shooters/{slug}/stages/{stage_number}/trim")
async def trim_stage(request: Request, slug: str, stage_number: int, force: bool = False) -> JSONResponse:
    """Submit an audit-mode short-GOP trim job for the stage's primary.

    Backward-compat shim: forwards to the per-video pipeline. Returns
    a Job snapshot. Idempotent on the worker side: when the cached
    MP4 is newer than the source, the job completes near-instantly
    without re-encoding.
    """
    state = request.app.state.splitsmith_state
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
    return await _submit_trim(state, slug, stage_number, stage, primary, project, force=force)


@router.post("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/trim")
async def trim_for_video(
    request: Request, slug: str, stage_number: int, video_id: str, force: bool = False
) -> JSONResponse:
    """Submit an audit-mode trim job for ``video_id`` on ``stage_number``."""
    state = request.app.state.splitsmith_state
    project, stage, video = _server._resolve_stage_video(state, slug, stage_number, video_id)
    return await _submit_trim(state, slug, stage_number, stage, video, project, force=force)


@router.post("/api/shooters/{slug}/stages/shot-detect")
async def shot_detect_all_endpoint(request: Request, slug: str, reset: bool = False) -> JSONResponse:
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
    state = request.app.state.splitsmith_state
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
        existing = await state.jobs.find_active(
            kind="shot_detect", stage_number=stage_number, shooter_slug=slug
        )
        if existing is not None:
            jobs.append(existing.model_dump(mode="json"))
            continue
        job = await state.jobs.submit(
            kind="shot_detect",
            stage_number=stage_number,
            shooter_slug=slug,
            args={"slug": slug, "stage_number": stage_number, "reset": reset},
        )
        jobs.append(job.model_dump(mode="json"))
    return JSONResponse({"jobs": jobs, "skipped": skipped})


@router.post("/api/shooters/{slug}/stages/{stage_number}/shot-detect")
async def shot_detect_endpoint(
    request: Request, slug: str, stage_number: int, reset: bool = False
) -> JSONResponse:
    """Submit a shot-detection job for the stage's audit clip.

    Returns a Job snapshot. Idempotent dedupe via the registry: a second
    click while one is running adopts the existing job. The candidate
    list lands in the audit JSON's ``_candidates_pending_audit`` block,
    which is what the audit screen reads to render markers.

    ``reset=true`` wipes ``shots[]`` first so the user can start over.
    """
    state = request.app.state.splitsmith_state
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

    existing = await state.jobs.find_active(kind="shot_detect", stage_number=stage_number, shooter_slug=slug)
    if existing is not None:
        return JSONResponse(existing.model_dump(mode="json"))
    job = await state.jobs.submit(
        kind="shot_detect",
        stage_number=stage_number,
        shooter_slug=slug,
        args={"slug": slug, "stage_number": stage_number, "reset": reset},
    )
    return JSONResponse(job.model_dump(mode="json"))


@router.post("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/beep")
async def override_beep_for_video(
    request: Request, slug: str, stage_number: int, video_id: str, req: BeepOverrideRequest
) -> JSONResponse:
    """Manually set or clear ``video``'s beep timestamp.

    ``req.beep_time = None`` clears back to "no beep yet"; otherwise
    the value (in seconds, must be >= 0) is taken as authoritative
    with ``beep_source="manual"``. Same dedupe + auto-trim chain as
    the legacy primary endpoint, just keyed per video.

    When ``beep_time`` is set on a video that belongs to a sequential
    take (multi-stage raw, no scoreboard timestamps), also advances the
    chain by submitting detect_beep for the next uncovered stage - the
    same advance the auto-detect job performs on success (Task 5).
    """
    state = request.app.state.splitsmith_state
    project, stage, video = _server._resolve_stage_video(state, slug, stage_number, video_id)
    if req.beep_time is not None:
        _server._check_beep_time(video, req.beep_time)
    _server._apply_beep_override(state, slug, project, stage, video, req.beep_time)
    beep_tone_check.record_tone_offset(state.shooter_root(slug), project, stage.stage_number, video)
    project.save(state.shooter_root(slug))
    # Mirrors mark state only: no raw media exists hosted-side, so
    # there is nothing to trim or detect against. Desktop re-derives
    # on its next sync pull (bidirectional sync design).
    if req.beep_time is not None and current_match_origin.get() != "desktop":
        await _server._maybe_chain_trim(state, slug, stage, video)
        try:
            await _server._advance_sequential_chain(state, slug, project, video, stage_number)
        except FeatureRequiredError as exc:
            _server._log_chain_skipped("detect_beep", slug, stage_number, exc)
    return JSONResponse(project.model_dump(mode="json"))


@router.post("/api/shooters/{slug}/stages/{stage_number}/beep")
async def override_beep(
    request: Request, slug: str, stage_number: int, req: BeepOverrideRequest
) -> JSONResponse:
    """Backward-compat shim: manually set / clear the primary's beep."""
    state = request.app.state.splitsmith_state
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
    _server._apply_beep_override(state, slug, project, stage, primary, req.beep_time)
    project.save(state.shooter_root(slug))
    if req.beep_time is not None:
        await _server._maybe_chain_trim(state, slug, stage, primary)
        try:
            await _server._advance_sequential_chain(state, slug, project, primary, stage_number)
        except FeatureRequiredError as exc:
            _server._log_chain_skipped("detect_beep", slug, stage_number, exc)
    return JSONResponse(project.model_dump(mode="json"))


@router.post("/api/shooters/{slug}/stages/{stage_number}/time")
def set_stage_time(request: Request, slug: str, stage_number: int, req: StageTimeRequest) -> JSONResponse:
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
    state = request.app.state.splitsmith_state
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
    if stage.scorecard is not None:
        # A hit factor worked out from the old time follows the new one.
        stage.scorecard = stage_details.derive(
            stage.scorecard,
            stage.time_seconds,
            stage_details.power_factor_for(project.competitor_division, project.power_factor),
        )
    project.save(state.shooter_root(slug))
    return JSONResponse(project.model_dump(mode="json"))


@router.post("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/beep/snap")
def snap_beep_for_video(
    request: Request, slug: str, stage_number: int, video_id: str, req: BeepSnapRequest
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
    state = request.app.state.splitsmith_state
    project, _stage, video = _server._resolve_stage_video(state, slug, stage_number, video_id)
    source = project.resolve_video_path(state.shooter_root(slug), video.path)
    ensure_source_reachable(stage_number, source)
    if req.hint_time < 0.0:
        raise HTTPException(status_code=400, detail="hint_time must be >= 0")
    if req.window_s <= 0.0:
        raise HTTPException(status_code=400, detail="window_s must be > 0")

    audio_path = audio_helpers.published_video_audio(
        state.shooter_root(slug),
        stage_number,
        video,
        project=project,
        source=_server._local_source(state, project, slug, video),
    )
    if audio_path is None:
        return _server._audio_pending(
            state, slug, "video", stage_number=stage_number, video_id=video.video_id
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


@router.post("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/beep/select")
async def select_beep_candidate_for_video(
    request: Request, slug: str, stage_number: int, video_id: str, req: BeepSelectRequest
) -> JSONResponse:
    """Promote one of ``video``'s ranked candidates as authoritative."""
    state = request.app.state.splitsmith_state
    project, stage, video = _server._resolve_stage_video(state, slug, stage_number, video_id)
    _select_candidate_on_video(video, req.time)
    audio_helpers.invalidate_video_audit_trim(state.shooter_root(slug), stage_number, video, project=project)
    project.save(state.shooter_root(slug))
    await _server._maybe_chain_trim(state, slug, stage, video)
    return JSONResponse(project.model_dump(mode="json"))


@router.post("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/beep/review")
async def set_beep_reviewed(
    request: Request, slug: str, stage_number: int, video_id: str, req: BeepReviewRequest
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
    state = request.app.state.splitsmith_state
    project, stage, video = _server._resolve_stage_video(state, slug, stage_number, video_id)
    if req.reviewed and video.beep_time is None:
        raise HTTPException(
            status_code=400,
            detail="cannot mark a beep reviewed before one has been detected",
        )
    video.beep_reviewed = bool(req.reviewed)
    project.save(state.shooter_root(slug))

    # The exporter does not depend on the stub audit doc existing;
    # projects predating it have no stub and still export.
    if req.reviewed:
        await _server._after_beep_reviewed(state, slug, stage_number, video)

    return JSONResponse(project.model_dump(mode="json"))


@router.patch("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/camera-mount")
def set_camera_mount(
    request: Request, slug: str, stage_number: int, video_id: str, req: CameraMountRequest
) -> JSONResponse:
    """Override the heuristic ``camera_mount`` (issue #143).

    Validated against the fixture-schema ``CameraMount`` enum so
    downstream code can trust the stored value. ``None`` clears the
    override -- the next shot-detect run will use the artifact's
    default class instead of a per-class threshold.
    """
    state = request.app.state.splitsmith_state
    project, _stage, video = _server._resolve_stage_video(state, slug, stage_number, video_id)

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


@router.patch("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/camera-model")
def set_camera_model(
    request: Request, slug: str, stage_number: int, video_id: str, req: CameraModelRequest
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
    state = request.app.state.splitsmith_state
    if (req.make is None) != (req.model is None):
        raise HTTPException(
            status_code=400,
            detail="camera-model: 'make' and 'model' must be supplied together or both null",
        )
    project, _stage, video = _server._resolve_stage_video(state, slug, stage_number, video_id)
    video.camera_make = req.make
    video.camera_model = req.model
    project.save(state.shooter_root(slug))
    return JSONResponse(project.model_dump(mode="json"))


@router.post("/api/shooters/{slug}/stages/camera/bulk-set")
def bulk_set_camera(request: Request, slug: str, req: BulkCameraSetRequest) -> JSONResponse:
    """Apply camera mount and/or model overrides to a batch of videos in one save.

    Designed for the CameraCard per-group edit UX: every video belonging to
    one physical camera has the same new value applied atomically. The
    ``set_mount`` / ``set_model`` flags let the caller update only one
    dimension (e.g. correct the model without touching the mount), and
    passing ``null`` with the flag set clears the override back to the
    heuristic / ffprobe default.

    Unknown ``(stage_number, video_id)`` pairs are skipped with a warning
    and do not abort the batch. The full updated project is returned so the
    SPA can replace its local copy without a follow-up GET.
    """
    state = request.app.state.splitsmith_state
    if not req.set_mount and not req.set_model:
        raise HTTPException(
            status_code=400,
            detail="bulk-set: at least one of 'set_mount' or 'set_model' must be true",
        )

    if req.set_mount and req.mount is not None:
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

    if req.set_model and (req.make is None) != (req.model is None):
        raise HTTPException(
            status_code=400,
            detail="camera-model: 'make' and 'model' must be supplied together or both null",
        )

    project = state.shooter_project(slug)
    skipped: list[dict[str, object]] = []
    for item in req.items:
        try:
            stage = project.stage(item.stage_number)
        except KeyError:
            logger.warning(
                "bulk_set_camera: stage %d not found for slug %r; skipping",
                item.stage_number,
                slug,
            )
            skipped.append({"stage_number": item.stage_number, "video_id": item.video_id})
            continue
        video = stage.find_video_by_id(item.video_id)
        if video is None:
            logger.warning(
                "bulk_set_camera: video %r not found on stage %d for slug %r; skipping",
                item.video_id,
                item.stage_number,
                slug,
            )
            skipped.append({"stage_number": item.stage_number, "video_id": item.video_id})
            continue
        if req.set_mount:
            video.camera_mount = req.mount
        if req.set_model:
            video.camera_make = req.make
            video.camera_model = req.model

    project.save(state.shooter_root(slug))
    return JSONResponse(project.model_dump(mode="json"))


@router.post("/api/shooters/{slug}/stages/{stage_number}/beep/select")
async def select_beep_candidate(
    request: Request, slug: str, stage_number: int, req: BeepSelectRequest
) -> JSONResponse:
    """Backward-compat shim: promote a ranked candidate on the primary.

    Lets the user fix a wrong auto-pick without typing a timestamp.
    Re-uses the auto-detect provenance because the time still came
    from the detector -- we only changed which candidate the project
    trusts. Triggers a re-trim so the cached audit clip lines up.
    """
    state = request.app.state.splitsmith_state
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
    await _server._maybe_chain_trim(state, slug, stage, primary)
    return JSONResponse(project.model_dump(mode="json"))


@router.get("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/beep-preview")
def video_beep_preview(
    request: Request, slug: str, stage_number: int, video_id: str, t: float | None = None
) -> FileResponse:
    """Serve a ~1 s MP4 around ``video``'s beep (or override ``t``)."""
    state = request.app.state.splitsmith_state
    project, _stage, video = _server._resolve_stage_video(state, slug, stage_number, video_id)
    return _server._serve_beep_preview(state, slug, project, stage_number, video, t)


@router.get("/api/shooters/{slug}/stages/{stage_number}/beep-preview")
def stage_beep_preview(
    request: Request, slug: str, stage_number: int, t: float | None = None
) -> FileResponse:
    """Serve a tiny MP4 around the primary's beep timestamp (#27, #22).

    Default center is the primary's persisted ``beep_time``. The
    optional ``t`` query param overrides it so the BeepCandidates
    picker (#22) can preview alternative ranked candidates without
    promoting them first. Cache keys on (source mtime/size, center
    time, duration), so each distinct ``t`` gets its own cached clip.
    """
    state = request.app.state.splitsmith_state
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
    return _server._serve_beep_preview(state, slug, project, stage_number, primary, t)


@router.get("/api/shooters/{slug}/stages/{stage_number}/audio", response_model=None)
def stage_audio(request: Request, slug: str, stage_number: int) -> FileResponse | JSONResponse:
    """Serve the audit-clip WAV for ``stage_number``.

    Prefers the primary's audit WAV (extracted from the short-GOP trimmed
    MP4 produced by Sub 5 / #16) so the waveform timeline matches what
    the user is auditing. Falls back to the primary's full source WAV
    when no trimmed clip exists yet -- the SPA surfaces this with a
    "trim required" hint.
    """
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    result = _server._resolve_audit_audio(state, slug, project, stage_number)
    if isinstance(result, JSONResponse):
        return result
    return FileResponse(
        result.audio_path,
        media_type="audio/wav",
        filename=result.audio_path.name,
    )


@router.get("/api/shooters/{slug}/stages/{stage_number}/peaks")
def stage_peaks(
    request: Request,
    slug: str,
    stage_number: int,
    # 1 ms bins on a clip up to ~131 s: Audit draws and snaps to a
    # shot's leading edge from these.
    bins: int = Query(default=1200, ge=16, le=131_072),
) -> JSONResponse:
    """Return ``bins`` peak magnitudes (0..1) for the stage's audit clip.

    The peaks come from whichever WAV ``_resolve_audit_audio`` picked
    (trimmed clip if available, full source otherwise). The response
    carries ``beep_time`` translated into the served clip's local
    timeline + ``trimmed`` so the SPA can render the beep marker
    correctly and warn when the user is auditing an untrimmed source.
    """
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    audit = _server._resolve_audit_audio(state, slug, project, stage_number)
    if isinstance(audit, JSONResponse):
        return audit
    peaks = waveform_helpers.ensure_peaks(audit.audio_path, bins)
    payload = peaks.model_dump(mode="json")
    payload["beep_time"] = audit.beep_in_clip
    payload["trimmed"] = audit.trimmed
    return JSONResponse(payload)


@router.get("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/audio", response_model=None)
def video_audio(request: Request, slug: str, stage_number: int, video_id: str) -> FileResponse | JSONResponse:
    """Serve ``video``'s WAV for the per-video beep picker.

    Primary forwards to the legacy stage audio resolver so the
    trimmed audit clip is preferred. Secondary returns the full
    per-cam WAV (``stage<N>_cam_<vid>.wav``) -- no per-secondary
    trim clip exists yet, and the picker needs the entire clip so
    the user can find the buzzer / first-shot regardless of where
    the current beep estimate is.
    """
    state = request.app.state.splitsmith_state
    project, _stage, video = _server._resolve_stage_video(state, slug, stage_number, video_id)
    result = _resolve_video_audio(state, slug, project, stage_number, video)
    if isinstance(result, JSONResponse):
        return result
    return FileResponse(
        result.audio_path,
        media_type="audio/wav",
        filename=result.audio_path.name,
    )


@router.get("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/peaks")
def video_peaks(
    request: Request,
    slug: str,
    stage_number: int,
    video_id: str,
    bins: int = Query(default=1200, ge=16, le=8192),
    # One window of the WAV, source seconds: the beep picker's 1 ms detail
    # around the beep. Both or neither.
    start: float | None = Query(default=None, ge=0),
    end: float | None = Query(default=None, gt=0),
    # A frequency band, ``LO-HI`` Hz, for the beep step's Timer tone
    # filter (spec 2026-10-17); only with a window.
    band: str | None = Query(default=None),
) -> JSONResponse:
    """Return ``bins`` peak magnitudes for ``video``'s WAV.

    Same shape as ``/api/stages/{n}/peaks`` so the SPA's waveform
    picker can take the same path for primary + secondary -- the
    only thing that varies between roles is the URL.
    """
    state = request.app.state.splitsmith_state
    project, _stage, video = _server._resolve_stage_video(state, slug, stage_number, video_id)
    if (start is None) != (end is None) or (start is not None and end is not None and end <= start):
        raise HTTPException(status_code=422, detail="start and end go together, start before end")
    band_range = _parse_band(band)
    if band_range is not None and start is None:
        raise HTTPException(status_code=422, detail="a band needs a window (start and end)")
    audit = _resolve_video_audio(state, slug, project, stage_number, video)
    if isinstance(audit, JSONResponse):
        return audit
    peaks = (
        waveform_helpers.ensure_peaks(audit.audio_path, bins)
        if start is None or end is None
        else waveform_helpers.ensure_peaks_window(
            audit.audio_path, bins, start=start, end=end, band=band_range
        )
    )
    payload = peaks.model_dump(mode="json")
    payload["beep_time"] = audit.beep_in_clip
    payload["trimmed"] = audit.trimmed
    return JSONResponse(payload)


@router.get("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/spectrum")
def video_spectrum(
    request: Request,
    slug: str,
    stage_number: int,
    video_id: str,
    start: float = Query(ge=0),
    end: float = Query(gt=0),
) -> JSONResponse:
    """The beep step's spectrogram row (spec 2026-10-17): one window of
    the video's own WAV, 0 to 8 kHz, ~5 ms columns, the window capped at
    ``BeepToneConfig.max_window_s``, plus ``tone_band``: the timer's own
    band around the video's beep, which the Timer tone filter asks for.
    Reads the published WAV; when it is missing, queues ``derive_audio``
    and answers 202 like the peaks."""
    state = request.app.state.splitsmith_state
    from ..beep import detect as beep_detect
    from ..beep import tone as beep_tone

    if end <= start:
        raise HTTPException(status_code=422, detail="start before end")
    project, _stage, video = _server._resolve_stage_video(state, slug, stage_number, video_id)
    audit = _resolve_video_audio(state, slug, project, stage_number, video)
    if isinstance(audit, JSONResponse):
        return audit
    audio, sr = beep_detect.load_audio(audit.audio_path)
    payload = beep_tone.spectrogram(audio, sr, start, end).model_dump(mode="json")
    around = float(video.beep_time) if video.beep_time is not None else (start + end) / 2
    payload["tone_band"] = list(beep_tone.tone_band(audio, sr, around))
    return JSONResponse(payload)


@router.get(
    "/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/beep-snippet/audio",
    response_model=None,
)
def beep_snippet_audio(
    request: Request, slug: str, stage_number: int, video_id: str
) -> FileResponse | RedirectResponse:
    """Serve the pushed beep review audio snippet for a mirror video."""
    state = request.app.state.splitsmith_state
    _server._resolve_stage_video(state, slug, stage_number, video_id)  # 404 on unknown video
    key = _beep_snippet_key(state, slug, video_id, ".m4a")
    if key is None or not state.storage.exists(key):
        raise HTTPException(status_code=404, detail="beep_snippet_not_available")
    local = state.shooter_root(slug) / "beep_review" / f"{video_id}.m4a"
    return _server.serve_media(state.storage, key, local, content_type="audio/mp4")


@router.get("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/beep-snippet/peaks")
def beep_snippet_peaks(request: Request, slug: str, stage_number: int, video_id: str) -> JSONResponse:
    """Return the pushed peaks JSON for a mirror video's beep snippet."""
    state = request.app.state.splitsmith_state
    _server._resolve_stage_video(state, slug, stage_number, video_id)  # 404 on unknown video
    key = _beep_snippet_key(state, slug, video_id, ".peaks.json")
    if key is None or not state.storage.exists(key):
        raise HTTPException(status_code=404, detail="beep_snippet_not_available")
    # Read through to storage every request - desktop rewrites this object
    # under the same key when it regenerates a snippet (beep_time/candidates
    # change), so mirror-once-then-serve-local would go stale. The JSON is
    # 100 bins/s over the search window (tens of KB, a few hundred for a
    # long multi-stage window), cheap enough to fetch fresh each time.
    return JSONResponse(json.loads(state.storage.read_bytes(key)))


@router.get("/api/shooters/{slug}/stages/{stage_number}/audit")
def get_stage_audit(request: Request, slug: str, stage_number: int) -> JSONResponse:
    """Return the stage's audit JSON (issue #15) if one has been written.

    Lives at ``<project>/audit/stage<N>.json`` -- the same path the
    existing audit-prep / audit-apply flow uses. Returns ``200 null``
    when no audit file exists yet (the audit screen treats this as
    "fresh -- start from candidates if any, otherwise empty
    markers"); 404 is reserved for genuinely-unknown stages so the
    SPA can distinguish.
    """
    state = request.app.state.splitsmith_state
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
    # On the clip's clock (#1497): Audit draws shot times on the clip it
    # plays, so an audit made on the source's clock, or saved with a source
    # beep beside clip times, is served moved onto it. The revision is the
    # stored doc's, so the next PUT, which stamps the clip beep, compares
    # against what is stored and persists the move.
    _stg, _project, clip_beep = _server._coach_stage(state, slug, stage_number)
    served, _kind = rebase_to_clip(payload, clip_beep)
    # The revision rides the response only (spec 2026-09-27 s5); the
    # SPA's buildAuditJson spreads the loaded doc, so it comes back on
    # the next PUT, which pops it before anything is stored.
    return JSONResponse({**(served or payload), REVISION_FIELD: audit_revision(payload)})


@router.put("/api/shooters/{slug}/stages/{stage_number}/audit")
def put_stage_audit(request: Request, slug: str, stage_number: int, payload: dict[str, Any]) -> JSONResponse:
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
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    try:
        project.stage(stage_number)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    client_revision = payload.pop(REVISION_FIELD, None)
    # #843: before anything touches disk. A non-finite float survives
    # json.loads, persists, and then makes this stage unreadable.
    _server._reject_non_finite(payload, what="audit document")
    # #1497: the shots Audit saves are on the clip's clock, so the beep it
    # stores is the clip's, whatever the page fell back to while the clip's
    # audio had not loaded (a source beep beside clip times made every
    # split read as before the beep).
    _stg, _project, clip_beep = _server._coach_stage(state, slug, stage_number)
    stamp_clip_beep(payload, clip_beep)
    # #775: an audited stage is fully classified. Run the auto-classifier
    # on every save so statistic_splits never sees a partial stage; shots
    # whose source is "manual" are preserved, shots with no ms_after_beep
    # are left unclassified (they never reach statistics).
    shots = payload.get("shots")
    if isinstance(shots, list):
        shot_dicts = [s for s in shots if isinstance(s, dict)]
        # Identity before anything else: the sync merge keys shot
        # membership on this, and shot_number cannot serve because it
        # renumbers on every insert. On a mirror the desktop is the sole
        # minter (see _may_mint_shot_ids) - a client-supplied id is still
        # kept, so a shot the SPA added arrives with its own identity.
        ensure_shot_ids(shot_dicts, mint=_server._may_mint_shot_ids())
        _server._classify_doc(payload, stage_number, coach_module.auto_classify_config())
    # Sync merge unions audit_events by id (bidirectional sync
    # slice); the SPA authors events without one, so stamp them here
    # at the save boundary.
    events = payload.get("audit_events")
    if isinstance(events, list):
        for event in events:
            if isinstance(event, dict) and not event.get("id"):
                event["id"] = _new_event_id()
    # Compare-and-save as one read-modify-write (spec 2026-09-27 s5): a PUT
    # carrying ``_version`` from a copy that no longer matches the stored
    # doc (a sync pull, another tab) is refused with 409 instead of
    # overwriting it. Locally the port holds the audit lock the pull's
    # apply holds; hosted it is optimistic on the state_docs version. A
    # PUT without ``_version`` keeps last-writer-wins.
    is_save = isinstance(events, list) and any(
        isinstance(e, dict) and e.get("kind") == "save" for e in events
    )
    incoming_flagged = bool((payload.get("needs_attention") or {}).get("flagged"))

    def merge(stored: dict[str, Any] | None, version: int) -> dict[str, Any]:
        if client_revision is not None and client_revision != audit_revision(stored):
            raise AuditRevisionConflictError(f"stage {stage_number} audit changed since it was loaded")
        # #823: a desktop full-audit save resolves an open triage flag, same
        # as the triage "accept" action. The SPA round-trips needs_attention
        # (Audit.tsx buildAuditJson spreads the loaded doc), so checking the
        # incoming payload covers the normal path - but a stale SPA session
        # that dropped the key would silently keep the stored doc flagged,
        # so check both sides.
        stored_flagged = bool(((stored or {}).get("needs_attention") or {}).get("flagged"))
        if is_save and (incoming_flagged or stored_flagged):
            _set_needs_attention(payload, flagged=False)
        return payload

    saved, _version = state.update_audit_at(slug, stage_number, merge)
    return JSONResponse({**saved, REVISION_FIELD: audit_revision(saved)})


@router.post("/api/shooters/{slug}/stages/{stage_number}/audit/accept")
def accept_stage_audit(request: Request, slug: str, stage_number: int) -> JSONResponse:
    """Mark a stage audited from the triage surface (slice 4).

    Appends an explicit ``accept`` audit event instead of the desktop
    ``save`` so provenance stays distinguishable; ``stage_audit_status``
    treats both as audited. Runs the auto-classifier first (#775) and
    refuses when the stage has nothing to accept or a kept shot cannot
    be classified (#778 invariant, enforced not just healed). Returns
    the fresh triage list (task 4) so the SPA never has to re-fetch
    after a write.
    """
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    try:
        project.stage(stage_number)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    def merge(stored: dict[str, Any] | None, version: int) -> dict[str, Any]:
        if stored is None:
            raise HTTPException(status_code=409, detail="nothing_to_accept")
        shots = [s for s in stored.get("shots") or [] if isinstance(s, dict)]
        kept = _kept_audit_shots(shots)
        if not kept:
            raise HTTPException(status_code=409, detail="nothing_to_accept")
        # Mirror-exempt from the read-only gate (slice 4), so this is the
        # save boundary a phone actually reaches on a mirror - and the one
        # that must not mint a second id for a legacy shot the desktop
        # will stamp itself. See _may_mint_shot_ids.
        ensure_shot_ids(shots, mint=_server._may_mint_shot_ids())
        _server._classify_doc(stored, stage_number, coach_module.auto_classify_config())
        if any(not s.get("interval_class") for s in kept):
            raise HTTPException(status_code=409, detail="not_fully_classified")
        stored.setdefault("audit_events", []).append(
            {
                "id": _new_event_id(),
                "ts": _now_iso(),
                "kind": "accept",
                "payload": {"source": "triage"},
            }
        )
        _set_needs_attention(stored, flagged=False)
        return stored

    # A lost hosted race re-merges onto the winner inside the port; one
    # that outlasts its attempts is the global 409 ``version_conflict``.
    state.update_audit_at(slug, stage_number, merge)
    return JSONResponse(_server._build_triage_response(state).model_dump())


@router.post("/api/shooters/{slug}/stages/{stage_number}/attention")
def set_stage_attention(
    request: Request, slug: str, stage_number: int, body: StageAttentionBody
) -> JSONResponse:
    """Flag or clear a stage for desktop follow-up (slice 4).

    The flag lives in the schemaless audit doc so it syncs with the
    other triage writes; flagging a stage that has no audit doc yet
    creates one holding only the flag. Returns the fresh triage list
    (task 4), same fresh-list contract as accept.
    """
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    try:
        project.stage(stage_number)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    def merge(stored: dict[str, Any] | None, version: int) -> dict[str, Any]:
        if stored is None:
            # Seed the same beep-confirm stub shape as the beep-review
            # endpoint (``_after_beep_reviewed``): is_stub_audit ignores the
            # needs_attention key, so a flagged doc-less stage still
            # reads as "ready" (derived from real shots/events) instead
            # of falling to "in_progress" and getting stuck flagged
            # forever once cleared.
            stored = {"shots": [], "detection": STUB_AUDIT_DETECTION}
        _set_needs_attention(stored, flagged=body.flagged, note=body.note)
        return stored

    state.update_audit_at(slug, stage_number, merge)
    return JSONResponse(_server._build_triage_response(state).model_dump())


@router.get("/api/shooters/{slug}/stages/{stage_number}/anomalies")
def get_stage_anomalies(request: Request, slug: str, stage_number: int) -> JSONResponse:
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
    state = request.app.state.splitsmith_state
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
    shots = audit_shots_to_engine_shots(audit_payload, beep_time_in_source=beep_time)
    anomalies = report.detect_anomalies_structured(shots, beep_time, stg.time_seconds)
    return JSONResponse({"anomalies": [a.model_dump() for a in anomalies]})


@router.get("/api/shooters/{slug}/stages/{stage_number}/coach")
def get_stage_coach(request: Request, slug: str, stage_number: int, seed: bool = False) -> JSONResponse:
    """Return the per-shot coach view for a stage.

    A legacy audit doc with unclassified shots is healed on first read
    so every consumer sees fully-classified data - see ``stale`` for
    whether the current rule disagrees with a stored (possibly manual)
    class. Owner reads persist the heal (#775); share-token reads
    classify in-memory only and never write back. The client can also
    call ``POST /coach/reclassify`` to force-persist the rule's verdict
    onto unset/auto entries. Returns ``200 null`` when the stage has no
    audit JSON yet (a normal pre-audit state); 404 is reserved for
    genuinely-unknown stage numbers.

    ``seed=1`` is Breakdown's: only then are reload proposals seeded
    (spec 2026-10-08), and only an owner read that is not a mirror
    persists them, and only when the seed proposed something. Every
    other reader (Coach, the Splits stage page, Compare) reads without
    writing anything but the heal: a page that fetches every stage must
    not move every stage's revision and queue a sync push per stage.
    """
    state = request.app.state.splitsmith_state
    stg, project, beep_in_clip = _server._coach_stage(state, slug, stage_number)
    cfg = coach_module.auto_classify_config()
    capacity = _server._coach_capacity(state, slug, project)
    share_read = current_share_request.get()
    keep_seed = seed and not share_read and not _server._is_mirror()
    # What the last run of ``merge`` built: the doc the response shows, the
    # stored doc's revision before the heal and seed touched it (a read
    # that persists nothing must hand back the stored ``_version``, or the
    # events PUT would 409 on every attempt), and the version it was read
    # at. Empty when the stage has no audit doc yet.
    seen: dict[str, Any] = {}

    def merge(stored: dict[str, Any] | None, version: int) -> dict[str, Any]:
        seen.clear()
        if stored is None:
            raise _server._AuditUnchanged
        seen.update(view=stored, revision=audit_revision(stored), version=version)
        # #775: heal legacy docs on read so consumers (Results, share view,
        # statistic_splits) always see a fully classified stage. Owners get
        # the heal persisted; share-token readers are read-only, so the
        # classes are computed in-memory and never written back. The guard
        # itself lives in ``coach.heal_unclassified`` (#780) - this is the
        # only one of its four callers that persists, and it keys the write
        # off the return value so an untouched doc costs no version bump.
        # It classifies against confirmed regions only, which no seed adds,
        # so the heal is the same with or without ``seed``.
        healed = coach_module.heal_unclassified(
            stored.get("shots"),
            cfg,
            events=events_module.confirmed(_server._coach_events(stored, stage_number)),
        )
        persist = healed and not share_read
        payload = stored
        if seed:
            # The seed runs on a copy: a failed seed leaves nothing behind,
            # and one nobody persists (a mirror, a share read) never rides
            # a heal saved below.
            target = copy.deepcopy(stored)
            # Stage events (spec 2026-10-08): reload and movement proposals, seeded once
            # per stage. An earlier seeder's untouched proposals are
            # replaced, and the shots classified again (confirmed regions
            # only) so nothing an old proposal may have set stays.
            try:
                reseeding = events_module.reseedable(target)
                seeded = events_module.seed_doc(
                    target,
                    hint_min_s=cfg.reload_hint_min_s,
                    capacity=capacity,
                    min_reload_s=cfg.reload_min_s,
                    # A late first shot: the shooter moved before shooting.
                    draw=True,
                    draw_movement_min_s=cfg.draw_movement_min_s,
                )
                if seeded and reseeding:
                    _server._classify_doc(target, stage_number, cfg)
            except Exception:  # noqa: BLE001 -- a seed must never fail the read
                logger.exception("stage %s/%s: seeding region proposals failed", slug, stage_number)
                seeded = False
            if seeded:
                seen["view"] = target
                if keep_seed:
                    payload = target
                    persist = True
        if not persist:
            raise _server._AuditUnchanged
        return payload

    try:
        persisted, version = state.update_audit_at(slug, stage_number, merge)
        stored_revision = audit_revision(persisted)
    except _server._AuditUnchanged:
        if not seen:
            return JSONResponse(None)
        version, stored_revision = seen["version"], seen["revision"]
    except _server._state_conflict_excs():
        # Every re-merge lost the hosted version race; serve the in-memory
        # heal and let the next read persist it. ``_state_conflict_excs``
        # resolves the hosted-only conflict type lazily and guarded - a slim
        # local install has no db extras, and an unguarded import here
        # 500ed every heal-triggering coach GET.
        version, stored_revision = seen["version"], seen["revision"]
    return JSONResponse(
        _server._build_coach_response(
            state,
            slug,
            seen["view"],
            beep_in_clip,
            stg,
            project,
            cfg,
            version,
            capacity=capacity,
            stored_revision=stored_revision,
        )
    )


@router.put("/api/shooters/{slug}/stages/{stage_number}/events")
def put_stage_events(
    request: Request, slug: str, stage_number: int, req: StageEventsPutRequest
) -> JSONResponse:
    """Replace the stage's event list (spec 2026-10-08) and re-classify
    its shots against it. A lane overlap is a 422 ``lane_overlap``; a
    ``_version`` that no longer matches the stored doc is the audit
    PUT's 409 ``version_conflict``. Returns the coach payload."""
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    try:
        stg = project.stage(stage_number)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # #843: a NaN ``end`` passes every field bound (``NaN <= start`` is
    # false), persists, and then makes every coach GET of the stage 500.
    _server._reject_non_finite([e.model_dump() for e in req.events], what="stage events")
    try:
        events_module.validate_lanes(req.events)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "lane_overlap", "message": str(exc)}) from exc
    cfg = coach_module.auto_classify_config()

    def merge(doc: dict[str, Any] | None, version: int) -> dict[str, Any]:
        stored = _server._require_audit_doc(doc, stage_number)
        if req.revision is not None and req.revision != audit_revision(stored):
            raise AuditRevisionConflictError(f"stage {stage_number} audit changed since it was loaded")
        stored[events_module.EVENTS_FIELD] = [e.model_dump(exclude_none=True) for e in req.events]
        stored[events_module.EVENTS_SEEDED_FIELD] = events_module.SEED_VERSION
        _server._classify_doc(stored, stage_number, cfg)
        stored.setdefault("audit_events", []).append(
            {
                "id": _new_event_id(),
                "ts": _now_iso(),
                "kind": "events_save",
                "payload": {"count": len(req.events)},
            }
        )
        return stored

    # #1330: the response is built from the saved doc and the version the
    # save returned, never from a reload.
    stored, version = state.update_audit_at(slug, stage_number, merge)
    prim = stg.primary()
    beep_in_clip = (
        _server._video_beep_in_clip(state, slug, project, stage_number, prim) if prim is not None else None
    )
    return JSONResponse(
        _server._build_coach_response(
            state,
            slug,
            stored,
            beep_in_clip,
            stg,
            project,
            cfg,
            version,
            capacity=_server._coach_capacity(state, slug, project),
        )
    )


@router.patch("/api/shooters/{slug}/stages/{stage_number}/stage-note")
def patch_stage_note(
    request: Request, slug: str, stage_number: int, req: StageNotePatchRequest
) -> JSONResponse:
    """Write the stage's note (#1376), edited on the Coach review page.
    Empty or null removes it. A ``_version`` that no longer matches the
    stored doc is the audit PUT's 409 ``version_conflict``. A REVIEW
    route, like the shot coach PATCH: ``sync.merge.merge_audit_doc``
    merges the field three-way, so a mirror's note reaches the desktop.
    Returns the coach payload."""
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    try:
        stg = project.stage(stage_number)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    cfg = coach_module.auto_classify_config()
    # A clear writes ``null``, never drops the key: sync reads an absent
    # key as "this writer never knew the field" (an older install), and
    # only the tombstone deletes on the other side.
    text = req.stage_note or None

    def merge(doc: dict[str, Any] | None, version: int) -> dict[str, Any]:
        stored = _server._require_audit_doc(doc, stage_number)
        if req.revision is not None and req.revision != audit_revision(stored):
            raise AuditRevisionConflictError(f"stage {stage_number} audit changed since it was loaded")
        # The response reads the events; a corrupt list is the GET's 422,
        # raised here before the save rather than after it.
        _server._coach_events(stored, stage_number)
        stored[coach_module.FIELD_STAGE_NOTE] = text
        stored.setdefault("audit_events", []).append(
            {
                "id": _new_event_id(),
                "ts": _now_iso(),
                "kind": "stage_note",
                "payload": {"stage_note": _server._note_digest(text)},
            }
        )
        return stored

    stored, version = state.update_audit_at(slug, stage_number, merge)
    prim = stg.primary()
    beep_in_clip = (
        _server._video_beep_in_clip(state, slug, project, stage_number, prim) if prim is not None else None
    )
    return JSONResponse(
        _server._build_coach_response(
            state,
            slug,
            stored,
            beep_in_clip,
            stg,
            project,
            cfg,
            version,
            capacity=_server._coach_capacity(state, slug, project),
        )
    )


@router.post("/api/shooters/{slug}/stages/{stage_number}/coach/reclassify")
def reclassify_stage_coach(request: Request, slug: str, stage_number: int) -> JSONResponse:
    """Force the auto-classifier to (re)write ``interval_class`` for
    every shot whose source is unset or ``"auto"``. Manual entries are
    preserved. Idempotent.
    """
    state = request.app.state.splitsmith_state
    stg, project, beep_in_clip = _server._coach_stage(state, slug, stage_number)
    cfg = coach_module.auto_classify_config()

    def merge(stored: dict[str, Any] | None, version: int) -> dict[str, Any]:
        payload = _server._require_audit_doc(stored, stage_number)
        shots = payload.get("shots") or []
        if not isinstance(shots, list):
            raise HTTPException(status_code=500, detail="audit shots is not a list")
        _server._classify_doc(payload, stage_number, cfg)
        events = list(payload.get("audit_events") or [])
        events.append(
            {
                "id": _new_event_id(),
                "ts": _now_iso(),
                "kind": "coach_reclassify",
                "payload": {"shot_count": len(shots)},
            }
        )
        payload["audit_events"] = events
        return payload

    payload, version = state.update_audit_at(slug, stage_number, merge)
    return JSONResponse(
        _server._build_coach_response(
            state,
            slug,
            payload,
            beep_in_clip,
            stg,
            project,
            cfg,
            version,
            capacity=_server._coach_capacity(state, slug, project),
        )
    )


@router.patch("/api/shooters/{slug}/stages/{stage_number}/shots/{shot_number}/coach")
def patch_stage_shot_coach(
    request: Request,
    slug: str,
    stage_number: int,
    shot_number: int,
    body: CoachShotPatchRequest,
) -> JSONResponse:
    """Patch one shot's coaching annotation, addressed by position.

    Retained for compatibility. Prefer the by-id route: ``shot_number``
    renumbers whenever a shot is inserted or deleted, so a client holding
    a stale number can annotate the wrong shot unless it also sends
    ``expected_version``.
    """
    state = request.app.state.splitsmith_state
    return _apply_shot_coach_patch(
        state,
        slug,
        stage_number,
        body,
        lambda s: int(s.get("shot_number", -1)) == shot_number,
        f"shot {shot_number}",
    )


@router.patch("/api/shooters/{slug}/stages/{stage_number}/shots/by-id/{shot_id}/coach")
def patch_stage_shot_coach_by_id(
    request: Request,
    slug: str,
    stage_number: int,
    shot_id: str,
    body: CoachShotPatchRequest,
) -> JSONResponse:
    """Patch one shot's coaching annotation, addressed by stable id.

    Immune to renumbering, so this is the route any client that did not
    just write the document should use.
    """
    state = request.app.state.splitsmith_state
    return _apply_shot_coach_patch(
        state,
        slug,
        stage_number,
        body,
        lambda s: s.get("id") == shot_id,
        f"shot id {shot_id!r}",
    )


@router.get("/api/shooters/{slug}/stages/{stage_number}/coach/distributions")
def get_stage_coach_distributions(request: Request, slug: str, stage_number: int) -> JSONResponse:
    """Histograms + summary stats for one stage's coach annotations.

    Unset interval classes are computed in memory; nothing persists.
    Empty classes still appear in the response with count=0 so the
    UI can render an empty histogram without a special case.
    """
    state = request.app.state.splitsmith_state
    payload, _version, _beep_in_clip, stg, _project = _server._load_audit_for_coach(state, slug, stage_number)
    cfg = coach_module.auto_classify_config()
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


@router.post("/api/shooters/{slug}/stages/{stage_number}/skip")
def set_stage_skipped(request: Request, slug: str, stage_number: int, req: SkipStageRequest) -> JSONResponse:
    """Mark ``stage_number`` as skipped (or un-skip it).

    A skipped stage is excluded from "next step" gating in the ingest
    screen so the user can advance even when the stage has no videos
    (e.g. they didn't film stage 4).
    """
    state = request.app.state.splitsmith_state
    root = state.shooter_root(slug)
    project = state.shooter_project(slug)
    try:
        stage = project.stage(stage_number)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    stage.skipped = req.skipped
    project.save(root)
    return JSONResponse(project.model_dump(mode="json"))
