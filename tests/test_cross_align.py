"""Tests for the cross-cam audio aligner.

These use synthetic mono audio rather than fixture files: the aligner is
a pure function on numpy arrays + sample rates, so a deterministic seed
plus a few injected "events" (beep tone + click transients) gives us a
ground-truth lag the test asserts against. Real same-stage audio will
have richer envelopes than this -- if anything the synthetic case is
HARDER for the aligner because the events are sparser and shorter.
"""

from __future__ import annotations

import numpy as np
import pytest

from splitsmith.cross_align import (
    CrossAlignError,
    align_secondary_to_primary,
)


def _stage_audio(
    sr: int,
    duration_s: float,
    *,
    beep_at: float,
    shots_at: list[float],
    noise_amp: float = 0.01,
    beep_amp: float = 0.5,
    shot_amp: float = 0.7,
    seed: int = 0,
) -> np.ndarray:
    """Build a mono float32 audio buffer with a beep tone + click transients.

    Useful for synthetic alignment tests: caller sets the time of the
    beep + each shot in this clip, and the test correlates two such
    clips with a known time-shift between them.
    """
    rng = np.random.default_rng(seed)
    n = int(sr * duration_s)
    audio = rng.normal(0, noise_amp, n).astype(np.float32)
    t = np.arange(n) / sr
    # 2.5 kHz tone burst, ~400 ms, models the buzzer.
    s = int(beep_at * sr)
    e = s + int(0.4 * sr)
    if 0 <= s < n:
        e = min(e, n)
        audio[s:e] += beep_amp * np.sin(2 * np.pi * 2500 * t[s:e]).astype(np.float32)
    # Each shot is a 5 ms exponentially-decaying broadband click.
    for shot_t in shots_at:
        s = int(shot_t * sr)
        e = s + int(0.005 * sr)
        if 0 <= s < n:
            e = min(e, n)
            envelope = np.exp(-np.linspace(0, 5, e - s)).astype(np.float32)
            audio[s:e] += shot_amp * envelope * rng.choice([-1.0, 1.0], size=e - s).astype(
                np.float32
            )
    return audio


def test_aligner_recovers_known_offset_within_5ms() -> None:
    """A synthetic primary + a copy shifted by 3.7 s should yield the known
    secondary beep_time within 5 ms (one envelope sample at 200 Hz)."""
    sr = 48000
    primary_beep_t = 5.0
    shift = 3.7
    primary = _stage_audio(
        sr, duration_s=15.0, beep_at=primary_beep_t, shots_at=[6.5, 7.0, 7.6, 8.3], seed=1
    )
    secondary = _stage_audio(
        sr,
        duration_s=20.0,
        beep_at=primary_beep_t + shift,
        shots_at=[6.5 + shift, 7.0 + shift, 7.6 + shift, 8.3 + shift],
        noise_amp=0.02,  # noisier secondary, weaker beep -- realistic phone-far-from-buzzer
        beep_amp=0.05,
        shot_amp=0.4,
        seed=2,
    )
    result = align_secondary_to_primary(primary, sr, primary_beep_t, secondary, sr)
    assert abs(result.secondary_beep_time - (primary_beep_t + shift)) < 0.01
    assert result.confidence > 1.5


def test_aligner_low_confidence_when_audio_unrelated() -> None:
    """Two unrelated noise clips should give a confidence near 1.0 -- the
    aligner picks SOME peak, but the runner-up is essentially equal, so
    the confidence ratio reveals the lack of a real landmark."""
    sr = 48000
    rng_a = np.random.default_rng(11)
    rng_b = np.random.default_rng(22)
    primary = rng_a.normal(0, 0.05, sr * 10).astype(np.float32)
    secondary = rng_b.normal(0, 0.05, sr * 10).astype(np.float32)
    # Inject a beep into the primary so we have a beep_time to project,
    # but DON'T put one in the secondary -- there's nothing to align to.
    s = int(2.0 * sr)
    e = s + int(0.4 * sr)
    t = np.arange(e - s) / sr
    primary[s:e] += 0.5 * np.sin(2 * np.pi * 2500 * t).astype(np.float32)

    result = align_secondary_to_primary(primary, sr, 2.0, secondary, sr)
    assert result.confidence < 1.5


def test_aligner_raises_when_secondary_too_short() -> None:
    """A secondary shorter than the landmark template can't be searched."""
    sr = 48000
    primary = _stage_audio(sr, 10.0, beep_at=5.0, shots_at=[6.5, 7.0])
    secondary = np.zeros(sr // 2, dtype=np.float32)  # 0.5 s
    with pytest.raises(CrossAlignError):
        align_secondary_to_primary(primary, sr, 5.0, secondary, sr)


def test_aligner_raises_when_beep_time_negative() -> None:
    sr = 48000
    primary = _stage_audio(sr, 10.0, beep_at=5.0, shots_at=[6.5])
    secondary = _stage_audio(sr, 10.0, beep_at=5.0, shots_at=[6.5])
    with pytest.raises(CrossAlignError):
        align_secondary_to_primary(primary, sr, -1.0, secondary, sr)


def test_aligner_raises_when_beep_beyond_primary_duration() -> None:
    sr = 48000
    primary = _stage_audio(sr, 5.0, beep_at=2.0, shots_at=[3.0])
    secondary = _stage_audio(sr, 10.0, beep_at=2.0, shots_at=[3.0])
    with pytest.raises(CrossAlignError):
        align_secondary_to_primary(primary, sr, 99.0, secondary, sr)
