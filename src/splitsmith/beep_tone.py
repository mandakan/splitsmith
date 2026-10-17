"""The timer tone's start, measured in its frequency band (spec
2026-10-17-beep-spectral-view-design).

A timer beep is a near-pure tone (2.3 to 3.2 kHz on the labelled corpus); a
cross-bay shot landing on it spreads energy across the spectrum. The full
range envelope rises with the shot, the band-passed one with the tone. Pure:
arrays in, numbers out, no I/O."""

from __future__ import annotations

import base64

import numpy as np
from pydantic import BaseModel
from scipy.signal import butter, sosfiltfilt, stft

from . import rise_foot
from .config import BeepDetectConfig, BeepToneConfig

_CFG = BeepToneConfig()
_COLUMN_S = 0.005
_NPERSEG = 1024
_DB_RANGE = 80.0


class Spectrogram(BaseModel):
    """One window's magnitude, column-major uint8 (0 = -80 dB of the
    window's loudest bin, 255 = that bin), ``len(hz)`` bytes per column."""

    start: float
    duration: float
    hz: list[float]
    columns: int
    data: str


def default_band() -> tuple[float, float]:
    cfg = BeepDetectConfig()
    return (cfg.tonal_band_lo_hz, cfg.tonal_band_hi_hz)


def band_pass(audio: np.ndarray, sr: int, lo_hz: float, hi_hz: float) -> np.ndarray:
    """Zero-phase 4th-order Butterworth band-pass: zero phase, because a
    phase shift would move the rise this exists to find."""
    hi = min(hi_hz, sr / 2 * 0.99)
    sos = butter(4, [lo_hz, hi], btype="band", fs=sr, output="sos")
    return sosfiltfilt(sos, np.asarray(audio, dtype=np.float64)).astype(np.float32)


def _window(audio: np.ndarray, sr: int, start_s: float, end_s: float) -> tuple[np.ndarray, float]:
    lo = min(len(audio), max(0, int(round(start_s * sr))))
    hi = min(len(audio), max(lo, int(round(end_s * sr))))
    return audio[lo:hi], lo / sr


def tone_offset_ms(
    audio: np.ndarray, sr: int, beep_s: float, band: tuple[float, float] | None
) -> float | None:
    """The beep's distance from its tone's rise foot, ms; positive when the
    beep is before the rise. ``band`` None is the full range (the old rule).
    None when there is no rise within Snap's reach."""
    win, start = _window(audio, sr, beep_s - _CFG.window_s, beep_s + _CFG.window_s)
    if win.size == 0:
        return None
    sig = band_pass(win, sr, *band) if band is not None else win
    peaks, duration = rise_foot.peak_envelope(sig, sr)
    foot = rise_foot.rise_foot(peaks, duration, beep_s - start, tolerance_s=_CFG.reach_s)
    if foot is None:
        return None
    return round((foot + start - beep_s) * 1000, 1)


def spectrogram(audio: np.ndarray, sr: int, start_s: float, end_s: float) -> Spectrogram:
    win, start = _window(audio, sr, start_s, min(end_s, start_s + _CFG.max_window_s))
    hop = max(1, int(round(_COLUMN_S * sr)))
    if win.size < _NPERSEG:
        win = np.pad(win, (0, _NPERSEG - win.size))
    freqs, _times, z = stft(
        win, fs=sr, nperseg=_NPERSEG, noverlap=_NPERSEG - hop, boundary="even", padded=False
    )
    keep = freqs <= _CFG.max_hz
    mag = np.abs(z[keep])
    ref = float(mag.max()) or 1.0
    db = 20 * np.log10(np.maximum(mag, ref * 10 ** (-_DB_RANGE / 20)) / ref)
    grid = np.round((db + _DB_RANGE) / _DB_RANGE * 255).astype(np.uint8)
    cols = grid.T.copy()  # column-major: one row per time column
    return Spectrogram(
        start=start,
        duration=len(win) / sr if win.size else 0.0,
        hz=[float(f) for f in freqs[keep]],
        columns=int(cols.shape[0]),
        data=base64.b64encode(cols.tobytes()).decode("ascii"),
    )
