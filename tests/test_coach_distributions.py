"""Tests for the Coach distributions module (#163)."""

from __future__ import annotations

from typing import Any

import pytest

from splitsmith.coach_distributions import (
    DEFAULT_BUCKET_S,
    DEFAULT_HISTOGRAM_CLASSES,
    match_distributions,
    stage_distributions,
)
from splitsmith.config import CoachAutoClassifyConfig


@pytest.fixture
def cfg() -> CoachAutoClassifyConfig:
    return CoachAutoClassifyConfig()


def _shot(n: int, ms: int, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"shot_number": n, "ms_after_beep": ms}
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Per-stage distributions
# ---------------------------------------------------------------------------


def test_stage_distribution_classifies_unset_in_memory(cfg: CoachAutoClassifyConfig) -> None:
    # Three splits + a transition + a movement, all unset; the helper
    # classifies them in memory without persisting anything.
    shots = [
        _shot(1, 1500),
        _shot(2, 1700),  # 0.20 split
        _shot(3, 1900),  # 0.20 split
        _shot(4, 2100),  # 0.20 split
        _shot(5, 2900),  # 0.80 transition
        _shot(6, 4500),  # 1.60 movement
    ]
    out = stage_distributions(
        stage_number=1,
        stage_name="K-vallen",
        shots=shots,
        config=cfg,
    )
    assert out.first_shot_s == pytest.approx(1.5)
    by_class = {d.interval_class: d for d in out.distributions}
    assert by_class["split"].count == 3
    assert by_class["transition"].count == 1
    assert by_class["movement"].count == 1
    # No persistence: the input dicts are untouched.
    assert all("interval_class" not in s for s in shots)


def test_stage_distribution_buckets_seeded_from_class(cfg: CoachAutoClassifyConfig) -> None:
    # All splits hit the same 0.05 s bucket (0.20-0.25).
    shots = [_shot(1, 1500), _shot(2, 1700), _shot(3, 1900), _shot(4, 2100)]
    out = stage_distributions(stage_number=1, stage_name="x", shots=shots, config=cfg)
    splits = next(d for d in out.distributions if d.interval_class == "split")
    assert splits.bucket_size_s == DEFAULT_BUCKET_S["split"]
    assert len(splits.buckets) == 1
    assert splits.buckets[0].lo == pytest.approx(0.20)
    assert splits.buckets[0].count == 3


def test_stage_distribution_summary_stats(cfg: CoachAutoClassifyConfig) -> None:
    # Splits at 0.20, 0.30, 0.40 -> mean 0.30, median 0.30. p90 with
    # statistics.quantiles' default "exclusive" method extrapolates
    # beyond the max for small samples; we just assert it lands above
    # the median, since the user-facing value is "rough p90 hint".
    shots = [_shot(1, 1500), _shot(2, 1700), _shot(3, 2000), _shot(4, 2400)]
    out = stage_distributions(stage_number=1, stage_name="x", shots=shots, config=cfg)
    splits = next(d for d in out.distributions if d.interval_class == "split")
    assert splits.count == 3
    assert splits.mean_s == pytest.approx(0.30, abs=1e-9)
    assert splits.median_s == pytest.approx(0.30, abs=1e-9)
    assert splits.p90_s is not None and splits.p90_s > splits.median_s


def test_stage_distribution_empty_class_has_zero_count(cfg: CoachAutoClassifyConfig) -> None:
    # Just three splits -- transition and movement should still appear in
    # the response with count=0 so the UI can render an empty histogram.
    shots = [_shot(1, 1500), _shot(2, 1700), _shot(3, 1900), _shot(4, 2100)]
    out = stage_distributions(stage_number=1, stage_name="x", shots=shots, config=cfg)
    classes = {d.interval_class for d in out.distributions}
    assert classes == set(DEFAULT_HISTOGRAM_CLASSES)
    transitions = next(d for d in out.distributions if d.interval_class == "transition")
    assert transitions.count == 0
    assert transitions.buckets == []
    assert transitions.mean_s is None


def test_stage_distribution_preserves_manual_class(cfg: CoachAutoClassifyConfig) -> None:
    # Manual reload override on a long gap -- the auto rule would call
    # this "movement"; manual sticks and the gap is counted under reload.
    shots = [
        _shot(1, 1500),
        _shot(
            2,
            5000,
            interval_class="reload",
            interval_class_source="manual",
        ),
    ]
    out = stage_distributions(stage_number=1, stage_name="x", shots=shots, config=cfg)
    by_class = {d.interval_class: d for d in out.distributions}
    assert by_class["reload"].count == 1
    assert by_class["movement"].count == 0


# ---------------------------------------------------------------------------
# Match-level aggregation
# ---------------------------------------------------------------------------


def test_match_distribution_aggregates_across_stages(cfg: CoachAutoClassifyConfig) -> None:
    stage_a = [_shot(1, 1500), _shot(2, 1700), _shot(3, 1900)]  # 2 splits
    stage_b = [_shot(1, 2000), _shot(2, 2300), _shot(3, 2600)]  # 2 splits
    out = match_distributions(
        stages=[(1, "A", stage_a), (2, "B", stage_b)],
        config=cfg,
    )
    by_class = {d.interval_class: d for d in out.distributions}
    assert by_class["split"].count == 4
    assert out.stage_count == 2
    assert out.first_shot_seconds == pytest.approx([1.5, 2.0])


def test_match_top_shots_sorted_desc(cfg: CoachAutoClassifyConfig) -> None:
    # Build a stage with three transitions of increasing length so we
    # can verify top-N ordering.
    shots = [
        _shot(1, 1500),
        _shot(2, 2200),  # 0.70 transition
        _shot(3, 3000),  # 0.80 transition
        _shot(4, 3950),  # 0.95 transition
    ]
    out = match_distributions(
        stages=[(1, "A", shots)],
        config=cfg,
        top_n=3,
    )
    assert [e.gap_s for e in out.top_transitions] == pytest.approx([0.95, 0.80, 0.70])
    assert all(e.interval_class == "transition" for e in out.top_transitions)


def test_match_flagged_list_filters_by_flag(cfg: CoachAutoClassifyConfig) -> None:
    shots = [
        _shot(1, 1500, improvement_flag=True, coaching_note="slow draw"),
        _shot(2, 1800),
        _shot(3, 2100, improvement_flag=True),
    ]
    out = match_distributions(stages=[(1, "A", shots)], config=cfg)
    assert {e.shot_number for e in out.flagged_shots} == {1, 3}
    n1 = next(e for e in out.flagged_shots if e.shot_number == 1)
    assert n1.coaching_note == "slow draw"
    # Shot 1 is the first shot -> gap is None on the flagged entry.
    assert n1.gap_s is None
    n3 = next(e for e in out.flagged_shots if e.shot_number == 3)
    assert n3.gap_s == pytest.approx(0.30)


def test_match_skips_empty_stage(cfg: CoachAutoClassifyConfig) -> None:
    # A stage with no shots shouldn't contribute -- it's not an audited
    # stage yet, so it would just dilute the average.
    out = match_distributions(
        stages=[(1, "A", [_shot(1, 1500), _shot(2, 1700)]), (2, "B", [])],
        config=cfg,
    )
    assert out.stage_count == 1
