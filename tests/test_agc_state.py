"""Unit tests for the AGC-state estimator (issue #88).

Synthesised signals only -- a real fixture is exercised through
``test_ensemble.py`` once the calibration build picks up the new feature.
"""

from __future__ import annotations

import numpy as np

from splitsmith.ensemble.agc_state import (
    AGCConfig,
    compute_agc_features,
    detect_loud_events,
)


def _impulse(sr: int, n: int, indices: list[int], amp: float = 0.9) -> np.ndarray:
    """Audio with brief (~5 ms) loud bursts at each index."""
    audio = np.full(n, 0.001, dtype=np.float32)
    burst_n = max(1, int(0.005 * sr))
    for idx in indices:
        lo, hi = idx, min(n, idx + burst_n)
        audio[lo:hi] = amp
    return audio


def test_compute_agc_features_returns_zero_for_empty_universe() -> None:
    audio = np.zeros(48_000, dtype=np.float32)
    out = compute_agc_features(audio, 48_000, np.array([], dtype=np.float64), np.array([]))
    assert out.agc_state.shape == (0,)
    assert out.time_since_last_loud_event.shape == (0,)
    assert out.peak_floor_ratio.shape == (0,)


def test_detect_loud_events_thresholds_on_candidate_peaks() -> None:
    """Top fraction of candidate peaks register as loud events; quiet
    candidates fall below threshold."""
    cand = np.array([0.5, 1.0, 1.5, 2.0, 2.5, 3.0], dtype=np.float64)
    peaks = np.array([0.8, 0.05, 0.7, 0.05, 0.65, 0.05], dtype=np.float64)
    cfg = AGCConfig(loud_event_peak_quantile=0.5, loud_event_min_separation_s=0.1)
    events = detect_loud_events(cand, peaks, cfg)
    assert events.size == 3
    assert events.tolist() == [0.5, 1.5, 2.5]


def test_agc_state_decays_with_time_since_loud_event() -> None:
    """Right after a loud event agc_state ~ 1; after 1.5 s (tau) ~ 1/e."""
    sr = 48_000
    n = sr * 6
    audio = _impulse(sr, n, [int(1.0 * sr)])
    # Candidate at 1.0 with a loud peak triggers the loud event; the rest
    # (1.1, 1.5, 2.5, 4.5) have small peaks and look back at it.
    candidate_times = np.array([1.0, 1.1, 1.5, 2.5, 4.5], dtype=np.float64)
    peaks = np.array([0.9, 0.05, 0.05, 0.05, 0.05], dtype=np.float64)
    cfg = AGCConfig(recovery_tau_s=1.5, lookback_s=5.0)
    out = compute_agc_features(audio, sr, candidate_times, peaks, cfg)
    # At ~0.1 s after the event agc_state is high and time_since is small.
    assert out.agc_state[1] > 0.9
    assert out.time_since_last_loud_event[1] < 0.15
    # Monotone decay across the candidates that follow the event.
    assert out.agc_state[1] > out.agc_state[2] > out.agc_state[3] > out.agc_state[4]
    assert out.time_since_last_loud_event[4] > out.time_since_last_loud_event[1]


def test_agc_state_zero_when_no_loud_event_in_lookback() -> None:
    """A candidate before any loud event sees agc_state=0 and time_since=lookback_s."""
    sr = 48_000
    n = sr * 6
    audio = _impulse(sr, n, [int(5.0 * sr)])
    candidate_times = np.array([0.5, 5.0], dtype=np.float64)
    peaks = np.array([0.05, 0.9], dtype=np.float64)
    cfg = AGCConfig(lookback_s=5.0)
    out = compute_agc_features(audio, sr, candidate_times, peaks, cfg)
    assert out.agc_state[0] == 0.0
    assert out.time_since_last_loud_event[0] == cfg.lookback_s


def test_self_exclude_picks_previous_event_not_candidates_own_peak() -> None:
    """A loud candidate should look back at the *prior* loud event, not
    register as having time_since=0 against itself."""
    sr = 48_000
    n = sr * 5
    burst_idxs = [int(1.0 * sr), int(3.0 * sr)]
    audio = _impulse(sr, n, burst_idxs)
    candidate_times = np.array([1.0, 3.0], dtype=np.float64)
    peaks = np.array([0.9, 0.9], dtype=np.float64)
    cfg = AGCConfig()
    out = compute_agc_features(audio, sr, candidate_times, peaks, cfg)
    # The previous loud event was at 1.0 s; self-exclude (50 ms) should
    # discard the candidate at 3.0 from being its own predecessor.
    assert abs(out.time_since_last_loud_event[1] - 2.0) < 0.05


def test_peak_floor_ratio_tracks_local_quiet() -> None:
    """A candidate over a quiet stretch (low local floor) gives a higher
    peak_floor_ratio than the same peak over a noisy stretch."""
    sr = 48_000
    n = sr * 6

    quiet = np.full(n, 0.001, dtype=np.float32)
    noisy = np.full(n, 0.05, dtype=np.float32)
    candidate_times = np.array([3.0], dtype=np.float64)
    peaks = np.array([0.5], dtype=np.float64)
    # Use a flat config so the per-stage peak quantile doesn't fire on the
    # single test candidate (we only care about the floor here).
    cfg = AGCConfig()

    out_quiet = compute_agc_features(quiet, sr, candidate_times, peaks, cfg)
    out_noisy = compute_agc_features(noisy, sr, candidate_times, peaks, cfg)
    assert out_quiet.peak_floor_ratio[0] > out_noisy.peak_floor_ratio[0]


def test_features_added_to_hand_feature_dim() -> None:
    """The ensemble's hand-feature vector now includes the three AGC features."""
    from splitsmith.ensemble.features import HAND_FEATURE_DIM, _HAND_FEATURE_NAMES

    assert "agc_state" in _HAND_FEATURE_NAMES
    assert "time_since_last_loud_event" in _HAND_FEATURE_NAMES
    assert "peak_floor_ratio" in _HAND_FEATURE_NAMES
    assert HAND_FEATURE_DIM == len(_HAND_FEATURE_NAMES)
