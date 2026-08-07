"""Declaration to sprite: the live per-tile overlay, behind the same seam.

Issue #693. What one tile says *during* a run -- the shot counter and the
last split -- as declared ``Group``/``Element`` objects, composed into
one canvas-sized HTML document by :func:`splitsmith.overlay_html.grid_html`
and turned into a PNG by an injected
:class:`~splitsmith.overlay_raster.Rasterizer`. This module is to the live
overlay exactly what ``compare/overlay_summary.py`` is to the freeze-frame
stage summary, and it is deliberately the *smaller* of the two: a tile
says two things while it is running, and both of them are one string.

**Why this module exists at all.** The live sprite used to be drawn with
PIL by ``overlay_sprites.render_state``, which hand-rolled its own width
fitter (``_fit_font_by_width``: step the font size down by 2 until
``textbbox`` reports a width under budget). Issue #693's measurement of
the sibling code path is the argument against keeping it -- roughly 4:1
mechanism to meaning, with *both* Critical findings of #683's review
inside the mechanism rather than the meaning. #683 replaced the summary's
half of that with a real box model; this module replaces the live half,
so there is one text-fitting mechanism in the overlay pipeline instead of
two, and so #684's single-shooter port inherits one rather than being the
second consumer of a hand-rolled fitter.

**Nothing here measures text.** No ``textbbox``, no font size loop, no
font object -- the one PIL call left in the module
(:func:`write_absent_sprite_sequence`) allocates a fully transparent
canvas and draws nothing on it. A cell's own ``overflow: hidden`` and
CSS's cumulative advance are what keep a counter inside the tile it
belongs to; that guarantee is structural rather than arithmetic, which is
the whole point.
The size a counter draws at is read straight off
:class:`~splitsmith.overlay_layout.CellScale` (driven by *cell* height,
never canvas height -- 3x3 and 4x4 are first-class grid kinds), and CSS
does the rest.

**One behaviour did change, in a corner nothing reaches.** The old
fitter shrank a too-wide string (in steps of 2, floored at 12px) until it
fit its cell; CSS clips it instead. Measured across every grid kind in
``compare/layout.py`` and every canvas in #692, neither happens: a
counter is at most ``"32/32"`` and a split at most ``"0.28s"``, and both
fit at ``CellScale.live_primary`` with room to spare even in a 4x4 cell
at 1080p. Reaching the clip at all takes a shot count no match produces
(``test_a_counter_too_wide_for_its_cell_is_clipped_not_spilled`` uses
eight digits). Where the two differ the clip is the safer failure --
truncated text inside the right tile beats shrunken text that the old
fitter's missing cumulative-width bound could still walk into the next
shooter's cell, which is the #683 review defect this swap removes.

**What did not move.** The running clock is still an ffmpeg ``drawtext``
filter positioned by an expression ffmpeg evaluates at draw time, when it
finally knows ``tw``/``th`` (see
:func:`splitsmith.overlay_layout.anchor_ffmpeg_expr`). It is the one
genuinely per-frame element in the overlay and no rasterizer choice
touches it. ``overlay_sprites.theme_font_face`` therefore stays where it
is: the clock and this module have to resolve the same typeface or the
two halves of the overlay stop matching.

**The step function survives, and it is what makes this affordable.**
Rasterizing through a browser costs roughly 4-5x what PIL did per sprite
(measured on the dev host: ~300 ms at 1920x1080/2x2 and ~460-580 ms at
3840x2160/3x3, against ~60 ms and ~145 ms for PIL). That is a per-*state*
cost, not a per-frame one -- around 30 states per stage, deduped
content-addressed by :func:`write_sprite_sequence` -- so a 12-stage match
pays roughly two extra minutes of rasterizing against an encode measured
in tens of minutes. A per-frame rasterizer would be a non-starter at
these numbers, which is why :mod:`splitsmith.compare.overlay_sprites`'s
event-stepped state machine is the part that did *not* change.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from PIL import Image

from ..overlay_html import grid_html
from ..overlay_layout import Anchor, CellScale, ColorToken, Element, Flow, Group, Role
from ..overlay_raster import Rasterizer
from ..overlay_theme import OverlayTheme
from .overlay_sprites import OverlayState, SpriteGeometry, TilePanel, TilePlacement


def panel_groups(panel: TilePanel) -> tuple[Group, ...]:
    """What one tile says while its run is in progress.

    Two things, each absent when it has nothing honest to say:

    - the **shot counter** at :attr:`~splitsmith.overlay_layout.Anchor.TOP_LEFT`,
      reading ``"7/32"`` when the expected shot count is known and a bare
      ``"7"`` when it is not -- never ``"7/?"``, which would claim a
      denominator exists;
    - the **last split** at
      :attr:`~splitsmith.overlay_layout.Anchor.BOTTOM_CENTER` in the
      theme's split colour.

    A tile that has not fired declares no counter (``"0/32"`` over
    somebody standing at the start position is a number, not information),
    and a tile on its first shot declares no split -- there is no previous
    shot to measure against, and ``0.00s`` would invent one.

    A filler tile (``present`` false) declares nothing at all. It is not a
    shooter, so ``"--/32"`` over black would imply a competitor who isn't
    there. :func:`splitsmith.overlay_html.grid_html` enforces the same
    thing independently for a filler placement; the guard is here as well
    because "absence is first class" is this pipeline's rule, not a
    property a caller should have to remember to preserve.

    Both elements are :attr:`~splitsmith.overlay_layout.Role.LIVE_PRIMARY`
    -- the counter and the running clock have been pinned to one size
    since before ``CellScale`` existed, and the split reads at that size
    too. #683's issue text claimed the counter and clock were at
    *different* weights; rendering them proved that false, and the shared
    role is what keeps it false.
    """
    if not panel.present:
        return ()
    groups: list[Group] = []
    if panel.shots_fired > 0:
        if panel.expected_shots is not None:
            counter = f"{panel.shots_fired}/{panel.expected_shots}"
        else:
            counter = f"{panel.shots_fired}"
        groups.append(
            Group(
                anchor=Anchor.TOP_LEFT,
                flow=Flow.ROW,
                elements=(Element(text=counter, role=Role.LIVE_PRIMARY),),
            )
        )
    if panel.last_split is not None:
        groups.append(
            Group(
                anchor=Anchor.BOTTOM_CENTER,
                flow=Flow.ROW,
                elements=(
                    Element(
                        text=f"{panel.last_split:.2f}s",
                        role=Role.LIVE_PRIMARY,
                        color=ColorToken.SPLIT,
                    ),
                ),
            )
        )
    return tuple(groups)


def state_html(state: OverlayState, geometry: SpriteGeometry, *, theme: OverlayTheme) -> str:
    """One :class:`~splitsmith.compare.overlay_sprites.OverlayState` as a
    canvas-sized HTML document.

    Pure: no browser, no file I/O, no font opened. Rasterizing what this
    returns is :mod:`splitsmith.overlay_raster`'s job, which is why a
    fake rasterizer is enough to test everything above the pixels.

    The document is built by the same :func:`splitsmith.overlay_html.grid_html`
    the stage summary uses, at the same cell geometry
    (``SpriteGeometry.cell_width``/``cell_height``, floor division,
    matching ``mp4_grid._cell_size``) -- so the live overlay and the
    summary land on exactly the integer cell boundaries the ffmpeg
    ``xstack`` graph uses, and cannot drift apart from each other.

    ``CellScale`` is resolved from the *cell* height, once for the whole
    document: every cell in a grid is the same size, and a size read off
    canvas height would be identical at 2x2 and 4x4, which is the bug
    that made narrow cells overflow before ``CellScale`` existed.
    """
    scale = CellScale.for_cell(geometry.cell_height)
    cells = [
        (
            TilePlacement(label=panel.label, row=panel.row, col=panel.col, present=panel.present),
            panel_groups(panel),
        )
        for panel in state.panels
    ]
    return grid_html(cells, geometry=geometry, scale=scale, theme=theme)


def _cache_key(geometry: SpriteGeometry, theme: OverlayTheme, panels: tuple[TilePanel, ...]) -> str:
    """SHA-256 over a stable JSON dump of the render *inputs* -- never the
    rendered bytes. Two states with identical geometry/theme/panels hash
    to the same key regardless of timing, so a stage where nothing changes
    between two shot events rasterizes once, not twice.

    Hashing the inputs rather than the output is what makes the dedup
    worth having now that a sprite costs a browser render: the key is
    known *before* the expensive call, so a repeat is skipped rather than
    computed and then discovered to be identical.
    """
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
    rasterizer: Rasterizer,
) -> tuple[tuple[Path, float], ...]:
    """Rasterize every state, content-addressed, and return ``(png_path,
    duration_seconds)`` per state in order.

    States with identical ``(geometry, theme.name, panels)`` share one
    file and one rasterization -- a 30-shot stage where nothing changes
    between two events renders one PNG, not two, which is the whole point
    of stepping on events instead of frames and matters roughly five times
    more than it did under PIL.

    ``rasterizer`` is required, not optional. The summary's own seam
    accepts ``None`` and degrades to a still with no text
    (``overlay_summary.build_hold_still``), but there is no equivalent
    here: without a rasterizer there is no sprite at all, so "no browser"
    is a decision for :func:`splitsmith.compare.mp4_grid.render_grid_mp4`
    to take once, up front, and report as a degradation -- not something
    this function papers over by silently writing empty PNGs. A rasterizer
    that fails *mid-render* is a different thing again and propagates:
    the browser was there and then wasn't, which is a fault, not a
    configuration.
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
                png = rasterizer.png(
                    state_html(state, geometry, theme=theme),
                    width=geometry.canvas_width,
                    height=geometry.canvas_height,
                )
                path.write_bytes(png)
            written[key] = path
        sequence.append((path, state.duration_seconds))
    return tuple(sequence)


def write_absent_sprite_sequence(
    states: Sequence[OverlayState],
    geometry: SpriteGeometry,
    *,
    cache_dir: Path,
) -> tuple[tuple[Path, float], ...]:
    """The same sequence shape, with every state drawing nothing.

    What ``--overlay`` degrades to on a host with no usable Chromium. The
    sprites are the composited half of the overlay and they are gone; the
    running clock is an ffmpeg ``drawtext`` filter that owes the browser
    nothing and still draws.

    **Why an empty stream rather than no stream.** The sprite concat list
    is read as an extra ffmpeg *input* and the clock's filter chain is
    built around the resulting stream indices (see
    :class:`splitsmith.compare.mp4_grid.StageOverlayPlan`). Dropping the
    input would renumber every stream after it and take the clock down
    with the sprites -- so the honest degradation is a stream that exists
    and paints nothing, not a missing one. One transparent canvas covers
    every state: they all say the same nothing, so a 30-shot stage writes
    one PNG.

    Deliberately takes no ``theme``. There is no content to colour, and
    accepting one would invite a caller to believe the theme still reaches
    the picture here. The cache name is keyed on geometry alone, under its
    own ``blank-`` prefix so it can never collide with a real sprite in
    the shared cache directory -- a blank sharing a name with a rendered
    state would silently blank out a stage that rendered fine.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / (
        f"blank-{geometry.canvas_width}x{geometry.canvas_height}-{geometry.rows}x{geometry.cols}.png"
    )
    if not path.exists():
        # PIL, not the rasterizer: this path exists precisely because there
        # is no rasterizer to call.
        Image.new("RGBA", (geometry.canvas_width, geometry.canvas_height), (0, 0, 0, 0)).save(path)
    return tuple((path, state.duration_seconds) for state in states)
