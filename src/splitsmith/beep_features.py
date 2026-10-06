"""Per-candidate features for ranking beep candidates (#949).

Spec: docs/superpowers/specs/2026-10-06-beep-learned-ranker-design.md, section 1.
``detect_beep`` calls :func:`candidate_features` for every run it finds and the
trainer reads the result back off the candidates, so training and runtime share
this one implementation.

Pure: arrays and numbers in, a ``BeepFeatures`` out.
"""

from __future__ import annotations

import math

import numpy as np

from .config import BeepFeatures

FEATURE_NAMES: tuple[str, ...] = tuple(BeepFeatures.model_fields)

_EPS = 1e-9
# Spectral bins below this fraction of the band's peak read as this fraction.
_SPECTRUM_FLOOR = 1e-12
# The spectrum is taken over at most this much of the run: a beep is 300-500
# ms, and a long merged run's tail is the other event, not the beep.
SPECTRUM_MAX_S = 0.5


def candidate_features(
    audio: np.ndarray,
    sample_rate: int,
    *,
    run_start: int,
    run_end: int,
    run_peak: float,
    noise_floor: float,
    global_peak: float,
    silence_score: float,
    tonal_ratio: float,
    band_lo_hz: float,
    band_hi_hz: float,
) -> BeepFeatures:
    """The feature vector for the run ``audio[run_start:run_end]``."""
    stop = min(run_end, run_start + int(SPECTRUM_MAX_S * sample_rate))
    flatness, prominence = _band_spectrum_shape(
        np.asarray(audio[run_start:stop], dtype=np.float64), sample_rate, band_lo_hz, band_hi_hz
    )
    return BeepFeatures(
        log_silence=math.log(max(silence_score, _EPS)),
        tonal_ratio=tonal_ratio,
        duration_ms=(run_end - run_start) * 1000.0 / sample_rate,
        log_peak_over_floor=math.log((run_peak + _EPS) / (noise_floor + _EPS)),
        peak_over_global=run_peak / (global_peak + _EPS),
        spectral_flatness=flatness,
        log_spectral_prominence=math.log(prominence),
    )


def feature_vector(features: BeepFeatures) -> list[float]:
    """``features`` as a list in ``FEATURE_NAMES`` order."""
    return [float(getattr(features, name)) for name in FEATURE_NAMES]


def _band_spectrum_shape(
    segment: np.ndarray, sample_rate: int, lo_hz: float, hi_hz: float
) -> tuple[float, float]:
    """(Wiener flatness, peak-over-median prominence) of the power spectrum in
    ``[lo_hz, hi_hz]``. A pure tone is near (0, large); noise near (high, small).
    A segment too short to hold a bin in the band reads neutral, (1.0, 1.0)."""
    if segment.size < 2:
        return 1.0, 1.0
    power = np.abs(np.fft.rfft(segment * np.hanning(segment.size))) ** 2
    freqs = np.fft.rfftfreq(segment.size, 1.0 / sample_rate)
    band = power[(freqs >= lo_hz) & (freqs <= hi_hz)]
    if band.size == 0 or float(band.max()) <= 0.0:
        return 1.0, 1.0
    # A floor relative to the band's own peak, not an absolute epsilon: a pure
    # tone's off-peak bins sit near zero, so an absolute floor would set the
    # median and make both figures depend on recording gain.
    band = band + float(band.max()) * _SPECTRUM_FLOOR
    flatness = float(np.exp(np.mean(np.log(band))) / np.mean(band))
    prominence = float(np.max(band) / np.median(band))
    return flatness, prominence
