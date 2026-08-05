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
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

from ..overlay_text import OverlayFace, _draw_text_with_shadow, load_face, resolve_overlay_face
from ..overlay_theme import OverlayTheme
from .overlay_data import TileShot, TileStageData

# Shots land on the tolerance side of a boundary rather than the wrong
# side of it: a shot whose time equals the event time has fired.
_EPSILON = 1e-6

# Two shooters firing within the same millisecond must collapse to one
# state; a zero-length state would render a sprite nobody ever sees.
_EVENT_PRECISION = 3

# A boundary already sitting on a frame must stay on it. Frame positions
# are computed in binary floating point, where ``8.3 * 30`` is
# ``249.00000000000003``, so the ceil that rounds a mid-frame boundary
# forward needs a tolerance well below one frame at any real rate. Three
# of the 20000 millisecond positions in a 20s stage do this at 30fps.
_FRAME_EPSILON = 1e-9

_EMPTY = TileStageData(label="", stage_number=0)


@dataclass(frozen=True)
class TilePlacement:
    """Where one shooter's tile sits in the grid.

    ``present=False`` is a filler tile -- the shooter has no trim for this
    stage. It is drawn as nothing at all.
    """

    label: str
    row: int
    col: int
    present: bool


@dataclass(frozen=True)
class TilePanel:
    """One tile's overlay content for the duration of one state.

    Every numeric field is optional because absent data stays absent: a
    tile that has not fired has no split, and a stage with no round count
    has no ``expected_shots``. Neither degrades to zero -- a zero split
    reads as a real number to a viewer.

    Content is strictly per tile. Cross-shooter comparison used to live
    here as ``rank``/``delta_to_leader``, feeding a bottom delta strip;
    both are gone (see :func:`render_state`).
    """

    label: str
    row: int
    col: int
    present: bool
    shots_fired: int
    expected_shots: int | None
    last_split: float | None


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
                # ``overlay_data`` re-derives every split over the
                # time-sorted sequence rather than taking the audit's own,
                # because an audit whose row order is not its time order
                # yields splits between non-adjacent shots -- negative
                # ones included, and this is the number a viewer reads off
                # the screen. Treat ``split`` as given; do not recompute
                # it here from ``time_from_beep``.
                last_split=shots[-1].split if shots else None,
            )
        )
    return tuple(panels)


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

    All text is fit to the space it actually has (the cell's own width)
    before it is drawn: 3x3 and 4x4 are first-class grid kinds
    (``compare/layout.py`` routes 5-16 shooters there), and a font size
    picked from ``cell_height`` alone overflows a narrow cell once there
    are more than a handful of columns.

    **Everything drawn here is per tile.** A full-width delta strip
    ranking the shooters used to run across the bottom of the canvas; it
    was removed after watching it on real footage. A beep-aligned grid
    already *is* the race -- the tiles are synchronised, so who is ahead
    reads straight off the picture -- and a ranked list competes with the
    thing it describes while its band overlaps the bottom row of tiles.
    Cross-shooter comparison belongs to the Milestone B stage summary,
    where the run is over and the ranking is ``stage_pct`` off the
    scorecard rather than live elapsed time. Do not put it back.

    A consequence: a state where nobody has fired yet draws *nothing*, so
    ``render_state`` legitimately returns a fully transparent canvas. Any
    test claiming the sprite reached the pixels has to sample a moment
    where a counter or a split genuinely exists.
    """
    canvas = Image.new("RGBA", (geometry.canvas_width, geometry.canvas_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    pad = max(24, geometry.cell_height // 36)
    big = max(48, geometry.cell_height // 14)

    for panel in state.panels:
        _draw_panel(canvas, draw, panel, geometry, theme=theme, pad=pad, base_size=big)

    return canvas


@lru_cache(maxsize=256)
def _font_at(face: OverlayFace, size: int):
    """One :class:`PIL.ImageFont.FreeTypeFont` per ``(face, size)``.

    Loading re-reads the TTF off disk every call, and the width-fitting
    loops call it once per size step per panel per state. At 3840x2160 /
    3x3 that dominated ``render_state``, which a 12-stage 9-shooter match
    pays a few hundred times before ffmpeg starts.

    Fonts are only measured and drawn from here, never mutated, so one
    instance is safe to share. The key is the resolved *face* rather than
    the theme, so an unhashable theme object can never break the cache
    and two themes resolving to the same face share one font.
    """
    return load_face(face, size)


def theme_font_face(theme: OverlayTheme) -> OverlayFace:
    """The one face this theme's overlay is drawn with, both halves of it.

    The sprite (PIL) and the running clock (ffmpeg ``drawtext``) are two
    different font loaders, and this is the single point that decides
    what either of them draws -- ``mp4_grid`` resolves it once per render
    and hands the same answer to both, so they cannot pick up different
    typefaces. Face selection still matches ``DefaultTemplate``: the
    bundled mono for the splitsmith theme, generic system discovery
    otherwise, except that discovery now ends at the bundled face rather
    than PIL's bitmap default (see
    :func:`splitsmith.overlay_text.resolve_overlay_face`).
    """
    return resolve_overlay_face("splitsmith-mono" if theme.name == "splitsmith" else None)


def _scaled_font(theme: OverlayTheme, size: int):
    """Load this theme's face at ``size``."""
    return _font_at(theme_font_face(theme), size)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


# Font sizes never shrink below this floor. Still legible at typical
# viewing distances; below it a further shrink reads as noise rather
# than smaller text.
_MIN_FONT_SIZE = 12


def _fit_font_by_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    theme: OverlayTheme,
    *,
    base_size: int,
    budget: float,
) -> tuple[object, int]:
    """The largest font at or below ``base_size`` (in steps of 2) that
    draws ``text`` no wider than ``budget`` pixels, floored at
    :data:`_MIN_FONT_SIZE`. Returns ``(font, size)`` -- the caller derives
    stroke/shadow parameters from ``size`` so they scale down with it."""
    size = base_size
    font = _scaled_font(theme, size)
    while size > _MIN_FONT_SIZE and _text_width(draw, text, font) > budget:
        size -= 2
        font = _scaled_font(theme, size)
    return font, size


def _draw_panel(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    panel: TilePanel,
    geometry: SpriteGeometry,
    *,
    theme: OverlayTheme,
    pad: int,
    base_size: int,
) -> None:
    """One tile's counter + last split. A filler tile (``present`` False)
    draws nothing at all -- it is not a shooter, so drawing "--/12" over
    black would imply a competitor who isn't there.

    Each text is sized to the cell's own width, not just ``base_size``
    off ``cell_height`` -- a narrow cell (many columns) can't fit a
    "16/16" counter at the height-driven size, and the text must never
    spill past the tile it belongs to."""
    if not panel.present:
        return

    x0 = panel.col * geometry.cell_width
    y0 = panel.row * geometry.cell_height
    # Bottom-anchored content runs to the cell's own bottom edge. It used
    # to stop short of a full-width delta strip; that strip is gone, so
    # the whole cell is the tile's again.
    content_bottom = y0 + geometry.cell_height
    ink = (*theme.ink, 255)
    width_budget = max(1, geometry.cell_width - 2 * pad)

    if panel.shots_fired > 0:
        if panel.expected_shots is not None:
            counter_text = f"{panel.shots_fired}/{panel.expected_shots}"
        else:
            counter_text = f"{panel.shots_fired}"
        font, size = _fit_font_by_width(draw, counter_text, theme, base_size=base_size, budget=width_budget)
        _draw_text_with_shadow(
            draw,
            canvas,
            (x0 + pad, y0 + pad),
            counter_text,
            font,
            ink,
            stroke_width=max(2, size // 18),
            shadow_offset=max(2, size // 24),
            shadow_blur=max(3, size // 12),
            stroke_color=theme.stroke,
            shadow_color=theme.shadow,
        )

    if panel.last_split is not None:
        split_text = f"{panel.last_split:.2f}s"
        font, size = _fit_font_by_width(draw, split_text, theme, base_size=base_size, budget=width_budget)
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
            stroke_width=max(2, size // 18),
            shadow_offset=max(2, size // 24),
            shadow_blur=max(3, size // 12),
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


def quantize_durations(
    durations: Sequence[float],
    *,
    frame_rate: tuple[int, int],
) -> tuple[float, ...]:
    """Snap every state boundary onto a whole output frame.

    The overlay's boundaries are shot times, which are millisecond-grained
    and land wherever they land. The picture underneath them can only
    change on a frame, so a boundary between two frames is one the
    renderer has to round -- and the ``drawtext`` clock, a per-frame
    expression rather than a stepped image, does not round with it. Left
    alone the two halves of the overlay disagree at every shot: the frame
    at a shot's own time shows the clock reading ``0.70`` with no
    counter, and the counter arrives a frame later against ``0.73``.

    Boundaries round **up**, never to nearest. A shot at 1.712s has not
    happened yet on the frame shown at 1.700s, so incrementing that
    tile's counter there would put a shot on screen before it was fired.
    Rounding up costs at most one frame of lag and can never show a shot
    early.

    The one exception is the segment's own last frame, and it is forced.
    A shot inside that frame has no later frame to round up to: the
    rounded boundary names a frame the segment does not contain, the
    final duration comes out *negative*, and :func:`write_concat_list`
    drops the entry -- so the last state's sprite is never written, the
    trailing repeat holds the previous one, and that shot's counter
    increment never reaches the screen at all. Boundaries are therefore
    clamped to the last frame that exists. Showing a shot up to one frame
    early, on the last frame of the segment, is strictly better than
    never showing it; losing it would break the invariant that a shot is
    never lost.

    Only the boundaries move; the total is preserved exactly, since the
    last state runs to the end of the segment either way, and the final
    duration is always positive for a segment longer than nothing.

    Two events closer together than one frame land on the same boundary,
    as do two clamped to the last frame. The earlier state then has zero
    length and is dropped by :func:`write_concat_list` -- a *display* is
    skipped, never a shot: every state's ``shots_fired`` counts all shots
    up to its own event, so the surviving state already accounts for
    both. Collapsing always keeps the *later* state, which is the one
    whose display supersedes the other; the final state can never be the
    one dropped.
    """
    num, den = frame_rate
    if num <= 0 or den <= 0:
        raise ValueError(f"frame rate must be positive, got {num}/{den}")
    total = math.fsum(durations)
    if not durations:
        return ()
    # The last frame the segment actually contains. ``total`` need not sit
    # on a frame itself, so this is the last frame *started* before the
    # end, and no state may begin after it.
    last_frame = max(0, math.ceil(total * num / den - _FRAME_EPSILON) - 1)
    ceiling = last_frame * den / num
    boundaries: list[float] = []
    elapsed = 0.0
    for duration in durations:
        # ``- _FRAME_EPSILON`` keeps a boundary that is already exactly on
        # a frame there: ``8.3 * 30`` is ``249.00000000000003`` in binary
        # floating point, and a bare ceil would push it a whole frame late.
        frame = math.ceil(elapsed * num / den - _FRAME_EPSILON)
        boundaries.append(min(frame * den / num, ceiling))
        elapsed += duration
    return tuple(
        (boundaries[index + 1] if index + 1 < len(boundaries) else total) - start
        for index, start in enumerate(boundaries)
    )


def write_concat_list(
    sequence: Sequence[tuple[Path, float]],
    path: Path,
    *,
    frame_rate: tuple[int, int],
) -> Path:
    """Write an ffmpeg concat-demuxer list for ``sequence``.

    ``frame_rate`` is the *output* rate, threaded from the canvas rather
    than assumed, and it does two things.

    It quantises every boundary onto a whole output frame
    (:func:`quantize_durations`), so a state can only start on a frame
    that actually exists.

    It is also written into the list as an ``option framerate`` directive
    per entry. Without it the concat demuxer opens each PNG with the
    ``image2`` demuxer's default 25 fps, takes its time base from it and
    snaps every boundary to the 1/25 s grid -- measured with
    ``showinfo``, requested boundaries ``0, 1.6, 1.7, 2.4, 2.5, 3.1``
    decode as ``0, 1.6, 1.72, 2.4, 2.52, 3.12``. Quantising alone cannot
    survive that, because 1/30 s boundaries are not expressible on a
    1/25 s grid at all. The directive goes on the trailing repeat too:
    without it that entry is opened at 25 fps and lands early.

    Zero-length states -- two shots inside one frame, collapsed by the
    quantiser -- are dropped rather than written as ``duration 0``, which
    the demuxer turns into a state no frame can ever show.

    The final ``file`` line repeats the last entry with no duration --
    the concat demuxer ignores the last entry's duration otherwise and
    drops that state to a single frame.
    """
    num, den = frame_rate
    durations = quantize_durations([duration for _, duration in sequence], frame_rate=frame_rate)
    lines: list[str] = []
    last: Path | None = None
    for (sprite_path, _), duration in zip(sequence, durations, strict=True):
        if duration <= 0.0:
            continue
        last = sprite_path.resolve()
        lines.append(f"file '{last}'")
        lines.append(f"option framerate {num}/{den}")
        lines.append(f"duration {duration:.9g}")
    if last is not None:
        lines.append(f"file '{last}'")
        lines.append(f"option framerate {num}/{den}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path
