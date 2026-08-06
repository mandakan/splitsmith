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

from ..overlay_layout import (
    Anchor,
    CellScale,
    Element,
    Emphasis,
    Flow,
    Group,
    Role,
    anchor_origin,
)
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


#: Scales :func:`_fit_group_scale` tries, in order, before giving up and
#: accepting whatever the floor gives it. Each step is a 15% cut, which is
#: coarse enough that a group needing a real shrink gets there in a
#: handful of measurements and fine enough that a group that barely
#: overflows does not end up half-size.
_GROUP_SCALE_STEPS = (1.0, 0.85, 0.72, 0.61, 0.52, 0.44, 0.37, 0.32, 0.27, 0.23)


def _accuracy_line(scorecard) -> str | None:
    """``A7 C2 D1``, omitting any field that is ``None``.

    Accuracy only. Misses and no-shoots used to share this line, which
    made them impossible to emphasise separately from the hits -- and
    procedurals were on neither, so they never reached the screen at all.
    See :func:`_faults_line`.
    """
    return _counts(scorecard, (("alphas", "A"), ("charlies", "C"), ("deltas", "D")))


def _faults_line(scorecard) -> str | None:
    """``M0 NS0 P2``, omitting any field that is ``None``.

    What went wrong, as opposed to how well the shooter shot. A recorded
    zero is drawn: a scoreboard row that says the shooter took no
    procedurals is a fact worth stating, and it is a different fact from
    a row that carried no procedural column at all -- which draws nothing
    here. Those two must stay distinguishable, which is the same rule the
    rest of this module follows.

    ``P`` is the one this function exists for. ``StageScorecard`` has
    carried ``procedurals`` all along and the old merged line never read
    it, so two procedurals -- 20 points -- rendered as nothing.
    """
    return _counts(scorecard, (("misses", "M"), ("no_shoots", "NS"), ("procedurals", "P")))


def _counts(scorecard, fields: tuple[tuple[str, str], ...]) -> str | None:
    """``"<tag><value>"`` per field that is not ``None``, space-joined.

    Returns ``None`` -- draw nothing -- when every field is ``None``.
    """
    parts = [f"{tag}{value}" for name, tag in fields if (value := getattr(scorecard, name)) is not None]
    return " ".join(parts) if parts else None


def has_faults(scorecard) -> bool:
    """Did anything actually go wrong?

    Drives *emphasis*, not presence: a clean run still states ``M0 NS0
    P0``, it just does not light an accent plate to do it. Presence is a
    fact; emphasis is a judgement.
    """
    return any(bool(getattr(scorecard, name)) for name in ("misses", "no_shoots", "procedurals"))


def _cell_groups(
    tile: TileStageData | None,
    placing: StagePlacing | None,
    label: str,
) -> tuple[Group, ...]:
    """What one cell says, as anchored groups rather than an ordered list.

    This used to be a ``list`` of ``(text, size, colour)`` built in a
    fixed sequence, which meant every new figure was an insertion into
    that sequence and every layout assumption around it shifted. Declaring
    position and role instead lets an element be absent without the ones
    around it moving.

    A tile with no audit and no scorecard yields just the label -- that
    cell is the control the hold's pixel checks measure against, so it
    must stay text-free apart from the name.
    """
    scorecard = tile.scorecard if tile is not None else None
    groups: list[Group] = []

    # Top-left: who this is, and how they placed.
    identity: list[Element] = [Element(role=Role.IDENTITY, text=label)]
    if scorecard is not None and scorecard.dq:
        # A DQ takes the placing's slot rather than sitting beside it: a
        # DQ'd run has no rankable finish, so there is no placing to show.
        identity.append(Element(role=Role.VERDICT, text="DQ", emphasis=Emphasis.PLATE))
    elif placing is not None:
        # Bare "#2", not "#2 of 4": only scorecard-carrying tiles enter
        # the ranked pool, so "of 4" on a stage a 7-shooter roster ran
        # would read as a smaller field than actually shot. The grid
        # itself already shows the field size.
        identity.append(Element(role=Role.VERDICT, text=f"#{placing.rank}", emphasis=Emphasis.PLATE))
    groups.append(Group(anchor=Anchor.TOP_LEFT, flow=Flow.ROW, elements=tuple(identity)))

    if tile is None:
        return tuple(groups)

    # Top-right: the running clock's old corner. The shooter's own shot
    # detail settles here so nothing jumps across the action-to-hold cut.
    detail: list[Element] = []
    if tile.has_shots:
        detail.append(Element(role=Role.DETAIL, text=f"{tile.shot_count} shots", emphasis=Emphasis.MUTED))
        rest = [shot.split for shot in tile.shots[1:]]
        if rest:
            best_avg_worst = f"Best {min(rest):.2f}  Avg {sum(rest) / len(rest):.2f}  Worst {max(rest):.2f}"
            detail.append(Element(role=Role.DETAIL, text=best_avg_worst, emphasis=Emphasis.MUTED))
        detail.append(
            Element(role=Role.DETAIL, text=f"Draw {tile.shots[0].split:.2f}", emphasis=Emphasis.MUTED)
        )
    if detail:
        groups.append(Group(anchor=Anchor.TOP_RIGHT, flow=Flow.COLUMN, elements=tuple(detail)))

    # Bottom-left, declared first so it sits on the cell's bottom edge:
    # the three figures the viewer reads first.
    band: list[Element] = []
    if tile.stage_time_seconds is not None:
        text = f"{tile.stage_time_seconds:.2f}"
        if tile.stage_time_is_manual:
            text += " (manual)"
        band.append(Element(role=Role.HEADLINE, text=text, caption="TIME"))
    if scorecard is not None and not scorecard.dq:
        if scorecard.hit_factor is not None:
            band.append(Element(role=Role.HEADLINE, text=f"{scorecard.hit_factor:.2f}", caption="HF"))
        if scorecard.stage_pct is not None:
            band.append(Element(role=Role.HEADLINE, text=f"{scorecard.stage_pct:.1f}%", caption="STAGE"))
    if band:
        groups.append(Group(anchor=Anchor.BOTTOM_LEFT, flow=Flow.ROW, elements=tuple(band)))

    # Then, stacked above it: how well they shot, and what went wrong.
    if scorecard is not None and not scorecard.dq:
        counts: list[Element] = []
        accuracy = _accuracy_line(scorecard)
        if accuracy is not None:
            counts.append(Element(role=Role.DETAIL, text=accuracy, emphasis=Emphasis.MUTED))
        faults = _faults_line(scorecard)
        if faults is not None:
            counts.append(
                Element(
                    role=Role.VERDICT,
                    text=faults,
                    emphasis=Emphasis.PLATE if has_faults(scorecard) else Emphasis.MUTED,
                )
            )
        if counts:
            groups.append(Group(anchor=Anchor.BOTTOM_LEFT, flow=Flow.ROW, elements=tuple(counts)))

    return tuple(groups)


def _plate(
    canvas: Image.Image,
    xy: tuple[int, int],
    text: str,
    font,
    *,
    theme: OverlayTheme,
    size: int,
) -> tuple[int, int]:
    """Ink on a filled accent rectangle, returning the plate's size.

    Not decoration. Measured on a shipped frame, the accent placing drew
    7.1% accent pixels against 33.9% stroke pixels and its reddest pixel
    was ``(201, 8, 10)`` against a theme accent of ``(255, 45, 45)``: a
    stroke around thin glyphs is a halo that eats the glyph, and it eats
    most of it at the smallest size in the cell. The footage underneath
    an overlay is always arbitrary, so the only reliable contrast is one
    that brings its own ground.
    """
    draw = ImageDraw.Draw(canvas)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y = max(8, size // 3), max(5, size // 5)
    plate_w, plate_h = text_w + 2 * pad_x, text_h + 2 * pad_y
    x, y = xy
    canvas.alpha_composite(Image.new("RGBA", (plate_w, plate_h), (*theme.accent, 235)), (int(x), int(y)))
    draw.text((x + pad_x - bbox[0], y + pad_y - bbox[1]), text, font=font, fill=(*theme.ink, 255))
    return plate_w, plate_h


def _fit_group_scale(
    draw: ImageDraw.ImageDraw,
    group: Group,
    theme: OverlayTheme,
    scale: CellScale,
    *,
    width_budget: float,
    height_budget: float,
) -> float:
    """The largest uniform scale factor (from :data:`_GROUP_SCALE_STEPS`)
    that lays ``group`` out within ``height_budget``.

    Mirrors the old whole-cell block shrink, but scoped to one group
    instead of the whole cell: a column of DETAIL lines (top-right's shot
    count / Best-Avg-Worst / Draw) is exactly the shape that used to spill
    one shooter's figures into the cell below, so it is measured and
    shrunk on its own rather than trusting per-element width-fitting alone
    to keep it inside the cell.

    A ``ROW`` group's height is its tallest single line (elements run
    side by side, not stacked); a ``COLUMN`` group's height is the sum of
    its lines. Returns the smallest step in the ladder once every element
    is already at the font floor, even if the group still does not fit --
    :func:`_draw_group` is the one that decides what to do about that
    (drop trailing lines for a column, draw a tight row anyway).
    """
    for factor in _GROUP_SCALE_STEPS:
        extent = 0.0
        row_extent = 0.0
        at_floor = True
        for element in group.elements:
            base = max(_MIN_FONT_SIZE, round(scale.size_for(element.role) * factor))
            font, fitted = _fit_font(draw, element.text, theme, base_size=base, budget=width_budget)
            at_floor = at_floor and fitted <= _MIN_FONT_SIZE
            text_h = draw.textbbox((0, 0), element.text, font=font)[3]
            caption_h = 0
            if element.caption is not None:
                cap_base = max(_MIN_FONT_SIZE, round(scale.caption * factor))
                caption_font, cap_fitted = _fit_font(
                    draw, element.caption, theme, base_size=cap_base, budget=width_budget
                )
                at_floor = at_floor and cap_fitted <= _MIN_FONT_SIZE
                caption_h = draw.textbbox((0, 0), element.caption, font=caption_font)[3] + max(
                    4, scale.caption // 3
                )
            block_h = text_h + caption_h
            if group.flow is Flow.COLUMN:
                extent += block_h + max(6, fitted // 6)
            else:
                row_extent = max(row_extent, block_h)
        total = extent if group.flow is Flow.COLUMN else row_extent
        if total <= height_budget or at_floor:
            return factor
    return _GROUP_SCALE_STEPS[-1]


def _draw_group(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    group: Group,
    *,
    theme: OverlayTheme,
    scale: CellScale,
    origin: tuple[int, int],
    width_budget: float,
    height_budget: float,
) -> int:
    """Draw one group from its anchor origin. Returns the height used.

    The return value is what lets two groups share an anchor without
    overlapping -- the caller offsets the next one by it.

    Bounded on both axes, the invariant the old whole-cell
    ``_lay_out_block`` used to hold: the group is scaled down first (via
    :func:`_fit_group_scale`, in the same coarse steps that function used
    to shrink the whole cell), and a ``COLUMN`` group still too tall at the
    floor has its trailing elements dropped rather than drawn across the
    cell's edge -- always keeping at least the first, the same trade-off
    the old block-level bound made. A ``ROW`` group never drops an
    element -- dropping one would not reduce its height, since a row's
    height is its tallest single line, not a sum -- so it is the caller's
    job (:func:`_draw_cell`) to withhold a *second* group sharing an
    anchor entirely when the first has already used the available space.
    """
    ink = (*theme.ink, 255)
    muted = (*theme.ink, 170)
    factor = _fit_group_scale(
        draw, group, theme, scale, width_budget=width_budget, height_budget=height_budget
    )

    origin_x, origin_y = origin
    cursor_x, cursor_y = origin_x, origin_y
    tallest = 0
    consumed = 0

    for index, element in enumerate(group.elements):
        base_size = max(_MIN_FONT_SIZE, round(scale.size_for(element.role) * factor))
        font, fitted = _fit_font(draw, element.text, theme, base_size=base_size, budget=width_budget)
        bbox = draw.textbbox((0, 0), element.text, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]

        caption_h = 0
        caption_font = None
        caption_bbox = None
        if element.caption is not None:
            cap_base = max(_MIN_FONT_SIZE, round(scale.caption * factor))
            caption_font, _ = _fit_font(draw, element.caption, theme, base_size=cap_base, budget=width_budget)
            caption_bbox = draw.textbbox((0, 0), element.caption, font=caption_font)
            caption_h = (caption_bbox[3] - caption_bbox[1]) + max(4, scale.caption // 3)

        block_h = text_h + caption_h

        if group.flow is Flow.COLUMN and index > 0 and consumed + block_h > height_budget:
            # Would cross the cell edge: drop this line and every one
            # after it rather than draw over the shooter in the next
            # cell. The first element always draws -- an empty group
            # reads as no data, not as "there was no room".
            break

        x = cursor_x - text_w if group.anchor.is_right else cursor_x
        if group.anchor.is_center:
            x = cursor_x - text_w // 2
        y = cursor_y - block_h if group.anchor.is_bottom else cursor_y

        if element.caption is not None:
            _draw_text_with_shadow(
                draw,
                canvas,
                (x, y - caption_bbox[1]),
                element.caption,
                caption_font,
                muted,
                stroke_width=max(2, scale.caption // 16),
                shadow_offset=max(2, scale.caption // 20),
                shadow_blur=max(3, scale.caption // 10),
                stroke_color=theme.stroke,
                shadow_color=theme.shadow,
            )

        text_y = y + caption_h
        if element.emphasis is Emphasis.PLATE:
            plate_w, plate_h = _plate(canvas, (x, text_y), element.text, font, theme=theme, size=fitted)
            advance_w, block_h = plate_w, caption_h + plate_h
        else:
            _draw_text_with_shadow(
                draw,
                canvas,
                (x, text_y - bbox[1]),
                element.text,
                font,
                muted if element.emphasis is Emphasis.MUTED else ink,
                stroke_width=max(2, fitted // 16),
                shadow_offset=max(2, fitted // 20),
                shadow_blur=max(3, fitted // 10),
                stroke_color=theme.stroke,
                shadow_color=theme.shadow,
            )
            advance_w = text_w

        gap = max(6, fitted // 6)
        if group.flow is Flow.ROW:
            cursor_x += -(advance_w + gap) if group.anchor.is_right else advance_w + gap
            tallest = max(tallest, block_h)
        else:
            cursor_y += -(block_h + gap) if group.anchor.is_bottom else block_h + gap
            tallest += block_h + gap
            consumed += block_h + gap

    return tallest + max(6, scale.detail // 2)


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
    """Draw one present tile's declared groups over its own cell.

    A filler tile (``present`` False) draws nothing, matching the live
    sprite's treatment of an empty slot: it is not a shooter, so text
    over black would imply a competitor who isn't there.

    Groups sharing an anchor stack away from that anchor's edge in
    declaration order. Each group is height-bounded on its own rather
    than the cell being bounded once, so a long ``Best/Avg/Worst`` line
    at top-right cannot push the name around. A *second* group sharing an
    anchor (the faults/accuracy row stacked above the TIME/HF/STAGE band)
    is skipped entirely, not drawn undersized, once the first has used up
    the space available on that side -- the band it stacks above is the
    figure the viewer reads first and must not be crowded to make room
    for the quieter line above it.
    """
    if not placement.present:
        return

    scale = CellScale.for_cell(geometry.cell_height)
    cell_x = placement.col * geometry.cell_width
    cell_y = placement.row * geometry.cell_height
    width_budget = max(1, geometry.cell_width - 2 * scale.pad)
    height_budget = max(1, geometry.cell_height - 2 * scale.pad)

    groups = _cell_groups(tile, placing, placement.label)
    consumed: dict[Anchor, int] = {}
    for group in groups:
        offset = consumed.get(group.anchor, 0)
        remaining = height_budget - offset
        if remaining <= 0 and offset > 0:
            # No room left for a *second* group at this anchor: leave it
            # off rather than draw it over the group that already claimed
            # the space (see the docstring). The first group at an anchor
            # (offset == 0) always gets a real attempt.
            continue

        origin_x, origin_y = anchor_origin(
            group.anchor,
            cell_x=cell_x,
            cell_y=cell_y,
            cell_w=geometry.cell_width,
            cell_h=geometry.cell_height,
            pad=scale.pad,
        )
        # Groups grow away from their own edge, so a bottom anchor's
        # second group moves *up* by what the first consumed.
        origin_y += -offset if group.anchor.is_bottom else offset
        used = _draw_group(
            canvas,
            draw,
            group,
            theme=theme,
            scale=scale,
            origin=(origin_x, origin_y),
            width_budget=width_budget,
            height_budget=max(1, remaining),
        )
        consumed[group.anchor] = offset + used


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
