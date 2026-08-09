"""Share-card figures. The RULE lives in ``coach.statistic_splits`` (#774);
this module only shapes its output into a card's two headline numbers, so
these tests assert the shaping, not the rule."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from splitsmith.share_card import StageFigures, stage_figures


@dataclass(frozen=True)
class _Shot:
    """The two attributes ``coach.SplitStatInterval`` reads. A real engine
    ``Shot`` needs six more required fields that no card looks at."""

    split: float
    interval_class: str | None


# Draw 1.28, nine splits (mean 0.182), two transitions, one movement, one
# reload. The intervals sum to 14.74 s.
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


def _classified() -> tuple[_Shot, ...]:
    return tuple(_Shot(split=s, interval_class=c) for s, c in zip(_SECONDS, _CLASSES, strict=True))


def _unclassified() -> tuple[_Shot, ...]:
    return tuple(_Shot(split=s, interval_class=None) for s in _SECONDS)


def test_classified_stage_reports_the_draw_and_the_split_mean() -> None:
    figs = stage_figures(_classified())
    assert figs.source == "coach"
    assert figs.draw == pytest.approx(1.28)
    assert figs.avg_split == pytest.approx(0.182, abs=5e-4)
    assert figs.split_count == 9
    assert figs.interval_count == 14


def test_unclassified_stage_falls_back_through_the_shared_helper() -> None:
    figs = stage_figures(_unclassified())
    assert figs.source == "threshold"
    assert figs.draw == pytest.approx(1.28)
    assert figs.split_count == 9
    assert figs.avg_split == pytest.approx(0.182, abs=5e-4)


def test_the_fallback_excludes_a_draw_faster_than_the_threshold() -> None:
    """Isolates the index-0 guard: the draw must be excluded from the split
    average by *position*, not by duration.

    The fixture is synthetic, not representative -- at the 0.5 s
    ``split_max`` cutoff (#776) no realistic Production Optics draw
    (~1.0-1.5 s) could ever land here; a real draw is already excluded by
    duration alone, which would let the index-0 guard rot unnoticed. So
    the draw below is pinned under 0.5 s on purpose, to force the guard to
    be the only thing keeping it out of the average."""
    shots = (
        _Shot(split=0.40, interval_class=None),
        _Shot(split=0.20, interval_class=None),
        _Shot(split=0.20, interval_class=None),
        _Shot(split=0.20, interval_class=None),
    )
    figs = stage_figures(shots)
    assert figs.draw == pytest.approx(0.40)
    assert figs.split_count == 3
    assert figs.avg_split == pytest.approx(0.20)


def test_partial_classification_follows_mains_any_rule_see_issue_775() -> None:
    """This branch's spec argued for all-or-nothing; main counts the
    classified intervals as soon as ANY interval carries a class. Main is
    canonical, so the card follows it. The disagreement is issue #775 --
    if that issue changes the rule, this test changes with it."""
    shots = (
        _Shot(split=1.28, interval_class="first_shot"),
        _Shot(split=0.19, interval_class="split"),
        _Shot(split=0.80, interval_class=None),
    )
    figs = stage_figures(shots)
    assert figs.source == "coach"
    assert figs.split_count == 1
    assert figs.avg_split == pytest.approx(0.19)


def test_a_stage_of_pure_dead_time_reports_a_draw_but_no_average() -> None:
    shots = (
        _Shot(split=1.28, interval_class="first_shot"),
        _Shot(split=2.40, interval_class="reload"),
    )
    figs = stage_figures(shots)
    assert figs.draw == pytest.approx(1.28)
    assert figs.avg_split is None
    assert figs.split_count == 0


def test_no_shots_yields_empty_source_and_no_figures() -> None:
    figs = stage_figures(())
    assert figs == StageFigures(draw=None, avg_split=None, split_count=0, interval_count=0, source="empty")
