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

import hashlib
import json
import logging
import re
from collections.abc import Callable
from contextvars import ContextVar, Token
from importlib import resources
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from .user_config import user_config_dir

logger = logging.getLogger(__name__)

SLOT_NAMES: tuple[str, ...] = ("title_page", "slate", "lower_third", "summary", "closing", "transition")
"""Every slot a manifest may name. ``summary`` is reserved: no renderer
reads it in this slice, the stage summary still composes through
``overlay_summary_cell``. ``transition`` holds the Look's stings (issue
#1245): each variant is a ``sting:<variant>`` transition kind."""

STING_SLOT = "transition"
PREVIEW_DIR = "preview"
"""Where a Look keeps the gallery's pictures of it (issue #1246):
``<slot>-<variant>.png`` (or ``.webp``) per template variant and
``look.png`` for the Look itself. A Look without one borrows the
shipped default's, as it borrows its templates."""
CARD_PREVIEW_SLOTS: tuple[str, ...] = ("title_page", "slate", "lower_third", "closing", "transition")

CardSlot = Literal["title_page", "slate", "lower_third", "closing"]
"""The slots ``overlay_card`` renders through a template."""

DEFAULT_LOOK = "splitsmith"
DEFAULT_VARIANT = "default"
"""The variant every slot has: the still card. A manifest's bare
``"slot": "file.html"`` is this variant alone."""

SlotVariants = dict[str, str]
"""Variant name -> template file, for one slot."""

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
LOOK_NAME_RE = _NAME_RE
"""The shape of a Look name and of a variant name (public for the request layer)."""
_TEMPLATE_FILE_RE = re.compile(r"^[A-Za-z0-9_.-]+\.html$")
_ACCENT_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

RGB = tuple[int, int, int]


class LookError(RuntimeError):
    """A Look directory that cannot be used, with the reason."""


class LookNotFoundError(LookError):
    """No shipped or user Look has this name."""


def check_colors(value: dict[str, RGB]) -> dict[str, RGB]:
    """Every required token present, every channel in 0..255 (shared with
    ``look_store.StoredLookBody``)."""
    missing = [token for token in REQUIRED_COLORS if token not in value]
    if missing:
        raise ValueError(f"missing colour tokens: {', '.join(missing)}")
    for token, rgb in value.items():
        if any(not 0 <= channel <= 255 for channel in rgb):
            raise ValueError(f"colour {token!r} has a channel outside 0..255: {rgb!r}")
    return value


def check_accent_series(value: list[str]) -> list[str]:
    for colour in value:
        if not _ACCENT_RE.match(colour):
            raise ValueError(f"accent_series entry {colour!r} must be a #rrggbb colour")
    return [colour.lower() for colour in value]


def check_styles(value: dict[str, str]) -> dict[str, str]:
    for slot, variant in value.items():
        if slot not in get_args(CardSlot):
            raise ValueError(f"styles: {slot!r} is not a card slot; expected one of {get_args(CardSlot)}")
        if not _NAME_RE.fullmatch(variant):
            raise ValueError(f"styles: slot {slot!r}: variant name {variant!r} must match {_NAME_RE.pattern}")
    return value


class LookManifest(BaseModel):
    """``look.json``. ``extra="ignore"`` so a manifest written by a newer
    splitsmith (with fields this version does not know) still loads."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    schema_version: int = 1
    name: str
    label: str = ""
    colors: dict[str, RGB]
    fonts: dict[str, str] = {}
    #: The colours shooters without an accent of their own are told apart
    #: by, by slot index (spec section 2). Empty falls back to ``accent``.
    accent_series: list[str] = []
    slots: dict[str, SlotVariants] = {}
    source: str | None = None
    #: The Look this one was made from (#1263): a record for the editor;
    #: nothing is inherited from it at render time.
    base: str | None = None
    #: Per card slot, the variant a request for ``default`` draws (#1263):
    #: the Look's card style. ``{"slate": "rise"}`` makes every slate rise.
    styles: dict[str, str] = {}

    @field_validator("name")
    @classmethod
    def _name_shape(cls, value: str) -> str:
        if not _NAME_RE.match(value):
            raise ValueError(f"Look name {value!r} must match {_NAME_RE.pattern}")
        return value

    @field_validator("colors")
    @classmethod
    def _required_colors(cls, value: dict[str, RGB]) -> dict[str, RGB]:
        return check_colors(value)

    @field_validator("accent_series")
    @classmethod
    def _series_shape(cls, value: list[str]) -> list[str]:
        return check_accent_series(value)

    @field_validator("styles")
    @classmethod
    def _styles_shape(cls, value: dict[str, str]) -> dict[str, str]:
        return check_styles(value)

    @field_validator("slots", mode="before")
    @classmethod
    def _normalise_slots(cls, value: object) -> object:
        """``"slot": "file.html"`` is shorthand for ``{"default": "file.html"}``."""
        if not isinstance(value, dict):
            return value
        return {
            slot: ({DEFAULT_VARIANT: spec} if isinstance(spec, str) else spec) for slot, spec in value.items()
        }

    @field_validator("slots")
    @classmethod
    def _slot_shape(cls, value: dict[str, SlotVariants]) -> dict[str, SlotVariants]:
        for slot, variants in value.items():
            if slot not in SLOT_NAMES:
                raise ValueError(f"unknown slot {slot!r}; expected one of {SLOT_NAMES}")
            for variant, file in variants.items():
                if not _NAME_RE.match(variant):
                    raise ValueError(f"slot {slot!r}: variant name {variant!r} must match {_NAME_RE.pattern}")
                if not _TEMPLATE_FILE_RE.match(file):
                    raise ValueError(
                        f"slot {slot!r} variant {variant!r} must name a bare .html file inside the Look, "
                        f"got {file!r}"
                    )
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

    @property
    def accent_series(self) -> tuple[str, ...]:
        return tuple(self.manifest.accent_series)

    def own_template(self, slot: str, variant: str = DEFAULT_VARIANT) -> Path | None:
        """This Look's template for ``slot`` in ``variant``, or ``None``
        when it declares none (see :func:`template_for` for the fallback)."""
        file = self.manifest.slots.get(slot, {}).get(variant)
        return None if file is None else self.root / file

    def variants(self, slot: str) -> tuple[str, ...]:
        """This Look's own variants for ``slot``, ``default`` first."""
        names = list(self.manifest.slots.get(slot, {}))
        return tuple(sorted(names, key=lambda n: (n != DEFAULT_VARIANT, n)))


def shipped_looks_dir() -> Path:
    """``splitsmith/data/looks`` inside the installed package."""
    return Path(str(resources.files("splitsmith.data").joinpath("looks")))


UserLooksProvider = Callable[[], "Path | None"]
_provider: ContextVar[UserLooksProvider | None] = ContextVar("splitsmith_user_looks", default=None)
_NO_USER_LOOKS = Path("/nonexistent/splitsmith-no-user-looks")


def set_user_looks_provider(provider: UserLooksProvider | None) -> Token[UserLooksProvider | None]:
    """Replace where the user's Looks come from, for this context (#1263).
    Hosted mode sets one wherever it pins a tenant (the request middleware,
    the share alias, the queue task) that answers the account's Looks
    materialized from its rows, so every renderer that resolves a Look by
    name sees that account's and no one else's. A provider that answers
    ``None`` means no user Looks at all, never the container's own folder.
    Reset with :func:`reset_user_looks_provider` and the returned token."""
    return _provider.set(provider)


def reset_user_looks_provider(token: Token[UserLooksProvider | None]) -> None:
    _provider.reset(token)


def user_looks_dir() -> Path:
    """``<user_config_dir>/looks`` (honours ``SPLITSMITH_HOME``; not created
    here), or what this context's provider answers."""
    provider = _provider.get()
    if provider is not None:
        return provider() or _NO_USER_LOOKS
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
    for slot, variants in manifest.slots.items():
        for variant, file in variants.items():
            if not (root / file).is_file():
                raise LookError(
                    f"{manifest_path}: slot {slot!r} variant {variant!r} names {file!r}, "
                    f"which is not in {root}"
                )
    return Look(manifest=manifest, root=root, source=source)


def look_files(root: Path) -> dict[str, Path]:
    """Every file of the Look folder ``root`` but its manifest and its
    ``preview/`` pictures, by path relative to ``root``: the templates and
    whatever they load beside them (an image, a stylesheet, its own fonts).
    What a copy of the Look carries, whether ``looks new --from`` or the
    editor's draft."""
    return {
        str(path.relative_to(root)): path
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != MANIFEST_FILE and path.relative_to(root).parts[0] != PREVIEW_DIR
    }


def look_fingerprint(root: Path) -> str:
    """What a render of the Look folder ``root`` depends on, cheaply: every
    file :func:`look_files` lists and the manifest, by relative path, size
    and modification time. A Save rewrites ``look.json`` and an own font is
    a new content-named file, so either moves it. For cache keys that would
    otherwise know a Look only by its name."""
    digest = hashlib.sha256()
    for rel, path in sorted({**look_files(root), MANIFEST_FILE: root / MANIFEST_FILE}.items()):
        try:
            st = path.stat()
        except OSError:
            continue
        digest.update(f"{rel}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def read_look(root: Path, source: Literal["shipped", "user"]) -> Look:
    """The Look in ``root``, or :class:`LookError` naming what is wrong: the
    strict read ``looks check`` uses, with no shipped fallback (#1262)."""
    return _read_look(root, source)


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
    :class:`LookNotFoundError`.

    A broken user Look that shadows a shipped one is skipped with a
    warning and the shipped one is returned, the policy :func:`list_looks`
    already applies: ``splitsmith`` is the default every renderer asks
    for, and one bad file in the user directory must not fail every
    default export. A broken user Look with no shipped namesake raises
    :class:`LookError`, because there is nothing else the user could
    have meant."""
    user_root = user_looks_dir() / name
    shipped_root = shipped_looks_dir() / name
    shipped_exists = (shipped_root / MANIFEST_FILE).exists()
    if (user_root / MANIFEST_FILE).exists():
        try:
            return _read_look(user_root, "user")
        except LookError as exc:
            if not shipped_exists:
                raise
            logger.warning("ignoring the user Look %s, using the shipped one: %s", name, exc)
    if shipped_exists:
        return _read_look(shipped_root, "shipped")
    raise LookNotFoundError(f"no Look named {name!r}; installed: {', '.join(look_names()) or 'none'}")


def _shipped_default() -> Look:
    return _read_look(shipped_looks_dir() / DEFAULT_LOOK, "shipped")


def variants_for(look: Look, slot: str) -> tuple[str, ...]:
    """Every variant a card in ``slot`` may name for ``look``: its own plus
    the shipped default Look's (the fallback), ``default`` first."""
    names = set(look.variants(slot)) | set(_shipped_default().variants(slot))
    return tuple(sorted(names, key=lambda n: (n != DEFAULT_VARIANT, n)))


def template_for(look: Look, slot: CardSlot, variant: str = DEFAULT_VARIANT) -> Path:
    """The template that draws ``slot`` in ``variant`` for ``look``: its
    own, else the shipped default Look's; a variant neither has falls
    back to ``default`` with a warning, so a stale or mistyped variant
    name costs the motion and never the card. The shipped default
    declares every card slot (pinned by ``tests/test_looks.py``), so
    this always resolves."""
    if variant == DEFAULT_VARIANT and slot in look.manifest.styles:
        variant = look.manifest.styles[slot]
    own = look.own_template(slot, variant)
    if own is not None:
        return own
    shipped = _shipped_default().own_template(slot, variant)
    if shipped is not None:
        return shipped
    if variant != DEFAULT_VARIANT:
        logger.warning("Look %s has no %r variant for slot %s; drawing the default", look.name, variant, slot)
        return template_for(look, slot, DEFAULT_VARIANT)
    raise LookError(f"the shipped {DEFAULT_LOOK!r} Look has no template for slot {slot!r}")


def sting_template_for(look: Look, name: str) -> Path | None:
    """The template for the sting ``name`` (issue #1245): the Look's own
    ``transition`` variant, else the shipped default Look's, else ``None``.
    No fallback to another variant: a sting the Look lacks is a plain
    fade, decided by the renderer, which records a degradation."""
    own = look.own_template(STING_SLOT, name)
    if own is not None:
        return own
    return _shipped_default().own_template(STING_SLOT, name)


def preview_file(look: Look, slot: str, variant: str = DEFAULT_VARIANT) -> Path | None:
    """The picture the gallery shows for ``variant`` of ``slot`` in ``look``
    (``slot`` ``"look"`` is the Look's own sample tile): the Look's
    ``preview/<slot>-<variant>.webp`` or ``.png``, else the shipped
    default Look's, else ``None``."""
    stem = "look" if slot == "look" else f"{slot}-{variant}"
    shipped = _shipped_default()
    candidates = [look] if look.root == shipped.root else [look, shipped]
    for candidate in candidates:
        for suffix in (".webp", ".png"):
            path = candidate.root / PREVIEW_DIR / f"{stem}{suffix}"
            if path.is_file():
                return path
    return None


SHIPPED_OWNER = "_shipped"
"""The owner a borrowed preview is served under: the shipped default Look
itself, whatever a user Look of the same name shadows (no Look can be
named this; ``_NAME_RE`` refuses a leading underscore)."""


TRANSITIONS_OWNER = "_transitions"
"""The owner the xfade families' looping previews are served under
(issue #1259): ``data/looks/_transitions/preview/<family>.webp``. Not a
Look; the leading underscore keeps it out of :func:`list_looks`."""


def preview_owner_root(owner: str) -> Path | None:
    """The directory ``/api/looks/{owner}/preview/`` reads from: the shipped
    default for :data:`SHIPPED_OWNER`, the transition previews for
    :data:`TRANSITIONS_OWNER`, an installed Look's root for its name, else
    ``None``."""
    if owner == SHIPPED_OWNER:
        return _shipped_default().root
    if owner == TRANSITIONS_OWNER:
        return shipped_looks_dir() / TRANSITIONS_OWNER
    if owner in look_names():
        return load_look(owner).root
    return None


class LookVariantInfo(BaseModel):
    """One template variant of a slot as the gallery sees it."""

    model_config = ConfigDict(frozen=True)

    name: str
    #: ``/api/looks/<owner>/preview/<file>``, the Look whose file it is.
    preview: str | None


class LookInfo(BaseModel):
    """One installed Look as ``GET /api/looks`` lists it (issue #1246)."""

    model_config = ConfigDict(frozen=True)

    name: str
    label: str
    source: Literal["shipped", "user"]
    #: The caller may change or delete it: every user Look, never a shipped one.
    editable: bool = False
    accent_series: list[str]
    preview: str | None
    slots: dict[str, list[LookVariantInfo]]


def _preview_url(look: Look, slot: str, variant: str) -> str | None:
    path = preview_file(look, slot, variant)
    if path is None:
        return None
    owner = look.name if path.is_relative_to(look.root) else SHIPPED_OWNER
    return f"/api/looks/{owner}/preview/{path.name}"


def look_catalog() -> list[LookInfo]:
    """Every installed Look with every slot's variants (the Look's own and
    the shipped default's, as :func:`variants_for` resolves them) and the
    preview each one shows; in :func:`list_looks` order."""
    out: list[LookInfo] = []
    for look in list_looks():
        slots = {
            slot: [
                LookVariantInfo(name=variant, preview=_preview_url(look, slot, variant))
                for variant in variants_for(look, slot)
            ]
            for slot in SLOT_NAMES
        }
        out.append(
            LookInfo(
                name=look.name,
                label=look.label,
                source=look.source,
                editable=look.source == "user",
                accent_series=list(look.accent_series),
                preview=_preview_url(look, "look", DEFAULT_VARIANT),
                slots=slots,
            )
        )
    return out


__all__ = [
    "look_fingerprint",
    "look_files",
    "DEFAULT_LOOK",
    "DEFAULT_VARIANT",
    "REQUIRED_COLORS",
    "SLOT_NAMES",
    "STING_SLOT",
    "CardSlot",
    "Look",
    "LookError",
    "LookInfo",
    "LookManifest",
    "LookNotFoundError",
    "LOOK_NAME_RE",
    "LookVariantInfo",
    "PREVIEW_DIR",
    "SHIPPED_OWNER",
    "TRANSITIONS_OWNER",
    "list_looks",
    "look_catalog",
    "load_look",
    "preview_file",
    "check_accent_series",
    "check_colors",
    "check_styles",
    "read_look",
    "reset_user_looks_provider",
    "set_user_looks_provider",
    "preview_owner_root",
    "look_names",
    "shared_dir",
    "shipped_looks_dir",
    "sting_template_for",
    "template_for",
    "user_looks_dir",
    "variants_for",
]
