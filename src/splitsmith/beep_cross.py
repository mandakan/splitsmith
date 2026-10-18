"""Find a stage's beep in one camera from the beep confirmed in another (#1442).

The confirmed camera's own beep is the best template there is for the beep
in every other angle of the same stage: the same timer, the same tone. Three
steps, each pure over audio arrays:

1. **Coarse:** project the confirmed beep into the other clip with the
   envelope cross-correlation ``cross_align`` already does (the beep and the
   first shots, about 5 ms resolution).
2. **Template:** the confirmed beep's tone, band-passed to its own band
   (``beep_tone.tone_band``), as a fine envelope; matched against the other
   clip's same band within ``reach_s`` of the coarse position. The best match
   lines up the two tones' onsets, which is that camera's own arrival of the
   beep (later than the confirmed camera's by the distance between them,
   which is why the beep is found, not shifted).
3. **Tone:** ``beep_tone.tone_onset`` moves the match onto the tone's start
   when it is clearly off it, as the detector does.

Measured on the corpus by ``scripts/eval_beep_from_angle.py``; nothing in
the product reads it yet.
"""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel
from scipy.signal import correlate, hilbert

from . import beep_tone
from .cross_align import CrossAlignError, align_secondary_to_primary

#: The fine envelope's rate: 1 ms steps for the template match.
_FINE_SR = 1000
#: How much of the confirmed beep's tone the template holds, and how much
#: lead-in (the silence before the tone is what pins the onset).
_TEMPLATE_PRE_S = 0.05
_TEMPLATE_POST_S = 0.25


class BeepPlacement(BaseModel):
    """Where the confirmed beep lands in the other clip, seconds."""

    #: The envelope projection alone.
    coarse: float
    coarse_confidence: float
    #: The template's best match near ``coarse``; ``None`` when the clip has
    #: no room for it.
    matched: float | None
    #: Normalised correlation of that match, 0 to 1.
    match_score: float | None
    #: ``matched`` moved onto the tone's start when it was clearly off it.
    time: float


def _fine_envelope(audio: np.ndarray, sr: int, band: tuple[float, float]) -> np.ndarray:
    filtered = beep_tone.band_pass(audio.astype(np.float64), sr, band[0], band[1])
    env = np.abs(hilbert(filtered))
    step = max(1, int(round(sr / _FINE_SR)))
    n = env.size // step
    return env[: n * step].reshape(n, step).mean(axis=1) if n else np.zeros(0)


def _slice(audio: np.ndarray, sr: int, start_s: float, end_s: float) -> tuple[np.ndarray, float]:
    lo = max(0, int(round(start_s * sr)))
    hi = min(audio.size, int(round(end_s * sr)))
    return audio[lo:hi], lo / sr


def match_template(
    ref_audio: np.ndarray,
    ref_sr: int,
    ref_beep: float,
    other_audio: np.ndarray,
    other_sr: int,
    near_s: float,
    *,
    reach_s: float = 0.3,
) -> tuple[float, float] | None:
    """The other clip's time whose tone onset lines up with the reference's
    beep, searched within ``reach_s`` of ``near_s``: ``(time, score)``, or
    ``None`` when either window is cut off."""
    band = beep_tone.tone_band(ref_audio, ref_sr, ref_beep)
    ref_win, ref_t0 = _slice(ref_audio, ref_sr, ref_beep - _TEMPLATE_PRE_S, ref_beep + _TEMPLATE_POST_S)
    other_win, other_t0 = _slice(
        other_audio, other_sr, near_s - reach_s - _TEMPLATE_PRE_S, near_s + reach_s + _TEMPLATE_POST_S
    )
    template = _fine_envelope(ref_win, ref_sr, band)
    hay = _fine_envelope(other_win, other_sr, band)
    if template.size < 50 or hay.size <= template.size:
        return None
    template = template - template.mean()
    norm_t = np.linalg.norm(template)
    if norm_t == 0:
        return None
    corr = correlate(hay, template, mode="valid")
    # Normalise per position so a loud stretch does not win on energy.
    win = template.size
    csum = np.cumsum(np.concatenate([[0.0], hay]))
    csq = np.cumsum(np.concatenate([[0.0], hay**2]))
    sums = csum[win:] - csum[:-win]
    sq = csq[win:] - csq[:-win]
    var = np.maximum(sq - sums**2 / win, 1e-12)
    score = corr / (norm_t * np.sqrt(var))
    best = int(np.argmax(score))
    # Index 0 puts the template's first sample at the window's start; the
    # beep sits _TEMPLATE_PRE_S into the template.
    t = other_t0 + best / _FINE_SR + (ref_beep - ref_t0)
    return t, float(score[best])


def line_onset_near(
    ref_audio: np.ndarray,
    ref_sr: int,
    ref_beep: float,
    other_audio: np.ndarray,
    other_sr: int,
    near_s: float,
) -> float | None:
    """Where the reference timer's own frequency starts in the other clip,
    near ``near_s``: the line at ``tone_line_hz`` of the confirmed beep
    (``beep_tone._line_onset``), refined to its band's sustained rise
    (``tone_offset_ms``) when that is close. ``None`` without a line."""
    hz = beep_tone.tone_line_hz(ref_audio, ref_sr, ref_beep)
    if hz is None:
        return None
    # The fundamental only: taking the earliest of its harmonics fixed
    # Tallmilan 7 on an iPhone (1.6 kHz read 200 ms late, 3.2 kHz on time)
    # but took a stray line 294 ms early on Stockholm 11's Samsung; on the
    # corpus it was a wash (131 against 132 pairs within 5 ms).
    onset = beep_tone._line_onset(other_audio, other_sr, near_s, hz)
    if onset is None:
        return None
    band = (hz - beep_tone._TONE_HALF_HZ, hz + beep_tone._TONE_HALF_HZ)
    off = beep_tone.tone_offset_ms(other_audio, other_sr, onset, band)
    if off is not None and abs(off) <= beep_tone._ONSET_KEEP_S * 1000:
        return onset + off / 1000.0
    return onset


def place_beep(
    ref_audio: np.ndarray,
    ref_sr: int,
    ref_beep: float,
    other_audio: np.ndarray,
    other_sr: int,
    *,
    reach_s: float = 0.3,
) -> BeepPlacement:
    """The beep confirmed at ``ref_beep`` in the reference clip, found in the
    other clip. Raises ``CrossAlignError`` when the coarse projection fails."""
    coarse = align_secondary_to_primary(ref_audio, ref_sr, ref_beep, other_audio, other_sr)
    matched = match_template(
        ref_audio, ref_sr, ref_beep, other_audio, other_sr, coarse.secondary_beep_time, reach_s=reach_s
    )
    at = matched[0] if matched is not None else coarse.secondary_beep_time
    onset = (
        beep_tone.tone_onset(other_audio, other_sr, at) if 0 <= at <= other_audio.size / other_sr else None
    )
    return BeepPlacement(
        coarse=coarse.secondary_beep_time,
        coarse_confidence=coarse.confidence,
        matched=matched[0] if matched is not None else None,
        match_score=matched[1] if matched is not None else None,
        time=onset if onset is not None else at,
    )


__all__ = ["BeepPlacement", "CrossAlignError", "match_template", "place_beep"]
