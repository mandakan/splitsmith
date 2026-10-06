"""Looks: where a rendered video's colours and card templates come from.

Spec ``docs/superpowers/specs/2026-10-06-rendered-video-first-design.md``,
section 1. A Look is a directory: ``look.json`` (colour tokens, font
names, which template draws which slot) and one HTML template per card
slot. Shipped Looks live under ``splitsmith/data/looks/``; user Looks
under ``<user_config_dir>/looks/`` and shadow a shipped Look of the same
name. ``_shared/`` under the shipped directory is not a Look: it holds
the engine scripts (``fit.js``, ``cell.js``) every shipped template
loads.

This module is pure. It reads JSON and resolves paths; it never opens a
browser, runs ffmpeg or writes a file. A template file that a manifest
names must exist at load time, so a missing one fails here, with the
file named, rather than in the middle of a render.
"""

from __future__ import annotations

import json
import logging
import re
from importlib import resources
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from .user_config import user_config_dir

logger = logging.getLogger(__name__)

SLOT_NAMES: tuple[str, ...] = ("title_page", "slate", "lower_third", "summary", "closing")
"""Every slot a manifest may name. ``summary`` is reserved: no renderer
reads it in this slice, the stage summary still composes through
``overlay_summary_cell``."""

CardSlot = Literal["title_page", "slate", "lower_third", "closing"]
"""The slots ``overlay_card`` renders through a template."""

DEFAULT_LOOK = "splitsmith"

REQUIRED_COLORS: tuple[str, ...] = (
    "ink",
    "split",
    "split_good",
    "stroke",
    "accent",
    "accent_fill",
    "accent_text",
    "rule",
    "muted",
    "ink_2",
    "surface",
    "subtle",
)
"""The tokens ``overlay_theme.OverlayTheme`` is built from. A manifest may
carry more (``split_slow`` is written by the build script and read by
nothing yet)."""

MANIFEST_FILE = "look.json"
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_TEMPLATE_FILE_RE = re.compile(r"^[A-Za-z0-9_.-]+\.html$")

RGB = tuple[int, int, int]


class LookError(RuntimeError):
    """A Look directory that cannot be used, with the reason."""


class LookNotFoundError(LookError):
    """No shipped or user Look has this name."""


class LookManifest(BaseModel):
    """``look.json``. ``extra="ignore"`` so a manifest written by a newer
    splitsmith (with fields this version does not know) still loads."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    schema_version: int = 1
    name: str
    label: str = ""
    colors: dict[str, RGB]
    fonts: dict[str, str] = {}
    slots: dict[str, str] = {}
    source: str | None = None

    @field_validator("name")
    @classmethod
    def _name_shape(cls, value: str) -> str:
        if not _NAME_RE.match(value):
            raise ValueError(f"Look name {value!r} must match {_NAME_RE.pattern}")
        return value

    @field_validator("colors")
    @classmethod
    def _required_colors(cls, value: dict[str, RGB]) -> dict[str, RGB]:
        missing = [token for token in REQUIRED_COLORS if token not in value]
        if missing:
            raise ValueError(f"missing colour tokens: {', '.join(missing)}")
        for token, rgb in value.items():
            if any(not 0 <= channel <= 255 for channel in rgb):
                raise ValueError(f"colour {token!r} has a channel outside 0..255: {rgb!r}")
        return value

    @field_validator("slots")
    @classmethod
    def _slot_shape(cls, value: dict[str, str]) -> dict[str, str]:
        for slot, file in value.items():
            if slot not in SLOT_NAMES:
                raise ValueError(f"unknown slot {slot!r}; expected one of {SLOT_NAMES}")
            if not _TEMPLATE_FILE_RE.match(file):
                raise ValueError(f"slot {slot!r} must name a bare .html file inside the Look, got {file!r}")
        return value


class Look(BaseModel):
    """A loaded Look: its manifest, where it lives, and whether it is ours
    or the user's."""

    model_config = ConfigDict(frozen=True)

    manifest: LookManifest
    root: Path
    source: Literal["shipped", "user"]

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def label(self) -> str:
        return self.manifest.label or self.manifest.name

    def own_template(self, slot: str) -> Path | None:
        """This Look's template for ``slot``, or ``None`` when it declares
        none (see :func:`template_for` for the fallback)."""
        file = self.manifest.slots.get(slot)
        return None if file is None else self.root / file


def shipped_looks_dir() -> Path:
    """``splitsmith/data/looks`` inside the installed package."""
    return Path(str(resources.files("splitsmith.data").joinpath("looks")))


def user_looks_dir() -> Path:
    """``<user_config_dir>/looks``; honours ``SPLITSMITH_HOME``. Not created here."""
    return user_config_dir() / "looks"


def shared_dir() -> Path:
    """The engine scripts shipped templates load (``fit.js``, ``cell.js``)."""
    return shipped_looks_dir() / "_shared"


def _read_look(root: Path, source: Literal["shipped", "user"]) -> Look:
    manifest_path = root / MANIFEST_FILE
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LookError(f"{root}: no {MANIFEST_FILE}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise LookError(f"{manifest_path}: cannot read: {exc}") from exc
    try:
        manifest = LookManifest.model_validate(raw)
    except ValidationError as exc:
        raise LookError(f"{manifest_path}: {exc.errors()[0]['msg']}") from exc
    if manifest.name != root.name:
        raise LookError(f"{manifest_path}: name {manifest.name!r} does not match its directory {root.name!r}")
    for slot, file in manifest.slots.items():
        if not (root / file).is_file():
            raise LookError(f"{manifest_path}: slot {slot!r} names {file!r}, which is not in {root}")
    return Look(manifest=manifest, root=root, source=source)


def _candidate_dirs(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_"))


def list_looks() -> list[Look]:
    """Every usable Look: the shipped ones (the default first, then by
    name), then the user's by name. A user Look with a shipped Look's
    name replaces it in place. A user directory that is not a valid Look
    is skipped with a warning; a broken shipped Look raises, because that
    is a packaging defect."""
    shipped: dict[str, Look] = {}
    for root in _candidate_dirs(shipped_looks_dir()):
        look = _read_look(root, "shipped")
        shipped[look.name] = look
    result: dict[str, Look] = {}
    for name in (DEFAULT_LOOK, *sorted(shipped)):
        if name in shipped and name not in result:
            result[name] = shipped[name]
    for root in _candidate_dirs(user_looks_dir()):
        try:
            look = _read_look(root, "user")
        except LookError as exc:
            logger.warning("ignoring user Look %s: %s", root.name, exc)
            continue
        result[look.name] = look
    return list(result.values())


def look_names() -> tuple[str, ...]:
    return tuple(look.name for look in list_looks())


def load_look(name: str) -> Look:
    """The user's Look of this name, else the shipped one, else
    :class:`LookNotFoundError`. A user Look that exists but is broken
    raises :class:`LookError` rather than silently falling through to
    the shipped one: the user asked for theirs."""
    user_root = user_looks_dir() / name
    if (user_root / MANIFEST_FILE).exists():
        return _read_look(user_root, "user")
    shipped_root = shipped_looks_dir() / name
    if (shipped_root / MANIFEST_FILE).exists():
        return _read_look(shipped_root, "shipped")
    raise LookNotFoundError(f"no Look named {name!r}; installed: {', '.join(look_names()) or 'none'}")


def template_for(look: Look, slot: CardSlot) -> Path:
    """The template that draws ``slot`` for ``look``: its own, else the
    shipped default Look's. The shipped default declares every card
    slot (pinned by ``tests/test_looks.py``), so this always resolves."""
    own = look.own_template(slot)
    if own is not None:
        return own
    fallback = _read_look(shipped_looks_dir() / DEFAULT_LOOK, "shipped").own_template(slot)
    if fallback is None:
        raise LookError(f"the shipped {DEFAULT_LOOK!r} Look has no template for slot {slot!r}")
    return fallback


__all__ = [
    "DEFAULT_LOOK",
    "REQUIRED_COLORS",
    "SLOT_NAMES",
    "CardSlot",
    "Look",
    "LookError",
    "LookManifest",
    "LookNotFoundError",
    "list_looks",
    "load_look",
    "look_names",
    "shared_dir",
    "shipped_looks_dir",
    "template_for",
    "user_looks_dir",
]
