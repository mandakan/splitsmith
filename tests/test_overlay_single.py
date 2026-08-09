"""Runs and declarations for the single-shooter overlay (issue #684)."""

import pytest

from splitsmith.overlay_layout import Anchor, ColorToken, Role
from splitsmith.overlay_render import build_frame_states
from splitsmith.overlay_single import OverlayRun, build_overlay_runs, run_groups


def _states(shots: list[float], *, beep: float = 1.0, fps: float = 30.0, duration: float = 10.0):
    return build_frame_states(
        shot_times_in_clip=shots,
        beep_time_in_clip=beep,
        fps=fps,
        duration_seconds=duration,
    )


def test_run_count_is_one_per_distinct_shots_fired_value() -> None:
    """A 12-shot stage steps 13 times: once for the pre-beep state and
    once per shot. 600 frames collapse to 13 browser renders."""
    shots = [1.0 + 0.2 * i for i in range(12)]
    runs = build_overlay_runs(_states(shots))
    assert len(runs) == 13
    assert [r.shots_fired for r in runs] == list(range(13))


def test_run_lengths_sum_to_the_frame_count() -> None:
    """The pipe writes one buffer per frame. If the runs do not tile the
    timeline exactly, the MOV is a different length than the trim and
    drifts on the FCP timeline -- the module's first promise."""
    states = _states([1.0 + 0.2 * i for i in range(12)])
    runs = build_overlay_runs(states)
    assert sum(r.frame_count for r in runs) == len(states)


def test_runs_are_contiguous_and_start_at_zero() -> None:
    runs = build_overlay_runs(_states([1.0 + 0.2 * i for i in range(12)]))
    assert runs[0].start_frame == 0
    for earlier, later in zip(runs, runs[1:], strict=False):
        assert earlier.start_frame + earlier.frame_count == later.start_frame


def test_two_shots_inside_one_frame_are_one_boundary_not_two() -> None:
    """Run count is distinct ``shots_fired`` values, not shots plus one.
    At 30fps these two shots both land after frame 60's timestamp and
    before frame 61's, so the counter steps straight from 0 to 2 and
    there is no frame on which to draw the state in between."""
    runs = build_overlay_runs(_states([2.001, 2.005], duration=5.0))
    assert [r.shots_fired for r in runs] == [0, 2]


def test_the_split_survives_to_the_final_frame() -> None:
    """No fade: the last split stays up through the post-buffer. The grid
    convention, chosen deliberately -- a step function has no frames
    between events to ramp alpha across."""
    runs = build_overlay_runs(_states([1.2, 1.5], duration=10.0))
    assert runs[-1].last_split == pytest.approx(0.3)


def test_the_draw_is_drawn_as_shot_ones_split() -> None:
    """Shot 1 has no previous shot, so ``build_frame_states`` reports its
    time from the beep -- the draw. A single-shooter overlay shows it,
    because the draw is a number the shooter cares about. Only the
    pre-beep run has no split at all."""
    runs = build_overlay_runs(_states([1.4], duration=5.0))
    assert runs[0].last_split is None
    assert runs[1].last_split == pytest.approx(0.4)


def test_groups_put_the_counter_top_left_and_the_split_bottom_centre() -> None:
    groups = run_groups(
        OverlayRun(start_frame=0, frame_count=1, shots_fired=7, shot_count=32, last_split=0.21)
    )
    by_anchor = {g.anchor: g for g in groups}
    assert by_anchor[Anchor.TOP_LEFT].elements[0].text == "7/32"
    assert by_anchor[Anchor.BOTTOM_CENTER].elements[0].text == "0.21s"


def test_the_split_paints_in_the_split_colour_and_the_counter_does_not() -> None:
    groups = run_groups(
        OverlayRun(start_frame=0, frame_count=1, shots_fired=7, shot_count=32, last_split=0.21)
    )
    by_anchor = {g.anchor: g for g in groups}
    assert by_anchor[Anchor.BOTTOM_CENTER].elements[0].color is ColorToken.SPLIT
    assert by_anchor[Anchor.TOP_LEFT].elements[0].color is None


def test_both_elements_are_live_primary() -> None:
    groups = run_groups(
        OverlayRun(start_frame=0, frame_count=1, shots_fired=7, shot_count=32, last_split=0.21)
    )
    assert all(e.role is Role.LIVE_PRIMARY for g in groups for e in g.elements)


def test_the_counter_reads_zero_of_m_before_the_first_shot() -> None:
    """Unchanged from today, and deliberately different from the grid's
    rule. Four tiles all reading 0/32 over people standing still is
    noise; on a single-shooter frame it is the only thing on screen and
    it tells the viewer the stage's round count."""
    groups = run_groups(
        OverlayRun(start_frame=0, frame_count=30, shots_fired=0, shot_count=32, last_split=None)
    )
    assert len(groups) == 1
    assert groups[0].anchor is Anchor.TOP_LEFT
    assert groups[0].elements[0].text == "0/32"
