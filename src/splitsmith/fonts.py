"""The faces a Look may draw with (issue #1272): open-licensed fonts bundled
in ``data/fonts/``, each next to its licence.

A Look picks one face per **role**. Templates never name a file: they draw
``"Splitsmith Display"`` and ``"Splitsmith Mono"``, and the engine stylesheet
(``overlay_html``) points those two family names at whatever the Look chose,
so every template, the shipped ones included, follows a Look's choice with
no edit. The ffmpeg clock reads the mono role too
(``compare.overlay_sprites.theme_font_face``), so the counter and the clock
beside it never disagree.

``look.json``'s ``fonts`` names a face by id or by family label; anything
this catalog does not know (the shipped manifest's old ``"sans": "Geist"``,
a family not bundled) falls back to the role's default, so a manifest written
elsewhere still loads. Pure: no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FontRole = Literal["display", "mono"]
ROLES: tuple[FontRole, ...] = ("display", "mono")


@dataclass(frozen=True)
class BundledFont:
    id: str
    label: str
    role: FontRole
    file: str
    #: The ``font-weight`` the ``@font-face`` rule declares. A single-weight
    #: file declares the range the stylesheet asks for so Chromium draws it
    #: as is instead of synthesizing a bold.
    weight: str
    help: str


FONTS: tuple[BundledFont, ...] = (
    BundledFont(
        "antonio", "Antonio", "display", "Antonio-VariableFont.ttf", "400 700", "Condensed, the default"
    ),
    BundledFont(
        "bebas-neue", "Bebas Neue", "display", "BebasNeue-Regular.ttf", "400 700", "All capitals, poster-like"
    ),
    BundledFont(
        "oswald", "Oswald", "display", "Oswald-VariableFont.ttf", "400 700", "Condensed, a little wider"
    ),
    BundledFont(
        "barlow-condensed",
        "Barlow Condensed",
        "display",
        "BarlowCondensed-SemiBold.ttf",
        "400 700",
        "Condensed, softer and rounder",
    ),
    BundledFont(
        "jetbrains-mono", "JetBrains Mono", "mono", "JetBrainsMono-Bold.ttf", "700", "Figures, the default"
    ),
    BundledFont(
        "roboto-mono", "Roboto Mono", "mono", "RobotoMono-VariableFont.ttf", "700", "Figures, rounder"
    ),
    BundledFont(
        "ibm-plex-mono",
        "IBM Plex Mono",
        "mono",
        "IBMPlexMono-Bold.ttf",
        "700",
        "Figures, with serifs on the 1",
    ),
)

DEFAULTS: dict[str, str] = {"display": "antonio", "mono": "jetbrains-mono"}

_BY_ID = {f.id: f for f in FONTS}


def font(font_id: str) -> BundledFont:
    """The catalog entry for ``font_id``; ``KeyError`` when unknown."""
    return _BY_ID[font_id]


def _match(role: str, value: object) -> str | None:
    if not isinstance(value, str):
        return None
    wanted = value.strip().lower()
    for face in FONTS:
        if face.role == role and wanted in (face.id, face.label.lower()):
            return face.id
    return None


def normalize(declared: dict[str, object]) -> dict[str, str]:
    """``declared`` as catalog ids, keeping only the roles and faces this
    catalog knows (an id or a family label per role)."""
    out: dict[str, str] = {}
    for role in ROLES:
        hit = _match(role, declared.get(role))
        if hit is not None:
            out[role] = hit
    return out


def resolve(declared: dict[str, object]) -> dict[str, str]:
    """Every role's face for a Look's ``fonts``: its choice when the catalog
    knows it, else the role's default."""
    return {**DEFAULTS, **normalize(declared)}


def check(declared: dict[str, str]) -> dict[str, str]:
    """Strict validation for a stored Look: each role is ``display`` or
    ``mono`` and names a face of that role by id."""
    for role, font_id in declared.items():
        if role not in ROLES:
            raise ValueError(f"fonts: {role!r} is not a font role; expected one of {ROLES}")
        face = _BY_ID.get(font_id)
        if face is None or face.role != role:
            known = ", ".join(f.id for f in FONTS if f.role == role)
            raise ValueError(f"fonts: {font_id!r} is not a {role} face; one of {known}")
    return declared


__all__ = ["BundledFont", "DEFAULTS", "FONTS", "FontRole", "ROLES", "check", "font", "normalize", "resolve"]
