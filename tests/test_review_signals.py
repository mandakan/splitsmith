"""What the Audit page flags when it reviews a fixture (spec 2026-10-14)."""

from __future__ import annotations

from splitsmith.lab.review_signals import review_signals


def _snap(**over):
    data = {
        "beep_time": 5.0,
        "fixture_window_in_source": [1.0, 40.0],
        "anchor": {"fixture_slug": "a", "snap_window_ms": 60},
        "shots": [
            {"shot_number": 1, "time": 6.0, "source": "promoted", "snap_displacement_ms": 10.0},
            {"shot_number": 2, "time": 6.5, "source": "promoted", "snap_displacement_ms": -58.0},
            {"shot_number": 3, "time": 7.0, "source": "promoted", "snap_displacement_ms": 2.0},
        ],
        "_candidates_pending_audit": {
            "candidates": [
                {"candidate_number": 1, "time": 6.002, "kept": True, "confidence": 0.9},
                {"candidate_number": 2, "time": 8.4, "kept": True, "confidence": 0.7},
                {"candidate_number": 3, "time": 9.9, "kept": False, "confidence": 0.1},
            ]
        },
        "stage_rounds": {"expected": 2},
    }
    data.update(over)
    return data


REPORT = {
    "per_shot": [{"snapped_time": 7.0}, {"snapped_time": 7.5}, {"snapped_time": 8.0}, {"snapped_time": None}]
}


def test_a_snap_at_its_window_edge_is_flagged():
    s = review_signals(_snap(), report=None, inventory=None)
    assert [e.time for e in s.edges] == [6.5]


def test_a_kept_candidate_with_no_shot_near_it_is_a_possible_miss():
    s = review_signals(_snap(), report=None, inventory=None)
    assert [(u.time, u.confidence) for u in s.unmarked] == [(8.4, 0.7)]


def test_suggested_moves_come_from_the_inventory():
    inventory = {"moves": [{"shot_index": 0, "time": 6.0, "suggested": 5.992, "move_ms": -8.0}]}
    s = review_signals(_snap(), report=None, inventory=inventory)
    assert [(m.time, m.suggested, m.move_ms) for m in s.moves] == [(6.0, 5.992, -8.0)]


def test_a_snap_is_checked_against_the_shots_it_was_snapped_with():
    """The reference is the snapped list as written (the report's snapped
    times, on the fixture's clock), never the scorecard's rounds."""
    s = review_signals(_snap(), report=REPORT, inventory=None)
    assert s.reference is not None
    assert s.reference.kind == "snap"
    assert s.reference.times == [6.0, 6.5, 7.0]
    assert s.rounds == 2


def test_a_hand_audited_fixture_has_no_snap_reference():
    data = _snap()
    del data["anchor"]
    s = review_signals(data, report=None, inventory=None)
    assert s.reference is None
    assert s.edges == []


def test_missing_pieces_are_empty_not_errors():
    s = review_signals({"shots": []}, report=None, inventory=None)
    assert (s.moves, s.edges, s.unmarked, s.reference, s.rounds) == ([], [], [], None, None)
