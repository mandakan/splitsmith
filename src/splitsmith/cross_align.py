"""Align a secondary camera's audio to a primary's beep timeline.

Pure function: takes two mono audio arrays + the primary's beep_time, returns
the best estimate of where the same moment occurs in the secondary's audio.

Why this exists:
``beep_detect`` is built for headcam-style audio with a clean, sustained
2-5 kHz buzzer tone. Many secondary cameras (iPhone tripod, RO-position
GoPro, AGC'd phone audio) don't capture a sustained tone -- the buzzer is
either too far, smeared by AGC, or the recording started after it. For
those, in-stream beep detection fails (``BeepNotFoundError``) and the user
is left to type a timestamp by hand.

But the secondary camera was filming the SAME stage in the SAME room. The
buzzer + first shots form a loudness pattern that's broadly the same in
both recordings, modulo gain / mic distance / a constant time offset. We
exploit that: cross-correlate the loudness ENVELOPE of a few seconds of
primary audio centered on the primary's beep against the secondary's
envelope, find the time-lag where they match best, and use that lag to
project the primary's beep_time into secondary time.

Why envelope, not raw signal:
- Mics differ in phase + frequency response; raw cross-correlation peaks
  smear or miss entirely.
- Envelope (low-pass-filtered |signal|, downsampled to ~200 Hz) captures
  the gross loudness shape -- silence -> beep -> pause -> shot -> pause
  -> shot -- which is what we actually want to align on.
- 200 Hz envelope means correlations run on ~vector lengths in the
  thousands, not millions; even a wide search window stays sub-second.

Confidence:
The peak correlation alone is fragile -- a flat envelope correlates well
with everything. We return ``confidence`` as the ratio of the best peak
to the second-best peak outside a small exclusion zone, which is a
peak-to-side-lobe-style metric. >= 1.5 is "trust it"; lower means the
landmark wasn't distinctive enough and the user should review.
"""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel
from scipy.signal import correlate, hilbert


class CrossAlignResult(BaseModel):
    """Output of :func:`align_secondary_to_primary`.

    ``method`` records how the offset was obtained:

    - ``"cross_correlation"`` -- inferred from envelope cross-correlation
      against the primary's beep landmark (this module's own work).
    - ``"known_beeps"`` -- caller supplied audited beep positions for
      both clips, so the offset was just arithmetic. The promote
      pipeline takes this path on the project flow.
    """

    secondary_beep_time: float
    confidence: float
    peak_correlation: float
    lag_seconds: float
    method: str = "cross_correlation"


class CrossAlignError(RuntimeError):
    """Alignment couldn't be computed (e.g. landmark window too narrow)."""


_ENVELOPE_SR = 200  # Hz. Low enough to correlate cheaply, high enough for ~5 ms resolution.


def _envelope(audio: np.ndarray, source_sr: int) -> np.ndarray:
    """Hilbert-magnitude envelope downsampled to ``_ENVELOPE_SR``.

    Mean-subtracts so cross-correlation isn't dominated by DC offset.
    """
    if audio.ndim != 1:
        raise ValueError("audio must be mono")
    env = np.abs(hilbert(audio.astype(np.float64))).astype(np.float32)
    # Downsample by averaging blocks. Cheaper than a proper polyphase
    # filter and the alignment doesn't need anti-aliasing precision --
    # we're after the gross loudness shape.
    block = max(1, source_sr // _ENVELOPE_SR)
    trimmed = (env.size // block) * block
    if trimmed == 0:
        return np.zeros(0, dtype=np.float32)
    env = env[:trimmed].reshape(-1, block).mean(axis=1)
    env = env - env.mean()
    return env.astype(np.float32, copy=False)


def align_secondary_to_primary(
    primary_audio: np.ndarray,
    primary_sr: int,
    primary_beep_time: float,
    secondary_audio: np.ndarray,
    secondary_sr: int,
    *,
    landmark_window_s: float = 4.0,
    landmark_pre_s: float = 0.5,
) -> CrossAlignResult:
    """Estimate the time at which ``primary_beep_time`` occurs in the secondary.

    Args:
        primary_audio: mono float32, the primary's full extracted audio.
        primary_sr: sample rate of primary_audio.
        primary_beep_time: timestamp of the beep in the primary, in seconds.
        secondary_audio: mono float32, the secondary's full extracted audio.
        secondary_sr: sample rate of secondary_audio.
        landmark_window_s: length of the primary excerpt used as the
            cross-correlation template, in seconds. Long enough to span
            the beep + the first 1-2 shots (the most distinctive
            part of stage audio); short enough that the secondary likely
            covers the same moment.
        landmark_pre_s: how much of the landmark sits BEFORE the beep.
            A small lead-in lets the silence-to-beep transition contribute
            to the correlation, which sharpens the peak.

    Returns:
        :class:`CrossAlignResult` with the secondary-time location of
        the primary's beep, plus diagnostic confidence.

    Raises:
        :class:`CrossAlignError` when the landmark window can't be cut
        from the primary (beep too close to the start/end), or when
        either audio is too short to be useful.
    """
    if primary_audio.ndim != 1 or secondary_audio.ndim != 1:
        raise ValueError("audio arrays must be mono")
    if primary_beep_time < 0:
        raise CrossAlignError("primary_beep_time is negative")

    primary_dur = primary_audio.size / primary_sr
    if primary_beep_time > primary_dur:
        raise CrossAlignError(
            f"primary_beep_time {primary_beep_time:.3f}s exceeds primary duration " f"{primary_dur:.3f}s"
        )

    landmark_start = primary_beep_time - landmark_pre_s
    landmark_end = landmark_start + landmark_window_s
    # Clip to the primary's actual range; the alignment still works as long
    # as the landmark is a few seconds and contains the beep.
    landmark_start = max(0.0, landmark_start)
    landmark_end = min(primary_dur, landmark_end)
    if landmark_end - landmark_start < 1.0:
        raise CrossAlignError(
            "landmark window shrank below 1 s after clipping; primary too short "
            "or beep too close to an edge"
        )

    p_lo = int(round(landmark_start * primary_sr))
    p_hi = int(round(landmark_end * primary_sr))
    template = _envelope(primary_audio[p_lo:p_hi], primary_sr)
    haystack = _envelope(secondary_audio, secondary_sr)

    if template.size < _ENVELOPE_SR or haystack.size <= template.size:
        # Need at least 1 s of template and a haystack longer than it for
        # the correlation to have somewhere to slide.
        raise CrossAlignError(
            "secondary audio is too short for cross-correlation against the " "primary landmark"
        )

    # 'valid' mode: only positions where the template fits entirely inside
    # the haystack. The lag is then the index of the peak * 1/_ENVELOPE_SR.
    corr = correlate(haystack, template, mode="valid")
    if corr.size == 0:
        raise CrossAlignError("cross-correlation produced no samples")

    # Normalize: divide by the per-position L2 norm of the haystack window
    # so a loud secondary section doesn't dominate purely on energy. We
    # avoid the full sliding-norm computation (cumsum trick) since the
    # haystack is at 200 Hz envelope rate -- an explicit loop over positions
    # would be cheap enough but cumulative-sum is even cheaper.
    sq = haystack.astype(np.float64) ** 2
    csum = np.concatenate(([0.0], np.cumsum(sq)))
    # window[i] = sum(sq[i : i + len(template)])
    win_energy = csum[template.size :] - csum[: csum.size - template.size]
    template_energy = float(np.sum(template.astype(np.float64) ** 2))
    norm = np.sqrt(np.maximum(win_energy * template_energy, 1e-12))
    corr_norm = corr / norm

    best_idx = int(np.argmax(corr_norm))
    best_corr = float(corr_norm[best_idx])

    # Peak-to-second-peak ratio with an exclusion zone around the winner so
    # we don't compare against the same peak's shoulder.
    excl_radius = max(_ENVELOPE_SR // 2, template.size // 4)  # ~0.5 s either side
    masked = corr_norm.copy()
    lo = max(0, best_idx - excl_radius)
    hi = min(masked.size, best_idx + excl_radius + 1)
    masked[lo:hi] = -np.inf
    if np.all(masked == -np.inf):
        runner_up = 0.0
    else:
        runner_up = float(np.max(masked))
    confidence = best_corr / max(abs(runner_up), 1e-6)

    # best_idx is the lag in haystack samples -> seconds via _ENVELOPE_SR.
    # That's the time in the secondary at which the template's start
    # aligns. Add the in-template offset of the beep to get the beep's
    # secondary-time.
    landmark_to_beep = primary_beep_time - landmark_start  # seconds
    secondary_beep_time = best_idx / _ENVELOPE_SR + landmark_to_beep

    return CrossAlignResult(
        secondary_beep_time=secondary_beep_time,
        confidence=confidence,
        peak_correlation=best_corr,
        lag_seconds=best_idx / _ENVELOPE_SR,
    )
