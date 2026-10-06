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
import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from .looks import shared_dir
from .overlay_layout import MIN_FONT_SIZE, Element, Group
from .overlay_theme import OverlayTheme


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


def engine_block(*, css: str) -> dict[str, Any]:
    return {"css": css, "min_font_size": MIN_FONT_SIZE}


def shared_url() -> str:
    return shared_dir().resolve().as_uri()


__all__ = ["TemplateContext", "engine_block", "group_json", "shared_url", "theme_tokens"]
