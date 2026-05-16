"""Tests for the overlay theme palette + DefaultTemplate integration."""

from __future__ import annotations

import json
from importlib import resources

import pytest
from PIL import Image

from splitsmith import overlay_render, overlay_theme


def test_clean_preset_matches_legacy_hardcoded_values() -> None:
    """The ``clean`` preset must match the colors the renderer used before
    overlay_theme.py existed; otherwise exports rendered with --theme clean
    stop being byte-comparable with archived per-stage MOVs."""
    t = overlay_theme.load_theme("clean")
    assert t.name == "clean"
    assert t.ink == (255, 255, 255)
    assert t.split == (255, 220, 80)
    assert t.stroke == (0, 0, 0)
    assert t.shadow == (0, 0, 0)


def test_splitsmith_preset_loads_from_packaged_json() -> None:
    """The ``splitsmith`` preset must round-trip through the JSON mirror so
    a regenerate step actually flows into runtime."""
    with resources.files("splitsmith.data").joinpath("overlay_theme.json").open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    t = overlay_theme.load_theme("splitsmith")
    assert t.name == "splitsmith"
    assert list(t.ink) == data["colors"]["ink"]
    assert list(t.split) == data["colors"]["split"]
    assert list(t.stroke) == data["colors"]["stroke"]
    # Sanity: ink is light (designed for dark surfaces); stroke is dark.
    assert sum(t.ink) > 600
    assert sum(t.stroke) < 100


def test_unknown_theme_raises() -> None:
    with pytest.raises(overlay_theme.OverlayThemeError):
        overlay_theme.load_theme("midnight")  # type: ignore[arg-type]


def test_default_template_paints_theme_ink() -> None:
    """DefaultTemplate must paint the theme's ink color for shot count /
    timer, not a hardcoded white. We swap in a magenta ink and verify the
    canvas carries that hue."""
    magenta = overlay_theme.OverlayTheme(
        name="clean",
        ink=(255, 0, 200),
        split=(0, 200, 255),
        stroke=(0, 0, 0),
        accent=(255, 0, 0),
        font_display="Antonio",
        font_mono="JetBrains Mono",
    )
    tmpl = overlay_render.DefaultTemplate(width=320, height=180, theme=magenta)
    state = overlay_render.FrameState(
        time_seconds=2.0,
        beep_time_in_clip=0.5,
        shot_count=5,
        shots_fired=2,
        last_split=0.21,
        last_shot_time_in_clip=1.5,
        running_total=1.5,
    )
    canvas = Image.new("RGBA", (320, 180), (0, 0, 0, 0))
    tmpl.draw_frame(canvas, state)

    # Look for at least one opaque, magenta-ish pixel (high R, low G, high B).
    # The fill is anti-aliased so we tolerate near-matches rather than
    # require an exact RGBA tuple.
    pixels = canvas.load()
    found = False
    for y in range(canvas.height):
        for x in range(canvas.width):
            r, g, b, a = pixels[x, y]
            if a > 200 and r > 200 and g < 80 and b > 150:
                found = True
                break
        if found:
            break
    assert found, "expected magenta ink pixels from the theme override"


def test_default_template_paints_theme_split_color() -> None:
    """The bottom-center last-split label uses theme.split, not the
    hardcoded gold."""
    cyan_split = overlay_theme.OverlayTheme(
        name="clean",
        ink=(255, 255, 255),
        split=(0, 220, 255),
        stroke=(0, 0, 0),
        accent=(0, 0, 0),
        font_display="Antonio",
        font_mono="JetBrains Mono",
    )
    tmpl = overlay_render.DefaultTemplate(width=320, height=180, theme=cyan_split)
    state = overlay_render.FrameState(
        time_seconds=2.0,
        beep_time_in_clip=0.5,
        shot_count=5,
        shots_fired=2,
        last_split=0.21,
        last_shot_time_in_clip=2.0,  # split label fully held (alpha=255)
        running_total=1.5,
    )
    canvas = Image.new("RGBA", (320, 180), (0, 0, 0, 0))
    tmpl.draw_frame(canvas, state)

    # Cyan: low R, high G, high B. Scan the bottom half where the split label sits.
    pixels = canvas.load()
    found = False
    for y in range(canvas.height // 2, canvas.height):
        for x in range(canvas.width):
            r, g, b, a = pixels[x, y]
            if a > 200 and r < 80 and g > 150 and b > 200:
                found = True
                break
        if found:
            break
    assert found, "expected cyan split label pixels from the theme override"


def test_default_template_defaults_to_splitsmith() -> None:
    """Omitting ``theme`` picks the brand palette so every overlay matches
    the web UI without callers having to opt in."""
    tmpl = overlay_render.DefaultTemplate(width=320, height=180)
    assert tmpl.theme.name == "splitsmith"


def test_splitsmith_theme_picks_bundled_mono_font_by_default() -> None:
    """When the caller doesn't pin a font and the theme is splitsmith,
    DefaultTemplate must resolve the bundled JetBrains Mono Bold so the
    overlay matches the web UI's tabular numerals on every host."""
    tmpl = overlay_render.DefaultTemplate(width=320, height=180)
    # PIL exposes the family/style via getname().
    family, style = tmpl.font_big.getname()
    assert family == "JetBrains Mono"
    assert "Bold" in style


def test_clean_theme_leaves_font_resolution_to_system_fallback() -> None:
    """The clean preset must NOT pin the bundled font, so callers picking
    the neutral palette still get whatever system mono they had before.
    The bundled JetBrains Mono is only auto-wired for splitsmith; clean
    theme falls through to the OS-specific fallback list, which doesn't
    include the bundled file."""
    clean = overlay_theme.load_theme("clean")
    tmpl = overlay_render.DefaultTemplate(width=320, height=180, theme=clean)
    family, _ = tmpl.font_big.getname()
    assert family != "JetBrains Mono", f"clean theme should not auto-wire bundled font; got family={family!r}"


def test_available_font_names_includes_bundled_presets() -> None:
    """The bundled splitsmith-* presets must be discoverable via the
    public preset listing -- they're how a future UI picker offers brand
    fonts without filesystem path knowledge."""
    names = overlay_render.available_font_names()
    assert "splitsmith-mono" in names
    assert "splitsmith-display" in names


def test_overlay_theme_json_is_in_sync_with_css() -> None:
    """Re-running scripts/build_overlay_theme.py against the current
    index.css must produce byte-identical output. Catches drift between
    the design tokens and the mirrored JSON."""
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "build_overlay_theme.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"overlay_theme.json drifted from index.css: {proc.stderr}"
