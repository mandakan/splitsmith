"""A Look's own font files (issue #1272, step 2): the upload's checks and
the ``fonts/`` folder they land in. Desktop only: hosted refuses an own
face (``db.looks``) until account assets and the template sandbox (#1266)
cover it.

A face is drawn twice over, by Chromium (``@font-face`` for the cards and
sprites) and by FreeType (ffmpeg ``drawtext`` for the clock), so only what
both read is accepted: one TrueType or OpenType font, no WOFF (FreeType
reads it only when built with brotli and zlib, which ``drawtext`` cannot
be relied on to be) and no collection (``@font-face`` takes one face). The
bytes are sniffed, never the client's file name, and opened with Pillow's
FreeType before they are written, which is also where the family name the
editor shows comes from. The file is named by its content, so a new font
is a new value in ``look.json`` and every cache key that reads it moves.

The licence is the user's to hold; the editor says so at upload.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from .fonts import OWN_DIR, OWN_FILE_RE, own_value

#: The largest file the upload takes. A condensed display face is a few
#: hundred kilobytes; a CJK face, which a card has no use for, is tens of MB.
MAX_FONT_BYTES = 2 * 1024 * 1024

_TTF_MAGIC = (b"\x00\x01\x00\x00", b"true")
_OTF_MAGIC = b"OTTO"


class OwnFontError(ValueError):
    """An upload this module refuses, with the reason in the user's words."""


@dataclass(frozen=True)
class OwnFont:
    #: What ``look.json``'s ``fonts`` names it by: ``own:font-<12hex>.<ext>``.
    value: str
    #: The file under the Look's ``fonts/`` folder.
    file: str
    #: The family name the font itself declares, for the editor's list.
    family: str


def _suffix(data: bytes) -> str:
    head = data[:4]
    if head in (b"wOFF", b"wOF2"):
        raise OwnFontError("This is a WOFF font. Upload the TTF or OTF file it was made from.")
    if head == b"ttcf":
        raise OwnFontError("This is a font collection. Upload a single TTF or OTF font.")
    if head in _TTF_MAGIC:
        return ".ttf"
    if head == _OTF_MAGIC:
        return ".otf"
    raise OwnFontError("This is not a TrueType or OpenType font (TTF or OTF).")


def _family(data: bytes) -> str:
    """The family FreeType reads from ``data``; refuses what it cannot open."""
    from PIL import ImageFont

    try:
        face = ImageFont.truetype(io.BytesIO(data), size=24)
        family, _style = face.getname()
    except (OSError, ValueError) as exc:
        raise OwnFontError("The font could not be read. It may be damaged or not a font at all.") from exc
    return family or "Unnamed font"


def save_font(root: Path, data: bytes) -> OwnFont:
    """Check ``data`` and write it into ``root``'s ``fonts/`` folder, named by
    its content; the same bytes twice are the same file."""
    if len(data) > MAX_FONT_BYTES:
        raise OwnFontError("The font is larger than 2 MB.")
    suffix = _suffix(data)
    family = _family(data)
    file = f"font-{hashlib.sha256(data).hexdigest()[:12]}{suffix}"
    folder = root / OWN_DIR
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / file
    if not target.is_file():
        partial = folder / f".{file}.part"
        partial.write_bytes(data)
        partial.replace(target)
    return OwnFont(value=own_value(file), file=file, family=family)


def list_fonts(root: Path) -> list[OwnFont]:
    """Every own face in ``root``'s ``fonts/`` folder that still reads, by
    family name. A file that does not (copied in by hand) is left out."""
    folder = root / OWN_DIR
    if not folder.is_dir():
        return []
    out: list[OwnFont] = []
    for path in sorted(folder.iterdir()):
        if not OWN_FILE_RE.fullmatch(path.name) or not path.is_file() or path.is_symlink():
            continue
        try:
            family = _family(path.read_bytes())
        except OwnFontError:
            continue
        out.append(OwnFont(value=own_value(path.name), file=path.name, family=family))
    return sorted(out, key=lambda f: (f.family.lower(), f.file))


def own_font_path(root: Path, file: str) -> Path | None:
    """The own face ``file`` under ``root``, or ``None`` when the name is not
    one this module writes or the file is gone."""
    if not OWN_FILE_RE.fullmatch(file):
        return None
    path = root / OWN_DIR / file
    return path if path.is_file() and not path.is_symlink() else None


__all__ = ["MAX_FONT_BYTES", "OwnFont", "OwnFontError", "list_fonts", "own_font_path", "save_font"]
