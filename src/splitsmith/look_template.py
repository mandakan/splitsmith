"""The template contract (spec 2026-10-06, section 1).

A Look template is one HTML document. Before any of its scripts run the
renderer assigns ``window.splitsmith`` (see :class:`TemplateContext`);
the document may define ``window.duration()`` (seconds; 0 for a still)
and ``window.seek(seconds)``. This slice renders every template at
``seek(0)``.

``engine`` carries what the shipped templates need to draw exactly what
``overlay_html`` draws: the stylesheet text (``css``) and the fit
policy's legibility floor (``min_font_size``). ``assets.shared`` is a
``file://`` URL to ``data/looks/_shared``, where ``fit.js`` and
``cell.js`` live. A custom Look may ignore all of it and draw
``data.card`` its own way.

Pure: builds JSON-able values and a path; nothing here opens a browser.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from .identity import LOGO_MAX_BYTES, ResolvedIdentity
from .looks import shared_dir
from .overlay_layout import MIN_FONT_SIZE, Element, Group
from .overlay_theme import OverlayTheme

logger = logging.getLogger(__name__)


class TemplateContext(BaseModel):
    """``window.splitsmith`` as the template sees it."""

    model_config = ConfigDict(frozen=True)

    theme: dict[str, str]
    data: dict[str, Any]
    size: dict[str, int]
    fps: float
    engine: dict[str, Any]
    assets: dict[str, str]

    def init_script(self) -> str:
        """The statement the renderer installs as an init script. ``</``
        is split so a value containing ``</script>`` cannot end the
        script element the browser parses this into."""
        payload = json.dumps(self.model_dump(), ensure_ascii=False).replace("</", "<\\/")
        return f"window.splitsmith = {payload};"


def _hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"#{r:02x}{g:02x}{b:02x}"


def theme_tokens(theme: OverlayTheme) -> dict[str, str]:
    """Every palette field as ``#rrggbb``, plus the derived ``shadow``."""
    tokens = {f.name: _hex(getattr(theme, f.name)) for f in dataclasses.fields(theme) if f.name != "name"}
    tokens["shadow"] = _hex(theme.shadow)
    return tokens


def _element_json(element: Element) -> dict[str, Any]:
    return {
        "role": element.role.value,
        "text": element.text,
        "emphasis": element.emphasis.value,
        "caption": element.caption,
        "color": None if element.color is None else element.color.value,
        "unit": element.unit,
        "drop_priority": element.drop_priority,
    }


def group_json(group: Group) -> dict[str, Any]:
    """A :class:`Group` as the plain values ``cell.js`` reads. Field for
    field what ``overlay_html._group_div`` reads."""
    return {
        "anchor": group.anchor.value,
        "flow": group.flow.value,
        "divider": group.divider,
        "align": group.align,
        "gap": group.gap,
        "margin_top": group.margin_top,
        "elements": [_element_json(e) for e in group.elements],
    }


def shooter_json(shooter: ResolvedIdentity | Any) -> dict[str, Any]:
    """One shooter as ``data.shooters[i]``: label, accent, club, and the
    logo as a ``file://`` URL when the file exists and is within
    ``LOGO_MAX_BYTES``, else ``None`` with a warning (a missing logo costs
    the logo, never the card). Accepts anything with those four
    attributes (``composition.CompositionShooter`` carries the same)."""
    logo: str | None = None
    path = getattr(shooter, "logo_path", None)
    if path is not None:
        try:
            size = Path(path).stat().st_size
        except OSError:
            logger.warning("identity: logo %s is missing; the card draws without it", path)
        else:
            if 0 < size <= LOGO_MAX_BYTES:
                logo = Path(path).resolve().as_uri()
            else:
                logger.warning("identity: logo %s is %d bytes; the card draws without it", path, size)
    return {"label": shooter.label, "accent": shooter.accent, "club": shooter.club, "logo": logo}


def engine_block(*, css: str) -> dict[str, Any]:
    return {"css": css, "min_font_size": MIN_FONT_SIZE}


def shared_url() -> str:
    return shared_dir().resolve().as_uri()


def template_digest(template: Path, context: TemplateContext, *, fps: float, engine_version: str) -> str:
    """What a template render depends on, hashed: the template's bytes,
    the whole context, the frame rate and the engine. The segment cache
    keys a motion clip by this instead of by the clip's own content, so a
    cached segment is found before any frame is rendered."""
    digest = hashlib.sha256()
    digest.update(template.read_bytes())
    digest.update(context.init_script().encode("utf-8"))
    digest.update(f"|fps={fps!r}|engine={engine_version}".encode())
    return digest.hexdigest()


__all__ = [
    "TemplateContext",
    "engine_block",
    "group_json",
    "shared_url",
    "shooter_json",
    "template_digest",
    "theme_tokens",
]
