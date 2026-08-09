"""Push planning for the desktop-to-hosted sync MVP (#631).

``build_push_plan`` reads one local match directory and decides what a
push executor (Task 8) should upload: the match doc, each shooter's
sanitized project doc, one audit doc per stage that has been audited, and
one media item per trimmed clip (+ its ``.params.json`` sidecar, when
present) whose size or mtime has changed since the last recorded push.

This module does no network I/O and never rewrites anything on disk - it
only reads the match tree and returns a plan. Docs are always included
(they're small and the hosted upsert is idempotent); media is filtered
against ``sync_state`` so an unattended re-push doesn't re-upload
gigabytes of untouched trims.
"""

from __future__ import annotations

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
    docs: list[DocItem] = Field(default_factory=list)
    media: list[MediaItem] = Field(default_factory=list)  # only items that differ from sync_state
    media_skipped: int = 0
    errors: list[str] = Field(default_factory=list)  # non-empty -> push must not run


def _remote_key(match_id: str, slug: str, basename: str) -> str:
    return f"matches/{match_id}/shooters/{slug}/trimmed/{basename}"


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

    docs: list[DocItem] = [DocItem(kind="match", body=match.model_dump(mode="json"))]
    media: list[MediaItem] = []
    media_skipped = 0
    errors: list[str] = []

    for slug in match.shooters:
        shooter_root = shooter_roots[slug]
        project = MatchProject.load(shooter_root)

        for stage_number, path in absolute_path_videos(project):
            errors.append(
                f"shooter {slug!r} stage {stage_number}: video path is absolute and cannot "
                f"sync (video_id is a hash of the path; rewriting it is not safe): {path}"
            )

        docs.append(
            DocItem(kind="project", slug=slug, body=sanitize_project_doc(project.model_dump(mode="json")))
        )

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
                docs.append(DocItem(kind="audit", slug=slug, stage_number=stage_number, body=body))

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
        errors=errors,
    )
