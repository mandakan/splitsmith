"""Upright canvases and the area a vertical-video platform draws over.

An **upright** canvas is taller than wide (:func:`is_upright`); square and
wider canvases keep the landscape layouts, byte for byte. Shorts, Reels and
TikTok draw their own UI over an upright video: the caption, the channel
line and the sound strip along the bottom, and a column of buttons (like,
comment, share, remix) down the right edge from about mid-frame. Text under
either is covered when the video plays there, so every upright card keeps
out of the :class:`SafeArea` and the upright HUD will too (issue #1394).

One definition: the cards read :func:`safe_area` for their insets, and a
document reads it as CSS custom properties through :meth:`SafeArea.css_vars`
(``--safe-bottom``, ``--safe-right``, ``--safe-right-top``, pixels), which
is how a HUD template is meant to receive it. Nothing here opens a font or
a browser.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The bottom band the platforms caption over, as a share of the height.
#: The owner's range is 12-14 % (decision 2026-10-10); 13 % is 250 px at
#: 1080x1920.
BOTTOM_SHARE = 0.13
#: The right-edge button column, as a share of the width: 130 px at 1080.
RIGHT_SHARE = 0.12
#: Where that column starts, as a share of the height. TikTok's avatar sits
#: highest of the three, a little under mid-frame; 40 % leaves a margin.
RIGHT_TOP_SHARE = 0.40


def is_upright(width: int, height: int) -> bool:
    """Whether a canvas takes the upright layouts: taller than wide. A square
    canvas is not upright."""
    return height > width


@dataclass(frozen=True)
class SafeArea:
    """What a platform draws over on a ``width x height`` upright canvas, in
    pixels: a band ``bottom`` tall along the bottom edge, and a column
    ``right`` wide down the right edge from ``right_top`` to the bottom."""

    width: int
    height: int
    bottom: int
    right: int
    right_top: int

    @property
    def bottom_line(self) -> int:
        """The y no text may pass: where the bottom band starts."""
        return self.height - self.bottom

    @property
    def right_line(self) -> int:
        """The x no text below :attr:`right_top` may pass."""
        return self.width - self.right

    def covers(self, left: float, top: float, right: float, bottom: float) -> bool:
        """Whether a box (CSS pixels, page coordinates) reaches into the area."""
        if bottom > self.bottom_line + 0.5:
            return True
        return right > self.right_line + 0.5 and bottom > self.right_top - 0.5

    def box_insets(self, left: int, top: int, right: int, bottom: int) -> tuple[int, int]:
        """How far a box (a grid tile, in canvas pixels) must keep its text in
        from its own right and bottom edges to stay out of the area:
        ``(right, bottom)``, ``0`` on a side the area does not reach. The
        right inset covers the whole box when any of it reaches down into
        the button column's rows."""
        inset_bottom = max(0, bottom - self.bottom_line)
        inset_right = max(0, right - self.right_line) if bottom > self.right_top else 0
        return inset_right, inset_bottom

    def css_vars(self) -> str:
        """The area as CSS custom properties, for a document's ``:root``."""
        return (
            f"--safe-bottom: {self.bottom}px; --safe-right: {self.right}px; "
            f"--safe-right-top: {self.right_top}px;"
        )


def safe_area(width: int, height: int) -> SafeArea | None:
    """The platform safe area of an upright canvas; ``None`` for a square or
    wider one, which no vertical-video platform overlays."""
    if not is_upright(width, height):
        return None
    return SafeArea(
        width=width,
        height=height,
        bottom=round(height * BOTTOM_SHARE),
        right=round(width * RIGHT_SHARE),
        right_top=round(height * RIGHT_TOP_SHARE),
    )


__all__ = ["BOTTOM_SHARE", "RIGHT_SHARE", "RIGHT_TOP_SHARE", "SafeArea", "is_upright", "safe_area"]
