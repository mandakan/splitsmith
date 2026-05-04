"""Test-time augmentation: per-candidate stability score across detector
perturbations (issue #92).

Re-runs ``shot_detect.detect_shots`` on the same audio under four small
perturbations -- ``+/-2 dB`` amplitude scaling and ``+/-5 ms`` integer-sample
time shifts -- and counts, for each base candidate, how many of the perturbed
runs produced a candidate within ``match_tol_ms``. The original run always
counts as one, so the returned agreement is in ``[1, 5]``.

Why this is in voter C's feature space, not a stand-alone voter:

* Real shots clear the candidate generator robustly across small perturbations
  (envelope is dominated by the muzzle blast, sub-frame timing offsets don't
  move the rise foot far). FPs that survive only at the original audio --
  envelope artefacts barely above the smoothed threshold -- drop out under
  one or more perturbations.
* Used as a voter C feature, the GBDT decides how much weight to give
  agreement vs. the existing CLAP/PANN/hand signals. As a stand-alone soft
  veto it would drop borderline real shots in busy fixtures (the issue's
  "soft veto" path is deferred until the feature path validates).

Cost: 4 extra ``detect_shots`` passes per fixture. Detect is the cheapest
stage of the pipeline (no model inference); empirically <100 ms per call on
stage-length audio.
"""

from __future__ import annotations

import numpy as np

from ..config import ShotDetectConfig
from ..shot_detect import detect_shots

_DEFAULT_DB_PERTURB: float = 2.0
_DEFAULT_TIME_SHIFT_MS: float = 5.0
_DEFAULT_MATCH_TOL_MS: float = 15.0


def compute_tta_agreement(
    audio: np.ndarray,
    sample_rate: int,
    beep_time: float,
    stage_time: float,
    base_candidate_times: np.ndarray,
    *,
    db_perturb: float = _DEFAULT_DB_PERTURB,
    time_shift_ms: float = _DEFAULT_TIME_SHIFT_MS,
    match_tol_ms: float = _DEFAULT_MATCH_TOL_MS,
    detector_cfg: ShotDetectConfig | None = None,
) -> np.ndarray:
    """Per-candidate agreement count across 4 detector perturbations + original.

    Returned values are floats in ``[1.0, 5.0]`` (the original run is always
    counted, so the minimum is 1). The integer-valued result is returned as
    float64 so it slots straight into ``compute_hand_features``' feature
    matrix without an extra cast at the call site.
    """
    base = np.asarray(base_candidate_times, dtype=np.float64)
    if base.size == 0:
        return np.zeros(0, dtype=np.float64)
    if detector_cfg is None:
        detector_cfg = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0)

    shift_n = max(1, int(round(time_shift_ms * 1e-3 * sample_rate)))
    audio_f32 = audio.astype(np.float32, copy=False)

    perturbations: list[np.ndarray] = []

    # Amplitude perturbations: scale, redetect, no time-axis remapping.
    for db in (-db_perturb, +db_perturb):
        scaled = audio_f32 * np.float32(10.0 ** (db / 20.0))
        perturbations.append(
            _detect_times(scaled, sample_rate, beep_time, stage_time, detector_cfg)
        )

    # Time perturbations via np.roll. Positive roll = content moved later
    # in the array by ``shift_n`` samples. A candidate at time ``t`` in the
    # rolled audio corresponds to time ``t - shift_s`` in the original
    # (the content that's now at sample ``t*sr`` was originally at sample
    # ``t*sr - shift_n``). Negative roll is symmetric.
    shift_s = shift_n / sample_rate
    plus_audio = np.roll(audio_f32, +shift_n)
    plus_audio[:shift_n] = 0.0
    plus_times = _detect_times(plus_audio, sample_rate, beep_time, stage_time, detector_cfg)
    perturbations.append(plus_times - shift_s)

    minus_audio = np.roll(audio_f32, -shift_n)
    minus_audio[-shift_n:] = 0.0
    minus_times = _detect_times(minus_audio, sample_rate, beep_time, stage_time, detector_cfg)
    perturbations.append(minus_times + shift_s)

    tol_s = match_tol_ms / 1000.0
    agreement = np.ones(base.size, dtype=np.float64)
    for ptimes in perturbations:
        if ptimes.size == 0:
            continue
        # For each base candidate, the nearest perturbed candidate suffices --
        # we only need a within-tolerance match, and base candidates are far
        # enough apart (min_gap_ms >= 80 ms) that two base candidates can't
        # share the same perturbed match within a 15 ms tolerance.
        diffs = np.abs(ptimes[None, :] - base[:, None])
        nearest = diffs.min(axis=1)
        agreement += (nearest <= tol_s).astype(np.float64)
    return agreement


def _detect_times(
    audio: np.ndarray,
    sample_rate: int,
    beep_time: float,
    stage_time: float,
    cfg: ShotDetectConfig,
) -> np.ndarray:
    shots = detect_shots(audio, sample_rate, beep_time, stage_time, cfg)
    if not shots:
        return np.zeros(0, dtype=np.float64)
    return np.array([s.time_absolute for s in shots], dtype=np.float64)
