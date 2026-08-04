"""The grid overlay as a step function over shot events.

Overlay content changes only when someone fires. A 30-shot stage
therefore has ~30 distinct states rather than one per frame, and each
state is rendered once and held -- that is the whole reason this path
costs draws in the tens rather than the hundreds.

Pure computation: states in seconds, no rasterizer, no file I/O. Turning
a state into pixels belongs to the sprite renderer, not here.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .overlay_data import TileShot, TileStageData

# Shots land on the tolerance side of a boundary rather than the wrong
# side of it: a shot whose time equals the event time has fired.
_EPSILON = 1e-6

# Two shooters firing within the same millisecond must collapse to one
# state; a zero-length state would render a sprite nobody ever sees.
_EVENT_PRECISION = 3

_EMPTY = TileStageData(label="", stage_number=0)


@dataclass(frozen=True)
class TilePlacement:
    """Where one shooter's tile sits in the grid.

    ``present=False`` is a filler tile -- the shooter has no trim for this
    stage. It is drawn as nothing and never enters the ranking.
    """

    label: str
    row: int
    col: int
    present: bool


@dataclass(frozen=True)
class TilePanel:
    """One tile's overlay content for the duration of one state.

    Every numeric field is optional because absent data stays absent: a
    tile that has not fired has no split, no rank and no delta, and a
    stage with no round count has no ``expected_shots``. None of them
    degrade to zero -- a zero split reads as a real number to a viewer.
    """

    label: str
    row: int
    col: int
    present: bool
    shots_fired: int
    expected_shots: int | None
    last_split: float | None
    rank: int | None
    delta_to_leader: float | None


@dataclass(frozen=True)
class OverlayState:
    """The whole grid's overlay content over one segment-time interval.

    Times are seconds from the start of the stage's segment in the
    rendered MP4, not from the beep: ``start_seconds`` already includes
    the grid's head pad.
    """

    start_seconds: float
    duration_seconds: float
    panels: tuple[TilePanel, ...]

    @property
    def end_seconds(self) -> float:
        return self.start_seconds + self.duration_seconds


def build_overlay_states(
    placements: Sequence[TilePlacement],
    data: Mapping[str, TileStageData],
    *,
    head_pad_seconds: float,
    duration_seconds: float,
) -> tuple[OverlayState, ...]:
    """Ordered overlay states covering the whole stage segment.

    The states tile ``[0, duration_seconds)`` exactly: each runs to the
    next one's start and the last is clamped to the segment end, so the
    durations sum to ``duration_seconds`` and the overlay neither leaves a
    gap nor overruns the clip.
    """
    starts = _state_starts(
        placements,
        data,
        head_pad_seconds=head_pad_seconds,
        duration_seconds=duration_seconds,
    )
    states: list[OverlayState] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else duration_seconds
        # The opening state covers the pre-beep pad as well as the beep
        # itself, so its lookups run at beep time: nothing has fired.
        event_time = max(0.0, start - head_pad_seconds)
        states.append(
            OverlayState(
                start_seconds=start,
                duration_seconds=end - start,
                panels=_panels_at(placements, data, event_time),
            )
        )
    return tuple(states)


def _state_starts(
    placements: Sequence[TilePlacement],
    data: Mapping[str, TileStageData],
    *,
    head_pad_seconds: float,
    duration_seconds: float,
) -> list[float]:
    """Segment times at which the overlay changes, in order.

    Events are deduplicated to the millisecond, but each bucket keeps its
    *latest* raw shot time rather than the rounded value. Rounding down to
    the bucket would put the boundary in front of a shot at, say, 1.0004s
    and that shot would then be counted in no state at all -- it would
    simply never appear on screen.
    """
    buckets: dict[int, float] = {}
    for placement in placements:
        if not placement.present:
            continue
        for shot in data.get(placement.label, _EMPTY).shots:
            key = round(shot.time_from_beep, _EVENT_PRECISION)
            bucket = int(key * 10**_EVENT_PRECISION)
            buckets[bucket] = max(buckets.get(bucket, shot.time_from_beep), shot.time_from_beep)
    # 0.0 is the opening state, already covered; a shot at or past the
    # segment end has nowhere to be drawn.
    starts = {0.0} | {
        head_pad_seconds + event
        for event in buckets.values()
        if event > 0.0 and head_pad_seconds + event < duration_seconds
    }
    return sorted(starts)


def _panels_at(
    placements: Sequence[TilePlacement],
    data: Mapping[str, TileStageData],
    event_time: float,
) -> tuple[TilePanel, ...]:
    """Every tile's content at one beep-relative instant, in grid order."""
    fired: dict[str, tuple[TileShot, ...]] = {}
    for placement in placements:
        if not placement.present:
            continue
        shots = data.get(placement.label, _EMPTY).shots
        fired[placement.label] = tuple(s for s in shots if s.time_from_beep <= event_time + _EPSILON)

    ranks, deltas = _rank(placements, fired)

    panels: list[TilePanel] = []
    for placement in placements:
        shots = fired.get(placement.label, ())
        tile = data.get(placement.label, _EMPTY) if placement.present else _EMPTY
        panels.append(
            TilePanel(
                label=placement.label,
                row=placement.row,
                col=placement.col,
                present=placement.present,
                shots_fired=len(shots),
                expected_shots=tile.stage_rounds.expected if tile.stage_rounds else None,
                # The audit's own split, never re-derived from the times:
                # the two can legitimately disagree and the audit wins.
                last_split=shots[-1].split if shots else None,
                rank=ranks.get(placement.label),
                delta_to_leader=deltas.get(placement.label),
            )
        )
    return tuple(panels)


def _rank(
    placements: Sequence[TilePlacement],
    fired: Mapping[str, tuple[TileShot, ...]],
) -> tuple[dict[str, int], dict[str, float]]:
    """Rank the tiles that have fired, and time each against the leader.

    Further along wins, then faster to get there: sort by shot count
    descending, then by the time of that shot ascending. Ties keep grid
    order, since ``sorted`` is stable over ``placements``.

    The delta compares like with like -- a tile on shot ``k`` is measured
    against the leader's time at shot ``k``, never the leader's latest
    shot. Comparing a shooter on shot 3 to a leader's shot-8 elapsed time
    would show a lead that means nothing. The leader holds the highest
    shot count, so its shot ``k`` always exists.
    """
    contenders = [p.label for p in placements if p.present and fired.get(p.label)]
    if not contenders:
        return {}, {}
    ordered = sorted(contenders, key=lambda label: (-len(fired[label]), fired[label][-1].time_from_beep))

    leader = fired[ordered[0]]
    ranks: dict[str, int] = {}
    deltas: dict[str, float] = {}
    for position, label in enumerate(ordered):
        shots = fired[label]
        ranks[label] = position + 1
        deltas[label] = shots[-1].time_from_beep - leader[len(shots) - 1].time_from_beep
    return ranks, deltas
