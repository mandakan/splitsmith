"""Mutating MCP tools (issue #211 layer 3b).

Each tool follows the same load -> mutate -> save pattern: open the
project, apply a focused change, persist back to ``project.json``.
The mutations mirror the corresponding HTTP endpoints in
:mod:`splitsmith.ui.server` so an agent driving the MCP and a user
driving the SPA produce identical project state.

What's deliberately NOT here:

* Background-job triggers (auto-trim, auto-shot-detect, auto-beep
  on assignment). Those are the HTTP server's job runner; the MCP
  surface is project-state only. A user with the splitsmith UI
  daemon running concurrently still gets the chained jobs because
  the server watches ``project.json`` mtime; an agent running
  without the daemon can call the future ``trim`` / ``detect_*``
  tools (layer 3c) explicitly.
* Audio cache invalidation is included for the beep tools because
  it's a synchronous filesystem operation that the agent expects
  to "just happen" -- otherwise a stale audit clip survives the
  beep change and silently mis-aligns the next viewing session.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..ui import audio as audio_helpers
from ..ui.project import MatchProject, StageVideo
from .sandbox import resolve_project_root

# Roles the assign tool accepts. Mirror of
# :data:`splitsmith.ui.project.VideoRole` -- spelled out here so the
# MCP error message tells the agent what valid values look like.
ALLOWED_ROLES = ("primary", "secondary", "ignored")


def assign_video(
    project_root: str,
    video_path: str,
    *,
    stage_number: int | None,
    role: str = "secondary",
) -> dict[str, Any]:
    """Assign a registered video to a stage (or back to unassigned).

    ``stage_number=None`` moves the video to the project's
    ``unassigned_videos`` tray regardless of ``role``. With a stage
    number, ``role`` is one of ``primary | secondary | ignored``;
    ``primary`` demotes any existing primary to ``secondary``, and
    ``secondary`` auto-upgrades to primary on a stage that has none
    (matches the SPA's drag-and-drop semantics for first-video
    placement).

    Returns ``{video_id, role, stage_number}`` so the agent can
    reference the placed video in subsequent tool calls without a
    second ``get_project`` round-trip.
    """
    if role not in ALLOWED_ROLES:
        raise ValueError(f"role must be one of {', '.join(ALLOWED_ROLES)}; got {role!r}")
    root = resolve_project_root(project_root)
    # ``video_path`` is a project-internal identifier (the project
    # stores paths as strings relative to the project root, and
    # ``find_video`` does string equality). Sandboxing + absolute
    # resolution here breaks the lookup. The path was registered
    # via a separate tool (the future scan/register tool) where
    # sandbox enforcement applies; here we just move the label.
    project = MatchProject.load(root)
    placed = project.assign_video(
        Path(video_path),
        to_stage_number=stage_number,
        role=role,  # type: ignore[arg-type]
    )
    project.save(root)
    return {
        "video_id": placed.video_id,
        "role": placed.role,
        "stage_number": stage_number,
    }


def set_beep_manual(
    project_root: str,
    *,
    stage_number: int,
    video_id: str,
    time_seconds: float | None,
) -> dict[str, Any]:
    """Manually pin (or clear) ``video``'s beep timestamp.

    ``time_seconds=None`` clears any existing beep back to "no beep
    yet" (resets review, processed flags, candidate list). Otherwise
    the value is stored with ``beep_source="manual"`` and confidence
    pinned at 1.0 (the user told us where the beep is, so the
    auto-trust gate (#219) opens immediately). The cached audit trim
    is invalidated either way so the next viewing rebuilds with the
    new offset.

    Returns ``{beep_time, beep_source, beep_confidence,
    beep_reviewed, beep_auto_detect_failed}`` for the updated video.
    """
    if time_seconds is not None and time_seconds < 0:
        raise ValueError(f"time_seconds must be >= 0 or None; got {time_seconds}")
    root = resolve_project_root(project_root)
    project = MatchProject.load(root)
    stage, video = _resolve_stage_video(project, stage_number=stage_number, video_id=video_id)
    if time_seconds is None:
        _apply_beep_clear(video)
    else:
        _apply_beep_manual(video, float(time_seconds))
    audio_helpers.invalidate_video_audit_trim(root, stage_number, video, project=project)
    project.save(root)
    return _beep_state_summary(video)


def select_beep_candidate(
    project_root: str,
    *,
    stage_number: int,
    video_id: str,
    time_seconds: float,
) -> dict[str, Any]:
    """Promote one of ``video.beep_candidates`` (matched within 1 ms of
    ``time_seconds``) as the authoritative beep.

    Mirror of ``POST /api/stages/{n}/videos/{vid}/beep/select`` --
    keeps ``beep_source="auto"`` because the time still came from the
    detector, but copies the chosen candidate's diagnostic fields
    (peak amplitude, duration, confidence) onto the video so the
    HITL queue + UI render the right values. Resets ``beep_reviewed``
    to False so a subsequent ``mark_beep_reviewed`` call confirms
    the *new* pick rather than carrying over approval of the
    previous one. Audit trim cache is invalidated.

    Raises ``ValueError`` if the candidate list is empty or no
    candidate is within 1 ms of ``time_seconds``.
    """
    root = resolve_project_root(project_root)
    project = MatchProject.load(root)
    stage, video = _resolve_stage_video(project, stage_number=stage_number, video_id=video_id)
    if not video.beep_candidates:
        raise ValueError(f"video {video_id} has no candidate list yet; run detect_beep first")
    match_eps = 1e-3
    chosen = next(
        (c for c in video.beep_candidates if abs(c.time - time_seconds) <= match_eps),
        None,
    )
    if chosen is None:
        available = ", ".join(f"{c.time:.3f}" for c in video.beep_candidates)
        raise ValueError(
            f"no candidate within {match_eps * 1000:.0f} ms of "
            f"{time_seconds:.3f}s; available: [{available}]"
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
    # Switching candidate is a fresh claim; the user (or another
    # agent) should re-confirm before the auto-trust gate opens.
    video.beep_reviewed = False
    if video.role == "primary":
        video.processed["shot_detect"] = False
    audio_helpers.invalidate_video_audit_trim(root, stage_number, video, project=project)
    project.save(root)
    return _beep_state_summary(video)


def mark_beep_reviewed(
    project_root: str,
    *,
    stage_number: int,
    video_id: str,
    reviewed: bool = True,
) -> dict[str, Any]:
    """Flip ``video.beep_reviewed`` (issue #71).

    Setting True requires ``beep_time`` to be present; setting False
    is always allowed (e.g. agent wants the user to re-pick). This
    is the explicit handoff between detect-stage and shot-detect:
    when the SPA's user (or an agent) confirms a beep, downstream
    detection chains can fire. The MCP write tool only updates the
    flag -- the chain itself runs on the HTTP server's job runner
    or via a future ``detect_shots`` MCP tool (layer 3c).
    """
    root = resolve_project_root(project_root)
    project = MatchProject.load(root)
    _stage, video = _resolve_stage_video(project, stage_number=stage_number, video_id=video_id)
    if reviewed and video.beep_time is None:
        raise ValueError("cannot mark a beep reviewed before one has been detected")
    video.beep_reviewed = bool(reviewed)
    project.save(root)
    return _beep_state_summary(video)


def _resolve_stage_video(
    project: MatchProject, *, stage_number: int, video_id: str
) -> tuple[Any, StageVideo]:
    try:
        stage = project.stage(stage_number)
    except KeyError as exc:
        raise ValueError(f"stage {stage_number} not found") from exc
    video = stage.find_video_by_id(video_id) if hasattr(stage, "find_video_by_id") else None
    if video is None:
        # Older code paths don't have ``find_video_by_id``; scan manually.
        video = next((v for v in stage.videos if v.video_id == video_id), None)
    if video is None:
        raise ValueError(
            f"video {video_id} not on stage {stage_number}; "
            f"available: {[v.video_id for v in stage.videos]}"
        )
    return stage, video


def _apply_beep_manual(video: StageVideo, time_seconds: float) -> None:
    video.beep_time = time_seconds
    video.beep_source = "manual"
    video.beep_peak_amplitude = None
    video.beep_duration_ms = None
    # Manual entry pins confidence at 1.0 so the auto-trust gate
    # (#219) opens immediately.
    video.beep_confidence = 1.0
    video.beep_candidates = []
    video.beep_auto_detect_failed = False
    video.beep_alignment_confidence = None
    video.beep_alignment_delta_ms = None
    video.processed["beep"] = True
    video.processed["trim"] = False
    # Manual entry implies the user / agent looked at the waveform;
    # skip the review pill (#71).
    video.beep_reviewed = True
    if video.role == "primary":
        video.processed["shot_detect"] = False


def _apply_beep_clear(video: StageVideo) -> None:
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


def _beep_state_summary(video: StageVideo) -> dict[str, Any]:
    return {
        "video_id": video.video_id,
        "beep_time": video.beep_time,
        "beep_source": video.beep_source,
        "beep_confidence": video.beep_confidence,
        "beep_reviewed": video.beep_reviewed,
        "beep_auto_detect_failed": video.beep_auto_detect_failed,
        "processed": dict(video.processed),
    }
