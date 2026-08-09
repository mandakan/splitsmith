"""The drawtext clock's shared vocabulary (issue #684).

These assert exact literal strings rather than recomputing them. The
escaping was established against ffmpeg 6.1.1 by measurement, not by
reasoning, and a "harmless tidy" of it has to change a test deliberately.
"""

from pathlib import Path

from splitsmith import overlay_clock


def test_ffmpeg_color_is_hex_because_the_theme_ink_has_no_name() -> None:
    assert overlay_clock.ffmpeg_color((244, 244, 245)) == "0xf4f4f5"
    assert overlay_clock.ffmpeg_color((0, 0, 0)) == "0x000000"


def test_clock_text_truncates_rather_than_rounds() -> None:
    # The held value must never read above the last value the ticking
    # filter drew, so this truncates.
    assert overlay_clock.clock_text(2.567) == "2.56"
    assert overlay_clock.clock_text(0.05) == "0.05"


def test_clock_text_truncates_on_milliseconds_not_in_floating_point() -> None:
    # 2.09 * 100 is 208.99999999999997, which floors to 2.08 and would
    # show the clock jumping backwards at the freeze.
    assert overlay_clock.clock_text(2.09) == "2.09"


def test_the_elapsed_expression_is_character_for_character_what_the_grid_emits() -> None:
    assert overlay_clock.elapsed_text_option("1.5") == (
        r"text='%{eif\:trunc(t-1.5)\:d}.%{eif\:trunc(mod((t-1.5)*100\,100))\:d\:2}'"
    )


def test_the_common_options_are_character_for_character_what_the_grid_emits() -> None:
    assert overlay_clock.clock_common_options(
        font_path=Path("/tmp/JetBrainsMono-Bold.ttf"),
        font_size=64,
        ink=(244, 244, 245),
        stroke=(10, 11, 13),
        x_expr="960+960-tw-24",
        y_expr="540+24",
    ) == (
        "fontfile='/tmp/JetBrainsMono-Bold.ttf':fontsize=64:"
        "fontcolor=0xf4f4f5:borderw=3:bordercolor=0x0a0b0d:"
        "x=960+960-tw-24:y=540+24"
    )
