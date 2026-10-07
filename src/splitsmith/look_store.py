"""Looks per account (spec 2026-10-07 section 3, issue #1263).

One interface, two backends, as export presets have: :class:`LookStore`
(list, get, put, delete) over the local Looks folder
(:class:`FolderLookStore`) and, hosted, a ``user_looks`` table per account
(``splitsmith.db.looks.PostgresLookStore``).

What a store holds is a :class:`StoredLookBody`: the colours, the accent
series, each card slot's style and the Look it was made from. No
template: hosted, an account's code never runs on our server until the
sandboxed loader ships (#1266), so a stored Look is drawn by the shipped
templates (``looks.template_for`` falls back to them). Locally a Look is
an ordinary folder; ``put`` on one the user made by hand rewrites only
these fields in its ``look.json`` and keeps its templates.

Renderers resolve Looks by name through ``looks.user_looks_dir()``.
Hosted, :func:`materialize` writes an account's stored Looks as
manifest-only folders into a content-named directory, and the provider
``looks.set_user_looks_provider`` points there for that account's
requests and jobs.

This module is imported by the local server, so it stays free of
``sqlalchemy`` and ``splitsmith.db``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .fonts import check as check_fonts
from .fonts import normalize as normalize_fonts
from .looks import (
    DEFAULT_VARIANT,
    LOOK_NAME_RE,
    MANIFEST_FILE,
    RGB,
    STING_SLOT,
    Look,
    LookError,
    LookManifest,
    check_accent_series,
    check_colors,
    check_styles,
    look_files,
    read_look,
    shipped_looks_dir,
    user_looks_dir,
)

MAX_LABEL_LENGTH = 60


class LookStoreError(ValueError):
    """A Look the store refuses to write or delete, with the reason."""


class StoredLookBody(BaseModel):
    """The part of a Look an account edits without code. Each field is
    validated by the same rule ``look.json`` is, so an error names it."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    label: str = Field(default="", max_length=MAX_LABEL_LENGTH)
    base: str | None = None
    colors: dict[str, RGB]
    accent_series: list[str] = []
    styles: dict[str, str] = {}
    #: A face per role from the bundled catalog (``splitsmith.fonts``, #1272).
    fonts: dict[str, str] = {}

    @field_validator("fonts")
    @classmethod
    def _fonts(cls, value: dict[str, str]) -> dict[str, str]:
        return check_fonts(value)

    @field_validator("base")
    @classmethod
    def _base_shape(cls, value: str | None) -> str | None:
        if value is not None and not LOOK_NAME_RE.fullmatch(value):
            raise ValueError(f"{value!r} is not a Look name ({LOOK_NAME_RE.pattern})")
        return value

    @field_validator("colors")
    @classmethod
    def _colors(cls, value: dict[str, RGB]) -> dict[str, RGB]:
        return check_colors(value)

    @field_validator("accent_series")
    @classmethod
    def _series(cls, value: list[str]) -> list[str]:
        return check_accent_series(value)

    @field_validator("styles")
    @classmethod
    def _styles(cls, value: dict[str, str]) -> dict[str, str]:
        return check_styles(value)


class StoredLook(BaseModel):
    name: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    body: StoredLookBody


class LookStore(Protocol):
    """An account's own Looks. Shipped Looks are not stored; the catalog
    merges them."""

    async def list(self) -> list[StoredLook]: ...

    async def get(self, name: str) -> StoredLook | None: ...

    async def put(self, name: str, body: StoredLookBody) -> StoredLook: ...

    async def delete(self, name: str) -> None: ...


class HostedLookStore(LookStore, Protocol):
    """A store whose Looks live somewhere renderers cannot read, so it
    writes them out as a Looks folder for ``looks.set_user_looks_provider``."""

    def materialized_dir(self, cache_root: Path) -> Path: ...


def check_name(name: str) -> str:
    if not LOOK_NAME_RE.fullmatch(name):
        raise LookStoreError(
            f"{name!r} is not a Look name: lower-case letters, digits, '-' and '_', starting with a letter"
        )
    return name


def is_shipped_name(name: str) -> bool:
    return (shipped_looks_dir() / name / MANIFEST_FILE).is_file()


def manifest_for(name: str, body: StoredLookBody) -> dict[str, object]:
    """``look.json`` for a stored Look: its fields and no template slots."""
    return {"schema_version": 1, "name": name, "slots": {}, **_body_fields(body)}


def _body_fields(body: StoredLookBody) -> dict[str, object]:
    return body.model_dump(mode="json")


def body_from_manifest(manifest: LookManifest) -> StoredLookBody:
    return StoredLookBody(
        label=manifest.label,
        base=manifest.base,
        colors=manifest.colors,
        accent_series=manifest.accent_series,
        styles=manifest.styles,
        # A manifest may name a family or a role the catalog lacks (the
        # shipped one says "Antonio" and "sans"); the stored body keeps ids.
        fonts=normalize_fonts(manifest.fonts),
    )


def _write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _dump(raw: dict[str, object]) -> str:
    return json.dumps(raw, indent=2, sort_keys=True) + "\n"


class FolderLookStore:
    """Local mode: the folders under ``~/.splitsmith/looks``. A user Look may
    shadow a shipped name here, as it always could. A folder that does not
    load is neither listed nor returned; ``looks check`` is what explains it."""

    def _root(self) -> Path:
        return user_looks_dir()

    def _stored(self, root: Path) -> StoredLook | None:
        try:
            look = read_look(root, "user")
        except LookError:
            return None
        stamp = datetime.fromtimestamp((root / MANIFEST_FILE).stat().st_mtime, UTC)
        return StoredLook(name=look.name, updated_at=stamp, body=body_from_manifest(look.manifest))

    async def list(self) -> list[StoredLook]:
        base = self._root()
        if not base.is_dir():
            return []
        found = (self._stored(p) for p in sorted(base.iterdir()) if p.is_dir() and not p.name.startswith("_"))
        return [stored for stored in found if stored is not None]

    async def get(self, name: str) -> StoredLook | None:
        root = self._root() / check_name(name)
        return self._stored(root) if root.is_dir() else None

    async def put(self, name: str, body: StoredLookBody) -> StoredLook:
        root = self._root() / check_name(name)
        manifest_path = root / MANIFEST_FILE
        previous = manifest_path.read_text(encoding="utf-8") if manifest_path.is_file() else None
        raw: dict[str, object] = manifest_for(name, body)
        if previous is not None:
            try:
                kept = json.loads(previous)
            except json.JSONDecodeError:
                kept = None
            if isinstance(kept, dict):
                raw = {**kept, "name": name, **_body_fields(body)}
        created = not root.exists()
        root.mkdir(parents=True, exist_ok=True)
        try:
            _write_atomic(manifest_path, _dump(raw))
            read_look(root, "user")
        except LookError as exc:
            if previous is not None:
                _write_atomic(manifest_path, previous)
            elif created:
                shutil.rmtree(root, ignore_errors=True)
            raise LookStoreError(str(exc)) from None
        stored = self._stored(root)
        assert stored is not None  # it read back above
        return stored

    async def delete(self, name: str) -> None:
        root = self._root() / check_name(name)
        if root.is_dir():
            shutil.rmtree(root)


#: The largest template the editor saves; a card is a page of HTML, not a bundle.
MAX_TEMPLATE_BYTES = 256_000
TEMPLATE_SLOTS: tuple[str, ...] = ("title_page", "slate", "lower_third", "closing", STING_SLOT)


class TemplateEdit(BaseModel):
    """One template's text for a slot and variant (the template editor, #1265)."""

    model_config = ConfigDict(frozen=True)

    slot: str
    variant: str
    content: str = Field(max_length=MAX_TEMPLATE_BYTES)

    @field_validator("slot")
    @classmethod
    def _slot(cls, value: str) -> str:
        if value not in TEMPLATE_SLOTS:
            raise ValueError(f"{value!r} is not a template slot; expected one of {TEMPLATE_SLOTS}")
        return value

    @field_validator("variant")
    @classmethod
    def _variant(cls, value: str) -> str:
        if not LOOK_NAME_RE.fullmatch(value):
            raise ValueError(f"{value!r} is not a variant name ({LOOK_NAME_RE.pattern})")
        return value


def template_file(slots: dict[str, dict[str, str]], slot: str, variant: str) -> str:
    """The file a Look's ``slot`` / ``variant`` is written to: the one its
    manifest already names, else ``<slot>-<variant>.html``."""
    return slots.get(slot, {}).get(variant) or f"{slot}-{variant}.html"


def apply_template_edits(root: Path, edits: Iterable[TemplateEdit]) -> None:
    """Write each edit's text into the Look folder ``root`` and name it in
    ``look.json``. The caller validates the folder after (``read_look``)."""
    manifest_path = root / MANIFEST_FILE
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    slots: dict[str, dict[str, str]] = {
        slot: ({DEFAULT_VARIANT: spec} if isinstance(spec, str) else dict(spec))
        for slot, spec in (raw.get("slots") or {}).items()
    }
    for e in edits:
        file = template_file(slots, e.slot, e.variant)
        _write_atomic(root / file, e.content)
        slots.setdefault(e.slot, {})[e.variant] = file
    raw["slots"] = slots
    _write_atomic(manifest_path, _dump(raw))


def draft_look(
    saved: Look, body: StoredLookBody | None, work: Path, edits: Iterable[TemplateEdit] = ()
) -> Look:
    """``saved`` with ``body``'s fields in place of its own (``None`` keeps
    them) and ``edits``' template text, written under ``work`` beside a copy
    of every template it names: what the Look and template editors preview
    and check before Save (#1264, #1265). Nothing of ``saved`` is touched."""
    raw = saved.manifest.model_dump(mode="json", exclude={"source"})
    if body is not None:
        raw.update(_body_fields(body))
    root = work / saved.name
    root.mkdir(parents=True, exist_ok=True)
    # Every file, not only the templates: a template may load an image
    # beside it, and the Look's own fonts resolve under its root (#1272).
    for file, src in look_files(saved.root).items():
        (root / file).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, root / file)
    (root / MANIFEST_FILE).write_text(_dump(raw), encoding="utf-8")
    edits = list(edits)
    if edits:
        apply_template_edits(root, edits)
    try:
        return read_look(root, "user")
    except LookError as exc:
        raise LookStoreError(str(exc)) from None


def materialize(stored: Iterable[StoredLook], cache_root: Path) -> Path:
    """Write ``stored`` as manifest-only Look folders under a directory of
    ``cache_root`` named by their content, and return it. The same Looks
    always land in the same directory, which is written once (to a
    temporary sibling, then renamed) and never changed, so concurrent
    renders of one account share it safely. A shipped name is skipped: a
    hosted Look never shadows a shipped one."""
    looks = sorted((s for s in stored if not is_shipped_name(s.name)), key=lambda s: s.name)
    payload = json.dumps([(s.name, _body_fields(s.body)) for s in looks], sort_keys=True)
    target = cache_root / hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    if target.is_dir():
        return target
    cache_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=cache_root, prefix=".looks-"))
    try:
        for s in looks:
            (staging / s.name).mkdir()
            (staging / s.name / MANIFEST_FILE).write_text(
                _dump(manifest_for(s.name, s.body)), encoding="utf-8"
            )
        staging.replace(target)
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)
        if not target.is_dir():
            raise
    return target


__all__ = [
    "FolderLookStore",
    "HostedLookStore",
    "LookStore",
    "LookStoreError",
    "MAX_LABEL_LENGTH",
    "StoredLook",
    "StoredLookBody",
    "body_from_manifest",
    "check_name",
    "MAX_TEMPLATE_BYTES",
    "TEMPLATE_SLOTS",
    "TemplateEdit",
    "apply_template_edits",
    "draft_look",
    "template_file",
    "is_shipped_name",
    "manifest_for",
    "materialize",
]
