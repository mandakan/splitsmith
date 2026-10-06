"""Color palette for the alpha overlay renderer.

Palettes come from Looks (``splitsmith.looks``): ``load_theme(name)``
reads ``<look>/look.json``'s ``colors``. The two shipped Looks are
``splitsmith`` (tokens lifted from the web UI's ``index.css`` by
``scripts/build_overlay_theme.py``, so the overlay cannot drift from the
design system) and ``clean`` (neutral white on black). A user Look under
``~/.splitsmith/looks/`` is a theme too.

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

from dataclasses import dataclass
from typing import Literal

from .looks import Look, LookError, LookNotFoundError, load_look

ThemeName = Literal["splitsmith", "clean"]
"""The typed pair the export API accepts today. The CLI and the renderers
take any installed Look (``load_theme`` accepts any name); widening the
API is #1246."""

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

    name: str
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


def theme_for(look: Look) -> OverlayTheme:
    """The palette a Look declares (``look.json``'s ``colors``)."""
    c = look.manifest.colors
    return OverlayTheme(
        name=look.name,
        ink=c["ink"],
        split=c["split"],
        split_good=c["split_good"],
        stroke=c["stroke"],
        accent=c["accent"],
        accent_fill=c["accent_fill"],
        accent_text=c["accent_text"],
        rule=c["rule"],
        muted=c["muted"],
        ink_2=c["ink_2"],
        surface=c["surface"],
        subtle=c["subtle"],
    )


def load_theme(name: str) -> OverlayTheme:
    """Resolve a Look name to its palette. Any installed Look, shipped or
    the user's. ``OverlayThemeError`` for an unknown name, so the callers
    that predate Looks keep the exception they handle."""
    try:
        return theme_for(load_look(name))
    except LookNotFoundError as exc:
        raise OverlayThemeError(str(exc)) from exc
    except LookError as exc:
        raise OverlayThemeError(f"Look {name!r} cannot be used: {exc}") from exc
