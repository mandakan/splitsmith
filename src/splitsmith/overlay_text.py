"""Font resolution and shadowed text drawing, shared by both overlays.

Extracted from ``overlay_render.py`` so the multi-shooter grid
(``compare/overlay_sprites.py``) can draw the same typography without a
top-level module importing from a subpackage to reach its own helpers.
This is a move: every function body here is byte-identical to the one it
replaced, and ``overlay_render`` re-exports them so its callers and its
tests keep reaching them at the old names.

``OverlayRenderError`` moved with them because ``_load_font`` raises it
and leaving it behind would make the import circular. It keeps its name
-- it is still the overlay pipeline's error -- and ``overlay_render``
re-exports the same class object, so ``except`` clauses elsewhere are
unaffected.
"""

import contextlib
import logging
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

# Track which (font_name, tier) pairs we've already logged so the
# resolver doesn't spam one line per frame. Cleared via
# ``reset_font_log_cache()`` in tests; otherwise process-lifetime.
_LOGGED_FONT_TIERS: set[tuple[str | None, str]] = set()


def reset_font_log_cache() -> None:
    """Test-only: forget which font-tier choices have been logged."""
    _LOGGED_FONT_TIERS.clear()


def _log_font_choice(font_name: str | None, tier: str, source: str | None) -> None:
    key = (font_name, tier)
    if key in _LOGGED_FONT_TIERS:
        return
    _LOGGED_FONT_TIERS.add(key)
    if tier == "explicit":
        logger.debug("overlay font: using explicit path %s", source)
    elif tier == "bundled":
        logger.debug("overlay font %r: using bundled %s", font_name, source)
    elif tier == "preset-found":
        logger.info("overlay font %r resolved to system path %s", font_name, source)
    elif tier == "fallback":
        logger.warning(
            "overlay font %r unavailable; using system fallback %s",
            font_name,
            source,
        )
    elif tier == "pil-default":
        logger.warning(
            "overlay font %r unavailable and no system fallback present; "
            "falling back to PIL's built-in bitmap font (overlay will look low-res). "
            "Install DejaVu Sans Mono (Debian/Ubuntu: ``apt install fonts-dejavu-core``) "
            "or pass an explicit ``font_path``.",
            font_name,
        )


class OverlayRenderError(RuntimeError):
    """Raised when the audit JSON is missing / malformed, when ffmpeg
    blows up, or when the trimmed clip can't be probed."""


# Bundled fonts shipped under ``splitsmith/data/fonts/``. The
# ``splitsmith-*`` presets here resolve to real TTFs in the wheel
# (Antonio + JetBrains Mono, SIL OFL 1.1), so the design-system overlay
# theme renders the same typography across every machine -- no system
# font dependency. ``variation`` is the named instance for variable
# fonts (Antonio ships as a variable wght axis); ``None`` means a static
# font where setting an axis is a no-op.
@dataclass(frozen=True)
class _BundledFont:
    filename: str
    variation: str | None = None


_BUNDLED_FONTS: dict[str, _BundledFont] = {
    "splitsmith-mono": _BundledFont("JetBrainsMono-Bold.ttf"),
    "splitsmith-display": _BundledFont("Antonio-VariableFont.ttf", variation="Bold"),
}


# Named font presets the user can select without hunting for a path.
# Order inside each tuple is preferred-first (bold variants beat regular for
# legibility against busy backgrounds). Unknown / missing files fall through
# to the generic fallback list below.
_FONT_PRESETS: dict[str, tuple[str, ...]] = {
    "menlo": ("/System/Library/Fonts/Menlo.ttc",),
    "monaco": ("/System/Library/Fonts/Monaco.ttf",),
    "sf-mono": (
        "/System/Library/Fonts/SFNSMono.ttf",
        "/Library/Fonts/SF-Mono-Bold.otf",
        "/Library/Fonts/SF-Mono-Regular.otf",
    ),
    "sf-pro": (
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/SFNSDisplay.ttf",
    ),
    "helvetica": ("/System/Library/Fonts/Helvetica.ttc",),
    "dejavu-mono": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ),
    "consolas": (
        "C:/Windows/Fonts/consolab.ttf",
        "C:/Windows/Fonts/consola.ttf",
    ),
    "courier": (
        "C:/Windows/Fonts/courbd.ttf",
        "C:/Windows/Fonts/cour.ttf",
    ),
}

_FONT_FALLBACKS: tuple[str, ...] = (
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
    "/Library/Fonts/Andale Mono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    # Windows: Consolas ships with Vista+, Courier New / Lucida Console are
    # always present. PIL accepts forward slashes here on Windows too.
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/lucon.ttf",
    "C:/Windows/Fonts/cour.ttf",
)


def available_font_names() -> tuple[str, ...]:
    """Preset font names accepted by :func:`_load_font` / template kwargs.
    Exposed so a future template config UI can offer a real picker."""
    return tuple(_BUNDLED_FONTS.keys()) + tuple(_FONT_PRESETS.keys())


def _load_bundled_font(name: str, size: int) -> ImageFont.FreeTypeFont | None:
    """Resolve a ``splitsmith-*`` preset to a PIL font. Returns ``None`` if
    the name isn't bundled or the file is missing (shouldn't happen for an
    installed wheel; defensive for source-tree edits).

    Variable fonts get their named instance (e.g. ``Bold``) applied after
    load so callers don't have to think about wght axes. The
    ``as_file`` context exits before this function returns, but PIL has
    already mmap'd the file by then -- safe for static layouts; the
    Pillow team treats this as supported.
    """
    spec = _BUNDLED_FONTS.get(name)
    if spec is None:
        return None
    # Chain ``joinpath`` calls -- ``MultiplexedPath.joinpath`` only accepts
    # one path segment per call, unlike ``pathlib.Path.joinpath``.
    resource = files("splitsmith.data").joinpath("fonts").joinpath(spec.filename)
    if not resource.is_file():
        return None
    with as_file(resource) as p:
        font = ImageFont.truetype(str(p), size=size)
    if spec.variation is not None:
        # Variable-font axes; quietly accept static fallback if the named
        # instance isn't present (older Pillow / hand-substituted TTF).
        with contextlib.suppress(Exception):
            font.set_variation_by_name(spec.variation.encode())
    return font


def materialize_font(font_name: str, dest_dir: Path) -> Path:
    """Copy a bundled font to a real path that outlives this call.

    ``_load_font`` hands PIL an mmap'd resource, which is fine for a
    process that draws and exits. ffmpeg's ``drawtext`` is a different
    consumer: it needs ``fontfile=`` to name a path that still exists
    when ffmpeg opens it, and ``importlib.resources.as_file`` may be
    handing back a temp file that is unlinked when its context closes.
    """
    key = font_name.lower()
    spec = _BUNDLED_FONTS.get(key)
    if spec is None:
        raise OverlayRenderError(
            f"unknown bundled font {font_name!r}; available: {', '.join(_BUNDLED_FONTS)}"
        )
    resource = files("splitsmith.data").joinpath("fonts").joinpath(spec.filename)
    if not resource.is_file():
        raise OverlayRenderError(f"bundled font file missing for {font_name!r}: {spec.filename}")
    data = resource.read_bytes()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / spec.filename
    if dest_path.exists() and dest_path.stat().st_size == len(data):
        return dest_path
    dest_path.write_bytes(data)
    return dest_path


def _load_font(
    font_path: Path | None,
    size: int,
    *,
    font_name: str | None = None,
) -> ImageFont.ImageFont:
    if font_path is not None:
        _log_font_choice(font_name, "explicit", str(font_path))
        return ImageFont.truetype(str(font_path), size=size)
    if font_name is not None:
        key = font_name.lower()
        bundled = _load_bundled_font(key, size)
        if bundled is not None:
            _log_font_choice(font_name, "bundled", key)
            return bundled
        if key in _BUNDLED_FONTS:
            # Bundled name resolved but the file is missing -- fall through
            # to generic discovery rather than fail; surface a clear error
            # only if the system fallback also can't load.
            pass
        elif key not in _FONT_PRESETS:
            raise OverlayRenderError(
                f"unknown font_name {font_name!r}; " f"available: {', '.join(available_font_names())}"
            )
        for candidate in _FONT_PRESETS.get(key, ()):
            p = Path(candidate)
            if p.exists():
                _log_font_choice(font_name, "preset-found", str(p))
                return ImageFont.truetype(str(p), size=size)
        # Named preset asked for but no file found -- fall through to the
        # generic discovery list rather than crashing the export.
    for candidate in _FONT_FALLBACKS:
        p = Path(candidate)
        if p.exists():
            _log_font_choice(font_name, "fallback", str(p))
            return ImageFont.truetype(str(p), size=size)
    _log_font_choice(font_name, "pil-default", None)
    return ImageFont.load_default()


def _draw_text_with_shadow(
    draw: ImageDraw.ImageDraw,
    canvas: Image.Image,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    *,
    stroke_width: int = 2,
    shadow_offset: int = 3,
    shadow_blur: int = 6,
    stroke_color: tuple[int, int, int] = (0, 0, 0),
    shadow_color: tuple[int, int, int] = (0, 0, 0),
) -> None:
    """Stroke + soft drop shadow so text reads on bright/busy backgrounds.

    The shadow is rendered into a tight per-text scratch layer (textbbox
    plus padding for the blur kernel) and composited onto ``canvas`` --
    cheaper than a full-frame blur and identical visually. The foreground
    glyph is then drawn with a crisp black stroke. Shadow alpha tracks
    the foreground alpha so the last-split fade stays clean.
    """
    x, y = xy
    fg_alpha = fill[3]
    if fg_alpha <= 0:
        return
    shadow_alpha = int(fg_alpha * 0.65)

    if shadow_alpha > 0:
        bbox = draw.textbbox(xy, text, font=font, stroke_width=stroke_width)
        pad = max(1, shadow_blur * 2 + shadow_offset + stroke_width)
        sx0, sy0 = bbox[0] - pad, bbox[1] - pad
        sx1, sy1 = bbox[2] + pad, bbox[3] + pad
        sw, sh = sx1 - sx0, sy1 - sy0
        if sw > 0 and sh > 0:
            shadow_img = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
            sd = ImageDraw.Draw(shadow_img)
            sd.text(
                (x - sx0 + shadow_offset, y - sy0 + shadow_offset),
                text,
                font=font,
                fill=(*shadow_color, shadow_alpha),
                stroke_width=stroke_width,
                stroke_fill=(*shadow_color, shadow_alpha),
            )
            if shadow_blur > 0:
                shadow_img = shadow_img.filter(ImageFilter.GaussianBlur(shadow_blur))
            canvas.alpha_composite(shadow_img, (sx0, sy0))

    draw.text(
        xy,
        text,
        font=font,
        fill=fill,
        stroke_width=stroke_width,
        stroke_fill=(*stroke_color, fg_alpha),
    )
