"""Font resolution for both overlays: naming one face, putting it on disk.

Extracted from ``overlay_render.py`` so the multi-shooter grid
(``compare/overlay_sprites.py``) could share it without a top-level
module importing from a subpackage to reach its own helpers.

It used to draw, too. Both overlays rasterized their counters and splits
with PIL, so this module carried a font loader, a preset table, a system
discovery list and a stroke+shadow text routine. None of that survives:
the grid moved to a browser render in #693 and the single-shooter export
followed in #684, and the running clock has always been an ffmpeg
``drawtext`` filter. Neither consumer takes a PIL font. What is left is
the part they both still need -- agreeing on *which* face, and producing
a real file for ffmpeg to open -- and this module no longer imports PIL
at all (issue #759).

``OverlayRenderError`` lives here because :func:`materialize_font` raises
it and moving it would make the import circular. It keeps its name -- it
is still the overlay pipeline's error -- and ``overlay_render``
re-exports the same class object, so ``except
overlay_render.OverlayRenderError`` clauses elsewhere are unaffected.
"""

import logging
from importlib.resources import files
from pathlib import Path

logger = logging.getLogger(__name__)


class OverlayRenderError(RuntimeError):
    """Raised when the audit JSON is missing / malformed, when ffmpeg
    blows up, or when the trimmed clip can't be probed."""


#: Fonts shipped under ``splitsmith/data/fonts/`` (Antonio + JetBrains
#: Mono, SIL OFL 1.1), mapped from the name a caller asks for to the file
#: in the wheel. Bundling them is what makes an overlay render the same
#: typography on every machine with no system font dependency.
#:
#: Antonio is a variable font. Selecting its weight used to happen here,
#: through PIL's named-instance API; ``overlay_html``'s ``@font-face``
#: rule declares ``font-weight: 400 700`` and lets the browser do it, so
#: there is nothing left for Python to set.
_BUNDLED_FONTS: dict[str, str] = {
    "splitsmith-mono": "JetBrainsMono-Bold.ttf",
    "splitsmith-display": "Antonio-VariableFont.ttf",
}


def materialize_font(font_name: str, dest_dir: Path) -> Path:
    """Copy a bundled font to a real path that outlives this call.

    ffmpeg's ``drawtext`` needs ``fontfile=`` to name a path that still
    exists when ffmpeg opens it, and ``importlib.resources.as_file`` may
    be handing back a temp file that is unlinked when its context closes.
    """
    key = font_name.lower()
    filename = _BUNDLED_FONTS.get(key)
    if filename is None:
        raise OverlayRenderError(
            f"unknown bundled font {font_name!r}; available: {', '.join(_BUNDLED_FONTS)}"
        )
    resource = files("splitsmith.data").joinpath("fonts").joinpath(filename)
    if not resource.is_file():
        raise OverlayRenderError(f"bundled font file missing for {font_name!r}: {filename}")
    data = resource.read_bytes()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename
    if dest_path.exists() and dest_path.stat().st_size == len(data):
        return dest_path
    dest_path.write_bytes(data)
    return dest_path


#: Face used when a caller names none. Bundled, so it exists on every
#: host including one with no system fonts at all.
FALLBACK_BUNDLED_FONT = "splitsmith-mono"

#: A resolved face: a bundled font's name, or a path to a real file.
#: Only the second is directly usable by ffmpeg's ``drawtext`` (see
#: :func:`overlay_font_file`); a caller holding a real file already keeps
#: it.
OverlayFace = str | Path


def resolve_overlay_face(font_name: str | None) -> OverlayFace:
    """Resolve ``font_name`` to one face, for *both* halves of an overlay.

    Each overlay is drawn twice over: the counters and splits through
    headless Chromium, the running clock through ffmpeg ``drawtext``. Two
    different font loaders, which is why resolution happens once, here,
    and both halves consume the result. Left to themselves they
    disagreed -- back when the sprite half was PIL, the ``clean`` theme
    sent it hunting through system fonts while the clock stayed pinned to
    the bundled mono, so one cell drew in two typefaces.

    **This no longer searches the host.** It used to try a preset table
    (``menlo``, ``sf-mono``, ...) and then a generic discovery list
    before falling back to the bundled face. Nothing under ``src/``
    passes anything but a bundled name, so every one of those branches
    was unreachable in practice, and the half that could still honour a
    system font -- the browser -- stopped doing so in #693 when
    ``overlay_html`` began declaring the bundled faces unconditionally.
    Resolving a system name now would produce a face only one half could
    draw with, so an unknown name is an error rather than a hunt.
    """
    if font_name is None:
        return FALLBACK_BUNDLED_FONT
    key = font_name.lower()
    if key not in _BUNDLED_FONTS:
        raise OverlayRenderError(f"unknown font_name {font_name!r}; available: {', '.join(_BUNDLED_FONTS)}")
    logger.debug("overlay font %r: using bundled %s", font_name, key)
    return key


def overlay_font_file(face: OverlayFace, dest_dir: Path) -> Path:
    """A real filesystem path for a face, for ffmpeg's ``drawtext``.

    A discovered face is already a path. A bundled one is materialized
    (see :func:`materialize_font`), because ``drawtext`` opens the file
    itself long after this call.
    """
    if isinstance(face, Path):
        return face
    return materialize_font(face, dest_dir)
