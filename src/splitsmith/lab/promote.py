"""Promote-from-anchor engine (issue #124).

Pure function: takes pre-loaded audio arrays + anchor data and returns a
complete fixture dict + promotion report.  No file I/O; the CLI wires
inputs/outputs to disk.

Pipeline:
1. Cross-align secondary audio to anchor's beep (existing cross_align module).
2. Warn when alignment confidence < 1.5 (threshold from cross_align docs).
3. Estimate constant offset + linear drift by fitting (time_since_beep, offset)
   over all successfully snapped shots.
4. Run ensemble shot detection on the secondary (full 4-voter pipeline).
5. Snap each anchor shot to the nearest Voter-A-positive candidate within
   ``snap_window_ms`` using the snap_window module.
6. Build the fixture JSON with camera / anchor / history blocks.
7. Build a promotion report with per-shot diagnostics and aggregates.

Design decisions from issue #97 / #123:
- Missed shots (no Voter-A candidate within window) appear in shots[] with
  source="promoted-missed" so the review UI can surface them for escalation.
- Subclass (paper / steel) is copied from the anchor -- it is shot-physics-
  bound and does not depend on microphone position.
- The anchor block carries the revision SHA so stale-anchor detection works.
- history[] always receives one "promote-from-anchor" entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..cross_align import CrossAlignResult, align_secondary_to_primary
from ..ensemble.api import EnsembleResult, detect_shots_ensemble, load_ensemble_runtime
from ..fixture_schema import (
    AnchorLink,
    Camera,
    HistoryEntry,
    now_iso,
    shots_revision_sha,
)
from .core import (
    DEFAULT_SHOOTER_KEY,
    build_event_id,
    event_id_from_payload,
    match_stage_from_slug,
)
from .snap_window import SnapResult, guided_snap_anchor_shots, snap_anchor_shots

_TOOL_VERSION = "0.1.0"
_ALIGN_CONFIDENCE_WARN = 1.5
_DEFAULT_SNAP_WINDOW_MS = 60.0
_DEFAULT_MIN_SPACING_MS = 80.0


# ---------------------------------------------------------------------------
# Request / result types
# ---------------------------------------------------------------------------


@dataclass
class PromoteFromAnchorRequest:
    """All inputs for :func:`promote_from_anchor`."""

    anchor_data: dict[str, Any]
    primary_audio: np.ndarray
    primary_sr: int
    secondary_audio: np.ndarray
    secondary_sr: int
    secondary_source_desc: str
    camera: Camera
    slug: str
    snap_window_ms: float = _DEFAULT_SNAP_WINDOW_MS
    min_spacing_ms: float = _DEFAULT_MIN_SPACING_MS
    # When the caller has the secondary's audited beep position (project
    # flow: ``StageVideo.beep_time`` from in-stream detect or manual
    # override), pass it here. The engine then skips cross-correlation
    # entirely -- the offset is just ``secondary_beep_time - anchor_beep``.
    # Cross-correlation remains the fallback for raw-WAV / CLI use where
    # no audited beep exists.
    secondary_beep_time: float | None = None


@dataclass
class PromoteFromAnchorResult:
    """Outputs of :func:`promote_from_anchor`."""

    fixture_data: dict[str, Any]
    promotion_report: dict[str, Any]
    snap_results: list[SnapResult]
    ensemble_result: EnsembleResult
    align: CrossAlignResult
    secondary_beep_time: float
    drift_ms_per_minute: float | None
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def promote_from_anchor(
    req: PromoteFromAnchorRequest,
    *,
    runtime: Any | None = None,
) -> PromoteFromAnchorResult:
    """Run the full promote-from-anchor pipeline.

    Args:
        req: all inputs (audio arrays, anchor data, camera metadata, etc.)
        runtime: pre-loaded :class:`~splitsmith.ensemble.api.EnsembleRuntime`.
            When ``None`` the runtime is loaded on demand (slow first call).

    Returns:
        :class:`PromoteFromAnchorResult` containing the fixture dict ready
        to write, the promotion report, and diagnostic intermediates.

    Raises:
        :class:`~splitsmith.cross_align.CrossAlignError` when the beep
        alignment cannot be computed (e.g. secondary is too short).
    """
    warnings: list[str] = []
    anchor = req.anchor_data

    anchor_beep = float(anchor["beep_time"])
    anchor_shots: list[dict[str, Any]] = anchor.get("shots", [])
    anchor_shot_times = [float(s["time"]) for s in anchor_shots]
    stage_time = float(anchor["stage_time_seconds"])
    expected_rounds: int | None = anchor.get("stage_rounds", {}).get("expected")

    # 1. Determine the secondary beep position. The project flow passes
    # the audited ``StageVideo.beep_time`` directly so we don't re-derive
    # what the ingest screen already captured. Cross-correlation only
    # fires when no known beep is supplied (CLI / raw-WAV use).
    if req.secondary_beep_time is not None:
        secondary_beep_time = float(req.secondary_beep_time)
        align = CrossAlignResult(
            secondary_beep_time=secondary_beep_time,
            lag_seconds=secondary_beep_time - anchor_beep,
            confidence=float("inf"),
            peak_correlation=1.0,
            method="known_beeps",
        )
    else:
        align = align_secondary_to_primary(
            primary_audio=req.primary_audio,
            primary_sr=req.primary_sr,
            primary_beep_time=anchor_beep,
            secondary_audio=req.secondary_audio,
            secondary_sr=req.secondary_sr,
        )
        if align.confidence < _ALIGN_CONFIDENCE_WARN:
            warnings.append(
                f"cross-align confidence {align.confidence:.2f} is below "
                f"{_ALIGN_CONFIDENCE_WARN} -- alignment may be inaccurate "
                f"(offset {align.lag_seconds:.3f}s). "
                "Consider using --secondary-wav with the project-extracted audio "
                "or supplying a manual offset."
            )
        secondary_beep_time = align.secondary_beep_time

    # 2. Ensemble detection on secondary.
    if runtime is None:
        runtime = load_ensemble_runtime()

    ensemble_result = detect_shots_ensemble(
        req.secondary_audio,
        req.secondary_sr,
        beep_time=secondary_beep_time,
        stage_time=stage_time,
        runtime=runtime,
        expected_rounds=expected_rounds,
    )
    voter_a_candidates = [
        (c.time, c.confidence) for c in ensemble_result.candidates if c.vote_a >= 1
    ]

    # 3. Snap. Two paths:
    #    - Trusted-prior (project flow with known beep): guided snap in
    #      onset mode (rising-edge time = ground-truth shot moment),
    #      run twice. First pass with a wide window (150 ms default)
    #      absorbs cross-camera clock drift; we fit a linear drift
    #      model on the first-pass displacements; the second pass
    #      re-snaps with drift-corrected predictions and a tighter
    #      window so the result is robust against multi-second stages
    #      where drift would push later shots outside a single-pass
    #      window.
    #    - Untrusted-prior (cross-correlation fallback): threshold-based
    #      snap against voter A. Without a known beep we can't trust
    #      the offset enough to drop the detector threshold.
    if req.secondary_beep_time is not None:
        first_pass = guided_snap_anchor_shots(
            anchor_beep_time=anchor_beep,
            anchor_shots=anchor_shot_times,
            secondary_beep_time=secondary_beep_time,
            secondary_audio=req.secondary_audio,
            secondary_sr=req.secondary_sr,
            window_ms=max(150.0, req.snap_window_ms * 2.5),
            min_spacing_ms=req.min_spacing_ms,
            mode="onset",
            drift_ms_per_s=0.0,
        )
        first_pass_drift_per_minute = _estimate_drift(first_pass)
        drift_ms_per_s = (
            (first_pass_drift_per_minute or 0.0) / 60.0
            if first_pass_drift_per_minute is not None
            else 0.0
        )
        snaps = guided_snap_anchor_shots(
            anchor_beep_time=anchor_beep,
            anchor_shots=anchor_shot_times,
            secondary_beep_time=secondary_beep_time,
            secondary_audio=req.secondary_audio,
            secondary_sr=req.secondary_sr,
            window_ms=req.snap_window_ms,
            min_spacing_ms=req.min_spacing_ms,
            mode="onset",
            drift_ms_per_s=drift_ms_per_s,
        )
    else:
        snaps = snap_anchor_shots(
            anchor_beep_time=anchor_beep,
            anchor_shots=anchor_shot_times,
            secondary_beep_time=secondary_beep_time,
            voter_a_candidates=voter_a_candidates,
            window_ms=req.snap_window_ms,
            min_spacing_ms=req.min_spacing_ms,
        )

    # 4. Drift estimation on the final pass: linear fit of displacement
    # vs time-since-beep. After the two-pass correction this is the
    # *residual* drift the second pass couldn't model linearly.
    drift_ms_per_minute = _estimate_drift(snaps)

    # 5. Build fixture JSON.
    anchor_sha = shots_revision_sha(anchor_shots)
    anchor_link = AnchorLink(
        fixture_slug=_slug_from_source(anchor),
        revision_sha=anchor_sha,
        promoted_at=now_iso(),
        offset_seconds=secondary_beep_time - anchor_beep,
        drift_ms_per_minute=drift_ms_per_minute,
        snap_window_ms=int(req.snap_window_ms),
    )
    history_entry = HistoryEntry(
        at=now_iso(),
        action="promote-from-anchor",
        tool_version=_TOOL_VERSION,
        details={
            "secondary_source": req.secondary_source_desc,
            "cross_align_confidence": round(align.confidence, 3),
            "voter_a_candidate_count": len(voter_a_candidates),
            "snapped": sum(1 for s in snaps if s.snapped_time is not None),
            "missed": sum(1 for s in snaps if s.sanity_flag == "no-candidate"),
            "sanity_flagged": sum(1 for s in snaps if s.sanity_flag not in ("", "no-candidate")),
        },
    )
    fixture_data = _build_fixture(
        anchor=anchor,
        snaps=snaps,
        anchor_shots=anchor_shots,
        secondary_beep_time=secondary_beep_time,
        secondary_source_desc=req.secondary_source_desc,
        slug=req.slug,
        camera=req.camera,
        anchor_link=anchor_link,
        history_entry=history_entry,
        ensemble_result=ensemble_result,
    )

    # 6. Promotion report.
    report = _build_report(
        slug=req.slug,
        secondary_source=req.secondary_source_desc,
        anchor_slug=anchor_link.fixture_slug,
        align=align,
        snaps=snaps,
        drift_ms_per_minute=drift_ms_per_minute,
        snap_window_ms=req.snap_window_ms,
        voter_a_count=len(voter_a_candidates),
        total_candidates=len(ensemble_result.candidates),
        warnings=warnings,
    )

    return PromoteFromAnchorResult(
        fixture_data=fixture_data,
        promotion_report=report,
        snap_results=snaps,
        ensemble_result=ensemble_result,
        align=align,
        secondary_beep_time=secondary_beep_time,
        drift_ms_per_minute=drift_ms_per_minute,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _estimate_drift(snaps: list[SnapResult]) -> float | None:
    """Fit a line through (time_since_beep_s, displacement_ms) for snapped shots.

    Returns drift in ms/minute (positive = secondary clock runs slow relative
    to primary).  Returns ``None`` when fewer than 2 snapped shots are
    available.
    """
    pairs = [
        (s.time_since_beep_s, s.displacement_ms)
        for s in snaps
        if s.snapped_time is not None and s.displacement_ms is not None
    ]
    if len(pairs) < 2:
        return None
    times = np.array([p[0] for p in pairs], dtype=np.float64)
    disps = np.array([p[1] for p in pairs], dtype=np.float64)
    slope = float(np.polyfit(times, disps, 1)[0])
    return round(slope * 60.0, 3)


def _slug_from_source(anchor: dict[str, Any]) -> str:
    """Best-effort slug from the anchor's source or stage fields."""
    src = anchor.get("source", "")
    if src:
        import re

        name = re.sub(r"[^a-z0-9]+", "-", src.lower()).strip("-")
        return name[:60] or "anchor"
    stage = anchor.get("stage_number")
    name = anchor.get("stage_name", "")
    if stage:
        return f"stage{stage}" + (f"-{name}".lower().replace(" ", "-") if name else "")
    return "anchor"


def _build_fixture(
    *,
    anchor: dict[str, Any],
    snaps: list[SnapResult],
    anchor_shots: list[dict[str, Any]],
    secondary_beep_time: float,
    secondary_source_desc: str,
    slug: str,
    camera: Camera,
    anchor_link: AnchorLink,
    history_entry: HistoryEntry,
    ensemble_result: EnsembleResult,
) -> dict[str, Any]:
    """Assemble the complete fixture JSON for the derived secondary fixture."""
    shots_out: list[dict[str, Any]] = []
    for i, snap in enumerate(snaps):
        anchor_shot = anchor_shots[i] if i < len(anchor_shots) else {}
        if snap.snapped_time is not None:
            ms_after_beep = round((snap.snapped_time - secondary_beep_time) * 1000)
            shots_out.append(
                {
                    "shot_number": snap.shot_number,
                    "candidate_number": _find_candidate_number(snap.snapped_time, ensemble_result),
                    "time": round(snap.snapped_time, 4),
                    "ms_after_beep": ms_after_beep,
                    "source": "promoted",
                    "subclass": anchor_shot.get("subclass", "unknown"),
                    "snap_displacement_ms": round(snap.displacement_ms or 0.0, 2),
                    "sanity_flag": snap.sanity_flag,
                }
            )
        else:
            # Include missed shots so the review UI can surface them.
            shots_out.append(
                {
                    "shot_number": snap.shot_number,
                    "candidate_number": None,
                    "time": None,
                    "ms_after_beep": None,
                    "source": "promoted-missed",
                    "subclass": anchor_shot.get("subclass", "unknown"),
                    "snap_displacement_ms": None,
                    "sanity_flag": snap.sanity_flag,
                }
            )

    candidates_block = {
        "candidates": [c.model_dump(mode="json") for c in ensemble_result.candidates],
        "labels_by_time": {},
    }

    return {
        "source": secondary_source_desc,
        "source_video": None,
        "stage_number": anchor.get("stage_number"),
        "stage_name": anchor.get("stage_name"),
        "fixture_window_in_source": None,
        "beep_time": round(secondary_beep_time, 4),
        "tolerance_ms": anchor.get("tolerance_ms", 15),
        "stage_time_seconds": anchor.get("stage_time_seconds"),
        "stage_window_end_in_fixture": None,
        "shots": shots_out,
        "_candidates_pending_audit": candidates_block,
        "audit_notes": "",
        "stage_rounds": anchor.get("stage_rounds"),
        "shooter": _inherit_shooter(anchor),
        "event_id": _inherit_event_id(anchor, anchor_link.fixture_slug),
        "camera": camera.model_dump(mode="json"),
        "anchor": anchor_link.model_dump(mode="json"),
        "history": [history_entry.model_dump(mode="json")],
    }


def _inherit_shooter(anchor: dict[str, Any]) -> dict[str, Any]:
    """Derived fixtures share the anchor's shooter identity verbatim.

    Multi-cam siblings always belong to the same shooter -- they are
    different angles of the same physical run. Falls back to the legacy
    sentinel for anchors that pre-date the field.
    """
    block = anchor.get("shooter")
    if isinstance(block, dict) and isinstance(block.get("id"), str) and block["id"]:
        return dict(block)
    return {"id": DEFAULT_SHOOTER_KEY}


def _inherit_event_id(anchor: dict[str, Any], anchor_slug: str) -> str | None:
    """Derived fixtures share the anchor's event_id when set; otherwise
    the same shooter + stage parse + match keeps siblings grouped."""
    raw = anchor.get("event_id")
    if isinstance(raw, str) and raw:
        return raw
    parsed = match_stage_from_slug(anchor_slug)
    if parsed is None:
        return None
    match_slug, n = parsed
    shooter_block = anchor.get("shooter")
    shooter_key = DEFAULT_SHOOTER_KEY
    if isinstance(shooter_block, dict):
        sid = shooter_block.get("id")
        if isinstance(sid, str) and sid:
            shooter_key = sid
    return build_event_id(match_slug, n, shooter_key)


# Re-exported so callers (server.py promote-from-project) can resolve the
# event_id without re-importing core.
_ = (event_id_from_payload,)


def _find_candidate_number(snap_time: float, ensemble: EnsembleResult) -> int | None:
    """Return the candidate_number of the ensemble candidate closest to snap_time."""
    best: int | None = None
    best_dist = float("inf")
    for c in ensemble.candidates:
        d = abs(c.time - snap_time)
        if d < best_dist:
            best_dist = d
            best = c.candidate_number
    return best


def _build_report(
    *,
    slug: str,
    secondary_source: str,
    anchor_slug: str,
    align: CrossAlignResult,
    snaps: list[SnapResult],
    drift_ms_per_minute: float | None,
    snap_window_ms: float,
    voter_a_count: int,
    total_candidates: int,
    warnings: list[str],
) -> dict[str, Any]:
    snapped = [s for s in snaps if s.snapped_time is not None]
    displacements = [s.displacement_ms for s in snapped if s.displacement_ms is not None]
    amplitudes = [s.snap_confidence for s in snapped if s.snap_confidence is not None]

    # Fixture-level quality verdict. Catches "this clip doesn't actually
    # belong to this stage" -- block-max guided snap finds *some* peak
    # in any window, even silence, so a per-shot relative threshold
    # alone can't distinguish "shot we snapped to" from "noise blip in a
    # wrong-stage clip". The structure gives it away though: a real
    # match has tightly-clustered residuals after drift correction and
    # consistent shot amplitudes; a wrong-clip match has neither.
    disp_std = float(np.std(displacements)) if len(displacements) > 1 else 0.0
    disp_p95 = float(np.percentile(displacements, 95)) if displacements else None
    amp_median = float(np.median(amplitudes)) if amplitudes else None
    amp_p10 = float(np.percentile(amplitudes, 10)) if amplitudes else None
    # Flag the fixture as suspicious when:
    #   - residual displacement std > 30 ms (snaps look like noise picks
    #     within the search window rather than locking to a real peak)
    #   - OR median snap amplitude < 0.05 (whole clip is essentially
    #     silent at "shot" positions)
    #   - OR fewer than 60% of shots snapped (lots of "no candidate")
    snap_rate = len(snapped) / len(snaps) if snaps else 0.0
    wrong_clip_suspected = bool(
        len(snapped) >= 2
        and (disp_std > 30.0 or (amp_median is not None and amp_median < 0.05) or snap_rate < 0.6)
    )

    quality_warnings: list[str] = []
    if disp_std > 30.0:
        quality_warnings.append(
            f"residual displacement std {disp_std:.1f} ms is high; "
            "snaps may be landing on noise rather than actual shots"
        )
    if amp_median is not None and amp_median < 0.05:
        quality_warnings.append(
            f"median snap amplitude {amp_median:.3f} is very low; "
            "the secondary may be silent at shot positions "
            "(wrong stage / occluded mic / wrong video)"
        )
    if snap_rate < 0.6:
        quality_warnings.append(
            f"only {len(snapped)}/{len(snaps)} shots snapped; "
            "verify that this clip covers the right stage"
        )

    return {
        "slug": slug,
        "secondary_source": secondary_source,
        "anchor_slug": anchor_slug,
        "cross_align": {
            "method": align.method,
            "secondary_beep_time": round(align.secondary_beep_time, 4),
            "offset_seconds": round(align.lag_seconds, 4),
            "confidence": (
                None if align.confidence == float("inf") else round(align.confidence, 3)
            ),
            "peak_correlation": round(align.peak_correlation, 4),
        },
        "snap_window_ms": snap_window_ms,
        "drift_ms_per_minute": drift_ms_per_minute,
        "counts": {
            "anchor_shots": len(snaps),
            "snapped": len(snapped),
            "missed": sum(1 for s in snaps if s.sanity_flag == "no-candidate"),
            "monotonicity_flagged": sum(1 for s in snaps if s.sanity_flag == "monotonicity"),
            "min_spacing_flagged": sum(1 for s in snaps if s.sanity_flag == "min-spacing"),
            "voter_a_candidates": voter_a_count,
            "total_candidates": total_candidates,
        },
        "displacement_stats": {
            "mean_ms": round(float(np.mean(displacements)), 2) if displacements else None,
            "stdev_ms": round(disp_std, 2) if len(displacements) > 1 else None,
            "p95_ms": round(disp_p95, 2) if disp_p95 is not None else None,
            "min_ms": round(min(displacements), 2) if displacements else None,
            "max_ms": round(max(displacements), 2) if displacements else None,
        },
        "amplitude_stats": {
            "median": round(amp_median, 4) if amp_median is not None else None,
            "p10": round(amp_p10, 4) if amp_p10 is not None else None,
            "low_amplitude_shots": sum(1 for s in snaps if s.sanity_flag == "low-amplitude"),
        },
        "quality": {
            "wrong_clip_suspected": wrong_clip_suspected,
            "snap_rate": round(snap_rate, 3),
            "warnings": quality_warnings,
        },
        "per_shot": [s.model_dump() for s in snaps],
        "warnings": warnings + quality_warnings,
    }
