"""Where overlay elements sit, what they are, and how big they get.

Both overlay renderers draw the same kinds of thing in the same cells:
the compare grid's live sprite (PIL, stepped on shot events), its running
clock (an ffmpeg ``drawtext`` filter, genuinely per frame) and its frozen
stage summary (PIL, once per stage). Until this module existed, the live
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

It is not a plugin system. Five roles, six anchors, one product.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

#: Font sizes never shrink below this. Matches
#: ``overlay_sprites._MIN_FONT_SIZE`` and
#: ``overlay_summary._MIN_FONT_SIZE``: below it a further shrink reads as
#: noise rather than as smaller text.
MIN_FONT_SIZE = 12


class Anchor(Enum):
    """Which corner or edge-centre of a cell an element group sits in.

    Six rather than nine, not all live yet. Five are drawn today: the
    live sprite's counter sits at ``TOP_LEFT`` and its last split at
    ``BOTTOM_CENTER``, the clock draws at ``TOP_RIGHT``, and the frozen
    stage summary (``overlay_summary._draw_cell``) composes a shooter's
    identity and placing at ``TOP_LEFT``, split statistics at
    ``TOP_RIGHT`` (the clock's own corner -- the two never draw at the
    same time, since the clock belongs to the action and the summary to
    the hold after it), and its TIME/HF/STAGE band plus the
    accuracy/faults row stacked above it at ``BOTTOM_LEFT``.
    ``BOTTOM_RIGHT`` is declared and not yet drawn by anything. Adding a
    middle row would mean inventing a vertical-centring rule nothing has
    asked for.
    """

    TOP_LEFT = "top-left"
    TOP_CENTER = "top-center"
    TOP_RIGHT = "top-right"
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
        return self in (Anchor.TOP_CENTER, Anchor.BOTTOM_CENTER)


class Flow(Enum):
    """How the elements of one group run.

    ``COLUMN`` stacks them away from the anchored edge; ``ROW`` runs them
    along it.
    """

    COLUMN = "column"
    ROW = "row"


class Role(Enum):
    """What an element is. Not how big it is -- :class:`CellScale` decides
    that, so a role can be reasoned about without knowing a cell size."""

    #: The shooter's name.
    IDENTITY = "identity"
    #: A figure the viewer should read first -- stage time, hit factor.
    HEADLINE = "headline"
    #: A cross-shooter or disqualifying fact: placing, DQ, penalties.
    VERDICT = "verdict"
    #: Supporting figures -- split statistics, hit counts, shot count.
    #: Since issue #683 Task 7 this is also eligible for
    #: :attr:`Emphasis.PLATE`: the stage summary's individual fault counts
    #: (misses/no-shoots/procedurals) plate when genuinely nonzero, at the
    #: same size as the rest of the equal-weight hit/fault row --
    #: :class:`Emphasis` decides eligibility for ``PLATE`` now, not
    #: ``Role``, because a cross-shooter fact and a same-size fault count
    #: both need it at different type sizes.
    DETAIL = "detail"
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


@dataclass(frozen=True)
class Group:
    """Elements sharing one anchor, laid out together.

    Several groups may share an anchor. They stack away from that
    anchor's edge in declaration order -- the first declared sits closest
    to the edge. Groups do not nest; sharing an anchor is what that would
    otherwise have been for.
    """

    anchor: Anchor
    flow: Flow
    elements: tuple[Element, ...]


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
    caption: int
    live_primary: int
    pad: int

    @classmethod
    def for_cell(cls, cell_height: int) -> CellScale:
        """Resolve the scale for a cell of ``cell_height`` pixels.

        ``live_primary`` and ``pad`` reproduce exactly what the live
        overlay computed before this module existed. They are pinned by
        ``test_live_primary_and_pad_match_what_the_grid_computes_today``
        and are not free to drift: the sprite and the clock have to land
        on the same cell geometry or the two halves of the overlay stop
        lining up.
        """
        floor = MIN_FONT_SIZE
        return cls(
            identity=max(30, cell_height // 17),
            headline=max(30, cell_height // 14),
            verdict=max(24, cell_height // 17),
            detail=max(14, cell_height // 40),
            caption=max(floor, cell_height // 44),
            # Pinned to today's live overlay. Do not "tidy" toward
            # ``headline`` -- see the class docstring.
            live_primary=max(48, cell_height // 14),
            pad=max(24, cell_height // 36),
        )

    def size_for(self, role: Role) -> int:
        """The font size for ``role``.

        ``caption`` has no matching role and is read directly off the
        dataclass -- a caption is never an element on its own.
        """
        return {
            Role.IDENTITY: self.identity,
            Role.HEADLINE: self.headline,
            Role.VERDICT: self.verdict,
            Role.DETAIL: self.detail,
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
    inset edge -- there is nothing to inset from.
    """
    if anchor.is_center:
        x = cell_x + cell_w // 2
    elif anchor.is_right:
        x = cell_x + cell_w - pad
    else:
        x = cell_x + pad
    y = cell_y + cell_h - pad if anchor.is_bottom else cell_y + pad
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
