"""Your brand in a Look: a logo and a line the Look carries, drawn as the
centrepiece of the title page and the closing card (the branding work,
PR 2). The person making the video chooses it once, in the Look, and every
export that uses the Look carries it: a club, a personal brand, a sponsor.

The logo is a file in the Look's ``brand/`` folder, named by its content
(``brand-<12hex>.<ext>``), written by :func:`save_brand_logo` after the
bytes are sniffed and sized the way a shooter's logo is. Desktop only for
now: hosted Looks have no file store (``db.looks`` refuses a brand logo).
:func:`brand_json` is what a card's template receives as ``data.brand``;
a Look without a brand sends no key at all, so its cards render and key
exactly as before.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

from .composition import BrandMark
from .identity import LOGO_MAX_BYTES, LOGO_MAX_SIDE
from .looks import BRAND_DIR, BRAND_FILE_RE, Look

#: The card slots that draw the brand.
BRAND_SLOTS = frozenset({"title_page", "closing"})

_FORMATS = {"PNG": "png", "JPEG": "jpeg", "MPO": "jpeg", "WEBP": "webp"}


class BrandError(ValueError):
    """An upload refused, with the reason in the user's words."""


def check_brand_logo(data: bytes) -> str:
    """Check ``data`` as a brand logo; the content name it is stored under
    (``brand-<12hex>.<ext>``). ``BrandError`` with the user's reason."""
    from PIL import Image, UnidentifiedImageError

    if len(data) > LOGO_MAX_BYTES:
        raise BrandError("The logo is larger than 2 MB.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            fmt = (image.format or "").upper()
            side = max(image.size)
            image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError) as exc:
        raise BrandError("The logo must be a PNG, JPEG or WebP image.") from exc
    ext = _FORMATS.get(fmt)
    if ext is None:
        raise BrandError("The logo must be a PNG, JPEG or WebP image.")
    if side > LOGO_MAX_SIDE:
        raise BrandError(f"The logo is over {LOGO_MAX_SIDE} px on a side.")
    return f"brand-{hashlib.sha256(data).hexdigest()[:12]}.{ext}"


def save_brand_logo(root: Path, data: bytes) -> str:
    """Check ``data`` and store it in ``root``'s ``brand/`` folder, named by
    its content; the file name ``look.json``'s ``brand.logo`` names."""
    name = check_brand_logo(data)
    folder = root / BRAND_DIR
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    if not target.is_file():
        partial = folder / f".{name}.part"
        partial.write_bytes(data)
        partial.replace(target)
    return name


def brand_path(look: Look) -> Path | None:
    """The Look's brand logo on disk: a content-named file in its own
    ``brand/`` folder that is not a symlink, or ``None``."""
    brand = look.manifest.brand
    if brand is None or not brand.logo or not BRAND_FILE_RE.fullmatch(brand.logo):
        return None
    path = look.root / BRAND_DIR / brand.logo
    if path.is_symlink() or not path.is_file():
        return None
    return path


def brand_json(
    look: Look | None, slot: str, fallback: BrandMark | None = None
) -> dict[str, str | None] | None:
    """``data.brand`` for a card of ``slot``: the logo's URL and the line,
    on the title page and the closing card of a Look that has a brand; else
    ``fallback``, the account's brand (spec 2026-10-08), as a whole: a
    Look's brand is never mixed with the account's. ``None`` everywhere else,
    which leaves the context as it always was."""
    if slot not in BRAND_SLOTS:
        return None
    return brand_mark_json(look, fallback)


def brand_mark_json(look: Look | None, fallback: BrandMark | None = None) -> dict[str, str | None] | None:
    """The brand as a template draws it, whatever the slot: the Look's when
    it has one, else ``fallback`` (the account's), as a whole; ``None`` when
    neither has a logo or a line. :func:`brand_json` gates it to the cards;
    the sting asks for it directly when the ``wipe`` logo spot is on."""
    brand = look.manifest.brand if look is not None else None
    if brand is not None and (brand.logo or brand.line):
        path = brand_path(look) if look is not None else None
        return {"logo": path.resolve().as_uri() if path is not None else None, "line": brand.line or None}
    if fallback is None or (fallback.logo_path is None and not fallback.line):
        return None
    logo = fallback.logo_path
    if logo is not None and (logo.is_symlink() or not logo.is_file()):
        logo = None
    if logo is None and not fallback.line:
        return None
    return {"logo": logo.resolve().as_uri() if logo is not None else None, "line": fallback.line or None}


__all__ = [
    "BRAND_SLOTS",
    "BrandError",
    "brand_json",
    "brand_mark_json",
    "brand_path",
    "check_brand_logo",
    "save_brand_logo",
]
