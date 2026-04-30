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
5. **Rise-foot leading-edge refinement** (the per-onset time you actually
   see in the output): for each kept onset, build a smoothed envelope
   (2 ms moving max of |audio|), find the local peak in a window anchored
   on the librosa frame, and walk BACKWARD from that peak to the foot of
   the rise. Walking stops when the envelope either drops below 5 % of
   peak (silence at the foot) or starts rising again (entering an earlier
   transient). The window extends up to 100 ms before the librosa frame
   so the foot is reachable even when librosa fires well into the rise.
6. Per-onset HF/LF spectral signature: 30 ms FFT window centred on the
   refined leading edge, ratio of power in [4 kHz, 12 kHz] to power in
   [0, 2 kHz]. Real shots into a head-mounted mic arrive as broadband
   transients with strong HF content; other-bay shots are distance-
   attenuated and air-absorption-filtered, leaving relatively more LF.
   Folded into ``confidence`` as the cube root of strength_norm * peak_norm
   * hf_ratio_norm so AGC-ducked-but-on-mic shots are not pushed to the
   bottom by amplitude alone.

Why rise-foot (not half-rise, amplitude threshold, or noise-floor crossing):
- INSENSITIVE to ambient noise and AGC: the burst's own peak is the reference,
  so a quiet AGC-ducked shot lands at the same fractional point on its rise as
  a loud unducked shot. This keeps timing consistent across stages, matches,
  and recording conditions.
- TARGETS THE VISIBLE START of the transient -- where your eye picks the
  leading edge when scrubbing a waveform. Half-rise (50 %) lands mid-rise,
  visibly later than the audible onset; users dragging UI markers
  consistently pull them earlier toward the rise foot.
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
from scipy.ndimage import maximum_filter1d

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

# Rise-foot leading-edge refinement. For each kept onset:
#   1. Build a smoothed envelope (2 ms moving max of |audio|) over a window
#      that spans up to 100 ms BEFORE the librosa frame and 30 ms after.
#   2. Find the peak of the envelope in [onset - 5 ms, onset + 30 ms] -- the
#      transient that librosa flagged.
#   3. Walk BACKWARD from that peak, sample by sample. Stop when:
#      a. the envelope drops below ``_RISE_FOOT_FRAC * peak`` (we've reached
#         the silence at the foot of the rise); OR
#      b. the envelope starts rising as we go back (we'd be entering an
#         earlier transient).
# The reported leading edge is that stop sample -- the foot of the rise that
# leads to the local peak.
_LEAD_EDGE_PRE_PAD_S = 0.005  # peak-search anchor BEFORE the librosa frame
_LEAD_EDGE_PEAK_WINDOW_S = 0.030  # peak-search anchor AFTER the librosa frame
_LEAD_EDGE_BACKWARD_MAX_S = 0.100  # max backward walk (caps if librosa fires late)
_RISE_FOOT_FRAC = 0.05  # rise-foot threshold (fraction of local peak)
_LEAD_EDGE_SMOOTH_S = 0.002  # 2 ms moving-max smoothing of |audio| -> envelope
# Backward-walk rise-detection guard. After the walk has reached at least this
# many ms before the peak (so it has actually descended into the rise), stop
# when the envelope climbs back above ``_RISE_BACK_FACTOR`` x the deepest
# valley reached so far. Without this, busy audio (continuous sub-shot
# transients between real shots) lets the walk run all the way to the 100 ms
# cap and the candidate lands on the foot of the PREVIOUS event. 20 ms is
# the upper end of the safe range -- ``scripts/eval_detector.py`` shows this
# value gives the lowest median+p90 drag without losing any audited shots;
# 25 ms+ starts overshooting on tight inter-shot gaps and regresses recall.
_LEAD_EDGE_RISE_GUARD_MIN_S = 0.020
_RISE_BACK_FACTOR = 1.5

# HF/LF spectral signature window. Centred on the refined leading edge so the
# transient body sits inside the window. 30 ms is short enough that the FFT
# captures the burst itself (not the surrounding ambience) and long enough
# that the lowest analysed frequency (~33 Hz) is well below the LF band cap.
_HF_LF_WINDOW_S = 0.030
_HF_LF_LOW_BAND_HZ = (0.0, 2000.0)  # LF: room rumble, distant-bay thud
_HF_LF_HIGH_BAND_HZ = (4000.0, 12000.0)  # HF: muzzle crack, on-mic broadband
_HF_LF_EPS = 1e-9  # guard against division by zero on silent windows


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
    kept_times = [_leading_edge(audio, t, sample_rate) for t in kept_times]
    # Re-measure peak amplitude at the backtracked times (the actual transient
    # peak is typically a few ms past the leading edge, still inside the peak
    # window since _PEAK_WIN_MS is symmetric).
    kept_peaks = [_peak_amplitude(audio, t, sample_rate, peak_win_samples) for t in kept_times]
    # HF/LF spectral signature at the refined leading edge. Re-rank only --
    # nothing is dropped on this feature.
    kept_hf_lf = [_hf_lf_ratio(audio, t, sample_rate) for t in kept_times]

    # Combined confidence: cube root of onset strength * peak amplitude *
    # HF/LF ratio, each normalized to its own max within the kept set.
    # Sorting CSV rows by this column ascending puts the most likely false
    # positives at the top.
    max_kept_strength = max(kept_strengths) if kept_strengths else 0.0
    max_kept_peak = max(kept_peaks) if kept_peaks else 0.0
    max_kept_hf_lf = max(kept_hf_lf) if kept_hf_lf else 0.0

    shots: list[Shot] = []
    prev_t = beep_time
    for i, (t_abs, strength, peak, hf_lf) in enumerate(
        zip(kept_times, kept_strengths, kept_peaks, kept_hf_lf, strict=True), start=1
    ):
        s_norm = strength / max_kept_strength if max_kept_strength > 0 else 0.0
        p_norm = peak / max_kept_peak if max_kept_peak > 0 else 0.0
        hf_norm = hf_lf / max_kept_hf_lf if max_kept_hf_lf > 0 else 0.0
        confidence = float(np.clip((s_norm * p_norm * hf_norm) ** (1.0 / 3.0), 0.0, 1.0))
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


def _leading_edge(audio: np.ndarray, onset_t: float, sr: int) -> float:
    """Return the rise-foot leading edge of the transient surrounding ``onset_t``.

    The window extends up to 100 ms BEFORE the librosa frame (so we can find
    the foot even if librosa fired well into the rise) and 30 ms after. Peak
    search is anchored to ``[onset - 5 ms, onset + 30 ms]`` to lock onto the
    transient librosa flagged. Walking backward from the peak stops when the
    envelope either:
    * drops below ``_RISE_FOOT_FRAC * peak`` (silence at the foot), or
    * rises again (we'd be entering an earlier transient).
    """
    onset_idx = int(round(onset_t * sr))
    win_lo = max(0, onset_idx - int(_LEAD_EDGE_BACKWARD_MAX_S * sr))
    win_hi = min(audio.size, onset_idx + int(_LEAD_EDGE_PEAK_WINDOW_S * sr))
    if win_hi <= win_lo:
        return onset_t

    raw = np.abs(audio[win_lo:win_hi]).astype(np.float32)
    if raw.size == 0:
        return onset_t

    smooth_n = max(1, int(round(sr * _LEAD_EDGE_SMOOTH_S)))
    envelope = maximum_filter1d(raw, size=smooth_n, mode="nearest")

    peak_search_lo_abs = max(win_lo, onset_idx - int(_LEAD_EDGE_PRE_PAD_S * sr))
    peak_search_lo = peak_search_lo_abs - win_lo
    if peak_search_lo >= envelope.size:
        return onset_t
    peak_local = peak_search_lo + int(np.argmax(envelope[peak_search_lo:]))
    peak = float(envelope[peak_local])
    if peak <= 0.0:
        return onset_t
    foot_threshold = peak * _RISE_FOOT_FRAC

    # Walk back from the peak. Stop when the envelope drops below the foot
    # threshold (silence) OR climbs back above the running minimum by more
    # than _RISE_BACK_FACTOR (we've descended into the rise's foot and are
    # now climbing into a previous transient). The running-minimum check
    # only engages after _LEAD_EDGE_RISE_GUARD_MIN_S so the walk has time
    # to actually reach the foot before any rise can stop it.
    rise_guard_samples = max(1, int(round(sr * _LEAD_EDGE_RISE_GUARD_MIN_S)))
    i = peak_local
    min_so_far = float(envelope[i])
    min_pos = i
    while i > 0:
        prev = float(envelope[i - 1])
        if prev < foot_threshold:
            break
        if prev < min_so_far:
            min_so_far = prev
            min_pos = i - 1
        elif (
            (peak_local - i) >= rise_guard_samples
            and prev > min_so_far * _RISE_BACK_FACTOR
        ):
            i = min_pos
            break
        i -= 1
    return (win_lo + i) / sr


def _hf_lf_ratio(audio: np.ndarray, t: float, sr: int) -> float:
    """Ratio of HF (4-12 kHz) to LF (0-2 kHz) power in a 30 ms window at ``t``.

    On-mic shots are broadband transients with strong HF content; other-bay
    shots and ambient thumps have relatively more LF, since high frequencies
    decay faster in air. Returned as a non-negative float; ``0.0`` for empty
    or all-silent windows.
    """
    half = int(round(sr * _HF_LF_WINDOW_S / 2.0))
    centre = int(round(t * sr))
    lo = max(0, centre - half)
    hi = min(audio.size, centre + half)
    if hi - lo < 2:
        return 0.0
    window = audio[lo:hi].astype(np.float32)
    # Hann window to reduce spectral leakage of the asymmetric burst shape.
    spectrum = np.fft.rfft(window * np.hanning(window.size))
    power = (spectrum.real**2 + spectrum.imag**2).astype(np.float64)
    freqs = np.fft.rfftfreq(window.size, d=1.0 / sr)
    lf = power[(freqs >= _HF_LF_LOW_BAND_HZ[0]) & (freqs < _HF_LF_LOW_BAND_HZ[1])].sum()
    hf = power[(freqs >= _HF_LF_HIGH_BAND_HZ[0]) & (freqs < _HF_LF_HIGH_BAND_HZ[1])].sum()
    return float(hf / (lf + _HF_LF_EPS))


def _ms_to_frames(ms: int, sample_rate: int) -> int:
    return max(1, int(round(ms / 1000.0 * sample_rate / _HOP_LENGTH)))


def _peak_amplitude(audio: np.ndarray, t: float, sr: int, half_win: int) -> float:
    centre = int(round(t * sr))
    lo = max(0, centre - half_win)
    hi = min(audio.size, centre + half_win)
    if hi <= lo:
        return 0.0
    return float(np.max(np.abs(audio[lo:hi])))
