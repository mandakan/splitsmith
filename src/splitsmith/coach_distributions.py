"""Distributions over Coach interval classifications (#163).

Pure functions only. Callers feed in audit-style shot dicts (one or many
stages); we classify any unset intervals in memory using
:func:`splitsmith.coach.classify_intervals_in_dicts` so an unannotated
project still produces meaningful histograms.

Histogram bucket sizes are tuned per class so the visual lands on real
distinctions:

- Splits, transitions: 0.05 s. A shooter who runs 0.20 s splits should
  see a peak in a different bucket from one who runs 0.25.
- Movement: 0.25 s. Movement gaps span 1 - 6 s; finer buckets just spray
  the dots and hide the peak.
"""

from __future__ import annotations

import statistics
from typing import Any

from pydantic import BaseModel, Field

from .coach import (
    FIELD_INTERVAL_CLASS,
    FIELD_INTERVAL_CLASS_SOURCE,
    classify_intervals_in_dicts,
    read_coach_fields,
)
from .config import CoachAutoClassifyConfig, IntervalClass

# Bucket size per class (seconds). Movement gets a coarser bucket.
DEFAULT_BUCKET_S: dict[IntervalClass, float] = {
    "first_shot": 0.10,
    "split": 0.05,
    "transition": 0.05,
    "movement": 0.25,
    "reload": 0.25,
    "activation": 0.25,
}

# Histograms only render the four classes the auto-classifier emits plus
# reload (because manual reload overrides are common). Activation is
# rare and lives on a different timescale; keep it out of the default
# panel set so the UI doesn't sprout an empty histogram every match.
DEFAULT_HISTOGRAM_CLASSES: tuple[IntervalClass, ...] = (
    "split",
    "transition",
    "movement",
    "reload",
)


class HistogramBucket(BaseModel):
    """One bin of a histogram. ``lo`` inclusive, ``hi`` exclusive."""

    lo: float
    hi: float
    count: int


class IntervalDistribution(BaseModel):
    """Distribution of gap-times for one ``interval_class`` across one
    stage or one match.
    """

    interval_class: IntervalClass
    bucket_size_s: float
    buckets: list[HistogramBucket] = Field(default_factory=list)
    count: int = 0
    mean_s: float | None = None
    median_s: float | None = None
    p90_s: float | None = None
    p25_s: float | None = None
    p75_s: float | None = None


class TopShotEntry(BaseModel):
    """One row of "top-N longest <class>" -- aimed at coaching drilling
    into the slowest moments.
    """

    stage_number: int
    stage_name: str
    shot_number: int
    interval_class: IntervalClass
    gap_s: float
    coaching_note: str | None = None
    improvement_flag: bool = False


class FlaggedShotEntry(BaseModel):
    """One improvement-flagged shot, surfaced in the match-level panel."""

    stage_number: int
    stage_name: str
    shot_number: int
    interval_class: IntervalClass | None = None
    gap_s: float | None = None
    coaching_note: str | None = None


class StageDistributionsResponse(BaseModel):
    stage_number: int
    stage_name: str
    distributions: list[IntervalDistribution]
    first_shot_s: float | None = None


class MatchDistributionsResponse(BaseModel):
    distributions: list[IntervalDistribution]
    first_shot_seconds: list[float] = Field(default_factory=list)
    top_splits: list[TopShotEntry] = Field(default_factory=list)
    top_transitions: list[TopShotEntry] = Field(default_factory=list)
    flagged_shots: list[FlaggedShotEntry] = Field(default_factory=list)
    stage_count: int = 0
    shot_count: int = 0


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _gaps_by_class_for_stage(
    shots: list[dict[str, Any]],
    config: CoachAutoClassifyConfig,
) -> tuple[
    dict[IntervalClass, list[tuple[int, float]]],
    float | None,
]:
    """Compute gaps grouped by ``interval_class``. Returns
    ``({class: [(shot_number, gap_s), ...]}, first_shot_s)`` where the
    ``first_shot`` class is excluded from the dict (it's a stage-level
    scalar, not a distribution).
    """
    # Classify any unset intervals in memory; we never persist here.
    # ``classify_intervals_in_dicts`` mutates in place, so deep-copy the
    # dicts to avoid leaking auto entries back to the caller's audit
    # JSON, then re-read the stored class field directly.
    working = [dict(s) for s in shots if isinstance(s, dict) and "ms_after_beep" in s]
    classify_intervals_in_dicts(working, config)
    grouped: dict[IntervalClass, list[tuple[int, float]]] = {}
    first_shot_s: float | None = None
    ordered = sorted(working, key=lambda s: float(s.get("ms_after_beep", 0)))
    prev_ms: float | None = None
    for s in ordered:
        ms = float(s["ms_after_beep"])
        cls_raw = s.get(FIELD_INTERVAL_CLASS)
        cls: IntervalClass | None = cls_raw if cls_raw is not None else None
        if cls == "first_shot":
            first_shot_s = ms / 1000.0
            prev_ms = ms
            continue
        if prev_ms is None:
            prev_ms = ms
            continue
        gap = (ms - prev_ms) / 1000.0
        prev_ms = ms
        if cls is None:
            continue
        grouped.setdefault(cls, []).append((int(s.get("shot_number", 0)), gap))
    return grouped, first_shot_s


def _bucket_values(values: list[float], bucket_size: float) -> list[HistogramBucket]:
    """Bin values into fixed-size buckets starting at 0. Buckets with
    zero count are dropped to keep the wire payload small.
    """
    if not values:
        return []
    counts: dict[int, int] = {}
    for v in values:
        idx = int(v // bucket_size)
        counts[idx] = counts.get(idx, 0) + 1
    return [
        HistogramBucket(
            lo=idx * bucket_size,
            hi=(idx + 1) * bucket_size,
            count=count,
        )
        for idx, count in sorted(counts.items())
    ]


def _summarize(
    values: list[float],
) -> tuple[float | None, float | None, float | None, float | None, float | None]:
    if not values:
        return None, None, None, None, None
    mean = statistics.fmean(values)
    median = statistics.median(values)
    if len(values) >= 2:
        # quantiles(n=10) returns 9 cut-points; the 9th == 90th percentile.
        # Pad with the max for tiny lists where quantiles can't compute.
        p90 = statistics.quantiles(values, n=10)[8]
        # quantiles(n=4) returns 3 cut-points: index 0 == p25, index 2 == p75.
        # Used by the self-relative split tier labels (quick/typical/long).
        quartiles = statistics.quantiles(values, n=4)
        p25 = quartiles[0]
        p75 = quartiles[2]
    else:
        p90 = values[0]
        p25 = None
        p75 = None
    return mean, median, p90, p25, p75


def _build_distribution(
    cls: IntervalClass,
    values: list[float],
) -> IntervalDistribution:
    bucket = DEFAULT_BUCKET_S[cls]
    mean, median, p90, p25, p75 = _summarize(values)
    return IntervalDistribution(
        interval_class=cls,
        bucket_size_s=bucket,
        buckets=_bucket_values(values, bucket),
        count=len(values),
        mean_s=mean,
        median_s=median,
        p90_s=p90,
        p25_s=p25,
        p75_s=p75,
    )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def stage_distributions(
    *,
    stage_number: int,
    stage_name: str,
    shots: list[dict[str, Any]],
    config: CoachAutoClassifyConfig,
    classes: tuple[IntervalClass, ...] = DEFAULT_HISTOGRAM_CLASSES,
) -> StageDistributionsResponse:
    grouped, first_shot_s = _gaps_by_class_for_stage(shots, config)
    distributions = [_build_distribution(cls, [g for _, g in grouped.get(cls, [])]) for cls in classes]
    return StageDistributionsResponse(
        stage_number=stage_number,
        stage_name=stage_name,
        distributions=distributions,
        first_shot_s=first_shot_s,
    )


def match_distributions(
    *,
    stages: list[tuple[int, str, list[dict[str, Any]]]],
    config: CoachAutoClassifyConfig,
    classes: tuple[IntervalClass, ...] = DEFAULT_HISTOGRAM_CLASSES,
    top_n: int = 5,
) -> MatchDistributionsResponse:
    """Aggregate distributions across an entire match.

    ``stages`` is a list of ``(stage_number, stage_name, shots)`` tuples.
    Stages with no shots are skipped entirely; they don't contribute
    empty buckets that misrepresent the shooter's average.
    """
    aggregate: dict[IntervalClass, list[float]] = {cls: [] for cls in classes}
    first_shot_seconds: list[float] = []
    top_split_pool: list[TopShotEntry] = []
    top_trans_pool: list[TopShotEntry] = []
    flagged: list[FlaggedShotEntry] = []
    shot_count = 0
    stage_count = 0

    for stage_number, stage_name, shots in stages:
        if not shots:
            continue
        stage_count += 1
        # Walk shots once: build aggregate distributions, top-N candidates,
        # flagged list. Re-deriving classes via the same pure helper is
        # cheaper than threading state out of stage_distributions.
        grouped, first_shot_s = _gaps_by_class_for_stage(shots, config)
        if first_shot_s is not None:
            first_shot_seconds.append(first_shot_s)

        for cls, entries in grouped.items():
            shot_count += len(entries)
            if cls in aggregate:
                aggregate[cls].extend(g for _, g in entries)

        # Top-N pool. Build TopShotEntry against the original shots so
        # we have the user's notes / flag intact; lookup by shot_number.
        by_num = {int(s.get("shot_number", -1)): s for s in shots if isinstance(s, dict)}
        for cls in ("split", "transition"):
            entries = grouped.get(cls, [])
            for shot_number, gap in entries:
                raw = by_num.get(shot_number, {})
                fields = read_coach_fields(raw)
                entry = TopShotEntry(
                    stage_number=stage_number,
                    stage_name=stage_name,
                    shot_number=shot_number,
                    interval_class=cls,
                    gap_s=gap,
                    coaching_note=fields.get("coaching_note"),
                    improvement_flag=bool(fields.get("improvement_flag", False)),
                )
                if cls == "split":
                    top_split_pool.append(entry)
                else:
                    top_trans_pool.append(entry)

        # Flagged list -- include every flagged shot regardless of class.
        # We deliberately skip ``classify_intervals_in_dicts`` here and
        # read the user's stored fields directly so we don't surface
        # a class the user explicitly never set.
        prev_ms: float | None = None
        ordered = sorted(
            (s for s in shots if isinstance(s, dict) and "ms_after_beep" in s),
            key=lambda s: float(s.get("ms_after_beep", 0)),
        )
        for s in ordered:
            ms = float(s["ms_after_beep"])
            gap_s: float | None
            if prev_ms is None:
                gap_s = None
            else:
                gap_s = (ms - prev_ms) / 1000.0
            prev_ms = ms
            if not s.get("improvement_flag", False):
                continue
            stored_class = s.get(FIELD_INTERVAL_CLASS)
            stored_source = s.get(FIELD_INTERVAL_CLASS_SOURCE)
            cls_for_entry: IntervalClass | None
            if stored_class and stored_source is not None:
                cls_for_entry = stored_class
            else:
                cls_for_entry = None
            flagged.append(
                FlaggedShotEntry(
                    stage_number=stage_number,
                    stage_name=stage_name,
                    shot_number=int(s.get("shot_number", 0)),
                    interval_class=cls_for_entry,
                    gap_s=gap_s,
                    coaching_note=(
                        s.get("coaching_note") if isinstance(s.get("coaching_note"), str) else None
                    ),
                )
            )

    distributions = [_build_distribution(cls, aggregate[cls]) for cls in classes]
    top_splits = sorted(top_split_pool, key=lambda e: e.gap_s, reverse=True)[:top_n]
    top_transitions = sorted(top_trans_pool, key=lambda e: e.gap_s, reverse=True)[:top_n]

    return MatchDistributionsResponse(
        distributions=distributions,
        first_shot_seconds=first_shot_seconds,
        top_splits=top_splits,
        top_transitions=top_transitions,
        flagged_shots=flagged,
        stage_count=stage_count,
        shot_count=shot_count,
    )
