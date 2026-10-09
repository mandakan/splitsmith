"""Placeholder logos for previews: a dashed, labelled square where a logo
would go when none is set, so a preview shows which corner belongs to which
logo (the shooter's, your brand, the event's) before any is uploaded.

Previews only. An export never draws one: the request layer that renders a
video never asks for them, and a placeholder file is never a logo on record.
"""

from __future__ import annotations

import hashlib
import io
from importlib import resources
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .fonts import DEFAULTS, font

#: Bump when the picture changes; it is part of every placeholder's name.
PLACEHOLDER_REVISION = 1
SIZE = 512
_INK = (255, 255, 255, 230)
_FILL = (255, 255, 255, 38)

#: The labels the preview uses, by whose logo the spot holds.
SHOOTER = "Shooter logo"
BRAND = "Your brand"
EVENT = "Event logo"


def _face(size: int) -> ImageFont.FreeTypeFont:
    file = font(DEFAULTS["display"]).file
    path = Path(str(resources.files("splitsmith.data").joinpath("fonts").joinpath(file)))
    return ImageFont.truetype(str(path), size)


def placeholder_png(label: str) -> bytes:
    """A transparent square PNG: a translucent rounded panel with a dashed
    border and ``label`` in the middle, one word per line."""
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    inset, radius, width = 18, 56, 12
    draw.rounded_rectangle((inset, inset, SIZE - inset, SIZE - inset), radius=radius, fill=_FILL)
    # The dashes: short segments along each straight edge.
    dash, gap = 46, 30
    lo, hi = inset + radius, SIZE - inset - radius
    for start in range(lo, hi, dash + gap):
        end = min(start + dash, hi)
        for line in (
            ((start, inset), (end, inset)),
            ((start, SIZE - inset), (end, SIZE - inset)),
            ((inset, start), (inset, end)),
            ((SIZE - inset, start), (SIZE - inset, end)),
        ):
            draw.line(line, fill=_INK, width=width)
    for corner in (
        (inset, inset, inset + 2 * radius, inset + 2 * radius, 180, 270),
        (SIZE - inset - 2 * radius, inset, SIZE - inset, inset + 2 * radius, 270, 360),
        (SIZE - inset - 2 * radius, SIZE - inset - 2 * radius, SIZE - inset, SIZE - inset, 0, 90),
        (inset, SIZE - inset - 2 * radius, inset + 2 * radius, SIZE - inset, 90, 180),
    ):
        draw.arc(corner[:4], corner[4], corner[5], fill=_INK, width=width)
    words = label.upper().split()
    face = _face(96 if max(len(w) for w in words) <= 7 else 76)
    heights = [draw.textbbox((0, 0), w, font=face) for w in words]
    line_h = max(b[3] - b[1] for b in heights) + 18
    y = (SIZE - line_h * len(words)) // 2
    for word, box in zip(words, heights, strict=True):
        x = (SIZE - (box[2] - box[0])) // 2 - box[0]
        draw.text((x, y - box[1]), word, font=face, fill=_INK)
        y += line_h
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def placeholder_logo(label: str, cache_dir: Path) -> Path:
    """The placeholder for ``label`` as a file under ``cache_dir``, named by
    its label and revision so a template digest and a preview cache key move
    only when the picture does. Written once."""
    name = hashlib.sha256(f"{PLACEHOLDER_REVISION}\0{label}".encode()).hexdigest()[:12]
    path = cache_dir / f"placeholder-{name}.png"
    if not path.is_file():
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(placeholder_png(label))
        tmp.replace(path)
    return path


__all__ = ["BRAND", "EVENT", "SHOOTER", "placeholder_logo", "placeholder_png"]
