import pytest

from splitsmith.config import HighlightConfig, StageEvent
from splitsmith.highlights import (
    BEAT_ORDER,
    DEFAULT_MOMENTS,
    MOMENT_GROUPS,
    MOMENT_KINDS,
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


def test_skipped_stages_and_stages_without_video_do_not_block():
    rows = [
        stage_readiness(1, StageStatus.audited, _doc(), has_footage=True),
        stage_readiness(2, StageStatus.skipped, None, has_footage=False),
        stage_readiness(3, StageStatus.todo, None, has_video=False, has_footage=False),
    ]
    assert [r.state for r in rows] == ["ready", "skipped", "no_video"]
    assert reel_unlocked(rows)


def test_an_untouched_stage_with_video_blocks_even_without_a_trim():
    row = stage_readiness(2, StageStatus.ready, None, has_footage=False)
    assert row.state == "not_audited"
    assert not reel_unlocked([stage_readiness(1, StageStatus.audited, _doc(), has_footage=True), row])


def test_a_done_stage_whose_trim_is_missing_or_stale_blocks():
    row = stage_readiness(3, StageStatus.audited, _doc(), has_footage=False)
    assert row.state == "trim_missing"
    assert not reel_unlocked([stage_readiness(1, StageStatus.audited, _doc(), has_footage=True), row])


def test_nothing_ready_is_not_unlocked():
    assert not reel_unlocked([stage_readiness(1, StageStatus.skipped, None, has_footage=False)])
    assert not reel_unlocked([])


def test_events_that_are_not_a_list_count_as_none():
    doc = {"events_seeded": 4, "events": "garbage"}
    assert stage_readiness(1, StageStatus.audited, doc, has_footage=True).state == "ready"


def _stage(
    n: int,
    times: list[float],
    *,
    events=(),
    pct=None,
    hf=None,
    stage_time=None,
    standard=True,
    classes=None,
) -> TileStageData:
    from splitsmith.events import reload_figures, shot_is_moving

    shots = []
    prev = 0.0
    for i, t in enumerate(times):
        cls = classes[i] if classes else ("first_shot" if i == 0 else "split")
        moving = shot_is_moving(t, list(events))
        shots.append(TileShot(time_from_beep=t, split=round(t - prev, 3), interval_class=cls, moving=moving))
        prev = t
    card = StageScorecard(stage_pct=pct, hit_factor=hf) if pct is not None or hf is not None else None
    return TileStageData(
        label="A",
        stage_number=n,
        shots=tuple(shots),
        stage_time_seconds=stage_time,
        events=tuple(events),
        reloads=tuple(reload_figures(list(events))),
        scorecard=card,
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


def test_every_beat_is_the_true_best_of_its_kind_even_on_a_used_stage():
    # "FASTEST TRANSITION" over stage 2's slower one would be a false label.
    found = _found(
        fastest_draw=[_m("fastest_draw", 1)],
        fastest_transition=[_m("fastest_transition", 1, 0.9), _m("fastest_transition", 2, 1.4)],
        fastest_reload=[_m("fastest_reload", 3)],
        fastest_split=[_m("fastest_split", 4, 0.2)],  # the cold open
    )
    picks = pick_reel(
        found, [], kinds=("fastest_draw", "fastest_transition", "fastest_reload", "fastest_split")
    )
    assert [(m.kind, m.stage_number, m.figure) for m in picks.beats] == [
        ("fastest_draw", 1, 1.0),
        ("fastest_transition", 1, 0.9),
        ("fastest_reload", 3, 1.0),
    ]


def test_the_cold_open_moment_is_not_played_again_as_a_beat():
    split = _m("fastest_split", 1, 0.17)
    found = _found(fastest_draw=[_m("fastest_draw", 1)], fastest_split=[split, _m("fastest_split", 2, 0.3)])
    picks = pick_reel(found, [])
    assert picks.cold_open == split
    assert [m.kind for m in picks.beats] == ["fastest_draw"]


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
    assert set(find_moments([], CFG)) == set(MOMENT_KINDS)
    assert len(MOMENT_KINDS) == 18


def test_the_menu_defaults_to_five_and_every_kind_has_a_group():
    assert DEFAULT_MOMENTS == (
        "fastest_draw",
        "fastest_split",
        "longest_streak",
        "fastest_reload",
        "best_stage",
    )
    assert tuple(CFG.moments) == DEFAULT_MOMENTS
    assert set(MOMENT_GROUPS) == set(MOMENT_KINDS)
    assert {
        MOMENT_GROUPS[k] for k in ("thinker", "slowest_draw", "slowest_reload", "longest_stage", "most_shots")
    } == {"lolz"}
    assert MOMENT_GROUPS["fastest_transition"] == "stat"
    assert set(BEAT_ORDER) == set(MOMENT_KINDS)


def test_only_chosen_kinds_become_beats():
    found = find_moments([_stage(1, [1.0, 1.2, 1.4, 1.6, 1.8], stage_time=9.0)], CFG)
    picks = pick_reel(found, [], kinds=("fastest_split", "fastest_draw", "longest_stage"))
    assert picks.cold_open.kind == "fastest_split"
    assert [m.kind for m in picks.beats] == ["fastest_draw", "longest_stage"]
    # No split chosen: the cold open is the first beat, not repeated.
    picks = pick_reel(found, [], kinds=("longest_stage",))
    assert picks.cold_open.kind == "longest_stage" and picks.beats == ()


def test_slowest_draw_is_the_largest_standard_draw():
    found = find_moments([_stage(1, [1.2]), _stage(2, [2.9]), _stage(3, [4.0], standard=False)], CFG)
    assert [(m.stage_number, m.figure) for m in found["slowest_draw"]] == [(2, 2.9), (1, 1.2)]


def test_longest_burst_counts_shots_inside_the_window():
    stage = _stage(1, [1.0, 1.2, 1.4, 1.6, 2.1, 3.0, 3.2])
    best = find_moments([stage], CFG)["longest_burst"][0]
    assert best.figure == 4  # 1.0, 1.2, 1.4, 1.6 lie within 1.0 s; 2.1 does not
    assert (best.focus, best.label) == (1.6, "IN 1 S")
    assert find_moments([stage], HighlightConfig(burst_window_s=1.0))["longest_burst"][0].figure == 4
    assert find_moments([stage], HighlightConfig(burst_window_s=0.9))["longest_burst"][0].figure == 4
    assert find_moments([stage], HighlightConfig(burst_window_s=0.5))["longest_burst"][0].figure == 3


def test_fastest_string_is_n_shots_in_a_row_without_a_reload_or_movement():
    # Five quick shots after the reload: a six-shot string there must cross it.
    times = [1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 4.0, 4.1, 4.2, 4.3, 4.4]
    classes = ["first_shot"] + ["split"] * 6 + ["reload"] + ["split"] * 4
    found = find_moments([_stage(1, times, classes=classes)], HighlightConfig(string_shots=6))
    best = found["fastest_string"][0]
    # 2.0 .. 4.3 or 1.8 .. 4.2 would cross the reload; 1.0 .. 2.0 is the best legal one.
    assert best.figure == pytest.approx(1.0) and best.label == "6 SHOTS"
    assert (best.start, best.focus) == (pytest.approx(1.0 - 0.4), 2.0)


def test_mag_changes_count_confirmed_reloads_per_stage():
    reloads = [_ev(1, "reload", 2.0, 3.5), _ev(2, "reload", 5.0, 6.2), _ev(3, "reload", 8.0, 9.9)]
    found = find_moments([_stage(1, [1.0, 4.0, 7.0, 10.0], events=reloads), _stage(2, [1.0])], CFG)
    best = found["most_reloads"][0]
    assert (best.stage_number, best.figure) == (1, 3)
    assert best.focus == 6.2  # shown on the stage's fastest reload


def test_reload_on_the_move_is_the_least_exposed_moving_reload():
    events = [
        _ev(1, "movement", 2.0, 4.0),
        _ev(2, "reload", 2.5, 3.6),  # fully inside the movement: 0 exposed
        _ev(3, "reload", 6.0, 7.0),  # standing: never a candidate
    ]
    found = find_moments([_stage(1, [1.0, 5.0, 8.0], events=events)], CFG)
    assert [(m.figure, m.focus) for m in found["moving_reload"]] == [(0.0, 3.6)]


def test_most_shots_on_the_move():
    events = [_ev(1, "movement", 2.0, 4.0)]
    found = find_moments([_stage(1, [1.0, 2.5, 3.0, 3.5, 5.0], events=events)], CFG)
    best = found["most_moving_shots"][0]
    assert (best.figure, best.focus) == (3, 3.5)


def test_stage_level_figures():
    a = _stage(1, [1.0, 9.0], stage_time=9.1, hf=5.2)
    b = _stage(2, [1.0, 30.0], stage_time=30.4, hf=7.9)
    found = find_moments([a, b], CFG)
    assert found["fastest_stage"][0].stage_number == 1
    assert found["longest_stage"][0].stage_number == 2
    assert (found["top_hit_factor"][0].stage_number, found["top_hit_factor"][0].figure) == (2, 7.9)
    assert (found["most_shots"][0].stage_number, found["most_shots"][0].figure) == (
        1,
        2,
    )  # a tie, stage 1 first
    assert found["longest_stage"][0].focus == 30.0  # ends on the last shot


def test_the_thinker_is_the_longest_pause_outside_movement_and_reloads():
    times = [1.0, 1.3, 4.7, 5.0, 9.5, 9.8]
    classes = ["first_shot", "split", "transition", "split", "movement", "split"]
    events = [_ev(1, "movement", 5.2, 9.3)]
    best = find_moments([_stage(1, times, classes=classes, events=events)], CFG)["thinker"][0]
    assert (best.figure, best.focus) == (pytest.approx(3.4), 4.7)


def test_slowest_reload_is_the_longest_confirmed_reload():
    events = [_ev(1, "reload", 2.0, 3.0), _ev(2, "reload", 5.0, 9.1)]
    best = find_moments([_stage(1, [1.0, 4.0, 10.0], events=events)], CFG)["slowest_reload"][0]
    assert best.figure == pytest.approx(4.1)
