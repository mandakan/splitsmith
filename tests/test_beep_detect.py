"""Fixture-based tests for beep_detect."""

import json
from pathlib import Path

import numpy as np
import pytest

from splitsmith.beep_detect import BeepNotFoundError, detect_beep, load_audio
from splitsmith.config import BeepDetectConfig


def _load_fixture(fixtures_dir: Path, stem: str) -> tuple[np.ndarray, int, dict]:
    audio, sr = load_audio(fixtures_dir / f"{stem}.wav")
    truth = json.loads((fixtures_dir / f"{stem}.json").read_text())
    return audio, sr, truth


def test_detect_beep_on_real_recording(fixtures_dir: Path) -> None:
    audio, sr, truth = _load_fixture(fixtures_dir, "beep-test")
    result = detect_beep(audio, sr, BeepDetectConfig())
    tol_s = truth["tolerance_ms"] / 1000.0
    assert result.time == pytest.approx(truth["beep_time"], abs=tol_s), (
        f"detected {result.time:.4f}s, ground truth {truth['beep_time']:.4f}s "
        f"(tolerance {truth['tolerance_ms']} ms)"
    )
    # The Tallmilan timer beep is ~400 ms long; detected duration should be in that ballpark.
    assert 200.0 <= result.duration_ms <= 600.0
    assert result.peak_amplitude > 0.0


def test_detect_beep_raises_on_silence() -> None:
    sr = 48000
    audio = np.zeros(sr * 2, dtype=np.float32)
    with pytest.raises(BeepNotFoundError):
        detect_beep(audio, sr, BeepDetectConfig())


def test_detect_beep_raises_on_no_sustained_tone() -> None:
    sr = 48000
    rng = np.random.default_rng(0)
    audio = (rng.standard_normal(sr * 3) * 0.01).astype(np.float32)
    # Add a very short (10 ms) 3 kHz tone -- shorter than min_duration_ms (150 ms).
    t = np.arange(int(sr * 0.010)) / sr
    audio[sr : sr + t.size] += 0.5 * np.sin(2 * np.pi * 3000 * t).astype(np.float32)
    with pytest.raises(BeepNotFoundError):
        detect_beep(audio, sr, BeepDetectConfig())


def test_detect_beep_synthetic_clean_tone() -> None:
    """Synthetic 300 ms / 3 kHz tone in a quiet 3 s buffer; leading edge should land
    within a few ms of the insertion point."""
    sr = 48000
    rng = np.random.default_rng(42)
    audio = (rng.standard_normal(sr * 3) * 0.001).astype(np.float32)  # very low noise
    insert_at = int(sr * 1.0)
    duration = int(sr * 0.300)
    t = np.arange(duration) / sr
    tone = 0.6 * np.sin(2 * np.pi * 3000 * t).astype(np.float32)
    audio[insert_at : insert_at + duration] += tone

    result = detect_beep(audio, sr, BeepDetectConfig())
    assert result.time == pytest.approx(insert_at / sr, abs=0.015)
    assert 250.0 <= result.duration_ms <= 350.0


def test_detect_beep_returns_ranked_candidates() -> None:
    """Two synthetic beeps in one buffer; candidates should be sorted by
    silence-preference score (descending) and the winner should match
    candidates[0]."""
    sr = 48000
    rng = np.random.default_rng(7)
    audio = (rng.standard_normal(sr * 6) * 0.001).astype(np.float32)

    def _stamp(at_s: float, amp: float, dur_s: float = 0.300) -> None:
        n = int(sr * dur_s)
        t = np.arange(n) / sr
        audio[int(sr * at_s) : int(sr * at_s) + n] += (
            amp * np.sin(2 * np.pi * 3000 * t).astype(np.float32)
        )

    _stamp(1.0, 0.6)  # the "real" beep -- preceded by ~1 s of silence
    _stamp(3.0, 0.4)  # competing transient further into the clip

    result = detect_beep(audio, sr, BeepDetectConfig())
    assert len(result.candidates) >= 2
    # Sorted by score, descending.
    scores = [c.score for c in result.candidates]
    assert scores == sorted(scores, reverse=True)
    # Winner == candidates[0].
    assert result.time == pytest.approx(result.candidates[0].time, abs=1e-9)
    assert result.peak_amplitude == pytest.approx(
        result.candidates[0].peak_amplitude, abs=1e-9
    )
    assert result.duration_ms == pytest.approx(
        result.candidates[0].duration_ms, abs=1e-9
    )


def test_detect_beep_top_n_zero_returns_only_winner() -> None:
    sr = 48000
    rng = np.random.default_rng(11)
    audio = (rng.standard_normal(sr * 3) * 0.001).astype(np.float32)
    n = int(sr * 0.300)
    t = np.arange(n) / sr
    audio[sr : sr + n] += 0.6 * np.sin(2 * np.pi * 3000 * t).astype(np.float32)

    cfg = BeepDetectConfig(top_n_candidates=0)
    result = detect_beep(audio, sr, cfg)
    assert len(result.candidates) == 1


def test_detect_beep_rejects_2d_input() -> None:
    sr = 48000
    audio = np.zeros((sr, 2), dtype=np.float32)
    with pytest.raises(ValueError, match="1-D"):
        detect_beep(audio, sr, BeepDetectConfig())


def test_detect_beep_rejects_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        detect_beep(np.array([], dtype=np.float32), 48000, BeepDetectConfig())
