"""The grid overlay as a step function over shot events.

Overlay content changes only when someone fires. A 30-shot stage
therefore has ~30 distinct states rather than one per frame, and each
state is rendered once and held -- that is the whole reason this path
costs draws in the tens rather than the hundreds.

Pure computation: states in seconds, no rasterizer, no file I/O. Turning
a state into pixels belongs to the sprite renderer, not here.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from ..overlay_text import _draw_text_with_shadow, _load_font
from ..overlay_theme import OverlayTheme
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

    ``data`` is keyed by tile label, for *one* stage. ``load_overlay_data``
    returns a whole-match mapping keyed by ``(label, stage_number)``, so a
    caller must slice out the stage first.
    """
    _check_keys(data)
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


def _check_keys(data: Mapping[str, TileStageData]) -> None:
    """Reject a whole-match mapping passed where a per-stage one belongs.

    ``load_overlay_data`` is keyed by ``(label, stage_number)``. Handing
    that straight to :func:`build_overlay_states` would match no label at
    all, so every tile would fall back to ``_EMPTY`` and the overlay would
    render completely blank -- no crash, no warning, just an empty panel
    on every tile. Cheap to check, invisible if left to run.
    """
    for key in data:
        if not isinstance(key, str):
            raise ValueError(
                "build_overlay_states expects a mapping keyed by tile label (str), "
                f"got a {type(key).__name__} key {key!r}. load_overlay_data is keyed "
                "by (label, stage_number) -- slice out the stage first."
            )


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
    buckets: dict[float, float] = {}
    for placement in placements:
        if not placement.present:
            continue
        for shot in data.get(placement.label, _EMPTY).shots:
            # Key on the rounded time itself. Scaling it to an int bucket
            # invites ``int(round(1.001, 3) * 1000) == 1000``, which is
            # truncation, not rounding, and silently merges 1.001 into
            # 1.000 -- 372 of the 60000 millisecond slots in a 60s stage.
            key = round(shot.time_from_beep, _EVENT_PRECISION)
            buckets[key] = max(buckets.get(key, shot.time_from_beep), shot.time_from_beep)
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


# --- rendering --------------------------------------------------------


@dataclass(frozen=True)
class SpriteGeometry:
    """Canvas + grid geometry a sprite is rendered for.

    ``cell_width`` / ``cell_height`` use floor division, matching
    ``mp4_grid._cell_size`` exactly -- the sprite has to land on the same
    integer cell boundaries the xstack filter graph uses, or the overlay
    drifts off the tile it is meant to sit on.
    """

    canvas_width: int
    canvas_height: int
    rows: int
    cols: int

    @property
    def cell_width(self) -> int:
        return self.canvas_width // self.cols

    @property
    def cell_height(self) -> int:
        return self.canvas_height // self.rows

    @property
    def strip_height(self) -> int:
        return max(48, self.canvas_height // 20)


def render_state(state: OverlayState, geometry: SpriteGeometry, *, theme: OverlayTheme) -> Image.Image:
    """Rasterize one :class:`OverlayState` to a canvas-sized RGBA sprite.

    Layout mirrors ``overlay_render.DefaultTemplate`` cell-for-cell so the
    single-shooter alpha overlay and this grid overlay read as one
    product: top-left counter, bottom-center last split, same padding and
    type-size formulas driven by the cell (not the canvas).

    Deliberate divergence from ``DefaultTemplate``: the single-shooter
    overlay fades the last split out after ``split_hold_seconds``. This is
    a step function over discrete states, not a per-frame loop, so it
    cannot fade without inventing extra states purely to animate an alpha
    ramp. In a grid the viewer also wants to glance at any moment and read
    what a shooter's last split was, not just the instant after they
    fired. The split label therefore persists at full alpha until the
    next shot replaces it.
    """
    canvas = Image.new("RGBA", (geometry.canvas_width, geometry.canvas_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    pad = max(24, geometry.cell_height // 36)
    big = max(48, geometry.cell_height // 14)
    # Same default-font selection ``DefaultTemplate`` uses: the bundled
    # mono face for the splitsmith theme (deterministic across hosts),
    # generic system discovery otherwise.
    font_name = "splitsmith-mono" if theme.name == "splitsmith" else None
    font = _load_font(None, big, font_name=font_name)
    stroke_width = max(2, big // 18)
    shadow_offset = max(2, big // 24)
    shadow_blur = max(3, big // 12)

    for panel in state.panels:
        _draw_panel(
            canvas,
            draw,
            panel,
            geometry,
            font=font,
            theme=theme,
            pad=pad,
            stroke_width=stroke_width,
            shadow_offset=shadow_offset,
            shadow_blur=shadow_blur,
        )

    _draw_strip(canvas, draw, state, geometry, theme=theme)
    return canvas


def _draw_panel(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    panel: TilePanel,
    geometry: SpriteGeometry,
    *,
    font,
    theme: OverlayTheme,
    pad: int,
    stroke_width: int,
    shadow_offset: int,
    shadow_blur: int,
) -> None:
    """One tile's counter + last split. A filler tile (``present`` False)
    draws nothing at all -- it is not a shooter, so drawing "--/12" over
    black would imply a competitor who isn't there."""
    if not panel.present:
        return

    x0 = panel.col * geometry.cell_width
    y0 = panel.row * geometry.cell_height
    # The strip sits over the bottom of the whole canvas, which can
    # overlap a bottom-row tile's nominal cell. Bottom-anchored content
    # must clear it or the split label would render under the strip.
    content_bottom = min(y0 + geometry.cell_height, geometry.canvas_height - geometry.strip_height)
    ink = (*theme.ink, 255)

    if panel.shots_fired > 0:
        if panel.expected_shots is not None:
            counter_text = f"{panel.shots_fired}/{panel.expected_shots}"
        else:
            counter_text = f"{panel.shots_fired}"
        _draw_text_with_shadow(
            draw,
            canvas,
            (x0 + pad, y0 + pad),
            counter_text,
            font,
            ink,
            stroke_width=stroke_width,
            shadow_offset=shadow_offset,
            shadow_blur=shadow_blur,
            stroke_color=theme.stroke,
            shadow_color=theme.shadow,
        )

    if panel.last_split is not None:
        split_text = f"{panel.last_split:.2f}s"
        bbox = draw.textbbox((0, 0), split_text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = x0 + (geometry.cell_width - tw) // 2
        y = content_bottom - th - pad * 2
        _draw_text_with_shadow(
            draw,
            canvas,
            (x, y),
            split_text,
            font,
            (*theme.split, 255),
            stroke_width=stroke_width,
            shadow_offset=shadow_offset,
            shadow_blur=shadow_blur,
            stroke_color=theme.stroke,
            shadow_color=theme.shadow,
        )


def _strip_entry_text(panel: TilePanel) -> str:
    """One shooter's label in the delta strip.

    The leader's elapsed time at their last shot is not known to the
    sprite (only per-tile shot data crosses into ``render_state``), so
    the leader gets rank + label only -- never a fabricated number. Every
    other ranked tile gets a signed delta; ``delta_to_leader`` can be
    negative (a tile behind on shot count but faster to its own shot k),
    so the sign must come from ``:+.2f`` formatting, never a hardcoded
    ``+`` prefix, or a negative delta would render as ``+-0.10``. A tile
    that hasn't fired yet gets its label with no rank number at all.
    """
    label = panel.label.upper()
    if panel.rank is None:
        return label
    if panel.rank == 1 or panel.delta_to_leader is None:
        return f"{panel.rank} {label}"
    return f"{panel.rank} {label} {panel.delta_to_leader:+.2f}"


def _draw_strip(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    state: OverlayState,
    geometry: SpriteGeometry,
    *,
    theme: OverlayTheme,
) -> None:
    """The bottom band: one entry per present tile, ranked tiles first.

    Nothing is drawn until at least one present tile has fired -- before
    the first shot there is no ranking to show, and a band of bare labels
    would just be noise the viewer has already seen on the tiles above.
    """
    present = [p for p in state.panels if p.present]
    ranked = sorted((p for p in present if p.rank is not None), key=lambda p: p.rank)
    if not ranked:
        return
    unranked = [p for p in present if p.rank is None]
    entries = ranked + unranked

    strip_size = max(20, geometry.strip_height * 2 // 3)
    font = _load_font(None, strip_size, font_name="splitsmith-mono" if theme.name == "splitsmith" else None)
    stroke_width = max(2, strip_size // 18)
    shadow_offset = max(2, strip_size // 24)
    shadow_blur = max(3, strip_size // 12)
    ink = (*theme.ink, 255)

    y0 = geometry.canvas_height - geometry.strip_height
    slot_width = geometry.canvas_width / len(entries)
    for index, panel in enumerate(entries):
        text = _strip_entry_text(panel)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        cx = slot_width * index + slot_width / 2
        x = int(cx - tw / 2)
        y = y0 + (geometry.strip_height - th) // 2
        _draw_text_with_shadow(
            draw,
            canvas,
            (x, y),
            text,
            font,
            ink,
            stroke_width=stroke_width,
            shadow_offset=shadow_offset,
            shadow_blur=shadow_blur,
            stroke_color=theme.stroke,
            shadow_color=theme.shadow,
        )


def _cache_key(geometry: SpriteGeometry, theme: OverlayTheme, panels: tuple[TilePanel, ...]) -> str:
    """SHA-256 over a stable JSON dump of the render *inputs* -- never the
    rendered bytes. Two states with identical geometry/theme/panels hash
    to the same key regardless of timing, so a stage where nothing
    changes between two shot events writes one PNG, not two."""
    payload = {
        "geometry": {
            "canvas_width": geometry.canvas_width,
            "canvas_height": geometry.canvas_height,
            "rows": geometry.rows,
            "cols": geometry.cols,
        },
        "theme": theme.name,
        "panels": [
            {
                "label": p.label,
                "row": p.row,
                "col": p.col,
                "present": p.present,
                "shots_fired": p.shots_fired,
                "expected_shots": p.expected_shots,
                "last_split": p.last_split,
                "rank": p.rank,
                "delta_to_leader": p.delta_to_leader,
            }
            for p in panels
        ],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def write_sprite_sequence(
    states: Sequence[OverlayState],
    geometry: SpriteGeometry,
    *,
    theme: OverlayTheme,
    cache_dir: Path,
) -> tuple[tuple[Path, float], ...]:
    """Render every state, content-addressed, and return ``(png_path,
    duration_seconds)`` per state in order.

    States with identical ``(geometry, theme.name, panels)`` share one
    file -- a 30-shot stage where nothing changes between two events
    writes one PNG, not two, which is the whole point of stepping on
    events instead of frames.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    sequence: list[tuple[Path, float]] = []
    for state in states:
        key = _cache_key(geometry, theme, state.panels)
        path = written.get(key)
        if path is None:
            path = cache_dir / f"sprite-{key[:16]}.png"
            if not path.exists():
                render_state(state, geometry, theme=theme).save(path)
            written[key] = path
        sequence.append((path, state.duration_seconds))
    return tuple(sequence)


def write_concat_list(sequence: Sequence[tuple[Path, float]], path: Path) -> Path:
    """Write an ffmpeg concat-demuxer list for ``sequence``.

    The final ``file`` line repeats the last entry with no duration --
    the concat demuxer ignores the last entry's duration otherwise and
    drops that state to a single frame.
    """
    lines: list[str] = []
    for sprite_path, duration in sequence:
        lines.append(f"file '{sprite_path.resolve()}'")
        lines.append(f"duration {duration:g}")
    if sequence:
        lines.append(f"file '{sequence[-1][0].resolve()}'")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path
