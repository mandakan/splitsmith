"""Per-candidate beep features (#949, spec 2026-10-06 section 1)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from splitsmith.beep_calibration import load_manifest
from splitsmith.beep_detect import _candidate_runs, detect_beep, load_audio
from splitsmith.beep_features import FEATURE_NAMES, candidate_features, feature_vector
from splitsmith.config import BeepCandidate, BeepDetectConfig, BeepFeatures

SR = 48_000
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _features(audio: np.ndarray, *, gain: float = 1.0) -> BeepFeatures:
    peak = float(np.abs(audio).max())
    return candidate_features(
        audio * gain,
        SR,
        run_start=0,
        run_end=audio.size,
        run_peak=peak * gain,
        noise_floor=0.01 * gain,
        global_peak=2.0 * peak * gain,
        silence_score=8.0,
        tonal_ratio=0.9,
        band_lo_hz=2000,
        band_hi_hz=5000,
    )


def _tone(seconds: float = 0.4, hz: float = 3000.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (0.5 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def _noise(seconds: float = 0.4) -> np.ndarray:
    return np.random.default_rng(0).normal(0.0, 0.2, int(seconds * SR)).astype(np.float32)


def test_a_tone_is_not_flat_and_is_prominent() -> None:
    tone, noise = _features(_tone()), _features(_noise())
    assert tone.spectral_flatness < 0.05 < 0.3 < noise.spectral_flatness
    assert tone.log_spectral_prominence > noise.log_spectral_prominence + 3.0


def test_features_do_not_move_with_recording_gain() -> None:
    quiet, loud = _features(_tone()), _features(_tone(), gain=10.0)
    for name in FEATURE_NAMES:
        assert getattr(loud, name) == pytest.approx(getattr(quiet, name), rel=1e-4, abs=1e-6), name


def test_the_ratios_are_what_their_names_say() -> None:
    f = _features(_tone())
    assert f.log_silence == pytest.approx(math.log(8.0))
    assert f.tonal_ratio == pytest.approx(0.9)
    assert f.duration_ms == pytest.approx(400.0)
    assert f.log_peak_over_floor == pytest.approx(math.log(0.5 / 0.01), rel=1e-3)
    assert f.peak_over_global == pytest.approx(0.5, rel=1e-6)


def test_a_run_too_short_for_a_spectrum_reads_neutral() -> None:
    f = candidate_features(
        np.zeros(1, dtype=np.float32),
        SR,
        run_start=0,
        run_end=1,
        run_peak=0.0,
        noise_floor=0.0,
        global_peak=0.0,
        silence_score=0.0,
        tonal_ratio=0.0,
        band_lo_hz=2000,
        band_hi_hz=5000,
    )
    assert (f.spectral_flatness, f.log_spectral_prominence) == (1.0, 0.0)
    assert all(math.isfinite(v) for v in feature_vector(f))


def test_the_vector_follows_the_model_field_order() -> None:
    f = _features(_tone())
    assert FEATURE_NAMES == tuple(BeepFeatures.model_fields)
    assert feature_vector(f) == [getattr(f, n) for n in FEATURE_NAMES]


def test_a_stored_candidate_without_features_still_loads() -> None:
    old = {"time": 1.0, "score": 0.5, "peak_amplitude": 0.2, "duration_ms": 300.0}
    assert BeepCandidate.model_validate(old).features is None


def test_detect_beep_attaches_the_features_candidate_features_computes() -> None:
    """No skew: what the trainer reads off a candidate is exactly what a
    direct call on the same run returns. ``detect_beep`` ranks runs by
    score (a stable sort), so its candidates line up with the runs sorted
    the same way."""
    entry = load_manifest(FIXTURES / "beep_calibration" / "manifest.yaml").fixtures[1]
    audio, sr = load_audio(FIXTURES / entry.clip_wav)
    config = BeepDetectConfig(top_n_candidates=1000)

    detection = detect_beep(audio, sr, config)
    runs = sorted(_candidate_runs(audio, sr, config), key=lambda r: r.score, reverse=True)

    assert len(detection.candidates) == len(runs) > 1
    for candidate, run in zip(detection.candidates, runs, strict=True):
        assert candidate.features == candidate_features(
            run.audio,
            sr,
            run_start=run.start,
            run_end=run.end,
            run_peak=run.run_peak,
            noise_floor=run.noise_floor,
            global_peak=run.global_peak,
            silence_score=run.silence_score,
            tonal_ratio=run.tonal_ratio,
            band_lo_hz=config.freq_min_hz,
            band_hi_hz=config.freq_max_hz,
        )
