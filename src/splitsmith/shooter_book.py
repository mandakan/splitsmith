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

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from . import user_config
from .async_bridge import run_sync
from .identity import _LOGO_RE, ShooterIdentity, logo_name, sniff_logo

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


async def save_identity(
    store: ShooterBookStore,
    *,
    shooter_id: int,
    identity: ShooterIdentity,
    label: str | None,
    logo_bytes: bytes | None,
) -> None:
    """Write a shooter's whole identity to the book (spec 2026-10-08): the
    logo stored in the book's own files from ``logo_bytes`` (same content
    name as the match's); an identity that sets nothing removes the entry.
    A logo that cannot be stored is left out, logged; the rest is saved."""
    if not is_set(identity):
        await store.delete(shooter_id)
        return
    logo = None
    if identity.logo is not None and logo_bytes is not None:
        try:
            logo = await store.put_logo(logo_bytes)
        except Exception as exc:  # noqa: BLE001 -- storage errors are their own types (botocore)
            logger.warning("shooter book: the logo for %s was not saved (%s)", shooter_id, exc)
    elif identity.logo is not None:
        # The match's file is not on this disk: keep the book's own copy of
        # the same logo rather than wiping it.
        existing = await store.get(shooter_id)
        if existing is not None and existing.identity.logo == identity.logo:
            logo = identity.logo
    await store.put(
        ShooterBookEntry(
            shooter_id=shooter_id,
            identity=identity.model_copy(update={"logo": logo}),
            label=(label or "")[:LABEL_MAX] or None,
        )
    )


@dataclass(frozen=True)
class BackfillCandidate:
    """One shooter as an existing match has them, for the one-time fill:
    ``read_logo`` returns the logo's bytes (or ``None``) only when called."""

    shooter_id: int
    identity: ShooterIdentity
    label: str | None
    updated_at: datetime
    read_logo: Callable[[], bytes | None]


async def backfill(store: ShooterBookStore, candidates: Sequence[BackfillCandidate]) -> int:
    """Seed the book from existing matches (spec 2026-10-08): per SSI shooter
    id, the most recently updated match that set a look; an id the book
    already holds is left as it is (the user's own edits win). Writes no
    match. The number of entries written."""
    latest: dict[int, BackfillCandidate] = {}
    for candidate in candidates:
        if not is_set(candidate.identity):
            continue
        held = latest.get(candidate.shooter_id)
        if held is None or candidate.updated_at > held.updated_at:
            latest[candidate.shooter_id] = candidate
    written = 0
    for sid, candidate in latest.items():
        if await store.get(sid) is not None:
            continue
        logo_bytes = None
        if candidate.identity.logo is not None:
            try:
                # Off the event loop: hosted, this is a storage read.
                logo_bytes = await asyncio.to_thread(candidate.read_logo)
            except Exception as exc:  # noqa: BLE001 -- the look without its logo is still worth keeping
                logger.info("shooter book: could not read a logo for %s (%s)", sid, exc)
        await save_identity(
            store, shooter_id=sid, identity=candidate.identity, label=candidate.label, logo_bytes=logo_bytes
        )
        written += 1
    if written:
        logger.info("shooter book: filled %d entries from existing matches", written)
    return written


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


BACKFILL_MARKER = ".backfilled"


class JsonShooterBookStore:
    """The local book: one JSON file and a folder of content-named logos.
    With a ``backfill_source`` (the app's own store), the first read fills it
    once from the matches on this machine, marked by a file beside it."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        backfill_source: Callable[[], Awaitable[Sequence[BackfillCandidate]]] | None = None,
    ) -> None:
        self._root = root
        self._backfill_source = backfill_source

    async def _ensure_backfilled(self) -> None:
        folder = self._dir()
        if self._backfill_source is None or folder is None or (folder / BACKFILL_MARKER).exists():
            return
        try:
            await backfill(self, await self._backfill_source())
        except Exception as exc:  # noqa: BLE001 -- a fill that fails is retried on the next start
            logger.warning("shooter book: the fill from existing matches failed (%s)", exc)
            return
        folder.mkdir(parents=True, exist_ok=True)
        (folder / BACKFILL_MARKER).write_text(_now().isoformat())

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
        await self._ensure_backfilled()
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
        if folder is None or not _LOGO_RE.match(name or ""):
            return None
        return _safe_file(folder / FILES_DIRNAME, name)

    async def snapshot(self) -> BookSnapshot:
        await self._ensure_backfilled()
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
    "BACKFILL_MARKER",
    "EMPTY_BOOK",
    "BackfillCandidate",
    "backfill",
    "BookSnapshot",
    "EmptyShooterBookStore",
    "JsonShooterBookStore",
    "ShooterBookEntry",
    "ShooterBookStore",
    "account_dir",
    "identity_digest",
    "is_set",
    "load_snapshot",
    "save_identity",
]
