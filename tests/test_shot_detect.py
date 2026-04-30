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


_AUDITED_FIXTURES = [
    "stage-shots",  # Tallmilan 2026 Stage 3, 15 shots
    "stage-shots-blacksmith-h5",  # Blacksmith Handgun Open 2026 Stage 7, 24 shots (4 manual)
    "stage-shots-tallmilan-stage2",  # Tallmilan 2026 Stage 2, 12 shots (2 manual)
]


@pytest.mark.parametrize("fixture_stem", _AUDITED_FIXTURES)
def test_detect_shots_reproduces_fixture_candidates(fixtures_dir: Path, fixture_stem: str) -> None:
    """The detector must reproduce the fixture's recorded candidate list to
    within 1 ms, so audited ``candidate_number`` references stay valid.

    This is a determinism gate: any change to the detection algorithm requires
    regenerating fixtures with ``splitsmith audit-prep``. Without it, prior
    audit work would silently bind to different physical onsets.
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
    fixture_times = sorted(c["time"] for c in truth["_candidates_pending_audit"]["candidates"])

    assert len(detected_times) == len(fixture_times), (
        f"{fixture_stem}: detector returned {len(detected_times)} shots, "
        f"fixture has {len(fixture_times)} candidates -- regenerate with audit-prep"
    )
    for d, c in zip(detected_times, fixture_times, strict=True):
        assert abs(d - c) < 0.001, (
            f"{fixture_stem}: detector at {d:.4f}s vs fixture {c:.4f}s "
            f"(delta {abs(d - c) * 1000:.2f} ms) -- regenerate with audit-prep"
        )


@pytest.mark.parametrize("fixture_stem", _AUDITED_FIXTURES)
def test_audited_shots_are_well_formed(fixtures_dir: Path, fixture_stem: str) -> None:
    """Audit integrity: every kept shot has a valid source, detected shots
    reference a real candidate_number, and drag distances are within a sane
    bound (100 ms; anything beyond that suggests a mistaken candidate pick)."""
    _, _, truth = _load_fixture(fixtures_dir, fixture_stem)
    cands_by_num = {
        c["candidate_number"]: c for c in truth["_candidates_pending_audit"]["candidates"]
    }
    audited = truth.get("shots", [])
    if not audited:
        pytest.skip(f"{fixture_stem}: no audited shots yet")

    for s in audited:
        source = s.get("source")
        assert source in ("detected", "manual"), f"{fixture_stem}: invalid source {source!r}"
        if source == "manual":
            assert (
                s.get("candidate_number") is None
            ), f"{fixture_stem}: manual shot should not reference a candidate"
            continue
        cn = s.get("candidate_number")
        assert cn is not None, f"{fixture_stem}: detected shot missing candidate_number"
        cand = cands_by_num.get(cn)
        assert cand is not None, f"{fixture_stem}: candidate_number {cn} not in candidates list"
        drag_ms = abs(s["time"] - cand["time"]) * 1000.0
        assert drag_ms < 100.0, (
            f"{fixture_stem}: shot {s.get('shot_number')} dragged {drag_ms:.1f} ms from "
            f"candidate {cn} -- suspiciously large, likely the wrong candidate was kept"
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
        # min-gap (default 80 ms) is enforced on librosa-frame times BEFORE the
        # leading-edge backtrack. After backtrack, leading edges may land closer
        # than min_gap (an echo pulled adjacent to a real shot, or a fast double).
        # 40 ms is a soft lower bound; anything tighter is spurious.
        assert cur.split >= 0.040 - 1e-9
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
