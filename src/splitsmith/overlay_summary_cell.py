"""What one stage summary says, and how it composes -- the pure half.

Hoisted out of ``compare/overlay_summary.py`` (issue #972) so the
single-shooter export's rendered MP4 can end each stage on the same
summary the compare grid holds on, without core code importing from
``compare/``. The grid module rebinds these under its old private names,
so its tests and its behaviour are untouched.

The declaration is issue #683 Task 8's approved design
(``scripts/mock_summary_cell.py``): the shooter's name (with a DQ chip
when DQ'd), then two equal-weight bands -- **Scoring** and **Splits**.
See :func:`summary_groups`. Nothing here measures text or opens a font;
:mod:`splitsmith.overlay_html` turns the groups into a document and an
injected :class:`~splitsmith.overlay_raster.Rasterizer` into pixels.

Never draws a number that is absent. ``scorecard`` is ``None`` for
placeholder stages and pre-scorecard projects; a manually-timed stage
carries ``stage_time_is_manual`` with no scorecard; a stage may have no
audit at all. Each of those renders less, never a zero and never a guess.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NamedTuple

from PIL import Image

from .coach import statistic_split_shots
from .events import ReloadFigure, exposed_reload_s
from .match_project import StageScorecard
from .overlay_html import single_html
from .overlay_layout import MIN_FONT_SIZE, Anchor, CellScale, ColorToken, Element, Emphasis, Flow, Group, Role
from .overlay_raster import Rasterizer
from .overlay_still import DEFAULT_DIM, backdrop_from_frame
from .overlay_theme import OverlayTheme
from .safe_area import SafeArea, safe_area
from .stage_summary_data import TileStageData

logger = logging.getLogger(__name__)


def summary_scale(cell_height: int) -> CellScale:
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


def count_elements(scorecard: StageScorecard) -> list[Element]:
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


def time_text_for(tile: TileStageData) -> str | None:
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


#: The Splits band's column count once it runs to more than one row: a
#: Static / Moving row is a label and three figures, the last row the Draw
#: and up to three reload figures.
_SPLIT_COLUMNS = 4

#: The shortest cell that gets separate Static and Moving rows. The two
#: rows and the Draw row below them need the band at about 0.58 of its
#: full size, and the fit policy's legibility floor (``MIN_FONT_SIZE`` over
#: the caption size, ``cell_h / 20``) only allows that from about 420 px;
#: below it the fit drops the Scoring band's figures to make room, which
#: breaks the two bands' equal weight. Measured with real Chromium on a
#: full scorecard (2026-10-09); 480 leaves a margin. Every landscape
#: single-shooter canvas from 540p up qualifies. (Grid cells never split:
#: the grid hold passes ``split_rows=False``.)
_SPLIT_ROWS_MIN_CELL_HEIGHT = 480


def _fits_split_rows(cell_width: int, cell_height: int) -> bool:
    """Whether a single-shooter card has room for Static and Moving rows:
    tall enough (:data:`_SPLIT_ROWS_MIN_CELL_HEIGHT`) and at least as wide
    as tall. A portrait card's four columns are already too narrow for a
    figure each (the plain row clips there too); with the Draw beside the
    reload count a clipped ``1.1`` next to ``1`` reads as ``1.11``, a
    plausible wrong figure, so a narrow card keeps one row and the reload
    row on its own line."""
    return cell_height >= _SPLIT_ROWS_MIN_CELL_HEIGHT and cell_width >= cell_height


def _split_stat_elements(splits: list[float]) -> list[Element]:
    """Best / Avg / Worst over ``splits``; nothing when there are none."""
    if not splits:
        return []
    return [
        Element(role=Role.HEADLINE, text=f"{min(splits):.2f}", caption="Best"),
        Element(role=Role.HEADLINE, text=f"{sum(splits) / len(splits):.2f}", caption="Avg"),
        Element(role=Role.HEADLINE, text=f"{max(splits):.2f}", caption="Worst"),
    ]


def _unlit_fault_count(counts: list[Element]) -> int:
    """How many of the counts row's elements sit in the unlit-fault tier
    (:data:`_TIER_UNLIT_FAULT`): a zero fault, which drops first."""
    return sum(1 for e in counts if e.color is ColorToken.ACCENT_TEXT and e.emphasis is not Emphasis.PLATE)


def _reload_elements(reloads: tuple[ReloadFigure, ...], *, first_priority: int) -> list[Element]:
    """Reloads / Reload avg / Exposed for the stage's confirmed reloads.

    Exposed is the reloads' time no confirmed movement covered, summed and
    unsigned (``1.42``), the Coach page's figure (``events.exposed_reload_s``):
    a standing reload is exposed for its whole duration, one hidden inside a
    movement for none. Drawn whenever there is a confirmed reload; nothing at
    all without one.

    Drop priorities run from ``first_priority`` right to left, so a cell
    that must give up part of the row loses the exposed time first and the
    count last."""
    if not reloads:
        return []
    avg = sum(r.duration for r in reloads) / len(reloads)
    declared = [
        (str(len(reloads)), "Reloads"),
        (f"{avg:.2f}", "Reload avg"),
        (f"{exposed_reload_s(reloads):.2f}", "Exposed"),
    ]
    last = first_priority + len(declared) - 1
    return [
        Element(role=Role.HEADLINE, text=text, caption=caption, drop_priority=last - index)
        for index, (text, caption) in enumerate(declared)
    ]


def summary_groups(
    tile: TileStageData | None,
    label: str,
    *,
    scale: CellScale,
    cell_width: int,
    cell_height: int,
    split_rows: bool = True,
    upright: bool = False,
    grid: UprightGridType | None = None,
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

    Confirmed stage events (spec 2026-10-08, part 2) add to the Splits
    band only: Static and Moving rows in place of the one Best/Avg/Worst
    row when both kinds of split exist, ``split_rows`` is on and the cell
    is tall and wide enough (:func:`_fits_split_rows`), and a
    Reloads / Reload avg / Exposed row when the stage has a confirmed
    reload. A stage without confirmed regions declares exactly the groups
    it did before (pinned in ``tests/test_overlay_summary_cell.py``).

    ``split_rows=False`` is the compare grid's hold: its cells sit side by
    side to be compared, so every one keeps the combined Best/Avg/Worst
    row (a static-only figure beside a neighbour's combined one is not a
    comparison) and is not shrunk by two extra rows its neighbours lack.
    The reload row still appears; it is that shooter's own fact.

    ``upright=True`` is the single-shooter card on a canvas taller than
    wide (issue #1394): the Splits band comes first and is stacked
    (:func:`_upright_split_groups`; Static / Moving rows whenever both
    kinds of split exist and ``split_rows`` is on, whatever the aspect),
    and hit factor and time share its two columns under the counts.

    ``grid`` (with ``upright=True``) is one tile of an upright compare
    grid's hold (issue #1394 part 2), typed for the whole grid by
    :func:`upright_grid_type`: the same order, but Best / Avg over Worst /
    Draw as a 2x2, the reload row as a row of its own
    (:attr:`UprightGridType.reload_columns` wide), the counts wrapping to
    three columns, and hit factor and time one size down
    (:attr:`~splitsmith.overlay_layout.Role.DETAIL`, the counts' size), over
    each other when the grid's narrowest tile has no room for both on a row
    (:attr:`UprightGridType.stack_figures`).
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
    # groups sharing MIDDLE_CENTER stack in declaration order: Scoring then
    # Splits, Splits first on an upright card.

    counts = count_elements(active_scorecard) if active_scorecard is not None else []
    # The reload row (confirmed reloads only) is the first thing a cell too
    # small for everything gives up after the unlit faults: it is neither a
    # split nor a scoring input. Its figures take the drop priorities right
    # after the unlit faults' tier and every later count moves up past
    # them. Without a reload nothing is renumbered.
    reload_row = _reload_elements(tile.reloads, first_priority=_unlit_fault_count(counts))
    if reload_row:
        counts = [
            (
                replace(e, drop_priority=e.drop_priority + len(reload_row))
                if e.drop_priority is not None and e.drop_priority >= _unlit_fault_count(counts)
                else e
            )
            for e in counts
        ]
    time_text = time_text_for(tile)
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

    scoring: list[Group] = []
    if scoring_present:
        # Drop-priority continues past the counts row's own tiers (issue
        # #683 F1): the whole counts row is exhausted before the fit
        # policy ever reaches hit factor/time, and the "Scoring" label
        # itself is the last thing this module will ever offer to drop
        # on the Scoring side -- see ``overlay_html._fit_script``. Never
        # assigned to a split figure; F1's rule 2 is "never the splits".
        # The reload row is the one droppable thing in the Splits band
        # (see ``reload_row`` above).
        next_priority = len(counts) + len(reload_row)
        working: list[Element] = []
        if hf_text is not None:
            working.append(Element(role=Role.HEADLINE, text=hf_text, unit="HF", drop_priority=next_priority))
            next_priority += 1
        if time_text is not None:
            working.append(Element(role=Role.HEADLINE, text=time_text, drop_priority=next_priority))
            next_priority += 1
        scoring.append(
            Group(
                anchor=Anchor.MIDDLE_CENTER,
                flow=Flow.ROW,
                elements=(Element(role=Role.LABEL, text="Scoring", drop_priority=next_priority),),
                align="left",
            )
        )
        if counts and upright and grid is not None:
            scoring.append(
                Group(
                    anchor=Anchor.MIDDLE_CENTER,
                    flow=Flow.GRID,
                    elements=tuple(counts),
                    align="left",
                    gap=_counts_gap(scale),
                    columns=_UPRIGHT_COUNT_COLUMNS,
                )
            )
        elif counts:
            scoring.append(
                Group(
                    anchor=Anchor.MIDDLE_CENTER,
                    flow=Flow.ROW,
                    elements=tuple(counts),
                    align="left",
                    gap=_counts_gap(scale),
                )
            )
        if working and upright and grid is not None:
            working = [replace(e, role=Role.DETAIL) for e in working]
        if working and upright:
            # Upright: hit factor and time sit in the Splits band's two
            # columns, under the 2x2 of figures, so the eye reads one table.
            scoring.append(
                Group(
                    anchor=Anchor.MIDDLE_CENTER,
                    flow=Flow.GRID,
                    elements=tuple(working),
                    align="left",
                    gap=_sgrid_gap(cell_width),
                    columns=1 if grid is not None and grid.stack_figures else _UPRIGHT_COLUMNS,
                )
            )
        elif working:
            scoring.append(
                Group(
                    anchor=Anchor.MIDDLE_CENTER,
                    flow=Flow.ROW,
                    elements=tuple(working),
                    align="left",
                    gap=_figrow_gap(cell_width),
                )
            )

    # Splits: Best/Avg/Worst/Draw, only what can actually be computed --
    # "Best"/"Avg"/"Worst" need at least one split-classed interval
    # (transitions, movement and reloads are the run's dead time, not its
    # shooting - issue #772; ``statistic_split_shots`` owns the rule and the
    # unclassified fallback); "Draw" needs only the draw itself. Never
    # invented, per the module's own rule.
    #
    # Stage events (spec 2026-10-08, part 2): when the selection holds both
    # static and moving splits (``TileShot.moving``, confirmed movement
    # regions only) and the cell is tall enough
    # (:func:`_fits_split_rows`) the Best/Avg/Worst row becomes a
    # Static and a Moving row, and the Draw moves down to the last row;
    # confirmed reloads add Reloads / Reload avg / Exposed to that last row
    # (their own row under Best/Avg/Worst/Draw otherwise). Every row then
    # shares four columns so the figures line up. A stage with neither
    # declares exactly what it did before: one grid, one column per element.
    splits: list[Group] = []
    if upright:
        splits = _upright_split_groups(
            tile,
            reload_row,
            scale=scale,
            cell_width=cell_width,
            cell_height=cell_height,
            split_rows=split_rows,
            grid=grid,
        )
    else:
        rows: list[list[Element]] = []
        if tile.has_shots:
            selected = statistic_split_shots(tile.shots)
            static = [shot.split for shot in selected if not shot.moving]
            moving = [shot.split for shot in selected if shot.moving]
            draw = Element(role=Role.HEADLINE, text=f"{tile.shots[0].split:.2f}", caption="Draw")
            if static and moving and split_rows and _fits_split_rows(cell_width, cell_height):
                rows.append([Element(role=Role.LABEL, text="Static"), *_split_stat_elements(static)])
                # No captions: the Moving figures sit under the Static row's.
                rows.append(
                    [
                        Element(role=Role.LABEL, text="Moving"),
                        *(replace(e, caption=None) for e in _split_stat_elements(moving)),
                    ]
                )
                rows.append([draw, *reload_row])
            else:
                rows.append([*_split_stat_elements([shot.split for shot in selected]), draw])
                if reload_row:
                    rows.append(reload_row)
        columns = _SPLIT_COLUMNS if len(rows) > 1 else None
        if rows:
            splits.append(
                Group(
                    anchor=Anchor.MIDDLE_CENTER,
                    flow=Flow.ROW,
                    elements=(Element(role=Role.LABEL, text="Splits"),),
                    align="left",
                    margin_top=_band_gap_extra(cell_height) if scoring_present else None,
                )
            )
            for row in rows:
                splits.append(
                    Group(
                        anchor=Anchor.MIDDLE_CENTER,
                        flow=Flow.GRID,
                        elements=tuple(row),
                        align="left",
                        gap=_sgrid_gap(cell_width),
                        columns=columns,
                    )
                )

    if upright:
        # Splits lead on an upright card; the Scoring band follows a band
        # gap below them.
        if splits and scoring:
            scoring[0] = replace(scoring[0], margin_top=_band_gap_extra(cell_height))
        groups.extend(splits + scoring)
    else:
        groups.extend(scoring + splits)
    return tuple(groups)


#: The upright stage summary's column count for its 2x2 rows (Best / Avg
#: over Worst / Draw; Draw / Reloads over Reload avg / Exposed) and its hit
#: factor and time; the Static and Moving rows take three.
_UPRIGHT_COLUMNS = 2
_UPRIGHT_STAT_COLUMNS = 3
#: An upright grid tile's hit/fault counts: A C D over M NS P.
_UPRIGHT_COUNT_COLUMNS = 3


def _upright_split_groups(
    tile: TileStageData,
    reload_row: list[Element],
    *,
    scale: CellScale,
    cell_width: int,
    cell_height: int,
    split_rows: bool,
    grid: UprightGridType | None = None,
) -> list[Group]:
    """The upright card's Splits band (issue #1394): stacked, never four
    across. Without confirmed regions Best / Avg over Worst / Draw as a 2x2;
    with both static and moving splits a Static and a Moving row of three
    (Moving without captions, as landscape), then Draw / Reloads / Reload
    avg / Exposed as a 2x2; with a confirmed reload and no such split
    Best / Avg / Worst as a row of three over the same 2x2. Nothing when
    the stage has no shots."""
    if not tile.has_shots:
        return []
    selected = statistic_split_shots(tile.shots)
    static = [shot.split for shot in selected if not shot.moving]
    moving = [shot.split for shot in selected if shot.moving]
    draw = Element(role=Role.HEADLINE, text=f"{tile.shots[0].split:.2f}", caption="Draw")
    gap = _sgrid_gap(cell_width)

    def label(text: str) -> Group:
        return Group(
            anchor=Anchor.MIDDLE_CENTER,
            flow=Flow.ROW,
            elements=(Element(role=Role.LABEL, text=text),),
            align="left",
        )

    def grid_of(elements: list[Element], columns: int) -> Group:
        return Group(
            anchor=Anchor.MIDDLE_CENTER,
            flow=Flow.GRID,
            elements=tuple(elements),
            align="left",
            gap=gap,
            columns=columns,
        )

    groups = [label("Splits")]
    if grid is not None:
        # An upright grid tile: the combined 2x2 whatever the stage marked
        # up (the hold keeps the combined row, ``split_rows=False``).
        groups.append(
            grid_of([*_split_stat_elements([shot.split for shot in selected]), draw], _UPRIGHT_COLUMNS)
        )
        if reload_row:
            groups.append(grid_of(reload_row, grid.reload_columns))
        return groups
    if static and moving and split_rows and cell_height >= _SPLIT_ROWS_MIN_CELL_HEIGHT:
        groups.append(label("Static"))
        groups.append(grid_of(_split_stat_elements(static), _UPRIGHT_STAT_COLUMNS))
        groups.append(label("Moving"))
        groups.append(
            grid_of([replace(e, caption=None) for e in _split_stat_elements(moving)], _UPRIGHT_STAT_COLUMNS)
        )
        groups.append(grid_of([draw, *reload_row], _UPRIGHT_COLUMNS))
    elif reload_row:
        stats = _split_stat_elements([shot.split for shot in selected])
        if stats:
            groups.append(grid_of(stats, _UPRIGHT_STAT_COLUMNS))
        groups.append(grid_of([draw, *reload_row], _UPRIGHT_COLUMNS))
    else:
        groups.append(
            grid_of([*_split_stat_elements([shot.split for shot in selected]), draw], _UPRIGHT_COLUMNS)
        )
    return groups


def upright_summary_scale(width: int, height: int, *, dense: bool) -> CellScale:
    """The :class:`CellScale` of an upright stage summary (issue #1394),
    keyed to the canvas width, which is what limits a row of figures on a
    tall card: the name at 12 % of the width (130 px at 1080x1920, never
    more than the landscape rule's ``height / 7``), every figure at 13.5 %
    (about 1.2x the 1920x1080 card's) or, ``dense`` (Static / Moving rows
    or a reload row to fit), 10.5 %, the counts at 6.6 % and the captions
    at 3.2 %. The live-overlay fields stay :meth:`CellScale.for_cell`'s."""
    base = CellScale.for_cell(height)
    identity = max(MIN_FONT_SIZE, min(round(width * 0.12), height // 7))
    return replace(
        base,
        identity=identity,
        headline=max(MIN_FONT_SIZE, round(width * (0.105 if dense else 0.135))),
        verdict=max(MIN_FONT_SIZE, identity // 2),
        detail=max(MIN_FONT_SIZE, round(width * 0.066)),
        caption=max(13, round(width * 0.032)),
        pad=max(16, round(width * 0.06)),
        stroke_width=max(1, width // 540),
    )


def _upright_dense(tile: TileStageData) -> bool:
    """Whether an upright card has more than one row of split figures to
    fit: a confirmed reload, or both static and moving splits."""
    if tile.reloads:
        return True
    if not tile.has_shots:
        return False
    selected = statistic_split_shots(tile.shots)
    return any(shot.moving for shot in selected) and any(not shot.moving for shot in selected)


#: An upright grid tile's sizes, in hundredths of the cell's width (issue
#: #1394 part 2, the owner's mockup): the name, the split figures, the counts
#: with hit factor and time one size down, the captions and band labels, and
#: the cell's padding.
_GRID_IDENTITY_U = 14.0
_GRID_HEADLINE_U = 13.0
_GRID_DETAIL_U = 9.0
_GRID_CAPTION_U = 4.6
_GRID_PAD_U = 5.0

#: The bundled faces' line box over their size (measured in Chromium: the
#: mono face 1.31-1.33, the display face 1.30), the mono face's advance per
#: character, and a label's with its 0.08 em of letter spacing.
_LINE_EMS = 1.33
_ADVANCE_EMS = 0.6
_LABEL_ADVANCE_EMS = 0.68
#: What ``fit.js``'s column step keeps between a column's text and the next
#: column (``COLUMN_GAP_EM``).
_COLUMN_GAP_EMS = 0.6
#: A stage time entered by hand as a hold tile draws it: ``12.34s (manual)``.
_MANUAL_TIME_CHARS = 15
#: What a lit penalty plate adds to its row (0.15 em of padding each side).
_PLATE_EMS = 0.3
#: How far under the legibility floor a tile's captions may come by the
#: width before they are dropped rather than drawn at the floor: a 9-up
#: 1080 px wide hold's 11 px captions draw at the floor, a 9-up 720 px
#: one's 8 px captions are dropped.
_CAPTION_CLAMP = 0.85


@dataclass(frozen=True)
class UprightGridType:
    """One upright grid's type (issue #1394 part 2): the scale every tile
    draws at, and what its tightest tile makes every tile give up."""

    scale: CellScale
    #: ``False`` when a caption would come out under the legibility floor
    #: (:data:`~splitsmith.overlay_layout.MIN_FONT_SIZE`): the captions are
    #: dropped instead, and the band labels sit at the floor.
    captions: bool
    #: Hit factor over time rather than beside it: the narrowest tile has
    #: no room for both on one row.
    stack_figures: bool = False
    #: How many of Reloads / Reload avg / Exposed sit on a row: all three
    #: when the narrowest tile has the room, else two.
    reload_columns: int = 2


class TileRoom(NamedTuple):
    """What one upright grid tile has for its text once the platform safe
    area is taken out of it, whether it draws a reload row, and whether its
    stage time was entered by hand (``4.50s (manual)``, too long to sit
    beside hit factor)."""

    usable_width: int
    available_height: int
    reloads: bool = False
    manual_time: bool = False


def _grid_scale(unit: float, cell_width: int, cell_height: int) -> UprightGridType:
    caption = round(unit * _GRID_CAPTION_U)
    identity = max(MIN_FONT_SIZE, round(unit * _GRID_IDENTITY_U))
    scale = replace(
        CellScale.for_cell(cell_height),
        identity=identity,
        headline=max(MIN_FONT_SIZE, round(unit * _GRID_HEADLINE_U)),
        verdict=max(MIN_FONT_SIZE, identity // 2),
        detail=max(MIN_FONT_SIZE, round(unit * _GRID_DETAIL_U)),
        caption=max(MIN_FONT_SIZE, caption),
        pad=max(8, round(unit * _GRID_PAD_U)),
        stroke_width=max(1, cell_width // 360),
    )
    return UprightGridType(scale=scale, captions=unit * _GRID_CAPTION_U >= _CAPTION_CLAMP * MIN_FONT_SIZE)


def _fits_row(widths: list[float], *, inner: float, columns: int, gap: float, em: float) -> bool:
    """Whether text ``widths`` laid ``columns`` to a row of equal columns in
    ``inner`` pixels each end ``fit.js``'s column gap short of the next."""
    column = (inner - (columns - 1) * gap) / columns
    return all(
        width + (_COLUMN_GAP_EMS * em if index % columns < columns - 1 else 0) <= column
        for index, width in enumerate(widths)
    )


def _grid_rows_fit(kind: UprightGridType, *, inner: float, cell_width: int, match: bool) -> bool:
    """Whether a tile's widest rows fit ``inner`` pixels of text width: the
    counts three to a row, then two split figures (``0.00``) side by side on
    the hold, a "Best draw" caption and its figure on the match summary."""
    scale = kind.scale
    count = 3 * _ADVANCE_EMS * scale.detail
    if not _fits_row(
        [count] * 3,
        inner=inner,
        columns=3,
        gap=_counts_gap(scale),
        em=scale.caption if kind.captions else scale.detail,
    ):
        return False
    figure = 4 * _ADVANCE_EMS * scale.headline
    if match:
        label = 9 * _LABEL_ADVANCE_EMS * scale.caption
        return label + _caption_value_gap(scale) + figure <= inner
    return _fits_row(
        [figure, figure],
        inner=inner,
        columns=2,
        gap=_sgrid_gap(cell_width),
        em=scale.caption if kind.captions else scale.headline,
    )


def _figures_fit(kind: UprightGridType, *, inner: float, cell_width: int) -> bool:
    """Whether hit factor (``12.34`` and its unit) fits beside a time
    (``12.34s``) on one row of a hold tile."""
    scale = kind.scale
    unit = max(0.45 * scale.detail, MIN_FONT_SIZE)
    hit_factor = 5 * _ADVANCE_EMS * scale.detail + (0.25 + 2 * _ADVANCE_EMS) * unit
    time = 6 * _ADVANCE_EMS * scale.detail
    return _fits_row(
        [hit_factor, time],
        inner=inner,
        columns=2,
        gap=_sgrid_gap(cell_width),
        em=scale.caption if kind.captions else scale.detail,
    )


def _caption_value_gap(scale: CellScale) -> int:
    """The gap between a match summary tile's caption and its figure, and
    between its caption-value rows."""
    return max(4, round(scale.caption * 0.8))


def _reload_row_fits(kind: UprightGridType, *, inner: float, cell_width: int) -> bool:
    """Whether Reloads / Reload avg / Exposed fit three to a row, each
    column as wide as its caption ("Reload avg") or its figure (``1.42``)."""
    scale = kind.scale
    figure = 4 * _ADVANCE_EMS * scale.headline
    caption = 10 * _LABEL_ADVANCE_EMS * scale.caption if kind.captions else 0
    return _fits_row(
        [max(figure, caption)] * 3,
        inner=inner,
        columns=3,
        gap=_sgrid_gap(cell_width),
        em=scale.caption if kind.captions else scale.headline,
    )


def _grid_tile_height(
    kind: UprightGridType, *, cell_width: int, cell_height: int, match: bool, reloads: bool = False
) -> float:
    """How tall the fullest tile's text is, top padding to the band's last
    line, in pixels: the name, then on the hold the Splits 2x2 (with its
    captions when they show) and its reload row when it has one, the counts
    as two rows of three with a lit plate, and hit factor with time (over
    each other when stacked); on the match summary three caption-value rows
    and the counts."""
    scale = kind.scale

    def line(size: int) -> float:
        return size * _LINE_EMS

    gutter = max(8, scale.pad // 2)
    counts = 2 * line(scale.detail) + _PLATE_EMS * scale.detail + _counts_gap(scale)
    if match:
        rows = 3 * line(scale.headline) + 2 * _caption_value_gap(scale)
        stack = 2 * line(scale.caption) + rows + counts + 3 * gutter
    else:
        figure = line(scale.headline) + (line(scale.caption) if kind.captions else 0)
        splits = 2 * figure + _sgrid_gap(cell_width)
        if reloads:
            reload_rows = -(-3 // kind.reload_columns)
            splits += gutter + reload_rows * figure + (reload_rows - 1) * _sgrid_gap(cell_width)
        working = (
            2 * line(scale.detail) + _sgrid_gap(cell_width) if kind.stack_figures else line(scale.detail)
        )
        stack = 2 * line(scale.caption) + splits + counts + working + 4 * gutter
    # The name's row and its padding, the gaps either side of the band, and
    # the gap before the Scoring band.
    return 2 * scale.pad + line(scale.identity) + stack + _band_gap_extra(cell_height)


def upright_grid_type(
    cell_width: int, cell_height: int, tiles: Sequence[TileRoom], *, match: bool = False
) -> UprightGridType:
    """The one type scale of an upright grid's tiles (issue #1394 part 2),
    keyed to the cell's width: the name at 14 % of it, the split figures at
    13 %, the counts, hit factor and time at 9 %, captions and band labels
    at 4.6 % and the padding at 5 %. ``tiles`` is what each shooter's tile
    has once the platform safe area is taken out of it (:class:`TileRoom`);
    the unit shrinks until every tile's text (:func:`_grid_tile_height`, a
    reload row included where the tile draws one) fits its height and the
    widest rows fit the narrowest, so every tile in the grid draws at the
    same size and no tile is sized on its own. On the hold, hit factor goes
    over time (``stack_figures``) when any tile's time was entered by hand,
    and the reload row takes two columns
    rather than three when the narrowest tile has no room for them on one
    row at that size. A caption a little under the legibility floor draws
    at the floor; one further under it is dropped (``captions`` is
    ``False``), never drawn smaller. When a tile cannot fit even with
    everything at the floor (sixteen shooters on a 720 px wide frame), the
    type is what the widths allow and the fit drops by priority in the
    tiles that are short of room."""
    rooms = list(tiles) or [TileRoom(cell_width, cell_height)]
    narrowest = min(min(cell_width, room.usable_width) for room in rooms)
    manual = not match and any(room.manual_time for room in rooms)

    def candidates() -> Iterator[UprightGridType]:
        unit = cell_width / 100
        while True:
            kind = _grid_scale(unit, cell_width, cell_height)
            if not match:
                inner = narrowest - 2 * kind.scale.pad
                kind = replace(
                    kind,
                    stack_figures=manual or not _figures_fit(kind, inner=inner, cell_width=cell_width),
                    reload_columns=3 if _reload_row_fits(kind, inner=inner, cell_width=cell_width) else 2,
                )
            yield kind
            if kind.scale.headline <= MIN_FONT_SIZE:
                return
            unit *= 0.98

    def wide_enough(kind: UprightGridType) -> bool:
        inner = narrowest - 2 * kind.scale.pad
        if manual and _MANUAL_TIME_CHARS * _ADVANCE_EMS * kind.scale.detail > inner:
            return False
        return _grid_rows_fit(kind, inner=inner, cell_width=cell_width, match=match)

    def short_enough(kind: UprightGridType) -> bool:
        return all(
            _grid_tile_height(
                kind, cell_width=cell_width, cell_height=cell_height, match=match, reloads=room.reloads
            )
            <= room.available_height
            for room in rooms
        )

    kinds = list(candidates())
    return next(
        (k for k in kinds if wide_enough(k) and short_enough(k)),
        next((k for k in kinds if wide_enough(k)), kinds[-1]),
    )


def without_captions(groups: tuple[Group, ...]) -> tuple[Group, ...]:
    """``groups`` with every figure's caption taken off: what an upright grid
    tile draws when its captions would sit under the legibility floor."""
    return tuple(
        replace(group, elements=tuple(replace(e, caption=None) for e in group.elements)) for group in groups
    )


def upright_cell_style(area: SafeArea, *, pad: int) -> str:
    """The upright stage summary's cell padding: the name starts 7 % down
    the frame, the figures end at the safe area's bottom line and short of
    its button column, given the anchors' own ``pad`` inset; plus the area
    as CSS custom properties."""
    top = max(0, round(area.height * 0.07) - pad)
    right = max(0, area.right - pad)
    bottom = max(0, area.bottom - pad)
    return f"padding:{top}px {right}px {bottom}px 0;{area.css_vars()}"


def summary_still_html(
    tile: TileStageData,
    label: str,
    *,
    width: int,
    height: int,
    theme: OverlayTheme,
    accent: str | None = None,
) -> str:
    """The document :func:`build_summary_still` rasterizes. An upright
    canvas (taller than wide) takes the upright layout (issue #1394):
    :func:`upright_summary_scale`, the Splits band first and stacked
    (``summary_groups(upright=True)``), the cell padded out of the platform
    safe area (:func:`upright_cell_style`). Square and wider canvases are
    the landscape card, unchanged."""
    area = safe_area(width, height)
    if area is not None:
        scale = upright_summary_scale(width, height, dense=_upright_dense(tile))
        groups = summary_groups(tile, label, scale=scale, cell_width=width, cell_height=height, upright=True)
        return single_html(
            groups,
            width=width,
            height=height,
            scale=scale,
            theme=theme,
            accent=accent,
            fit_columns=True,
            cell_style=upright_cell_style(area, pad=scale.pad),
        )
    scale = summary_scale(height)
    groups = summary_groups(tile, label, scale=scale, cell_width=width, cell_height=height)
    # The summary's table rows fit their own columns (fit.js
    # ``fitColumns``): a portrait card otherwise clips 1.42 to "1.4".
    return single_html(
        groups, width=width, height=height, scale=scale, theme=theme, accent=accent, fit_columns=True
    )


def build_summary_still(
    tile: TileStageData,
    label: str,
    *,
    width: int,
    height: int,
    theme: OverlayTheme,
    rasterizer: Rasterizer | None,
    backdrop: Path | None,
    blur_radius: int | None = None,
    dim: float = DEFAULT_DIM,
    accent: str | None = None,
) -> Image.Image | None:
    """One shooter's stage summary as a full-frame ``width x height`` RGB
    still (issue #972): the whole frame is one cell, composed the way the
    grid composes each of its cells.

    ``backdrop`` is the stage's last visible frame, blurred and dimmed
    (:mod:`splitsmith.overlay_still`); ``None`` or unreadable paints the
    theme's surface. ``rasterizer`` ``None`` -- no usable browser, decided
    once by the caller -- composes the still with no text, the same
    degradation the grid's hold takes: the frame is still the shooter's
    own. Returns ``None`` only when there is neither a backdrop nor text
    to hold on.
    """
    canvas: Image.Image | None = None
    if backdrop is not None:
        canvas = backdrop_from_frame(backdrop, width=width, height=height, radius=blur_radius, dim_amount=dim)
    text: Image.Image | None = None
    if rasterizer is not None:
        html = summary_still_html(tile, label, width=width, height=height, theme=theme, accent=accent)
        try:
            png_bytes = rasterizer.png(html, width=width, height=height)
            with Image.open(io.BytesIO(png_bytes)) as rendered:
                text = rendered.convert("RGBA")
        except Exception as exc:  # noqa: BLE001 -- one bad rasterization must not lose the stage
            logger.warning("could not rasterize the stage summary (%s); the still composes without text", exc)
    if canvas is None and text is None:
        return None
    if canvas is None:
        canvas = Image.new("RGB", (width, height), theme.surface)
    composed = canvas.convert("RGBA")
    if text is not None:
        composed.alpha_composite(text)
    return composed.convert("RGB")


__all__ = [
    "build_summary_still",
    "count_elements",
    "summary_groups",
    "summary_scale",
    "summary_still_html",
    "time_text_for",
    "TileRoom",
    "UprightGridType",
    "upright_cell_style",
    "upright_grid_type",
    "upright_summary_scale",
    "without_captions",
]
