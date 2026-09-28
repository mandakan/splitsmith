"""Desktop side of the command queue (#1100, spec 2026-09-28): the checks a
claimed command passes before the desktop runs it.

A reset re-detect replaces a stage's shots. The phone recorded the stage
audit's revision when it asked; if the local audit no longer has that
revision (after the pre-run pull), something changed that the request did
not see (an edit on either side, or this very command already ran once
before its lease lapsed), so the command is refused rather than run.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..audit_revision import audit_revision
from ..match_model import load_match_or_legacy

#: Kinds this desktop can run. Hosted may queue a kind a newer desktop
#: knows; an older one refuses it with a reason instead of guessing.
RUNNABLE_KINDS = frozenset({"shot_detect"})

STAGE_CHANGED = "the stage changed after this was requested; ask again"


def _audit_path(match_root: Path, slug: str, stage_number: int) -> Path | None:
    _, shooter_roots = load_match_or_legacy(match_root)
    root = shooter_roots.get(slug)
    if root is None:
        return None
    return root / "audit" / f"stage{stage_number}.json"


def local_stage_revision(match_root: Path, slug: str, stage_number: int) -> str:
    """``audit_revision`` of the local stage audit, the same hash hosted
    recorded; a missing or unreadable file reads as "no doc"."""
    path = _audit_path(match_root, slug, stage_number)
    doc = None
    if path is not None:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            doc = None
    return audit_revision(doc)


def refuse_reason(match_root: Path, command: dict) -> str | None:
    """Why this desktop will not run ``command``, or ``None`` to run it."""
    kind = command.get("kind")
    if kind not in RUNNABLE_KINDS:
        return f"this desktop cannot run {kind!r} requests yet; update the desktop app"
    slug, stage_number = command.get("slug"), command.get("stage_number")
    if not isinstance(slug, str) or not isinstance(stage_number, int):
        return "the request names no stage"
    try:
        if _audit_path(match_root, slug, stage_number) is None:
            return f"shooter {slug!r} is not in this match on the desktop"
    except (OSError, ValueError, FileNotFoundError):
        return "the match could not be read on the desktop"
    expected = command.get("expected_revision")
    if expected is not None and local_stage_revision(match_root, slug, stage_number) != expected:
        return STAGE_CHANGED
    return None
