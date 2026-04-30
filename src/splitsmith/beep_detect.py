"""Detect the start beep timestamp via bandpass + envelope peak detection.

Strategy (per SPEC.md):
1. Bandpass to [freq_min_hz, freq_max_hz] (typical shot-timer beep ~2.5-4 kHz).
2. Hilbert envelope, lightly smoothed.
3. Find runs where the smoothed envelope (normalized to its global peak) exceeds
   ``min_amplitude`` for at least ``min_duration_ms``. Pick the strongest run.
4. Estimate the in-band noise floor in a window before that run, then **backtrack**
   from the run's threshold-crossing to the earliest sample where the smoothed
   envelope exceeds K * noise_p95. That index is the leading edge of the tone --
   substantially earlier and more accurate than the 30%-of-peak crossing, which
   under-anchors slow-attack timer beeps.

Pure function: takes audio + sample rate + config, returns a BeepDetection. No
file I/O. ``load_audio`` is provided as a thin convenience for callers.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, hilbert, sosfiltfilt

from .config import BeepDetectConfig, BeepDetection

# Noise-floor backtracking parameters. These are intentionally not in the YAML
# config: they're algorithmic constants, not knobs the user should tune.
_BACKTRACK_WINDOW_S = 0.5  # how far before the strong run we'll search for the true onset
_NOISE_WINDOW_LO_S = 1.5  # noise estimate is taken from [run_start - hi, run_start - lo]
_NOISE_WINDOW_HI_S = 0.2  # ...so the noise window ends 200 ms before the run starts
_NOISE_K = 5.0  # leading edge = first sample where smoothed env > K * noise_p95
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

    # Strong-run candidate selection: ``min_amplitude`` is interpreted as a
    # fraction of the global peak envelope, matching how the SPEC describes it.
    above = env >= (config.min_amplitude * peak_value)
    edges = np.diff(above.astype(np.int8), prepend=0, append=0)
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)  # exclusive

    min_run_samples = int(round(sample_rate * config.min_duration_ms / 1000.0))

    candidates: list[tuple[int, int, float]] = []
    for s, e in zip(starts, ends, strict=True):
        if (e - s) < min_run_samples:
            continue
        run_peak = float(env[s:e].max())
        candidates.append((s, e, run_peak))

    if not candidates:
        raise BeepNotFoundError(
            f"no beep candidate of >={config.min_duration_ms} ms above "
            f"{config.min_amplitude:.2f} of peak in [{config.freq_min_hz}, "
            f"{config.freq_max_hz}] Hz"
        )

    run_start, run_end, run_peak = max(candidates, key=lambda c: c[2])

    leading_idx = _backtrack_to_leading_edge(env, run_start, sample_rate)

    return BeepDetection(
        time=leading_idx / sample_rate,
        peak_amplitude=run_peak,
        duration_ms=(run_end - run_start) * 1000.0 / sample_rate,
    )


def _backtrack_to_leading_edge(env: np.ndarray, run_start: int, sample_rate: int) -> int:
    """Walk backwards from ``run_start`` to find the earliest sample whose envelope
    exceeds K times the pre-beep noise p95.

    Falls back to ``run_start`` if no valid noise window exists or no crossing is
    found within the backtrack window.
    """
    noise_lo = max(0, run_start - int(sample_rate * _NOISE_WINDOW_LO_S))
    noise_hi = max(0, run_start - int(sample_rate * _NOISE_WINDOW_HI_S))
    noise_window = env[noise_lo:noise_hi]
    if noise_window.size == 0:
        return run_start

    noise_p95 = float(np.percentile(noise_window, 95))
    threshold = _NOISE_K * noise_p95
    if threshold <= 0.0:
        return run_start

    search_lo = max(0, run_start - int(sample_rate * _BACKTRACK_WINDOW_S))
    segment = env[search_lo:run_start]
    above = segment > threshold
    if not above.any():
        return run_start
    return search_lo + int(np.argmax(above))
