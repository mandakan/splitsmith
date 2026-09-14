"""The blurred, dimmed backdrop under a held still.

Hoisted out of ``compare/overlay_summary.py`` (issue #973) so the
generated cards in the single-shooter MP4 can sit on the same treatment
the grid's stage summary uses, without core code importing from
``compare/``. The grid module keeps calling these through its own
module-level names (``_letterbox`` / ``_apply_blur`` / ``_dim``) so its
tests, which count calls to those names, are unaffected.

**The blur happens once, not per frame.** A held still is one PIL
``GaussianBlur`` call held for the whole hold duration; ``gblur`` in an
ffmpeg graph would cost orders of magnitude more for an identical
result.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

#: Default fraction of black composited over a still. Chosen so the text
#: is legible over any footage without crushing the picture to nothing --
#: it is still recognisably the shooter's own frame.
DEFAULT_DIM = 0.45


def default_blur_radius(height: int) -> int:
    """``max(8, height // 60)`` -- scaled from the cell so a 4K canvas and
    a small preview blur proportionally."""
    return max(8, height // 60)


def letterbox(frame: Image.Image, width: int, height: int) -> Image.Image:
    """Scale ``frame`` to fit inside ``width x height``, preserving aspect
    ratio, and centre it on black -- the PIL equivalent of ffmpeg's
    ``scale=w:h:force_original_aspect_ratio=decrease,pad=w:h:...`` pair,
    so a freeze frame lands exactly the way the live footage did."""
    scale = min(width / frame.width, height / frame.height)
    new_size = (max(1, round(frame.width * scale)), max(1, round(frame.height * scale)))
    resized = frame.resize(new_size, Image.Resampling.LANCZOS)
    cell = Image.new("RGB", (width, height), (0, 0, 0))
    cell.paste(resized, ((width - new_size[0]) // 2, (height - new_size[1]) // 2))
    return cell


def apply_blur(image: Image.Image, radius: int) -> Image.Image:
    """The one place a Gaussian blur touches a still."""
    if radius <= 0:
        return image
    return image.filter(ImageFilter.GaussianBlur(radius))


def dim(image: Image.Image, amount: float) -> Image.Image:
    """Darken ``image`` by compositing a black layer at ``amount`` alpha.

    ``Image.blend(image, black, amount)`` over two same-size RGB images is
    the same operation as alpha-compositing an opaque black layer at
    ``amount``, without a round-trip through an alpha channel.
    """
    if amount <= 0:
        return image
    black = Image.new("RGB", image.size, (0, 0, 0))
    return Image.blend(image, black, min(1.0, amount))


def backdrop_from_frame(
    frame_path: Path,
    *,
    width: int,
    height: int,
    radius: int | None = None,
    dim_amount: float = DEFAULT_DIM,
) -> Image.Image | None:
    """The blurred, dimmed, canvas-sized RGB backdrop read from one frame
    on disk, or ``None`` when the frame cannot be read -- the caller
    degrades (a black cell, a flat theme colour) rather than failing."""
    try:
        with Image.open(frame_path) as source:
            frame = source.convert("RGB")
    except Exception as exc:  # noqa: BLE001 -- a bad frame degrades, it does not crash the render
        logger.warning("cannot read frame %s (%s); the still composes without it", frame_path, exc)
        return None
    blur = radius if radius is not None else default_blur_radius(height)
    return dim(apply_blur(letterbox(frame, width, height), blur), dim_amount)


__all__ = [
    "DEFAULT_DIM",
    "apply_blur",
    "backdrop_from_frame",
    "default_blur_radius",
    "dim",
    "letterbox",
]
