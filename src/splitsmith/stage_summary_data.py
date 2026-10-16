"""What the overlay knows about one shooter on one stage, read off disk.

Hoisted out of ``compare/overlay_data.py`` (issue #972) so the
single-shooter export's stage summary can read the same shape without
core code importing from ``compare/``. The grid module re-exports these
names and wraps :func:`load_stage_shots` for its own bundles, so nothing
about the grid's behaviour moved.

Everything here is offline. The renderer is batch and must never reach a
network service mid-render, so scoring comes from the ``MatchProject``
already on disk rather than from the scoreboard.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .audit_data import audit_shots_to_engine_shots, read_audit_data
from .coach import heal_unclassified, statistic_split_shots
from .config import IntervalClass, StageEvent, StageRounds
from .events import ReloadFigure, confirmed_from_doc, exposed_reload_s, reload_figures, shot_is_moving
from .match_project import StageScorecard, is_stub_audit

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TileShot:
    """One accepted shot, measured from the beep.

    ``time_from_beep`` is seconds after the start signal. It is
    independent of the trim, the head pad and
    :attr:`CompareStageBundle.beep_offset_in_clip` -- that field converts
    to *clip-local* time, which is a different origin, and the grid's own
    head pad is applied later by the sprite builder.

    There is deliberately no shot number here. The sequence is ordered by
    time and the overlay counts what has been fired, so an index over
    this tuple is all any caller needs. A stored number would have been
    that index plus one, which disagrees with the audit's own
    ``shot_number`` on exactly the input that motivated re-deriving the
    splits -- an audit whose row order is not its time order.

    ``interval_class`` is the Coach annotation when the audit carries one
    (#772): the split statistics need it to tell a split from a reload.
    ``None`` means unclassified, and the statistics fall back to a
    threshold rule - see :func:`splitsmith.coach.statistic_splits`.

    ``moving`` is true when the shot falls inside a confirmed movement
    region (``events.shot_is_moving`` over ``events.confirmed``); the stage
    summary splits its statistics into static and moving rows by it.
    """

    time_from_beep: float
    split: float
    interval_class: IntervalClass | None = None
    moving: bool = False


@dataclass(frozen=True)
class TileStageData:
    """Everything the overlay knows about one shooter on one stage.

    Every field is optional in practice: a shooter can have a trim with no
    audit, an audit with no scorecard, or a manually entered stage time
    with neither. Absent data stays absent -- nothing here substitutes a
    zero for a number that was never read.
    """

    label: str
    stage_number: int
    shots: tuple[TileShot, ...] = ()
    stage_time_seconds: float | None = None
    stage_time_is_manual: bool = False
    scorecard: StageScorecard | None = None
    stage_rounds: StageRounds | None = None
    #: The stage's confirmed reloads, in time order
    #: (:func:`load_stage_reloads`). Empty when there are none, which is
    #: every stage nobody marked up: the summary then draws no reload row.
    reloads: tuple[ReloadFigure, ...] = ()
    #: The stage's confirmed regions (:func:`load_stage_events`), for a
    #: summary template's ``data.stage``. The engine card reads ``reloads``
    #: and ``shots[].moving`` and never this.
    events: tuple[StageEvent, ...] = ()

    @property
    def shot_count(self) -> int:
        return len(self.shots)

    @property
    def has_shots(self) -> bool:
        return bool(self.shots)

    @property
    def last_shot_time(self) -> float | None:
        return self.shots[-1].time_from_beep if self.shots else None


def _confirmed_events(audit_data: dict[str, Any], audit_path: Path) -> list[StageEvent]:
    """The doc's confirmed regions. A corrupt events list must not cost the
    stage its shots (one bad doc, a 12-stage render): it reads as none, the
    way the HUD render and the export preview treat it
    (``events.confirmed_from_doc``)."""
    return confirmed_from_doc(audit_data, log_context=f"stage summary: {audit_path}")


def load_stage_reloads(audit_path: Path) -> tuple[ReloadFigure, ...]:
    """This stage's confirmed reloads as :class:`~splitsmith.events.ReloadFigure`
    records (duration, exposed time against the confirmed movements).
    An auto proposal is never here. Missing, unreadable or corrupt reads as
    none."""
    try:
        audit_data = read_audit_data(audit_path)
    except Exception:  # noqa: BLE001 -- load_stage_shots already says why this file is unreadable
        return ()
    if not isinstance(audit_data, dict):
        return ()
    return tuple(reload_figures(_confirmed_events(audit_data, audit_path)))


def load_stage_events(audit_path: Path) -> tuple[StageEvent, ...]:
    """This stage's confirmed regions. Missing, unreadable or corrupt reads
    as none, as :func:`load_stage_reloads` does."""
    try:
        audit_data = read_audit_data(audit_path)
    except Exception:  # noqa: BLE001 -- load_stage_shots already says why this file is unreadable
        return ()
    if not isinstance(audit_data, dict):
        return ()
    return tuple(_confirmed_events(audit_data, audit_path))


def load_stage_shots(audit_path: Path) -> tuple[TileShot, ...]:
    """Read this stage's audited shots, measured from the beep.

    ``beep_time_in_source=0.0`` makes the engine's ``time_absolute``
    degenerate to ``time_from_beep`` so nothing downstream can mistake it
    for a source-absolute value. A corrupt audit degrades to no shots:
    one bad file must not fail a 12-stage render.

    Splits are re-derived over the time-sorted sequence rather than taken
    from ``audit_shots_to_engine_shots``, which is the one audit consumer
    in this codebase that orders by ``shot_number`` instead of by time
    (``ui/server.py`` and ``coach_distributions`` both sort by time). The
    two orderings agree on every audit a detector writes, but
    ``audit.py``'s CSV apply preserves row order as ``shot_number``, so a
    hand-sorted prep sheet can land shots out of time order -- and the
    helper's splits would then be differences between non-adjacent shots,
    including negative ones. The overlay draws the split on screen, so a
    wrong number is worse than no number. Parsing and the helper's
    rejection filtering are still its job; only ``split`` is recomputed.

    #775: an audited stage is an invariant of "fully classified", but a
    legacy doc on disk can still be partially classified. When that is
    true here, ``coach.heal_unclassified`` runs the raw shot dicts through
    the in-memory classifier before conversion - otherwise
    ``statistic_splits`` (overlay_summary.py) sees a mixed doc and renders
    the wrong average into the exported MP4. That helper is where "needs a
    heal" is defined for every surface (#780); this path never writes back
    to disk, so it ignores the return value. Only the coach GET/PUT
    persist a heal.
    """
    try:
        audit_data = read_audit_data(audit_path)
        if not isinstance(audit_data, dict):
            # Valid JSON, wrong shape. ``read_audit_data`` returns whatever
            # ``json.loads`` produced, so a list/null/string audit would
            # otherwise reach ``.get`` and raise past this handler.
            raise TypeError(f"audit JSON is {type(audit_data).__name__}, expected object")
        if is_stub_audit(audit_data):
            # A beep-confirm placeholder means the same thing as no audit.
            # Belt and braces today: ``is_stub_audit`` requires an empty
            # ``shots``, so this branch can never change the result while
            # that definition holds. It is here so the intent survives a
            # future loosening of the sentinel rather than becoming a
            # silent bug.
            return ()
        # Heal in memory only - see the #775 note above. The guard lives in
        # ``coach.heal_unclassified`` (#780); its return value is ignored
        # here because this path never writes back.
        heal_unclassified(audit_data.get("shots"))
        engine_shots = audit_shots_to_engine_shots(audit_data, beep_time_in_source=0.0)
        regions = _confirmed_events(audit_data, audit_path)
    except Exception as exc:  # noqa: BLE001 -- one bad file must not fail the render
        logger.warning(
            "stage summary: unreadable audit %s (%s); rendering this stage without shots",
            audit_path,
            exc,
        )
        return ()
    ordered = sorted(engine_shots, key=lambda s: s.time_from_beep)
    shots: list[TileShot] = []
    previous: float | None = None
    for shot in ordered:
        # Shot 1's split is the draw; every later split is the gap from
        # the shot before it in time order.
        split = shot.time_from_beep if previous is None else shot.time_from_beep - previous
        previous = shot.time_from_beep
        shots.append(
            TileShot(
                time_from_beep=shot.time_from_beep,
                split=split,
                interval_class=shot.interval_class,
                moving=shot_is_moving(shot.time_from_beep, regions),
            )
        )
    return tuple(shots)


class SplitFigures(BaseModel):
    """Best / Avg / Worst over a set of splits, and how many there were."""

    best: float
    avg: float
    worst: float
    n: int


class ReloadSummary(BaseModel):
    """The stage's confirmed reloads: count, mean duration, exposed time."""

    count: int
    avg: float
    exposed: float


class StageSummaryFigures(BaseModel):
    """The numbers a stage summary shows, as data (spec
    ``2026-10-16-summary-templates-design``): what a summary template reads
    as ``data.figures``. Each comes from the rule that owns it, the one the
    engine card calls too (``coach.statistic_split_shots``,
    ``events.exposed_reload_s``, the scorecard), so a template and the
    engine card never disagree; ``tests/test_stage_summary_figures.py`` holds
    the two side by side. Absent stays ``None``: nothing here invents a zero.
    A DQ keeps its time and drops its scoring, as the engine card does."""

    time_seconds: float | None = None
    time_is_manual: bool = False
    hit_factor: float | None = None
    stage_pct: float | None = None
    points: float | None = None
    #: The scorecard's counts, by tag (A, C, D, M, NS, P); a column the
    #: scoreboard never carried is left out.
    counts: dict[str, int] = Field(default_factory=dict)
    draw: float | None = None
    splits: SplitFigures | None = None
    #: Static and moving splits, both set only when the stage has confirmed
    #: movement covering at least one split and at least one split outside.
    static: SplitFigures | None = None
    moving: SplitFigures | None = None
    reloads: ReloadSummary | None = None
    rounds: int = 0
    min_rounds: int | None = None
    dq: bool = False


_COUNT_TAGS = (
    ("A", "alphas"),
    ("C", "charlies"),
    ("D", "deltas"),
    ("M", "misses"),
    ("NS", "no_shoots"),
    ("P", "procedurals"),
)


def _split_figures(splits: list[float]) -> SplitFigures | None:
    if not splits:
        return None
    return SplitFigures(
        best=round(min(splits), 6),
        avg=round(sum(splits) / len(splits), 6),
        worst=round(max(splits), 6),
        n=len(splits),
    )


def stage_summary_figures(tile: TileStageData) -> StageSummaryFigures:
    """The figures for ``tile``'s summary (:class:`StageSummaryFigures`)."""
    card = tile.scorecard
    dq = bool(card is not None and card.dq)
    active = card if card is not None and not dq else None
    figures = StageSummaryFigures(
        time_seconds=tile.stage_time_seconds,
        time_is_manual=tile.stage_time_is_manual,
        rounds=tile.shot_count,
        min_rounds=tile.stage_rounds.expected if tile.stage_rounds is not None else None,
        dq=dq,
    )
    if active is not None:
        figures.hit_factor = active.hit_factor
        figures.stage_pct = active.stage_pct
        figures.points = active.stage_points
        figures.counts = {
            tag: value for tag, name in _COUNT_TAGS if (value := getattr(active, name)) is not None
        }
    if tile.has_shots:
        selected = statistic_split_shots(tile.shots)
        figures.draw = round(tile.shots[0].split, 6)
        figures.splits = _split_figures([s.split for s in selected])
        static = [s.split for s in selected if not s.moving]
        moving = [s.split for s in selected if s.moving]
        if static and moving:
            figures.static = _split_figures(static)
            figures.moving = _split_figures(moving)
    if tile.reloads:
        figures.reloads = ReloadSummary(
            count=len(tile.reloads),
            avg=round(sum(r.duration for r in tile.reloads) / len(tile.reloads), 6),
            exposed=round(exposed_reload_s(tile.reloads), 6),
        )
    return figures


__all__ = [
    "ReloadSummary",
    "SplitFigures",
    "StageSummaryFigures",
    "TileShot",
    "TileStageData",
    "load_stage_events",
    "load_stage_reloads",
    "load_stage_shots",
    "stage_summary_figures",
]
