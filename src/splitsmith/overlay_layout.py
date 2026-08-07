"""Where overlay elements sit, what they are, and how big they get.

Both overlay renderers draw the same kinds of thing in the same cells:
the compare grid's live sprite (PIL, stepped on shot events), its running
clock (an ffmpeg ``drawtext`` filter, genuinely per frame) and its frozen
stage summary -- headless Chromium rasterizing CSS built by
``overlay_html.py`` from this module's own declarations, once per stage
(``docs/superpowers/plans/2026-08-06-overlay-composition-seam-amendment.md``;
it was PIL before that pivot). Until this module existed, the live
sprite (``overlay_sprites.render_state``) and the clock
(``mp4_grid._clock_pad`` / ``mp4_grid._stage_overlay_plan``) each wrote out
the same byte-identical ``max(48, h // 14)`` / ``max(24, h // 36)`` pair
independently -- two copies of one formula. The frozen stage summary
(``overlay_summary._draw_cell``) used to size itself with its own,
differently shaped constants (``max(20, h // 40)`` pad, ``max(32, h //
16)`` and ``max(18, h // 32)`` sizes) -- a separate formula, not a third
copy of the same one -- and its composition was a hardcoded list of
lines that every new figure had to be inserted into. It now resolves its
own sizes through this module's :class:`CellScale` like the other two
renderers do, and composes :class:`Group`/:class:`Element` declarations
instead of that hardcoded list.

This module owns two things and deliberately nothing else:

- **What an element is** -- an :class:`Anchor`, a :class:`Role`, an
  :class:`Emphasis`. Not a size and not a colour.
- **What a size is** -- :class:`CellScale`, resolved once per cell.

It is not a plugin system. Six roles, seven anchors, one product.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

#: Font sizes never shrink below this. Matches ``overlay_sprites.
#: _MIN_FONT_SIZE`` (the live sprite's own PIL fitter) and is read
#: directly -- not copied into a second constant -- by ``overlay_html``'s
#: in-cell ``--fit-scale`` policy (issue #683 F1): below it a further
#: shrink reads as noise rather than as smaller text, on both renderers.
MIN_FONT_SIZE = 12


class Anchor(Enum):
    """Which corner, edge-centre, or the true centre of a cell an element
    group sits in.

    Seven rather than nine, not all live yet. The stage summary (issue
    #683 Task 8, ``overlay_summary._cell_groups``) is the "approved
    bands" design, and uses exactly two of the seven: the shooter's name
    (with a DQ chip beside it, when DQ'd -- a status, not a placing) at
    ``TOP_CENTER``, and a vertically centred stack of two equal-weight
    bands -- Scoring, then Splits -- at ``MIDDLE_CENTER``. Earlier designs
    on this branch used a three-rail layout with a hairline rule, a hero
    stage percentage and a cross-shooter placing at ``BOTTOM_CENTER``;
    all of that was deleted, not merely resized or moved (see the issue
    #683 Task 8 report for why). The live sprite's counter sits at
    ``TOP_LEFT`` and its last split at ``BOTTOM_CENTER``, and the clock
    draws at ``TOP_RIGHT`` -- the summary and the live overlay never draw
    at the same time, since the clock and counter belong to the action
    and the summary to the hold after it, so ``BOTTOM_CENTER`` serving
    both is not a collision. ``TOP_LEFT``/``TOP_RIGHT``/``BOTTOM_LEFT``/
    ``BOTTOM_RIGHT`` stay corner anchors for the live overlay's own use;
    ``BOTTOM_CENTER`` and ``BOTTOM_RIGHT`` are declared and not currently
    drawn by anything.

    ``MIDDLE_CENTER`` is vertically centred rather than edge-pinned --
    unlike every other anchor, it has no ``pad`` inset on either side,
    since there is no edge to be inset from.
    """

    TOP_LEFT = "top-left"
    TOP_CENTER = "top-center"
    TOP_RIGHT = "top-right"
    MIDDLE_CENTER = "middle-center"
    BOTTOM_LEFT = "bottom-left"
    BOTTOM_CENTER = "bottom-center"
    BOTTOM_RIGHT = "bottom-right"

    @property
    def is_bottom(self) -> bool:
        return self in (Anchor.BOTTOM_LEFT, Anchor.BOTTOM_CENTER, Anchor.BOTTOM_RIGHT)

    @property
    def is_right(self) -> bool:
        return self in (Anchor.TOP_RIGHT, Anchor.BOTTOM_RIGHT)

    @property
    def is_center(self) -> bool:
        return self in (Anchor.TOP_CENTER, Anchor.MIDDLE_CENTER, Anchor.BOTTOM_CENTER)

    @property
    def is_middle(self) -> bool:
        """Vertically centred rather than pinned to the top or bottom
        edge. Only :attr:`MIDDLE_CENTER` today."""
        return self is Anchor.MIDDLE_CENTER


class Flow(Enum):
    """How the elements of one group run.

    ``COLUMN`` stacks them away from the anchored edge; ``ROW`` runs them
    along it. ``GRID`` is the stage summary's split-statistics band
    (issue #683 Task 8): an evenly spaced row that -- unlike ``ROW``,
    which shrink-wraps to its own content -- always stretches to the full
    width its anchor has, so ``Best`` / ``Avg`` / ``Worst`` / ``Draw``
    land at even quarters of the cell rather than clustered on the left.
    """

    COLUMN = "column"
    ROW = "row"
    GRID = "grid"


class Role(Enum):
    """What an element is. Not how big it is -- :class:`CellScale` decides
    that, so a role can be reasoned about without knowing a cell size."""

    #: The shooter's name.
    IDENTITY = "identity"
    #: A figure both bands' rows draw at (issue #683 Task 8): hit factor,
    #: stage time, and each split statistic (Best/Avg/Worst/Draw). The
    #: whole point of the redesign is that neither band -- and no figure
    #: within a band -- outranks another by size; colour and position
    #: carry meaning that a size ladder used to carry unevenly.
    HEADLINE = "headline"
    #: A cross-shooter or disqualifying fact: today, only the DQ chip
    #: beside the shooter's name. (The placing this once shared the role
    #: with is gone -- issue #683 Task 8.)
    VERDICT = "verdict"
    #: Supporting figures -- the six colour-coded hit/fault counts.
    #: Since issue #683 Task 7 this is also eligible for
    #: :attr:`Emphasis.PLATE`: the stage summary's individual fault counts
    #: (misses/no-shoots/procedurals) plate when genuinely nonzero, at the
    #: same size as the rest of the equal-weight hit/fault row --
    #: :class:`Emphasis` decides eligibility for ``PLATE`` now, not
    #: ``Role``, because a cross-shooter fact and a same-size fault count
    #: both need it at different type sizes.
    DETAIL = "detail"
    #: A small caption-weight string that stands on its own rather than
    #: labelling a value above it (issue #683 Task 8's band headers,
    #: "Scoring" / "Splits") -- the same visual weight and treatment as
    #: :attr:`CellScale.caption`, which is why ``size_for`` reads the same
    #: field for both rather than carrying a second number that could
    #: drift from it.
    LABEL = "label"
    #: The live overlay's shot counter and running clock.
    LIVE_PRIMARY = "live-primary"


class ColorToken(Enum):
    """Which theme colour an element's text paints in, when it is not the
    default ink.

    Introduced for the stage summary's hit/fault counts (issue #683 Task
    7): ``A``/``C``/``D``/``M``/``NS``/``P`` are colour-coded by the point
    value they carry in IPSC scoring -- ``A`` full points, ``C`` mid,
    ``D`` low, and ``M``/``NS``/``P`` each a flat -10 -- rather than by
    role/size the way the rest of the vocabulary works. Colour is a fact
    about the number (its worth), not a judgement about the shooter's run
    (:class:`Emphasis` already owns that). ``None`` on :attr:`Element.color`
    means "the emphasis's own ink", which is every element that existed
    before this token did.
    """

    INK = "ink"
    SPLIT = "split"
    SPLIT_GOOD = "split_good"
    ACCENT = "accent"
    #: Body-size red text (see :attr:`~splitsmith.overlay_theme.OverlayTheme.accent_text`)
    #: -- what an *unplated* fault count (a genuine zero: ``M0``, ``NS0``)
    #: draws instead of the raw, too-thin-at-small-sizes identity red.
    #: Introduced alongside :attr:`Emphasis.PLATE` reading ``accent_fill``
    #: instead of :attr:`ACCENT` for its own background (issue #683 Task
    #: 8): the plate/fill/text split the theme already carried was never
    #: wired into the renderer until now.
    ACCENT_TEXT = "accent_text"


class Emphasis(Enum):
    """How hard an element pushes.

    ``PLATE`` draws ink on a filled accent rectangle rather than accent
    ink with a stroke. This is not decoration: measured on a shipped
    frame, the accent placing drew 7.1% accent pixels against 33.9%
    stroke pixels, and the reddest pixel found was ``(201, 8, 10)``
    against a theme accent of ``(255, 45, 45)``. A stroke around thin
    glyphs is a halo that eats the glyph. A plate holds the same
    figure/ground relationship over any footage, and the footage under an
    overlay is always arbitrary.
    """

    PLAIN = "plain"
    MUTED = "muted"
    PLATE = "plate"


@dataclass(frozen=True)
class Element:
    """One drawn string, with what it is rather than how it looks."""

    role: Role
    text: str
    emphasis: Emphasis = Emphasis.PLAIN
    #: The small muted label drawn above a headline value ("TIME", "HF").
    #: A field rather than its own :class:`Role`: a caption is never an
    #: element on its own, it always belongs to the value it labels, and
    #: its size comes from :attr:`CellScale.caption`.
    caption: str | None = None
    #: Overrides the emphasis's own ink colour with a theme token. See
    #: :class:`ColorToken`. ``None`` (the default) draws exactly what it
    #: always did -- this field is additive, not a second way to spell an
    #: existing colour.
    color: ColorToken | None = None
    #: A smaller, muted suffix drawn immediately after ``text`` on the
    #: same baseline (issue #683 Task 8: ``"12.00"`` plus a smaller
    #: ``"HF"``) -- distinct from :attr:`caption`, which sits on its own
    #: line *above* the value. Most units attach directly into ``text``
    #: instead (``"4.50s"``): this field exists only for the one case the
    #: approved design draws at two type sizes on one line.
    unit: str | None = None
    #: This element's place in the cell's drop order when its group's
    #: content still does not fit after the whole group has been
    #: shrunk to the legibility floor (see :data:`MIN_FONT_SIZE` and
    #: ``overlay_html``'s ``--fit-scale`` fit policy, issue #683 F1).
    #: Lower drops first. ``None`` (the default) means "never drop this
    #: element" -- every split statistic keeps this default; only the
    #: scoring-side elements (``overlay_summary._cell_groups``) assign
    #: one, in an order that never removes a genuinely nonzero (lit)
    #: fault count while a zero-valued one still survives. The number
    #: itself carries no other meaning -- it is a total order over
    #: "least important to keep on screen", declared once by the module
    #: that already knows which counts are real, not computed from any
    #: rendered size.
    drop_priority: int | None = None


@dataclass(frozen=True)
class Group:
    """Elements sharing one anchor, laid out together.

    Several groups may share an anchor. They stack away from that
    anchor's edge in declaration order -- the first declared sits closest
    to the edge. Groups do not nest; sharing an anchor is what that would
    otherwise have been for.

    ``divider`` marks a group as a hairline rule rather than text -- a
    group with ``divider=True`` carries no elements (``flow`` is present
    only because the dataclass needs one and is ignored) and renders as a
    plain line stretched to the width of the widest group sharing its
    anchor. Introduced for issue #683 Task 7b's three-rail design (a rule
    between what a shooter did and what it was worth, borrowed from a
    printed IPSC score slip); Task 8's approved bands design that
    replaced it draws no divider anywhere -- ``overlay_summary._cell_groups``
    never sets this field, and the "SCORING"/"SPLITS" band headers do the
    job a rule used to. Kept as live infrastructure (``overlay_html``
    still renders ``.group.divider`` correctly) for a future caller that
    wants one, not as something currently drawn -- see
    ``scripts/build_overlay_theme.py``'s ``rule`` comment for the same
    situation on the theme-token side.
    """

    anchor: Anchor
    flow: Flow
    elements: tuple[Element, ...]
    divider: bool = False
    #: Overrides the anchor's own horizontal alignment for every group
    #: sharing this anchor (issue #683 Task 8). ``None`` (the default)
    #: keeps whatever the anchor itself implies -- see
    #: :attr:`Anchor.is_center` / :attr:`Anchor.is_right` -- which is what
    #: every anchor did before this field existed. The redesigned stage
    #: summary needs ``TOP_CENTER``'s and ``MIDDLE_CENTER``'s *rows*
    #: (their grid-row placement, not their centring) for its identity
    #: row and its two-band stack, but left-aligned, filling the cell's
    #: width rather than shrink-wrapped and centred -- reusing the row is
    #: the point; a fourth/fifth anchor for "the same row, left-aligned"
    #: would just be the same information spelled two ways.
    align: Literal["left", "center", "right"] | None = None
    #: Overrides this group's own internal gap -- between a ``ROW``'s
    #: elements, or a ``GRID``'s columns -- in pixels. ``None`` keeps the
    #: shared default (``CellScale``-derived). Introduced for issue #683
    #: Task 8: the stage summary's hit/fault counts, its hit-factor/time
    #: row and its split-statistics grid each want a visually distinct
    #: gap, and forcing one shared constant across all three read as
    #: either a cramped counts row or the working figures crowded
    #: together, whichever value won.
    gap: int | None = None
    #: Extra space (pixels) added *before* this group, on top of
    #: whatever gap the anchor already places between stacked groups.
    #: Issue #683 Task 8's two bands ("Scoring", "Splits") are otherwise
    #: an unbroken stack of same-weight lines -- the gap between a band's
    #: own lines and the gap *between* the two bands need to differ for
    #: them to read as two bands rather than one long list, and this is
    #: the one boundary in that stack that wants the bigger of the two.
    margin_top: int | None = None


@dataclass(frozen=True)
class CellScale:
    """Every type size in one cell, resolved from its height.

    One object rather than a formula per caller. The formulas were
    previously written out in ``overlay_sprites.render_state``,
    ``mp4_grid._clock_pad``, ``mp4_grid._stage_overlay_plan`` and
    ``overlay_summary._draw_cell`` independently, which is what the issue
    meant by "nothing owns what size is a per-tile element".

    Sizes are driven by *cell* height, never canvas height: 3x3 and 4x4
    are first-class grid kinds (``compare/layout.py`` routes 5-16 shooters
    there) and a size picked from the canvas overflows a small cell.
    """

    identity: int
    headline: int
    verdict: int
    detail: int
    #: Also :attr:`Role.LABEL`'s size -- see that role's docstring for why
    #: a caption (a label *above* a value) and a standalone band header
    #: read the same field rather than two numbers that could drift apart.
    caption: int
    live_primary: int
    pad: int
    #: Text-stroke width in pixels, uniform across every size in the cell.
    #:
    #: Flat pixels rather than an em, and that is the whole point. An
    #: em-relative stroke scales with each element's own font size, so a
    #: 90px figure gets a ~8px halo while a 26px count gets ~2px -- the
    #: large numerals come out chunky and slightly muddied while the small
    #: ones stay crisp, which is backwards. Measured against the approved
    #: mock, the em version put 15-18% of a glyph box in stroke against
    #: the mock's OWN flat-px reference implementation at 8.9% -- this
    #: (production) formula measures 8.4%, not that number: the mock used
    #: a float ``cell_h / 540``, this uses integer floor division
    #: (``cell_h // 540``), which is a deliberately different, smaller
    #: value at some heights -- e.g. at ``cell_h=720`` the mock's flat
    #: reference wants 1.3px, this gives exactly 1px. Every other length
    #: this module's formulas produce is already an integer CSS pixel
    #: value (see ``for_cell``'s other fields), so keeping ``stroke_width``
    #: an ``int`` too -- rounding a fraction of a pixel of stroke down
    #: rather than introducing the only float-valued field in this
    #: dataclass -- was the deliberate choice, not an oversight. Target
    #: still dominates either way: both numbers are well under the
    #: em-relative version's 15-18%.
    stroke_width: int

    @classmethod
    def for_cell(cls, cell_height: int) -> CellScale:
        """Resolve the scale for a cell of ``cell_height`` pixels.

        ``live_primary`` and ``pad`` reproduce exactly what the live
        overlay computed before this module existed. They are pinned by
        ``test_live_primary_and_pad_match_what_the_grid_computes_today``
        and are not free to drift: the sprite and the clock have to land
        on the same cell geometry or the two halves of the overlay stop
        lining up.

        The other five formulas are issue #683 Task 8's approved design,
        exactly (``scripts/mock_summary_cell.py``): the shooter's name at ``cell_h/7``, the
        one figure size both bands' rows draw at (hit factor, time, and
        each split statistic) at ``cell_h/8``, the six hit/fault counts at
        ``cell_h/14``, and every label (the band headers, and each split
        statistic's own caption) at ``cell_h/20`` floored at 13px. The DQ
        chip is sized off ``identity`` directly (half the name's own
        size) rather than as an independent formula, matching the mock's
        ``int(name_px * .5)`` -- nothing else in the cell is a "cross-
        shooter verdict" today, so there is no design pressure pushing it
        to a different weight than "smaller than the name, roughly
        counts-sized". Only the stage summary reads any of these five
        today (the live overlay reads only ``live_primary``/``pad``), so
        there is no other consumer's needs to balance against the mock's
        own numbers.
        """
        floor = MIN_FONT_SIZE
        identity = max(floor, cell_height // 7)
        return cls(
            identity=identity,
            headline=max(floor, cell_height // 8),
            verdict=max(floor, identity // 2),
            detail=max(floor, cell_height // 14),
            caption=max(13, cell_height // 20),
            # Pinned to today's live overlay. Do not "tidy" toward
            # ``headline`` -- see the class docstring.
            live_primary=max(48, cell_height // 14),
            pad=max(24, cell_height // 36),
            stroke_width=max(1, cell_height // 540),
        )

    def size_for(self, role: Role) -> int:
        """The font size for ``role``.

        ``Role.LABEL`` reads ``caption`` -- see that role's docstring.
        """
        return {
            Role.IDENTITY: self.identity,
            Role.HEADLINE: self.headline,
            Role.VERDICT: self.verdict,
            Role.DETAIL: self.detail,
            Role.LABEL: self.caption,
            Role.LIVE_PRIMARY: self.live_primary,
        }[role]


def anchor_origin(
    anchor: Anchor,
    *,
    cell_x: int,
    cell_y: int,
    cell_w: int,
    cell_h: int,
    pad: int,
) -> tuple[int, int]:
    """The origin corner of a group at ``anchor``, in canvas pixels.

    ``cell_x`` / ``cell_y`` are the cell's own top-left on the canvas, so
    a caller passes ``col * cell_w`` and ``row * cell_h``. The returned
    point is the corner the group grows *away* from: a bottom anchor
    returns its bottom edge and the caller stacks upward, a right anchor
    returns its right edge and the caller runs leftward. Converting that
    into a text position needs the text's measured box, which only the
    renderer has.

    Centre anchors return the cell's horizontal middle rather than an
    inset edge -- there is nothing to inset from. :attr:`Anchor.MIDDLE_CENTER`
    additionally returns the cell's *vertical* middle, uninset by ``pad``
    on that axis for the same reason.
    """
    if anchor.is_center:
        x = cell_x + cell_w // 2
    elif anchor.is_right:
        x = cell_x + cell_w - pad
    else:
        x = cell_x + pad
    if anchor.is_middle:
        y = cell_y + cell_h // 2
    elif anchor.is_bottom:
        y = cell_y + cell_h - pad
    else:
        y = cell_y + pad
    return x, y


def anchor_ffmpeg_expr(
    anchor: Anchor,
    *,
    col: int,
    row: int,
    cell_w: int,
    cell_h: int,
    pad: int,
) -> tuple[str, str]:
    """``(x, y)`` expressions for a ``drawtext`` filter at ``anchor``.

    ``drawtext`` positions text by expression, and ``tw`` / ``th`` (text
    width and height) are only known to ffmpeg at draw time -- which is
    exactly why the clock cannot share the PIL path. So a right or bottom
    anchor subtracts ``tw`` / ``th`` inside the expression rather than in
    Python.

    The ``TOP_RIGHT`` form is what ``mp4_grid._clock_filters`` built
    inline before this function existed and it is reproduced character
    for character. Both argv fingerprint tests hash whole commands, so
    any drift here fails them --
    ``test_the_clock_expression_is_character_for_character_what_it_is_today``
    exists so that a drift is a deliberate act rather than a surprise.
    """
    left = col * cell_w
    top = row * cell_h
    if anchor.is_center:
        x_expr = f"{left}+({cell_w}-tw)/2"
    elif anchor.is_right:
        x_expr = f"{left}+{cell_w}-tw-{pad}"
    else:
        x_expr = f"{left}+{pad}"
    y_expr = f"{top}+{cell_h}-th-{pad}" if anchor.is_bottom else f"{top}+{pad}"
    return x_expr, y_expr
