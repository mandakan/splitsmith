"""Top-level ensemble entry point + runtime container.

``load_ensemble_runtime`` does the slow side-effect work once (model
weights, calibration JSON). ``detect_shots_ensemble`` is then a pure
function of ``(audio, sr, beep_time, stage_time, runtime, ...)`` over
the per-candidate universe voter A produces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from ..config import ShotDetectConfig
from ..shot_detect import detect_shots
from . import features as feat
from . import voters
from .calibration import EnsembleCalibration, load_calibration, load_voter_c_model


class EnsembleConfig(BaseModel):
    """Tunable parameters that don't depend on the calibration set."""

    consensus: int = Field(
        default=3,
        ge=1,
        le=5,
        description=(
            "Keep when ``vote_total + apriori_boost >= consensus``. "
            "Default 3-of-4 -- preserves recall on the calibration set "
            "while culling the bulk of voter-A's false positives."
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
    vote_d: int
    vote_total: int
    apriori_boost: float
    ensemble_score: float
    score_c: float
    clap_diff: float
    gunshot_prob: float
    kept: bool = Field(
        description="True when this candidate is part of the consensus shots set.",
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
    voter_c_model: Any
    clap: feat.ClapRuntime
    pann: feat.PannRuntime
    # Track the prompt list the calibration was built against so we can
    # warn if the on-package CLAP prompt bank ever drifts.
    expected_prompts: tuple[str, ...] = field(default_factory=tuple)


def load_ensemble_runtime() -> EnsembleRuntime:
    """Materialise calibration + heavy models. Slow first-call (model downloads)."""
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
    return EnsembleRuntime(
        calibration=calibration,
        voter_c_model=voter_c_model,
        clap=clap,
        pann=pann,
        expected_prompts=tuple(calibration.clap_prompts),
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
    """
    cfg = ensemble_config or EnsembleConfig()
    cal = runtime.calibration

    # Voter A universe: maximum recall (raw detector, no confidence floor).
    # ``recall_fallback="cwt"`` matches the calibration script so the
    # candidate set the GBDT was trained against is the same set we're
    # scoring at runtime.
    detector_cfg = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0)
    shots = detect_shots(audio, sample_rate, beep_time, stage_time, detector_cfg)
    n = len(shots)
    if n == 0:
        return EnsembleResult(
            candidates=[], consensus=cfg.consensus, expected_rounds=expected_rounds
        )

    times = np.array([s.time_absolute for s in shots], dtype=np.float64)
    confidences = np.array([s.confidence for s in shots], dtype=np.float64)
    peak_amps = np.array([s.peak_amplitude for s in shots], dtype=np.float64)
    ms_after_beep = np.array([round(s.time_from_beep * 1000) for s in shots], dtype=np.int64)

    # Per-voter signals.
    hand = feat.compute_hand_features(audio, sample_rate, times, beep_time, confidences, peak_amps)
    clap_sims = feat.compute_clap_similarities(audio, sample_rate, times, runtime.clap)
    clap_diff = feat.clap_diff_from_similarities(clap_sims)
    gunshot_prob = feat.compute_pann_gunshot_probs(audio, sample_rate, times, runtime.pann)
    voter_c_x = feat.voter_c_feature_matrix(hand, clap_sims, clap_diff)
    score_c = runtime.voter_c_model.predict_proba(voter_c_x)[:, 1].astype(np.float64)

    va = voters.vote_a(confidences, cal.voter_a_floor)
    vb = voters.vote_b(clap_diff, cal.voter_b_threshold)
    if expected_rounds is not None and expected_rounds > 0:
        vc = voters.vote_c_adaptive(score_c, expected_rounds)
    else:
        vc = voters.vote_c_global(score_c, cal.voter_c_threshold)
    vd = voters.vote_d(gunshot_prob, cal.voter_d_threshold)

    vote_total = va + vb + vc + vd
    boost = voters.apriori_boost(confidences, expected_rounds, cfg.apriori_boost)
    ensemble_score = vote_total.astype(np.float64) + boost
    keep_mask = voters.consensus_keep(vote_total, boost, cfg.consensus)

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
                vote_d=int(vd[i]),
                vote_total=int(vote_total[i]),
                apriori_boost=float(boost[i]),
                ensemble_score=round(float(ensemble_score[i]), 2),
                score_c=round(float(score_c[i]), 4),
                clap_diff=round(float(clap_diff[i]), 4),
                gunshot_prob=round(float(gunshot_prob[i]), 4),
                kept=bool(keep_mask[i]),
            )
        )
    return EnsembleResult(
        candidates=candidates,
        consensus=cfg.consensus,
        expected_rounds=expected_rounds,
    )
