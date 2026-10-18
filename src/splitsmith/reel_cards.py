"""The highlight reel's cards through the Look's ``highlight`` slot.

Loaded like a sting (``look_sting``): the card as ``data.highlight``, the
palette, the canvas, the engine stylesheet. Frames render lazily, so a
cached segment pulls none; ``digest`` is what the segment cache keys the
clip by. ``None`` when the Look has no such variant or it fails to load:
the renderer then holds the card's backdrop and notes it.
"""

from __future__ import annotations

import logging

from .look_template import TemplateContext, engine_block, shared_url, template_digest, theme_tokens
from .looks import Look, highlight_template_for
from .overlay_card import CardMotion, card_scale
from .overlay_html import single_css
from .overlay_raster import Rasterizer, TemplateFrames
from .overlay_theme import OverlayTheme, theme_for
from .reel_plan import ReelCard

logger = logging.getLogger(__name__)


def reel_card_context(
    card: ReelCard, *, width: int, height: int, fps: float, theme: OverlayTheme
) -> TemplateContext:
    return TemplateContext(
        theme=theme_tokens(theme),
        data={
            "highlight": {
                "kind": card.kind,
                "text": card.text,
                "figure": card.figure,
                "unit": card.unit,
                "lines": list(card.lines),
                "duration_seconds": card.seconds,
            }
        },
        size={"width": width, "height": height},
        fps=fps,
        engine=engine_block(css=single_css(width=width, height=height, scale=card_scale(height), theme=theme)),
        assets={"shared": shared_url()},
    )


def reel_card_motion(
    look: Look,
    card: ReelCard,
    *,
    width: int,
    height: int,
    fps: float,
    rasterizer: Rasterizer,
    variant: str = "trailer",
) -> CardMotion | None:
    template = highlight_template_for(look, variant)
    if template is None:
        logger.warning("Look %s has no highlight %r", look.name, variant)
        return None
    context = reel_card_context(card, width=width, height=height, fps=fps, theme=theme_for(look))
    frames: TemplateFrames | None = None
    try:
        frames = rasterizer.render_template_frames(
            template, context=context, width=width, height=height, fps=fps, max_seconds=card.seconds
        )
        digest = template_digest(template, context, fps=fps, engine_version=rasterizer.engine_version())
    except Exception as exc:  # noqa: BLE001 -- one bad template must not lose the reel
        if frames is not None:
            frames.close()
        logger.warning("could not load the highlight card %s through %s (%s)", card.name, template, exc)
        return None
    return CardMotion(template=template, context=context, frames=frames, digest=digest)
