"""Rolling AGC-state estimator (issue #88).

Cross-bay shots and AGC-ducked local shots are acoustically near-identical
in a single candidate window (peak, attack, tail all match): AGC has
compressed the local shot's amplitude after a previous loud burst. The
information that splits the classes lives in the *recent* audio history --
whether a loud event happened recently enough that AGC is still ducked.

This module exposes three per-candidate features computed from the
fixture-wide audio:

* ``agc_state`` (0-1): how compressed the recording is at candidate
  time. ``exp(-dt / recovery_tau_s)`` where ``dt`` is seconds since the
  most recent loud envelope peak. 1.0 = a loud event just happened;
  0.0 = no loud event in the last ``lookback_s`` seconds.
* ``time_since_last_loud_event``: seconds since the most recent loud
  envelope peak, capped at ``lookback_s``.
* ``peak_floor_ratio``: candidate's peak amplitude divided by the
  local noise floor (low percentile of ``|audio|`` in a pre-window
  ending just before the candidate). Cross-bay shots have small peak
  relative to a normal floor (low ratio); AGC-ducked local shots have
  small peak relative to a suppressed floor (high ratio).

The estimator is parameterised because AGC dynamics depend on the camera.
Defaults are tuned for the Insta360 GO 3S; per-camera calibration is left
for the #18 follow-up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

import numpy as np


@dataclass(frozen=True)
class AGCConfig:
    """Tunables for the rolling AGC estimator."""

    loud_event_peak_quantile: float = 0.60
    """Quantile of candidate peak_amplitudes used as the loud-event threshold.
    Default 0.60 = the upper 40 % of detected peaks count as "loud enough to
    duck the AGC". Tied to candidate peaks (not raw audio) so the threshold
    auto-scales with the user's per-stage gain."""

    loud_event_min_separation_s: float = 0.3
    """Minimum spacing between consecutive loud events; prevents adjacent
    candidates in a single burst from registering as separate events."""

    recovery_tau_s: float = 1.5
    """Exponential decay constant for ``agc_state`` after a loud event."""

    lookback_s: float = 5.0
    """Cap for ``time_since_last_loud_event`` (returned when no recent event)."""

    self_exclude_s: float = 0.05
    """Don't treat the candidate's own peak as the most-recent loud event;
    look strictly at events ``self_exclude_s`` seconds or more before it."""

    floor_window_s: float = 1.0
    """Pre-window for the local noise-floor estimate."""

    floor_percentile: float = 10.0
    """Percentile of ``|audio|`` used as the local-floor estimate."""


class AGCFeatures(NamedTuple):
    """Per-candidate AGC features, all shape ``(N,)`` in candidate order."""

    agc_state: np.ndarray
    time_since_last_loud_event: np.ndarray
    peak_floor_ratio: np.ndarray


def detect_loud_events(
    candidate_times: np.ndarray,
    peak_amplitudes: np.ndarray,
    config: AGCConfig | None = None,
) -> np.ndarray:
    """Return the times (s) of loud-enough candidate peaks.

    A candidate counts as a "loud event" when its peak amplitude lands in
    the top ``1 - loud_event_peak_quantile`` fraction of all candidate
    peaks in the universe. The threshold therefore adapts per stage to
    whatever the user's typical shot peak is on this recording.

    Adjacent loud candidates within ``loud_event_min_separation_s`` are
    collapsed (a 0.3-0.4 s burst of two shots fires once, not twice) so
    the recovery decay isn't reset by every shot inside a string.
    """
    cfg = config or AGCConfig()
    if candidate_times.size == 0:
        return np.zeros(0, dtype=np.float64)
    threshold = float(np.quantile(peak_amplitudes, cfg.loud_event_peak_quantile))
    mask = peak_amplitudes >= threshold
    times = np.asarray(candidate_times, dtype=np.float64)[mask]
    if times.size == 0:
        return times
    times = np.sort(times)
    keep = [float(times[0])]
    for t in times[1:]:
        if float(t) - keep[-1] >= cfg.loud_event_min_separation_s:
            keep.append(float(t))
    return np.asarray(keep, dtype=np.float64)


def compute_agc_features(
    audio: np.ndarray,
    sample_rate: int,
    candidate_times: np.ndarray,
    peak_amplitudes: np.ndarray,
    config: AGCConfig | None = None,
) -> AGCFeatures:
    """Per-candidate AGC features. See module docstring for what each one means.

    Pure function of the audio + candidate locations; no I/O. The loud-event
    detection is fixture-global, so the same audio gives the same events
    regardless of which candidates are passed in.
    """
    cfg = config or AGCConfig()
    n = len(candidate_times)
    if n == 0:
        z = np.zeros(0, dtype=np.float64)
        return AGCFeatures(z, z.copy(), z.copy())

    abs_audio = np.abs(audio.astype(np.float32))
    loud_event_times = detect_loud_events(
        np.asarray(candidate_times, dtype=np.float64),
        np.asarray(peak_amplitudes, dtype=np.float64),
        cfg,
    )

    agc_state = np.zeros(n, dtype=np.float64)
    time_since = np.full(n, cfg.lookback_s, dtype=np.float64)
    peak_floor_ratio = np.zeros(n, dtype=np.float64)

    floor_win_n = int(round(cfg.floor_window_s * sample_rate))
    self_excl_n = int(round(cfg.self_exclude_s * sample_rate))

    for k, t in enumerate(candidate_times):
        t = float(t)
        if loud_event_times.size:
            cutoff = t - cfg.self_exclude_s
            j = int(np.searchsorted(loud_event_times, cutoff, side="right")) - 1
            if j >= 0:
                dt = t - float(loud_event_times[j])
                if 0.0 <= dt < cfg.lookback_s:
                    time_since[k] = dt
                    agc_state[k] = float(np.exp(-dt / cfg.recovery_tau_s))

        cand_idx = int(round(t * sample_rate))
        floor_hi = max(0, cand_idx - self_excl_n)
        floor_lo = max(0, floor_hi - floor_win_n)
        if floor_hi > floor_lo + 1:
            local_floor = float(np.percentile(abs_audio[floor_lo:floor_hi], cfg.floor_percentile))
        else:
            local_floor = 0.0
        peak_floor_ratio[k] = float(peak_amplitudes[k]) / (local_floor + 1e-6)

    return AGCFeatures(
        agc_state=agc_state,
        time_since_last_loud_event=time_since,
        peak_floor_ratio=peak_floor_ratio,
    )
