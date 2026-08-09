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

Bundled fonts (Antonio + JetBrains Mono, SIL OFL 1.1) live under
``src/splitsmith/data/fonts/`` so overlay text renders deterministic
typography without depending on whatever the host machine happens to
have installed. The numeric readouts (the live sprites and the stage
summary alike, both ``@font-face``-declared CSS -- see
``overlay_html.py``) use JetBrains Mono Bold; Antonio is
``.role-identity``'s live condensed face for the stage summary's
shooter-name row (issue #683 Task 7b), not a placeholder waiting on a
future consumer -- a competitor's name is the one string in a cell that
is not a number, and condensed genuinely matters where names run long
and cells run narrow.

The stage summary itself moved off PIL to headless Chromium rasterizing
real CSS (issue #683's amendment) specifically to get a genuine box
model and, as a side effect, proper text shaping (kerning, ligatures,
condensed-face width control) for exactly the reason this paragraph used
to describe as a hypothetical Skia swap. Issue #693 then took the live
per-tile sprites the same way (``compare/overlay_live.py``), so **no
renderer in this pipeline is PIL any more** and every colour token here
reaches the picture as CSS. The one remaining non-CSS consumer is the
running clock, an ffmpeg ``drawtext`` filter that reads only ``ink`` and
``stroke`` (see ``mp4_grid._clock_filters``) -- a token added here for
CSS alone will not reach it.
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
    """Palette for the alpha overlay renderer.

    All colors are 8-bit RGB tuples. The pre-port PIL template applied
    alpha at draw time (the last-split label faded in and out, shadows
    tracked foreground alpha); the ported renderer draws through CSS
    instead and the last-split label is present-or-absent per run rather
    than fading (see ``overlay_single.run_groups``).

    A theme decides every colour in the overlay and nothing else. It used
    to carry ``font_display`` / ``font_mono`` from the JSON build too,
    but no renderer ever read them -- ``overlay_html`` names its bundled
    faces directly -- so they came out with the rest of the font
    machinery in issue #759. The build script still writes a ``fonts``
    block into the JSON; it documents the design system's font stack,
    and :func:`load_theme` ignores it.
    """

    name: ThemeName
    ink: RGB
    split: RGB
    split_good: RGB
    stroke: RGB
    accent: RGB
    #: The filled-plate variant of :attr:`accent` -- darker, so ink text
    #: on top of it reaches AA-large contrast (mirrors the web UI's
    #: ``--color-led-fill``: "slightly darker than --color-led so cream
    #: text reaches AA-large + survives red-green colorblindness"). Every
    #: :attr:`~splitsmith.overlay_layout.Emphasis.PLATE` background reads
    #: this, not :attr:`accent` -- see issue #683 Task 7c.
    accent_fill: RGB
    #: Body-size red text (10-14px) -- an unplated fault count reads
    #: this, not :attr:`accent`. The web UI's own comment on
    #: ``--color-led-text`` names the exact failure this token exists to
    #: avoid: "the saturated identity red is too thin for 10-12px running
    #: text". Measured on this branch before the fix: a small unplated
    #: accent glyph read 7.1% accent-coloured pixels against 33.9% stroke
    #: -- the stroke was eating the glyph.
    accent_text: RGB
    #: A hairline rule's own colour.
    rule: RGB
    #: Secondary/tertiary text -- what a caller used to fake by applying
    #: an arbitrary opacity to :attr:`ink` instead of reading a real
    #: token.
    muted: RGB
    #: A step down from :attr:`ink`, a step up from :attr:`muted` -- the
    #: web UI's own text ramp is ink / ink-2 / muted / subtle / whisper.
    #: Issue #683 Task 8's stage-summary labels ("SCORING", "SPLITS",
    #: "BEST", ...) want exactly this middle tone: bright enough to read
    #: as a real label with no text-stroke (the design drops the stroke
    #: for labels, text-shadow only), dim enough not to compete with the
    #: figure it sits above or beside.
    ink_2: RGB
    #: Plate fill behind a share-card stat cell (``--color-surface``).
    surface: RGB
    #: Dimmer label grey than :attr:`muted` (``--color-subtle``), for
    #: captions that must sit below a value without competing with it.
    subtle: RGB

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
    # No brand palette to draw from here, so this is a plain, defensible
    # "success green" -- (46, 204, 113), the Flat UI Colors "Emerald" --
    # picked for being a common, accessible semantic-success hue that
    # reads clearly against clean's black stroke and stays visually
    # distinct from both split's gold and accent's red.
    split_good=(46, 204, 113),
    stroke=(0, 0, 0),
    accent=(255, 45, 45),
    # No brand palette here either, so these mirror the *relationship*
    # the splitsmith theme's own three reds carry (a darker fill, a
    # lighter body-text tint) applied to clean's own accent hue rather
    # than inventing an unrelated one -- clean's accent already happens
    # to equal the brand's led red, so the same fill/text pair the brand
    # theme resolved from --color-led-fill/--color-led-text is reused
    # verbatim rather than re-derived.
    accent_fill=(220, 38, 38),
    accent_text=(255, 180, 180),
    # Brand-neutral dark and mid greys -- no hue borrowed from accent,
    # split or split_good, so a hairline or a muted label doesn't quietly
    # read as "for" one of those semantics.
    rule=(60, 60, 60),
    muted=(150, 150, 150),
    # Sits between ink (255,255,255) and muted (150,150,150) -- no hue
    # borrowed from accent/split/split_good, same discipline as rule/muted
    # above.
    ink_2=(205, 205, 205),
    # No brand palette here either. The clean theme's existing neutral
    # discipline: pure black for the plate fill, mid grey for the dimmer
    # caption tone.
    surface=(0, 0, 0),
    subtle=(128, 128, 128),
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
        raise OverlayThemeError("overlay_theme.json missing; run scripts/build_overlay_theme.py") from exc
    except json.JSONDecodeError as exc:
        raise OverlayThemeError(f"overlay_theme.json is not valid JSON: {exc}") from exc

    colors = data.get("colors") or {}
    try:
        return OverlayTheme(
            name="splitsmith",
            ink=_rgb(colors, "ink"),
            split=_rgb(colors, "split"),
            split_good=_rgb(colors, "split_good"),
            stroke=_rgb(colors, "stroke"),
            accent=_rgb(colors, "accent"),
            accent_fill=_rgb(colors, "accent_fill"),
            accent_text=_rgb(colors, "accent_text"),
            rule=_rgb(colors, "rule"),
            muted=_rgb(colors, "muted"),
            ink_2=_rgb(colors, "ink_2"),
            surface=_rgb(colors, "surface"),
            subtle=_rgb(colors, "subtle"),
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
