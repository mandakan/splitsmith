"""Detect the start beep timestamp via bandpass + envelope peak detection.

Strategy:
1. Bandpass to [freq_min_hz, freq_max_hz] (typical shot-timer beep ~2.5-4 kHz).
2. Hilbert envelope, lightly smoothed.
3. Find runs where the smoothed envelope (normalized to its global peak) exceeds
   ``min_amplitude`` for at least ``min_duration_ms``.
4. **Silence-preference scoring**: pick the run with the highest ratio of
   ``run_peak / (mean envelope in the pre-window + eps)``. Real IPSC beeps are
   preceded by several seconds of "Are you ready? / Stand by" and a pause; in-
   stage steel rings and shots are not. Ranking by peak alone biases toward
   loud mid-stage transients on AGC'd / quiet recordings; ranking by
   peak-over-pre-silence biases toward the actual beep regardless of absolute
   loudness.
5. **Rise-foot leading edge** (matches shot_detect): locate the peak of the
   smoothed envelope inside the chosen run, then walk backward through the
   envelope. Walking stops when the envelope drops below 5 % of peak (silence
   before the tone) or starts rising again (entering an earlier transient).

This shares the "leading edge" definition with shot_detect: peak-relative,
insensitive to gain / distance / ambient noise, and lands at the visibly
audible start of the rise.

Note on draw-time interpretation: rise-foot may sit a bit earlier or later
than the previous noise-floor backtrack depending on the beep's ramp shape
and noise floor. ``draw_time = first_shot - beep_time`` shifts accordingly;
splits BETWEEN shots are unaffected (beep_time cancels).

Pure function: takes audio + sample rate + config, returns a BeepDetection. No
file I/O. ``load_audio`` is provided as a thin convenience for callers.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, hilbert, sosfiltfilt

from .config import BeepDetectConfig, BeepDetection

# Rise-foot leading-edge parameters. Same definition as shot_detect (the
# burst's own peak is the reference, so detection is insensitive to gain /
# distance / ambient noise). Tied to the smoothed bandpass envelope -- the
# tone's amplitude profile, not the raw oscillation.
_RISE_FOOT_FRAC = 0.05
_SMOOTHING_S = 0.010  # 10 ms moving-average smoothing of the envelope


class BeepNotFoundError(RuntimeError):
    """No beep candidate met the duration + amplitude criteria."""


def load_audio(path: Path) -> tuple[np.ndarray, int]:
    """Load an audio file and return (mono float32 samples, sample rate)."""
    data, sr = sf.read(path, always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data.astype(np.float32, copy=False), int(sr)


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

    sos = butter(
        4,
        [config.freq_min_hz, config.freq_max_hz],
        btype="band",
        fs=sample_rate,
        output="sos",
    )
    band = sosfiltfilt(sos, audio)
    env = np.abs(hilbert(band)).astype(np.float32)

    smooth_win = max(1, int(round(sample_rate * _SMOOTHING_S)))
    if smooth_win > 1:
        kernel = np.ones(smooth_win, dtype=np.float32) / smooth_win
        env = np.convolve(env, kernel, mode="same")

    peak_value = float(env.max())
    if peak_value <= 0.0:
        raise BeepNotFoundError("flat audio: no energy in beep band")

    # Strong-run candidate selection: combine the fraction-of-peak threshold
    # (current behaviour) with an ABSOLUTE peak floor so a low global peak
    # doesn't let near-silent ambient noise qualify as a candidate. The
    # effective cutoff is the larger of the two.
    cutoff = max(config.min_amplitude * peak_value, config.min_abs_peak)
    above = env >= cutoff
    edges = np.diff(above.astype(np.int8), prepend=0, append=0)
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)  # exclusive

    min_run_samples = int(round(sample_rate * config.min_duration_ms / 1000.0))

    pre_window_samples = int(round(sample_rate * config.silence_window_s))
    pre_skip_samples = int(round(sample_rate * config.silence_pre_skip_s))

    candidates: list[tuple[int, int, float, float]] = []
    for s, e in zip(starts, ends, strict=True):
        if (e - s) < min_run_samples:
            continue
        run_peak = float(env[s:e].max())
        # Mean envelope in the pre-silence window. ``silence_pre_skip_s``
        # excludes the immediate ramp-up so a slow beep onset doesn't bleed
        # into its own pre-silence measurement. If the candidate starts too
        # early in the audio for a full window, use what we have.
        pre_hi = max(0, s - pre_skip_samples)
        pre_lo = max(0, pre_hi - pre_window_samples)
        if pre_hi > pre_lo:
            pre_mean = float(env[pre_lo:pre_hi].mean())
        else:
            pre_mean = 0.0
        # Score: loud burst preceded by quiet wins over loud burst preceded
        # by an active stage (or other shots / RO chatter). Eps prevents a
        # zero-pre-silence candidate from scoring infinity.
        score = run_peak / (pre_mean + 1e-6)
        candidates.append((s, e, run_peak, score))

    if not candidates:
        raise BeepNotFoundError(
            f"no beep candidate of >={config.min_duration_ms} ms above "
            f"{config.min_amplitude:.2f} of peak in [{config.freq_min_hz}, "
            f"{config.freq_max_hz}] Hz"
        )

    run_start, run_end, run_peak, _score = max(candidates, key=lambda c: c[3])

    leading_idx = _rise_foot_leading_edge(env, run_start, run_end)

    return BeepDetection(
        time=leading_idx / sample_rate,
        peak_amplitude=run_peak,
        duration_ms=(run_end - run_start) * 1000.0 / sample_rate,
    )


def _rise_foot_leading_edge(env: np.ndarray, run_start: int, run_end: int) -> int:
    """Rise-foot of the tone: walk backward from the envelope peak (within the
    strong run) while the envelope stays at or above ``_RISE_FOOT_FRAC * peak``.
    The earliest such sample is the foot of the rise.
    """
    if run_end <= run_start:
        return run_start
    peak_offset = int(np.argmax(env[run_start:run_end]))
    peak_idx = run_start + peak_offset
    peak = float(env[peak_idx])
    if peak <= 0.0:
        return run_start
    foot = peak * _RISE_FOOT_FRAC
    i = peak_idx
    while i > 0 and env[i - 1] >= foot:
        i -= 1
    return i
