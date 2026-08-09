"""Split-figure derivation for the share card (spec 2026-08-09).

The coach path and the threshold fallback must disagree on a run that
contains transitions -- a test that cannot pass if classification is
being ignored.
"""

from __future__ import annotations

import pytest

from splitsmith.share_card import Interval, intervals_from_audit_shots, stage_figures

# One real-shaped run: draw 1.28, nine splits (mean 0.182), two
# transitions, one movement, one reload. Intervals sum to 14.74 s.
_SECONDS = [1.28, 0.19, 0.17, 0.22, 1.85, 0.16, 0.18, 2.42, 0.21, 0.15, 5.45, 0.20, 0.16, 2.10]
_CLASSES = [
    "first_shot",
    "split",
    "split",
    "split",
    "transition",
    "split",
    "split",
    "reload",
    "split",
    "split",
    "movement",
    "split",
    "split",
    "transition",
]


def _classified() -> tuple[Interval, ...]:
    return tuple(
        Interval(index=i + 1, seconds=s, interval_class=c)
        for i, (s, c) in enumerate(zip(_SECONDS, _CLASSES, strict=True))
    )


def _unclassified() -> tuple[Interval, ...]:
    return tuple(Interval(index=i + 1, seconds=s, interval_class=None) for i, s in enumerate(_SECONDS))


def test_coach_path_averages_only_split_intervals() -> None:
    figs = stage_figures(_classified(), transition_min=1.0)
    assert figs.source == "coach"
    assert figs.draw == pytest.approx(1.28)
    assert figs.avg_split == pytest.approx(0.182, abs=5e-4)
    assert figs.split_count == 9
    assert figs.interval_count == 14


def test_threshold_fallback_used_when_no_interval_is_classified() -> None:
    figs = stage_figures(_unclassified(), transition_min=1.0)
    assert figs.source == "threshold"
    assert figs.draw == pytest.approx(1.28)
    # Same nine sub-second intervals survive the 1.0 s cut on this run.
    assert figs.split_count == 9
    assert figs.avg_split == pytest.approx(0.182, abs=5e-4)


def test_threshold_fallback_diverges_from_coach_when_a_transition_is_short() -> None:
    """A 0.80 s transition is below transition_min, so the fallback counts
    it as a split and the coach path does not. This is the assertion that
    fails if classification is silently ignored."""
    seconds = [1.28, 0.19, 0.80, 0.17]
    classes = ["first_shot", "split", "transition", "split"]
    classified = tuple(
        Interval(index=i + 1, seconds=s, interval_class=c)
        for i, (s, c) in enumerate(zip(seconds, classes, strict=True))
    )
    unclassified = tuple(Interval(index=i + 1, seconds=s, interval_class=None) for i, s in enumerate(seconds))
    coach = stage_figures(classified, transition_min=1.0)
    threshold = stage_figures(unclassified, transition_min=1.0)
    assert coach.split_count == 2
    assert threshold.split_count == 3
    assert coach.avg_split != pytest.approx(threshold.avg_split)


def test_partial_classification_falls_back_to_threshold_for_the_whole_stage() -> None:
    """All-or-nothing: one unset interval demotes the entire run."""
    intervals = (
        Interval(index=1, seconds=1.28, interval_class="first_shot"),
        Interval(index=2, seconds=0.19, interval_class="split"),
        Interval(index=3, seconds=0.80, interval_class=None),
    )
    assert stage_figures(intervals, transition_min=1.0).source == "threshold"


def test_all_intervals_are_transitions_yields_draw_but_no_average() -> None:
    intervals = (
        Interval(index=1, seconds=1.28, interval_class="first_shot"),
        Interval(index=2, seconds=2.40, interval_class="reload"),
    )
    figs = stage_figures(intervals, transition_min=1.0)
    assert figs.draw == pytest.approx(1.28)
    assert figs.avg_split is None
    assert figs.split_count == 0


def test_threshold_fallback_excludes_the_draw_even_when_it_is_a_fast_draw() -> None:
    """The fallback guard excludes index 1 by position, not by duration. A
    draw at or under transition_min must still be kept out of the split
    average -- if the guard were dropped, avg_split would move from 0.20
    (three splits only) to 0.375 (draw folded in), which is the sharp
    signal this assertion is checking for."""
    intervals = (
        Interval(index=1, seconds=0.90, interval_class=None),
        Interval(index=2, seconds=0.20, interval_class=None),
        Interval(index=3, seconds=0.20, interval_class=None),
        Interval(index=4, seconds=0.20, interval_class=None),
    )
    figs = stage_figures(intervals, transition_min=1.0)
    assert figs.source == "threshold"
    assert figs.draw == pytest.approx(0.90)
    assert figs.split_count == 3
    assert figs.avg_split == pytest.approx(0.20)


def test_no_intervals_yields_empty_source_and_no_figures() -> None:
    figs = stage_figures((), transition_min=1.0)
    assert figs.source == "empty"
    assert figs.draw is None
    assert figs.avg_split is None
    assert figs.interval_count == 0


def test_intervals_from_audit_shots_orders_by_time_and_derives_gaps() -> None:
    shots = [
        {"shot_number": 2, "ms_after_beep": 1470, "interval_class": "split"},
        {"shot_number": 1, "ms_after_beep": 1280, "interval_class": "first_shot"},
        {"shot_number": 3, "ms_after_beep": 1640, "interval_class": "split"},
    ]
    intervals = intervals_from_audit_shots(shots)
    assert [i.index for i in intervals] == [1, 2, 3]
    assert intervals[0].seconds == pytest.approx(1.28)
    assert intervals[1].seconds == pytest.approx(0.19)
    assert intervals[2].seconds == pytest.approx(0.17)


def test_intervals_from_audit_shots_skips_entries_without_a_time() -> None:
    shots = [
        {"shot_number": 1, "ms_after_beep": 1280},
        {"shot_number": 2, "interval_class": "split"},
    ]
    assert len(intervals_from_audit_shots(shots)) == 1
