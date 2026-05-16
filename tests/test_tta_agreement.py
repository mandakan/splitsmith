"""Test-time augmentation agreement count (issue #92).

The function under test re-runs ``shot_detect.detect_shots`` under 4 small
perturbations and counts agreement vs. the original detection. We don't have
a real fixture audio sample in scope here -- instead, we synthesise a tiny
audio buffer with an impulsive transient so detect_shots fires once, and
assert the agreement bookkeeping (range, length, dtype) holds.
"""

from __future__ import annotations

import numpy as np

from splitsmith.ensemble.tta import compute_tta_agreement


def _synthesise_shot_audio(sr: int = 48000, duration_s: float = 3.0) -> np.ndarray:
    """A single sharp transient at ~1.5 s with broadband content + decay."""
    rng = np.random.default_rng(42)
    n = int(duration_s * sr)
    audio = rng.standard_normal(n).astype(np.float32) * 1e-4  # mic floor noise
    impulse_idx = int(1.5 * sr)
    decay_n = int(0.05 * sr)
    decay = np.exp(-np.arange(decay_n) / (sr * 0.005))
    burst = (rng.standard_normal(decay_n).astype(np.float32) * decay).astype(np.float32)
    audio[impulse_idx : impulse_idx + decay_n] += burst * 0.5
    return audio


def test_tta_agreement_empty_input_returns_empty() -> None:
    audio = _synthesise_shot_audio()
    out = compute_tta_agreement(
        audio, 48000, beep_time=0.0, stage_time=2.0, base_candidate_times=np.array([])
    )
    assert out.shape == (0,)
    assert out.dtype == np.float64


def test_tta_agreement_in_expected_range_and_length() -> None:
    audio = _synthesise_shot_audio()
    base = np.array([1.5])
    out = compute_tta_agreement(audio, 48000, beep_time=0.0, stage_time=2.0, base_candidate_times=base)
    assert out.shape == (1,)
    # Original always counts (1.0 floor); 4 perturbations cap us at 5.0.
    assert 1.0 <= float(out[0]) <= 5.0


def test_tta_agreement_unmatched_candidate_stays_at_floor() -> None:
    """A candidate with no nearby perturbed match collapses to the 1.0 floor."""
    audio = _synthesise_shot_audio()
    # Pick a time far away from the synthesised impulse so no perturbation
    # reproduces a candidate within the 15 ms tolerance.
    base = np.array([0.5])
    out = compute_tta_agreement(audio, 48000, beep_time=0.0, stage_time=2.0, base_candidate_times=base)
    assert float(out[0]) == 1.0
