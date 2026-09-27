"""Per-match local auto-sync state (spec 2026-09-27 s6).

Lives in ``<match-root>/auto_sync.json``, not in ``sync_state.json``:
``run_sync`` saves that file repeatedly from its in-memory copy, so a
toggle made during a sync would be overwritten. Root-level files are
never in the push plan, so nothing here syncs. One writer helper
load-modify-saves under a module lock; every writer lives in the local
server process.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from ..match_project import atomic_write_json
from .state import SyncState

AUTO_SYNC_FILE = "auto_sync.json"

_lock = threading.Lock()


class AutoRunSummary(BaseModel):
    """The last automatic run, for the SyncCard."""

    at: datetime
    ok: bool
    message: str
    conflicts: int = 0
    notes: int = 0


class AutoSyncPrefs(BaseModel):
    #: ``None`` = default: on once the match has been synced.
    enabled: bool | None = None
    #: Upload full-resolution audit trims too (spec 2026-09-27 v1.1). Off:
    #: hosted plays the 720p rendition and full trims leave R2.
    full_media: bool = False
    #: reconcile step key -> input key the step last failed with.
    reconcile_failures: dict[str, str] = Field(default_factory=dict)
    last_auto: AutoRunSummary | None = None
    #: Set when a sync found this match gone from hosted after it had been
    #: synced (deleted there). Sync stays off until "Publish again".
    hosted_deleted_at: datetime | None = None


def load_auto_prefs(match_root: Path) -> AutoSyncPrefs:
    """Missing or unreadable file -> defaults, never an error."""
    path = match_root / AUTO_SYNC_FILE
    try:
        return AutoSyncPrefs.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError):
        return AutoSyncPrefs()


def update_auto_prefs(match_root: Path, fn: Callable[[AutoSyncPrefs], None]) -> AutoSyncPrefs:
    """Load, apply ``fn`` in place, save atomically; returns the saved prefs."""
    with _lock:
        prefs = load_auto_prefs(match_root)
        fn(prefs)
        atomic_write_json(match_root / AUTO_SYNC_FILE, prefs.model_dump(mode="json"))
        return prefs


def auto_sync_effective(prefs: AutoSyncPrefs, sync_state: SyncState, *, global_enabled: bool) -> bool:
    """Whether the service should watch this match. Needs a first sync
    (there is no hosted mirror to watch before it), the global switch,
    and the per-match flag unless that is left at its default."""
    if not global_enabled or sync_state.last_synced_at is None or prefs.hosted_deleted_at is not None:
        return False
    return prefs.enabled is not False
