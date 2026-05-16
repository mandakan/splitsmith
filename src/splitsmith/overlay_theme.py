"""Color palette for the alpha overlay renderer.

Two presets ship today:

- ``splitsmith`` (default): tokens lifted from the web UI's
  ``src/splitsmith/ui_static/src/styles/index.css`` ``@theme`` block --
  the Shot Timer brand palette. Built into
  ``src/splitsmith/data/overlay_theme.json`` by
  ``scripts/build_overlay_theme.py`` so the overlay can't silently drift
  from the rest of the design system.
- ``clean``: a neutral white-on-amber palette with a pure-black stroke.
  No brand colours; useful when the overlay needs to read against any
  background without identifying the tool.

The JSON mirror is intentional: parsing CSS at runtime would mean a CSS
parser as a runtime dep, and the overlay only needs a handful of tokens.
Re-run the build script after touching ``index.css``.

Down the line, swapping PIL for a Skia-based renderer (proper HarfBuzz
text shaping, variable-font axes) lets the ``splitsmith`` theme actually
render Antonio condensed instead of falling back to the system mono. The
font names are already in the JSON so that swap doesn't need new tokens.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import Literal

ThemeName = Literal["splitsmith", "clean"]
"""Stable identifiers exposed in the export request + CLI."""

THEME_NAMES: tuple[ThemeName, ...] = ("splitsmith", "clean")

RGB = tuple[int, int, int]


class OverlayThemeError(RuntimeError):
    """Raised when the design-system JSON is missing or malformed."""


@dataclass(frozen=True)
class OverlayTheme:
    """Palette + font name hints used by ``DefaultTemplate``.

    All colors are 8-bit RGB tuples; alpha is applied at draw time by the
    template (the last-split label fades, shadows track foreground alpha,
    etc.). Font names are advisory: today they only matter when the caller
    also passes ``font_name=None`` so the template can fall back to a
    sensible default for the theme.
    """

    name: ThemeName
    ink: RGB
    split: RGB
    stroke: RGB
    accent: RGB
    font_display: str
    font_mono: str

    @property
    def shadow(self) -> RGB:
        """Drop shadow color. Today this matches the stroke -- a dark halo
        reads cleanly on both bright and busy backgrounds. Kept as a
        property so a future variant can carry an explicit token without
        churning callers."""
        return self.stroke


_CLEAN = OverlayTheme(
    name="clean",
    ink=(255, 255, 255),
    split=(255, 220, 80),
    stroke=(0, 0, 0),
    accent=(255, 45, 45),
    font_display="Antonio",
    font_mono="JetBrains Mono",
)


def _load_splitsmith() -> OverlayTheme:
    try:
        with (
            resources.files("splitsmith.data")
            .joinpath("overlay_theme.json")
            .open("r", encoding="utf-8") as fh
        ):
            data = json.load(fh)
    except (FileNotFoundError, OSError) as exc:
        raise OverlayThemeError(
            "overlay_theme.json missing; run scripts/build_overlay_theme.py"
        ) from exc
    except json.JSONDecodeError as exc:
        raise OverlayThemeError(f"overlay_theme.json is not valid JSON: {exc}") from exc

    colors = data.get("colors") or {}
    fonts = data.get("fonts") or {}
    try:
        return OverlayTheme(
            name="splitsmith",
            ink=_rgb(colors, "ink"),
            split=_rgb(colors, "split"),
            stroke=_rgb(colors, "stroke"),
            accent=_rgb(colors, "accent"),
            font_display=str(fonts.get("display", "Antonio")),
            font_mono=str(fonts.get("mono", "JetBrains Mono")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise OverlayThemeError(f"overlay_theme.json malformed: {exc}") from exc


def _rgb(colors: dict, role: str) -> RGB:
    raw = colors[role]
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise ValueError(f"{role!r} must be a 3-element list, got {raw!r}")
    r, g, b = (int(v) for v in raw)
    for v in (r, g, b):
        if not 0 <= v <= 255:
            raise ValueError(f"{role!r} channel out of 0..255 range: {raw!r}")
    return r, g, b


def load_theme(name: ThemeName) -> OverlayTheme:
    """Resolve a theme name to its palette. Cached at the module level so
    repeated stage exports don't re-read the JSON. Raises
    ``OverlayThemeError`` for unknown names or a missing splitsmith JSON
    artefact."""
    if name == "clean":
        return _CLEAN
    if name == "splitsmith":
        global _SPLITSMITH
        if _SPLITSMITH is None:
            _SPLITSMITH = _load_splitsmith()
        return _SPLITSMITH
    raise OverlayThemeError(f"unknown theme {name!r}; expected one of {THEME_NAMES}")


_SPLITSMITH: OverlayTheme | None = None
