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
from dataclasses import replace
from pathlib import Path

from PIL import Image

from .coach import statistic_splits
from .match_project import StageScorecard
from .overlay_html import single_html
from .overlay_layout import Anchor, CellScale, ColorToken, Element, Emphasis, Flow, Group, Role
from .overlay_raster import Rasterizer
from .overlay_still import DEFAULT_DIM, backdrop_from_frame
from .overlay_theme import OverlayTheme
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


def summary_groups(
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

    counts = count_elements(active_scorecard) if active_scorecard is not None else []
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
    # "Best"/"Avg"/"Worst" need at least one split-classed interval
    # (transitions, movement and reloads are the run's dead time, not its
    # shooting - issue #772; ``statistic_splits`` owns the rule and the
    # unclassified fallback); "Draw" needs only the draw itself. Never
    # invented, per the module's own rule.
    splits: list[Element] = []
    if tile.has_shots:
        rest = statistic_splits(tile.shots)
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
        scale = summary_scale(height)
        groups = summary_groups(tile, label, scale=scale, cell_width=width, cell_height=height)
        html = single_html(groups, width=width, height=height, scale=scale, theme=theme)
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
    "time_text_for",
]
