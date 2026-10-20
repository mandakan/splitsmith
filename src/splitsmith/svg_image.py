"""SVG as a logo: the check that makes one safe to accept, and the raster
copy the Pillow surfaces draw (spec 2026-10-20).

An SVG is a document, not a picture: it can carry script, event handlers,
HTML inside ``foreignObject`` and references to other files, and a Look
template loads a logo in Chromium. :func:`check_svg` admits an SVG only when
it is an image and nothing else, so the bytes can be stored as uploaded and
served under a strict policy. :func:`rasterize_svg` draws it with resvg for
the surfaces that compose with Pillow (summary logos, the watermark, the
palette's logo colours), capped on its longest side.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
#: The longest side of a rasterised logo. Every logo slot is far smaller
#: than this; the caller resizes down as it does for a raster logo.
RASTER_MAX_SIDE = 1024

_LEADING = re.compile(rb"^\s*(?:<\?xml[^>]*\?>\s*)?(?:<!--.*?-->\s*)*", re.DOTALL)
_FORBIDDEN_TAGS = frozenset({"script", "foreignobject", "iframe", "embed", "object", "audio", "video"})
_ANIMATION_TAGS = frozenset({"set", "animate", "animatemotion", "animatetransform", "animatecolor"})
_SAFE_DATA_URL = re.compile(r"^data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=\s]*$")
_CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE | re.DOTALL)
_LENGTH = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(px|pt|pc|mm|cm|in)?\s*$")
_ROOT_TAG = re.compile(r"<svg\b[^>]*>", re.IGNORECASE | re.DOTALL)
_ROOT_SIZE_ATTR = re.compile(r"\s(width|height)\s*=\s*([\"'])(.*?)\2", re.IGNORECASE | re.DOTALL)
#: The side the upload check draws at: enough to know resvg can draw it.
PROBE_SIDE = 8
_UNIT_PX = {None: 1.0, "px": 1.0, "pt": 4 / 3, "pc": 16.0, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96.0}

REFUSED = "The SVG must be a plain image: no script, no event handlers, no links to other files."


def is_svg(data: bytes) -> bool:
    """Whether ``data`` looks like an SVG document: after an optional XML
    declaration and comments, the first tag is ``<svg``."""
    head = _LEADING.sub(b"", data[:4096].lstrip(b"\xef\xbb\xbf"), count=1)
    return head[:4].lower() == b"<svg"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _check_url_value(value: str) -> None:
    v = value.strip()
    if not v or v.startswith("#"):
        return
    if _SAFE_DATA_URL.match(v):
        return
    raise ValueError(REFUSED)


def _check_css(text: str) -> None:
    if "@import" in text.lower() or "expression(" in text.lower():
        raise ValueError(REFUSED)
    for match in _CSS_URL.finditer(text):
        _check_url_value(match.group(2))


def check_svg(data: bytes) -> None:
    """Refuse ``data`` unless it is an SVG that is an image and nothing
    else; ``ValueError`` with the user's reason.

    Refused: a ``DOCTYPE`` or entity declaration, ``script``,
    ``foreignObject``, ``iframe``, ``embed``, ``object``, ``audio``,
    ``video``, an animation element with a ``href``, any ``on*`` attribute,
    any ``href`` that is not a fragment or a raster ``data:`` URL, and
    ``url(...)`` in styles that is not a fragment. The check is on the whole
    tree, so no wrapper element hides anything.
    """
    if not is_svg(data):
        raise ValueError("The logo must be a PNG, JPEG, WebP or SVG image.")
    lowered = data[: 64 * 1024].lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ValueError(REFUSED)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError("The SVG file is not well formed.") from exc
    if _local(root.tag) != "svg":
        raise ValueError("The logo must be a PNG, JPEG, WebP or SVG image.")
    for element in root.iter():
        if not isinstance(element.tag, str):
            # A comment or processing instruction node: nothing to run.
            continue
        tag = _local(element.tag)
        if tag in _FORBIDDEN_TAGS:
            raise ValueError(REFUSED)
        has_href = False
        for name, value in element.attrib.items():
            local = _local(name)
            if local.startswith("on"):
                raise ValueError(REFUSED)
            if local == "href":
                has_href = True
                _check_url_value(value)
            elif local == "style":
                _check_css(value)
        if tag in _ANIMATION_TAGS and has_href:
            raise ValueError(REFUSED)
        if tag == "style" and element.text:
            _check_css(element.text)
    # resvg has to be able to draw it: a size it cannot resolve or a
    # feature it lacks is found here, with a reason, not on the first card.
    rasterize_svg(data, max_side=PROBE_SIDE)


def _normalize_size(text: str) -> str:
    """The root's ``width`` / ``height`` in CSS pixels: resvg refuses a
    physical unit (``mm``, ``pt``, ...), which drawing tools write by
    default, even beside a ``viewBox``."""

    def fix(tag: re.Match[str]) -> str:
        def attr(match: re.Match[str]) -> str:
            length = _LENGTH.match(match.group(3))
            if length is None or length.group(2) in (None, "px"):
                return match.group(0)
            px = float(length.group(1)) * _UNIT_PX[length.group(2)]
            return f' {match.group(1)}="{px:g}"'

        return _ROOT_SIZE_ATTR.sub(attr, tag.group(0))

    return _ROOT_TAG.sub(fix, text, count=1)


def _intrinsic_aspect(root: ET.Element) -> float:
    """Width over height from ``viewBox``, else ``width`` / ``height`` in
    absolute units; 1.0 when the file says nothing usable."""
    view_box = root.get("viewBox")
    if view_box:
        parts = view_box.replace(",", " ").split()
        if len(parts) == 4:
            try:
                w, h = float(parts[2]), float(parts[3])
            except ValueError:
                w = h = 0.0
            if w > 0 and h > 0:
                return w / h
    dims = []
    for attr in ("width", "height"):
        match = _LENGTH.match(root.get(attr) or "")
        if match is None:
            return 1.0
        dims.append(float(match.group(1)) * _UNIT_PX[match.group(2)])
    if dims[0] > 0 and dims[1] > 0:
        return dims[0] / dims[1]
    return 1.0


def rasterize_svg(data: bytes, *, max_side: int = RASTER_MAX_SIDE) -> Image.Image:
    """``data`` drawn with resvg as an RGBA image whose longest side is
    ``max_side``, keeping the SVG's own aspect. ``ValueError`` when resvg
    cannot draw it."""
    import resvg_py
    from PIL import Image

    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError("The SVG file is not well formed.") from exc
    aspect = _intrinsic_aspect(root)
    size = {"width": max_side} if aspect >= 1.0 else {"height": max_side}
    try:
        png = resvg_py.svg_to_bytes(
            svg_string=_normalize_size(data.decode("utf-8", errors="replace")), **size
        )
    except Exception as exc:  # noqa: BLE001 -- resvg raises its own classes; the caller wants a reason
        raise ValueError(f"The SVG could not be drawn: {exc}") from exc
    with Image.open(io.BytesIO(bytes(png))) as image:
        return image.convert("RGBA")


def open_image(path: Path) -> Image.Image:
    """A Pillow image for a logo file of any accepted kind: an SVG is
    rasterised (:func:`rasterize_svg`), anything else opened and copied so
    the file is closed on return. Raises what ``Image.open`` raises, or
    ``ValueError`` for an SVG resvg cannot draw."""
    from PIL import Image

    if path.suffix.lower() == ".svg":
        return rasterize_svg(path.read_bytes())
    with Image.open(path) as source:
        return source.convert("RGBA")


__all__ = [
    "PROBE_SIDE",
    "RASTER_MAX_SIDE",
    "REFUSED",
    "check_svg",
    "is_svg",
    "open_image",
    "rasterize_svg",
]
