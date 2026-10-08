"""The shooter book (spec 2026-10-08-account-identity-and-shooter-book-design):
a per-account store of shooter identities keyed by SSI shooter id, so a
shooter's accent, club line and logo follow them from match to match.

The key is the SSI shooter index id (``MatchProject.selected_shooter_id``),
never a name: two shooters may share one, and a wrong match would put a
stranger's logo on a tile. Renderers never read a store: the request layer
takes a :class:`BookSnapshot` once per export (:func:`load_snapshot`) and
hands it to ``identity_media``, which is where the book meets a match.

Local mode stores ``<user config>/account/shooter_book.json`` and the logos
beside it in ``account/files/``, content-named like a shooter's
(``identity.logo_name``). Hosted mode has its own store (``db``); until it
exists hosted reads :class:`EmptyShooterBookStore`, so a hosted render is
what it always was. Never a ``state_docs`` kind.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from . import user_config
from .async_bridge import run_sync
from .identity import ShooterIdentity, logo_name, sniff_logo

logger = logging.getLogger(__name__)

ACCOUNT_DIRNAME = "account"
BOOK_FILENAME = "shooter_book.json"
FILES_DIRNAME = "files"
LABEL_MAX = 120


def account_dir() -> Path | None:
    """``<user config>/account``, or ``None`` when the user config is
    disabled. Not created here."""
    if user_config.is_disabled():
        return None
    return user_config.user_config_dir() / ACCOUNT_DIRNAME


def _now() -> datetime:
    return datetime.now(UTC)


class ShooterBookEntry(BaseModel):
    """One shooter's identity in the book. ``label`` is the name last seen,
    for listing only; nothing matches on it."""

    model_config = ConfigDict(extra="ignore")

    shooter_id: int
    identity: ShooterIdentity = Field(default_factory=ShooterIdentity)
    label: str | None = Field(default=None, max_length=LABEL_MAX)
    updated_at: datetime = Field(default_factory=_now)


def is_set(identity: ShooterIdentity | None) -> bool:
    """Whether ``identity`` carries anything: an all-``None`` record is
    "set nothing", which is what lets the book apply."""
    return identity is not None and bool(identity.accent or identity.club or identity.logo)


@dataclass(frozen=True)
class BookSnapshot:
    """The book as one export reads it: identities by shooter id and the
    logo files that exist on this disk (a missing one is simply absent, so
    a template is never handed a dangling path)."""

    entries: Mapping[int, ShooterIdentity] = field(default_factory=dict)
    logos: Mapping[str, Path] = field(default_factory=dict)

    def get(self, shooter_id: int | None) -> ShooterIdentity | None:
        if shooter_id is None:
            return None
        return self.entries.get(shooter_id)

    def logo_path(self, identity: ShooterIdentity) -> Path | None:
        return self.logos.get(identity.logo) if identity.logo else None


EMPTY_BOOK = BookSnapshot()


def identity_digest(identity: ShooterIdentity) -> str:
    """A short content address for a book identity, for a cache key that the
    project's own timestamp cannot move (a book edit touches no match)."""
    import hashlib

    payload = json.dumps(identity.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class ShooterBookStore(Protocol):
    async def list(self) -> list[ShooterBookEntry]: ...

    async def get(self, shooter_id: int) -> ShooterBookEntry | None: ...

    async def put(self, entry: ShooterBookEntry) -> None: ...

    async def delete(self, shooter_id: int) -> None: ...

    async def put_logo(self, data: bytes) -> str:
        """Check and store a logo; its content name. ``ValueError`` with the
        user's reason when refused (``identity.sniff_logo``)."""
        ...

    async def logo_file(self, name: str) -> Path | None:
        """The logo on this disk (mirrored first where it lives elsewhere),
        or ``None`` when it is not there."""
        ...

    async def snapshot(self) -> BookSnapshot: ...


def load_snapshot(store: ShooterBookStore | None) -> BookSnapshot:
    """The book for one export, from sync code (a job thread, a CLI). A store
    that fails to read is an empty book, logged: a render never fails on it."""
    if store is None:
        return EMPTY_BOOK
    try:
        return run_sync(store.snapshot())
    except Exception as exc:  # noqa: BLE001 -- the book is never worth a failed render
        logger.warning("shooter book: could not read it (%s); rendering without it", exc)
        return EMPTY_BOOK


def _safe_file(folder: Path, name: str) -> Path | None:
    """``folder/name`` when it is a regular file and not a symlink."""
    path = folder / name
    if path.is_symlink() or not path.is_file():
        return None
    return path


class JsonShooterBookStore:
    """The local book: one JSON file and a folder of content-named logos."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root

    def _dir(self) -> Path | None:
        return self._root if self._root is not None else account_dir()

    def _load(self) -> dict[int, ShooterBookEntry]:
        folder = self._dir()
        if folder is None:
            return {}
        path = folder / BOOK_FILENAME
        if path.is_symlink():
            logger.warning("shooter book: %s is a symlink; ignored", path)
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("shooter book: ignoring unreadable %s: %s", path, exc)
            return {}
        out: dict[int, ShooterBookEntry] = {}
        for item in raw.get("entries", []) if isinstance(raw, dict) else []:
            try:
                entry = ShooterBookEntry.model_validate(item)
            except Exception as exc:  # noqa: BLE001 -- one bad row must not lose the book
                logger.warning("shooter book: skipping a malformed entry: %s", exc)
                continue
            out[entry.shooter_id] = entry
        return out

    def _save(self, entries: dict[int, ShooterBookEntry]) -> None:
        folder = self._dir()
        if folder is None:
            return
        payload = {
            "schema_version": 1,
            "entries": [entries[k].model_dump(mode="json") for k in sorted(entries)],
        }
        user_config._atomic_write_text(folder / BOOK_FILENAME, json.dumps(payload, indent=2))

    async def list(self) -> list[ShooterBookEntry]:
        entries = self._load()
        return [entries[k] for k in sorted(entries)]

    async def get(self, shooter_id: int) -> ShooterBookEntry | None:
        return self._load().get(shooter_id)

    async def put(self, entry: ShooterBookEntry) -> None:
        entries = self._load()
        entries[entry.shooter_id] = entry
        self._save(entries)

    async def delete(self, shooter_id: int) -> None:
        entries = self._load()
        if entries.pop(shooter_id, None) is not None:
            self._save(entries)

    async def put_logo(self, data: bytes) -> str:
        ext = sniff_logo(data)
        folder = self._dir()
        if folder is None:
            raise ValueError("The user settings folder is disabled; nothing can be stored.")
        name = logo_name(data, ext)
        files = folder / FILES_DIRNAME
        files.mkdir(parents=True, exist_ok=True)
        target = files / name
        if not target.is_file():
            partial = files / f".{name}.part"
            partial.write_bytes(data)
            partial.replace(target)
        return name

    async def logo_file(self, name: str) -> Path | None:
        folder = self._dir()
        return _safe_file(folder / FILES_DIRNAME, name) if folder is not None else None

    async def snapshot(self) -> BookSnapshot:
        entries = self._load()
        logos: dict[str, Path] = {}
        for entry in entries.values():
            name = entry.identity.logo
            if name and name not in logos:
                path = await self.logo_file(name)
                if path is not None:
                    logos[name] = path
        return BookSnapshot(entries={k: e.identity for k, e in entries.items()}, logos=logos)


class EmptyShooterBookStore:
    """A book that holds nothing and refuses writes: hosted until its own
    store exists, so a hosted render is what it always was."""

    async def list(self) -> list[ShooterBookEntry]:
        return []

    async def get(self, shooter_id: int) -> ShooterBookEntry | None:
        return None

    async def put(self, entry: ShooterBookEntry) -> None:
        return None

    async def delete(self, shooter_id: int) -> None:
        return None

    async def put_logo(self, data: bytes) -> str:
        raise ValueError("The shooter book is not available here yet.")

    async def logo_file(self, name: str) -> Path | None:
        return None

    async def snapshot(self) -> BookSnapshot:
        return EMPTY_BOOK


__all__ = [
    "EMPTY_BOOK",
    "BookSnapshot",
    "EmptyShooterBookStore",
    "JsonShooterBookStore",
    "ShooterBookEntry",
    "ShooterBookStore",
    "account_dir",
    "identity_digest",
    "is_set",
    "load_snapshot",
]
