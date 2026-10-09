"""overlay_hud: what a template HUD is told, and which frames it draws."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.config import StageEvent
from splitsmith.overlay_hud import (
    HudFrame,
    HudOptions,
    OverlayStyleFields,
    declared_positions,
    hud_frame_plan,
    hud_options_data,
    hud_page_size,
    hud_stage_data,
    resolve_position,
    speed_tiers,
)
from splitsmith.stage_summary_data import TileShot


def _shot(t: float, split: float, cls: str | None) -> TileShot:
    return TileShot(time_from_beep=t, split=split, interval_class=cls)  # type: ignore[arg-type]


STAGE = [
    _shot(1.10, 1.10, "first_shot"),
    _shot(1.35, 0.25, "split"),
    _shot(1.60, 0.25, "split"),
    _shot(2.40, 0.80, "split"),
    _shot(2.65, 0.25, "split"),
    _shot(4.30, 1.65, "reload"),
    _shot(4.52, 0.22, "split"),
]


def test_tiers_compare_a_split_with_its_own_class_median() -> None:
    # split median over [0.25, 0.25, 0.80, 0.25, 0.22] is 0.25: 0.80 is slow,
    # 0.22 is 0.88x (good), the rest normal. The draw and the reload carry none.
    assert speed_tiers(STAGE) == [None, "normal", "normal", "slow", "normal", None, "good"]


def test_a_class_with_fewer_than_three_shots_has_no_tiers() -> None:
    shots = [_shot(1.0, 1.0, "first_shot"), _shot(2.0, 0.7, "transition"), _shot(2.9, 0.9, "transition")]
    assert speed_tiers(shots) == [None, None, None]


def test_an_unclassified_shot_has_no_tier() -> None:
    shots = [_shot(0.3 * i, 0.3, None) for i in range(1, 6)]
    assert speed_tiers(shots) == [None] * 5


def test_stage_data_is_in_clip_time_with_labels_and_rounded_numbers() -> None:
    data = hud_stage_data(STAGE, beep_in_clip=5.0)
    assert data["beep"] == 5.0
    assert data["rounds"] == 7
    assert data["stage_time"] == pytest.approx(4.52)
    first, slow = data["shots"][0], data["shots"][3]
    assert first == {
        "t": 6.1,
        "split": 1.1,
        "cls": "first_shot",
        "label": "Draw",
        "tier": None,
        "moving": False,
    }
    assert slow["t"] == pytest.approx(7.4) and slow["label"] == "Split" and slow["tier"] == "slow"
    assert data["shots"][5]["label"] == "Reload"
    # Rounded so float noise never moves a cache key.
    assert all(len(repr(s["t"])) <= 12 for s in data["shots"])


def test_stage_data_without_shots_is_an_empty_stage() -> None:
    assert hud_stage_data([], beep_in_clip=1.0) == {
        "beep": 1.0,
        "shots": [],
        "stage_time": 0.0,
        "rounds": 0,
        "events": [],
        "reloads": [],
    }


def test_declared_positions_reads_the_meta_tag_in_order(tmp_path: Path) -> None:
    template = tmp_path / "hud.html"
    template.write_text(
        '<meta name="splitsmith-positions" content="bottom-left, top-left,nowhere,top-right">',
        encoding="utf-8",
    )
    assert declared_positions(template) == ("bottom-left", "top-left", "top-right")


def test_a_template_without_the_tag_declares_none(tmp_path: Path) -> None:
    template = tmp_path / "hud.html"
    template.write_text("<!doctype html><body></body>", encoding="utf-8")
    assert declared_positions(template) == ()


def test_resolve_position_takes_a_declared_request_else_the_first() -> None:
    declared = ("bottom-left", "top-right")
    assert resolve_position("top-right", declared) == "top-right"
    assert resolve_position(None, declared) == "bottom-left"
    assert resolve_position("top-left", declared) == "bottom-left"
    assert resolve_position("top-left", ()) is None


def test_options_data_carries_the_resolved_position() -> None:
    options = HudOptions(speed_colors=False, position="top-left")
    assert hud_options_data(options, "bottom-left") == {
        "speed_colors": False,
        "class_labels": True,
        "landing": True,
        "reload_chip": False,
        "stage_bar": False,
        "position": "bottom-left",
    }


def _total(plan: tuple[HudFrame, ...]) -> int:
    return sum(frame.count for frame in plan)


def test_plan_holds_before_the_beep_renders_the_live_span_and_holds_the_tail() -> None:
    # 10 fps, 3 s clip = 30 frames; beep at 1.0 s, last shot 1.5 s, settle 0.2 s.
    plan = hud_frame_plan(frame_count=30, fps=10.0, beep=1.0, last_shot=1.5, settle=0.2)
    assert plan[0] == HudFrame(seek=0.0, count=10)
    assert [f.seek for f in plan[1:]] == [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7]
    assert [f.count for f in plan[1:-1]] == [1] * 7
    assert plan[-1] == HudFrame(seek=1.7, count=13)
    assert _total(plan) == 30


def test_plan_with_the_beep_on_frame_zero_has_no_hold() -> None:
    plan = hud_frame_plan(frame_count=20, fps=10.0, beep=0.0, last_shot=0.5, settle=0.0)
    assert plan[0] == HudFrame(seek=0.0, count=1)
    assert _total(plan) == 20


def test_plan_with_the_beep_past_the_clip_is_one_hold() -> None:
    assert hud_frame_plan(frame_count=20, fps=10.0, beep=5.0, last_shot=6.0, settle=0.5) == (
        HudFrame(seek=0.0, count=20),
    )


def test_plan_clamps_a_live_span_that_runs_past_the_clip() -> None:
    plan = hud_frame_plan(frame_count=20, fps=10.0, beep=1.0, last_shot=1.8, settle=5.0)
    assert plan[-1] == HudFrame(seek=1.9, count=1)
    assert _total(plan) == 20


def test_plan_starts_at_the_beep_even_when_a_shot_precedes_it() -> None:
    plan = hud_frame_plan(frame_count=30, fps=10.0, beep=1.0, last_shot=0.6, settle=0.2)
    assert plan[0] == HudFrame(seek=0.0, count=10)
    assert [f.seek for f in plan[1:]] == [1.0, 1.1, 1.2]
    assert _total(plan) == 30


def test_plan_does_not_cap_a_long_field_course() -> None:
    plan = hud_frame_plan(frame_count=3000, fps=30.0, beep=5.0, last_shot=95.0, settle=0.6)
    assert len(plan) == 1 + 2719 and _total(plan) == 3000


def test_plan_of_an_empty_clip_is_empty() -> None:
    assert hud_frame_plan(frame_count=0, fps=30.0, beep=0.0, last_shot=0.0, settle=0.0) == ()


def test_page_size_caps_at_1080_lines_and_keeps_even_sides() -> None:
    assert hud_page_size(1920, 1080) == (1920, 1080)
    assert hud_page_size(1280, 720) == (1280, 720)
    assert hud_page_size(3840, 2160) == (1920, 1080)
    assert hud_page_size(2704, 1520) == (1920, 1080)


def test_a_shot_before_the_beep_is_placed_at_the_beep() -> None:
    """An audit can carry a negative ``ms_after_beep``. The frame plan holds
    seek(0) for every pre-beep frame, so a shot drawn before the beep would
    pop in part-way through its animation at the beep frame. Classic clamps
    the same case; here the shot lands on the beep."""
    data = hud_stage_data([_shot(-0.12, -0.12, "first_shot"), _shot(0.4, 0.52, "split")], beep_in_clip=1.0)
    assert [s["t"] for s in data["shots"]] == [1.0, 1.4]


# --- stage events (spec 2026-10-08, part 2) -------------------------------------


def _event(ident: str, kind: str, start: float, end: float, source: str = "manual") -> StageEvent:
    return StageEvent(id=ident, kind=kind, start=start, end=end, source=source)  # type: ignore[arg-type]


EVENT_SHOTS = [
    _shot(1.20, 1.20, "first_shot"),
    _shot(4.35, 3.15, "movement"),
    _shot(7.90, 3.55, "split"),
    _shot(9.60, 1.70, "reload"),
]


def test_confirmed_regions_reach_the_stage_in_clip_seconds() -> None:
    events = [_event("evt-1", "movement", 3.4, 6.1), _event("evt-2", "reload", 8.05, 9.47)]
    data = hud_stage_data(EVENT_SHOTS, beep_in_clip=2.0, events=events)
    assert data["events"] == [
        {"kind": "movement", "start": 5.4, "end": 8.1},
        {"kind": "reload", "start": 10.05, "end": 11.47},
    ]
    (reload,) = data["reloads"]
    assert reload["start"] == 10.05 and reload["end"] == 11.47
    assert reload["duration"] == 1.42
    assert reload["overhang"] is None, "a standing reload has no overhang"
    assert [s["moving"] for s in data["shots"]] == [False, True, False, False]


def test_a_reload_inside_a_movement_carries_its_overhang() -> None:
    events = [_event("evt-1", "movement", 3.4, 9.0), _event("evt-2", "reload", 8.05, 9.47)]
    (reload,) = hud_stage_data(EVENT_SHOTS, beep_in_clip=2.0, events=events)["reloads"]
    assert reload["overhang"] == 0.47


def test_proposals_never_reach_the_stage() -> None:
    """Only confirmed regions render; an auto reload is a guess nobody
    looked at. ``render_hud_overlay`` filters through ``events.confirmed``,
    and ``hud_stage_data`` applies the same rule so no caller can leak one."""
    data = hud_stage_data(
        EVENT_SHOTS,
        beep_in_clip=2.0,
        events=[_event("evt-1", "reload", 8.05, 9.47, "auto"), _event("evt-2", "movement", 3.4, 6.1, "auto")],
    )
    assert data["events"] == [] and data["reloads"] == []
    assert all(s["moving"] is False for s in data["shots"])


def test_a_stage_without_events_still_carries_the_keys() -> None:
    data = hud_stage_data(EVENT_SHOTS, beep_in_clip=2.0)
    assert data["events"] == [] and data["reloads"] == []
    assert all(s["moving"] is False for s in data["shots"])


def test_the_two_event_toggles_default_off_and_ride_the_style_fields() -> None:
    assert HudOptions().reload_chip is False and HudOptions().stage_bar is False
    fields = OverlayStyleFields()
    assert fields.overlay_reload_chip is False and fields.overlay_stage_bar is False
    assert OverlayStyleFields(overlay_reload_chip=True).hud_options().reload_chip is True
    assert OverlayStyleFields(overlay_stage_bar=True).hud_options().stage_bar is True
    assert OverlayStyleFields(overlay_reload_chip=True).hud_options().stage_bar is False
    data = hud_options_data(HudOptions(reload_chip=True, stage_bar=True), None)
    assert data["reload_chip"] is True and data["stage_bar"] is True
