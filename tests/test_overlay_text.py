"""The moved font/text helpers, tested where they now live."""

import logging
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from splitsmith import overlay_text


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


# --- moved from tests/test_overlay_render.py (issue #684) -------------
#
# These cover ``overlay_text``'s own functions; they lived beside the
# renderer only because it used to re-export them.


def test_load_font_known_name_falls_back_when_missing(tmp_path: Path) -> None:
    """A named preset whose files don't exist on this machine must still
    produce a usable font (generic fallback or PIL default), not crash."""
    font = overlay_text._load_font(None, 24, font_name="dejavu-mono")
    assert font is not None


def test_available_font_names_includes_known_presets() -> None:
    names = overlay_text.available_font_names()
    assert "menlo" in names
    assert "sf-mono" in names
    assert "dejavu-mono" in names


def test_load_font_pil_default_fallback_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When neither bundled nor any system font is present we land on PIL's
    bitmap default and the overlay looks bad. The fallback must log a
    warning so a packaged Linux user sees *why* their overlays look low-res."""
    # Keep "dejavu-mono" as a known preset name (so the unknown-name guard
    # doesn't raise) but give it no candidate paths -- forces the full walk.
    monkeypatch.setattr(overlay_text, "_FONT_PRESETS", {"dejavu-mono": ()})
    monkeypatch.setattr(overlay_text, "_FONT_FALLBACKS", ())
    # Force bundled lookup to miss so we walk the full fallback chain.
    monkeypatch.setattr(overlay_text, "_load_bundled_font", lambda *_args, **_kw: None)
    overlay_text.reset_font_log_cache()

    with caplog.at_level("WARNING", logger="splitsmith.overlay_text"):
        font = overlay_text._load_font(None, 24, font_name="dejavu-mono")
    assert font is not None
    assert any("PIL's built-in bitmap font" in rec.message for rec in caplog.records)


def test_load_font_bundled_emits_debug_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The happy path (bundled font in the wheel) must not warn."""
    overlay_text.reset_font_log_cache()
    with caplog.at_level("WARNING", logger="splitsmith.overlay_text"):
        overlay_text._load_font(None, 24, font_name="splitsmith-mono")
    assert not [rec for rec in caplog.records if rec.levelname == "WARNING"]
