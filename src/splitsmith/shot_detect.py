"""Detect gunshot timestamps via librosa onset detection within the stage window.

Pipeline:
1. Slice ``[beep_time + 0.5, beep_time + stage_time + 1.0]`` of the audio.
2. ``librosa.onset.onset_detect`` (spectral flux, delta=onset_delta) to locate
   candidate onsets at frame resolution (~10.7 ms at 48 kHz / hop 512).
3. Greedy ``min_gap_ms`` filter: drop onsets within 80 ms of a previously
   kept one (handles within-shot echoes from steel/walls).
4. Echo refractory: drop onsets within 150 ms of a kept one whose peak
   amplitude is below 40 % of the previous peak (catches quieter intra-bay
   echoes that survive min-gap).
5. **Half-rise leading-edge refinement** (the per-onset time you actually
   see in the output): for each kept onset, find the local peak |audio| in
   a 30 ms window and walk forward to the first sample reaching 50 % of
   that peak. This is the "leading edge" definition used for all timing.

Why half-rise (not amplitude threshold or noise-floor crossing):
- INSENSITIVE to ambient noise and AGC: the burst's own peak is the reference,
  so a quiet AGC-ducked shot lands at the same fractional point on its rise as
  a loud unducked shot. This keeps timing consistent across stages, matches,
  and recording conditions.
- DEFENSIBLE: half-rise is the standard onset definition in audio-engineering
  literature for sharp transients. It's also approximately where the human
  eye picks the leading edge when scrubbing a waveform in a UI.
- NOT directly comparable to a CED7000 (which uses an absolute threshold),
  but SPLITS (differences between consecutive shots) ARE comparable since
  any constant detection offset cancels.

v1 priorities:
- High recall: prefer false positives over missed shots; the user culls in
  the review UI / CSV.
- Surface uncertainty: every onset is returned with peak amplitude and a
  confidence score (geometric mean of onset strength and peak, normalized
  within the kept set) so the user can sort by confidence ascending and cull
  from the bottom.
- Don't filter on absolute amplitude (Insta360 Go 3S AGC ducks follow-up
  shots; the first shot after a pause can be 5x quieter than peak).

Pure function: takes audio + beep time + stage time + config, returns
list[Shot]. No file I/O.
"""

from __future__ import annotations

import librosa
import numpy as np

from .config import Shot, ShotDetectConfig

_HOP_LENGTH = 512  # librosa default; ~10.7 ms per frame at 48 kHz
_PEAK_WIN_MS = 5.0  # half-width of the window used to read peak amplitude per shot
# Skip the typical beep tail + shooter-reaction floor before starting onset
# detection. Beep tones are 200-400 ms (per SPEC) and human reaction + draw is
# never under 500 ms on a head-mounted recording, so no real shot is missed by
# starting the search 500 ms after the beep. Including the beep in the segment
# also distorts the onset-envelope reference statistics enough to drop genuine
# late-stage onsets, so this is also a detection-quality fix.
_POST_BEEP_SKIP_S = 0.5

# Half-rise leading-edge refinement. For each kept onset:
#   1. find the peak |audio| within ``_LEAD_EDGE_PEAK_WINDOW_S`` of the
#      librosa-frame onset time (a small backward pad accommodates frame
#      quantization and any pre-peak slack);
#   2. walk forward from the start of that window to the first sample whose
#      |audio| reaches ``_LEAD_EDGE_HALF_RISE_FRAC * peak``.
# That sample is the reported leading edge.
_LEAD_EDGE_PRE_PAD_S = 0.005  # backward pad before the librosa frame
_LEAD_EDGE_PEAK_WINDOW_S = 0.030  # forward span used to locate the local peak
_LEAD_EDGE_HALF_RISE_FRAC = 0.5  # the "half" in half-rise


def detect_shots(
    audio: np.ndarray,
    sample_rate: int,
    beep_time: float,
    stage_time: float,
    config: ShotDetectConfig,
) -> list[Shot]:
    """Detect shots in ``[beep_time, beep_time + stage_time + 1.0]``.

    Returns shots in chronological order. ``time_absolute`` is in seconds from the
    start of ``audio``; ``split`` for shot 1 is measured from the beep.
    """
    if audio.ndim != 1:
        raise ValueError("audio must be 1-D (mono); mix down before calling detect_shots")
    if audio.size == 0:
        raise ValueError("audio is empty")
    if beep_time < 0.0:
        raise ValueError(f"beep_time must be non-negative, got {beep_time}")
    if stage_time <= 0.0:
        raise ValueError(f"stage_time must be positive, got {stage_time}")

    search_lo = int(round((beep_time + _POST_BEEP_SKIP_S) * sample_rate))
    search_hi = min(audio.size, int(round((beep_time + stage_time + 1.0) * sample_rate)))
    if search_hi <= search_lo:
        return []

    segment = audio[search_lo:search_hi]

    onset_env = librosa.onset.onset_strength(y=segment, sr=sample_rate, hop_length=_HOP_LENGTH)

    onset_frames = librosa.onset.onset_detect(
        onset_envelope=onset_env,
        sr=sample_rate,
        hop_length=_HOP_LENGTH,
        delta=config.onset_delta,
        pre_max=_ms_to_frames(config.pre_max_ms, sample_rate),
        post_max=_ms_to_frames(config.post_max_ms, sample_rate),
        backtrack=False,
    )
    if onset_frames.size == 0:
        return []

    onset_times_segment = librosa.frames_to_time(
        onset_frames, sr=sample_rate, hop_length=_HOP_LENGTH
    )
    onset_times_absolute = onset_times_segment + search_lo / sample_rate

    onset_strengths = onset_env[onset_frames]
    peak_win_samples = int(round(sample_rate * _PEAK_WIN_MS / 1000.0))
    onset_peaks = np.array(
        [
            _peak_amplitude(audio, float(t), sample_rate, peak_win_samples)
            for t in onset_times_absolute
        ],
        dtype=np.float32,
    )

    # Greedy minimum-gap filter: keep the first onset, drop subsequent onsets
    # within ``min_gap_ms`` of the previously kept onset.
    min_gap_s = config.min_gap_ms / 1000.0
    refractory_s = config.echo_refractory_ms / 1000.0
    ratio = config.echo_amplitude_ratio
    kept_times: list[float] = []
    kept_strengths: list[float] = []
    kept_peaks: list[float] = []
    for t, strength, peak in zip(
        onset_times_absolute.tolist(), onset_strengths.tolist(), onset_peaks.tolist(), strict=True
    ):
        if kept_times:
            gap = t - kept_times[-1]
            if gap < min_gap_s:
                continue
            # Echo refractory: within (min_gap, refractory) of the previous kept
            # shot AND substantially quieter -> drop as likely echo.
            if gap < refractory_s and peak < kept_peaks[-1] * ratio:
                continue
        kept_times.append(float(t))
        kept_strengths.append(float(strength))
        kept_peaks.append(float(peak))

    # Refine each kept onset's time to its half-rise leading edge. Min-gap
    # and refractory operate on the librosa-frame times above (preserving
    # ordering and candidate count); only the OUTPUT time changes.
    kept_times = [_half_rise_leading_edge(audio, t, sample_rate) for t in kept_times]
    # Re-measure peak amplitude at the backtracked times (the actual transient
    # peak is typically a few ms past the leading edge, still inside the peak
    # window since _PEAK_WIN_MS is symmetric).
    kept_peaks = [_peak_amplitude(audio, t, sample_rate, peak_win_samples) for t in kept_times]

    # Combined confidence: geometric mean of onset strength and peak amplitude,
    # each normalized to its own max within the kept set. Sorting CSV rows by
    # this column ascending puts the most likely false positives at the top.
    max_kept_strength = max(kept_strengths) if kept_strengths else 0.0
    max_kept_peak = max(kept_peaks) if kept_peaks else 0.0

    shots: list[Shot] = []
    prev_t = beep_time
    for i, (t_abs, strength, peak) in enumerate(
        zip(kept_times, kept_strengths, kept_peaks, strict=True), start=1
    ):
        s_norm = strength / max_kept_strength if max_kept_strength > 0 else 0.0
        p_norm = peak / max_kept_peak if max_kept_peak > 0 else 0.0
        confidence = float(np.clip((s_norm * p_norm) ** 0.5, 0.0, 1.0))
        shots.append(
            Shot(
                shot_number=i,
                time_absolute=t_abs,
                time_from_beep=t_abs - beep_time,
                split=t_abs - prev_t,
                peak_amplitude=peak,
                confidence=confidence,
            )
        )
        prev_t = t_abs

    return shots


def _half_rise_leading_edge(audio: np.ndarray, onset_t: float, sr: int) -> float:
    """Return the half-rise time of the transient surrounding ``onset_t``.

    The window starts a few ms BEFORE the librosa frame (to accommodate frame
    quantization, since the actual transient often begins slightly before the
    frame at which spectral flux peaks) and extends ~30 ms after. Within that
    window we find the absolute peak |audio| (the burst's transient peak),
    then walk forward from the window's start to the first sample at or above
    HALF that peak. That's the reported leading edge.

    Falls back to ``onset_t`` if the audio in the window is silent.
    """
    onset_idx = int(round(onset_t * sr))
    win_lo = max(0, onset_idx - int(_LEAD_EDGE_PRE_PAD_S * sr))
    win_hi = min(audio.size, onset_idx + int(_LEAD_EDGE_PEAK_WINDOW_S * sr))
    if win_hi <= win_lo:
        return onset_t
    window = np.abs(audio[win_lo:win_hi])
    peak = float(window.max()) if window.size else 0.0
    if peak <= 0.0:
        return onset_t
    half = peak * _LEAD_EDGE_HALF_RISE_FRAC
    above = window >= half
    if not above.any():
        return onset_t
    return (win_lo + int(np.argmax(above))) / sr


def _ms_to_frames(ms: int, sample_rate: int) -> int:
    return max(1, int(round(ms / 1000.0 * sample_rate / _HOP_LENGTH)))


def _peak_amplitude(audio: np.ndarray, t: float, sr: int, half_win: int) -> float:
    centre = int(round(t * sr))
    lo = max(0, centre - half_win)
    hi = min(audio.size, centre + half_win)
    if hi <= lo:
        return 0.0
    return float(np.max(np.abs(audio[lo:hi])))
