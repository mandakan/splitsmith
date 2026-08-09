"""The running clock's ``drawtext`` vocabulary, shared by both renderers.

The clock is the one genuinely per-frame element in either overlay: it
changes every frame, so it cannot be a pre-rendered sprite, and it is
positioned by an expression ffmpeg evaluates at draw time when it finally
knows ``tw``/``th``. Both the compare grid (``compare/mp4_grid.py``) and
the single-shooter export (``overlay_render.py``) therefore build
``drawtext`` filters, and before issue #684 only the grid could -- these
helpers lived as module-private functions inside it.

**Nothing here is new.** Every string this module produces is
character-for-character what ``mp4_grid`` built inline, and the tests
assert the literals rather than recomputing them. The escaping was
established against ffmpeg 6.1.1 by measurement: inside ``text='...'``
the ``:`` and ``,`` separators of ``%{eif:...}`` still have to be
backslash-escaped or the filtergraph parser splits the option on them,
and ``%{eif:...:d:2}`` zero-pads so 0.05s renders ``0.05`` and not
``0.5``.

**Known, measured, and deliberately left alone:** the hundredths half of
:func:`elapsed_text_option` reads one hundredth *low* on about 4.6% of
frames. ``t`` arrives as a binary float, so ``mod((t-start)*100,100)``
lands just under the integer it should be and ``trunc`` takes the value
below. Simulated over 95,132 frames (4 frame rates x 4 start offsets):
4.59% of frames affected, **zero** backward steps, and across 112 freeze
scenarios the held text never read below the last value the ticking
filter drew. The properties a viewer can perceive -- a clock that only
counts up, and a final time agreeing with the last ticked one -- all
hold. An epsilon inside the expression only reaches 2.52% and is
identical at 1e-7, 1e-6 and 1e-5: it moves which frames are wrong rather
than making them right. Getting it exact means computing hundredths
outside ffmpeg, i.e. one filter per hundredth -- thousands per stage. Do
not "tidy" this expression into a third form without re-measuring both
numbers.
"""

from __future__ import annotations

from pathlib import Path

from .runtime import quote_filter_value


def ffmpeg_color(rgb: tuple[int, int, int]) -> str:
    """``drawtext`` colour literal. Hex, because it takes named colours
    only from its own table -- the splitsmith theme's ink is
    ``(244, 244, 245)``, which has no name."""
    red, green, blue = rgb
    return f"0x{red:02x}{green:02x}{blue:02x}"


def clock_text(seconds: float) -> str:
    """Format an elapsed time the way the ticking filter renders it.

    Truncated to hundredths rather than rounded, so a held value can
    never read above the last value the ticking filter drew.

    The truncation runs on integer milliseconds and not on
    ``math.floor(seconds * 100) / 100``: ``2.09 * 100`` is
    ``208.99999999999997`` in binary floating point, which floors to
    ``2.08`` and would show the clock jumping backwards at the freeze.
    """
    hundredths = round(seconds * 1000) // 10
    return f"{hundredths // 100}.{hundredths % 100:02d}"


def border_width(font_size: int) -> int:
    """Stroke around the clock's glyphs, floored so small type still
    reads over bright footage."""
    return max(2, font_size // 18)


def elapsed_text_option(start: str) -> str:
    """The ``text='...'`` option for a clock ticking from ``start``.

    ``start`` is pre-formatted by the caller (``f"{seconds:g}"``) so the
    same literal appears in both this expression and the ``enable``
    window that gates it -- two spellings of one number is how a filter
    ends up drawing over its neighbour.
    """
    return f"text='%{{eif\\:trunc(t-{start})\\:d}}." f"%{{eif\\:trunc(mod((t-{start})*100\\,100))\\:d\\:2}}'"


def clock_common_options(
    *,
    font_path: Path,
    font_size: int,
    ink: tuple[int, int, int],
    stroke: tuple[int, int, int],
    x_expr: str,
    y_expr: str,
) -> str:
    """Every ``drawtext`` option a clock filter shares with its siblings.

    ``font_path`` must be a real file that outlives the render --
    ``drawtext`` opens it itself, so a temp file from
    ``importlib.resources.as_file`` will not do. See
    :func:`splitsmith.overlay_text.materialize_font`.
    """
    font = quote_filter_value(str(font_path))
    return (
        f"fontfile={font}:fontsize={font_size}:"
        f"fontcolor={ffmpeg_color(ink)}:"
        f"borderw={border_width(font_size)}:"
        f"bordercolor={ffmpeg_color(stroke)}:"
        f"x={x_expr}:y={y_expr}"
    )
