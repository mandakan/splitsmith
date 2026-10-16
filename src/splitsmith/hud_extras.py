"""Opt-in HUD data beyond ``data.stage`` (spec ``2026-10-16-hud-scoring-data-design``).

A template asks for keys with
``<meta name="splitsmith-data" content="scoring,ammo,bursts">``; one that
asks for nothing gets exactly the context it always did, so its digest and
cached MOVs never move. Like ``overlay_hud``, this module is pure: every
figure a template draws is computed here, never in the template.

Each key is a :class:`HudDataProvider` in one registry (:data:`PROVIDERS`):
a name, a version and a pure function of :class:`HudInputs`. A new data
source is one provider (and, when it needs something the audit does not
hold, one field on :class:`HudExtras` filled by ``ui.exports.hud_extras_for``);
nothing else changes. Bump a provider's ``version`` when its output changes
for the same inputs: the version is in every declaring overlay's record, so
those overlays draw again and no other does.

There is no per-shot score. ``scoring.spreads`` therefore guesses, four ways,
which shot took which hit; every guess ends on the scorecard's totals, and
the template picks one by name.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict

from .coach import split_stat_split_max
from .config import StageEvent, StageRounds
from .events import capacity_for, confirmed, shot_is_moving
from .match_project import StageScorecard
from .stage_summary_data import TileShot

SpreadName = Literal["even", "fast", "moving", "end"]
SPREADS: tuple[SpreadName, ...] = get_args(SpreadName)

#: Worst first: the order a spread hands outcomes to the shots it ranks
#: most likely to have dropped points.
_OUTCOMES_WORST_FIRST = ("M", "D", "C", "A")
_COUNT_FIELDS = {"A": "alphas", "C": "charlies", "D": "deltas", "M": "misses"}

#: A gap of one of these classes starts a new burst.
BURST_BREAK_CLASSES = frozenset({"transition", "movement", "reload", "activation"})

_DATA_META = re.compile(r"""<meta\s+name=["']splitsmith-data["']\s+content=["']([^"']*)["']""", re.IGNORECASE)


class HudExtras(BaseModel):
    """What the request layer knows about a stage that the audit does not:
    resolved from ``project.json`` and handed to the HUD render. Every field
    is optional; absent data stays absent in the context."""

    model_config = ConfigDict(frozen=True)

    stage_name: str | None = None
    stage_number: int | None = None
    scorecard: StageScorecard | None = None
    division: str | None = None
    #: Magazine capacity (``events.capacity_for`` of the division); ``None``
    #: for a division with no entry, which draws no ammo counter.
    capacity: int | None = None
    #: The split above which an unclassified gap ends a burst
    #: (``coach.split_stat_split_max``, which a ``SPLITSMITH_CONFIG`` can
    #: move). Carried here, not read at render time, so a changed cutoff is
    #: in the record's ``hud_data`` and redraws. ``None`` reads it now.
    burst_split_max: float | None = None
    #: The fewest rounds that can score every target (SSI's ``min_rounds``,
    #: ``StageEntry.stage_rounds.expected``); ``None`` when the scoreboard
    #: gave none.
    min_rounds: int | None = None


def hud_extras_from(
    *,
    stage_name: str | None,
    stage_number: int,
    scorecard: StageScorecard | None,
    stage_rounds: StageRounds | None,
    division: str | None,
) -> HudExtras:
    """:class:`HudExtras` from a stage's fields and an already resolved
    division (``division.competitor_division``): the one builder every
    writer shares (``ui.exports.hud_extras_for``, the match export's summary
    holds, the export preview), so their records and pictures agree."""
    return HudExtras(
        stage_name=stage_name,
        stage_number=stage_number,
        scorecard=scorecard,
        division=division,
        capacity=capacity_for(division),
        min_rounds=stage_rounds.expected if stage_rounds else None,
        burst_split_max=split_stat_split_max(),
    )


@dataclass(frozen=True)
class HudInputs:
    """Everything a provider may read: the stage's shots in time order, where
    the beep sits in the clip, its confirmed regions only, and the facts
    beyond the audit."""

    shots: Sequence[TileShot]
    beep_in_clip: float
    events: Sequence[StageEvent]
    extras: HudExtras


@dataclass(frozen=True)
class HudDataProvider:
    """One opt-in ``data`` key."""

    key: str
    #: Bump when the output changes for the same inputs.
    version: int
    build: Callable[[HudInputs], Any]


#: Every opt-in key, in the order a context carries them. Filled by
#: :func:`register` at the bottom of this module.
PROVIDERS: dict[str, HudDataProvider] = {}

#: Keys a provider may never take: the context's own.
_RESERVED = frozenset({"stage", "options", "safe_area"})


def register(provider: HudDataProvider) -> HudDataProvider:
    if provider.key in _RESERVED or not re.fullmatch(r"[a-z][a-z0-9_]*", provider.key):
        raise ValueError(f"{provider.key!r} cannot be a HUD data key")
    if provider.key in PROVIDERS:
        raise ValueError(f"HUD data key {provider.key!r} is registered twice")
    PROVIDERS[provider.key] = provider
    return provider


def declared_data(template: Path) -> tuple[str, ...]:
    """The extra keys a template asks for, from its
    ``<meta name="splitsmith-data">`` tag, in registry order. Read as text:
    no browser. Unknown names are ignored."""
    try:
        match = _DATA_META.search(template.read_text(encoding="utf-8"))
    except OSError:
        return ()
    if match is None:
        return ()
    names = {name.strip() for name in match.group(1).split(",")}
    return tuple(key for key in PROVIDERS if key in names)


def extras_identity(keys: Sequence[str], extras: HudExtras | None) -> str | None:
    """What an overlay's record carries as ``hud_data``: the keys the template
    declared, each at its provider's version, and the extras it was drawn
    with. ``None`` when it declared none, so the record of every other
    template is what it always was."""
    known = [key for key in keys if key in PROVIDERS]
    if not known:
        return None
    body = (extras or HudExtras()).model_dump_json()
    versions = ",".join(f"{key}@{PROVIDERS[key].version}" for key in known)
    return hashlib.sha256(f"{versions}\0{body}".encode()).hexdigest()


def _clip(beep_in_clip: float, seconds_from_beep: float) -> float:
    return round(beep_in_clip + max(0.0, seconds_from_beep), 6)


def ammo_plan(
    shots: Sequence[TileShot],
    *,
    beep_in_clip: float,
    events: Sequence[StageEvent],
    capacity: int | None,
) -> dict[str, Any] | None:
    """``data.ammo``: rounds left after each shot, from a full magazine with
    one chambered (``capacity + 1``). Each confirmed reload refills before
    the first shot after its start: to ``capacity + 1`` when rounds were
    left (a tactical reload keeps the chambered one), to ``capacity`` at
    slide lock. A shot at the reload's own start was fired before it: a
    seeded reload spans its whole gap, so it starts on the last shot of
    the magazine. ``refills[].t`` is the reload's end in clip seconds, when
    the magazine seats. A count below zero is kept, not clamped, and ``ok``
    goes false: a reload region is missing. ``None`` without a capacity."""
    if capacity is None or capacity <= 0:
        return None
    reloads = sorted((e for e in confirmed(events) if e.kind == "reload"), key=lambda e: e.start)
    start = capacity + 1
    left = start
    per_shot: list[int] = []
    refills: list[dict[str, Any]] = []
    pending = list(reloads)

    def refill() -> None:
        nonlocal left
        reload = pending.pop(0)
        left = capacity + 1 if left > 0 else capacity
        refills.append({"t": _clip(beep_in_clip, reload.end), "rounds": left})

    for shot in shots:
        while pending and pending[0].start < shot.time_from_beep:
            refill()
        left -= 1
        per_shot.append(left)
    # A reload after the last shot still seats a magazine on screen.
    while pending:
        refill()
    return {
        "capacity": capacity,
        "start": start,
        "per_shot": per_shot,
        "refills": refills,
        "ok": all(n >= 0 for n in per_shot),
    }


def bursts(shots: Sequence[TileShot], *, split_max: float | None = None) -> dict[str, Any]:
    """``data.bursts``: shots grouped by the gaps between them. A burst ends
    at a gap classed transition, movement, reload or activation; an
    unclassified gap breaks one when it is over ``split_max`` (the
    auto-classifier's split cutoff, ``coach.split_stat_split_max``, when
    ``None``). ``mean_split`` and ``min_split`` read the splits inside a
    burst only, so a burst of one has neither. ``shot_burst[i]`` is shot
    ``i``'s burst."""
    cutoff = split_stat_split_max() if split_max is None else split_max
    groups: list[list[int]] = []
    for i, shot in enumerate(shots):
        cls = shot.interval_class
        if i == 0 or cls in BURST_BREAK_CLASSES or (cls is None and shot.split > cutoff):
            groups.append([i])
        else:
            groups[-1].append(i)
    out: list[dict[str, Any]] = []
    shot_burst: list[int] = []
    for index, group in enumerate(groups):
        inner = [shots[i].split for i in group[1:]]
        out.append(
            {
                "first": group[0],
                "last": group[-1],
                "shots": len(group),
                "mean_split": round(sum(inner) / len(inner), 6) if inner else None,
                "min_split": round(min(inner), 6) if inner else None,
            }
        )
        shot_burst.extend(index for _ in group)
    return {"bursts": out, "shot_burst": shot_burst}


def _slot_counts(tokens: int, shots: int) -> list[int]:
    """``tokens`` outcomes over ``shots`` shots in time order, as evenly as
    integers allow: every count is the floor or the ceiling of the ratio."""
    return [((i + 1) * tokens) // shots - (i * tokens) // shots for i in range(shots)]


def _spread_order(name: SpreadName, shots: Sequence[TileShot], moving: Sequence[bool]) -> list[int]:
    """Shot indices, the one most likely to have dropped points first."""
    n = len(shots)
    by_speed = sorted(range(n), key=lambda i: (shots[i].split, i))
    if name == "fast":
        return by_speed
    if name == "moving":
        return [i for i in by_speed if moving[i]] + [i for i in by_speed if not moving[i]]
    if name == "end":
        return list(range(n - 1, -1, -1))
    # even: a low-discrepancy walk over the stage (van der Corput order), so
    # the first picks land far apart whatever their number.
    bits = max(1, (n - 1).bit_length())
    walk = sorted(range(1 << bits), key=lambda k: int(f"{k:0{bits}b}"[::-1], 2))
    return [k for k in walk if k < n]


def spread(
    name: SpreadName, shots: Sequence[TileShot], moving: Sequence[bool], scorecard: StageScorecard
) -> list[dict[str, Any]]:
    """One guess at who took what. Each shot gets ``hits`` (outcome letters,
    possibly none: a shot with nothing left to allot, or several when the
    scorecard counts more hits than shots were fired), ``ns`` (a no-shoot)
    and ``totals`` (running counts after it). Every guess ends on the
    scorecard's A / C / D / M / NS (no-shoots capped at the shot count)."""
    n = len(shots)
    counts = {letter: max(0, getattr(scorecard, field) or 0) for letter, field in _COUNT_FIELDS.items()}
    tokens = [letter for letter in _OUTCOMES_WORST_FIRST for _ in range(counts[letter])]
    slots = _slot_counts(len(tokens), n)
    order = _spread_order(name, shots, moving)
    hits: list[list[str]] = [[] for _ in range(n)]
    cursor = 0
    for i in order:
        hits[i] = tokens[cursor : cursor + slots[i]]
        cursor += slots[i]
    no_shoots = min(n, max(0, scorecard.no_shoots or 0))
    ns = set(order[:no_shoots])
    running = dict.fromkeys(("A", "C", "D", "M", "NS"), 0)
    out: list[dict[str, Any]] = []
    for i in range(n):
        for letter in hits[i]:
            running[letter] += 1
        if i in ns:
            running["NS"] += 1
        out.append({"hits": sorted(hits[i], key="ACDM".index), "ns": i in ns, "totals": dict(running)})
    return out


def scoring_data(
    shots: Sequence[TileShot], *, events: Sequence[StageEvent], extras: HudExtras | None
) -> dict[str, Any]:
    """``data.scoring``: the stage's name and number, the scorecard's
    figures, and every spread. Without a scorecard the figures, ``totals``
    and ``spreads`` are ``None``."""
    extras = extras or HudExtras()
    card = extras.scorecard
    base: dict[str, Any] = {
        "name": extras.stage_name,
        "number": extras.stage_number,
        "division": extras.division,
        "min_rounds": extras.min_rounds,
        "hit_factor": card.hit_factor if card else None,
        "stage_pct": card.stage_pct if card else None,
        "points": card.stage_points if card else None,
        "procedurals": card.procedurals if card else None,
        "totals": None,
        "spreads": None,
    }
    if card is None or not shots:
        return base
    regions = confirmed(events)
    moving = [shot_is_moving(s.time_from_beep, regions) for s in shots]
    base["totals"] = {
        "A": card.alphas or 0,
        "C": card.charlies or 0,
        "D": card.deltas or 0,
        "M": card.misses or 0,
        "NS": card.no_shoots or 0,
    }
    base["spreads"] = {name: spread(name, shots, moving, card) for name in SPREADS}
    return base


def hud_extras_data(
    keys: Sequence[str],
    shots: Sequence[TileShot],
    *,
    beep_in_clip: float,
    events: Sequence[StageEvent],
    extras: HudExtras | None,
) -> dict[str, Any]:
    """The top-level ``data`` keys a template declared, and only those, in
    registry order. ``shots`` are in time order, as ``load_stage_shots``
    returns them; ``events`` are narrowed to confirmed regions here, so no
    provider can draw a proposal."""
    inputs = HudInputs(
        shots=shots, beep_in_clip=beep_in_clip, events=confirmed(events), extras=extras or HudExtras()
    )
    return {key: provider.build(inputs) for key, provider in PROVIDERS.items() if key in keys}


register(HudDataProvider("scoring", 2, lambda i: scoring_data(i.shots, events=i.events, extras=i.extras)))
register(
    HudDataProvider(
        "ammo",
        2,
        lambda i: ammo_plan(
            i.shots, beep_in_clip=i.beep_in_clip, events=i.events, capacity=i.extras.capacity
        ),
    )
)
register(HudDataProvider("bursts", 1, lambda i: bursts(i.shots, split_max=i.extras.burst_split_max)))


__all__ = [
    "BURST_BREAK_CLASSES",
    "PROVIDERS",
    "HudDataProvider",
    "HudExtras",
    "HudInputs",
    "SPREADS",
    "SpreadName",
    "ammo_plan",
    "bursts",
    "declared_data",
    "extras_identity",
    "hud_extras_data",
    "hud_extras_from",
    "register",
    "scoring_data",
    "spread",
]
