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


def test_a_kept_candidate_with_no_shot_near_it_is_a_possible_miss_on_a_hand_audited_fixture():
    data = _snap()
    del data["anchor"]
    s = review_signals(data, report=None, inventory=None)
    assert [(u.time, u.confidence) for u in s.unmarked] == [(8.4, 0.7)]


def test_a_snap_has_no_possible_misses():
    """A snap copies a reviewed primary's shots: every shot exists, so a loud
    sound with no shot is another bay or a steel ring, not a miss."""
    assert review_signals(_snap(), report=None, inventory=None).unmarked == []


def test_suggested_moves_come_from_the_inventory_for_a_hand_audited_fixture():
    data = _snap()
    del data["anchor"]
    inventory = {"moves": [{"shot_index": 0, "time": 6.0, "suggested": 5.992, "move_ms": -8.0}]}
    s = review_signals(data, report=None, inventory=inventory)
    assert [(m.time, m.suggested, m.move_ms) for m in s.moves] == [(6.0, 5.992, -8.0)]


def test_a_snap_gets_no_inventory_moves():
    """A snap is seated on the rise foot; the inventory's stricter onset is a
    different point and would only contradict it."""
    inventory = {"moves": [{"shot_index": 0, "time": 6.0, "suggested": 5.992, "move_ms": -8.0}]}
    assert review_signals(_snap(), report=None, inventory=inventory).moves == []


def _envelope(bursts: list[tuple[float, float]], duration: float = 10.0) -> tuple[list[float], float]:
    """1 ms peaks: quiet, with a burst of ``level`` rising at each time."""
    peaks = [0.005] * int(duration * 1000)
    for t, level in bursts:
        i = int(round(t * 1000))
        for k in range(15):
            peaks[i + k] = max(peaks[i + k], level * (0.4 + 0.6 * min(1.0, k / 2)))
    return peaks, duration


def test_a_shot_on_a_louder_echo_after_a_real_blast_is_flagged():
    """Seat lands on the louder echo 20-35 ms after the blast on some head cams;
    that is where a human ear is needed (Earlier rise)."""
    data = _snap(shots=[{"shot_number": 1, "time": 2.025, "source": "promoted", "snap_displacement_ms": 5.0}])
    env = _envelope([(2.0, 0.7), (2.025, 1.0)])
    s = review_signals(data, report=None, inventory=None, envelope=env)
    assert len(s.echoes) == 1
    assert s.echoes[0].time == 2.025
    assert abs(s.echoes[0].earlier - 2.0) <= 0.002


def test_a_quiet_sound_before_the_shot_is_not_an_echo():
    data = _snap(shots=[{"shot_number": 1, "time": 2.025, "source": "promoted", "snap_displacement_ms": 5.0}])
    env = _envelope([(2.0, 0.2), (2.025, 1.0)])
    assert review_signals(data, report=None, inventory=None, envelope=env).echoes == []


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
    assert (s.moves, s.edges, s.unmarked, s.echoes, s.reference, s.rounds) == ([], [], [], [], None, None)
