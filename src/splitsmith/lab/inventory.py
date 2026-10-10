"""Which fixtures to review first, and why (#1363).

A fixture's shot times are the corpus's ground truth. Two things say a
time may be off: it was snapped from another angle and never checked on
this fixture's own audio (``review_status``), and the shot's leading edge
is ambiguous in this audio. The second is measured by comparing each
stored time (the detector's rise foot, 5 % of the local peak) with a
stricter onset (20 %): on a sharp onset the two move together, so the
spread of their difference around the fixture's own offset is near zero;
in noise or reverb they come apart. ``scripts/fixture_review_inventory.py``
writes the scores the lab's review queue sorts by.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.signal import butter, sosfiltfilt

from ..fixture_schema import REVIEW_NEEDED
from ..rise_foot import peak_envelope, rise_foot

_HOP_S = 0.00025  # envelope resolution
_WINDOW_S = 0.040  # around the stored time
_NOISE_S = 0.200  # before the window, for the floor
_ONSET_FRAC = 0.20
_MIN_SHOTS = 3
# Spread at or above this many ms counts against a fixture already marked reviewed.
_REVIEWED_SPREAD_MS = 5.0


def _envelope(audio: np.ndarray, sr: int) -> tuple[np.ndarray, float]:
    x = sosfiltfilt(butter(4, 300, btype="high", fs=sr, output="sos"), np.asarray(audio, dtype=np.float64))
    hop = max(1, int(round(_HOP_S * sr)))
    n = len(x) // hop
    return np.abs(x[: n * hop]).reshape(n, hop).max(axis=1), sr / hop


def _onset(env: np.ndarray, rate: float, t: float) -> float | None:
    i = int(t * rate)
    half = int(_WINDOW_S * rate)
    lo, hi = max(0, i - half), min(len(env), i + half)
    if hi - lo < 10:
        return None
    seg = env[lo:hi]
    before = env[max(0, lo - int(_NOISE_S * rate)) : lo]
    floor = float(np.median(before)) if before.size else float(np.median(seg))
    pk = int(np.argmax(seg))
    if seg[pk] < 4 * max(floor, 1e-6):
        return None  # nothing stands out: no onset to measure
    thr = floor + _ONSET_FRAC * (seg[pk] - floor)
    j = pk
    while j > 0 and seg[j - 1] >= thr:
        j -= 1
    return (lo + j) / rate


def onset_spread_ms(audio: np.ndarray, sr: int, shot_times: list[float]) -> float | None:
    """Median absolute deviation, in ms, of (stored time - 20 % onset) around its median.

    ``None`` when fewer than three shots have a measurable onset.
    """
    env, rate = _envelope(audio, sr)
    deltas = []
    for t in shot_times:
        onset = _onset(env, rate, float(t))
        if onset is not None:
            deltas.append((float(t) - onset) * 1000.0)
    if len(deltas) < _MIN_SHOTS:
        return None
    d = np.asarray(deltas)
    return float(np.median(np.abs(d - np.median(d))))


#: A suggestion only when the edge is further than this from the stored time.
MIN_MOVE_MS = 5.0


def suggested_moves(audio: np.ndarray, sr: int, shot_times: list[float]) -> list[dict[str, Any]]:
    """Shots whose rise foot (``splitsmith.rise_foot``, the shot-time
    definition) sits more than ``MIN_MOVE_MS`` from the stored time.

    Each entry: ``shot_index`` (into ``shot_times``), ``time``, ``suggested``
    and ``move_ms`` (suggested minus stored). For the review queue to point
    at, never written into a fixture.
    """
    peaks, duration = peak_envelope(audio, sr)
    out = []
    for idx, t in enumerate(shot_times):
        edge = rise_foot(peaks, duration, float(t))
        if edge is None:
            continue
        move_ms = (edge - float(t)) * 1000.0
        if abs(move_ms) > MIN_MOVE_MS:
            out.append(
                {
                    "shot_index": idx,
                    "time": round(float(t), 4),
                    "suggested": round(edge, 4),
                    "move_ms": round(move_ms, 1),
                }
            )
    return out


def review_priority(entry: dict[str, Any]) -> tuple[float, list[str]]:
    """Score (higher = review sooner) and the reasons, for one inventory entry.

    ``entry`` carries ``review_status``, ``derived`` (snapped from another
    angle), ``edge_fraction`` (share of snapped shots at the snap window's
    edge, from the promotion report) and ``onset_spread_ms``.
    """
    score = 0.0
    reasons: list[str] = []
    needs = entry.get("review_status") == REVIEW_NEEDED
    spread = entry.get("onset_spread_ms")
    if needs:
        score += 50.0
        if entry.get("derived"):
            score += 50.0
            reasons.append("snapped from another angle, never checked on this audio")
        else:
            reasons.append("marked as needing review")
        edge = entry.get("edge_fraction")
        if edge:
            score += 100.0 * float(edge)
            reasons.append(f"{round(100 * float(edge))} % of snapped shots at the window edge")
    if spread is not None and (spread >= _REVIEWED_SPREAD_MS or (needs and spread >= 2.0)):
        score += 2.0 * float(spread)
        reasons.append(f"leading edges disagree by {round(float(spread))} ms shot to shot")
    moves = int(entry.get("suggested_moves") or 0)
    n_shots = int(entry.get("n_shots") or 0)
    if moves and n_shots:
        score += 50.0 * moves / n_shots
        limit = round(MIN_MOVE_MS)
        reasons.append(f"{moves} of {n_shots} shots: the leading-edge snap disagrees by more than {limit} ms")
    return score, reasons
