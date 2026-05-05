"""Tests for ``splitsmith.lab.snap_window`` (issue #122).

Pure-function unit tests over synthetic anchor + candidate inputs. The
function does not read files or call detection -- it operates on a
list of ``(time, confidence)`` candidates and a list of anchor shot
times, so the tests are deterministic.
"""

from __future__ import annotations

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
