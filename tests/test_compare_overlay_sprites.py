"""The overlay's step function: shot events in, ordered states out."""

import pytest

from splitsmith.compare import overlay_sprites
from splitsmith.compare.overlay_data import TileShot, TileStageData

HEAD_PAD = 1.0
DURATION = 10.0


def _tile(label: str, times: list[float], *, expected: int | None = None) -> TileStageData:
    shots = []
    prev = 0.0
    for t in times:
        shots.append(TileShot(time_from_beep=t, split=t - prev))
        prev = t
    rounds = None
    if expected is not None:
        from splitsmith.config import StageRounds

        rounds = StageRounds(expected=expected)
    return TileStageData(label=label, stage_number=1, shots=tuple(shots), stage_rounds=rounds)


def _placements(*labels: str, absent: tuple[str, ...] = ()) -> list:
    out = []
    for index, label in enumerate(labels):
        row, col = divmod(index, 2)
        out.append(overlay_sprites.TilePlacement(label=label, row=row, col=col, present=label not in absent))
    return out


def _states(placements, data):
    return overlay_sprites.build_overlay_states(
        placements, data, head_pad_seconds=HEAD_PAD, duration_seconds=DURATION
    )


def _panel(state, label):
    return next(p for p in state.panels if p.label == label)


def test_one_state_per_distinct_event_plus_the_opening_state():
    data = {"ann": _tile("ann", [1.0, 1.5]), "bo": _tile("bo", [1.2])}
    states = _states(_placements("ann", "bo"), data)
    # opening + 3 shot events
    assert len(states) == 4


def test_first_state_starts_at_zero_and_shows_nothing_fired():
    data = {"ann": _tile("ann", [1.0])}
    states = _states(_placements("ann"), data)
    assert states[0].start_seconds == 0.0
    assert _panel(states[0], "ann").shots_fired == 0
    assert _panel(states[0], "ann").last_split is None


def test_state_boundaries_are_head_pad_plus_event_time():
    data = {"ann": _tile("ann", [1.0, 1.5])}
    states = _states(_placements("ann"), data)
    assert [round(s.start_seconds, 3) for s in states] == [0.0, 2.0, 2.5]


def test_durations_sum_to_the_segment_duration():
    data = {"ann": _tile("ann", [1.0, 1.5]), "bo": _tile("bo", [1.2])}
    states = _states(_placements("ann", "bo"), data)
    assert sum(s.duration_seconds for s in states) == pytest.approx(DURATION)


def test_no_state_is_zero_length():
    # ann's second shot lands exactly on the segment end (HEAD_PAD + 9.0 ==
    # DURATION). Admitting a boundary event there would open a state with
    # nowhere left to run, so the drop must be strict: at the end counts as
    # past it. bo covers the sub-millisecond neighbour of ann's first shot.
    data = {"ann": _tile("ann", [1.0, 9.0]), "bo": _tile("bo", [1.0004])}
    states = _states(_placements("ann", "bo"), data)
    assert [(s.start_seconds, s.duration_seconds) for s in states if s.duration_seconds <= 0] == []


def test_simultaneous_shots_collapse_to_one_state():
    data = {"ann": _tile("ann", [1.0]), "bo": _tile("bo", [1.0])}
    states = _states(_placements("ann", "bo"), data)
    assert len(states) == 2
    last = states[-1]
    assert _panel(last, "ann").shots_fired == 1
    assert _panel(last, "bo").shots_fired == 1


def test_shots_inside_the_same_millisecond_collapse_to_one_state():
    # Sub-millisecond apart: one state, and neither shot goes missing --
    # a boundary rounded down to 1.000 would leave bo's 1.0004 counted in
    # no state at all.
    data = {"ann": _tile("ann", [1.0]), "bo": _tile("bo", [1.0004])}
    states = _states(_placements("ann", "bo"), data)
    assert len(states) == 2
    last = states[-1]
    assert _panel(last, "ann").shots_fired == 1
    assert _panel(last, "bo").shots_fired == 1


def test_shots_past_the_segment_end_are_dropped():
    data = {"ann": _tile("ann", [1.0, 20.0])}
    states = _states(_placements("ann"), data)
    assert len(states) == 2
    assert states[-1].end_seconds == pytest.approx(DURATION)


def test_last_split_is_the_most_recent_shots_split():
    data = {"ann": _tile("ann", [1.0, 1.25])}
    states = _states(_placements("ann"), data)
    assert _panel(states[1], "ann").last_split == pytest.approx(1.0)
    assert _panel(states[2], "ann").last_split == pytest.approx(0.25)


def test_expected_shot_count_comes_from_stage_rounds():
    data = {"ann": _tile("ann", [1.0], expected=12)}
    states = _states(_placements("ann"), data)
    assert _panel(states[-1], "ann").expected_shots == 12


def test_expected_shot_count_is_none_without_stage_rounds():
    data = {"ann": _tile("ann", [1.0])}
    states = _states(_placements("ann"), data)
    assert _panel(states[-1], "ann").expected_shots is None


def test_shots_a_millisecond_apart_stay_separate_states():
    # 1.000 and 1.001 are adjacent millisecond slots, not the same one.
    # Scaling the key to an int bucket truncates them together.
    data = {"ann": _tile("ann", [1.0]), "bo": _tile("bo", [1.001])}
    states = _states(_placements("ann", "bo"), data)
    assert [round(s.start_seconds, 3) for s in states] == [0.0, 2.0, 2.001]


def test_a_whole_match_mapping_is_rejected_rather_than_rendered_blank():
    # load_overlay_data is keyed by (label, stage_number). Passing it
    # straight through would match no label and blank every tile silently.
    data = {("ann", 1): _tile("ann", [1.0])}
    with pytest.raises(ValueError, match="keyed by tile label"):
        _states(_placements("ann"), data)


def test_a_tile_shows_nothing_until_its_own_first_shot():
    # bo does not fire until 5.0s, so at ann's shot bo's panel is still
    # empty -- a tile's content is its own, never borrowed from whoever
    # has already fired.
    data = {"ann": _tile("ann", [1.0]), "bo": _tile("bo", [5.0])}
    states = _states(_placements("ann", "bo"), data)
    at_first_shot = states[1]
    assert _panel(at_first_shot, "ann").shots_fired == 1
    assert _panel(at_first_shot, "bo").shots_fired == 0
    assert _panel(at_first_shot, "bo").last_split is None


def test_filler_tiles_never_fire():
    data = {"ann": _tile("ann", [1.0]), "bo": _tile("bo", [1.1])}
    placements = _placements("ann", "bo", absent=("bo",))
    states = _states(placements, data)
    last = states[-1]
    assert _panel(last, "bo").present is False
    assert _panel(last, "bo").shots_fired == 0
    assert _panel(last, "ann").shots_fired == 1


def test_a_filler_tile_with_shots_on_disk_is_still_drawn_as_nothing():
    # bo has a full audit but no trim for this stage, and fired *first*.
    # A state builder that forgot to skip fillers would punch bo's shots
    # into the timeline as extra states nothing on screen accounts for.
    data = {"ann": _tile("ann", [1.0]), "bo": _tile("bo", [0.5], expected=12)}
    placements = _placements("ann", "bo", absent=("bo",))
    states = _states(placements, data)
    assert [round(s.start_seconds, 3) for s in states] == [0.0, 2.0]
    last = states[-1]
    assert _panel(last, "bo").shots_fired == 0
    assert _panel(last, "bo").last_split is None
    assert _panel(last, "bo").expected_shots is None
    assert _panel(last, "ann").shots_fired == 1


def test_a_tile_with_no_audit_still_gets_a_panel_in_every_state():
    data = {"ann": _tile("ann", [1.0]), "bo": TileStageData(label="bo", stage_number=1)}
    states = _states(_placements("ann", "bo"), data)
    for state in states:
        assert {p.label for p in state.panels} == {"ann", "bo"}
        assert _panel(state, "bo").shots_fired == 0


def test_panels_keep_placement_order_and_geometry():
    data = {"ann": _tile("ann", [1.0]), "bo": _tile("bo", [1.1])}
    placements = _placements("ann", "bo")
    states = _states(placements, data)
    for state in states:
        assert [(p.label, p.row, p.col) for p in state.panels] == [
            (p.label, p.row, p.col) for p in placements
        ]


def test_no_shots_at_all_yields_a_single_state():
    data = {"ann": TileStageData(label="ann", stage_number=1)}
    states = _states(_placements("ann"), data)
    assert len(states) == 1
    assert states[0].start_seconds == 0.0
    assert states[0].duration_seconds == pytest.approx(DURATION)
