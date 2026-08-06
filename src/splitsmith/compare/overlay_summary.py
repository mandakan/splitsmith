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

**The summary text is composed through the box engine, not hand-drawn.**
See ``docs/superpowers/plans/2026-08-06-overlay-composition-seam-amendment.md``
(Task 6R-3). ``_cell_groups`` still *declares* what one cell says; turning
that declaration into pixels is now ``overlay_html.summary_html`` (pure
HTML) plus an injected :class:`splitsmith.overlay_raster.Rasterizer`
(HTML -> PNG, via headless Chromium), composited once over the whole
canvas rather than drawn per cell with hand-fitted PIL text. This module
still shells out to ffmpeg only to extract each tile's freeze frame,
through an injected :data:`splitsmith.compare.mp4_grid.Runner` so unit
tests never shell out, and never launches a browser directly -- that is
entirely the injected :class:`~splitsmith.overlay_raster.Rasterizer`'s
job. A host whose ffmpeg lacks libfreetype loses the running clock
elsewhere in the graph but gets a complete summary here, untouched. A host
with no usable Chromium loses the summary's text (see ``build_hold_still``)
but still gets the blurred, dimmed freeze -- degradation, not a crash.

Never draws a number that is absent. ``scorecard`` is ``None`` for
placeholder stages and pre-scorecard projects; a manually-timed stage
carries ``stage_time_is_manual`` with no scorecard; a tile may have no
audit at all. Each of those renders less, never a zero and never a guess.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter

from ..overlay_html import summary_html
from ..overlay_layout import Anchor, CellScale, Element, Emphasis, Flow, Group, Role
from ..overlay_raster import Rasterizer
from ..overlay_theme import OverlayTheme
from .mp4_grid import GridStagePlan, Runner
from .overlay_data import TileStageData
from .overlay_sprites import SpriteGeometry, TilePlacement

logger = logging.getLogger(__name__)

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


def _summary_cells(
    placements: Sequence[TilePlacement],
    data: Mapping[str, TileStageData],
    placings: Mapping[str, StagePlacing],
) -> list[tuple[TilePlacement, tuple[Group, ...]]]:
    """One ``(placement, declared groups)`` pair per placement, in
    placement order -- :func:`splitsmith.overlay_html.summary_html`'s own
    input shape.

    A filler tile's groups are never computed: ``summary_html`` already
    treats ``present=False`` as an empty cell regardless of what groups it
    is handed (the same defensive posture ``_draw_cell`` used to take),
    but computing them anyway would be pointless work for a cell nothing
    will render text into.
    """
    cells: list[tuple[TilePlacement, tuple[Group, ...]]] = []
    for placement in placements:
        if not placement.present:
            cells.append((placement, ()))
            continue
        tile = data.get(placement.label)
        groups = _cell_groups(tile, placings.get(placement.label), placement.label)
        cells.append((placement, groups))
    return cells


def build_hold_still(
    placements: Sequence[TilePlacement],
    data: Mapping[str, TileStageData],
    freezes: Mapping[str, Path],
    geometry: SpriteGeometry,
    *,
    theme: OverlayTheme,
    rasterizer: Rasterizer | None = None,
    blur_radius: int | None = None,
    dim: float = DEFAULT_DIM,
) -> Image.Image:
    """Compose the canvas-sized RGB stage summary still.

    Each present tile's blurred, dimmed freeze frame is pasted into its
    cell first (never a crash: a cell with no freeze frame, extraction
    failed or the tile has no trim, is simply the canvas's own black
    background). Every shooter's summary text is then composed in one
    pass: the whole canvas's declared cells (see :func:`_summary_cells`)
    become one HTML document (``overlay_html.summary_html``), rasterized
    to one canvas-sized PNG by the injected ``rasterizer``, and
    alpha-composited over the freezes in a single call -- not drawn per
    cell. This is what makes ``overflow: hidden`` (declared once, in the
    HTML's own stylesheet) the thing that keeps one shooter's figures out
    of another's cell, rather than arithmetic bounding checked here. See
    the module docstring and
    ``docs/superpowers/plans/2026-08-06-overlay-composition-seam-amendment.md``.

    ``rasterizer`` is ``None`` by default -- no text is composed and the
    still is just the blurred, dimmed freezes, which is also the
    degradation path a caller with no usable Chromium falls back to (see
    :mod:`splitsmith.compare.mp4_grid`'s preflight around
    :class:`~splitsmith.overlay_raster.ChromiumRasterizer`). A rasterizer
    that *is* given but fails on this call -- as opposed to failing to
    launch at all, which is the caller's preflight's job to catch once
    per render -- degrades the same way: logged and skipped, so one bad
    stage's rasterization does not cost the whole render the way raising
    out of this function would (mirroring :func:`_prepare_cell` and
    :func:`extract_freeze_frames`'s own per-tile degradation elsewhere in
    this module).

    A filler tile (``present=False``) gets no freeze and no summary text
    at all: it is not a shooter, so text over black would imply a
    competitor who isn't there.

    ``blur_radius`` defaults to ``max(8, cell_height // 60)`` -- scaled
    from the cell so a 4K canvas and a small preview blur proportionally.
    ``dim`` defaults to :data:`DEFAULT_DIM`.
    """
    _check_stage_keys(data)
    radius = blur_radius if blur_radius is not None else max(8, geometry.cell_height // 60)

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

    if rasterizer is not None:
        placings = _rank_placings(placements, data)
        cells = _summary_cells(placements, data, placings)
        scale = CellScale.for_cell(geometry.cell_height)
        html = summary_html(cells, geometry=geometry, scale=scale, theme=theme)
        try:
            png_bytes = rasterizer.png(html, width=geometry.canvas_width, height=geometry.canvas_height)
            with Image.open(io.BytesIO(png_bytes)) as overlay_image:
                overlay_rgba = overlay_image.convert("RGBA")
        except Exception as exc:  # noqa: BLE001 -- one bad rasterization must not lose the stage
            logger.warning(
                "compare overlay summary: could not rasterize the stage summary (%s); "
                "the still composes without any text",
                exc,
            )
        else:
            canvas.alpha_composite(overlay_rgba)

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
    rasterizer: Rasterizer | None = None,
    blur_radius: int | None = None,
    dim: float = DEFAULT_DIM,
    output_path: Path | None = None,
) -> Path:
    """Extract this stage's freeze frames, compose the hold still, and
    save it. ``data`` is a single stage's slice keyed by label -- the same
    contract as :func:`build_hold_still` and
    ``overlay_sprites.build_overlay_states``.

    Convenience wrapper only: ``mp4_grid`` wires the frame it produces
    into the ffmpeg graph as one more static input, the same way the
    sprite sequence already is. Nothing here touches that graph.
    """
    placements = _placements_for_plan(plan)
    freezes = extract_freeze_frames(
        plan,
        work_dir=work_dir,
        ffmpeg_binary=ffmpeg_binary,
        runner=runner,
    )
    still = build_hold_still(
        placements,
        data,
        freezes,
        geometry,
        theme=theme,
        rasterizer=rasterizer,
        blur_radius=blur_radius,
        dim=dim,
    )
    out_path = output_path or work_dir / f"summary-stage{plan.stage_number}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    still.save(out_path)
    return out_path
