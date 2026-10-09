"""Stage-relative voter C features (spec 2026-10-09)."""

import json
from pathlib import Path

import numpy as np
import pytest
from scipy.signal import butter, sosfiltfilt

from splitsmith.beep_detect import load_audio
from splitsmith.config import ShotDetectConfig
from splitsmith.ensemble import features as feat
from splitsmith.ensemble.tta import compute_tta_agreement
from splitsmith.shot_detect import detect_shots

FIXTURES = Path(__file__).parent / "fixtures"
GO3S_FIXTURE = "stage-shots-hostfinalen-xi-2026-stage7-s97dcec94"


def _stage(name: str, gain: float = 1.0, lowpass_hz: float | None = None):
    audio, sr = load_audio(FIXTURES / f"{name}.wav")
    truth = json.loads((FIXTURES / f"{name}.json").read_text())
    shots = detect_shots(
        audio,
        sr,
        truth["beep_time"],
        truth["stage_time_seconds"],
        ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0),
    )
    times = np.array([s.time_absolute for s in shots])
    conf = np.array([s.confidence for s in shots])
    peaks = np.array([s.peak_amplitude for s in shots])
    tta = compute_tta_agreement(audio, sr, truth["beep_time"], truth["stage_time_seconds"], times)
    x = audio.astype(np.float64)
    if lowpass_hz is not None:
        x = sosfiltfilt(butter(4, lowpass_hz, btype="low", fs=sr, output="sos"), x)
    x = x * gain
    hand = feat.compute_hand_features(x, sr, times, truth["beep_time"], conf, peaks * gain, tta)
    return hand, truth, times


def test_hand_features_carry_centroid_and_high_band():
    assert feat.HAND_FEATURE_DIM == 19
    assert feat._HAND_FEATURE_NAMES[17:] == ("spectral_centroid_hz", "high_band_db")


def test_lowpass_lowers_centroid_and_high_band():
    hand, _, _ = _stage(GO3S_FIXTURE)
    dull, _, _ = _stage(GO3S_FIXTURE, lowpass_hz=1500.0)
    assert np.nanmedian(dull[:, 17]) < np.nanmedian(hand[:, 17]) - 100.0
    assert np.nanmedian(dull[:, 18]) < np.nanmedian(hand[:, 18]) - 3.0


def test_centroid_and_high_band_ignore_gain():
    hand, _, _ = _stage(GO3S_FIXTURE)
    quiet, _, _ = _stage(GO3S_FIXTURE, gain=0.25)
    assert quiet.shape[1] == hand.shape[1] == 19  # an empty slice would pass vacuously
    assert np.isfinite(hand[:, 17]).sum() > 10
    np.testing.assert_allclose(quiet[:, 17:19], hand[:, 17:19], rtol=1e-6, atol=1e-6)


def test_window_past_clip_end_is_nan():
    sr = 48000
    audio = np.random.default_rng(0).normal(0, 0.01, sr)
    t = np.array([len(audio) / sr - 0.01])
    hand = feat.compute_hand_features(audio, sr, t, 0.0, np.array([0.5]), np.array([0.1]), np.array([5.0]))
    assert np.isnan(hand[0, 17]) and np.isnan(hand[0, 18])


def test_reference_uses_round_count_then_thirty_percent():
    conf = np.array([0.1, 0.9, 0.5, 0.7, 0.3, 0.8, 0.2, 0.6, 0.4, 0.05])
    assert sorted(feat.reference_indices(conf, 3).tolist()) == [1, 3, 5]
    assert sorted(feat.reference_indices(conf, None).tolist()) == [1, 3, 5]  # max(3, round(3.0))
    assert len(feat.reference_indices(conf, 50)) == 10
    assert len(feat.reference_indices(conf, 0)) == 1
    assert len(feat.reference_indices(conf, -4)) == 1
    assert len(feat.reference_indices(np.array([]), None)) == 0


def test_reference_is_stable_under_ties():
    conf = np.full(9, 0.5)
    assert feat.reference_indices(conf, 4).tolist() == feat.reference_indices(conf, 4).tolist() == [0, 1, 2, 3]


def _rel(hand, n_clap=10, expected=None):
    n = hand.shape[0]
    sims = np.zeros((n, n_clap))
    return feat.stage_relative_features(hand, sims, np.zeros(n), np.zeros(n), expected)


def test_tiny_stages_do_not_raise():
    for n in (0, 1, 2):
        hand = np.abs(np.random.default_rng(n).normal(0.5, 0.1, (n, feat.HAND_FEATURE_DIM)))
        rel = _rel(hand)
        assert rel.shape == (n, feat.REL_FEATURE_DIM)
        assert not np.isnan(rel).any()


def test_nan_source_is_zero_and_excluded_from_median():
    hand = np.ones((4, feat.HAND_FEATURE_DIM))
    hand[:, 1] = [0.9, 0.8, 0.7, 0.1]  # confidence: the first three are the reference
    hand[:, 17] = [1000.0, 1200.0, np.nan, 500.0]
    rel = _rel(hand, expected=3)
    col = feat.REL_FEATURE_NAMES.index("rel_spectral_centroid_hz")
    assert rel[2, col] == 0.0
    assert rel[3, col] == pytest.approx(500.0 - 1100.0)


def test_relative_level_ignores_gain_on_real_audio():
    hand, truth, _ = _stage(GO3S_FIXTURE)
    quiet, _, _ = _stage(GO3S_FIXTURE, gain=0.25)
    exp = (truth.get("stage_rounds") or {}).get("expected")
    a, b = _rel(hand, expected=exp), _rel(quiet, expected=exp)
    for name in ("rel_peak_amp", "rel_rms_post", "rel_tail_amp"):
        c = feat.REL_FEATURE_NAMES.index(name)
        assert np.abs(a[:, c]).max() > 0.1  # the column carries signal
        np.testing.assert_allclose(b[:, c], a[:, c], atol=1e-6)


def test_relative_centroid_absorbs_a_whole_stage_timbre_shift():
    hand, truth, times = _stage(GO3S_FIXTURE)
    dull, _, _ = _stage(GO3S_FIXTURE, lowpass_hz=1500.0)
    exp = (truth.get("stage_rounds") or {}).get("expected")
    shot_times = np.array([float(s["time"]) for s in truth["shots"]])
    shot_mask = np.array([np.min(np.abs(float(t) - shot_times)) < 0.075 for t in times])
    c = feat.REL_FEATURE_NAMES.index("rel_spectral_centroid_hz")
    abs_shift = abs(np.nanmedian(dull[shot_mask, 17]) - np.nanmedian(hand[shot_mask, 17]))
    rel_shift = abs(
        np.median(_rel(dull, expected=exp)[shot_mask, c]) - np.median(_rel(hand, expected=exp)[shot_mask, c])
    )
    assert abs_shift > 100.0
    # Most of the shift is absorbed, not all: the low-pass takes more from
    # treble-rich shots than from the non-shot candidates in the reference.
    assert rel_shift < 0.5 * abs_shift
