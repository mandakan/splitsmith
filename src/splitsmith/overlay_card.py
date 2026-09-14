"""Generated cards for rendered output (issue #973).

A match title page, a closing card, a stage slate and a stage
lower-third are all the same operation the grid's stage summary already
performs: declare what the card says as ``overlay_layout`` groups, turn
that into one HTML document through ``overlay_html``, rasterize it in
headless Chromium through an injected
:class:`~splitsmith.overlay_raster.Rasterizer`, and composite the result
over a backdrop. Nothing here launches a browser, shells out to ffmpeg
or writes a file: :mod:`splitsmith.mp4_render` (and, later,
``compare/mp4_grid``) own the frame grab, the segment encode and the
splice. That is what lets one module serve both renderers.

**A card is its text.** The stage summary keeps its blurred freeze when
the rasterizer fails, because the freeze is still the shooter's own
frame. A slate with no text is a dimmed still of nothing in particular,
so :func:`build_card_still` returns ``None`` and the caller drops the
segment and records the degradation. A render never fails because of a
card.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Literal

from PIL import Image

from .composition import MatchTitle, TitleCard
from .overlay_html import single_html
from .overlay_layout import Anchor, CellScale, Element, Emphasis, Flow, Group, Role
from .overlay_raster import Rasterizer
from .overlay_still import DEFAULT_DIM, backdrop_from_frame
from .overlay_theme import OverlayTheme

logger = logging.getLogger(__name__)

Card = TitleCard | MatchTitle


def _is_lower_third(card: Card) -> bool:
    return isinstance(card, TitleCard) and card.style == "lower-third"


def card_groups(card: Card) -> tuple[Group, ...]:
    """What the card says, as anchored groups.

    A full-frame card (a :class:`MatchTitle`, or a ``slate``
    :class:`TitleCard`) centres its text at
    :attr:`~splitsmith.overlay_layout.Role.IDENTITY` size -- the largest
    the scale has, the same weight the summary gives the shooter's name
    -- with one :attr:`~Role.DETAIL` line per ``info`` entry stacked under
    it. A ``lower-third`` sits bottom-left, left-aligned, its text on an
    accent plate (:attr:`~splitsmith.overlay_layout.Emphasis.PLATE`) at
    :attr:`~Role.HEADLINE` size so it reads over arbitrary footage without
    a halo, and the info lines plain beneath it.

    One group per line, not one ``ROW`` group with several elements: the
    lines stack vertically, and groups sharing an anchor are exactly the
    thing ``overlay_layout`` provides for that. Groups stack *away from
    the anchor's edge* in declaration order, so a bottom-anchored
    lower-third declares its info lines first (last line nearest the
    edge) and the name last, which is what puts the name on top when
    read. A middle anchor stacks top-down, so the title page declares
    the name first. Measured, not assumed: the first cut declared the
    lead first for both and the lower-third came out upside down.
    """
    if _is_lower_third(card):
        lead = Element(role=Role.HEADLINE, text=card.text, emphasis=Emphasis.PLATE)
        lines = [_line(Anchor.BOTTOM_LEFT, "left", text) for text in reversed(card.info)]
        lines.append(Group(anchor=Anchor.BOTTOM_LEFT, flow=Flow.ROW, elements=(lead,), align="left"))
        return tuple(lines)
    lead = Element(role=Role.IDENTITY, text=card.text)
    groups = [Group(anchor=Anchor.MIDDLE_CENTER, flow=Flow.ROW, elements=(lead,), align="center")]
    groups.extend(_line(Anchor.MIDDLE_CENTER, "center", text) for text in card.info)
    return tuple(groups)


def _line(anchor: Anchor, align: Literal["left", "center"], text: str) -> Group:
    return Group(anchor=anchor, flow=Flow.ROW, elements=(Element(role=Role.DETAIL, text=text),), align=align)


def card_scale(height: int) -> CellScale:
    """The scale a card composes at: the whole frame is one cell."""
    return CellScale.for_cell(height)


def _rasterize(
    card: Card, *, width: int, height: int, theme: OverlayTheme, rasterizer: Rasterizer
) -> Image.Image | None:
    html = single_html(card_groups(card), width=width, height=height, scale=card_scale(height), theme=theme)
    try:
        png_bytes = rasterizer.png(html, width=width, height=height)
        with Image.open(io.BytesIO(png_bytes)) as rendered:
            return rendered.convert("RGBA")
    except Exception as exc:  # noqa: BLE001 -- one bad rasterization must not lose the render
        logger.warning("could not rasterize the card %r (%s); it is skipped", card.text, exc)
        return None


def build_card_still(
    card: Card,
    *,
    width: int,
    height: int,
    theme: OverlayTheme,
    rasterizer: Rasterizer,
    backdrop: Path | None,
    blur_radius: int | None = None,
    dim: float = DEFAULT_DIM,
) -> Image.Image | None:
    """Compose a full-frame card as a ``width x height`` RGB image.

    ``backdrop`` is a frame on disk -- the first visible frame of the
    stage the card precedes -- blurred and dimmed with the stage
    summary's own numbers (:mod:`splitsmith.overlay_still`). ``None``, or
    a frame that cannot be read, paints the theme's ``surface`` colour
    instead, so a failed frame grab costs the picture but never the card.

    Returns ``None`` when the text could not be rasterized; see the
    module docstring for why that skips the card rather than degrading
    it.
    """
    text = _rasterize(card, width=width, height=height, theme=theme, rasterizer=rasterizer)
    if text is None:
        return None
    canvas: Image.Image | None = None
    if backdrop is not None:
        canvas = backdrop_from_frame(backdrop, width=width, height=height, radius=blur_radius, dim_amount=dim)
    if canvas is None:
        canvas = Image.new("RGB", (width, height), theme.surface)
    composed = canvas.convert("RGBA")
    composed.alpha_composite(text)
    return composed.convert("RGB")


def build_lower_third(
    card: TitleCard,
    *,
    width: int,
    height: int,
    theme: OverlayTheme,
    rasterizer: Rasterizer,
) -> Image.Image | None:
    """Rasterize a lower-third as a transparent ``width x height`` RGBA
    image, for the renderer to composite over the stage's own head with
    a fade. No backdrop: the footage is the backdrop."""
    return _rasterize(card, width=width, height=height, theme=theme, rasterizer=rasterizer)


__all__ = ["Card", "build_card_still", "build_lower_third", "card_groups", "card_scale"]
