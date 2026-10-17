"""splitsmith.beep_tone: the timer tone's start, in its band (spec
2026-10-17-beep-spectral-view-design)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import numpy as np
import pytest

from splitsmith import beep_detect, beep_tone

SR = 48000
FIXTURES = Path(__file__).parent / "fixtures"


def _tone(seconds: float, hz: float, start: float, length: float = 0.4) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    out = np.zeros_like(t, dtype=np.float32)
    on = (t >= start) & (t < start + length)
    out[on] = 0.5 * np.sin(2 * np.pi * hz * t[on])
    return out


def _click(seconds: float, at: float) -> np.ndarray:
    """A broadband transient (a shot's shape: sharp rise, fast decay), at a
    cross-bay shot's level: louder than the tone over the full range, far
    weaker than it inside the timer band (a shot spreads its energy)."""
    rng = np.random.default_rng(1)
    out = np.zeros(int(seconds * SR), dtype=np.float32)
    i = int(at * SR)
    n = int(0.15 * SR)
    out[i : i + n] = 0.3 * rng.standard_normal(n).astype(np.float32) * np.exp(-np.arange(n) / (0.02 * SR))
    return out


def test_band_pass_keeps_the_tone_and_drops_the_low_end() -> None:
    sig = _tone(1.0, 2800, 0.2) + 0.5 * np.sin(2 * np.pi * 200 * np.arange(SR) / SR).astype(np.float32)
    out = beep_tone.band_pass(sig, SR, 2200, 3500)
    assert np.abs(out[int(0.05 * SR) : int(0.15 * SR)]).max() < 0.05  # the 200 Hz hum is gone
    assert np.abs(out[int(0.3 * SR) : int(0.5 * SR)]).max() > 0.4  # the tone is kept


def test_a_shot_before_the_tone_moves_the_full_range_foot_not_the_band_one() -> None:
    # A click 40 ms before a 2.8 kHz tone: in the full range the rise is the
    # click's; in the timer band it is the tone's.
    sig = _tone(2.0, 2800, 1.0) + _click(2.0, 0.96)
    assert beep_tone.tone_offset_ms(sig, SR, 1.0, (2200, 3500)) == pytest.approx(0.0, abs=6)
    full = beep_tone.tone_offset_ms(sig, SR, 1.0, None)
    assert full is not None and full < -25


def test_a_wavering_tone_starts_where_it_starts() -> None:
    """A timer tone that beats and grows louder 200 ms in (Little Trouble 1,
    Bofors 5): its start is its onset, not a dip before its loudest point."""
    t = np.arange(2 * SR) / SR
    env = np.where((t >= 1.0) & (t < 1.45), 0.25 + 0.35 * (t - 1.0) / 0.45 * (0.5 + 0.5 * np.cos(2 * np.pi * 9 * (t - 1.0))), 0.0)
    sig = (env * np.sin(2 * np.pi * 2800 * t)).astype(np.float32)
    assert beep_tone.tone_offset_ms(sig, SR, 1.0, (2200, 3500)) == pytest.approx(0.0, abs=6)


def test_a_tone_before_the_beep_reads_negative() -> None:
    """Oden Cup Martin 2: the tone starts 80 ms before the confirmed beep."""
    sig = _tone(2.0, 2800, 0.92)
    assert beep_tone.tone_offset_ms(sig, SR, 1.0, (2200, 3500)) == pytest.approx(-80.0, abs=6)


def test_silence_has_no_tone() -> None:
    assert beep_tone.tone_offset_ms(np.zeros(2 * SR, dtype=np.float32), SR, 1.0, (2200, 3500)) is None


def test_window_at_the_start_of_the_file() -> None:
    sig = _tone(1.0, 2800, 0.02)
    assert beep_tone.tone_offset_ms(sig, SR, 0.02, (2200, 3500)) == pytest.approx(0.0, abs=6)
    spec = beep_tone.spectrogram(sig, SR, -0.5, 0.5)
    assert spec.start == 0.0 and spec.duration == pytest.approx(0.5, abs=0.01)


def test_spectrogram_shape_and_tone_row() -> None:
    sig = _tone(2.0, 2800, 0.5, length=1.0)
    spec = beep_tone.spectrogram(sig, SR, 0.0, 2.0)
    assert spec.hz[0] == 0.0 and spec.hz[-1] <= 8000.0
    grid = np.frombuffer(base64.b64decode(spec.data), dtype=np.uint8).reshape(spec.columns, len(spec.hz))
    assert spec.columns == pytest.approx(2.0 / 0.005, rel=0.05)
    mid = grid[spec.columns // 2]
    assert abs(spec.hz[int(mid.argmax())] - 2800) < 100


def test_default_band_is_the_detectors_tonal_band() -> None:
    from splitsmith.config import BeepDetectConfig

    cfg = BeepDetectConfig()
    assert beep_tone.default_band() == (cfg.tonal_band_lo_hz, cfg.tonal_band_hi_hz)


def test_a_cross_bay_shot_on_a_real_beep() -> None:
    """Little Trouble stage 2 (Mathias, GO 3S): the full-range scan put the
    audited beep 79 ms off its tone. In the band the audited beep is on
    the tone. If this fails, the audited beep may be wrong: stop and
    report, do not tune the test."""
    stem = "stage-shots-little-trouble-in-big-satra-2026-stage2-s97dcec94"
    wav = FIXTURES / f"{stem}.wav"
    if not wav.exists() or not (FIXTURES / f"{stem}.json").exists():
        pytest.skip("fixture not here (scripts/fixture_audio.py fetch)")
    beep = json.loads((FIXTURES / f"{stem}.json").read_text())["beep_time"]
    audio, sr = beep_detect.load_audio(wav)
    banded = beep_tone.tone_offset_ms(audio, sr, beep, beep_tone.default_band())
    full = beep_tone.tone_offset_ms(audio, sr, beep, None)
    assert banded is not None and abs(banded) < 10
    assert full is not None and abs(full) >= 40
