"""The moved font/text helpers, tested where they now live."""

import logging

import pytest
from PIL import Image, ImageDraw

from splitsmith import overlay_render, overlay_text


def test_module_exposes_the_moved_helpers():
    for name in (
        "_load_font",
        "_load_bundled_font",
        "_draw_text_with_shadow",
        "_log_font_choice",
        "available_font_names",
        "reset_font_log_cache",
        "_BUNDLED_FONTS",
        "_FONT_PRESETS",
        "_FONT_FALLBACKS",
    ):
        assert hasattr(overlay_text, name), f"overlay_text is missing {name}"


def test_overlay_render_reexports_the_same_objects():
    # Identity, not equality: existing callers and tests reach these
    # through overlay_render, and an `except` clause matches on the
    # class object.
    assert overlay_render.OverlayRenderError is overlay_text.OverlayRenderError
    assert overlay_render._load_font is overlay_text._load_font
    assert overlay_render._draw_text_with_shadow is overlay_text._draw_text_with_shadow


def test_load_font_unknown_name_raises():
    with pytest.raises(overlay_text.OverlayRenderError):
        overlay_text._load_font(None, 24, font_name="not-a-real-font")


def test_load_font_bundled_returns_a_font():
    font = overlay_text._load_font(None, 24, font_name="splitsmith-mono")
    assert font is not None


def test_draw_text_with_shadow_marks_pixels():
    canvas = Image.new("RGBA", (240, 80), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font = overlay_text._load_font(None, 32, font_name="splitsmith-mono")
    overlay_text._draw_text_with_shadow(draw, canvas, (10, 10), "1.23", font, (255, 255, 255, 255))
    assert canvas.getextrema()[3][1] > 0, "nothing was drawn"


def test_draw_text_with_shadow_zero_alpha_draws_nothing():
    canvas = Image.new("RGBA", (240, 80), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font = overlay_text._load_font(None, 32, font_name="splitsmith-mono")
    overlay_text._draw_text_with_shadow(draw, canvas, (10, 10), "1.23", font, (255, 255, 255, 0))
    assert canvas.getextrema()[3][1] == 0


def test_font_log_is_emitted_once_per_tier(caplog):
    overlay_text.reset_font_log_cache()
    with caplog.at_level(logging.DEBUG, logger="splitsmith.overlay_text"):
        overlay_text._load_font(None, 24, font_name="splitsmith-mono")
        overlay_text._load_font(None, 24, font_name="splitsmith-mono")
    matching = [r for r in caplog.records if "splitsmith-mono" in r.getMessage()]
    assert len(matching) == 1
