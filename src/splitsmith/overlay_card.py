"""Generated cards for rendered output (issue #973).

A match title page, a closing card, a stage slate and a stage
lower-third are all one operation: declare what the card says as
``overlay_layout`` groups (:func:`card_groups`), hand that declaration
with the Look's palette and the engine stylesheet to the Look's template
for the card's slot (:func:`card_context`, the contract in
:mod:`splitsmith.look_template`), rasterize it in headless Chromium
through an injected :class:`~splitsmith.overlay_raster.Rasterizer`, and
composite the result over a backdrop. The shipped template draws the
engine's own markup, so the default Look renders what ``overlay_html``
renders; a user Look may draw the same data any way it likes. Nothing here
launches a browser, shells out to ffmpeg
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
from .look_template import TemplateContext, engine_block, group_json, shared_url, theme_tokens
from .looks import CardSlot, Look, template_for
from .overlay_html import single_css
from .overlay_layout import Anchor, CellScale, Element, Emphasis, Flow, Group, Role
from .overlay_raster import Rasterizer
from .overlay_still import DEFAULT_DIM, backdrop_from_frame
from .overlay_theme import OverlayTheme, theme_for

logger = logging.getLogger(__name__)

Card = TitleCard | MatchTitle

#: How long a lower-third fades out for, at the end of its window. Shared
#: by both MP4 renderers so the two products fade alike.
LOWER_THIRD_FADE_SECONDS = 0.5


def lower_third_filters(input_index: int, seconds: float, *, source_label: str) -> tuple[list[str], str]:
    """The two ``-filter_complex`` chains that composite a lower-third over
    ``source_label`` and the label they end on: the PNG made ``rgba`` and
    faded out over its last :data:`LOWER_THIRD_FADE_SECONDS`, then an
    ``overlay`` disabled once ``seconds`` have passed. One spelling for
    both renderers, so their fades cannot drift apart."""
    fade_start = max(0.0, seconds - LOWER_THIRD_FADE_SECONDS)
    return [
        f"[{input_index}:v]format=rgba,fade=t=out:st={fade_start:g}:d={LOWER_THIRD_FADE_SECONDS:g}:alpha=1[lt]",
        f"[{source_label}][lt]overlay=0:0:enable='lt(t,{seconds:g})'[withlt]",
    ], "withlt"


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


def card_context(
    card: Card, *, slot: CardSlot, width: int, height: int, fps: float, theme: OverlayTheme
) -> TemplateContext:
    """What the template for ``slot`` receives: the card as data
    (``data.card``), the engine's default declaration of it
    (``data.groups``, from :func:`card_groups`), the palette, the canvas,
    and the engine block a shipped template draws with."""
    scale = card_scale(height)
    return TemplateContext(
        theme=theme_tokens(theme),
        data={
            "card": {
                "slot": slot,
                "variant": card.variant,
                "text": card.text,
                "info": list(card.info),
                "duration_seconds": card.duration_seconds,
            },
            "groups": [group_json(g) for g in card_groups(card)],
        },
        size={"width": width, "height": height},
        fps=fps,
        engine=engine_block(css=single_css(width=width, height=height, scale=scale, theme=theme)),
        assets={"shared": shared_url()},
    )


def _rasterize(
    card: Card, *, slot: CardSlot, width: int, height: int, fps: float, look: Look, rasterizer: Rasterizer
) -> Image.Image | None:
    theme = theme_for(look)
    template = template_for(look, slot, card.variant)
    context = card_context(card, slot=slot, width=width, height=height, fps=fps, theme=theme)
    try:
        png_bytes = rasterizer.render_template(template, context=context, width=width, height=height)
        with Image.open(io.BytesIO(png_bytes)) as rendered:
            return rendered.convert("RGBA")
    except Exception as exc:  # noqa: BLE001 -- one bad rasterization must not lose the render
        logger.warning(
            "could not rasterize the card %r through %s (%s); it is skipped", card.text, template, exc
        )
        return None


def build_card_still(
    card: Card,
    *,
    slot: CardSlot,
    width: int,
    height: int,
    fps: float,
    look: Look,
    rasterizer: Rasterizer,
    backdrop: Path | None,
    blur_radius: int | None = None,
    dim: float = DEFAULT_DIM,
) -> Image.Image | None:
    """Compose a full-frame card as a ``width x height`` RGB image.

    ``slot`` names the Look template that draws it (a :class:`MatchTitle`
    is ``title_page`` or ``closing``; a slate :class:`TitleCard` is
    ``slate``). ``backdrop`` is a frame on disk -- the first visible frame
    of the stage the card precedes -- blurred and dimmed with the stage
    summary's own numbers (:mod:`splitsmith.overlay_still`). ``None``, or
    a frame that cannot be read, paints the Look's ``surface`` colour
    instead, so a failed frame grab costs the picture but never the card.

    Returns ``None`` when the text could not be rasterized; see the
    module docstring for why that skips the card rather than degrading
    it.
    """
    text = _rasterize(card, slot=slot, width=width, height=height, fps=fps, look=look, rasterizer=rasterizer)
    if text is None:
        return None
    canvas: Image.Image | None = None
    if backdrop is not None:
        canvas = backdrop_from_frame(backdrop, width=width, height=height, radius=blur_radius, dim_amount=dim)
    if canvas is None:
        canvas = Image.new("RGB", (width, height), theme_for(look).surface)
    composed = canvas.convert("RGBA")
    composed.alpha_composite(text)
    return composed.convert("RGB")


def build_lower_third(
    card: TitleCard, *, width: int, height: int, fps: float, look: Look, rasterizer: Rasterizer
) -> Image.Image | None:
    """Rasterize a lower-third as a transparent ``width x height`` RGBA
    image, for the renderer to composite over the stage's own head with
    a fade. No backdrop: the footage is the backdrop."""
    return _rasterize(
        card, slot="lower_third", width=width, height=height, fps=fps, look=look, rasterizer=rasterizer
    )


__all__ = [
    "LOWER_THIRD_FADE_SECONDS",
    "Card",
    "build_card_still",
    "build_lower_third",
    "card_context",
    "card_groups",
    "card_scale",
    "lower_third_filters",
]
