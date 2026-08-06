"""The frozen post-stage summary still: freeze, blur once, compose.

At the end of a stage's action the grid holds on a still frame per tile,
blurred and dimmed, with that shooter's stage summary drawn over their own
cell -- shot count, stage time, hit factor, ``stage_pct``, hit counts, and
(the one thing the pre-Milestone-A brief did not have) a cross-shooter
placing ranked by ``stage_pct``. See
``docs/superpowers/plans/2026-08-05-compare-grid-milestone-b-kickoff.md``,
"Amendments to Tasks 7-9", for why the ranking exists and what it must
never become: it is not the deleted live delta strip relocated, and it
does not reintroduce ``TilePanel.rank`` / ``delta_to_leader``.

**The blur happens once, not per frame.** The tile is a still: one PIL
``GaussianBlur`` call, held for the whole hold duration. Applying ``gblur``
to every frame of a multi-second 4K hold in the ffmpeg graph would cost
orders of magnitude more for an identical result -- if a filter graph
change to add a blur ever looks tempting, that is Task 9's territory and
this module already did the work.

**This module is pure PIL.** It draws no ``drawtext`` and shells out to
ffmpeg only to extract each tile's freeze frame, through an injected
:data:`splitsmith.compare.mp4_grid.Runner` so unit tests never shell out.
A host whose ffmpeg lacks libfreetype loses the running clock elsewhere in
the graph but gets a complete summary here, untouched.

Never draws a number that is absent. ``scorecard`` is ``None`` for
placeholder stages and pre-scorecard projects; a manually-timed stage
carries ``stage_time_is_manual`` with no scorecard; a tile may have no
audit at all. Each of those renders less, never a zero and never a guess.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from ..overlay_text import _draw_text_with_shadow, _load_font
from ..overlay_theme import OverlayTheme
from .mp4_grid import GridStagePlan, Runner
from .overlay_data import TileStageData
from .overlay_sprites import SpriteGeometry, TilePlacement

logger = logging.getLogger(__name__)

#: Font sizes never shrink below this floor -- matches
#: ``overlay_sprites._MIN_FONT_SIZE``'s reasoning: below it a further
#: shrink reads as noise rather than smaller text.
_MIN_FONT_SIZE = 12

#: Default fraction of black composited over a tile's still. Chosen so the
#: summary text is legible over any footage without crushing the picture
#: to nothing -- it is still recognisably the shooter's own frame.
DEFAULT_DIM = 0.45

#: How far before a tile's own footage end the freeze extraction starts
#: reading, in seconds.
#:
#: The seek cannot be the last frame's exact timestamp. ``-ss`` keeps the
#: first frame at or after the requested time, and the duration the loader
#: probed is the *container's* (``format=duration``), which on a real
#: trim runs past the last video frame: measured on the 24s synthetic
#: fixture, ``format=duration`` 24.000 against a last video pts of
#: 23.9573 and a video-stream duration of 23.9906. Asking for
#: ``duration - one_frame`` (23.9666) returns nothing at all -- and so
#: does asking for 23.9573, the last pts itself, because any rounding in
#: the seek path lands past it. Both exit **0** having written no file.
#:
#: So the read starts a window before the end and ``-update 1`` lets each
#: decoded frame overwrite the last, which leaves the true final frame in
#: the file. The window is what bounds the work (measured at 3840x2160:
#: 1.23s against 0.82s for a single-frame extraction) and how far the
#: container's duration may overstate the video's before the extraction
#: comes up empty -- at which point :func:`extract_freeze_frames` reports
#: it rather than returning a path to a file that was never written.
_FREEZE_TAIL_WINDOW_SECONDS = 0.5


def _check_stage_keys(data: Mapping[str, TileStageData]) -> None:
    """Reject a whole-match mapping passed where a per-stage one belongs.

    Mirrors ``overlay_sprites._check_keys``: ``load_overlay_data`` is keyed
    by ``(label, stage_number)`` tuples, and ``build_hold_still`` wants a
    single stage's slice keyed by label alone. Handing over the wrong
    mapping matches no tile at all -- every cell would silently render
    with no data rather than raise, so the guard is cheap and the failure
    mode it prevents is invisible without it.
    """
    for key in data:
        if not isinstance(key, str):
            raise ValueError(
                "build_hold_still expects a mapping keyed by tile label (str), "
                f"got a {type(key).__name__} key {key!r}. load_overlay_data is keyed "
                "by (label, stage_number) -- slice out the stage first."
            )


def _placements_for_plan(plan: GridStagePlan) -> tuple[TilePlacement, ...]:
    """A plan's tiles as :class:`TilePlacement`, mirroring
    ``mp4_grid._stage_overlay_plan``'s construction so this module and the
    sprite overlay agree on what "present" means for the same stage."""
    return tuple(
        TilePlacement(label=tile.label, row=tile.row, col=tile.col, present=tile.trim_path is not None)
        for tile in plan.tiles
    )


def extract_freeze_frames(
    plan: GridStagePlan,
    *,
    work_dir: Path,
    ffmpeg_binary: str,
    runner: Runner,
) -> dict[str, Path]:
    """One still per present tile: the last frame of *that tile's* footage.

    **Not the last frame of the action.** The stage runs for
    ``head_pad + the longest tile's post-beep span + tail_pad``, and every
    tile chain in ``mp4_grid.build_stage_command`` is ``tpad``-ed with
    black across that whole span. So the action's final frames are black
    on every tile -- on the longest one for the tail pad's worth, and on
    every shorter one for longer than that. A seek derived from
    ``plan.duration_seconds`` therefore lands past the end of the tile's
    own clip, where there is no frame to take at all: that is what put
    text on pure black in every cell of the shipped default render.

    The target is ``tile.source_duration_seconds`` instead, which is that
    clip's own length in its own time -- no beep, seek or lead pad enters
    into it, because the tile is being read directly rather than through
    the graph. See :data:`_FREEZE_TAIL_WINDOW_SECONDS` for why the read
    starts a window short of that and takes the last frame it decodes
    rather than seeking straight to the final timestamp.

    Filler tiles (``trim_path is None``) are skipped -- there is no source
    to seek into. A tile whose extraction fails is logged and skipped
    rather than raised: one unreadable trim must not lose the whole
    stage's summary, it should just leave that one cell black in
    :func:`build_hold_still`. "Fails" includes the quiet case, which is
    the one that shipped: ``ffmpeg -ss <past EOF> -i clip -frames:v 1``
    exits **0**, reports ``frame= 0`` and writes nothing, so a returncode
    check alone hands back a path to a file that does not exist and the
    fault only surfaces two layers later, in :func:`_prepare_cell`, as a
    cell that renders black for no stated reason.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    freezes: dict[str, Path] = {}
    for tile in plan.tiles:
        if tile.trim_path is None:
            continue
        seek_seconds = max(0.0, tile.source_duration_seconds - _FREEZE_TAIL_WINDOW_SECONDS)
        out_path = work_dir / f"freeze-stage{plan.stage_number}-r{tile.row}c{tile.col}.png"
        # A stale PNG from an earlier render into a reused work dir would
        # otherwise pass the existence check below without this run having
        # written anything, which is the same lie in a different disguise.
        out_path.unlink(missing_ok=True)
        cmd = [
            ffmpeg_binary,
            "-hide_banner",
            "-y",
            "-ss",
            f"{seek_seconds:g}",
            "-i",
            str(tile.trim_path),
            "-an",
            "-update",
            "1",
            str(out_path),
        ]
        try:
            completed = runner(cmd, capture_output=True)
        except Exception as exc:  # noqa: BLE001 -- one bad trim must not lose the stage
            logger.warning(
                "compare overlay summary: could not run ffmpeg to freeze %s (%s); "
                "that tile's summary cell will render black",
                tile.label,
                exc,
            )
            continue
        if completed.returncode != 0:
            logger.warning(
                "compare overlay summary: ffmpeg exited %d extracting a freeze frame for %s; "
                "that tile's summary cell will render black",
                completed.returncode,
                tile.label,
            )
            continue
        if not _wrote_a_frame(out_path):
            logger.warning(
                "compare overlay summary: ffmpeg exited 0 but wrote no freeze frame for %s "
                "(seek %.3fs into a %.3fs clip, %s); that tile's summary cell will render black",
                tile.label,
                seek_seconds,
                tile.source_duration_seconds,
                tile.trim_path,
            )
            continue
        freezes[tile.label] = out_path
    return freezes


def _wrote_a_frame(out_path: Path) -> bool:
    """Did the extraction actually leave a frame behind?

    ffmpeg's exit code does not answer this. Asked for a frame past the
    end of a clip it exits 0, prints ``frame= 0`` and creates no file, so
    without this check :func:`extract_freeze_frames` returns a path to
    nothing and reports success. A zero-byte file is the same failure with
    the file created (an interrupted or out-of-space write), so size is
    checked too rather than existence alone.
    """
    try:
        return out_path.stat().st_size > 0
    except OSError:
        return False


def _letterbox(frame: Image.Image, cell_width: int, cell_height: int) -> Image.Image:
    """Scale ``frame`` to fit inside a ``cell_width x cell_height`` cell,
    preserving aspect ratio, and centre it on black -- the PIL equivalent
    of ``mp4_grid``'s per-tile
    ``scale=cw:ch:force_original_aspect_ratio=decrease,pad=cw:ch:...``
    filter pair, so a freeze frame lands in its cell exactly the way the
    live footage did."""
    scale = min(cell_width / frame.width, cell_height / frame.height)
    new_size = (max(1, round(frame.width * scale)), max(1, round(frame.height * scale)))
    resized = frame.resize(new_size, Image.LANCZOS)
    cell = Image.new("RGB", (cell_width, cell_height), (0, 0, 0))
    cell.paste(resized, ((cell_width - new_size[0]) // 2, (cell_height - new_size[1]) // 2))
    return cell


def _apply_blur(image: Image.Image, radius: int) -> Image.Image:
    """The one place a Gaussian blur touches a hold-still tile.

    Kept as its own named function -- not inlined -- so a test can count
    calls to it directly rather than to ``Image.filter`` in general, which
    ``overlay_text._draw_text_with_shadow`` also calls (for its drop
    shadow) and would otherwise be double-counted.
    """
    if radius <= 0:
        return image
    return image.filter(ImageFilter.GaussianBlur(radius))


def _dim(image: Image.Image, amount: float) -> Image.Image:
    """Darken ``image`` by compositing a black layer at ``amount`` alpha.

    ``Image.blend(image, black, amount)`` over two same-size RGB images is
    the same operation as alpha-compositing an opaque black layer at
    ``amount``, without needing to round-trip through an alpha channel.
    """
    if amount <= 0:
        return image
    black = Image.new("RGB", image.size, (0, 0, 0))
    return Image.blend(image, black, min(1.0, amount))


def _prepare_cell(
    freeze_path: Path, geometry: SpriteGeometry, *, radius: int, dim: float
) -> Image.Image | None:
    """The blurred, dimmed, cell-sized still for one tile, or ``None`` if
    the freeze frame can't be read -- degrading to a black cell rather than
    failing the whole summary."""
    try:
        with Image.open(freeze_path) as source:
            frame = source.convert("RGB")
    except Exception as exc:  # noqa: BLE001 -- a bad freeze degrades to black, not a crash
        logger.warning(
            "compare overlay summary: cannot read freeze frame %s (%s); cell renders black",
            freeze_path,
            exc,
        )
        return None
    letterboxed = _letterbox(frame, geometry.cell_width, geometry.cell_height)
    blurred = _apply_blur(letterboxed, radius)
    return _dim(blurred, dim)


@dataclass(frozen=True)
class StagePlacing:
    """One shooter's cross-shooter rank for the stage, by ``stage_pct``.

    ``total_ranked`` is the size of the *ranked pool*, not the roster.
    It is computed but deliberately not drawn: "#1 of 3" on a stage a
    7-shooter roster ran (DQ'd, no scorecard and no audit each excluded
    from the pool) reads as a 3-shooter field when 7 people actually shot
    it. The grid itself already shows the field size; the bare "#1" says
    only where this shooter landed in it. Kept on the dataclass in case a
    future caller needs the pool size for something other than display.
    """

    rank: int
    total_ranked: int


def _rank_placings(
    placements: Sequence[TilePlacement], data: Mapping[str, TileStageData]
) -> dict[str, StagePlacing]:
    """Competition ranking (ties share a place; the next place skips
    accordingly) by ``scorecard.stage_pct``, highest first.

    Only a present tile with a scorecard carrying a ``stage_pct`` enters
    the pool. Three situations all stay out of it, and none gets an
    invented placing: a tile with no scorecard, a filler tile
    (``present=False``), and a DQ'd shooter -- a DQ's ``stage_pct`` is not
    a rankable finish even when the field carries a number, per the
    kickoff doc's "Amendments to Tasks 7-9".

    Never ``stage_points``: raw points are meaningless across stages and
    divisions, ``stage_pct`` is the number that is comparable.
    """
    entries: list[tuple[float, str]] = []
    for placement in placements:
        if not placement.present:
            continue
        tile = data.get(placement.label)
        if tile is None or tile.scorecard is None:
            continue
        scorecard = tile.scorecard
        if scorecard.dq or scorecard.stage_pct is None:
            continue
        entries.append((scorecard.stage_pct, placement.label))
    entries.sort(key=lambda entry: (-entry[0], entry[1]))
    total = len(entries)
    placings: dict[str, StagePlacing] = {}
    rank = 0
    previous_pct: float | None = None
    for index, (pct, label) in enumerate(entries):
        if previous_pct is None or pct != previous_pct:
            rank = index + 1
        placings[label] = StagePlacing(rank=rank, total_ranked=total)
        previous_pct = pct
    return placings


def _theme_font_name(theme: OverlayTheme) -> str | None:
    """The ``_load_font`` preset name for this theme's face.

    Mirrors ``overlay_sprites.theme_font_face``'s choice (bundled mono for
    ``splitsmith``, system discovery otherwise) so the summary's typography
    matches the sprite's, but goes through ``_load_font`` directly per this
    module's interface rather than ``load_face`` -- ``_load_font`` does its
    own bundled/preset/fallback resolution and needs no font object cache
    at this call volume (once per stage, not per frame).
    """
    return "splitsmith-mono" if theme.name == "splitsmith" else None


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    theme: OverlayTheme,
    *,
    base_size: int,
    budget: float,
):
    """The largest font at or below ``base_size`` (steps of 2) that draws
    ``text`` no wider than ``budget`` pixels, floored at
    :data:`_MIN_FONT_SIZE`. Returns ``(font, size)``."""
    font_name = _theme_font_name(theme)
    size = base_size
    font = _load_font(None, size, font_name=font_name)
    while size > _MIN_FONT_SIZE and _text_width(draw, text, font) > budget:
        size -= 2
        font = _load_font(None, size, font_name=font_name)
    return font, size


#: Scales :func:`_lay_out_block` tries, in order, before giving up and
#: clipping. Each step is a 15% cut, which is coarse enough that a block
#: needing a real shrink gets there in a handful of measurements and fine
#: enough that a block that barely overflows does not end up half-size.
_BLOCK_SCALES = (1.0, 0.85, 0.72, 0.61, 0.52, 0.44, 0.37, 0.32, 0.27, 0.23)


def _lay_out_block(
    draw: ImageDraw.ImageDraw,
    lines: Sequence[tuple[str, int, tuple[int, int, int, int]]],
    theme: OverlayTheme,
    *,
    width_budget: float,
    height_budget: float,
) -> list[tuple[str, object, int, tuple[int, int, int, int], int]]:
    """Place ``lines`` inside a cell, bounded on *both* axes.

    :func:`_fit_font` bounds the width of one line. Nothing bounded the
    height of the stack of them, so a cell shorter than the block --
    measured at a constant 207-247px, which is every cell below about
    250px tall -- spilled its shooter's figures down into the cell of the
    shooter underneath. That is worse than losing a line: it attributes
    one competitor's numbers to another, and the neighbour has no way to
    tell them apart.

    So the whole block is scaled down until it fits (floored at
    :data:`_MIN_FONT_SIZE`, the same floor per-line width fitting uses),
    and if it still does not fit at the floor, the lines that would cross
    the cell's bottom edge are dropped. Both bounds are hard; neither
    draws outside the cell. A cell with room for the block at full size
    -- which is every cell of the shipped 3840x2160 default, at 540-1080px
    tall -- lays out exactly as before, because the first scale tried is
    ``1.0``.

    Returns ``(text, font, fitted_size, color, y_offset)`` per drawn line,
    with ``y_offset`` relative to the block's top.
    """
    placed: list[tuple[str, object, int, tuple[int, int, int, int], int]] = []
    for scale in _BLOCK_SCALES:
        placed = []
        y = 0
        at_floor = True
        for text, size, color in lines:
            scaled = max(_MIN_FONT_SIZE, round(size * scale))
            font, fitted = _fit_font(draw, text, theme, base_size=scaled, budget=width_budget)
            at_floor = at_floor and fitted <= _MIN_FONT_SIZE
            placed.append((text, font, fitted, color, y))
            bbox = draw.textbbox((0, y), text, font=font)
            y += (bbox[3] - bbox[1]) + max(6, fitted // 6)
        if y <= height_budget or at_floor:
            break
    # Still over budget at the floor: drop whole lines from the bottom
    # rather than let them cross into the next shooter's cell.
    kept: list[tuple[str, object, int, tuple[int, int, int, int], int]] = []
    for text, font, fitted, color, y in placed:
        bbox = draw.textbbox((0, y), text, font=font)
        if bbox[3] > height_budget and kept:
            break
        kept.append((text, font, fitted, color, y))
    return kept


def _hit_count_line(scorecard) -> str | None:
    """``A7 C2 D1 M0 NS0``, omitting any field that is ``None``. Returns
    ``None`` (draw nothing) when every field is ``None``."""
    parts: list[str] = []
    if scorecard.alphas is not None:
        parts.append(f"A{scorecard.alphas}")
    if scorecard.charlies is not None:
        parts.append(f"C{scorecard.charlies}")
    if scorecard.deltas is not None:
        parts.append(f"D{scorecard.deltas}")
    if scorecard.misses is not None:
        parts.append(f"M{scorecard.misses}")
    if scorecard.no_shoots is not None:
        parts.append(f"NS{scorecard.no_shoots}")
    return " ".join(parts) if parts else None


def _cell_lines(
    tile: TileStageData | None,
    placing: StagePlacing | None,
    label: str,
    *,
    label_size: int,
    stat_size: int,
    ink: tuple[int, int, int, int],
    accent: tuple[int, int, int, int],
) -> list[tuple[str, int, tuple[int, int, int, int]]]:
    """Every line this cell draws, in order, each with its own size and
    color. A tile with no audit and no scorecard yields just the label."""
    lines: list[tuple[str, int, tuple[int, int, int, int]]] = [(label, label_size, ink)]
    if placing is not None:
        # Bare "#2", not "#2 of 4": only scorecard-carrying tiles enter
        # the ranked pool, so a stage a 7-shooter roster ran would show
        # "of 4" against 3 legitimately un-ranked shooters (DQ'd, no
        # scorecard, no audit) still on screen -- reading as a smaller
        # field than actually shot. The grid itself already shows the
        # field size; the placing only needs to say where this shooter
        # landed in it.
        lines.append((f"#{placing.rank}", stat_size, accent))

    if tile is None:
        return lines

    if tile.has_shots:
        lines.append((f"{tile.shot_count} shots", stat_size, ink))

    if tile.stage_time_seconds is not None:
        text = f"Time {tile.stage_time_seconds:.2f}"
        if tile.stage_time_is_manual:
            text += " (manual)"
        lines.append((text, stat_size, ink))

    scorecard = tile.scorecard
    if scorecard is not None:
        if scorecard.dq:
            lines.append(("DQ", stat_size, accent))
        else:
            if scorecard.hit_factor is not None:
                lines.append((f"HF {scorecard.hit_factor:.2f}", stat_size, ink))
            if scorecard.stage_pct is not None:
                lines.append((f"Stage {scorecard.stage_pct:.1f}%", stat_size, ink))
            hits = _hit_count_line(scorecard)
            if hits is not None:
                lines.append((hits, stat_size, ink))

    if tile.has_shots:
        rest = [shot.split for shot in tile.shots[1:]]
        if rest:
            lines.append(
                (
                    f"Best {min(rest):.2f}  Avg {sum(rest) / len(rest):.2f}  Worst {max(rest):.2f}",
                    stat_size,
                    ink,
                )
            )
        lines.append((f"Draw {tile.shots[0].split:.2f}", stat_size, ink))

    return lines


def _draw_cell(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    placement: TilePlacement,
    tile: TileStageData | None,
    placing: StagePlacing | None,
    geometry: SpriteGeometry,
    *,
    theme: OverlayTheme,
) -> None:
    """Draw one present tile's summary text over its own cell. A filler
    tile (``present`` False) draws nothing, matching the live sprite's
    treatment of an empty slot."""
    if not placement.present:
        return

    x0 = placement.col * geometry.cell_width
    y0 = placement.row * geometry.cell_height
    pad = max(20, geometry.cell_height // 40)
    width_budget = max(1, geometry.cell_width - 2 * pad)
    label_size = max(32, geometry.cell_height // 16)
    stat_size = max(18, geometry.cell_height // 32)
    ink = (*theme.ink, 255)
    accent = (*theme.accent, 255)

    lines = _cell_lines(
        tile,
        placing,
        placement.label,
        label_size=label_size,
        stat_size=stat_size,
        ink=ink,
        accent=accent,
    )

    height_budget = max(1, geometry.cell_height - 2 * pad)
    for text, font, fitted_size, color, offset in _lay_out_block(
        draw, lines, theme, width_budget=width_budget, height_budget=height_budget
    ):
        _draw_text_with_shadow(
            draw,
            canvas,
            (x0 + pad, y0 + pad + offset),
            text,
            font,
            color,
            stroke_width=max(2, fitted_size // 16),
            shadow_offset=max(2, fitted_size // 20),
            shadow_blur=max(3, fitted_size // 10),
            stroke_color=theme.stroke,
            shadow_color=theme.shadow,
        )


def build_hold_still(
    placements: Sequence[TilePlacement],
    data: Mapping[str, TileStageData],
    freezes: Mapping[str, Path],
    geometry: SpriteGeometry,
    *,
    theme: OverlayTheme,
    blur_radius: int | None = None,
    dim: float = DEFAULT_DIM,
) -> Image.Image:
    """Compose the canvas-sized RGB stage summary still.

    Each present tile's blurred, dimmed freeze frame is pasted into its
    cell first, then that shooter's summary text is drawn over it -- so a
    cell with no freeze frame (extraction failed, or the tile has no
    trim) is simply the canvas's own black background with the text drawn
    over it, never a crash. A filler tile (``present=False``) draws
    nothing at all: it is not a shooter, so text over black would imply a
    competitor who isn't there.

    ``blur_radius`` defaults to ``max(8, cell_height // 60)`` -- scaled
    from the cell so a 4K canvas and a small preview blur proportionally.
    ``dim`` defaults to :data:`DEFAULT_DIM`.
    """
    _check_stage_keys(data)
    radius = blur_radius if blur_radius is not None else max(8, geometry.cell_height // 60)

    # RGBA throughout the compose, not RGB: ``_draw_text_with_shadow``
    # alpha-composites its drop shadow onto the canvas, which PIL requires
    # to be RGBA (or LA). Converted to RGB only on the way out, matching
    # this function's documented return type.
    canvas = Image.new("RGBA", (geometry.canvas_width, geometry.canvas_height), (0, 0, 0, 255))
    for placement in placements:
        if not placement.present:
            continue
        freeze_path = freezes.get(placement.label)
        if freeze_path is None:
            continue
        cell_image = _prepare_cell(freeze_path, geometry, radius=radius, dim=dim)
        if cell_image is None:
            continue
        x0 = placement.col * geometry.cell_width
        y0 = placement.row * geometry.cell_height
        canvas.paste(cell_image.convert("RGBA"), (x0, y0))

    placings = _rank_placings(placements, data)
    draw = ImageDraw.Draw(canvas)
    for placement in placements:
        tile = data.get(placement.label)
        _draw_cell(canvas, draw, placement, tile, placings.get(placement.label), geometry, theme=theme)

    return canvas.convert("RGB")


def write_hold_still(
    plan: GridStagePlan,
    data: Mapping[str, TileStageData],
    geometry: SpriteGeometry,
    *,
    theme: OverlayTheme,
    work_dir: Path,
    ffmpeg_binary: str,
    runner: Runner,
    blur_radius: int | None = None,
    dim: float = DEFAULT_DIM,
    output_path: Path | None = None,
) -> Path:
    """Extract this stage's freeze frames, compose the hold still, and
    save it. ``data`` is a single stage's slice keyed by label -- the same
    contract as :func:`build_hold_still` and
    ``overlay_sprites.build_overlay_states``.

    Convenience wrapper only: Task 9 wires the frame it produces into the
    ffmpeg graph as one more static input, the same way the sprite
    sequence already is. Nothing here touches that graph.
    """
    placements = _placements_for_plan(plan)
    freezes = extract_freeze_frames(
        plan,
        work_dir=work_dir,
        ffmpeg_binary=ffmpeg_binary,
        runner=runner,
    )
    still = build_hold_still(
        placements, data, freezes, geometry, theme=theme, blur_radius=blur_radius, dim=dim
    )
    out_path = output_path or work_dir / f"summary-stage{plan.stage_number}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    still.save(out_path)
    return out_path
