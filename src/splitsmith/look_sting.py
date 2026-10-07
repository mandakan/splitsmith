"""A sting: a Look template laid over the boundary segment (issue #1245).

A transition of kind ``sting:<name>`` is the ``<name>`` variant of the
Look's ``transition`` slot, rendered as an alpha clip (the frames path the
animated cards use, :mod:`splitsmith.look_motion`) and composited over the
boundary's own crossfade, a plain ``fade`` of the same length, before the
segment's final ``format=yuv420p``. The template sees the transition
(``data.transition``: kind, name, duration, the labels either side of the
cut) and the shooters the cards see (``data.shooters``, the match logo
already folded in by ``identity.resolve_identity``), and sizes its
animation to the duration: ``duration()`` returns it, ``seek(t)`` drives
it, and the renderer samples ``ceil(duration * fps)`` frames.

Pure: nothing here shells out or writes a file. Both MP4 renderers call
:func:`sting_motion` while deciding a boundary, write the clip with
``look_motion.write_motion_clip`` and append :func:`sting_overlay_filters`
to the boundary command. A sting the Look lacks is ``None`` here and a
fade with a degradation there; a sting whose frames fail is a cut, like
any failed boundary.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from .composition import sting_name
from .look_motion import motion_overlay_filters
from .look_template import (
    TemplateContext,
    engine_block,
    shared_url,
    shooter_json,
    template_digest,
    theme_tokens,
)
from .looks import Look, sting_template_for
from .overlay_card import CardMotion, card_scale
from .overlay_html import single_css
from .overlay_raster import Rasterizer, TemplateFrames
from .overlay_theme import OverlayTheme, theme_for

logger = logging.getLogger(__name__)


def sting_context(
    *,
    kind: str,
    seconds: float,
    from_label: str,
    to_label: str,
    width: int,
    height: int,
    fps: float,
    theme: OverlayTheme,
    shooters: Sequence[Any] = (),
) -> TemplateContext:
    """What a ``transition`` slot template receives as ``window.splitsmith``:
    the transition as data, the shooters as the cards see them, the
    palette, the canvas and the engine block (its stylesheet, so a sting
    can draw in the overlay's typography)."""
    return TemplateContext(
        theme=theme_tokens(theme),
        data={
            "transition": {
                "kind": kind,
                "name": sting_name(kind),
                "duration_seconds": seconds,
                "from": from_label,
                "to": to_label,
            },
            "shooters": [shooter_json(shooter) for shooter in shooters],
        },
        size={"width": width, "height": height},
        fps=fps,
        engine=engine_block(
            css=single_css(width=width, height=height, scale=card_scale(height), theme=theme)
        ),
        assets={"shared": shared_url()},
    )


def sting_motion(
    look: Look,
    kind: str,
    *,
    seconds: float,
    from_label: str,
    to_label: str,
    width: int,
    height: int,
    fps: float,
    rasterizer: Rasterizer,
    shooters: Sequence[Any] = (),
) -> CardMotion | None:
    """Load the sting ``kind`` names and read how it renders over
    ``seconds``: frames are rendered lazily (a cached boundary pulls
    none), ``digest`` is what the segment cache keys the clip by.
    ``None`` when the Look (and the shipped default) has no such sting,
    or the template fails to load; the caller falls back to a fade."""
    template = sting_template_for(look, sting_name(kind))
    if template is None:
        logger.warning("Look %s has no sting %r; the transition is a fade", look.name, sting_name(kind))
        return None
    context = sting_context(
        kind=kind,
        seconds=seconds,
        from_label=from_label,
        to_label=to_label,
        width=width,
        height=height,
        fps=fps,
        theme=theme_for(look),
        shooters=shooters,
    )
    frames: TemplateFrames | None = None
    try:
        frames = rasterizer.render_template_frames(
            template, context=context, width=width, height=height, fps=fps, max_seconds=seconds
        )
        digest = template_digest(template, context, fps=fps, engine_version=rasterizer.engine_version())
    except Exception as exc:  # noqa: BLE001 -- one bad template must not lose the render
        if frames is not None:
            frames.close()
        logger.warning(
            "could not load the sting %r through %s (%s); the transition is a fade", kind, template, exc
        )
        return None
    return CardMotion(template=template, context=context, frames=frames, digest=digest)


def sting_overlay_filters(
    input_index: int, *, rate: str, seconds: float, source_label: str, out_label: str = "stung"
) -> tuple[list[str], str]:
    """Lay the sting clip (input ``input_index``) over ``source_label``
    (the crossfaded video) for the boundary's whole ``seconds``, from its
    first frame: the clip's timeline is the boundary's, so there is never
    a delay or an offset, whatever handle the edges had."""
    return motion_overlay_filters(
        input_index, rate=rate, seconds=seconds, source_label=source_label, out_label=out_label
    )


__all__ = ["sting_context", "sting_motion", "sting_overlay_filters"]
