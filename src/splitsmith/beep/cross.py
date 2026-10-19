"""Find a stage's beep in one camera from the beep confirmed in another (#1442).

The confirmed camera knows when the beep was and what the timer sounds like.
Two steps, each pure over audio arrays:

1. **The stage's shots** (:func:`stage_match`): the confirmed clip's onset
   pattern, from just before its beep through the stage, slid over the other
   clip's; seconds of shots pin the offset. The 4 s envelope landmark around
   the beep that ``cross_align`` uses locked onto the wrong shot on 32 of
   194 corpus pairs (seconds off); this on 9, all with a contrast under 1.2.
2. **The timer's tone** (:func:`line_onset_near`): where the confirmed
   timer's own frequency starts in the other clip near that projection, which
   is the other camera's own arrival of the beep (later than the confirmed
   camera's by the distance between them, so the beep is found, not shifted).

On the 2026-10-18 corpus (77 stages with reviewed beeps on two or more
angles, 194 ordered pairs; ``scripts/eval_beep_from_angle.py``): with a
contrast of at least :data:`MIN_CONTRAST`, 168 pairs, none placed more than
50 ms off, 128 within 5 ms of the reviewed beep. A template of the confirmed
tone's envelope matched near the projection did worse than the projection
(a 12 to 20 ms early bias: it lines up the tone's shape, not its start).
"""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel
from scipy.signal import correlate

from . import tone as beep_tone

#: The onset envelope's rate: 1 ms steps.
_ONSET_SR = 1000
#: Lead-in before the confirmed beep kept in the stage pattern.
_STAGE_PRE_S = 0.5
#: The stage pattern's length after the beep, as much as the clip holds.
_STAGE_SPAN_S = 20.0
#: A match must beat the best one more than 50 ms away by this much to be
#: trusted: below it, 9 of 26 corpus pairs were seconds off; at or above,
#: none of 168.
MIN_CONTRAST = 1.2
#: The tone's start replaces the shots' projection only this close to it:
#: every confident corpus pair more than 50 ms off had a tone 75 to 225 ms
#: from a projection that was itself within 50 ms.
MAX_TONE_SHIFT_S = 0.06


def _onsets(audio: np.ndarray, sr: int) -> np.ndarray:
    """Rises of the log block-max envelope at 1 kHz, mean removed: where
    sounds start, whatever their level (a far camera hears the same shots
    quieter, so levels never match but starts do)."""
    step = max(1, int(round(sr / _ONSET_SR)))
    n = audio.size // step
    if n < 2:
        return np.zeros(0)
    env = np.abs(audio[: n * step].astype(np.float64)).reshape(n, step).max(axis=1)
    env = np.log1p(env / (float(np.median(env)) + 1e-9))
    rise = np.maximum(np.diff(env, prepend=env[0]), 0.0)
    rise = np.convolve(rise, np.ones(3) / 3, mode="same")
    return rise - rise.mean()


class StageMatch(BaseModel):
    """Where the confirmed beep falls in the other clip by the stage's shots."""

    beep: float
    #: The best normalised match over the best one more than 50 ms away;
    #: about 1 means no clear answer.
    contrast: float
    score: float


def stage_match(
    ref_audio: np.ndarray,
    ref_sr: int,
    ref_beep: float,
    other_audio: np.ndarray,
    other_sr: int,
    *,
    span_s: float = _STAGE_SPAN_S,
) -> StageMatch | None:
    """Slide the confirmed clip's onset pattern (``_STAGE_PRE_S`` before its
    beep to ``span_s`` after) over the other clip's and take the best
    normalised match. The other clip is padded with silence, so a camera that
    started after the beep or stopped early still aligns, but at least half
    the pattern must lie on its audio. ``None`` when either is too short."""
    start = max(0.0, ref_beep - _STAGE_PRE_S)
    end = min(ref_audio.size / ref_sr, ref_beep + span_s)
    if end - start < 1.0:
        return None
    ref = _onsets(ref_audio[int(start * ref_sr) : int(end * ref_sr)], ref_sr)
    other = _onsets(other_audio, other_sr)
    norm_r = float(np.linalg.norm(ref))
    if ref.size < 200 or other.size < 200 or norm_r == 0:
        return None
    pad = ref.size
    hay = np.concatenate([np.zeros(pad), other, np.zeros(pad)])
    corr = correlate(hay, ref, mode="valid", method="fft")
    csq = np.cumsum(np.concatenate([[0.0], hay**2]))
    win_norm = np.sqrt(np.maximum(csq[ref.size :] - csq[: -ref.size], 1e-12))
    score = corr / (norm_r * win_norm)
    idx = np.arange(score.size)
    overlap = np.minimum(idx + ref.size, pad + other.size) - np.maximum(idx, pad)
    score = np.where(overlap >= ref.size // 2, score, -np.inf)
    if not np.isfinite(score).any():
        return None
    best = int(np.argmax(score))
    far = (np.abs(idx - best) > 50) & np.isfinite(score)
    runner_up = float(score[far].max()) if far.any() else 0.0
    contrast = float(score[best] / runner_up) if runner_up > 0 else float("inf")
    # ``best`` puts the pattern's first sample at other-clip time
    # (best - pad) / _ONSET_SR; the beep sits ``ref_beep - start`` into it.
    beep = (best - pad) / _ONSET_SR + (ref_beep - start)
    return StageMatch(beep=beep, contrast=contrast, score=float(score[best]))


def line_onset_near(
    ref_audio: np.ndarray,
    ref_sr: int,
    ref_beep: float,
    other_audio: np.ndarray,
    other_sr: int,
    near_s: float,
) -> float | None:
    """Where the confirmed timer's own frequency (``tone_line_hz`` at the
    confirmed beep) starts in the other clip within reach of ``near_s``
    (``beep_tone._line_onset``), refined to its band's sustained rise
    (``tone_offset_ms``) when that is close. ``None`` without a line."""
    hz = beep_tone.tone_line_hz(ref_audio, ref_sr, ref_beep)
    if hz is None:
        return None
    # The fundamental only: the earliest of its harmonics fixed Tallmilan 7
    # on an iPhone (1.6 kHz read 200 ms late, 3.2 kHz on time) but took a
    # stray line 294 ms early on Stockholm 11's Samsung; a wash on the corpus.
    onset = beep_tone._line_onset(other_audio, other_sr, near_s, hz)
    if onset is None:
        return None
    band = (hz - beep_tone._TONE_HALF_HZ, hz + beep_tone._TONE_HALF_HZ)
    off = beep_tone.tone_offset_ms(other_audio, other_sr, onset, band)
    if off is not None and abs(off) <= beep_tone._ONSET_KEEP_S * 1000:
        return onset + off / 1000.0
    return onset


class BeepPlacement(BaseModel):
    """The confirmed beep found in another clip, seconds in that clip."""

    #: Where the stage's shots put it.
    projected: float
    contrast: float
    #: Where the timer's tone starts near ``projected``; ``None`` without it.
    tone: float | None
    #: ``tone`` when found, else ``projected``.
    time: float

    @property
    def confident(self) -> bool:
        """The shots pinned the offset (:data:`MIN_CONTRAST`)."""
        return self.contrast >= MIN_CONTRAST


def place_beep(
    ref_audio: np.ndarray,
    ref_sr: int,
    ref_beep: float,
    other_audio: np.ndarray,
    other_sr: int,
) -> BeepPlacement | None:
    """The beep confirmed at ``ref_beep`` in the reference clip, found in the
    other clip; ``None`` when the clips are too short to match."""
    match = stage_match(ref_audio, ref_sr, ref_beep, other_audio, other_sr)
    if match is None:
        return None
    tone = line_onset_near(ref_audio, ref_sr, ref_beep, other_audio, other_sr, match.beep)
    # The shots put the beep within a few tens of ms; a tone found further
    # off is another sound on the timer's frequency or a line that read late.
    agrees = tone is not None and abs(tone - match.beep) <= MAX_TONE_SHIFT_S
    return BeepPlacement(
        projected=match.beep,
        contrast=match.contrast,
        tone=tone,
        time=tone if agrees and tone is not None else match.beep,
    )


__all__ = ["MIN_CONTRAST", "BeepPlacement", "StageMatch", "line_onset_near", "place_beep", "stage_match"]
