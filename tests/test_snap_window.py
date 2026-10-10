"""Tests for ``splitsmith.lab.snap_window`` (issue #122).

Pure-function unit tests over synthetic anchor + candidate inputs. The
function does not read files or call detection -- it operates on a
list of ``(time, confidence)`` candidates and a list of anchor shot
times, so the tests are deterministic.
"""

from __future__ import annotations

import numpy as np
import pytest

from splitsmith.lab.snap_window import SnapResult, snap_anchor_shots


def test_clean_snap_within_window() -> None:
    # Anchor beep at 0.5; shots at +1.0, +2.0, +3.0 from beep.
    # Secondary beep at 5.5 -> predicted shots at 6.5, 7.5, 8.5.
    # Candidates land 5 ms early on each predicted time.
    candidates = [
        (6.495, 0.9),
        (7.495, 0.9),
        (8.495, 0.9),
    ]
    results = snap_anchor_shots(
        anchor_beep_time=0.5,
        anchor_shots=[1.5, 2.5, 3.5],
        secondary_beep_time=5.5,
        voter_a_candidates=candidates,
    )

    assert [r.shot_number for r in results] == [1, 2, 3]
    assert all(r.sanity_flag == "" for r in results)
    assert all(r.snapped_time is not None for r in results)
    for r in results:
        assert r.displacement_ms is not None
        assert r.displacement_ms == pytest.approx(-5.0, abs=0.01)


def test_no_candidate_in_window_flags_no_candidate() -> None:
    # Predicted at 6.5; only candidate is 0.5 s away -> outside +/-200 ms.
    results = snap_anchor_shots(
        anchor_beep_time=0.0,
        anchor_shots=[6.5],
        secondary_beep_time=0.0,
        voter_a_candidates=[(7.0, 0.9)],
        window_ms=200.0,
    )
    assert len(results) == 1
    r = results[0]
    assert r.snapped_time is None
    assert r.displacement_ms is None
    assert r.snap_confidence is None
    assert r.sanity_flag == "no-candidate"


def test_picks_nearest_candidate_within_window() -> None:
    # Two candidates inside the window; closer one wins.
    results = snap_anchor_shots(
        anchor_beep_time=0.0,
        anchor_shots=[1.0],
        secondary_beep_time=0.0,
        voter_a_candidates=[(0.95, 0.5), (1.02, 0.9)],
        window_ms=100.0,
    )
    assert results[0].snapped_time == pytest.approx(1.02)
    assert results[0].snap_confidence == pytest.approx(0.9)
    assert results[0].sanity_flag == ""


def test_monotonicity_violation_flags_both_shots() -> None:
    # Two anchor shots both snap to the same candidate -> b.snap == a.snap,
    # gap = 0 -> monotonicity flag.
    results = snap_anchor_shots(
        anchor_beep_time=0.0,
        anchor_shots=[1.00, 1.05],
        secondary_beep_time=0.0,
        voter_a_candidates=[(1.025, 0.9)],
        window_ms=200.0,
    )
    assert len(results) == 2
    assert results[0].snapped_time == results[1].snapped_time
    assert results[0].sanity_flag == "monotonicity"
    assert results[1].sanity_flag == "monotonicity"


def test_min_spacing_violation_flags_both_shots() -> None:
    # Adjacent snaps land 50 ms apart, below the 80 ms default.
    results = snap_anchor_shots(
        anchor_beep_time=0.0,
        anchor_shots=[1.0, 1.05],
        secondary_beep_time=0.0,
        voter_a_candidates=[(1.0, 0.9), (1.05, 0.9)],
        window_ms=200.0,
        min_spacing_ms=80.0,
    )
    assert results[0].snapped_time == pytest.approx(1.0)
    assert results[1].snapped_time == pytest.approx(1.05)
    assert results[0].sanity_flag == "min-spacing"
    assert results[1].sanity_flag == "min-spacing"


def test_min_spacing_respected_when_gap_at_threshold() -> None:
    # Exact min-spacing gap -> no flag (gap is not strictly less than threshold).
    results = snap_anchor_shots(
        anchor_beep_time=0.0,
        anchor_shots=[1.0, 1.08],
        secondary_beep_time=0.0,
        voter_a_candidates=[(1.0, 0.9), (1.08, 0.9)],
        window_ms=200.0,
        min_spacing_ms=80.0,
    )
    assert results[0].sanity_flag == ""
    assert results[1].sanity_flag == ""


def test_no_candidate_does_not_propagate_flag_to_neighbours() -> None:
    # Middle shot has no candidate; flanking shots still snap clean and
    # are not retroactively flagged for the gap.
    results = snap_anchor_shots(
        anchor_beep_time=0.0,
        anchor_shots=[1.0, 2.0, 3.0],
        secondary_beep_time=0.0,
        voter_a_candidates=[(1.0, 0.9), (3.0, 0.9)],
        window_ms=200.0,
    )
    assert results[0].sanity_flag == ""
    assert results[1].sanity_flag == "no-candidate"
    assert results[2].sanity_flag == ""


def test_displacement_sign_matches_snap_minus_predicted() -> None:
    # Candidate lands 12 ms after the predicted time -> +12 ms displacement.
    results = snap_anchor_shots(
        anchor_beep_time=0.0,
        anchor_shots=[1.0],
        secondary_beep_time=0.0,
        voter_a_candidates=[(1.012, 0.9)],
        window_ms=200.0,
    )
    assert results[0].displacement_ms == pytest.approx(12.0, abs=0.01)


def test_empty_candidate_universe_marks_all_no_candidate() -> None:
    results = snap_anchor_shots(
        anchor_beep_time=0.0,
        anchor_shots=[1.0, 2.0],
        secondary_beep_time=0.0,
        voter_a_candidates=[],
    )
    assert all(r.sanity_flag == "no-candidate" for r in results)
    assert all(r.snapped_time is None for r in results)


def test_returns_pydantic_models() -> None:
    results = snap_anchor_shots(
        anchor_beep_time=0.0,
        anchor_shots=[1.0],
        secondary_beep_time=0.0,
        voter_a_candidates=[(1.0, 0.9)],
    )
    assert all(isinstance(r, SnapResult) for r in results)


def _clicks(times: list[float], duration: float, sr: int = 48000, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    audio = rng.normal(0.0, 0.002, int(duration * sr)).astype(np.float32)
    decay = np.exp(-np.arange(int(0.05 * sr)) / (0.008 * sr)).astype(np.float32)
    for t in times:
        i = int(t * sr)
        burst = rng.normal(0.0, 0.5, decay.size).astype(np.float32) * decay
        audio[i : i + decay.size] += burst[: audio.size - i]
    return audio


def test_onset_lag_finds_a_constant_offset_between_angles():
    """Two angles' marked beeps can disagree by 100 ms or more (#1363);
    the snap window is 60 ms, so the lag must be found first."""
    from splitsmith.lab.snap_window import estimate_onset_lag

    shots = [1.0, 1.31, 1.9, 2.4, 2.62, 3.5, 4.1, 4.33]
    anchor = _clicks([0.5 + s for s in shots], 6.0, seed=1)
    # Secondary: beep marked at 0.5 too, but every shot really lands 120 ms later.
    secondary = _clicks([0.5 + s + 0.120 for s in shots], 6.5, seed=2)
    lag_s, contrast = estimate_onset_lag(
        anchor_audio=anchor,
        anchor_sr=48000,
        anchor_beep_time=0.5,
        secondary_audio=secondary,
        secondary_sr=48000,
        secondary_beep_time=0.5,
        span_s=max(shots) + 0.5,
    )
    assert abs(lag_s - 0.120) <= 0.003
    assert contrast > 1.5


def test_onset_lag_is_zero_when_the_beeps_agree():
    from splitsmith.lab.snap_window import estimate_onset_lag

    shots = [1.0, 1.4, 2.2, 2.9]
    anchor = _clicks([0.5 + s for s in shots], 5.0, seed=3)
    secondary = _clicks([0.8 + s for s in shots], 5.5, seed=4)
    lag_s, _ = estimate_onset_lag(
        anchor_audio=anchor,
        anchor_sr=48000,
        anchor_beep_time=0.5,
        secondary_audio=secondary,
        secondary_sr=48000,
        secondary_beep_time=0.8,
        span_s=3.5,
    )
    assert abs(lag_s) <= 0.003
