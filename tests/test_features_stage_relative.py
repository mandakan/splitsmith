"""Stage-relative voter C features (spec 2026-10-09)."""

import json
from pathlib import Path

import numpy as np
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
