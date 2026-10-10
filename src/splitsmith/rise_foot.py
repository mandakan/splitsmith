"""A shot's time: the rise foot (definition in ``docs/METHODOLOGY.md``).

The time of a shot is the foot of the rise that leads to the shot's own
peak: walking back from that peak, the last moment the level is still above
both 5 % of the peak and 1.5 x the noise floor before it. An earlier sound
separated from the burst by a dip (an echo, the previous shot, a lead-in
that falls back before the blast) is not part of the rise; a lead-in that
ramps continuously into the burst is.

One rule, held identical by ``tests/fixtures/rise_foot/cases.json``: the
review inventory's suggestions (``lab.inventory``) and the app's drop snap
(``ui_static/src/lib/peak-snap.ts``, ``snapToLeadingEdge``); the shot
detector (``shot_detect``) moves to it in a follow-up.

Operates on a peak envelope: the maximum ``|audio|`` per bin, normalized to
the clip's loudest sample (``waveform.compute_peaks``), at 1 ms bins, the
resolution shot times are stored at.
"""

from __future__ import annotations

import numpy as np

#: Search window around the time given, seconds: cursor or detector slop
#: without reaching the neighbouring shot on a fast string (splits 150-400 ms).
TOLERANCE_S = 0.025
#: Below this normalized level the window is silence: there is no shot to find.
MIN_PEAK = 0.05
#: The foot: this fraction of the shot's own peak ...
RISE_FOOT_FRAC = 0.05
#: ... or this multiple of the noise floor, whichever is higher.
NOISE_FLOOR_FACTOR = 1.5
#: A dip below this fraction of the peak, with the level rising again behind
#: it, separates an earlier sound from this shot's rise.
VALLEY_FRAC = 0.25
MAX_WALK_S = 0.1
NOISE_WINDOW_S = 0.1
BIN_S = 0.001


def peak_envelope(audio: np.ndarray, sample_rate: int, bin_s: float = BIN_S) -> tuple[list[float], float]:
    """Normalized max-``|audio|`` per ``bin_s`` bin, and the clip's duration."""
    from .waveform import compute_peaks

    duration = len(audio) / sample_rate
    bins = max(1, int(np.ceil(duration / bin_s)))
    return compute_peaks(np.asarray(audio, dtype=np.float32), sample_rate, bins).peaks, duration


def rise_foot(peaks: list[float] | np.ndarray, duration: float, time: float) -> float | None:
    """The rise foot of the shot nearest ``time`` (start of its first bin);
    ``None`` when the window holds no shot (silence, or only the slope of a
    farther sound)."""
    p = np.asarray(peaks, dtype=np.float64)
    n = p.size
    if n == 0 or duration <= 0 or not np.isfinite(time):
        return None
    bin_w = duration / n
    center = min(n - 1, max(0, int(np.floor(time / bin_w))))
    radius = max(1, round(TOLERANCE_S / bin_w))
    lo, hi = max(0, center - radius), min(n - 1, center + radius)
    max_idx = lo + int(np.argmax(p[lo : hi + 1]))
    peak = p[max_idx]
    if peak < MIN_PEAK:
        return None
    if max_idx == hi and hi < n - 1 and p[hi + 1] > p[hi]:
        return None
    noise = p[max(0, lo - round(NOISE_WINDOW_S / bin_w)) : lo]
    floor = float(np.sort(noise)[noise.size // 2]) if noise.size else 0.0
    threshold = max(RISE_FOOT_FRAC * peak, NOISE_FLOOR_FACTOR * floor)
    max_walk = round(MAX_WALK_S / bin_w)
    i = max_idx
    while i > 0 and max_idx - i < max_walk:
        cur, prev = p[i], p[i - 1]
        if prev < threshold:
            break
        if cur < VALLEY_FRAC * peak and prev > cur:
            break
        i -= 1
    return i * bin_w
