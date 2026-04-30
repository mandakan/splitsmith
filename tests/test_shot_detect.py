"""Fixture-based tests for shot_detect."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from splitsmith.beep_detect import load_audio
from splitsmith.config import ShotDetectConfig
from splitsmith.shot_detect import detect_shots


def _load_fixture(fixtures_dir: Path, stem: str = "stage-shots") -> tuple[np.ndarray, int, dict]:
    audio, sr = load_audio(fixtures_dir / f"{stem}.wav")
    truth = json.loads((fixtures_dir / f"{stem}.json").read_text())
    return audio, sr, truth


@pytest.mark.parametrize(
    "fixture_stem",
    [
        "stage-shots",  # Tallmilan 2026 Stage 3, 14 shots
        "stage-shots-blacksmith-h5",  # Blacksmith Handgun Open 2026 Stage 7, 24 shots
    ],
)
def test_detect_shots_finds_all_audited_shots(fixtures_dir: Path, fixture_stem: str) -> None:
    """Every hand-audited shot must appear in the detection output, within tolerance.

    The detector is allowed to return extras (false positives from echoes,
    neighbouring bays, etc.); precision is handled at the report layer.

    Both fixtures are required to maintain 100% recall: any change that drops
    a real shot from either run is a regression.
    """
    audio, sr, truth = _load_fixture(fixtures_dir, fixture_stem)
    shots = detect_shots(
        audio,
        sample_rate=sr,
        beep_time=truth["beep_time"],
        stage_time=truth["stage_time_seconds"],
        config=ShotDetectConfig(),
    )
    detected_times = sorted(s.time_absolute for s in shots)
    audited = [s["time"] for s in truth["shots"]]
    tol_s = truth["tolerance_ms"] / 1000.0

    misses: list[tuple[float, float]] = []
    for t in audited:
        nearest = min(detected_times, key=lambda d: abs(d - t))
        if abs(nearest - t) > tol_s:
            misses.append((t, nearest))

    assert not misses, (
        f"{fixture_stem}: missed {len(misses)} of {len(audited)} hand-audited shots "
        f"(tolerance {truth['tolerance_ms']} ms): {misses}"
    )


def test_detect_shots_constrains_to_search_window(fixtures_dir: Path) -> None:
    audio, sr, truth = _load_fixture(fixtures_dir)
    beep = truth["beep_time"]
    stage_time = truth["stage_time_seconds"]
    shots = detect_shots(audio, sr, beep, stage_time, ShotDetectConfig())

    assert shots, "expected at least some shots in the stage window"
    for s in shots:
        assert s.time_absolute >= beep, f"shot before beep: {s.time_absolute}"
        assert (
            s.time_absolute <= beep + stage_time + 1.0
        ), f"shot beyond stage_time + 1s: {s.time_absolute}"


def test_detect_shots_fields_are_consistent(fixtures_dir: Path) -> None:
    audio, sr, truth = _load_fixture(fixtures_dir)
    shots = detect_shots(
        audio, sr, truth["beep_time"], truth["stage_time_seconds"], ShotDetectConfig()
    )
    assert shots
    # shot_number is 1-indexed and dense
    assert [s.shot_number for s in shots] == list(range(1, len(shots) + 1))
    # split for shot 1 is from beep; subsequent splits are from previous shot
    beep = truth["beep_time"]
    assert shots[0].split == pytest.approx(shots[0].time_absolute - beep)
    for prev, cur in zip(shots[:-1], shots[1:], strict=True):
        assert cur.split == pytest.approx(cur.time_absolute - prev.time_absolute)
        # min-gap (default 80 ms) must be respected
        assert cur.split >= 0.080 - 1e-9
    # time_from_beep matches the absolute - beep
    for s in shots:
        assert s.time_from_beep == pytest.approx(s.time_absolute - beep)
        assert 0.0 <= s.confidence <= 1.0
        assert s.peak_amplitude >= 0.0


def _make_burst_audio(sr: int, total_s: float, bursts: list[tuple[float, float]]) -> np.ndarray:
    """Build noise + a list of (at_seconds, amplitude) 30-ms broadband bursts."""
    rng = np.random.default_rng(0)
    audio = (rng.standard_normal(int(sr * total_s)) * 0.001).astype(np.float32)
    for seed, (at_s, amp) in enumerate(bursts, start=1):
        local = np.random.default_rng(seed)
        idx = int(at_s * sr)
        burst = (local.standard_normal(int(sr * 0.030)) * amp).astype(np.float32)
        audio[idx : idx + burst.size] += burst
    return audio


def test_detect_shots_refractory_drops_quiet_close_neighbour() -> None:
    """Within the refractory window (default 150 ms), a low-amplitude onset
    following a much louder one is suppressed as a likely echo."""
    sr = 48000
    audio = _make_burst_audio(sr, total_s=3.0, bursts=[(1.500, 0.6), (1.600, 0.05)])

    shots = detect_shots(audio, sr, beep_time=0.5, stage_time=2.0, config=ShotDetectConfig())
    near_loud = [s for s in shots if 1.45 <= s.time_absolute <= 1.55]
    near_echo = [s for s in shots if 1.55 <= s.time_absolute <= 1.65]
    assert len(near_loud) == 1, [s.time_absolute for s in shots]
    assert len(near_echo) == 0, "quiet onset within refractory should be suppressed"


def test_detect_shots_refractory_keeps_loud_close_neighbour() -> None:
    """A real fast split (loud onset within the refractory window) is NOT
    suppressed -- only quiet ones are. This guards against dropping AGC-ducked
    real shots that are still loud relative to their predecessor."""
    sr = 48000
    audio = _make_burst_audio(sr, total_s=3.0, bursts=[(1.500, 0.6), (1.640, 0.5)])

    shots = detect_shots(audio, sr, beep_time=0.5, stage_time=2.0, config=ShotDetectConfig())
    near_first = [s for s in shots if 1.45 <= s.time_absolute <= 1.55]
    near_second = [s for s in shots if 1.60 <= s.time_absolute <= 1.70]
    assert len(near_first) == 1
    assert len(near_second) == 1


def test_detect_shots_min_gap_drops_close_neighbour() -> None:
    """Two synthetic clicks 50 ms apart with default min_gap_ms=80 should yield one shot."""
    sr = 48000
    rng = np.random.default_rng(0)
    audio = (rng.standard_normal(sr * 3) * 0.001).astype(np.float32)
    for offset_s in (1.000, 1.050):
        i = int(offset_s * sr)
        # Sharp 5 ms broadband burst
        burst = (rng.standard_normal(int(sr * 0.005)) * 0.5).astype(np.float32)
        audio[i : i + burst.size] += burst

    shots = detect_shots(audio, sr, beep_time=0.5, stage_time=2.0, config=ShotDetectConfig())
    # The min-gap filter must collapse them into a single shot.
    near_target = [s for s in shots if 0.95 <= s.time_absolute <= 1.10]
    assert len(near_target) == 1, [s.time_absolute for s in near_target]


def test_detect_shots_returns_empty_when_window_outside_audio() -> None:
    sr = 48000
    audio = np.zeros(sr, dtype=np.float32)  # 1 second of silence
    shots = detect_shots(audio, sr, beep_time=10.0, stage_time=5.0, config=ShotDetectConfig())
    assert shots == []


def test_detect_shots_returns_empty_on_silence() -> None:
    sr = 48000
    audio = np.zeros(sr * 5, dtype=np.float32)
    shots = detect_shots(audio, sr, beep_time=0.5, stage_time=3.0, config=ShotDetectConfig())
    assert shots == []


def test_detect_shots_validates_inputs() -> None:
    sr = 48000
    audio = np.zeros(sr, dtype=np.float32)
    with pytest.raises(ValueError, match="empty"):
        detect_shots(np.array([], dtype=np.float32), sr, 0.0, 1.0, ShotDetectConfig())
    with pytest.raises(ValueError, match="1-D"):
        detect_shots(np.zeros((sr, 2), dtype=np.float32), sr, 0.0, 1.0, ShotDetectConfig())
    with pytest.raises(ValueError, match="beep_time"):
        detect_shots(audio, sr, -0.1, 1.0, ShotDetectConfig())
    with pytest.raises(ValueError, match="stage_time"):
        detect_shots(audio, sr, 0.0, 0.0, ShotDetectConfig())
