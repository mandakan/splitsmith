"""Where the logos go beyond the cards (spec 2026-10-09 logo spots): the
title page, the stage slates and the closing card always draw theirs; a
*spot* is one more place a video may carry a logo, chosen per export.

- ``wipe``: your brand rides the sting between stages (the shooter's logo,
  as before, when you have no brand).
- ``summaries``: the shooter's logo in the top right of the stage summary
  and the match summary, the corner the slates put it in; on the grid, in
  the top right of each shooter's own tile.
- ``thumbnail``: the YouTube thumbnail is a card (``thumbnail_card``) over
  an action frame, with the match name and every logo, instead of a frame
  of the title page.

Each logo keeps one corner everywhere (your brand top left, the shooter top
right, the event in the centre), so no frame shows the same logo twice. The
presets are what the Export page offers; ``polished`` is the default of
every request body, the export preset and both CLIs. A caller that passes no
spots (the renderers' own default) draws exactly what it drew before.

:func:`paste_logo` composites a logo file onto a still with Pillow: the
summaries are composed in Python, so a logo there needs no browser.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, get_args

from PIL import Image

logger = logging.getLogger(__name__)

LogoSpot = Literal["wipe", "summaries", "thumbnail"]
LOGO_SPOTS: tuple[LogoSpot, ...] = get_args(LogoSpot)

LogoPreset = Literal["cards", "polished", "everything"]
PRESETS: dict[str, frozenset[LogoSpot]] = {
    "cards": frozenset(),
    "polished": frozenset({"wipe", "summaries", "thumbnail"}),
    "everything": frozenset(LOGO_SPOTS),
}
DEFAULT_LOGO_SPOTS: frozenset[LogoSpot] = PRESETS["polished"]

#: A summary's logo, as a share of the frame (or tile) height; smaller than
#: the cards' (0.12), it sits beside figures rather than over a backdrop.
SUMMARY_LOGO_HEIGHT = 0.09
#: The gap to the frame's edge, as the cards keep it (``identity.js``).
LOGO_MARGIN = 0.04


def logo_spots(values: Iterable[str]) -> frozenset[LogoSpot]:
    """The spots ``values`` names, or ``ValueError`` naming the unknown one."""
    out: set[LogoSpot] = set()
    for value in values:
        if value not in LOGO_SPOTS:
            raise ValueError(f"unknown logo spot {value!r}; choose from {', '.join(LOGO_SPOTS)}")
        out.add(value)
    return frozenset(out)


def parse_logo_spots(text: str) -> frozenset[LogoSpot]:
    """The CLI's ``--logos``: a preset name, or spots separated by commas
    (``wipe,summaries``); ``none`` is the same as ``cards``."""
    value = text.strip().lower()
    if value in PRESETS:
        return PRESETS[value]
    if value in ("", "none"):
        return frozenset()
    return logo_spots(part.strip() for part in value.split(",") if part.strip())


def paste_logo(
    canvas: Image.Image,
    logo: Path | None,
    *,
    box: tuple[int, int, int, int] | None = None,
    height_share: float = SUMMARY_LOGO_HEIGHT,
) -> Image.Image:
    """``canvas`` with ``logo`` in the top-right corner of ``box`` (``x, y,
    width, height``; the whole canvas when ``None``), ``height_share`` of
    the box's height tall and at most twice that wide, ``LOGO_MARGIN`` in
    from the edges. A missing, symlinked or unreadable file leaves the
    canvas as it was (logged): a logo is never worth a failed render."""
    if logo is None:
        return canvas
    if logo.is_symlink() or not logo.is_file():
        logger.warning("logo %s is not a file; the still has no logo", logo)
        return canvas
    x0, y0, width, height = box if box is not None else (0, 0, canvas.width, canvas.height)
    target_h = max(1, round(height * height_share))
    margin = round(height * LOGO_MARGIN)
    try:
        with Image.open(logo) as source:
            mark = source.convert("RGBA")
    except (OSError, Image.DecompressionBombError, ValueError) as exc:
        logger.warning("could not read logo %s (%s); the still has no logo", logo, exc)
        return canvas
    scale = min(target_h / mark.height, (2 * target_h) / mark.width)
    size = (max(1, round(mark.width * scale)), max(1, round(mark.height * scale)))
    mark = mark.resize(size, Image.Resampling.LANCZOS)
    out = canvas.convert("RGBA")
    out.alpha_composite(mark, (x0 + width - margin - size[0], y0 + margin))
    return out.convert(canvas.mode)


__all__ = [
    "DEFAULT_LOGO_SPOTS",
    "LOGO_SPOTS",
    "LogoPreset",
    "LogoSpot",
    "PRESETS",
    "logo_spots",
    "parse_logo_spots",
    "paste_logo",
]
