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

from .. import youtube_sidecar
from ..audit_revision import audit_revision
from ..match_model import load_match_or_legacy
from ..match_project import MatchProject
from ..youtube import oauth

#: Kinds this desktop can run. Hosted may queue a kind a newer desktop
#: knows; an older one refuses it with a reason instead of guessing.
RUNNABLE_KINDS = frozenset({"shot_detect", "render_upload"})

STAGE_CHANGED = "the stage changed after this was requested; ask again"
YOUTUBE_NOT_CONNECTED = "YouTube is not connected on the desktop"


def _shooter_root(match_root: Path, slug: str) -> Path | None:
    _, shooter_roots = load_match_or_legacy(match_root)
    return shooter_roots.get(slug)


def _audit_path(match_root: Path, slug: str, stage_number: int) -> Path | None:
    root = _shooter_root(match_root, slug)
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
    if kind == "render_upload":
        return _render_upload_refusal(match_root, command)
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


def _render_upload_refusal(match_root: Path, command: dict) -> str | None:
    slug = command.get("slug")
    if not isinstance(slug, str):
        return "the request names no shooter"
    try:
        if _shooter_root(match_root, slug) is None:
            return f"shooter {slug!r} is not in this match on the desktop"
    except (OSError, ValueError):
        return "the match could not be read on the desktop"
    if oauth.load_connection() is None:
        return YOUTUBE_NOT_CONNECTED
    return None


def prior_result(match_root: Path, command: dict) -> dict | None:
    """The upload this very command already made, from its sidecar record.

    A render-upload whose completion never reached hosted is re-claimed
    after its lease lapses; this is what stops the re-run from uploading
    twice. It has to run before the render, which rewrites the sidecar
    and would drop the record."""
    if command.get("kind") != "render_upload":
        return None
    slug, command_id = command.get("slug"), command.get("id")
    if not isinstance(slug, str) or not command_id:
        return None
    try:
        root = _shooter_root(match_root, slug)
        if root is None:
            return None
        exports = MatchProject.load(root).exports_path(root)
    except (OSError, ValueError):
        return None
    for path in sorted(exports.glob("*-youtube.json")):
        try:
            record = youtube_sidecar.load_sidecar(path).upload
        except (OSError, ValueError):
            continue
        if record is not None and record.command_id == command_id:
            return {"video_id": record.video_id, "url": record.url, "channel_title": record.channel_title}
    return None
