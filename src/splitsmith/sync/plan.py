"""Push planning for the desktop-to-hosted sync MVP (#631).

``build_push_plan`` reads one local match directory and decides what a
push executor (Task 8) should upload: the match doc, each shooter's
sanitized project doc, one audit doc per stage that has been audited, and
one media item per trimmed clip (+ its ``.params.json`` sidecar, when
present) whose size or mtime has changed since the last recorded push.

This module does no network I/O and never rewrites anything on disk - it
only reads the match tree and returns a plan. Docs are filtered against
``sync_state.doc_hashes`` the same way media is filtered against
``sync_state.items`` (#797): a doc whose canonical-JSON sha256 matches
the last-recorded hash for its identity is skipped, so an unattended
re-push with nothing changed doesn't re-PUT every doc over WAN. Media is
filtered against ``sync_state`` so an unattended re-push doesn't
re-upload gigabytes of untouched trims.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..match_model import load_match_or_legacy
from ..match_project import MatchProject
from .docs import absolute_path_videos, sanitize_project_doc
from .state import SyncState

#: ``audit/stage<N>.json`` filename shape.
_AUDIT_FILENAME_RE = re.compile(r"^stage(\d+)\.json$")

#: ``trimmed/stage<N>_cam_<video_id>_trimmed.mp4`` filename shape.
_TRIMMED_GLOB = "stage*_cam_*_trimmed.mp4"


class DocItem(BaseModel):
    """One state doc to upsert on the hosted side."""

    kind: Literal["match", "project", "audit"]
    slug: str | None = None
    stage_number: int | None = None
    body: dict


class MediaItem(BaseModel):
    """One local file to push to object storage.

    ``sha256`` is deliberately absent: it's computed lazily by the push
    executor at upload time, not here - hashing every trim on every
    planning pass would defeat the point of the size+mtime skip check.
    """

    local_path: Path
    remote_key: str  # matches/<match_id>/shooters/<slug>/trimmed/<basename>
    size: int
    mtime_ns: int


class PushPlan(BaseModel):
    """What one local match should push, and why some of it can't."""

    match_id: str
    match_name: str
    docs: list[DocItem] = Field(default_factory=list)  # only docs whose hash differs from sync_state
    media: list[MediaItem] = Field(default_factory=list)  # only items that differ from sync_state
    media_skipped: int = 0
    docs_skipped: int = 0
    errors: list[str] = Field(default_factory=list)  # non-empty -> push must not run


def _remote_key(match_id: str, slug: str, basename: str) -> str:
    return f"matches/{match_id}/shooters/{slug}/trimmed/{basename}"


def doc_identity_key(kind: str, slug: str | None, stage_number: int | None) -> str:
    """The identity key one doc hashes/skips under - matches the URL shape
    :meth:`HostedSyncClient._doc_url` puts it at, so a doc's local digest
    key and its hosted address always agree."""
    if kind == "match":
        return "match"
    if kind == "project":
        return f"project/{slug}"
    return f"audit/{slug}/{stage_number}"


def hash_doc_body(body: dict) -> str:
    """sha256 of ``body``'s canonical JSON encoding (sorted keys, no
    whitespace) - stable regardless of dict insertion order, so an
    unrelated key-order shuffle never falsely churns the hash."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _plan_media_item(path: Path, remote_key: str, sync_state: SyncState) -> MediaItem | None:
    """Return a MediaItem for ``path``, or None if it matches what
    ``sync_state`` already has recorded for ``remote_key`` (skip)."""
    stat = path.stat()
    size = stat.st_size
    mtime_ns = stat.st_mtime_ns
    recorded = sync_state.items.get(remote_key)
    if recorded is not None and recorded.size == size and recorded.mtime_ns == mtime_ns:
        return None
    return MediaItem(local_path=path, remote_key=remote_key, size=size, mtime_ns=mtime_ns)


def _plan_doc(
    kind: Literal["match", "project", "audit"],
    body: dict,
    sync_state: SyncState,
    *,
    slug: str | None = None,
    stage_number: int | None = None,
) -> DocItem | None:
    """Return a DocItem for this doc, or None if its canonical-JSON hash
    matches what ``sync_state`` already has recorded for its identity
    (skip). Hashing is cheap - docs are small JSON, not multi-gigabyte
    trims - so this is an exact content comparison, not a size/mtime
    shortcut."""
    key = doc_identity_key(kind, slug, stage_number)
    digest = hash_doc_body(body)
    if sync_state.doc_hashes.get(key) == digest:
        return None
    return DocItem(kind=kind, slug=slug, stage_number=stage_number, body=body)


def build_push_plan(match_root: Path, *, sync_state: SyncState) -> PushPlan:
    """Plan what ``match_root`` should push, skipping unchanged media per ``sync_state``."""
    match, shooter_roots = load_match_or_legacy(match_root)
    if not match.match_id:
        return PushPlan(
            match_id="",
            match_name=match.name,
            docs=[],
            media=[],
            errors=[
                "this is a legacy single-shooter project without a match id - open it in "
                "the app to convert it to a match before syncing"
            ],
        )

    docs: list[DocItem] = []
    docs_skipped = 0
    media: list[MediaItem] = []
    media_skipped = 0
    errors: list[str] = []

    match_doc = _plan_doc("match", match.model_dump(mode="json"), sync_state)
    if match_doc is None:
        docs_skipped += 1
    else:
        docs.append(match_doc)

    for slug in match.shooters:
        shooter_root = shooter_roots[slug]
        project = MatchProject.load(shooter_root)

        for stage_number, path in absolute_path_videos(project):
            errors.append(
                f"shooter {slug!r} stage {stage_number}: video path is absolute and cannot "
                f"sync (video_id is a hash of the path; rewriting it is not safe): {path}"
            )

        project_doc = _plan_doc(
            "project", sanitize_project_doc(project.model_dump(mode="json")), sync_state, slug=slug
        )
        if project_doc is None:
            docs_skipped += 1
        else:
            docs.append(project_doc)

        audit_dir = shooter_root / "audit"
        if audit_dir.is_dir():
            for audit_file in sorted(audit_dir.iterdir()):
                audit_match = _AUDIT_FILENAME_RE.match(audit_file.name)
                if not audit_match:
                    continue
                stage_number = int(audit_match.group(1))
                try:
                    body = json.loads(audit_file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    errors.append(
                        f"shooter {slug!r} stage {stage_number}: audit file "
                        f"{audit_file.name} failed to parse and will not sync"
                    )
                    continue
                audit_doc = _plan_doc("audit", body, sync_state, slug=slug, stage_number=stage_number)
                if audit_doc is None:
                    docs_skipped += 1
                else:
                    docs.append(audit_doc)

        trimmed_dir = shooter_root / "trimmed"
        if trimmed_dir.is_dir():
            for clip_path in sorted(trimmed_dir.glob(_TRIMMED_GLOB)):
                candidates = [clip_path]
                sidecar = clip_path.with_suffix(".params.json")
                if sidecar.exists():
                    candidates.append(sidecar)
                for candidate in candidates:
                    remote_key = _remote_key(match.match_id, slug, candidate.name)
                    item = _plan_media_item(candidate, remote_key, sync_state)
                    if item is None:
                        media_skipped += 1
                    else:
                        media.append(item)

    return PushPlan(
        match_id=match.match_id,
        match_name=match.name,
        docs=docs,
        media=media,
        media_skipped=media_skipped,
        docs_skipped=docs_skipped,
        errors=errors,
    )
