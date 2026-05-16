"""Top-level ensemble entry point + runtime container.

``load_ensemble_runtime`` does the slow side-effect work once (model
weights, calibration JSON). ``detect_shots_ensemble`` is then a pure
function of ``(audio, sr, beep_time, stage_time, runtime, ...)`` over
the per-candidate universe voter A produces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from ..config import ShotDetectConfig
from ..shot_detect import detect_shots
from . import features as feat
from . import visual as vis
from . import voters
from .calibration import (
    EnsembleCalibration,
    load_calibration,
    load_voter_c_model,
    load_voter_e_probe,
)
from .tta import compute_tta_agreement


class EnsembleConfig(BaseModel):
    """Tunable parameters that don't depend on the calibration set."""

    consensus: int = Field(
        default=2,
        ge=1,
        le=4,
        description=(
            "Keep when ``vote_total + apriori_boost >= consensus``. "
            "Default 2-of-3 (voter D was folded into voter C as a "
            "feature, so the ensemble runs A+B+C) -- with "
            "``c_required=True`` this requires C plus at least one of "
            "{A, B}. Voter A's lowest-positive floor makes A=1 on "
            "every calibration positive, so the typical kept candidate "
            "has A and C voting yes."
        ),
    )
    apriori_boost: float = Field(
        default=1.0,
        ge=0.0,
        description=(
            "Magnitude of the apriori boost for top-N candidates by "
            "detector confidence (1.0 = equivalent to one extra vote). "
            "Applied only when ``expected_rounds`` is provided."
        ),
    )
    c_required: bool = Field(
        default=True,
        description=(
            "Issue #103: require voter C to say yes for a candidate to "
            "be kept (in addition to consensus). On the 281-FP labeled "
            "set, voter C correctly rejected 100 % of hand-labeled FPs "
            "while voters A/B were calibrated for high recall and "
            "rubber-stamped most of them. Pairing C-veto with the "
            "broadened voter-C adaptive slack (see ``vote_c_adaptive``) "
            "holds recall at 100 % while suppressing the bulk of FPs."
        ),
    )
    enable_voter_e: bool = Field(
        default=False,
        description=(
            "Issue #183: run Voter E (CLIP visual probe) when a video "
            "path and source-beep timestamp are provided. Off by default "
            "for the first release until field-tested. Requires a built "
            "Voter E probe artifact in package data."
        ),
    )
    e_required: bool = Field(
        default=False,
        description=(
            "When True, require Voter E to say yes for a candidate to be "
            "kept (in addition to ``c_required`` and consensus). Acts as "
            "a precision veto, mirroring ``c_required``. Only takes "
            "effect when ``enable_voter_e`` is also True."
        ),
    )
    e_audio_strong_min_votes: int | None = Field(
        default=3,
        description=(
            "Issue #185: when set, suppress Voter E's veto on candidates "
            "whose audio-side ``vote_total`` (A+B+C) is at or above this "
            "value. Default ``3`` skips Voter E whenever all three audio "
            "voters already agreed -- that's the 'audio unanimous' "
            "signal most reliably correlated with a true shot. Was ``4`` "
            "while voter D existed (A+B+C+D unanimous); after the fold "
            "the equivalent rule is ``3``. ``None`` reproduces the "
            "unconditional veto from #183. Only takes effect when both "
            "``enable_voter_e`` and ``e_required`` are True."
        ),
    )
    within_stage_amp_floor: float | None = Field(
        default=0.15,
        ge=0.0,
        description=(
            "Headcam-only post-consensus veto: drop kept candidates whose "
            "peak amplitude is below ``within_stage_amp_floor * anchor``, "
            "where ``anchor = median(top-K kept by peak_amp)`` and "
            "``K = stage_rounds.expected`` (fallback: p75 of kept "
            "peak_amps). The mic moves with the gun on a headcam, so "
            "TPs cluster around a stable amplitude within a stage; "
            "cross-bay shots and handling noises arrive much quieter. "
            "Skipped for any camera class != ``headcam`` because the "
            "mic-follows-gun assumption breaks when the mic position is "
            "decoupled from the shooter. ``None`` disables. Default "
            "0.15 was picked across two headcam shooters (s97dcec94, "
            "s0fe3d797) on 21 audited stages: holds TP loss at ~3% on "
            "both while cutting 19% and 47% of post-consensus FPs "
            "respectively. The threshold sweep falls off a cliff at "
            "0.20 on s0fe3d797 (TP loss jumps to 14.5%), so do not "
            "raise without per-camera-model validation."
        ),
    )


class EnsembleCandidate(BaseModel):
    """One candidate from the voter-A universe with all per-voter signals.

    Mirrors the structure ``build_ensemble_fixture.py`` writes into
    ``_candidates_pending_audit.candidates``, so the audit UI can render
    the same per-voter detail regardless of which path produced it.
    """

    candidate_number: int
    time: float
    ms_after_beep: int
    peak_amplitude: float
    confidence: float
    vote_a: int
    vote_b: int
    vote_c: int
    vote_e: int = Field(
        default=0,
        description=(
            "1 when Voter E (CLIP visual probe, issue #183) accepts the "
            "candidate. Always 0 when Voter E is disabled or not "
            "calibrated for this camera class."
        ),
    )
    vote_total: int
    apriori_boost: float
    ensemble_score: float
    score_c: float
    clap_diff: float
    gunshot_prob: float = Field(
        description=(
            "PANN ``Gunshot, gunfire`` class probability. Folded into "
            "voter C as a feature; surfaced here for audit-trail "
            "interpretability."
        ),
    )
    voter_e_signal: float = Field(
        default=0.0,
        description=(
            "CLIP probe ``P(shot)`` for the candidate's frame. 0.0 when "
            "Voter E is disabled or not run for this candidate's camera "
            "class."
        ),
    )
    kept: bool = Field(
        description="True when this candidate is part of the consensus shots set.",
    )
    amp_floor_vetoed: bool = Field(
        default=False,
        description=(
            "True when this candidate would have been kept by consensus "
            "but was dropped by the headcam within-stage amplitude floor "
            "(see ``EnsembleConfig.within_stage_amp_floor``). Lets the "
            "audit UI distinguish 'lost the vote' from 'too quiet for "
            "this stage' on otherwise high-vote candidates."
        ),
    )


class EnsembleResult(BaseModel):
    """Full universe + consensus subset (Pydantic so it crosses module boundaries cleanly)."""

    candidates: list[EnsembleCandidate]
    consensus: int
    expected_rounds: int | None = None


@dataclass
class EnsembleRuntime:
    """Loaded heavy state. Build once via ``load_ensemble_runtime``; reuse."""

    calibration: EnsembleCalibration
    voter_c_model: dict[str, Any]
    clap: feat.ClapRuntime
    pann: feat.PannRuntime
    # Track the prompt list the calibration was built against so we can
    # warn if the on-package CLAP prompt bank ever drifts.
    expected_prompts: tuple[str, ...] = field(default_factory=tuple)
    visual: vis.VisualRuntime | None = None


def load_ensemble_runtime(*, with_voter_e: bool = True) -> EnsembleRuntime:
    """Materialise calibration + heavy models. Slow first-call (model downloads).

    ``with_voter_e`` controls whether the CLIP visual model is loaded
    eagerly. Default ``True``; falls back to a runtime without Voter E
    when the probe artifact is missing or the calibration predates it.
    """
    calibration = load_calibration()
    voter_c_model = load_voter_c_model()
    if tuple(calibration.clap_prompts) != feat.CLAP_PROMPTS:
        raise RuntimeError(
            "ensemble calibration prompt bank does not match the package's "
            "CLAP_PROMPTS. Rebuild artifacts via "
            "scripts/build_ensemble_artifacts.py."
        )
    clap = feat.load_clap_runtime()
    pann = feat.load_pann_runtime()

    visual: vis.VisualRuntime | None = None
    if with_voter_e and calibration.voter_e_probe_artifact:
        probe = load_voter_e_probe(calibration.voter_e_probe_artifact)
        if probe is not None:
            visual = vis.load_visual_runtime(
                probe,
                model_id=calibration.voter_e_clip_model_id or vis.CLIP_VISUAL_MODEL_ID,
            )

    return EnsembleRuntime(
        calibration=calibration,
        voter_c_model=voter_c_model,
        clap=clap,
        pann=pann,
        expected_prompts=tuple(calibration.clap_prompts),
        visual=visual,
    )


def detect_shots_ensemble(
    audio: np.ndarray,
    sample_rate: int,
    beep_time: float,
    stage_time: float,
    runtime: EnsembleRuntime,
    *,
    expected_rounds: int | None = None,
    ensemble_config: EnsembleConfig | None = None,
    camera_class: str | None = None,
    camera_make: str | None = None,
    camera_model: str | None = None,
    video_path: Path | None = None,
    source_beep_time: float | None = None,
) -> EnsembleResult:
    """Run all four voters over the same per-candidate universe.

    Voter A produces the universe at ``min_confidence=0.0`` (and
    ``recall_fallback="cwt"`` to match the calibration generator); the
    calibrated floor is then applied as voter A's vote. The other voters
    score every candidate using shipped thresholds + the trained GBDT.

    When ``expected_rounds`` is provided (typically from the audit JSON's
    ``stage_rounds.expected``):
      * Voter C switches to its adaptive top-(K+slack) mode.
      * Apriori boost adds ``+ensemble_config.apriori_boost`` to the
        top-K candidates by detector confidence.

    ``camera_class`` selects the per-class threshold set from the shipped
    calibration (issue #137). ``None`` falls back to the artifact's
    default class -- for existing projects that's ``headcam``, byte-
    identical to pre-#137 behaviour.

    ``camera_make`` + ``camera_model`` are routed to the within-stage
    amplitude floor lookup (issue #304). When both are set and the
    calibrated ``amp_floor_by_camera_model`` table knows them, the veto
    uses the per-model value; otherwise it falls back to
    ``ensemble_config.within_stage_amp_floor`` (the generic-headcam
    default). The veto itself remains headcam-only.

    ``video_path`` + ``source_beep_time`` enable Voter E (issue #183)
    when ``ensemble_config.enable_voter_e`` is True and ``runtime.visual``
    is loaded. ``video_path`` is the source video file; ``source_beep_time``
    is the beep timestamp inside that file (the same value the audit
    clip's ``beep_in_clip`` is anchored to). Both are optional -- without
    them the ensemble runs without Voter E exactly as before.
    """
    cfg = ensemble_config or EnsembleConfig()
    cal = runtime.calibration
    thresholds = cal.thresholds_for(camera_class)

    # Voter A universe: maximum recall (raw detector, no confidence floor).
    # ``recall_fallback="cwt"`` matches the calibration script so the
    # candidate set the GBDT was trained against is the same set we're
    # scoring at runtime.
    detector_cfg = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0)
    shots = detect_shots(audio, sample_rate, beep_time, stage_time, detector_cfg)
    n = len(shots)
    if n == 0:
        return EnsembleResult(candidates=[], consensus=cfg.consensus, expected_rounds=expected_rounds)

    times = np.array([s.time_absolute for s in shots], dtype=np.float64)
    confidences = np.array([s.confidence for s in shots], dtype=np.float64)
    peak_amps = np.array([s.peak_amplitude for s in shots], dtype=np.float64)
    ms_after_beep = np.array([round(s.time_from_beep * 1000) for s in shots], dtype=np.int64)

    # Per-voter signals.
    tta_agreement = compute_tta_agreement(audio, sample_rate, beep_time, stage_time, times)
    hand = feat.compute_hand_features(
        audio, sample_rate, times, beep_time, confidences, peak_amps, tta_agreement
    )
    clap_sims = feat.compute_clap_similarities(audio, sample_rate, times, runtime.clap)
    clap_diff = feat.clap_diff_from_similarities(clap_sims)
    gunshot_prob = feat.compute_pann_gunshot_probs(audio, sample_rate, times, runtime.pann)
    voter_c_x = feat.voter_c_feature_matrix(
        hand, clap_sims, clap_diff, gunshot_prob, camera_classes=camera_class
    )
    cls_key = camera_class if camera_class in runtime.voter_c_model else cal.default_camera_class
    score_c = runtime.voter_c_model[cls_key].predict_proba(voter_c_x)[:, 1].astype(np.float64)

    va = voters.vote_a(confidences, thresholds.voter_a_floor)
    vb = voters.vote_b(clap_diff, thresholds.voter_b_threshold)
    if expected_rounds is not None and expected_rounds > 0:
        vc = voters.vote_c_adaptive(score_c, expected_rounds)
    else:
        vc = voters.vote_c_global(score_c, thresholds.voter_c_threshold)

    voter_e_signal = np.zeros(n, dtype=np.float32)
    ve = np.zeros(n, dtype=np.int64)
    voter_e_active = (
        cfg.enable_voter_e
        and runtime.visual is not None
        and video_path is not None
        and source_beep_time is not None
        and thresholds.voter_e_threshold is not None
    )
    if voter_e_active:
        source_times = vis.candidate_times_in_source(
            times,
            audit_beep_in_clip=beep_time,
            source_beep_time=float(source_beep_time),
        )
        offsets = tuple(cal.voter_e_frame_offsets) if cal.voter_e_frame_offsets else vis.DEFAULT_FRAME_OFFSETS
        features = vis.compute_visual_features(
            Path(video_path), source_times, runtime.visual, frame_offsets=offsets
        )
        voter_e_signal = vis.score_visual_candidates(features, runtime.visual)
        ve = voters.vote_e(voter_e_signal, float(thresholds.voter_e_threshold))

    vote_total = va + vb + vc
    boost = voters.apriori_boost(confidences, expected_rounds, cfg.apriori_boost)
    ensemble_score = vote_total.astype(np.float64) + boost
    keep_mask = voters.consensus_keep(
        vote_total,
        boost,
        cfg.consensus,
        vote_c=vc,
        c_required=cfg.c_required,
    )
    if voter_e_active and cfg.e_required:
        veto_mask = ~ve.astype(bool)
        if cfg.e_audio_strong_min_votes is not None:
            audio_strong = vote_total >= int(cfg.e_audio_strong_min_votes)
            veto_mask = veto_mask & ~audio_strong
        keep_mask = keep_mask & ~veto_mask

    effective_cam_class = camera_class or cal.default_camera_class
    amp_floor_vetoed = np.zeros(n, dtype=bool)
    if effective_cam_class == "headcam":
        # Per-model override (#304) wins when the camera is calibrated;
        # otherwise fall back to the engine-side generic-headcam default
        # so any new model that shows up still gets the safe floor.
        resolved_floor = cal.amp_floor_for(
            camera_make,
            camera_model,
            default=cfg.within_stage_amp_floor,
        )
        if resolved_floor is not None:
            new_keep = voters.within_stage_amp_veto(
                peak_amps,
                keep_mask,
                expected_rounds=expected_rounds,
                floor_ratio=resolved_floor,
            )
            amp_floor_vetoed = keep_mask & ~new_keep
            keep_mask = new_keep

    candidates: list[EnsembleCandidate] = []
    for i in range(n):
        candidates.append(
            EnsembleCandidate(
                candidate_number=i + 1,
                time=round(float(times[i]), 4),
                ms_after_beep=int(ms_after_beep[i]),
                peak_amplitude=round(float(peak_amps[i]), 4),
                confidence=round(float(confidences[i]), 3),
                vote_a=int(va[i]),
                vote_b=int(vb[i]),
                vote_c=int(vc[i]),
                vote_e=int(ve[i]),
                vote_total=int(vote_total[i]),
                apriori_boost=float(boost[i]),
                ensemble_score=round(float(ensemble_score[i]), 2),
                score_c=round(float(score_c[i]), 4),
                clap_diff=round(float(clap_diff[i]), 4),
                gunshot_prob=round(float(gunshot_prob[i]), 4),
                voter_e_signal=round(float(voter_e_signal[i]), 4),
                kept=bool(keep_mask[i]),
                amp_floor_vetoed=bool(amp_floor_vetoed[i]),
            )
        )
    return EnsembleResult(
        candidates=candidates,
        consensus=cfg.consensus,
        expected_rounds=expected_rounds,
    )
