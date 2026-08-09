"""The frozen post-stage summary still: freeze, blur once, compose.

At the end of a stage's action the grid holds on a still frame per tile,
blurred and dimmed, with that shooter's stage summary drawn over their own
cell: their name (with a DQ chip beside it when DQ'd), then a vertically
centred stack of two equal-weight bands -- **Scoring** (the six
colour-coded hit/fault counts, then hit factor and stage time) and
**Splits** (Best/Avg/Worst/Draw as a captioned four-column grid). This is
issue #683 Task 8's approved design
(``scripts/mock_summary_cell.py``),
replacing the three-rail layout Task 7b shipped: the stage percentage and
the cross-shooter placing that rail carried are gone entirely, not merely
resized or moved -- see the task report for what stayed behind
(``_rank_placings``/``StagePlacing``, unused by this module now but kept
for a possible future caller) and why.

**The blur happens once, not per frame.** The tile is a still: one PIL
``GaussianBlur`` call, held for the whole hold duration. Applying ``gblur``
to every frame of a multi-second 4K hold in the ffmpeg graph would cost
orders of magnitude more for an identical result -- if a filter graph
change to add a blur ever looks tempting, that is Task 9's territory and
this module already did the work.

**The summary text is composed through the box engine, not hand-drawn.**
See ``docs/superpowers/plans/2026-08-06-overlay-composition-seam-amendment.md``
(Task 6R-3). ``_cell_groups`` still *declares* what one cell says; turning
that declaration into pixels is now ``overlay_html.grid_html`` (pure
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
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image, ImageFilter

from ..overlay_html import grid_html
from ..overlay_layout import Anchor, CellScale, ColorToken, Element, Emphasis, Flow, Group, Role
from ..overlay_raster import Rasterizer
from ..overlay_theme import OverlayTheme
from ..ui.project import StageScorecard
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


def _summary_scale(cell_height: int) -> CellScale:
    """The :class:`CellScale` the hold still composes at.

    Deliberately **not** a change to ``CellScale.for_cell`` itself: that
    resolver is shared with the live sprite and the running clock
    (``test_live_primary_and_pad_match_what_the_grid_computes_today``
    pins ``live_primary``/``pad``), so a summary-only need still derives
    its own scale from the same base rather than touching the shared
    formula.

    As of issue #683 Task 8, that need is down to one field: ``pad``.
    Every other formula ``CellScale.for_cell`` resolves (``identity``,
    ``headline``, ``verdict``, ``detail``, ``caption``) *is* the approved
    bands design's own numbers now (``cell_h/7``, ``cell_h/8``, ...) --
    the stage summary is the only caller that reads any of them, so there
    is no other consumer's needs weighing against the mock's own. ``pad``
    stays a summary-only override because it is also the live overlay's
    own inset (the sprite's counter position, the clock's ``drawtext``
    expression) and cannot move -- the mock's own cell padding
    (``max(16, cell_h // 22)``) is a different number entirely.
    """
    base = CellScale.for_cell(cell_height)
    return replace(base, pad=max(16, cell_height // 22))


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
    black across that whole span. So every tile *chain* runs out of
    picture before the action does -- the longest one by the tail pad's
    worth, every shorter one by more. (What the viewer sees there is a
    separate question: with a hold, ``mp4_grid._early_summary_filters``
    paints each tile's own summary cell over that black, so the rendered
    frames are not black even though the chain underneath them is. The
    chain is what this function's arithmetic depends on.) A seek derived
    from ``plan.duration_seconds`` therefore lands past the end of the
    tile's own clip, where there is no frame to take at all: that is what put
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

    **Unused by this module's own output as of issue #683 Task 8.** The
    bare ``"#N"`` chip this fed was removed along with the stage
    percentage it was ranked by -- the approved bands design carries no
    cross-shooter placing at all, only the DQ chip, which is a status,
    not a rank. Kept, with :func:`_rank_placings`, because the ranking
    logic has its own tests and may be wanted again by a future caller;
    see the issue #683 Task 8 report for the decision not to delete it.

    ``total_ranked`` is the size of the *ranked pool*, not the roster.
    It was computed but deliberately never drawn even when this was
    wired up: "#1 of 3" on a stage a 7-shooter roster ran (DQ'd, no
    scorecard and no audit each excluded from the pool) reads as a
    3-shooter field when 7 people actually shot it.
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


#: One entry per hit/fault count, in the fixed reading order the row
#: draws in: accuracy first (best worth to least), then faults. Each
#: entry is ``(scorecard attribute, drawn tag, colour token)`` -- the
#: colour is what the count is *worth* in IPSC scoring, not a judgement
#: about whether the shooter did well: ``A`` is full points
#: (``split_good``), ``C`` is mid (``ink``), ``D`` is low (``split``),
#: and ``M``/``NS``/``P`` are each a flat -10 regardless of division
#: (``accent``) -- a procedural is a penalty by definition, so its tag is
#: red whether or not this shooter took one. See issue #683 Task 7: the
#: old design drew the accuracy trio at ``Role.DETAIL`` and the faults
#: trio at ``Role.VERDICT`` (visibly bigger), which made the faults
#: outweigh the hits even though all six -- plus time -- are inputs to
#: the same hit-factor number. They are one role, one size now; colour
#: carries the meaning size used to carry unevenly.
_COUNT_FIELDS: tuple[tuple[str, str, ColorToken], ...] = (
    ("alphas", "A", ColorToken.SPLIT_GOOD),
    ("charlies", "C", ColorToken.INK),
    ("deltas", "D", ColorToken.SPLIT),
    ("misses", "M", ColorToken.ACCENT_TEXT),
    ("no_shoots", "NS", ColorToken.ACCENT_TEXT),
    ("procedurals", "P", ColorToken.ACCENT_TEXT),
)


#: Drop-priority tiers within the counts row (issue #683 F1). Lower drops
#: first. A zero-valued (unlit) fault carries no information a viewer
#: would miss -- it is the same "worth -10, didn't happen" fact every
#: clean cell in the grid repeats -- so it goes before everything else in
#: Scoring. A genuinely nonzero (lit) fault is the opposite: Tasks 4 and
#: 5 existed specifically to put a real penalty on screen, so it is the
#: LAST thing in Scoring this module will ever drop, and F1's own rule 3
#: ("a lit penalty plate must never be dropped while a zero-valued count
#: survives") falls out of this ordering for free -- by the time a lit
#: element's turn comes up, every zero-valued one is already gone.
#: Accuracy (A/C/D) sits between the two: never a fault regardless of
#: value, so it never plates, but it is still live scoring data and
#: outranks an admittedly-empty fault slot.
_TIER_UNLIT_FAULT = 0
_TIER_ACCURACY = 1
_TIER_LIT_FAULT = 2


def _count_elements(scorecard: StageScorecard) -> list[Element]:
    """The six hit/fault counts as one equal-weight, colour-coded row.

    A field that is ``None`` (the scoreboard never carried that column)
    is omitted entirely -- the same "zero is drawn, absent is not" rule
    the rest of this module follows, just per-field instead of per-line
    now that there is no single joined string to omit as a whole.

    A recorded zero still draws (``P0`` reads body-size red --
    :attr:`~splitsmith.overlay_layout.ColorToken.ACCENT_TEXT`, not the raw
    identity red, which is too thin at this size -- a procedural is
    always worth -10, whether or not this shooter took one -- see
    :data:`_COUNT_FIELDS`), but an *actual* nonzero fault additionally
    gets :attr:`~splitsmith.overlay_layout.Emphasis.PLATE`: colour alone
    says what a count is worth, and a plate says this particular one
    happened. Only the ``M``/``NS``/``P`` entries are eligible -- ``A``/
    ``C``/``D`` are never a fault, so they never plate regardless of
    value.

    Each element also carries a ``drop_priority`` (issue #683 F1's fit
    policy -- see :attr:`~splitsmith.overlay_layout.Element.drop_priority`
    and ``overlay_html._fit_script``), assigned by tier
    (:data:`_TIER_UNLIT_FAULT` / ``_TIER_ACCURACY`` / ``_TIER_LIT_FAULT``)
    and, within a tier, by this row's own reading order -- **not**
    reordered in the returned list**, which stays ``_COUNT_FIELDS``
    order regardless: the priority is metadata for a browser to consult
    under space pressure, not a second way to spell the row's own layout.
    """
    entries: list[tuple[str, str, ColorToken, bool]] = []
    for name, tag, token in _COUNT_FIELDS:
        value = getattr(scorecard, name)
        if value is None:
            continue
        plate = token is ColorToken.ACCENT_TEXT and value > 0
        entries.append((tag, str(value), token, plate))

    def _tier(entry: tuple[str, str, ColorToken, bool]) -> int:
        _tag, _value, token, plate = entry
        if plate:
            return _TIER_LIT_FAULT
        if token is ColorToken.ACCENT_TEXT:
            return _TIER_UNLIT_FAULT
        return _TIER_ACCURACY

    drop_order = sorted(range(len(entries)), key=lambda i: (_tier(entries[i]), i))
    priorities = {index: rank for rank, index in enumerate(drop_order)}

    elements: list[Element] = []
    for index, (tag, value_text, token, plate) in enumerate(entries):
        elements.append(
            Element(
                role=Role.DETAIL,
                text=f"{tag}{value_text}",
                emphasis=Emphasis.PLATE if plate else Emphasis.PLAIN,
                color=token,
                drop_priority=priorities[index],
            )
        )
    return elements


def _time_text(tile: TileStageData) -> str | None:
    """The stage time with its unit attached (``"4.50s"``, optionally
    ``" (manual)"``), or ``None`` if there is no time to show. Units
    attach to the value itself now -- see :func:`_cell_groups`'s
    docstring for why a bare number with a floating caption above it is
    exactly the defect this redesign exists to fix."""
    if tile.stage_time_seconds is None:
        return None
    text = f"{tile.stage_time_seconds:.2f}s"
    if tile.stage_time_is_manual:
        text += " (manual)"
    return text


#: Gap (px) between the six hit/fault counts, matching the approved
#: mock's ``.counts { gap: .5em }`` -- an ``em`` that resolves against the
#: counts row's own font-size (``scale.detail``), which is exactly what
#: multiplying by 0.5 here reproduces without needing a live em context.
def _counts_gap(scale: CellScale) -> int:
    return max(2, round(scale.detail * 0.5))


#: Gap (px) between hit factor and time, matching the mock's
#: ``.figrow { gap: cw // 12 }`` -- deliberately wide (cell-width-driven,
#: not font-driven): these are two distinct figures, not a run of digits.
def _figrow_gap(cell_width: int) -> int:
    return max(8, cell_width // 12)


#: Gap (px) between the four split-statistic columns, matching the
#: mock's ``.sgrid { gap: cw // 24 }``.
def _sgrid_gap(cell_width: int) -> int:
    return max(4, cell_width // 24)


#: Extra space (px) added before the "Splits" label, on top of the
#: anchor's own between-groups gap (``_style_rules``' ``row_gutter``,
#: which approximates the mock's tighter ``.band { gap: ch // 40 }``
#: within-band line spacing) -- so the two bands read as visually equal,
#: separated blocks (mock: ``.stack { gap: ch // 22 }``) rather than one
#: unbroken list of lines.
def _band_gap_extra(cell_height: int) -> int:
    between_bands = max(4, cell_height // 22)
    within_band = max(2, cell_height // 40)
    return max(0, between_bands - within_band)


def _cell_groups(
    tile: TileStageData | None,
    label: str,
    *,
    scale: CellScale,
    cell_width: int,
    cell_height: int,
) -> tuple[Group, ...]:
    """What one cell says, as anchored groups rather than an ordered list.

    Issue #683 Task 8's approved design (``scripts/mock_summary_cell.py``),
    exactly: the shooter's name (with a DQ chip beside it when DQ'd) at
    :attr:`~splitsmith.overlay_layout.Anchor.TOP_CENTER`, left-aligned;
    below it, a vertically centred stack of two equal-weight bands at
    :attr:`~splitsmith.overlay_layout.Anchor.MIDDLE_CENTER`, also
    left-aligned -- **Scoring** (its own label, the six colour-coded
    hit/fault counts, then hit factor and stage time) and **Splits**
    (its own label, then Best/Avg/Worst/Draw as a four-column grid
    spanning the cell's full width). Neither band outranks the other --
    both draw their figures at the same size
    (:attr:`~splitsmith.overlay_layout.Role.HEADLINE`) -- which is the
    whole point of this design and the third attempt at it: the stage
    percentage and the cross-shooter placing this summary used to carry
    are gone entirely (issue #683 Task 8), not merely resized or moved.
    A DQ is not a placing; it stays, as the identity row's chip.

    Units attach to their own value (``"4.50s"``, ``"12.00"`` plus a
    smaller ``"HF"`` suffix -- see :attr:`~splitsmith.overlay_layout.Element.unit`)
    rather than a separate caption row above a bare number.

    A tile with no audit and no scorecard yields just the identity group
    -- that cell is the control the hold's pixel checks measure against,
    so it must stay text-free apart from the name.
    """
    scorecard = tile.scorecard if tile is not None else None
    # Narrowed to a real ``StageScorecard`` (not just a bool) so the reads
    # below -- ``_count_elements``, ``.hit_factor`` -- type-check without
    # an ``assert``/``# type: ignore``: a bare ``bool`` flag doesn't let
    # mypy re-narrow ``scorecard`` itself back from ``StageScorecard |
    # None``, but an ``is not None`` check on this variable directly does.
    active_scorecard: StageScorecard | None = (
        scorecard if scorecard is not None and not scorecard.dq else None
    )
    groups: list[Group] = []

    # Top row: who this is, and the DQ chip beside the name when DQ'd.
    # The placing this once shared the slot with is gone (issue #683 Task
    # 8) -- a DQ is a status, not a placing, so it keeps the slot alone.
    identity: list[Element] = [Element(role=Role.IDENTITY, text=label)]
    if scorecard is not None and scorecard.dq:
        identity.append(Element(role=Role.VERDICT, text="DQ", emphasis=Emphasis.PLATE))
    groups.append(Group(anchor=Anchor.TOP_CENTER, flow=Flow.ROW, elements=tuple(identity), align="left"))

    if tile is None:
        return tuple(groups)

    # The vertically centred stack of two bands. Declared top to bottom;
    # groups sharing MIDDLE_CENTER stack in declaration order.
    stack: list[Group] = []

    counts = _count_elements(active_scorecard) if active_scorecard is not None else []
    time_text = _time_text(tile)
    hf_text = (
        f"{active_scorecard.hit_factor:.2f}"
        if active_scorecard is not None and active_scorecard.hit_factor is not None
        else None
    )
    # A DQ's own scoring is suppressed (``active_scorecard`` above is
    # ``None`` for a DQ'd tile), but a DQ'd tile can still carry a stage
    # time -- the mock's own DQ cell shows "Scoring" with just the time,
    # no counts, no hit factor.
    scoring_present = bool(counts) or hf_text is not None or time_text is not None

    if scoring_present:
        # Drop-priority continues past the counts row's own tiers (issue
        # #683 F1): the whole counts row is exhausted before the fit
        # policy ever reaches hit factor/time, and the "Scoring" label
        # itself is the last thing this module will ever offer to drop
        # on the Scoring side -- see ``overlay_html._fit_script``. Never
        # assigned to anything in the Splits band; F1's rule 2 is "never
        # the splits", so nothing below this block ever sets one.
        next_priority = len(counts)
        working: list[Element] = []
        if hf_text is not None:
            working.append(Element(role=Role.HEADLINE, text=hf_text, unit="HF", drop_priority=next_priority))
            next_priority += 1
        if time_text is not None:
            working.append(Element(role=Role.HEADLINE, text=time_text, drop_priority=next_priority))
            next_priority += 1
        stack.append(
            Group(
                anchor=Anchor.MIDDLE_CENTER,
                flow=Flow.ROW,
                elements=(Element(role=Role.LABEL, text="Scoring", drop_priority=next_priority),),
                align="left",
            )
        )
        if counts:
            stack.append(
                Group(
                    anchor=Anchor.MIDDLE_CENTER,
                    flow=Flow.ROW,
                    elements=tuple(counts),
                    align="left",
                    gap=_counts_gap(scale),
                )
            )
        if working:
            stack.append(
                Group(
                    anchor=Anchor.MIDDLE_CENTER,
                    flow=Flow.ROW,
                    elements=tuple(working),
                    align="left",
                    gap=_figrow_gap(cell_width),
                )
            )

    # Splits: Best/Avg/Worst/Draw, only what can actually be computed --
    # "Best"/"Avg"/"Worst" need at least one non-draw shot; "Draw" needs
    # only the draw itself. Never invented, per the module's own rule.
    splits: list[Element] = []
    if tile.has_shots:
        rest = [shot.split for shot in tile.shots[1:]]
        if rest:
            splits.append(Element(role=Role.HEADLINE, text=f"{min(rest):.2f}", caption="Best"))
            splits.append(Element(role=Role.HEADLINE, text=f"{sum(rest) / len(rest):.2f}", caption="Avg"))
            splits.append(Element(role=Role.HEADLINE, text=f"{max(rest):.2f}", caption="Worst"))
        splits.append(Element(role=Role.HEADLINE, text=f"{tile.shots[0].split:.2f}", caption="Draw"))

    if splits:
        stack.append(
            Group(
                anchor=Anchor.MIDDLE_CENTER,
                flow=Flow.ROW,
                elements=(Element(role=Role.LABEL, text="Splits"),),
                align="left",
                margin_top=_band_gap_extra(cell_height) if scoring_present else None,
            )
        )
        stack.append(
            Group(
                anchor=Anchor.MIDDLE_CENTER,
                flow=Flow.GRID,
                elements=tuple(splits),
                align="left",
                gap=_sgrid_gap(cell_width),
            )
        )

    groups.extend(stack)
    return tuple(groups)


def _summary_cells(
    placements: Sequence[TilePlacement],
    data: Mapping[str, TileStageData],
    *,
    scale: CellScale,
    cell_width: int,
    cell_height: int,
) -> list[tuple[TilePlacement, tuple[Group, ...]]]:
    """One ``(placement, declared groups)`` pair per placement, in
    placement order -- :func:`splitsmith.overlay_html.grid_html`'s own
    input shape.

    A filler tile's groups are never computed: ``grid_html`` already
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
        groups = _cell_groups(
            tile, placement.label, scale=scale, cell_width=cell_width, cell_height=cell_height
        )
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
    """Compose the geometry-sized RGB stage summary still. Since #691 the
    production caller passes the composed grid size here, not the render
    canvas's.

    Each present tile's blurred, dimmed freeze frame is pasted into its
    cell first (never a crash: a cell with no freeze frame, extraction
    failed or the tile has no trim, is simply the canvas's own black
    background). Every shooter's summary text is then composed in one
    pass: the whole canvas's declared cells (see :func:`_summary_cells`)
    become one HTML document (``overlay_html.grid_html``), rasterized
    to one geometry-sized PNG by the injected ``rasterizer``, and
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
        scale = _summary_scale(geometry.cell_height)
        cells = _summary_cells(
            placements,
            data,
            scale=scale,
            cell_width=geometry.cell_width,
            cell_height=geometry.cell_height,
        )
        html = grid_html(cells, geometry=geometry, scale=scale, theme=theme)
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
