"""Tests for the overlay theme palette + its reach into rendered output."""

from __future__ import annotations

import json
from importlib import resources

import pytest

from splitsmith import overlay_text, overlay_theme
from splitsmith.overlay_html import single_html
from splitsmith.overlay_layout import Anchor, CellScale, ColorToken, Element, Flow, Group, Role
from splitsmith.overlay_theme import load_theme
from tests.test_overlay_html import _rule


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
    # split_good is new (issue #683 Task 7): no legacy value to match, but
    # it must be distinct from every other clean token and must not be
    # black/white (both of which would fail to read as "green").
    assert t.split_good not in (t.ink, t.split, t.stroke, t.accent)
    r, g, b = t.split_good
    assert g > r and g > b, f"expected a green-dominant split_good, got {t.split_good!r}"
    # issue #683 Task 7c: the three-reds discipline. accent_fill is the
    # PLATE background (darker than accent, for AA-large contrast with
    # ink text on top); accent_text is body-size unplated red text
    # (lighter than accent -- the raw identity red is too thin at small
    # sizes). Neither may just be accent again wearing a new name.
    assert t.accent_fill != t.accent
    assert t.accent_text != t.accent
    assert sum(t.accent_fill) < sum(t.accent), "accent_fill must be darker than accent"
    assert sum(t.accent_text) > sum(t.accent), "accent_text must be lighter than accent"
    # rule and muted are real tokens now, not an alpha hack on ink.
    assert t.rule not in (t.ink, t.stroke)
    assert t.muted not in (t.ink, t.stroke)
    # ink_2 (issue #683 Task 8): a step down from ink, a step up from
    # muted -- distinct from both, and from stroke/accent.
    assert t.ink_2 not in (t.ink, t.muted, t.stroke, t.accent)
    assert sum(t.muted) < sum(t.ink_2) < sum(t.ink)


def test_splitsmith_preset_loads_from_packaged_json() -> None:
    """The ``splitsmith`` preset must round-trip through the JSON mirror so
    a regenerate step actually flows into runtime."""
    with resources.files("splitsmith.data").joinpath("overlay_theme.json").open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    t = overlay_theme.load_theme("splitsmith")
    assert t.name == "splitsmith"
    assert list(t.ink) == data["colors"]["ink"]
    assert list(t.split) == data["colors"]["split"]
    assert list(t.split_good) == data["colors"]["split_good"]
    assert list(t.stroke) == data["colors"]["stroke"]
    assert list(t.accent_fill) == data["colors"]["accent_fill"]
    assert list(t.accent_text) == data["colors"]["accent_text"]
    assert list(t.rule) == data["colors"]["rule"]
    assert list(t.muted) == data["colors"]["muted"]
    assert list(t.ink_2) == data["colors"]["ink_2"]
    # Sanity: ink is light (designed for dark surfaces); stroke is dark.
    assert sum(t.ink) > 600
    assert sum(t.stroke) < 100


def test_unknown_theme_raises() -> None:
    with pytest.raises(overlay_theme.OverlayThemeError):
        overlay_theme.load_theme("midnight")  # type: ignore[arg-type]


def test_the_theme_ink_reaches_the_rendered_css() -> None:
    """The theme's ink color must reach the rendered document, not a
    hardcoded white. Re-expresses what
    ``test_default_template_paints_theme_ink`` checked on pixels back when
    ``DefaultTemplate`` drew the counter itself -- the counter now draws
    through ``single_html``'s CSS, so this checks the CSS instead."""
    theme = overlay_theme.load_theme("splitsmith")
    doc = single_html(
        (
            Group(
                anchor=Anchor.TOP_LEFT,
                flow=Flow.ROW,
                elements=(Element(text="7/32", role=Role.LIVE_PRIMARY),),
            ),
        ),
        width=1920,
        height=1080,
        scale=CellScale.for_cell(1080),
        theme=theme,
    )
    red, green, blue = theme.ink
    assert f"rgb({red},{green},{blue})" in doc


def test_the_theme_split_color_reaches_the_rendered_css() -> None:
    """The bottom-center last-split label used ``theme.split``, not a
    hardcoded gold -- re-expresses
    ``test_default_template_paints_theme_split_color``. ``ColorToken.SPLIT``
    is how an element opts into that token now; the CSS class it draws
    through (``.tok-split``) carries the color, not a per-pixel scan."""
    theme = overlay_theme.load_theme("splitsmith")
    doc = single_html(
        (
            Group(
                anchor=Anchor.BOTTOM_CENTER,
                flow=Flow.ROW,
                elements=(Element(text="0.21s", role=Role.LIVE_PRIMARY, color=ColorToken.SPLIT),),
            ),
        ),
        width=1920,
        height=1080,
        scale=CellScale.for_cell(1080),
        theme=theme,
    )
    red, green, blue = theme.split
    rule = _rule(doc, ".tok-split")
    assert f"rgb({red},{green},{blue})" in rule


def test_the_two_themes_do_not_render_the_same_ink() -> None:
    """The property the DefaultTemplate tests were really guarding: that
    --theme is not decoration. If both themes produced the same CSS the
    flag would be a lie, and no other test would notice."""
    groups = (
        Group(
            anchor=Anchor.TOP_LEFT,
            flow=Flow.ROW,
            elements=(Element(text="7/32", role=Role.LIVE_PRIMARY),),
        ),
    )
    kwargs = {"width": 1920, "height": 1080, "scale": CellScale.for_cell(1080)}
    assert single_html(groups, theme=overlay_theme.load_theme("splitsmith"), **kwargs) != single_html(
        groups, theme=overlay_theme.load_theme("clean"), **kwargs
    )


@pytest.mark.parametrize("face", ["splitsmith-mono", "splitsmith-display"])
def test_both_brand_faces_are_still_bundled(face: str, tmp_path) -> None:
    """Both brand faces have to reach disk from the installed package.

    The display face has no Python caller -- ``overlay_html`` reaches
    Antonio through a ``file://`` URL in an ``@font-face`` rule, so a
    wheel that stopped shipping it would fail in a browser, off the back
    of a stylesheet, rather than anywhere a stack trace would help.
    Materializing it here is the cheap Python-side proof that the file
    is present.
    """
    path = overlay_text.materialize_font(face, tmp_path)

    assert path.is_file()
    assert path.stat().st_size > 0


def test_splitsmith_theme_carries_surface_and_subtle() -> None:
    """The share card (spec 2026-08-09) paints a surface fill and a
    dimmer label grey. Both come from index.css via the build script,
    never hardcoded in a renderer."""
    theme = load_theme("splitsmith")
    assert theme.surface == (0x14, 0x17, 0x1C)  # --color-surface
    assert theme.subtle == (0x6B, 0x70, 0x79)  # --color-subtle


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
