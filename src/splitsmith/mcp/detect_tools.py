"""Detection MCP tools (issue #211 layer 3c).

Wraps the heavy detection stages (beep detection now; shot detection
in a later PR) as synchronous MCP tools. The agent flow expects to
call these and get a result back; the underlying functions take a
few seconds (audio extraction + 4th-order Butterworth + Hilbert) so
running them inline is acceptable for the typical MCP client. If
end-to-end latency becomes a problem we'd graduate to a job-runner
abstraction matched to the HTTP server's; the synchronous shape is
the right starting point.

Detection mutates project state:

* Persists the BeepDetection result onto ``StageVideo`` (time, source,
  candidates, peak, duration, confidence, auto-detect-failed).
* Honours the auto-trust gate (#219): when the resolved
  ``automation.beep_low_confidence_threshold`` is met,
  ``beep_reviewed`` flips to True so an external auto-trim chain
  fires; below it the beep stays unreviewed and lands in the HITL
  queue.
* Does NOT run trim or shot detection. Those are the next layers'
  jobs (and the HTTP daemon does them inline today).
* Cross-correlation alignment for secondaries is also deferred.
  ``detect_beep`` on a secondary marks ``beep_auto_detect_failed``
  if the in-stream detector finds nothing; the SPA's existing
  align flow handles those cases.
"""

from __future__ import annotations

from typing import Any

from .. import automation as automation_module
from .. import beep_detect
from ..ui import audio as audio_helpers
from ..ui.project import MatchProject
from .sandbox import resolve_project_root
from .write_tools import _resolve_stage_video


def detect_beep_for_video(
    project_root: str,
    *,
    stage_number: int,
    video_id: str,
    force: bool = False,
) -> dict[str, Any]:
    """Run ``beep_detect.detect_beep`` against ``video``'s cached audio
    and persist the result on the project.

    Skips when the video already has a beep recorded unless ``force``
    is True -- re-detecting a clean beep is wasteful and would clear
    a manual override or a reviewed pick.

    Returns a summary dict with the detected time, confidence, source
    (always ``"auto"`` here), the auto-trust gate decision, and the
    candidate list so the agent can immediately drive
    ``select_beep_candidate`` if it disagrees with the winner.
    """
    root = resolve_project_root(project_root)
    project = MatchProject.load(root)
    _stage, video = _resolve_stage_video(project, stage_number=stage_number, video_id=video_id)
    if video.role == "ignored":
        raise ValueError(
            f"video {video_id} on stage {stage_number} is ignored; "
            "assign it as primary or secondary first"
        )
    if not force and video.beep_time is not None and video.beep_source == "manual":
        # Manual entries are explicit user intent; never overwrite
        # without ``force=True``. Auto-detected beeps are eligible for
        # re-detection because the detector itself may have improved.
        return _summary(video, gate_threshold=None)
    if not force and video.processed.get("beep") and video.beep_source == "auto":
        return _summary(video, gate_threshold=None)

    source = project.resolve_video_path(root, video.path)
    if not source.exists():
        raise FileNotFoundError(
            f"source video missing for stage {stage_number} video {video_id}: {source}"
        )

    try:
        beep = audio_helpers.detect_video_beep(
            root,
            stage_number,
            video,
            source,
            project=project,
        )
    except beep_detect.BeepNotFoundError:
        _apply_beep_not_found(video)
        project.save(root)
        return _summary(video, gate_threshold=None, error="not_found")

    resolved_auto = automation_module.resolve_automation(
        project_override=project.automation,
    )
    threshold = resolved_auto.settings.beep_low_confidence_threshold
    _apply_detected_beep(video, beep, threshold=threshold)
    project.save(root)
    return _summary(video, gate_threshold=threshold)


def _apply_detected_beep(video: Any, beep: Any, *, threshold: float) -> None:
    """Persist a successful detection onto ``video``.

    Mirrors the logic in ``server.py:_run_detect_beep_for_video`` but
    skipped pieces (cross-align, audit-trim chain, shot-detect chain)
    so the MCP-driven flow stays synchronous and predictable.
    """
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
    # Audit trim is anchored to beep_time; a fresh detection invalidates
    # any cached clip. The MCP doesn't run the trim job itself, so we
    # leave the flag False -- the SPA / a future tool re-trims on next
    # view.
    video.processed["trim"] = False
    # Auto-trust gate (#219): high-confidence detections skip the
    # review pill; below-threshold land in the HITL queue.
    video.beep_reviewed = beep.confidence >= threshold
    if video.role == "primary":
        video.processed["shot_detect"] = False


def _apply_beep_not_found(video: Any) -> None:
    """Persist a no-candidate result so the HITL queue can surface it."""
    video.beep_time = None
    video.beep_source = "auto"
    video.beep_peak_amplitude = None
    video.beep_duration_ms = None
    video.beep_confidence = None
    video.beep_candidates = []
    video.beep_auto_detect_failed = True
    video.beep_alignment_confidence = None
    video.beep_alignment_delta_ms = None
    video.processed["beep"] = True
    video.processed["trim"] = False
    video.beep_reviewed = False
    if video.role == "primary":
        video.processed["shot_detect"] = False


def _summary(
    video: Any, *, gate_threshold: float | None, error: str | None = None
) -> dict[str, Any]:
    """Compact response shape -- enough for the agent to decide what
    to do next without a separate get_project round-trip."""
    return {
        "video_id": video.video_id,
        "beep_time": video.beep_time,
        "beep_source": video.beep_source,
        "beep_confidence": video.beep_confidence,
        "beep_reviewed": video.beep_reviewed,
        "beep_auto_detect_failed": video.beep_auto_detect_failed,
        "candidate_count": len(video.beep_candidates),
        "candidates": [c.model_dump(mode="json") for c in video.beep_candidates],
        "auto_trust_threshold": gate_threshold,
        "error": error,
    }
