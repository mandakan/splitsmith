"""Detect the start beep timestamp via bandpass + envelope peak detection.

Strategy:

1. Bandpass to ``[freq_min_hz, freq_max_hz]`` (typical shot-timer beep
   2-5 kHz). Hilbert envelope, smoothed at ``envelope_smoothing_ms``
   (40 ms by default -- wide enough to bridge the natural intra-beep
   wobble, narrow enough to keep the 300-500 ms beep distinct from
   sustained ambient noise).

2. **Adaptive cutoff**: a candidate run must clear ``max(min_amplitude *
   global_peak, noise_floor * noise_factor, min_abs_peak)``. The noise-
   floor leg is what recovers handheld / phone clips where the beep is
   faint in absolute terms but still 10x+ above the median noise floor.
   ``global_peak`` is held in reserve for cases where a gunshot dominates
   the band; ``min_abs_peak`` is a sub-noise sanity floor.

3. **Composite scoring**: each candidate is ranked by
   ``silence_score * tonal_score`` where:

   * ``silence_score = run_peak / (mean envelope in pre-silence window)``.
     IPSC beeps are preceded by ~3 s of "Are you ready / Stand by" + a
     pause; mid-stage transients are not. Higher = quieter pre-roll.
   * ``tonal_score = energy_in_3_kHz_band / energy_in_full_band``,
     in [0, 1]. The IPSC timer emits a near-pure ~3.0-3.3 kHz tone;
     gunshots, steel rings, and RO chatter spread energy across the
     full 2-5 kHz band. ``tonal_weight`` controls how strongly this
     component tilts the ranking.

4. **Adaptive rise-foot leading edge**: walk backward from the run's peak
   while the envelope stays above ``max(peak * RISE_FOOT_FRAC, noise_floor
   * RISE_FOOT_NOISE_FACTOR)``. The noise-floor lower bound stops the
   walk from sliding into pre-beep noise on faint beeps where 5 % of the
   peak falls below the noise floor.

This shares the "leading edge" definition with shot_detect: peak-relative
when the beep is loud, noise-floor-relative when it isn't, insensitive
to gain / distance / ambient noise, and lands at the visibly audible
start of the rise.

Pure function: takes audio + sample rate + config, returns a BeepDetection. No
file I/O. ``load_audio`` is provided as a thin convenience for callers.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, hilbert, sosfiltfilt

from .config import BeepCandidate, BeepDetectConfig, BeepDetection

# Rise-foot leading-edge parameters. Same definition as shot_detect (the
# burst's own peak is the reference, so detection is insensitive to gain /
# distance / ambient noise). Tied to the smoothed bandpass envelope -- the
# tone's amplitude profile, not the raw oscillation. The noise-floor
# multiplier kicks in when the burst is only marginally above the floor:
# walking back to 5 % of a faint peak otherwise crosses into pre-beep noise.
_RISE_FOOT_FRAC = 0.05
_RISE_FOOT_NOISE_FACTOR = 1.5
# Fine smoothing window applied to the rise-foot envelope only. Just enough
# to suppress single-sample wobble; not so wide that it shifts the onset.
_LEADING_EDGE_SMOOTHING_MS = 10.0


class BeepNotFoundError(RuntimeError):
    """No beep candidate met the duration + amplitude criteria."""


def load_audio(path: Path) -> tuple[np.ndarray, int]:
    """Load an audio file and return (mono float32 samples, sample rate)."""
    data, sr = sf.read(path, always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data.astype(np.float32, copy=False), int(sr)


def _bandpass_envelope(
    audio: np.ndarray, sample_rate: int, lo: float, hi: float, smoothing_ms: float
) -> np.ndarray:
    """4th-order Butterworth bandpass + Hilbert envelope + moving-average smooth."""
    sos = butter(4, [lo, hi], btype="band", fs=sample_rate, output="sos")
    band = sosfiltfilt(sos, audio)
    env = np.abs(hilbert(band)).astype(np.float32)
    smooth_win = max(1, int(round(sample_rate * smoothing_ms / 1000.0)))
    if smooth_win > 1:
        kernel = np.ones(smooth_win, dtype=np.float32) / smooth_win
        env = np.convolve(env, kernel, mode="same")
    return env


def detect_beep(
    audio: np.ndarray,
    sample_rate: int,
    config: BeepDetectConfig,
) -> BeepDetection:
    """Locate the start beep in ``audio`` and return its leading-edge timestamp.

    Raises ``BeepNotFoundError`` if no candidate satisfies the duration/amplitude
    thresholds.
    """
    if audio.ndim != 1:
        raise ValueError("audio must be 1-D (mono); mix down before calling detect_beep")
    if audio.size == 0:
        raise ValueError("audio is empty")

    # Limit the search to the configured leading window. This prevents mid-
    # stage low-activity moments from out-scoring the real beep on silence
    # preference alone (e.g. a steel ring after a long reload, late in the
    # stage, can have lower pre-window energy than the beep itself).
    if config.search_window_s and config.search_window_s > 0:
        search_hi = min(audio.size, int(round(config.search_window_s * sample_rate)))
        audio = audio[:search_hi]
        if audio.size == 0:
            raise BeepNotFoundError("search window is empty")

    # Two envelopes: coarse (40 ms) for run detection / scoring, fine (10
    # ms) for rise-foot leading-edge timing. The coarse envelope bridges
    # intra-beep dips; the fine envelope keeps the leading-edge sample
    # accurate -- a wide moving-average smear shifts the apparent onset
    # earlier by ~half the smoothing window, which would otherwise blow
    # the ~15 ms tolerance the audit JSONs use.
    env = _bandpass_envelope(
        audio,
        sample_rate,
        config.freq_min_hz,
        config.freq_max_hz,
        config.envelope_smoothing_ms,
    )
    env_fine = _bandpass_envelope(
        audio,
        sample_rate,
        config.freq_min_hz,
        config.freq_max_hz,
        _LEADING_EDGE_SMOOTHING_MS,
    )

    peak_value = float(env.max())
    if peak_value <= 0.0:
        raise BeepNotFoundError("flat audio: no energy in beep band")

    # Noise floor = median of the smoothed envelope. Robust to gunshots /
    # steel rings (a few high samples don't move the median) and to long
    # quiet leads (most samples are near-silent so median stays small).
    noise_floor = float(np.median(env))

    # Effective cutoff: see ``BeepDetectConfig`` -- three legs, take the max.
    cutoff = max(
        config.min_amplitude * peak_value,
        config.noise_floor_factor * noise_floor,
        config.min_abs_peak,
    )
    above = env >= cutoff
    edges = np.diff(above.astype(np.int8), prepend=0, append=0)
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)  # exclusive

    min_run_samples = int(round(sample_rate * config.min_duration_ms / 1000.0))
    pre_window_samples = int(round(sample_rate * config.silence_window_s))
    pre_skip_samples = int(round(sample_rate * config.silence_pre_skip_s))

    # Tonal-quality envelope: same audio, narrower bandpass around the
    # IPSC timer fundamental. We compare run-window energy in this band
    # against the wider band envelope above.
    tonal_env = _bandpass_envelope(
        audio,
        sample_rate,
        config.tonal_band_lo_hz,
        config.tonal_band_hi_hz,
        config.envelope_smoothing_ms,
    )

    candidates: list[tuple[int, int, float, float, float, float]] = []
    for s, e in zip(starts, ends, strict=True):
        if (e - s) < min_run_samples:
            continue
        run_peak = float(env[s:e].max())
        # Silence-preference uses the MAX of the pre-window envelope, not
        # its mean. Mean-based scoring let mid-stage candidates beat the
        # real beep when the pre-window happened to span a magazine swap
        # or a brief lull between shots: the lull dragged the mean down
        # even when the window also contained one or two loud transients.
        # Max-based scoring asks "is there anything else loud in recent
        # past?" -- a real beep has a clean pre-roll, so the answer is no.
        #
        # Candidates near t=0 don't have a full pre-window. The metric is
        # undefined for them, so we substitute a neutral 1.0: tonal +
        # duration must do the discrimination instead. Otherwise a
        # truncated-pre-window candidate gets ``peak / noise_floor``,
        # which beats real beeps whose pre-window contains RO chatter.
        pre_hi = max(0, s - pre_skip_samples)
        pre_lo = max(0, pre_hi - pre_window_samples)
        available_pre_s = (pre_hi - pre_lo) / sample_rate
        if available_pre_s < config.min_pre_window_s:
            silence_score = 1.0
        else:
            pre_max = float(env[pre_lo:pre_hi].max())
            pre_max = max(pre_max, noise_floor)
            silence_score = run_peak / (pre_max + 1e-6)

        # Tonal concentration: energy in the IPSC fundamental band over
        # energy in the full search band, computed on the smoothed envelope.
        # Sums over a few hundred ms of run samples are stable; a single-
        # sample peak ratio would be noisy.
        wide_energy = float(np.sum(env[s:e]))
        narrow_energy = float(np.sum(tonal_env[s:e]))
        tonal_ratio = narrow_energy / (wide_energy + 1e-6)
        tonal_ratio = max(0.0, min(1.0, tonal_ratio))

        # Composite score: silence-preference, modulated by tonal quality
        # AND duration-match. tonal_weight=0 + dur_match_weight=0 falls
        # back to legacy silence-only behaviour.
        weight = max(0.0, min(1.0, config.tonal_weight))
        tonal_factor = (1.0 - weight) + weight * tonal_ratio
        # Duration-match factor: ramp from 0 at min_ms to 1 at full_ms,
        # squared to make the penalty bite harder on short transients.
        # A 168 ms shot (typical post-smoothing length) lands at
        # ((168-150)/150)^2 = 0.014; a 340 ms beep at 1.0. The squaring
        # is what actually demotes shots whose pre-window happens to be
        # quiet (magazine-swap lulls etc.) -- silence-preference alone
        # can't tell those from the real beep.
        dur_ms = (e - s) * 1000.0 / sample_rate
        span_ms = max(1.0, config.dur_match_full_ms - config.dur_match_min_ms)
        dur_ratio = max(0.0, min(1.0, (dur_ms - config.dur_match_min_ms) / span_ms))
        dur_weight = max(0.0, min(1.0, config.dur_match_weight))
        dur_factor = (1.0 - dur_weight) + dur_weight * dur_ratio * dur_ratio
        score = silence_score * tonal_factor * dur_factor

        candidates.append((s, e, run_peak, score, silence_score, tonal_ratio))

    if not candidates:
        raise BeepNotFoundError(
            f"no beep candidate of >={config.min_duration_ms} ms above "
            f"cutoff {cutoff:.4f} (peak={peak_value:.4f}, "
            f"noise_floor={noise_floor:.4f}) in [{config.freq_min_hz}, "
            f"{config.freq_max_hz}] Hz"
        )

    # Rank by composite score (highest first). Compute the rise-foot
    # leading edge for every candidate so the UI can show alternatives
    # without a second pass.
    ranked = sorted(candidates, key=lambda c: c[3], reverse=True)
    ranked_models: list[BeepCandidate] = []
    for run_start, run_end, run_peak, score, silence_score, tonal_ratio in ranked:
        leading_idx = _rise_foot_leading_edge(env_fine, run_start, run_end, noise_floor)
        ranked_models.append(
            BeepCandidate(
                time=leading_idx / sample_rate,
                score=score,
                peak_amplitude=run_peak,
                duration_ms=(run_end - run_start) * 1000.0 / sample_rate,
                silence_score=silence_score,
                tonal_score=tonal_ratio,
            )
        )

    top_n = config.top_n_candidates if config.top_n_candidates > 0 else 1
    surfaced = ranked_models[:top_n]
    winner = ranked_models[0]
    return BeepDetection(
        time=winner.time,
        peak_amplitude=winner.peak_amplitude,
        duration_ms=winner.duration_ms,
        candidates=surfaced,
    )


def _rise_foot_leading_edge(
    env: np.ndarray, run_start: int, run_end: int, noise_floor: float
) -> int:
    """Rise-foot of the tone: walk backward from the envelope peak (within the
    strong run) while the envelope stays at or above ``max(peak *
    RISE_FOOT_FRAC, noise_floor * RISE_FOOT_NOISE_FACTOR)``. The earliest
    such sample is the foot of the rise.

    The noise-floor lower bound prevents the walk from continuing into
    pre-beep silence on faint beeps where 5 % of the peak falls below the
    median noise floor (e.g. iPhone handheld clips with ~10x SNR).
    """
    if run_end <= run_start:
        return run_start
    peak_offset = int(np.argmax(env[run_start:run_end]))
    peak_idx = run_start + peak_offset
    peak = float(env[peak_idx])
    if peak <= 0.0:
        return run_start
    foot = max(peak * _RISE_FOOT_FRAC, noise_floor * _RISE_FOOT_NOISE_FACTOR)
    i = peak_idx
    while i > 0 and env[i - 1] >= foot:
        i -= 1
    return i
