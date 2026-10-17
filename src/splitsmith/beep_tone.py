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
# The tone check: how long a rise must stay up, how much of that time, and
# its level against the loudest of the reach window.
_SUSTAIN_S = 0.1
_SUSTAIN_SHARE = 0.8
_LEVEL_FRAC = 0.15
# The timer's own band: half its width, how far a line stands over its
# neighbours, and the share of the window it must stand out in.
_TONE_HALF_HZ = 200.0
_LINE_PROMINENCE = 4.0
_LINE_MIN_SHARE = 0.15


class Spectrogram(BaseModel):
    """One window's magnitude, column-major uint8 (0 = -80 dB of the
    window's loudest bin, 255 = that bin), ``len(hz)`` bytes per column."""

    start: float
    duration: float
    hz: list[float]
    columns: int
    data: str


def beep_tone_config() -> BeepToneConfig:
    """The tone check's settings: from ``SPLITSMITH_CONFIG`` (a YAML
    ``config.Config`` document) when set, else the defaults, the way
    ``coach.auto_classify_config`` reads its own."""
    import os
    from pathlib import Path

    from .config import Config
    from .runtime import ENV_CONFIG_FILE

    raw = os.environ.get(ENV_CONFIG_FILE, "").strip()
    return Config.load(Path(raw).expanduser()).beep_tone if raw else BeepToneConfig()


def default_band() -> tuple[float, float]:
    cfg = BeepDetectConfig()
    return (cfg.tonal_band_lo_hz, cfg.tonal_band_hi_hz)


def tone_band(audio: np.ndarray, sr: int, beep_s: float) -> tuple[float, float]:
    """+-``_TONE_HALF_HZ`` around the timer's own frequency: the steadiest
    tonal line within 1.5 to 7 kHz from 0.3 s before the beep to 0.5 s after
    (the bin that stands ``_LINE_PROMINENCE``x over its +-300 Hz neighbours in
    the most columns). The corpus's timers run from 1.6 to 5.4 kHz, so a fixed
    band misses some, and a narrow one keeps most of a shot out. The
    detector's band when no line stands out (silence, a timer drowned out).
    On the 2026-10-17 corpus pass it agreed with the audited beep within 6 ms
    on 57 of 66 primaries."""
    lo = max(0, int(round((beep_s - 0.3) * sr)))
    hi = min(len(audio), int(round((beep_s + 0.5) * sr)))
    if hi - lo < 4096:
        return default_band()
    f, _t, z = stft(audio[lo:hi], fs=sr, nperseg=4096, noverlap=4096 - int(0.01 * sr))
    keep = (f >= 1500) & (f <= 7000)
    m, f = np.abs(z[keep]), f[keep]
    if m.size == 0 or m.max() <= 0:
        return default_band()
    k = max(1, int(round(300 / (f[1] - f[0]))))
    pad = np.pad(m, ((k, k), (0, 0)), mode="edge")
    med = np.stack([np.median(pad[i : i + 2 * k + 1], axis=0) for i in range(m.shape[0])])
    score = (m / (med + 1e-12) > _LINE_PROMINENCE).mean(axis=1)
    i = int(score.argmax())
    if score[i] < _LINE_MIN_SHARE:
        return default_band()
    hz = float(f[i])
    return (hz - _TONE_HALF_HZ, hz + _TONE_HALF_HZ)


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
    """The beep's distance from its tone's start, ms; positive when the beep
    is before the tone. ``band`` None is the full range.

    The start is the earliest moment within ``reach_s`` of the beep where
    the level rises above the floor and stays up for most of the next
    ``_SUSTAIN_S``: a timer tone lasts 300 to 500 ms, a cross-bay shot's
    energy dies in tens. Not Snap's rule (the nearest peak, walked back),
    which lands mid-tone on a tone that wavers and peaks late (Little
    Trouble 1, Bofors 5, 2026-10-17). None when nothing within reach stays up."""
    win, start = _window(audio, sr, beep_s - _CFG.window_s, beep_s + _CFG.window_s)
    if win.size == 0:
        return None
    sig = band_pass(win, sr, *band) if band is not None else win
    peaks, duration = rise_foot.peak_envelope(sig, sr)
    p = np.asarray(peaks, dtype=np.float64)
    if p.size == 0 or duration <= 0:
        return None
    bin_s = duration / p.size
    beep = beep_s - start

    def at(t: float) -> int:
        return min(p.size, max(0, int(round(t / bin_s))))

    pre = p[: at(beep - _CFG.reach_s - 0.05)]
    floor = float(np.percentile(pre, 20)) if pre.size else 0.0
    ref_win = p[at(beep - _CFG.reach_s) : at(beep + _CFG.reach_s + _SUSTAIN_S)]
    if ref_win.size == 0:
        return None
    ref = float(np.percentile(ref_win, 90))
    threshold = max(4 * floor, _LEVEL_FRAC * ref)
    if threshold <= 0:
        return None
    above = p >= threshold
    n = max(1, at(_SUSTAIN_S))
    # Share of each bin's next n bins that are above, by a running sum.
    csum = np.concatenate([[0], np.cumsum(above)])
    for i in range(at(beep - _CFG.reach_s), at(beep + _CFG.reach_s)):
        if i + n > p.size:
            break
        if above[i] and (csum[i + n] - csum[i]) >= _SUSTAIN_SHARE * n:
            # Back to the foot of the rise, a few bins at most.
            j = i
            while (
                j > 0 and i - j < at(0.01) and p[j - 1] >= max(2 * floor, 0.5 * threshold) and p[j - 1] < p[j]
            ):
                j -= 1
            return round((j * bin_s - beep) * 1000, 1)
    return None


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
