"""Base-snapshot store for the three-way sync merge.

``sync_base/`` under the match root holds each doc's body exactly as of
the last completed sync leg - the common ancestor the merge diffs both
sides against. Updated at two points in a sync run: after applying a
pull (base := the pulled remote snapshot) and after each successful doc
PUT (base := the pushed body). A missing or corrupt file reads as "never
synced", which the merge treats as an empty base: everything on each
side counts as that side's change - correct, just less discriminating.

Keys are :func:`splitsmith.sync.plan.doc_identity_key` strings; the
slash-separated segments become nested directories, so the layout reads
as ``sync_base/match.json``, ``sync_base/project/<slug>.json``,
``sync_base/audit/<slug>/<stage>.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..match_project import atomic_write_json

BASE_DIR = "sync_base"


def _base_path(match_root: Path, key: str) -> Path:
    return match_root / BASE_DIR / f"{key}.json"


def load_base_doc(match_root: Path, key: str) -> dict | None:
    """The base snapshot for ``key``, or None when absent/corrupt."""
    path = _base_path(match_root, key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_base_doc(match_root: Path, key: str, body: dict) -> None:
    """Atomically persist ``body`` as the base snapshot for ``key``."""
    path = _base_path(match_root, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, body)
