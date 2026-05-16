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

import json
import logging
import os
import threading
from datetime import UTC, datetime
from typing import Any

from .. import automation as automation_module
from .. import beep_detect
from .. import ensemble as ensemble_module
from ..ui import audio as audio_helpers
from ..ui.project import MatchProject
from .sandbox import resolve_project_root
from .write_tools import _resolve_stage_video

logger = logging.getLogger(__name__)

# Lazy module-level cache for the ensemble runtime (CLAP / GBDT / PANN
# weights, optionally CLIP for voter E). Loading takes ~5 s; subsequent
# calls in the same process are free. Mirrors the HTTP server's
# ``_get_ensemble_runtime`` so MCP clients pay the cost once per server
# lifetime.
_ENSEMBLE_RUNTIME: ensemble_module.EnsembleRuntime | None = None
_ENSEMBLE_RUNTIME_LOCK = threading.Lock()


def _get_ensemble_runtime() -> ensemble_module.EnsembleRuntime:
    """Load + cache the ensemble runtime; thread-safe.

    Test code monkeypatches this function (and
    ``ensemble.detect_shots_ensemble``) to avoid pulling the heavy
    model weights into the test process.
    """
    global _ENSEMBLE_RUNTIME
    if _ENSEMBLE_RUNTIME is None:
        with _ENSEMBLE_RUNTIME_LOCK:
            if _ENSEMBLE_RUNTIME is None:
                with_voter_e = os.environ.get("SPLITSMITH_ENABLE_VOTER_E") == "1"
                _ENSEMBLE_RUNTIME = ensemble_module.load_ensemble_runtime(with_voter_e=with_voter_e)
    return _ENSEMBLE_RUNTIME


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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
            f"video {video_id} on stage {stage_number} is ignored; " "assign it as primary or secondary first"
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
        raise FileNotFoundError(f"source video missing for stage {stage_number} video {video_id}: {source}")

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


def _summary(video: Any, *, gate_threshold: float | None, error: str | None = None) -> dict[str, Any]:
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


def detect_shots_for_stage(
    project_root: str,
    *,
    stage_number: int,
    reset: bool = False,
) -> dict[str, Any]:
    """Run the 4-voter shot-detection ensemble on a stage's audit clip.

    Mirror of ``POST /api/stages/{n}/shot-detect``: loads the audit
    audio (preferring the trim cache, falling back to the full
    primary WAV), runs CLAP + GBDT + PANN consensus, and writes the
    candidate universe + seeded ``shots[]`` into
    ``<project>/audit/stage<N>.json``. The file is the source of
    truth for the audit UI; the SPA + MCP both read / write it.

    Preconditions: stage has a primary, primary has ``beep_time``,
    stage has ``time_seconds > 0``. Raises ``ValueError`` otherwise.

    ``reset=True`` wipes the existing ``shots[]`` before seeding from
    the new run -- useful when re-running detection after fixing a
    wrong beep. By default ``shots[]`` is preserved if non-empty
    (the user retains authority over the curated list).

    First call in a process loads the ensemble runtime (~5 s for
    CLAP + GBDT + PANN weights, plus first-call download if not yet
    cached). Subsequent calls reuse it. Voter E (CLIP image
    encoder, ~600 MB) requires ``SPLITSMITH_ENABLE_VOTER_E=1`` --
    matches the HTTP server.
    """
    root = resolve_project_root(project_root)
    project = MatchProject.load(root)
    try:
        stage = project.stage(stage_number)
    except KeyError as exc:
        raise ValueError(f"stage {stage_number} not found") from exc
    primary = next((v for v in stage.videos if v.role == "primary"), None)
    if primary is None:
        raise ValueError(f"stage {stage_number} has no primary video")
    if primary.beep_time is None:
        raise ValueError(
            f"stage {stage_number} primary has no beep_time yet; " "run detect_beep or set_beep_manual first"
        )
    if stage.time_seconds <= 0:
        raise ValueError(
            f"stage {stage_number} has time_seconds=0; import a "
            "scoreboard or set the stage time before running shot detection"
        )
    source = project.resolve_video_path(root, primary.path)
    if not source.exists():
        raise FileNotFoundError(f"primary source missing for stage {stage_number}: {source}")

    audit = audio_helpers.ensure_audit_audio(root, stage_number, source, primary.beep_time, project=project)
    beep_in_clip = audit.beep_in_clip if audit.beep_in_clip is not None else primary.beep_time

    audit_dir = project.audit_path(root)
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_file = audit_dir / f"stage{stage_number}.json"
    existing_json = _load_or_seed_audit_json(audit_file, stage, beep_in_clip)
    if stage.stage_rounds is not None:
        existing_json["stage_rounds"] = stage.stage_rounds.model_dump(mode="json", exclude_none=True)
    expected_rounds = _expected_rounds_from(existing_json)

    runtime = _get_ensemble_runtime()
    audio_array, sr = beep_detect.load_audio(audit.audio_path)
    cam_class = ensemble_module.camera_class_from_mount(primary.camera_mount)
    enable_e = os.environ.get("SPLITSMITH_ENABLE_VOTER_E") == "1"
    ensemble_cfg = ensemble_module.EnsembleConfig(enable_voter_e=enable_e)
    result = ensemble_module.detect_shots_ensemble(
        audio_array,
        sr,
        beep_in_clip,
        stage.time_seconds,
        runtime,
        expected_rounds=expected_rounds,
        ensemble_config=ensemble_cfg,
        camera_class=cam_class,
        video_path=source if enable_e else None,
        source_beep_time=primary.beep_time if enable_e else None,
    )

    candidates = [_candidate_dict(c) for c in result.candidates]
    existing_json["_candidates_pending_audit"] = {
        "_note": (
            "4-voter ensemble (issue #31). vote_a/b/c/d=1 means the voter "
            "kept the candidate; ensemble_score = vote_total + apriori_boost. "
            "shots[] is seeded from candidates with ensemble_score >= consensus."
        ),
        "consensus": result.consensus,
        "expected_rounds": result.expected_rounds,
        "candidates": candidates,
    }
    if reset:
        existing_json["shots"] = []
    seeded = False
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
        seeded = True
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
                "seeded_shots": seeded,
                "source": "mcp",
            },
        }
    )
    existing_json["audit_events"] = events
    _atomic_write_audit_json(audit_file, existing_json)

    primary.processed["shot_detect"] = True
    project.save(root)
    return {
        "stage_number": stage_number,
        "candidate_count": len(candidates),
        "kept_count": sum(1 for c in result.candidates if c.kept),
        "consensus": result.consensus,
        "expected_rounds": result.expected_rounds,
        "shots_seeded": seeded,
        "shot_count": len(existing_json.get("shots") or []),
    }


def _candidate_dict(cand: Any) -> dict[str, Any]:
    return {
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


def _load_or_seed_audit_json(audit_file: Any, stage: Any, beep_in_clip: float) -> dict[str, Any]:
    if audit_file.exists():
        try:
            return json.loads(audit_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Discarding unreadable audit JSON at %s: %s", audit_file, exc)
    return {
        "stage_number": stage.stage_number,
        "stage_name": stage.stage_name,
        "stage_time_seconds": stage.time_seconds,
        "beep_time": round(beep_in_clip, 4),
        "shots": [],
    }


def _expected_rounds_from(audit_json: dict[str, Any]) -> int | None:
    sr_block = audit_json.get("stage_rounds")
    if isinstance(sr_block, dict):
        raw = sr_block.get("expected")
        if isinstance(raw, int) and raw > 0:
            return raw
    return None


def _atomic_write_audit_json(audit_file: Any, payload: dict[str, Any]) -> None:
    """Atomic write with a ``.bak`` of the previous version.

    Mirrors the SPA's ``put_stage_audit`` write semantics so a
    concurrent SPA / MCP read sees a consistent file.
    """
    tmp = audit_file.with_suffix(audit_file.suffix + ".tmp")
    backup = audit_file.with_suffix(audit_file.suffix + ".bak")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if audit_file.exists():
        if backup.exists():
            backup.unlink()
        audit_file.replace(backup)
    tmp.replace(audit_file)


def trim_audit_clip(
    project_root: str,
    *,
    stage_number: int,
    video_id: str | None = None,
) -> dict[str, Any]:
    """Build (or return cached) the audit-mode short-GOP trim for a stage's
    video.

    Mirror of the trim half of ``POST /api/stages/{n}/detect-beep`` plus the
    SPA's invalidate / re-trim flow. ``video_id=None`` targets the stage's
    primary; pass an explicit ID to trim a secondary instead. The trim
    window is ``[max(0, beep - pre_buffer), beep + stage_time +
    post_buffer]``, anchored to the video's own ``beep_time``.

    Idempotent: returns the cached path when source mtime + trim params
    match. Re-runs ffmpeg on a params mismatch (beep moved, buffer
    settings changed) without the agent having to invalidate first --
    the helper handles cache invalidation transparently.

    Preconditions: video has ``beep_time``, stage has
    ``time_seconds > 0``, source video exists on disk. Raises
    ``ValueError`` / ``FileNotFoundError`` otherwise. Sets
    ``video.processed["trim"] = True`` on success and saves the project.
    """
    root = resolve_project_root(project_root)
    project = MatchProject.load(root)
    try:
        stage = project.stage(stage_number)
    except KeyError as exc:
        raise ValueError(f"stage {stage_number} not found") from exc
    if stage.time_seconds <= 0:
        raise ValueError(f"stage {stage_number} has time_seconds=0; set the stage time " "before trimming")
    if video_id is None:
        target = next((v for v in stage.videos if v.role == "primary"), None)
        if target is None:
            raise ValueError(f"stage {stage_number} has no primary video")
    else:
        target = next((v for v in stage.videos if v.video_id == video_id), None)
        if target is None:
            raise ValueError(
                f"video {video_id} not on stage {stage_number}; "
                f"available: {[v.video_id for v in stage.videos]}"
            )
    if target.beep_time is None:
        raise ValueError(
            f"video {target.video_id} on stage {stage_number} has no "
            "beep_time yet; run detect_beep or set_beep_manual first"
        )
    source = project.resolve_video_path(root, target.path)
    if not source.exists():
        raise FileNotFoundError(
            f"source video missing for stage {stage_number} " f"video {target.video_id}: {source}"
        )

    output = audio_helpers.ensure_video_audit_trim(
        root,
        stage_number,
        target,
        source,
        target.beep_time,
        stage.time_seconds,
        project=project,
    )
    target.processed["trim"] = True
    project.save(root)
    return {
        "video_id": target.video_id,
        "role": target.role,
        "stage_number": stage_number,
        "beep_time": target.beep_time,
        "stage_time_seconds": stage.time_seconds,
        "output_path": str(output),
    }
