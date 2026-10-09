"""The YouTube thumbnail as a card (the ``thumbnail`` logo spot, spec
2026-10-09): the Look's ``thumbnail`` template drawn over a sharp action
frame, the match name large, your brand top left, the shooters' logos top
right and the event logo above the name. Without the spot, or when the
card cannot be drawn, the thumbnail is the plain frame it always was
(``youtube_sidecar.write_thumbnail``); a thumbnail never fails an export.

:func:`render_thumbnail_card` composes and writes the JPEG; the template's
context is :func:`thumbnail_context`. The frame is the caller's: the single
shooter's export grabs it from the stage clip (no HUD burnt in), the grid
from its render.
"""

from __future__ import annotations

import io
import logging
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from .look_template import TemplateContext, engine_block, shared_url, shooter_json, theme_tokens
from .looks import Look, thumbnail_template_for
from .overlay_card import card_scale
from .overlay_html import single_css
from .overlay_raster import Rasterizer
from .overlay_theme import theme_for

logger = logging.getLogger(__name__)

#: YouTube's recommended thumbnail size.
WIDTH = 1280
HEIGHT = 720


class ThumbnailError(RuntimeError):
    """The card could not be drawn; the caller falls back to the frame."""


@dataclass(frozen=True)
class ThumbnailData:
    """What the card says: the title, a line or two under it, and the logos."""

    title: str
    lines: tuple[str, ...] = ()
    #: The shooters as the cards see them (``ResolvedIdentity`` or
    #: ``CompositionShooter``); their logos go top right.
    shooters: Sequence[Any] = field(default_factory=tuple)
    #: ``look_brand.brand_mark_json``'s answer, or ``None``.
    brand: dict[str, str | None] | None = None
    event_logo: Path | None = None


def thumbnail_context(
    data: ThumbnailData, *, look: Look, width: int = WIDTH, height: int = HEIGHT
) -> TemplateContext:
    """What the ``thumbnail`` template receives as ``window.splitsmith``."""
    theme = theme_for(look)
    payload: dict[str, Any] = {
        "thumbnail": {"title": data.title, "lines": [line for line in data.lines if line.strip()]},
        "shooters": [shooter_json(shooter) for shooter in data.shooters],
    }
    if data.brand is not None:
        payload["brand"] = data.brand
    if data.event_logo is not None and data.event_logo.is_file() and not data.event_logo.is_symlink():
        payload["event"] = {"logo": data.event_logo.resolve().as_uri()}
    return TemplateContext(
        theme=theme_tokens(theme),
        data=payload,
        size={"width": width, "height": height},
        fps=30.0,
        engine=engine_block(
            css=single_css(width=width, height=height, scale=card_scale(height), theme=theme)
        ),
        assets={"shared": shared_url()},
    )


def compose_thumbnail(frame: Path, data: ThumbnailData, *, look: Look, rasterizer: Rasterizer) -> Image.Image:
    """The card over ``frame`` (cropped to fill 16:9) as an RGB image, or
    :class:`ThumbnailError` with the reason."""
    template = thumbnail_template_for(look)
    if template is None:
        raise ThumbnailError(f"the {look.name} Look has no thumbnail template")
    try:
        with Image.open(frame) as source:
            canvas = ImageOps.fit(source.convert("RGB"), (WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ThumbnailError(f"the frame could not be read: {exc}") from exc
    try:
        png = rasterizer.render_template(
            template, context=thumbnail_context(data, look=look), width=WIDTH, height=HEIGHT
        )
        with Image.open(io.BytesIO(png)) as rendered:
            layer = rendered.convert("RGBA")
    except Exception as exc:  # noqa: BLE001 -- a thumbnail never fails an export
        raise ThumbnailError(f"the thumbnail template failed: {exc}") from exc
    composed = canvas.convert("RGBA")
    composed.alpha_composite(layer)
    return composed.convert("RGB")


def grab_frame(
    video: Path,
    at_seconds: float,
    out: Path,
    *,
    ffmpeg_binary: str = "ffmpeg",
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Path:
    """One full-size PNG of ``video`` at ``at_seconds`` (output-side seek,
    so the frame is exact). Raises ``CalledProcessError`` / ``OSError``."""
    out.parent.mkdir(parents=True, exist_ok=True)
    runner(
        [
            ffmpeg_binary,
            "-hide_banner",
            "-y",
            "-i",
            str(video),
            "-ss",
            f"{max(0.0, at_seconds):.3f}",
            "-frames:v",
            "1",
            str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if not out.is_file() or out.stat().st_size == 0:
        raise OSError(f"no frame at {at_seconds:.3f} s in {video.name}")
    return out


def render_thumbnail_card(
    frame: Path, out: Path, data: ThumbnailData, *, look: Look, rasterizer: Rasterizer
) -> Path:
    """Compose the card over ``frame`` and write it to ``out`` as a JPEG
    YouTube takes (1280x720, well under its 2 MB cap)."""
    image = compose_thumbnail(frame, data, look=look, rasterizer=rasterizer)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out, format="JPEG", quality=90, optimize=True)
    return out


__all__ = [
    "HEIGHT",
    "WIDTH",
    "ThumbnailData",
    "ThumbnailError",
    "compose_thumbnail",
    "grab_frame",
    "render_thumbnail_card",
    "thumbnail_context",
]
