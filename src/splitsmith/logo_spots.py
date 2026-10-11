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
- ``watermark``: your brand, small and half see-through, over the stage
  footage in the top-left corner (top right when the HUD sits top left).
  For videos reposted off YouTube, where the channel watermark does not
  follow; only ``everything`` turns it on.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from PIL import Image

logger = logging.getLogger(__name__)

LogoSpot = Literal["wipe", "summaries", "thumbnail", "watermark"]
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
#: The room text keeps from a pasted logo, as a share of the box height
#: (#1432): enough for the text's own shadow, and small on purpose, so a
#: name that already ended short of the logo keeps its layout.
LOGO_TEXT_GAP = 0.005
#: The watermark: a share of the frame height, and how opaque it is.
WATERMARK_HEIGHT = 0.07
WATERMARK_OPACITY = 0.8


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


@dataclass(frozen=True)
class LogoBox:
    """Where :func:`paste_logo` draws a logo, in canvas pixels: ``x, y`` the
    top-left of the picture, ``width x height`` its drawn size, and ``gap``
    the room text keeps from it. A layout that draws text in the logo's
    corner reserves this box (:meth:`keep_out`) so the text ends before it."""

    x: int
    y: int
    width: int
    height: int
    gap: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def keep_out(self) -> tuple[int, int]:
        """``(left, bottom)``: text that starts above ``bottom`` must end at
        or before ``left``. The gap is taken off both."""
        return self.x - self.gap, self.bottom + self.gap


def _open_logo(logo: Path | None, what: str) -> Image.Image | None:
    """``logo`` as RGBA, or ``None`` (logged) when it is missing, a symlink
    or unreadable: a logo is never worth a failed render."""
    if logo is None:
        return None
    if logo.is_symlink() or not logo.is_file():
        logger.warning("logo %s is not a file; the %s has no logo", logo, what)
        return None
    try:
        with Image.open(logo) as source:
            return source.convert("RGBA")
    except (OSError, Image.DecompressionBombError, ValueError) as exc:
        logger.warning("could not read logo %s (%s); the %s has no logo", logo, exc, what)
        return None


def _place(
    mark_size: tuple[int, int], box: tuple[int, int, int, int], height_share: float
) -> tuple[LogoBox, tuple[int, int]]:
    """The drawn box for a ``mark_size`` picture in ``box``'s top-right slot,
    and the size to resample it to. The slot is ``height_share`` of the box's
    height tall and at most twice that wide; a picture smaller than the slot
    keeps its own pixels (#1433), centred where the slot would draw it."""
    x0, y0, width, height = box
    target_h = max(1, round(height * height_share))
    margin = round(height * LOGO_MARGIN)
    mark_w, mark_h = mark_size
    fit = min(target_h / mark_h, (2 * target_h) / mark_w)
    slot = (max(1, round(mark_w * fit)), max(1, round(mark_h * fit)))
    scale = min(fit, 1.0)
    size = (max(1, round(mark_w * scale)), max(1, round(mark_h * scale)))
    slot_x = x0 + width - margin - slot[0]
    slot_y = y0 + margin
    x = slot_x + (slot[0] - size[0]) // 2
    y = slot_y + (slot[1] - size[1]) // 2
    gap = max(1, round(height * LOGO_TEXT_GAP))
    return LogoBox(x=x, y=y, width=size[0], height=size[1], gap=gap), size


def logo_box(
    logo: Path | None,
    *,
    box: tuple[int, int, int, int],
    height_share: float = SUMMARY_LOGO_HEIGHT,
) -> LogoBox | None:
    """Where :func:`paste_logo` would draw ``logo`` in ``box`` (``x, y,
    width, height``), or ``None`` when it would draw nothing. Reads only the
    file's header."""
    if logo is None or logo.is_symlink() or not logo.is_file():
        return None
    try:
        with Image.open(logo) as source:
            size = source.size
    except (OSError, Image.DecompressionBombError, ValueError):
        return None
    return _place(size, box, height_share)[0]


def text_room(box: LogoBox | None, *, right: int, minimum: int = 0) -> int | None:
    """How far in from ``right`` (the right edge of a row of text, canvas
    pixels) that row must end to stay clear of the logo at ``box`` and its
    gap (#1432); ``None`` when there is no logo or the row already ends
    ``minimum`` px or more before it."""
    if box is None:
        return None
    room = right - box.keep_out()[0]
    return room if room > minimum else None


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
    from the edges; never larger than its own pixels: a smaller logo keeps
    its size, centred in that slot (#1433). :func:`logo_box` says where. A
    missing, symlinked or unreadable file leaves the canvas as it was
    (logged): a logo is never worth a failed render."""
    mark = _open_logo(logo, "still")
    if mark is None:
        return canvas
    placed, size = _place(
        mark.size, box if box is not None else (0, 0, canvas.width, canvas.height), height_share
    )
    if size != mark.size:
        mark = mark.resize(size, Image.Resampling.LANCZOS)
    out = canvas.convert("RGBA")
    out.alpha_composite(mark, (placed.x, placed.y))
    return out.convert(canvas.mode)


def watermark_image(logo: Path, *, height: int, opacity: float = WATERMARK_OPACITY) -> Image.Image | None:
    """``logo`` scaled to ``height`` (at most three times as wide, never past
    its own size) with its alpha multiplied by ``opacity``, as RGBA; ``None``
    (logged) when the file cannot be read or is a symlink."""
    if logo.is_symlink() or not logo.is_file():
        logger.warning("watermark logo %s is not a file; no watermark", logo)
        return None
    try:
        with Image.open(logo) as source:
            mark = source.convert("RGBA")
    except (OSError, Image.DecompressionBombError, ValueError) as exc:
        logger.warning("could not read watermark logo %s (%s); no watermark", logo, exc)
        return None
    # Never larger than its own pixels (#1433): a small logo stays small.
    scale = min(height / mark.height, (3 * height) / mark.width, 1.0)
    size = (max(1, round(mark.width * scale)), max(1, round(mark.height * scale)))
    if size != mark.size:
        mark = mark.resize(size, Image.Resampling.LANCZOS)
    alpha = mark.getchannel("A").point(lambda a: round(a * opacity))
    mark.putalpha(alpha)
    return mark


__all__ = [
    "DEFAULT_LOGO_SPOTS",
    "LOGO_SPOTS",
    "LogoBox",
    "LogoPreset",
    "LogoSpot",
    "PRESETS",
    "logo_box",
    "logo_spots",
    "parse_logo_spots",
    "paste_logo",
    "text_room",
    "watermark_image",
]
