"""Cross-bay spectral features for voter C (issue #108).

Spectral flatness and the 1-4 kHz / (low + high) energy ratio target the
acoustic differences between close shots and shots that arrive through
air or terrain from neighbouring bays. The echo-pairing features that
issue #108 also proposed (delta-t / amplitude ratio to the nearest
louder predecessor) were tried and dropped after the per-feature
ablation showed they were either dead (importance ~0) or actively
harmful: see PR #109's ablation table and follow-up #90 (sequence
voter) for a temporal-cadence approach that should fit those signals
better.
"""

from __future__ import annotations

import numpy as np

from splitsmith.ensemble.features import (
    HAND_FEATURE_DIM,
    _spectral_flatness_and_peak_ratio,
    compute_hand_features,
)


def test_spectral_flatness_distinguishes_tone_from_white_noise() -> None:
    """A pure tone should have very low flatness; white noise close to 1."""
    sr = 48000
    n = int(0.05 * sr)
    t = np.arange(n) / sr
    tone = np.sin(2 * np.pi * 2000 * t)
    rng = np.random.default_rng(0)
    noise = rng.standard_normal(n)

    flat_tone, _ = _spectral_flatness_and_peak_ratio(tone, sr)
    flat_noise, _ = _spectral_flatness_and_peak_ratio(noise, sr)

    assert flat_tone < 0.05
    assert flat_noise > 0.3
    assert flat_noise > flat_tone * 5


def test_spectral_peak_ratio_is_higher_for_band_centered_signal() -> None:
    """Band-limited 1-4 kHz signal should outscore broadband noise."""
    sr = 48000
    n = int(0.05 * sr)
    t = np.arange(n) / sr
    band = np.sin(2 * np.pi * 2500 * t)
    rng = np.random.default_rng(1)
    broadband = rng.standard_normal(n)

    _, ratio_band = _spectral_flatness_and_peak_ratio(band, sr)
    _, ratio_broad = _spectral_flatness_and_peak_ratio(broadband, sr)

    assert ratio_band > ratio_broad


def test_spectral_features_handle_short_segments_gracefully() -> None:
    """Segments below the FFT floor return zeros, never NaN/exceptions."""
    flatness, peak_ratio = _spectral_flatness_and_peak_ratio(np.zeros(8), 48000)
    assert flatness == 0.0
    assert peak_ratio == 0.0


def test_compute_hand_features_emits_expected_dim_and_finite_values() -> None:
    """End-to-end: shape matches the bumped HAND_FEATURE_DIM and the new
    spectral columns are populated and finite."""
    sr = 48000
    audio = np.zeros(int(2.0 * sr), dtype=np.float64)
    # Plant an impulse at 1.0 s so the feature loop has something to lock
    # onto for the local-peak window.
    audio[int(1.0 * sr)] = 0.6
    times = np.array([1.0, 1.2])
    amps = np.array([0.6, 0.15])
    confs = np.array([0.9, 0.4])
    tta = np.array([3.0, 1.0])
    out = compute_hand_features(
        audio,
        sr,
        times,
        beep_time=0.0,
        confidences=confs,
        peak_amplitudes=amps,
        tta_agreement=tta,
    )
    assert out.shape == (2, HAND_FEATURE_DIM)
    assert np.all(np.isfinite(out))
    # TTA is now the second-to-last column (peak_amp_within_stage_ratio
    # is appended last in #304's follow-up); the two spectral columns sit
    # just before TTA.
    assert out[0, -2] == 3.0
    assert out[1, -2] == 1.0
    assert out[:, -4:-2].shape == (2, 2)
