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
elsewhere still loads.

A desktop Look may also name **its own file** (step 2):
``own:font-<12hex>.ttf`` (or ``.otf``), a file in the Look's ``fonts/``
folder that ``own_fonts.save_font`` wrote and named by its content.
:func:`resolve` turns it into the file's absolute path when the Look has
it, so the theme carries either a catalog id or a path, and every consumer
(``overlay_html``'s ``@font-face``, ``overlay_text`` for ``drawtext``)
takes both. Pure apart from :func:`resolve` asking whether that file exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
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

#: A Look's own faces live in this folder of the Look, named by content.
OWN_DIR = "fonts"
OWN_PREFIX = "own:"
OWN_FILE_RE = re.compile(r"font-[0-9a-f]{12}\.(?:ttf|otf)")


def own_value(file: str) -> str:
    return f"{OWN_PREFIX}{file}"


def own_file(value: object) -> str | None:
    """The file an ``own:`` value names, or ``None`` when it is not one."""
    if not isinstance(value, str) or not value.startswith(OWN_PREFIX):
        return None
    file = value[len(OWN_PREFIX) :]
    return file if OWN_FILE_RE.fullmatch(file) else None


def is_font_path(value: str) -> bool:
    """Whether a theme's face is a resolved own file rather than a catalog id."""
    return Path(value).is_absolute()


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
    catalog knows (an id or a family label per role) and own files as
    written."""
    out: dict[str, str] = {}
    for role in ROLES:
        value = declared.get(role)
        hit = _match(role, value)
        if hit is not None:
            out[role] = hit
        elif own_file(value) is not None:
            out[role] = str(value)
    return out


def resolve(declared: dict[str, object], *, root: Path | None = None) -> dict[str, str]:
    """Every role's face for a Look's ``fonts``: its catalog id, or the
    absolute path of its own file under ``root`` when the Look has that
    file, else the role's default."""
    out = dict(DEFAULTS)
    for role, value in normalize(declared).items():
        file = own_file(value)
        if file is None:
            out[role] = value
        elif root is not None and (root / OWN_DIR / file).is_file():
            out[role] = str((root / OWN_DIR / file).resolve())
    return out


def check(declared: dict[str, str]) -> dict[str, str]:
    """Strict validation for a stored Look: each role is ``display`` or
    ``mono`` and names a face of that role by id."""
    for role, font_id in declared.items():
        if role not in ROLES:
            raise ValueError(f"fonts: {role!r} is not a font role; expected one of {ROLES}")
        if own_file(font_id) is not None:
            continue
        if isinstance(font_id, str) and font_id.startswith(OWN_PREFIX):
            raise ValueError(f"fonts: {font_id!r} is not a font file of this Look")
        face = _BY_ID.get(font_id)
        if face is None or face.role != role:
            known = ", ".join(f.id for f in FONTS if f.role == role)
            raise ValueError(f"fonts: {font_id!r} is not a {role} face; one of {known}")
    return declared


__all__ = [
    "BundledFont",
    "DEFAULTS",
    "FONTS",
    "FontRole",
    "OWN_DIR",
    "OWN_FILE_RE",
    "OWN_PREFIX",
    "ROLES",
    "check",
    "font",
    "is_font_path",
    "normalize",
    "own_file",
    "own_value",
    "resolve",
]
