"""Share-card figures and models (spec 2026-08-09).

Pure: no file I/O, no browser, no FastAPI. Rasterizing a card is
``share_card_html`` plus ``overlay_raster``'s job; serving one is
``ui/share_og.py``'s.

**One definition of a split.** A split statistic is computed over
intervals classed ``split`` -- transitions, movement, reloads and
activations are excluded by construction rather than by a threshold.
The draw is the ``first_shot`` interval. When a stage carries no
classification at all (detected and audited, never coached), the
fallback is the rule ``fcpxml_gen.split_color_band`` already encodes:
index 1 is the draw, and any interval above
``SplitColorThresholds.transition_min`` is not a split.

Classification is all-or-nothing per stage. Mixing the two rules within
one run would produce an average whose definition varies by which
intervals happened to be reviewed, so a single unset interval demotes
the whole stage to the threshold path. :attr:`StageFigures.source`
records which path ran.

Issue #772 brings the video stage summary and the results page onto
this same definition; both consume :func:`stage_figures`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

#: The interval class naming the draw. Mirrors ``CoachIntervalClass`` in
#: ``ui_static/src/lib/api.ts``; ``coach.py`` owns the Python side.
DRAW_CLASS = "first_shot"

#: The only class a split statistic is computed over.
SPLIT_CLASS = "split"

FigureSource = Literal["coach", "threshold", "empty"]


@dataclass(frozen=True)
class Interval:
    """One inter-shot gap. ``index`` is 1-based; index 1 is the draw, so
    its ``seconds`` is time from the beep rather than from a prior shot."""

    index: int
    seconds: float
    interval_class: str | None


@dataclass(frozen=True)
class StageFigures:
    """What a stage card puts on screen, and how it was derived."""

    draw: float | None
    avg_split: float | None
    split_count: int
    interval_count: int
    source: FigureSource


def intervals_from_audit_shots(shots: Sequence[Mapping[str, Any]]) -> tuple[Interval, ...]:
    """Derive ordered intervals from an audit doc's ``shots`` list.

    Mirrors the ordering and gap arithmetic ``ui/server._build_coach_response``
    applies: sort by ``ms_after_beep``, treat shot 1's time from the beep as
    its interval, and take each later shot's gap from its predecessor.
    Entries without ``ms_after_beep`` are not shots on a timeline and are
    dropped rather than defaulted to zero.
    """
    ordered = sorted(
        (s for s in shots if isinstance(s, Mapping) and s.get("ms_after_beep") is not None),
        key=lambda s: float(s["ms_after_beep"]),
    )
    out: list[Interval] = []
    prev_ms: float | None = None
    for i, shot in enumerate(ordered):
        ms = float(shot["ms_after_beep"])
        seconds = ms / 1000.0 if prev_ms is None else (ms - prev_ms) / 1000.0
        prev_ms = ms
        cls = shot.get("interval_class")
        out.append(
            Interval(
                index=i + 1,
                seconds=seconds,
                interval_class=cls if isinstance(cls, str) else None,
            )
        )
    return tuple(out)


def stage_figures(intervals: Sequence[Interval], *, transition_min: float) -> StageFigures:
    """Draw and average split for one run.

    ``transition_min`` comes from ``SplitColorThresholds`` and is only
    consulted on the fallback path. Passing it explicitly keeps this
    function pure and lets a caller A/B a candidate value (#773) without
    touching config.
    """
    if not intervals:
        return StageFigures(draw=None, avg_split=None, split_count=0, interval_count=0, source="empty")

    classified = all(i.interval_class is not None for i in intervals)
    if classified:
        source: FigureSource = "coach"
        draw = next((i.seconds for i in intervals if i.interval_class == DRAW_CLASS), None)
        splits = [i.seconds for i in intervals if i.interval_class == SPLIT_CLASS]
    else:
        source = "threshold"
        draw = next((i.seconds for i in intervals if i.index == 1), None)
        splits = [i.seconds for i in intervals if i.index != 1 and i.seconds <= transition_min]

    return StageFigures(
        draw=draw,
        avg_split=(sum(splits) / len(splits)) if splits else None,
        split_count=len(splits),
        interval_count=len(intervals),
        source=source,
    )


class RosterEntry(BaseModel):
    """One shooter on a match card."""

    name: str
    division: str | None = None


class MatchCard(BaseModel):
    """The top-level share card: identity plus who is in the match.

    No aggregate time figure by design -- IPSC ranks by hit factor and
    match percentage, so summed stage time is not a number the sport
    produces. See the spec's "Match card -- roster" section.
    """

    match_name: str
    match_date: str | None = None
    stage_count: int
    roster: list[RosterEntry] = Field(default_factory=list)

    @field_validator("roster")
    @classmethod
    def _sorted_roster(cls, value: list[RosterEntry]) -> list[RosterEntry]:
        """Alphabetical by name, matching the slot-order convention
        ``compare/`` already uses so a roster never reshuffles."""
        return sorted(value, key=lambda r: r.name)


class StageCard(BaseModel):
    """One shooter's run on one stage."""

    stage_number: int
    stage_name: str
    shooter_name: str
    match_name: str
    shot_count: int
    stage_time: float | None = None
    figures: StageFigures


def card_hash(card: MatchCard | StageCard) -> str:
    """Content hash over everything the card displays.

    The ``og:image`` URL carries this, so a re-audit that moves any
    displayed figure moves the URL and crawlers refetch instead of
    serving a stale preview. Sixteen hex characters: collision risk is
    negligible for a per-token keyspace and the URL stays readable.
    """
    payload = json.dumps(card.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
