"""Stage details (spec ``docs/superpowers/specs/2026-10-22-stage-details-design.md``):
the scoring rules behind a shooter's typed or pasted stage data.

Pure: a scorecard, a time and a power factor in, a scorecard out. A value
the user typed is listed in ``StageScorecard.manual`` and never computed
over; points and hit factor are filled only where they are empty or were
filled here before (``StageScorecard.derived``), so a scoreboard's own
figures stay as the scoreboard sent them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .match_project import MatchProject, StageScorecard

PowerFactor = Literal["minor", "major"]

#: The hit and penalty counts points are worked out from.
HIT_FIELDS: tuple[str, ...] = ("alphas", "charlies", "deltas", "misses", "no_shoots", "procedurals")
#: Every value field of a scorecard the page edits.
SCORE_FIELDS: tuple[str, ...] = (*HIT_FIELDS, "stage_points", "hit_factor", "stage_pct", "dq")

_VALUES: dict[PowerFactor, dict[str, int]] = {
    "minor": {"alphas": 5, "charlies": 3, "deltas": 1, "misses": -10, "no_shoots": -10, "procedurals": -10},
    "major": {"alphas": 5, "charlies": 4, "deltas": 2, "misses": -10, "no_shoots": -10, "procedurals": -10},
}


def power_factor_for(division: str | None, override: str | None) -> PowerFactor:
    """The override when set; otherwise major when the division's name says
    so (SSI spells the power factor into a division that allows both)."""
    if override in ("minor", "major"):
        return override  # type: ignore[return-value]
    return "major" if division and "major" in division.lower() else "minor"


def points_from_hits(card: StageScorecard, pf: PowerFactor) -> float | None:
    """IPSC points, floored at zero; None unless every count is known."""
    counts = [getattr(card, f) for f in HIT_FIELDS]
    if any(c is None for c in counts):
        return None
    total = sum(_VALUES[pf][f] * c for f, c in zip(HIT_FIELDS, counts, strict=True))
    return float(max(total, 0))


def hit_factor(points: float | None, time_seconds: float) -> float | None:
    if points is None or time_seconds <= 0:
        return None
    return round(points / time_seconds, 4)


def derive(card: StageScorecard, time_seconds: float, pf: PowerFactor) -> StageScorecard:
    """``card`` with points and hit factor worked out where allowed."""
    out = card.model_copy(deep=True)
    derived = set(out.derived)

    def put(field: str, value: float | None) -> None:
        if field in out.manual:
            derived.discard(field)
            return
        if getattr(out, field) is not None and field not in derived:
            return  # the scoreboard's own figure
        setattr(out, field, value)
        if value is None:
            derived.discard(field)
        else:
            derived.add(field)

    put("stage_points", points_from_hits(out, pf))
    put("hit_factor", hit_factor(out.stage_points, time_seconds))
    out.derived = sorted(derived)
    return out


def scorecard_order(project: MatchProject) -> list[int]:
    """The shooter's stages in the order they were scored, the order they
    were most likely shot in."""
    timed = [s for s in project.stages if s.scorecard_updated_at is not None]
    return [s.stage_number for s in sorted(timed, key=lambda s: s.scorecard_updated_at)]  # type: ignore[arg-type,return-value]
