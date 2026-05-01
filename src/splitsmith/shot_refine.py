"""Second-pass timing refinement for confirmed shots.

The candidate generator (``shot_detect``) uses a narrow [-5 ms, +30 ms] peak
search around each librosa onset frame -- a tradeoff favouring stable
feature extraction for the voter pipeline (broader scans pull negatives'
features toward shot-like values and tank precision).

For *confirmed* shots (post-voter, post-audit), we can afford a wider scan
because the candidate is known to be a real muzzle blast. This module runs a
200 ms broadband-envelope peak search around each confirmed shot's
approximate time and walks back to the rise foot. It produces a refined
timestamp that nothing in the voter pipeline ever sees -- output only goes
downstream into CSV / FCPXML / split calculations.

Two refinement methods are available:

* ``"envelope"`` (default): wide broadband 2 ms moving-max envelope + 5 %
  rise-foot backtrack. Reliable on AGC-ducked / reverb-anchored shots
  (those are exactly the cases the candidate generator gets wrong) at the
  cost of being amplitude-based -- but post-confirmation that's safe.
* ``"aic"``: AIC picker on bandpassed raw waveform. Sub-ms accurate on
  ISOLATED transients but degrades on busy reverb backgrounds (no clean
  noise / signal boundary). Available for clean recordings; not the
  default because IPSC stages are usually busy.

Pure function: takes audio + approximate time + config, returns a
``RefinedShot``. No file I/O. The expected caller wires it in *after* the
voter pipeline / audit UI confirmation step.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import maximum_filter1d
from scipy.signal import butter, sosfiltfilt

from .config import ShotRefineConfig

_ENV_SMOOTH_S = 0.002  # 2 ms moving-max -- matches shot_detect rise-foot
_RISE_FOOT_FRAC = 0.05  # foot threshold -- matches shot_detect


@dataclass(frozen=True)
class RefinedShot:
    """Result of refining a single shot's timestamp.

    ``time`` is the refined seconds-from-audio-start.
    ``original_time`` is the input approximate time (for diff inspection).
    ``confidence`` is the chosen method's quality metric in [0, 1].
    ``accepted`` is False when ``confidence`` fell below the configured
    minimum and the original timestamp was kept.
    ``method`` records which path produced the result.
    """

    time: float
    original_time: float
    confidence: float
    accepted: bool
    method: str

    @property
    def drift_ms(self) -> float:
        return (self.time - self.original_time) * 1000.0


def refine_shot_time(
    audio: np.ndarray,
    sample_rate: int,
    approx_time: float,
    config: ShotRefineConfig,
) -> RefinedShot:
    """Refine a single CONFIRMED shot's timestamp.

    Caller contract: ``approx_time`` should already have passed voter
    filtering / user audit (i.e. it is a real muzzle blast, not a stray
    negative). The refinement may move the timestamp by 50-200 ms in
    reverb-anchored cases; never wire this back into the voter feature
    extraction or precision will collapse.
    """
    if audio.ndim != 1:
        raise ValueError("audio must be 1-D (mono)")
    if approx_time < 0:
        raise ValueError(f"approx_time must be non-negative, got {approx_time}")

    if config.method == "aic":
        return _refine_aic(audio, sample_rate, approx_time, config)
    return _refine_envelope(audio, sample_rate, approx_time, config)


def _refine_envelope(
    audio: np.ndarray,
    sr: int,
    approx_time: float,
    config: ShotRefineConfig,
) -> RefinedShot:
    """Wide broadband-envelope peak + rise-foot backtrack.

    Only re-anchors when the wide-window peak is at least
    ``reanchor_ratio`` times the local-position peak. For clean shots the
    original anchor IS the local peak so the ratio is ~1 and refinement
    falls through to a tight rise-foot adjustment around the original
    position. For reverb-anchored shots (stage-3 cand #35 case) the wide
    peak is many times louder than the local one so we re-anchor to the
    true onset.
    """
    half = int(round(config.search_half_window_ms / 1000.0 * sr))
    centre = int(round(approx_time * sr))
    win_lo = max(0, centre - half)
    win_hi = min(audio.size, centre + half)
    if win_hi - win_lo < 8:
        return RefinedShot(approx_time, approx_time, 0.0, False, "envelope")

    smooth_n = max(1, int(round(sr * _ENV_SMOOTH_S)))
    raw = np.abs(audio[win_lo:win_hi]).astype(np.float32)
    env = maximum_filter1d(raw, size=smooth_n, mode="nearest")

    # Local peak at the original position (small +/- 10 ms anchor window).
    local_half = int(round(0.010 * sr))
    centre_local = centre - win_lo
    local_lo = max(0, centre_local - local_half)
    local_hi = min(env.size, centre_local + local_half)
    if local_hi - local_lo < 1:
        return RefinedShot(approx_time, approx_time, 0.0, False, "envelope")
    local_peak = float(env[local_lo:local_hi].max())

    # Wide-window peak (search the full +/- search_half_window_ms).
    wide_peak_local = int(np.argmax(env))
    wide_peak = float(env[wide_peak_local])
    if wide_peak <= 0.0:
        return RefinedShot(approx_time, approx_time, 0.0, False, "envelope")

    ratio = wide_peak / max(local_peak, 1e-9)
    # Confidence: how decisive the re-anchor is. ratio = 1 -> no need to
    # re-anchor (confidence 0); large ratio -> strong evidence the original
    # was on a reverb peak and the true onset is the wide peak.
    confidence = float(np.clip(1.0 - 1.0 / max(ratio, 1.0 + 1e-9), 0.0, 1.0))

    if ratio < config.reanchor_ratio:
        # Original anchor is at (or near) the local peak -- the candidate
        # generator's rise-foot already placed it correctly. Re-running
        # rise-foot here would compound (walk further back into pre-shot
        # noise above the 5 % threshold). Keep the original timestamp.
        return RefinedShot(approx_time, approx_time, confidence, False, "envelope")

    # Reverb-anchored case: re-anchor to the wide peak and walk back.
    foot_threshold = wide_peak * _RISE_FOOT_FRAC
    i = wide_peak_local
    while i > 0 and env[i - 1] >= foot_threshold:
        i -= 1
    refined_time = (win_lo + i) / sr
    return RefinedShot(refined_time, approx_time, confidence, True, "envelope")


def _tight_rise_foot(
    audio: np.ndarray,
    sr: int,
    approx_time: float,
    confidence: float,
) -> RefinedShot:
    """Tight +/- 30 ms rise-foot refinement; never re-anchors away."""
    half = int(round(0.030 * sr))
    centre = int(round(approx_time * sr))
    win_lo = max(0, centre - half)
    win_hi = min(audio.size, centre + half)
    if win_hi - win_lo < 8:
        return RefinedShot(approx_time, approx_time, confidence, True, "envelope-tight")
    smooth_n = max(1, int(round(sr * _ENV_SMOOTH_S)))
    env = maximum_filter1d(
        np.abs(audio[win_lo:win_hi]).astype(np.float32), size=smooth_n, mode="nearest"
    )
    peak_local = int(np.argmax(env))
    peak = float(env[peak_local])
    if peak <= 0.0:
        return RefinedShot(approx_time, approx_time, confidence, True, "envelope-tight")
    foot_threshold = peak * _RISE_FOOT_FRAC
    i = peak_local
    while i > 0 and env[i - 1] >= foot_threshold:
        i -= 1
    refined_time = (win_lo + i) / sr
    return RefinedShot(refined_time, approx_time, confidence, True, "envelope-tight")


def _refine_aic(
    audio: np.ndarray,
    sr: int,
    approx_time: float,
    config: ShotRefineConfig,
) -> RefinedShot:
    """AIC picker on bandpassed raw waveform."""
    half = int(round(config.search_half_window_ms / 1000.0 * sr))
    centre = int(round(approx_time * sr))
    lo = max(0, centre - half)
    hi = min(audio.size, centre + half)
    if hi - lo < 16:
        return RefinedShot(approx_time, approx_time, 0.0, False, "aic")

    seg = audio[lo:hi].astype(np.float64)
    if config.bandpass_low_hz is not None and config.bandpass_high_hz is not None:
        nyquist = sr / 2.0
        low = max(1.0, config.bandpass_low_hz)
        high = min(nyquist - 1.0, config.bandpass_high_hz)
        if high > low:
            sos = butter(4, [low, high], btype="band", fs=sr, output="sos")
            seg = sosfiltfilt(sos, seg)

    aic = _aic_curve(seg)
    if aic.size == 0 or not np.isfinite(aic).any():
        return RefinedShot(approx_time, approx_time, 0.0, False, "aic")

    finite_aic = aic[np.isfinite(aic)]
    pick = int(np.argmin(aic))
    aic_max = float(np.max(finite_aic))
    aic_min = float(np.min(finite_aic))
    span = abs(aic_max - aic_min)
    confidence = float(np.clip(span / (abs(aic_max) + abs(aic_min) + 1e-9), 0.0, 1.0))

    if confidence < config.min_confidence:
        return RefinedShot(approx_time, approx_time, confidence, False, "aic")
    refined_time = (lo + pick) / sr
    return RefinedShot(refined_time, approx_time, confidence, True, "aic")


def _aic_curve(x: np.ndarray) -> np.ndarray:
    """AIC at every sample of ``x``. O(n) via cumulative sums.

    AIC(k) = k * log(var(x[0:k])) + (n-k-1) * log(var(x[k:n]))

    Edge samples (margin=4) are masked to ``inf`` to avoid degenerate
    variances.
    """
    n = x.size
    if n < 8:
        return np.empty(0, dtype=np.float64)

    cs = np.cumsum(x, dtype=np.float64)
    cs2 = np.cumsum(x * x, dtype=np.float64)
    cs0 = np.concatenate(([0.0], cs))
    cs02 = np.concatenate(([0.0], cs2))

    aic = np.full(n, np.inf, dtype=np.float64)
    margin = 4
    for k in range(margin, n - margin):
        n1 = k
        s1 = cs0[k]
        s12 = cs02[k]
        var1 = max((s12 - s1 * s1 / n1) / n1, 1e-18)
        n2 = n - k
        s2 = cs0[n] - cs0[k]
        s22 = cs02[n] - cs02[k]
        var2 = max((s22 - s2 * s2 / n2) / n2, 1e-18)
        aic[k] = n1 * np.log(var1) + (n2 - 1) * np.log(var2)
    return aic
