"""Where overlay elements sit, what they are, and how big they get.

Both overlay renderers draw the same kinds of thing in the same cells:
the compare grid's live sprite (PIL, stepped on shot events), its running
clock (an ffmpeg ``drawtext`` filter, genuinely per frame) and its frozen
stage summary (PIL, once per stage). Until this module existed each of
them computed its own type sizes from cell height, writing the same
formula out in three files, and the summary's composition was a hardcoded
list of lines that every new figure had to be inserted into.

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

    Six rather than nine: these are the positions the two renderers
    actually use. The live sprite draws its counter at ``TOP_LEFT`` and
    its last split at ``BOTTOM_CENTER``, the clock draws at ``TOP_RIGHT``,
    and the summary uses ``TOP_LEFT`` / ``TOP_RIGHT`` / ``BOTTOM_LEFT``.
    Adding a middle row would mean inventing a vertical-centring rule
    nothing has asked for.
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
    #: The only role eligible for :attr:`Emphasis.PLATE`.
    VERDICT = "verdict"
    #: Supporting figures -- split statistics, hit counts, shot count.
    DETAIL = "detail"
    #: The live overlay's shot counter and running clock.
    LIVE_PRIMARY = "live-primary"


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
