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
        audio[int(sr * at_s) : int(sr * at_s) + n] += amp * np.sin(2 * np.pi * 3000 * t).astype(np.float32)

    _stamp(1.0, 0.6)  # the "real" beep -- preceded by ~1 s of silence
    _stamp(3.0, 0.4)  # competing transient further into the clip

    result = detect_beep(audio, sr, BeepDetectConfig())
    assert len(result.candidates) >= 2
    # Sorted by score, descending.
    scores = [c.score for c in result.candidates]
    assert scores == sorted(scores, reverse=True)
    # Winner == candidates[0].
    assert result.time == pytest.approx(result.candidates[0].time, abs=1e-9)
    assert result.peak_amplitude == pytest.approx(result.candidates[0].peak_amplitude, abs=1e-9)
    assert result.duration_ms == pytest.approx(result.candidates[0].duration_ms, abs=1e-9)


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


def test_detect_beep_recovers_faint_beep_below_legacy_threshold() -> None:
    """Faint tone (peak ~0.03 in [-1, 1]) should still be detected.

    The legacy detector required an absolute envelope peak of 0.04 + a
    fraction of global peak; a real iPhone beep at peak 0.025 fell below
    both and silently failed. The noise-floor-relative cutoff in
    ``BeepDetectConfig`` lets the candidate qualify on SNR, not absolute
    loudness.
    """
    sr = 48000
    rng = np.random.default_rng(13)
    audio = (rng.standard_normal(sr * 6) * 0.001).astype(np.float32)
    insert_at = int(sr * 3.0)
    duration = int(sr * 0.350)
    t = np.arange(duration) / sr
    # Ramp envelope so the beep onset is visibly faint, peak 0.03.
    ramp = np.minimum(np.linspace(0.0, 1.0, duration), 1.0)
    tone = 0.03 * ramp * np.sin(2 * np.pi * 2700 * t).astype(np.float32)
    audio[insert_at : insert_at + duration] += tone

    result = detect_beep(audio, sr, BeepDetectConfig())
    assert result.time == pytest.approx(insert_at / sr, abs=0.030)
    assert result.peak_amplitude < 0.04, "test must exercise the sub-legacy-threshold path"


def test_detect_beep_demotes_short_transient_in_favor_of_sustained_tone() -> None:
    """A 150 ms shot-shaped transient should not outrank a 350 ms tone.

    Real-world failure mode: stage shots have brief quiet pre-windows
    (between mag swaps) and bandpass envelope > 0.05, so silence-
    preference can crown them over the actual beep. The duration prior
    + tonal scoring is what keeps the real beep on top.
    """
    sr = 48000
    rng = np.random.default_rng(31)
    audio = (rng.standard_normal(sr * 8) * 0.001).astype(np.float32)

    # Pure tone (the "beep") at t=2 s, 350 ms, modest amplitude.
    beep_at = int(sr * 2.0)
    beep_dur = int(sr * 0.350)
    bt = np.arange(beep_dur) / sr
    audio[beep_at : beep_at + beep_dur] += 0.08 * np.sin(2 * np.pi * 2700 * bt).astype(np.float32)

    # Broadband short transient (the "shot") at t=5 s, 150 ms, slightly
    # louder than the beep -- preceded by 1.5 s of total silence so its
    # silence-preference score blows up under the legacy detector.
    shot_at = int(sr * 5.0)
    shot_dur = int(sr * 0.150)
    audio[shot_at : shot_at + shot_dur] += (rng.standard_normal(shot_dur) * 0.10).astype(np.float32)

    result = detect_beep(audio, sr, BeepDetectConfig())
    # Top-1 must be the real beep, not the shot.
    assert result.time == pytest.approx(beep_at / sr, abs=0.030)


def test_detect_beep_handles_truncated_pre_window_without_blowup() -> None:
    """Candidates near t=0 must not get a degenerate silence score.

    Without the ``min_pre_window_s`` clamp, a transient that fires
    at t=0.02 s gets ``peak / 0`` -- effectively infinity -- and beats
    the actual beep. The detector's neutral-fallback rule is what
    prevents this.
    """
    sr = 48000
    rng = np.random.default_rng(57)
    audio = (rng.standard_normal(sr * 6) * 0.001).astype(np.float32)

    # Edge transient at t=0.02 s, 250 ms tone
    edge_at = int(sr * 0.02)
    edge_dur = int(sr * 0.250)
    et = np.arange(edge_dur) / sr
    audio[edge_at : edge_at + edge_dur] += 0.04 * np.sin(2 * np.pi * 2700 * et).astype(np.float32)

    # Real beep at t=3 s, 400 ms tone (longer + louder = should win)
    beep_at = int(sr * 3.0)
    beep_dur = int(sr * 0.400)
    bt = np.arange(beep_dur) / sr
    audio[beep_at : beep_at + beep_dur] += 0.06 * np.sin(2 * np.pi * 2700 * bt).astype(np.float32)

    result = detect_beep(audio, sr, BeepDetectConfig())
    assert result.time == pytest.approx(beep_at / sr, abs=0.030)


def test_detect_beep_emits_silence_and_tonal_diagnostic_scores() -> None:
    """Layer-3 (HITL / confidence) needs the underlying score components."""
    sr = 48000
    rng = np.random.default_rng(101)
    audio = (rng.standard_normal(sr * 4) * 0.001).astype(np.float32)
    n = int(sr * 0.350)
    t = np.arange(n) / sr
    audio[sr : sr + n] += 0.1 * np.sin(2 * np.pi * 2700 * t).astype(np.float32)

    result = detect_beep(audio, sr, BeepDetectConfig())
    assert result.candidates
    winner = result.candidates[0]
    assert winner.silence_score > 0.0
    assert 0.0 <= winner.tonal_score <= 1.0
    # Pure tone should land near tonal_score 1.0 (energy concentrated in
    # the IPSC band).
    assert winner.tonal_score > 0.8


def test_candidate_confidence_high_for_perfect_winner() -> None:
    """Pure-tone winner with no contender should land in the auto-trust band.

    Anchors the calibration evidence: if this drops below 0.7 a layer-2
    detector tweak skewed the formula and the HITL gate (#219) needs
    re-tuning.
    """
    from splitsmith.beep_detect import candidate_confidence

    conf = candidate_confidence(
        silence_score=20.0,
        tonal_score=1.0,
        duration_ms=400.0,
        score=10.0,
        runner_up_score=0.0,
    )
    assert conf >= 0.95


def test_candidate_confidence_drops_when_runner_up_ties() -> None:
    """A near-tie with the runner-up demotes the winner into HITL territory."""
    from splitsmith.beep_detect import candidate_confidence

    conf = candidate_confidence(
        silence_score=20.0,
        tonal_score=1.0,
        duration_ms=400.0,
        score=10.0,
        runner_up_score=10.0,  # tie -> margin = 0
    )
    # Margin tilt should still leave some quality signal but well below
    # the auto-trust threshold (#219 default 0.6).
    assert 0.5 <= conf <= 0.65


def test_candidate_confidence_low_for_short_broadband_transient() -> None:
    """Gunshot-shaped runs should land below the HITL threshold."""
    from splitsmith.beep_detect import candidate_confidence

    conf = candidate_confidence(
        silence_score=2.0,
        tonal_score=0.4,
        duration_ms=160.0,
        score=1.0,
        runner_up_score=0.5,
    )
    assert conf < 0.4


def test_candidate_confidence_clamps_to_unit_interval() -> None:
    from splitsmith.beep_detect import candidate_confidence

    # Absurdly high silence + perfect tonal + ideal duration shouldn't
    # exceed 1.0; negative inputs shouldn't go below 0.0.
    high = candidate_confidence(
        silence_score=10_000.0,
        tonal_score=1.0,
        duration_ms=10_000.0,
        score=1.0,
        runner_up_score=0.0,
    )
    low = candidate_confidence(
        silence_score=-5.0,
        tonal_score=-0.5,
        duration_ms=10.0,
        score=0.0,
        runner_up_score=0.0,
    )
    assert 0.0 <= high <= 1.0
    assert low == 0.0


def test_detect_beep_populates_confidence_on_winner_and_detection() -> None:
    """End-to-end: ``BeepDetection.confidence`` mirrors candidates[0].confidence."""
    sr = 48000
    rng = np.random.default_rng(202)
    audio = (rng.standard_normal(sr * 4) * 0.001).astype(np.float32)
    n = int(sr * 0.400)
    t = np.arange(n) / sr
    audio[sr : sr + n] += 0.15 * np.sin(2 * np.pi * 2700 * t).astype(np.float32)

    result = detect_beep(audio, sr, BeepDetectConfig())
    assert 0.0 <= result.confidence <= 1.0
    assert result.confidence == pytest.approx(result.candidates[0].confidence, abs=1e-9)
    # Synthetic clean tone with no contender -- expect auto-trust band.
    assert result.confidence >= 0.7
