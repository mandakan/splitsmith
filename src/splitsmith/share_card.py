"""Share-card figures and models (spec 2026-08-09).

Pure: no file I/O, no browser, no FastAPI. Rasterizing a card is
``share_card_html`` plus ``overlay_raster``'s job; serving one is
``ui/share_og.py``'s.

**This module does not define the split rule.** That rule lives in
``coach.statistic_splits`` (issue #772, landed in #774) and is mirrored
in TS by ``statisticSplits``. This module only shapes the helper's
output into a card's two headline figures: the draw and the split mean,
plus their provenance. What happens on a partially classified stage is
that shared helper's call, not this module's -- see issue #775.

Issue #772 brings the video stage summary and the results page onto
that same definition; both consume :func:`stage_figures`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .coach import SPLIT_STAT_TRANSITION_MIN, SplitStatInterval, statistic_splits

FigureSource = Literal["coach", "threshold", "empty"]


@dataclass(frozen=True)
class StageFigures:
    """What a stage card puts on screen, and how it was derived."""

    draw: float | None
    avg_split: float | None
    split_count: int
    interval_count: int
    source: FigureSource


def stage_figures(
    shots: Sequence[SplitStatInterval],
    *,
    transition_min: float = SPLIT_STAT_TRANSITION_MIN,
) -> StageFigures:
    """The two headline figures a stage card shows, plus their provenance.

    **This function does not own the split rule.** ``coach.statistic_splits``
    does (issue #772, landed in #774), mirrored in TS by ``statisticSplits``.
    All this adds is the card's shape: the draw, the mean of whatever the
    shared helper returned, and how it was derived.

    ``shots`` is one stage's full time-ordered sequence, draw first --
    ``config.Shot`` from ``audit_data.audit_shots_to_engine_shots`` satisfies
    the protocol. The draw is ``shots[0].split``, matching the helper's own
    "index 0 is the draw" convention.

    ``avg_split`` is None rather than zero when the helper returns nothing:
    a stage of transitions and reloads has no splits to average, and the
    card renders no average rather than inventing one.
    """
    if not shots:
        return StageFigures(draw=None, avg_split=None, split_count=0, interval_count=0, source="empty")
    splits = statistic_splits(shots, transition_min=transition_min)
    classified = any(s.interval_class is not None for s in shots)
    return StageFigures(
        draw=shots[0].split,
        avg_split=(sum(splits) / len(splits)) if splits else None,
        split_count=len(splits),
        interval_count=len(shots),
        # Mirrors the helper's own branch condition. If #775 changes that
        # condition, this line changes with it -- they must not drift.
        source="coach" if classified else "threshold",
    )


class RosterEntry(BaseModel):
    """One shooter on a match card."""

    model_config = ConfigDict(frozen=True)

    name: str
    division: str | None = None


class MatchCard(BaseModel):
    """The top-level share card: identity plus who is in the match.

    No aggregate time figure by design -- IPSC ranks by hit factor and
    match percentage, so summed stage time is not a number the sport
    produces. See the spec's "Match card -- roster" section.
    """

    model_config = ConfigDict(frozen=True)

    match_name: str
    match_date: str | None = None
    stage_count: int
    roster: list[RosterEntry] = Field(default_factory=list)

    @field_validator("roster")
    @classmethod
    def _sorted_roster(cls, value: list[RosterEntry]) -> list[RosterEntry]:
        """Alphabetical by name, matching the slot-order convention
        ``compare/`` already uses so a roster never reshuffles.

        This validator runs on construction and on ``model_validate`` --
        the two paths a card normally travels. It does **not** run on
        ``model_copy(update={"roster": ...})``: Pydantic v2 gives frozen
        models no hook for that path, so a copy-update with an out-of-order
        list is not re-sorted and not rejected. Callers that need to change
        a roster must rebuild the ``MatchCard`` (or the ``roster`` list)
        rather than ``model_copy``-update it.
        """
        return sorted(value, key=lambda r: r.name)


class StageCard(BaseModel):
    """One shooter's run on one stage."""

    model_config = ConfigDict(frozen=True)

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
