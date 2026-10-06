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
    accent: str
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
) -> ResolvedIdentity:
    """The shooter's choices over the Look's defaults: their accent, else
    the Look's accent series by slot ``index`` (wrapping), else the Look's
    ``accent`` token; their logo under ``shooter_root/identity/``, else
    ``match_logo``; their club line or nothing."""
    from .overlay_theme import theme_for

    chosen = identity or ShooterIdentity()
    if chosen.accent is not None:
        accent = chosen.accent
    elif look.accent_series:
        accent = look.accent_series[index % len(look.accent_series)]
    else:
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
    "ResolvedIdentity",
    "ShooterIdentity",
    "logo_name",
    "resolve_identity",
]
