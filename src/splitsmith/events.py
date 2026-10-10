"""Stage events: movement, reload and activation as regions on the stage
timeline (spec 2026-10-08).

Pure functions only -- no I/O, no server state. The HTTP layer owns the
audit-doc read/write; this module owns the figures. Mirrored by
``ui_static/src/lib/events.ts`` over ``tests/fixtures/events/cases.json``:
a rule that changes here changes there in the same change.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel

from .config import Config, DivisionCapacityConfig, StageEvent
from .runtime import ENV_CONFIG_FILE

logger = logging.getLogger(__name__)

EVENTS_FIELD: Final = "events"
EVENTS_SEEDED_FIELD: Final = "events_seeded"
#: What ``events_seeded`` holds after seeding. 2: a capacity places the
#: fewest reloads the round count forces, in the longest gaps (version 1,
#: stored as ``True``, took the first long gap in each magazine's window).
SEED_VERSION: Final = 2
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
    """Two events of one kind never overlap (touching edges are fine)."""
    by_kind: dict[str, list[StageEvent]] = {}
    for e in events:
        by_kind.setdefault(e.kind, []).append(e)
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


def events_from_doc(doc: dict[str, Any]) -> list[StageEvent]:
    raw = doc.get(EVENTS_FIELD) or []
    return [StageEvent.model_validate(e) for e in raw]


def confirmed(events: Sequence[StageEvent]) -> list[StageEvent]:
    """The regions a rendered or exported output may show: the ones the
    user confirmed (``source == "manual"``), in their stored order. An auto
    proposal is a guess nobody looked at; the Coach page shows it, a video
    or an export never does (spec 2026-10-08, part 2)."""
    return [e for e in events if e.source == "manual"]


def region_marker_label(event: StageEvent) -> str:
    """An editor marker's name for a confirmed region: ``Reload 1.42`` /
    ``Movement`` / ``Activation`` (spec 2026-10-08, part 2). A reload's
    label carries its duration; the other two kinds don't -- the marker's
    own duration already shows the span. The FCPXML and FCP7 XML exports
    both name their region markers through this."""
    if event.kind == "reload":
        return f"Reload {event.end - event.start:.2f}"
    if event.kind == "movement":
        return "Movement"
    return "Activation"


def confirmed_from_doc(doc: Any, *, log_context: str = "") -> list[StageEvent]:
    """``confirmed(events_from_doc(doc))``, tolerant of a missing, corrupt
    or wrongly-shaped events list.

    Every rendered or exported surface that reads confirmed regions off an
    audit doc goes through this (spec 2026-10-08, part 2) instead of
    re-deriving the same try/except: a bad doc -- not a dict, an event
    with ``end <= start``, a non-object entry -- degrades to no regions
    (the conservative read: under-report rather than invent) instead of
    failing the whole render or export. ``log_context`` names the file or
    stage in the one warning this logs, e.g. an audit path's name; omit it
    for a caller with nothing more specific to say.
    """
    if not isinstance(doc, dict):
        return []
    try:
        return confirmed(events_from_doc(doc))
    except (ValueError, TypeError) as exc:
        where = f"{log_context}: " if log_context else ""
        logger.warning("%sunreadable stage events, treating as none: %s", where, exc)
        return []


def shot_times_from_doc(doc: dict[str, Any]) -> list[float]:
    """Kept shots' times from the beep, ascending."""
    times = [
        float(s["ms_after_beep"]) / 1000.0
        for s in doc.get("shots") or []
        if isinstance(s, dict) and s.get("ms_after_beep") is not None
    ]
    return sorted(times)


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
) -> list[StageEvent]:
    """Propose ``reload`` regions, each spanning a whole gap, ``source="auto"``.

    Without a capacity: every gap over the hint. With one, the round count
    decides: at most ``capacity + 1`` shots fit between reloads (one
    chambered on a full magazine; a bound, not a count), so the proposals
    are the fewest reloads that keep every run of shots within it, placed
    in the longest gaps those runs allow. Only a gap of at least
    ``min_reload_s`` can hold a reload; when no placement exists over those
    gaps, any gap may. A stage that fits in one magazine gets none, however
    long its gaps. Movement is never seeded.
    """
    times = sorted(shot_times)
    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    seeds: list[StageEvent] = []

    def _seed(g: int) -> None:
        seeds.append(
            StageEvent(
                id=next_event_id(seeds), kind="reload", start=times[g], end=times[g + 1], source="auto"
            )
        )

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
    doc: dict[str, Any], *, hint_min_s: float, capacity: int | None, min_reload_s: float = 0.6
) -> bool:
    """Seed once per stage, or again over an earlier seeder's untouched
    proposals (:func:`reseedable`). Returns True when the doc changed."""
    again = reseedable(doc)
    if not again and (doc.get(EVENTS_SEEDED_FIELD) or doc.get(EVENTS_FIELD)):
        return False
    times = shot_times_from_doc(doc)
    if not times:
        return False
    seeds = seed_events(times, hint_min_s=hint_min_s, capacity=capacity, min_reload_s=min_reload_s)
    events = [e.model_dump(exclude_none=True) for e in seeds]
    if again and events == doc.get(EVENTS_FIELD):
        doc[EVENTS_SEEDED_FIELD] = SEED_VERSION
        return True
    doc[EVENTS_FIELD] = events
    doc[EVENTS_SEEDED_FIELD] = SEED_VERSION
    return True
