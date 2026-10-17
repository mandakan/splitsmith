"""Stage events: movement, reload and activation as regions on the stage
timeline (spec 2026-10-08).

Pure functions only -- no I/O, no server state. The HTTP layer owns the
audit-doc read/write; this module owns the figures. Mirrored by
``ui_static/src/lib/events.ts`` over ``tests/fixtures/events/cases.json``:
a rule that changes here changes there in the same change.
"""

from __future__ import annotations

import logging
import math
import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, get_args

from pydantic import BaseModel

from .config import Config, DivisionCapacityConfig, EventKind, StageEvent
from .runtime import ENV_CONFIG_FILE

logger = logging.getLogger(__name__)

EVENTS_FIELD: Final = "events"
EVENTS_SEEDED_FIELD: Final = "events_seeded"
#: What ``events_seeded`` holds after seeding. 2: a capacity places the
#: fewest reloads the round count forces, in the longest gaps (version 1,
#: stored as ``True``, took the first long gap in each magazine's window).
#: 3: movement proposals over every gap the auto-classifier calls movement.
#: 4: a draw from the beep to the first shot on every stage, and a movement
#: over the same span when the first shot comes late
#: (``CoachAutoClassifyConfig.draw_movement_min_s``, 2.0 s; spec 2026-10-17).
SEED_VERSION: Final = 4
#: How far a seeded movement stays in from the shots either side of its gap
#: (at most a quarter of the gap): ``shot_is_moving`` is inclusive, and a
#: region on the shot's own time would call the shot before the move, and
#: the one after it, fired moving.
MOVEMENT_SEED_INSET_S: Final = 0.1
#: Shortest region the editor will produce; a handle dragged past its
#: partner stops here rather than inverting.
MIN_EVENT_S: Final = 0.05

_EVENT_ID_RE = re.compile(r"\Aevt-(\d+)\Z")


@dataclass(frozen=True)
class ReloadFigure:
    event_id: str
    duration: float
    #: True when any movement region overlaps the reload.
    moving: bool
    #: Exposed reload time: the reload's duration minus its total overlap
    #: with movement regions (their union, so nothing counts twice),
    #: floored at 0. The time the reload cost: a standing reload is exposed
    #: for its whole duration, one fully inside a movement for none, and a
    #: part before the movement starts or after it stops counts.
    exposed: float


class StageEventSummary(BaseModel):
    movement_s: float
    moving_shots: int
    reloads: int
    reload_avg_s: float | None
    #: Exposed reload time summed over the stage's reloads.
    exposed_reload_s: float
    #: "N shots without a reload" when a capacity division fired more
    #: than ``capacity + 1`` rounds between two reload regions.
    capacity_warning: str | None


def validate_lanes(events: Sequence[StageEvent]) -> None:
    """Two events of one kind never overlap (touching edges are fine), and a
    stage has one draw at most."""
    by_kind: dict[str, list[StageEvent]] = {}
    for e in events:
        by_kind.setdefault(e.kind, []).append(e)
    draws = by_kind.get("draw", [])
    if len(draws) > 1:
        raise ValueError(f"draw events {draws[0].id} and {draws[1].id}: a stage has one draw")
    for kind, lane in by_kind.items():
        lane.sort(key=lambda e: (e.start, e.end))
        for prev, cur in zip(lane, lane[1:], strict=False):
            if cur.start < prev.end:
                raise ValueError(f"{kind} events {prev.id} and {cur.id} overlap")


def next_event_id(events: Iterable[StageEvent | dict[str, Any]]) -> str:
    high = 0
    for e in events:
        ident = e.id if isinstance(e, StageEvent) else e.get("id")
        m = _EVENT_ID_RE.match(str(ident)) if ident is not None else None
        if m:
            high = max(high, int(m.group(1)))
    return f"evt-{high + 1}"


def _before_the_beep(e: Any) -> tuple[float, float] | None:
    """``(start, end)`` of a region whose start lies before the beep, else
    None (not a dict, unreadable times, or a start at or after the beep)."""
    if not isinstance(e, dict):
        return None
    start, end = e.get("start"), e.get("end")
    if isinstance(start, bool) or isinstance(end, bool):
        return None
    try:
        s, t = float(start), float(end)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(s) and math.isfinite(t)) or s >= 0:
        return None
    return s, t


def events_from_doc(doc: dict[str, Any]) -> list[StageEvent]:
    """The doc's regions. A region of a kind this version does not know (a
    newer version wrote it, as ``draw`` once was new) is skipped with a
    warning, never fatal: failing the list made the stage's coach read a
    422 on an install one version behind.

    A region that starts before the beep is what moving a beep later leaves
    behind (regions are seconds from the beep): it starts at the beep
    instead, or is skipped when it ends there or earlier, each with a
    warning. Little Trouble stage 2 held a draw and a movement 0.191 s
    before its moved beep, and the one bad start failed the whole list, so
    every export and the stage's Breakdown dropped all nine regions. Any
    other bad entry still raises."""
    raw = doc.get(EVENTS_FIELD) or []
    known = set(get_args(EventKind))
    out: list[StageEvent] = []
    for e in raw:
        if isinstance(e, dict) and isinstance(e.get("kind"), str) and e["kind"] not in known:
            logger.warning("skipping a stage event of unknown kind %r", e["kind"])
            continue
        early = _before_the_beep(e)
        if early is not None:
            start, end = early
            if end <= 0:
                logger.warning("skipping a %s region that ends before the beep (%.3f s)", e.get("kind"), end)
                continue
            logger.warning(
                "a %s region started %.3f s before the beep; it starts at the beep", e.get("kind"), -start
            )
            e = {**e, "start": 0.0}
        out.append(StageEvent.model_validate(e))
    return out


def confirmed(events: Sequence[StageEvent]) -> list[StageEvent]:
    """The regions a rendered or exported output may show: the ones the
    user confirmed (``source == "manual"``), in their stored order. An auto
    proposal is a guess nobody looked at; the Coach page shows it, a video
    or an export never does (spec 2026-10-08, part 2)."""
    return [e for e in events if e.source == "manual"]


def draw_seconds(first_shot_s: float | None, regions: Sequence[StageEvent]) -> float | None:
    """The stage's Draw figure (spec 2026-10-17): a confirmed draw region's
    length, else the first shot's time from the beep (``None`` without
    shots). The one rule every Draw surface reads (the summary card, the
    match summary, the share card, the project payload, Compare);
    ``drawSeconds`` in ``lib/events.ts`` is its twin, held to the shared
    ``cases.json``. A proposal never counts, whatever the caller passes."""
    draw = next((e for e in regions if e.kind == "draw" and e.source == "manual"), None)
    if draw is not None:
        return draw.end - draw.start
    return first_shot_s


def region_marker_label(event: StageEvent) -> str:
    """An editor marker's name for a confirmed region: ``Reload 1.42`` /
    ``Draw 0.98`` / ``Movement`` / ``Activation`` (spec 2026-10-08, part 2;
    draw 2026-10-17). A reload's and a draw's labels carry their duration,
    the figure each stands for; the others don't -- the marker's own
    duration already shows the span. The FCPXML and FCP7 XML exports
    both name their region markers through this."""
    if event.kind == "reload":
        return f"Reload {event.end - event.start:.2f}"
    if event.kind == "draw":
        # Like a reload, a draw's length is the figure (spec 2026-10-17).
        return f"Draw {event.end - event.start:.2f}"
    if event.kind == "movement":
        return "Movement"
    return "Activation"


def confirmed_from_doc(doc: Any, *, log_context: str = "") -> list[StageEvent]:
    """``confirmed(events_from_doc(doc))``, tolerant of a missing, corrupt
    or wrongly-shaped events list.

    Every rendered or exported surface that reads confirmed regions off an
    audit doc goes through this (spec 2026-10-08, part 2) instead of
    re-deriving the same try/except. A bad entry -- an event with
    ``end <= start``, a non-object, an unreadable time -- is left out on
    its own (the conservative read: under-report rather than invent), and
    the rest are kept: dropping the whole list for one bad entry took a
    stage's every region out of its export without a word. A list that is
    not a list, or a doc that is not a dict, reads as none. ``log_context``
    names the file or stage in the warnings this logs, e.g. an audit
    path's name; omit it for a caller with nothing more specific to say.
    """
    if not isinstance(doc, dict):
        return []
    raw = doc.get(EVENTS_FIELD) or []
    where = f"{log_context}: " if log_context else ""
    if not isinstance(raw, list):
        logger.warning("%sstage events are not a list, treating as none", where)
        return []
    kept: list[StageEvent] = []
    for entry in raw:
        try:
            kept.extend(events_from_doc({EVENTS_FIELD: [entry]}))
        except (ValueError, TypeError) as exc:
            logger.warning("%sleaving out an unreadable stage event: %s", where, exc)
    return confirmed(kept)


def shot_ms(shot: Any) -> float | None:
    """A shot dict's ``ms_after_beep`` as a finite number, or None when it
    is missing or unreadable (a hand-edited or legacy doc: a string, a bool,
    NaN). The coach GET and the seeder skip such a shot rather than 500."""
    if not isinstance(shot, dict):
        return None
    raw = shot.get("ms_after_beep")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        ms = float(raw)
    except (TypeError, ValueError):
        return None
    return ms if math.isfinite(ms) else None


def shot_times_from_doc(doc: dict[str, Any]) -> list[float]:
    """Kept shots' times from the beep, ascending. A time that is not a
    finite number is skipped (:func:`shot_ms`), never raised: the seeder
    reads this on a GET."""
    times = [ms / 1000.0 for s in doc.get("shots") or [] if (ms := shot_ms(s)) is not None]
    return sorted(times)


def movement_gap_ends(doc: dict[str, Any]) -> list[float]:
    """The times (seconds from the beep) of the kept shots whose gap, the one
    before them, is classed ``movement``, by the auto-classifier or by hand.
    What the seeder proposes movement regions over."""
    return [
        ms / 1000.0
        for s in doc.get("shots") or []
        if isinstance(s, dict) and s.get("interval_class") == "movement" and (ms := shot_ms(s)) is not None
    ]


def _overlaps(a: StageEvent, b: StageEvent) -> bool:
    return a.start < b.end and b.start < a.end


def shot_is_moving(time_from_beep: float, events: Sequence[StageEvent]) -> bool:
    return any(e.kind == "movement" and e.start <= time_from_beep <= e.end for e in events)


def exposed_spans(reload: StageEvent, events: Sequence[StageEvent]) -> list[tuple[float, float]]:
    """The stretches of ``reload`` no movement covers, in time order: the
    reload minus the union of the movements overlapping it, so two movements
    that touch or overlap never count the same instant twice. What the
    exposed figure sums (``exposedSpans`` in ``lib/events.ts``)."""
    covered = sorted(
        (max(m.start, reload.start), min(m.end, reload.end))
        for m in events
        if m.kind == "movement" and _overlaps(m, reload)
    )
    out: list[tuple[float, float]] = []
    at = reload.start
    for start, end in covered:
        if start > at:
            out.append((at, start))
        at = max(at, end)
    if reload.end > at:
        out.append((at, reload.end))
    return out


def reload_figures(events: Sequence[StageEvent]) -> list[ReloadFigure]:
    out: list[ReloadFigure] = []
    for r in sorted((e for e in events if e.kind == "reload"), key=lambda e: e.start):
        moving = any(m.kind == "movement" and _overlaps(m, r) for m in events)
        exposed = sum(end - start for start, end in exposed_spans(r, events))
        out.append(ReloadFigure(r.id, r.end - r.start, moving, max(0.0, exposed)))
    return out


def exposed_reload_s(figures: Sequence[ReloadFigure]) -> float:
    """The stage's exposed reload time: every reload's exposed time summed."""
    return sum(f.exposed for f in figures)


def _capacity_warning(
    shot_times: Sequence[float], reloads: Sequence[StageEvent], capacity: int | None
) -> str | None:
    if capacity is None or not shot_times:
        return None
    # Shots are split into segments at each reload's start; a shot fired
    # during a reload region counts toward the segment after it.
    cuts = sorted(r.start for r in reloads)
    counts: list[int] = []
    i = 0
    for cut in cuts:
        n = 0
        while i < len(shot_times) and shot_times[i] < cut:
            n += 1
            i += 1
        counts.append(n)
    counts.append(len(shot_times) - i)
    worst = max(counts)
    if worst > capacity + 1:
        return f"{worst} shots without a reload"
    return None


def stage_event_summary(
    shot_times: Sequence[float], events: Sequence[StageEvent], capacity: int | None
) -> StageEventSummary:
    # Sorted here, as ``lib/events.ts`` does: the capacity segmentation walks
    # shots in time order.
    shot_times = sorted(shot_times)
    figs = reload_figures(events)
    reloads = [e for e in events if e.kind == "reload"]
    return StageEventSummary(
        movement_s=sum(e.end - e.start for e in events if e.kind == "movement"),
        moving_shots=sum(1 for t in shot_times if shot_is_moving(t, events)),
        reloads=len(figs),
        reload_avg_s=(sum(f.duration for f in figs) / len(figs)) if figs else None,
        exposed_reload_s=exposed_reload_s(figs),
        capacity_warning=_capacity_warning(shot_times, reloads, capacity),
    )


def capacity_config() -> DivisionCapacityConfig:
    """The capacity table, from ``SPLITSMITH_CONFIG`` when set (the same
    accessor shape as ``coach.auto_classify_config``)."""
    raw = os.environ.get(ENV_CONFIG_FILE, "").strip()
    if not raw:
        return DivisionCapacityConfig()
    return Config.load(Path(raw).expanduser()).division_capacity


def _norm_division(division: str) -> str:
    return " ".join(division.split()).casefold()


def capacity_for(division: str | None, config: DivisionCapacityConfig | None = None) -> int | None:
    if not division:
        return None
    table = {_norm_division(k): v for k, v in (config or capacity_config()).capacities.items()}
    return table.get(_norm_division(division))


def seed_events(
    shot_times: Sequence[float],
    *,
    hint_min_s: float,
    capacity: int | None,
    min_reload_s: float = 0.6,
    movement_gaps: Sequence[float] = (),
    draw: bool = False,
    draw_movement_min_s: float | None = None,
) -> list[StageEvent]:
    """Propose ``reload`` regions, each spanning a whole gap, and
    ``movement`` regions, all ``source="auto"``.

    Reloads, without a capacity: every gap over the hint. With one, the
    round count decides: at most ``capacity + 1`` shots fit between reloads
    (one chambered on a full magazine; a bound, not a count), so the
    proposals are the fewest reloads that keep every run of shots within
    it, placed in the longest gaps those runs allow. Only a gap of at least
    ``min_reload_s`` can hold a reload; when no placement exists over those
    gaps, any gap may. A stage that fits in one magazine gets none, however
    long its gaps.

    Movements: one per gap that ends on a shot time in ``movement_gaps``
    (the shots whose gap the auto-classifier calls movement), inset
    :data:`MOVEMENT_SEED_INSET_S` from both shots so neither reads as fired
    moving. A movement may sit over a reload: a reload on the move.

    The draw: with ``draw``, a ``draw`` region from the beep to the first
    shot (the user moves its start to where the hands moved). With
    ``draw_movement_min_s``, a first shot more than that after the beep (a
    start that moves before it shoots) also gets a movement over the same
    span, inset from that shot. Either is one tap to delete or relabel when
    wrong; ``False`` / ``None`` propose none.

    A gap that cannot be a region is never proposed: one that starts before
    the beep (a pre-beep first shot), has no length, or has a time that is
    not a finite number. The seeder runs on a coach GET, and an invalid
    region there was a 500 on every read of the stage.
    """
    times = sorted(t for t in shot_times if math.isfinite(t))
    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    seeds: list[StageEvent] = []
    seeds.extend(_reload_seeds(times, gaps, hint_min_s, capacity, min_reload_s))
    if draw and times and times[0] >= MIN_EVENT_S:
        seeds.append(StageEvent(id=next_event_id(seeds), kind="draw", start=0.0, end=times[0], source="auto"))
    if draw_movement_min_s is not None and times and times[0] > draw_movement_min_s and times[0] > 0.0:
        seeds.append(
            StageEvent(
                id=next_event_id(seeds),
                kind="movement",
                start=0.0,
                end=round(times[0] - MOVEMENT_SEED_INSET_S, 6),
                source="auto",
            )
        )
    moving_ends = {t for t in movement_gaps if math.isfinite(t)}
    for g, gap in enumerate(gaps):
        start, end = times[g], times[g + 1]
        if end not in moving_ends or start < 0.0 or end <= start:
            continue
        inset = min(MOVEMENT_SEED_INSET_S, gap / 4)
        if gap - 2 * inset < MIN_EVENT_S:
            continue
        seeds.append(
            StageEvent(
                id=next_event_id(seeds),
                kind="movement",
                start=round(start + inset, 6),
                end=round(end - inset, 6),
                source="auto",
            )
        )
    return seeds


def _reload_seeds(
    times: Sequence[float],
    gaps: Sequence[float],
    hint_min_s: float,
    capacity: int | None,
    min_reload_s: float,
) -> list[StageEvent]:
    seeds: list[StageEvent] = []

    def _seed(g: int) -> None:
        start, end = times[g], times[g + 1]
        if start < 0.0 or end <= start:
            return
        seeds.append(StageEvent(id=next_event_id(seeds), kind="reload", start=start, end=end, source="auto"))

    if capacity is None:
        for g, gap in enumerate(gaps):
            if gap > hint_min_s:
                _seed(g)
        return seeds

    chosen = _fewest_reloads(gaps, capacity + 1, min_reload_s)
    if chosen is None:
        chosen = _fewest_reloads(gaps, capacity + 1, 0.0) or []
    for g in chosen:
        _seed(g)
    return seeds


def _fewest_reloads(gaps: Sequence[float], run_max: int, min_gap: float) -> list[int] | None:
    """The gap indices (gap ``g`` follows shot ``g + 1``) of the fewest
    reloads that leave no run of more than ``run_max`` shots, each in a gap
    of at least ``min_gap``; among those, the placement with the longest
    gaps in total. ``[]`` when every shot fits one run, ``None`` when no
    placement exists over the allowed gaps."""
    shots = len(gaps) + 1
    if shots <= run_max:
        return []
    allowed = [g for g, gap in enumerate(gaps) if gap >= min_gap]
    # best[g]: (reloads, -total gap, path) for a placement whose last reload
    # is gap g, every run before it within ``run_max``.
    best: dict[int, tuple[int, float, list[int]]] = {}
    for g in allowed:
        candidates: list[tuple[int, float, list[int]]] = []
        if g + 1 <= run_max:
            candidates.append((1, -gaps[g], [g]))
        for p, (count, neg_total, path) in best.items():
            if p < g and g - p <= run_max:
                candidates.append((count + 1, neg_total - gaps[g], [*path, g]))
        if candidates:
            best[g] = min(candidates, key=lambda c: (c[0], c[1]))
    finals = [entry for g, entry in best.items() if shots - (g + 1) <= run_max]
    if not finals:
        return None
    return min(finals, key=lambda c: (c[0], c[1]))[2]


def reseedable(doc: dict[str, Any]) -> bool:
    """True when an earlier seeder's proposals stand untouched: seeded by a
    version before :data:`SEED_VERSION`, with proposals and nothing else. A
    region the user drew, kept or edited, or proposals they deleted, keep
    the stage as it is."""
    seeded = doc.get(EVENTS_SEEDED_FIELD)
    events = doc.get(EVENTS_FIELD)
    if not seeded or seeded == SEED_VERSION or not isinstance(events, list) or not events:
        return False
    return all(isinstance(e, dict) and e.get("source") == "auto" for e in events)


def seed_doc(
    doc: dict[str, Any],
    *,
    hint_min_s: float,
    capacity: int | None,
    min_reload_s: float = 0.6,
    draw: bool = False,
    draw_movement_min_s: float | None = None,
) -> bool:
    """Seed once per stage, or again over an earlier seeder's untouched
    proposals (:func:`reseedable`). Returns True when the doc changed.

    A first seed that proposes nothing leaves the doc untouched: marking
    the stage seeded would stop a later read from proposing over shots
    added since, and recomputing an empty seed is cheap. Once proposals
    exist, ``events_seeded`` is what keeps deleted ones from coming back.
    """
    again = reseedable(doc)
    if not again and (doc.get(EVENTS_SEEDED_FIELD) or doc.get(EVENTS_FIELD)):
        return False
    times = shot_times_from_doc(doc)
    if not times:
        return False
    seeds = seed_events(
        times,
        hint_min_s=hint_min_s,
        capacity=capacity,
        min_reload_s=min_reload_s,
        movement_gaps=movement_gap_ends(doc),
        draw=draw,
        draw_movement_min_s=draw_movement_min_s,
    )
    if not seeds and not again:
        return False
    events = [e.model_dump(exclude_none=True) for e in seeds]
    if again and events == doc.get(EVENTS_FIELD):
        doc[EVENTS_SEEDED_FIELD] = SEED_VERSION
        return True
    doc[EVENTS_FIELD] = events
    doc[EVENTS_SEEDED_FIELD] = SEED_VERSION
    return True
