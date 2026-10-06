"""Per-shooter identity (spec 2026-10-06, section 2): an accent colour,
a logo and a club line, on the shooter's project. A Look is match-level;
identity is what tells one shooter's tile, lower third and roster row
from another's. :func:`resolve_identity` is where the defaults live, so
a shooter who never set one renders with the Look's own accent series
and the match's logo, and nothing is ever required of them.

Pure: no file is opened here. The logo is a path the renderer reads.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, field_validator

if TYPE_CHECKING:
    from .looks import Look

LOGO_DIR = "identity"
LOGO_EXTENSIONS: tuple[str, ...] = ("png", "jpg", "jpeg", "webp")
LOGO_MAX_BYTES = 2 * 1024 * 1024
#: The longest side a logo may have, in pixels: a 17 KB PNG can be
#: 12000 px square and Chromium would decode half a gigabyte of it on
#: every card.
LOGO_MAX_SIDE = 4096
CLUB_MAX_CHARS = 60

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_LOGO_RE = re.compile(r"^logo-[0-9a-f]{12}\.(?:png|jpg|jpeg|webp)$")


class ShooterIdentity(BaseModel):
    """What a shooter chose. Every field optional; ``None`` means "the
    Look's default", never "blank"."""

    model_config = ConfigDict(extra="ignore")

    accent: str | None = None
    logo: str | None = None
    club: str | None = None

    @field_validator("accent")
    @classmethod
    def _accent_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _HEX_RE.match(value):
            raise ValueError("accent must be a #rrggbb colour")
        return value.lower()

    @field_validator("logo")
    @classmethod
    def _logo_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _LOGO_RE.match(value):
            raise ValueError("logo must be a content-named raster file (logo-<hash>.png|jpg|jpeg|webp)")
        return value

    @field_validator("club")
    @classmethod
    def _club_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if len(stripped) > CLUB_MAX_CHARS:
            raise ValueError(f"club line is at most {CLUB_MAX_CHARS} characters")
        return stripped


def logo_name(data: bytes, ext: str) -> str:
    """The content-addressed file name a logo is stored under, so a
    replaced logo is a new key and the old one can be swept."""
    return f"logo-{hashlib.sha256(data).hexdigest()[:12]}.{ext.lower()}"


@dataclass(frozen=True)
class ResolvedIdentity:
    """What a template draws for one shooter, defaults applied."""

    label: str
    accent: str | None
    logo_path: Path | None
    club: str | None


def _hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"#{r:02x}{g:02x}{b:02x}"


def resolve_identity(
    *,
    label: str,
    identity: ShooterIdentity | None,
    index: int,
    look: Look,
    shooter_root: Path | None,
    match_logo: Path | None,
    series_default: bool = False,
) -> ResolvedIdentity:
    """The shooter's choices: their accent or none; their logo under
    ``shooter_root/identity/``, else ``match_logo``; their club line or
    nothing.

    A shooter who set no accent gets ``None``, so a render with no
    identity configured is what it was before identities existed (the
    summary draws no bar, the name keeps its ink). The spec's slot default
    (the Look's ``accent_series`` by ``index``, wrapping, else the Look's
    ``accent`` token) is opt-in through ``series_default``: the frame
    scripts' demo asks for it, production renders do not. Ruling from the
    #1243 review, recorded in the slice 3 ledger.
    """
    chosen = identity or ShooterIdentity()
    accent: str | None = chosen.accent
    if accent is None and series_default:
        if look.accent_series:
            accent = look.accent_series[index % len(look.accent_series)]
        else:
            from .overlay_theme import theme_for

            accent = _hex(theme_for(look).accent)
    logo_path: Path | None = match_logo
    if chosen.logo is not None and shooter_root is not None:
        logo_path = shooter_root / LOGO_DIR / chosen.logo
    return ResolvedIdentity(label=label, accent=accent, logo_path=logo_path, club=chosen.club)


__all__ = [
    "CLUB_MAX_CHARS",
    "LOGO_DIR",
    "LOGO_EXTENSIONS",
    "LOGO_MAX_BYTES",
    "LOGO_MAX_SIDE",
    "ResolvedIdentity",
    "ShooterIdentity",
    "logo_name",
    "resolve_identity",
]
