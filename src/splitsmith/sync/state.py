"""Local digest cache for the desktop-to-hosted sync push (#631).

``SyncState`` records the (sha256, size, mtime_ns) of every remote media
object this match has already pushed, keyed by remote key. The push
planner (:mod:`splitsmith.sync.plan`) compares a candidate file's current
size + mtime against the recorded entry to decide whether it needs
pushing again - the same rsync-style shortcut ``rsync --size-only``'s
cousin uses, cheap enough to run before every push without hashing
untouched multi-gigabyte trims. The sha256 itself is only ever verified
by the push executor (Task 8) at upload time; it is not recomputed here.

Persisted at ``<match-root>/sync_state.json``, written atomically via
:func:`splitsmith.match_project.atomic_write_json` so a crash mid-push never
corrupts the cache. A missing or corrupt file is not an error - it just
means "nothing has been pushed yet," which makes the next plan a full
plan.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from ..match_project import atomic_write_json

SYNC_STATE_FILE = "sync_state.json"


class SyncedItem(BaseModel):
    """Digest of one remote media object as of its last successful push."""

    sha256: str
    size: int
    mtime_ns: int


class SyncState(BaseModel):
    """The full local sync digest cache for one match."""

    schema_version: int = 1
    last_synced_at: datetime | None = None
    items: dict[str, SyncedItem] = Field(default_factory=dict)  # remote key -> digest


def load_sync_state(match_root: Path) -> SyncState:
    """Load ``sync_state.json`` from ``match_root``.

    A missing file, unreadable file, or a file that fails to parse as
    JSON or validate against :class:`SyncState` all return a fresh
    ``SyncState()`` rather than raising - a corrupt cache should degrade
    to "push everything again," never block the push.
    """
    path = match_root / SYNC_STATE_FILE
    if not path.exists():
        return SyncState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return SyncState()
    try:
        return SyncState.model_validate(data)
    except ValidationError:
        return SyncState()


def save_sync_state(match_root: Path, state: SyncState) -> None:
    """Atomically persist ``state`` to ``<match_root>/sync_state.json``."""
    atomic_write_json(match_root / SYNC_STATE_FILE, state.model_dump(mode="json"))
