import pytest

from splitsmith.config import HighlightConfig, StageEvent
from splitsmith.highlights import (
    BEAT_ORDER,
    Moment,
    StageReadiness,
    find_moments,
    pick_reel,
    reel_unlocked,
    stage_readiness,
)
from splitsmith.match_project import StageScorecard, StageStatus
from splitsmith.stage_summary_data import TileShot, TileStageData

CFG = HighlightConfig()


def _doc(*, seeded: bool = True, sources: tuple[str, ...] = ()) -> dict:
    doc: dict = {
        "events": [
            {"id": f"evt-{i}", "kind": "movement", "start": 1.0 + i, "end": 1.5 + i, "source": s}
            for i, s in enumerate(sources)
        ]
    }
    if seeded:
        doc["events_seeded"] = 4
    return doc


def test_audited_seeded_stage_without_proposals_is_ready():
    row = stage_readiness(1, StageStatus.audited, _doc(sources=("manual",)), has_footage=True)
    assert row == StageReadiness(1, "ready")


def test_auto_proposals_block_and_are_counted():
    row = stage_readiness(2, StageStatus.audited, _doc(sources=("auto", "manual", "auto")), has_footage=True)
    assert row == StageReadiness(2, "proposals_left", proposals=2)


def test_never_opened_in_breakdown_blocks():
    row = stage_readiness(3, StageStatus.audited, _doc(seeded=False), has_footage=True)
    assert row.state == "breakdown_not_opened"


def test_not_audited_blocks_before_breakdown_is_looked_at():
    row = stage_readiness(4, StageStatus.ready, None, has_footage=True)
    assert row.state == "not_audited"


def test_skipped_and_footageless_stages_do_not_block():
    rows = [
        stage_readiness(1, StageStatus.audited, _doc(), has_footage=True),
        stage_readiness(2, StageStatus.skipped, None, has_footage=False),
        stage_readiness(3, StageStatus.audited, _doc(), has_footage=False),
    ]
    assert [r.state for r in rows] == ["ready", "skipped", "no_footage"]
    assert reel_unlocked(rows)


def test_nothing_ready_is_not_unlocked():
    assert not reel_unlocked([stage_readiness(1, StageStatus.skipped, None, has_footage=False)])
    assert not reel_unlocked([])


def test_events_that_are_not_a_list_count_as_none():
    doc = {"events_seeded": 4, "events": "garbage"}
    assert stage_readiness(1, StageStatus.audited, doc, has_footage=True).state == "ready"


def _stage(n: int, times: list[float], *, events=(), pct=None, standard=True, classes=None) -> TileStageData:
    shots = []
    prev = 0.0
    for i, t in enumerate(times):
        cls = classes[i] if classes else ("first_shot" if i == 0 else "split")
        shots.append(TileShot(time_from_beep=t, split=round(t - prev, 3), interval_class=cls))
        prev = t
    return TileStageData(
        label="A",
        stage_number=n,
        shots=tuple(shots),
        events=tuple(events),
        scorecard=StageScorecard(stage_pct=pct) if pct is not None else None,
        standard_start=standard,
    )


def _ev(i: int, kind: str, start: float, end: float) -> StageEvent:
    return StageEvent(id=f"evt-{i}", kind=kind, start=start, end=end, source="manual")


def test_fastest_draw_is_the_smallest_standard_first_shot():
    found = find_moments([_stage(1, [1.20, 1.50]), _stage(2, [0.95, 1.30])], CFG)
    best = found["fastest_draw"][0]
    assert (best.stage_number, best.figure, best.focus) == (2, 0.95, 0.95)
    assert best.start == -0.4 and best.end == pytest.approx(0.95 + 0.6)


def test_non_standard_starts_never_give_a_draw():
    found = find_moments([_stage(1, [0.5, 0.8], standard=False)], CFG)
    assert found["fastest_draw"] == []


def test_fastest_split_skips_echo_doubles_and_non_split_classes():
    stage = _stage(
        1,
        [1.0, 1.05, 1.30, 3.00, 3.22],
        classes=["first_shot", "split", "split", "movement", "split"],
    )
    best = find_moments([stage], CFG)["fastest_split"][0]
    # 0.05 is under the floor; 1.70 is a movement; 0.22 wins over 0.25.
    assert best.figure == pytest.approx(0.22)
    assert best.focus == pytest.approx(3.22)
    assert best.start == pytest.approx(3.00 - 0.4)


def test_longest_streak_counts_shots_in_a_run_of_fast_splits():
    stage = _stage(1, [1.0, 1.25, 1.50, 1.74, 1.98, 3.5, 3.7])
    best = find_moments([stage], CFG)["longest_streak"][0]
    assert best.figure == 5  # 1.0 .. 1.98: four splits under 0.30, five shots
    assert best.start == pytest.approx(1.0 - 0.4) and best.end == pytest.approx(1.98 + 0.4)
    assert best.focus == pytest.approx(1.98)


def test_streak_shorter_than_the_minimum_is_not_a_moment():
    assert find_moments([_stage(1, [1.0, 1.2, 1.4])], CFG)["longest_streak"] == []


def test_reload_and_transition_come_from_confirmed_regions():
    events = [
        _ev(1, "draw", 0.0, 1.0),
        _ev(2, "movement", 0.2, 0.9),  # a draw on the move, before shot 1: not a transition
        _ev(3, "movement", 2.0, 3.1),
        _ev(4, "reload", 4.0, 5.3),
        _ev(5, "reload", 6.0, 6.2),  # under the floor
    ]
    found = find_moments([_stage(1, [1.0, 1.3, 3.3, 5.5, 7.0], events=events)], CFG)
    assert [m.figure for m in found["fastest_transition"]] == [pytest.approx(1.1)]
    assert [m.figure for m in found["fastest_reload"]] == [pytest.approx(1.3)]
    assert found["fastest_reload"][0].focus == pytest.approx(5.3)


def test_best_stage_is_the_highest_stage_pct_and_ends_on_the_last_shot():
    found = find_moments([_stage(1, [1.0, 2.0], pct=81.0), _stage(2, [1.0, 4.0], pct=97.5)], CFG)
    best = found["best_stage"][0]
    assert (best.stage_number, best.figure, best.focus) == (2, 97.5, 4.0)
    assert best.start == pytest.approx(4.0 - 2.5)


def test_ties_break_by_stage_then_time():
    found = find_moments([_stage(2, [1.0, 1.2]), _stage(1, [1.0, 1.2])], CFG)
    assert [m.stage_number for m in found["fastest_draw"]] == [1, 2]
    assert [m.stage_number for m in found["fastest_split"]] == [1, 2]


def test_finders_tolerate_empty_and_single_shot_stages():
    found = find_moments([_stage(1, []), _stage(2, [1.1])], CFG)
    assert found["fastest_split"] == [] and found["longest_streak"] == []
    assert [m.stage_number for m in found["fastest_draw"]] == [2]


def _m(kind, stage, figure=1.0, focus=1.0) -> Moment:
    return Moment(kind, stage, focus - 1.0, focus + 0.5, focus, figure)


def _found(**lists) -> dict:
    return {k: list(lists.get(k, [])) for k in BEAT_ORDER}


def test_beats_follow_the_order_and_prefer_unused_stages():
    found = _found(
        fastest_draw=[_m("fastest_draw", 1)],
        fastest_transition=[_m("fastest_transition", 1), _m("fastest_transition", 2)],
        fastest_split=[_m("fastest_split", 3)],
    )
    picks = pick_reel(found, [])
    assert [(m.kind, m.stage_number) for m in picks.beats] == [
        ("fastest_draw", 1),
        ("fastest_transition", 2),
        ("fastest_split", 3),
    ]


def test_a_stage_is_reused_only_when_no_other_candidate_exists():
    found = _found(fastest_draw=[_m("fastest_draw", 1)], fastest_split=[_m("fastest_split", 1)])
    assert [m.stage_number for m in pick_reel(found, []).beats] == [1, 1]


def test_cold_open_is_the_fastest_split_else_the_first_beat():
    split = _m("fastest_split", 3)
    assert pick_reel(_found(fastest_split=[split]), []).cold_open == split
    draw = _m("fastest_draw", 1)
    assert pick_reel(_found(fastest_draw=[draw]), []).cold_open == draw
    assert pick_reel(_found(), []).cold_open is None


def test_max_beats_caps_the_list():
    found = _found(**{k: [_m(k, i + 1)] for i, k in enumerate(BEAT_ORDER)})
    assert len(pick_reel(found, [], max_beats=3).beats) == 3


def test_montage_is_the_fastest_splits_in_match_order():
    stages = [_stage(2, [1.0, 1.18, 1.50]), _stage(1, [1.0, 1.21, 1.60, 1.79])]
    picks = pick_reel(_found(), stages, montage_cuts=3)
    # Fastest three: 0.18 (s2 @1.18), 0.19 (s1 @1.79), 0.21 (s1 @1.21); shown in match order.
    assert picks.montage == ((1, 1.21), (1, 1.79), (2, 1.18))


def test_every_kind_is_present_even_when_empty():
    assert set(find_moments([], CFG)) == {
        "fastest_draw",
        "fastest_split",
        "longest_streak",
        "fastest_reload",
        "fastest_transition",
        "best_stage",
    }
