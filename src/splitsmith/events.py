"""Stage events: movement, reload and activation as regions on the stage
timeline (spec 2026-10-08).

Pure functions only -- no I/O, no server state. The HTTP layer owns the
audit-doc read/write; this module owns the figures. Mirrored by
``ui_static/src/lib/events.ts`` over ``tests/fixtures/events/cases.json``:
a rule that changes here changes there in the same change.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel

from .config import Config, DivisionCapacityConfig, StageEvent
from .runtime import ENV_CONFIG_FILE

EVENTS_FIELD: Final = "events"
EVENTS_SEEDED_FIELD: Final = "events_seeded"
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
    #: ``reload.end - movement.end`` against the overlapping movement with
    #: the latest end; positive means the reload cost time, zero or
    #: negative means it was hidden. ``None`` for a standing reload.
    overhang: float | None


class StageEventSummary(BaseModel):
    movement_s: float
    moving_shots: int
    reloads: int
    reload_avg_s: float | None
    #: Sum of the positive overhangs only.
    overhang_s: float
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


def reload_figures(events: Sequence[StageEvent]) -> list[ReloadFigure]:
    movements = [e for e in events if e.kind == "movement"]
    out: list[ReloadFigure] = []
    for r in sorted((e for e in events if e.kind == "reload"), key=lambda e: e.start):
        overlapping = [m for m in movements if _overlaps(m, r)]
        if overlapping:
            latest_end = max(m.end for m in overlapping)
            out.append(ReloadFigure(r.id, r.end - r.start, True, r.end - latest_end))
        else:
            out.append(ReloadFigure(r.id, r.end - r.start, False, None))
    return out


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
        overhang_s=sum(f.overhang for f in figs if f.overhang is not None and f.overhang > 0),
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


def seed_events(shot_times: Sequence[float], *, hint_min_s: float, capacity: int | None) -> list[StageEvent]:
    """Propose ``reload`` regions, each spanning a whole gap, ``source="auto"``.

    Without a capacity: every gap over the hint. With one: ``capacity + 1``
    shots fit before a reload is forced, so from shot ``first`` the reload
    sits in one of the gaps after shots ``first .. first + capacity``; the
    first hinted gap in that window wins, else the longest; then the window
    advances to the shot after the seed. Movement is never seeded.
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

    first = 0
    while first < len(gaps):
        window = range(first, min(first + capacity, len(gaps) - 1) + 1)
        required = first + capacity <= len(gaps) - 1  # a shot beyond capacity + 1 exists
        hinted = [g for g in window if gaps[g] > hint_min_s]
        if hinted:
            chosen = hinted[0]
        elif required:
            chosen = max(window, key=lambda g: gaps[g])
        else:
            break
        _seed(chosen)
        first = chosen + 1
    return seeds


def seed_doc(doc: dict[str, Any], *, hint_min_s: float, capacity: int | None) -> bool:
    """Seed once per stage. Returns True when the doc changed."""
    if doc.get(EVENTS_SEEDED_FIELD) or doc.get(EVENTS_FIELD):
        return False
    times = shot_times_from_doc(doc)
    if not times:
        return False
    seeds = seed_events(times, hint_min_s=hint_min_s, capacity=capacity)
    doc[EVENTS_FIELD] = [e.model_dump(exclude_none=True) for e in seeds]
    doc[EVENTS_SEEDED_FIELD] = True
    return True
