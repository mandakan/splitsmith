"""The grid's spine (#1244): title page, per stage a slate and the stage,
the closing card, and the transitions placed as boundaries between
consecutive items with each side's cut, the same shape ``mp4_render``
plans for one shooter. Pure: nothing here touches ffmpeg."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith import composition
from splitsmith.compare import mp4_grid


def _tile(label: str, *, present: bool = True, lead: float = 0.0, seek: float = 0.25) -> mp4_grid.GridTile:
    return mp4_grid.GridTile(
        label=label,
        trim_path=Path(f"/trims/{label}.mov") if present else None,
        beep_offset_in_clip=1.25,
        seek_seconds=seek if present else 0.0,
        lead_pad_seconds=lead if present else 0.0,
        source_duration_seconds=6.0 if present else 0.0,
        row=0,
        col=0,
    )


def _plan(
    number: int, *, hold: float = 0.0, tiles: tuple[mp4_grid.GridTile, ...] | None = None
) -> mp4_grid.GridStagePlan:
    return mp4_grid.GridStagePlan(
        stage_number=number,
        stage_name=f"Stage {number}",
        tiles=tiles or (_tile("Ann"), _tile("Bo")),
        duration_seconds=12.5,
        audio_label="Ann",
        rows=1,
        cols=2,
        hold_seconds=hold,
    )


def _transition(seconds: float = 1.0, kind: str = "fade") -> composition.Transition:
    return composition.Transition(from_stage_index=0, to_stage_index=1, kind=kind, duration_seconds=seconds)  # type: ignore[arg-type]


def _spine(plans, *, transitions=(), slates=False, cards=False, hold=0.0, tail_pad=0.5):  # type: ignore[no-untyped-def]
    return mp4_grid.plan_grid_spine(
        plans,
        title_page=composition.MatchTitle(text="Match", duration_seconds=3.0) if cards else None,
        closing=composition.MatchTitle(text="Thanks", duration_seconds=2.0) if cards else None,
        stage_titles="slate" if slates else "none",
        title_duration_seconds=1.5,
        transitions=transitions,
        tail_pad_seconds=tail_pad,
    )


def test_head_pad_is_recovered_from_any_real_tile() -> None:
    assert mp4_grid.head_pad_of(_plan(1)) == pytest.approx(1.0)  # lead 0 + beep 1.25 - seek 0.25
    assert mp4_grid.head_pad_of(_plan(1, tiles=(_tile("Ann", lead=0.75, seek=0.0),))) == pytest.approx(2.0)
    assert mp4_grid.head_pad_of(_plan(1, tiles=(_tile("Ann", present=False),))) == 0.0


def test_the_spine_orders_cards_and_stages_like_the_driver() -> None:
    spine = _spine([_plan(1, hold=2.0), _plan(2, hold=2.0)], slates=True, cards=True)
    assert [item.name for item in spine.items] == [
        "title_page",
        "slate-stage1",
        "stage1",
        "slate-stage2",
        "stage2",
        "closing",
    ]
    assert [item.kind for item in spine.items] == [
        "title_page",
        "slate",
        "stage",
        "slate",
        "stage",
        "closing",
    ]
    assert [item.duration_seconds for item in spine.items] == [3.0, 1.5, 14.5, 1.5, 14.5, 2.0]
    assert spine.boundaries == () and spine.degradations == ()
    assert spine.duration_seconds == pytest.approx(3.0 + 1.5 + 14.5 + 1.5 + 14.5 + 2.0)


def test_a_transition_sits_between_a_stage_and_the_next_slate_and_keeps_the_length() -> None:
    plans = [_plan(1, hold=2.0), _plan(2, hold=2.0)]
    spine = _spine(plans, slates=True, cards=True, transitions=(_transition(1.0),))
    (boundary,) = spine.boundaries
    assert (boundary.after_index, boundary.kind, boundary.duration_seconds) == (2, "fade", 1.0)
    assert boundary.name == "boundary-002"
    assert spine.items[2].tail_cut_seconds == 0.5 and spine.items[3].head_cut_seconds == 0.5
    assert spine.items[2].duration_seconds == pytest.approx(14.0)
    assert spine.items[3].duration_seconds == pytest.approx(1.0)
    assert spine.boundary_after(2) is boundary and spine.boundary_after(3) is None
    assert spine.duration_seconds == pytest.approx(_spine(plans, slates=True, cards=True).duration_seconds)


def test_a_transition_between_two_bare_stages_cuts_both() -> None:
    spine = _spine([_plan(1), _plan(2)], transitions=(_transition(1.0, "dissolve"),))
    (boundary,) = spine.boundaries
    assert boundary.after_index == 0 and boundary.kind == "dissolve"
    assert [item.duration_seconds for item in spine.items] == [pytest.approx(12.0), pytest.approx(12.0)]


@pytest.mark.parametrize(
    ("plans", "kwargs", "message"),
    [
        (
            [_plan(1), _plan(2)],
            {"transitions": (_transition(2.0),), "tail_pad": 0.5},
            "transition after stage 'Stage 1' (2s) exceeds the stage's hold and tail pad (0.5s); "
            "lengthen the hold or the pad, or shorten the transition: rendered as a cut",
        ),
        (
            [_plan(1, hold=3.0), _plan(2, hold=3.0)],
            {"transitions": (_transition(3.0),)},
            "transition before stage 'Stage 2' (3s) exceeds the stage's head pad (1s); "
            "increase the pad or shorten the transition: rendered as a cut",
        ),
        (
            [_plan(1, hold=3.0), _plan(2, hold=3.0)],
            {"transitions": (_transition(4.0),), "slates": True},
            "transition into slate-stage2 (4s) exceeds half the card (0.75s): rendered as a cut",
        ),
    ],
)
def test_a_transition_that_does_not_fit_is_a_cut_and_a_degradation(plans, kwargs, message) -> None:  # type: ignore[no-untyped-def]
    """The hold counts as tail pad (the last shot is before it), the head
    pad keeps the beep out of the fade, a card must be at least d long;
    reported, never clamped."""
    spine = _spine(plans, **kwargs)
    assert spine.boundaries == ()
    assert spine.degradations == (message,)
    assert all(item.head_cut_seconds == item.tail_cut_seconds == 0.0 for item in spine.items)


def test_a_hold_long_enough_lets_a_transition_through_a_short_tail_pad() -> None:
    spine = _spine([_plan(1, hold=3.0), _plan(2, hold=3.0)], transitions=(_transition(2.0),), tail_pad=0.5)
    assert len(spine.boundaries) == 1 and spine.degradations == ()
