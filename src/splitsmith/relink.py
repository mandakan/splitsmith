"""Relink registered video sources after they move on disk.

Project ``raw/<name>`` entries are typically symlinks to the original
recordings; ``project.json`` stores the relative ``raw/<name>`` path.
When the originals move (e.g. onto a network share with a different
folder layout), nothing in ``project.json`` needs to change -- only the
symlink targets under ``raw/``.

This module provides pure helpers used by both the API and tests:

- :func:`inspect_links` reports the per-video link status (ok / broken /
  missing / not-a-symlink) for the current project.
- :func:`index_search_root` recursively walks a folder and indexes
  videos by lowercase basename.
- :func:`plan_relink` matches the project's registered videos against
  that index, defaulting to the single-candidate match when the basename
  is unique inside the search root.
- :func:`apply_relink` rewrites the symlinks atomically (delete + create
  in one step, mirroring ``ln -sfn``).

All functions are pure aside from :func:`apply_relink`, which is the
single place that mutates the filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .ui.project import VIDEO_EXTENSIONS, MatchProject

LinkStatus = Literal["ok", "broken", "missing_link", "not_a_symlink"]


@dataclass(frozen=True)
class LinkInfo:
    """Filesystem state of one ``raw/<name>`` entry.

    ``target`` is the symlink target as stored on disk (may be relative).
    ``status`` reduces the four-state matrix to a single label the UI
    can colour-code.
    """

    video_id: str
    name: str
    link_path: Path
    target: Path | None
    is_symlink: bool
    target_exists: bool
    status: LinkStatus


@dataclass(frozen=True)
class RelinkCandidate:
    """One row in the relink plan: registered video + matches found in
    the search root.

    ``chosen_path`` defaults to the only candidate when exactly one was
    found, leaves ``None`` for zero or many (the UI surfaces ambiguity).
    """

    video_id: str
    name: str
    link_path: Path
    current_target: Path | None
    current_status: LinkStatus
    candidates: list[Path] = field(default_factory=list)
    chosen_path: Path | None = None

    @property
    def ambiguous(self) -> bool:
        return len(self.candidates) > 1

    @property
    def found(self) -> bool:
        return bool(self.candidates)


@dataclass(frozen=True)
class AppliedRelink:
    """Result of one applied symlink rewrite."""

    video_id: str
    name: str
    link_path: Path
    previous_target: Path | None
    new_target: Path


def _link_status(link_path: Path) -> tuple[LinkStatus, Path | None, bool, bool]:
    """Inspect a ``raw/<name>`` entry. Returns ``(status, target,
    is_symlink, target_exists)``.

    Uses ``os.path.islink`` semantics via ``Path.is_symlink`` -- a broken
    symlink is detected as a symlink whose target doesn't resolve.
    """
    if not link_path.exists() and not link_path.is_symlink():
        return "missing_link", None, False, False
    if link_path.is_symlink():
        target = Path(link_path.readlink())
        # Resolve relative targets against the link's parent so we report
        # the actual file we're pointing at.
        resolved = target if target.is_absolute() else (link_path.parent / target).resolve()
        target_exists = resolved.exists()
        return ("ok" if target_exists else "broken"), target, True, target_exists
    # Plain file -- not a symlink. Could be a registered copy (link_mode
    # = "copy") or a stray. Either way relinking doesn't apply.
    return "not_a_symlink", None, False, link_path.exists()


def inspect_links(project: MatchProject, root: Path) -> list[LinkInfo]:
    """Report current link status for every registered video.

    Stages are walked in declaration order and each video appears once;
    if the same path is registered to multiple roles, the first
    occurrence wins (matches :meth:`MatchProject.all_videos`).
    """
    raw_dir = project.raw_path(root)
    out: list[LinkInfo] = []
    seen: set[str] = set()
    for video in project.all_videos():
        link_path = (root / video.path) if not video.path.is_absolute() else video.path
        # Project paths are stored as ``raw/<name>``; the registry only
        # ever points inside ``raw_dir``. Resolving against root keeps
        # the helper correct even if the user has overridden ``raw_dir``.
        if not link_path.is_absolute():
            link_path = raw_dir / link_path.name
        key = str(link_path)
        if key in seen:
            continue
        seen.add(key)
        status, target, is_symlink, target_exists = _link_status(link_path)
        out.append(
            LinkInfo(
                video_id=video.video_id,
                name=link_path.name,
                link_path=link_path,
                target=target,
                is_symlink=is_symlink,
                target_exists=target_exists,
                status=status,
            )
        )
    return out


def index_search_root(search_root: Path) -> dict[str, list[Path]]:
    """Recursively index video files under ``search_root`` by
    lowercase basename.

    Multiple files with the same basename in different subfolders are
    all collected; callers surface the ambiguity to the user. Only files
    with extensions in :data:`VIDEO_EXTENSIONS` are indexed.

    Symlinks inside the search root are followed via ``rglob`` default
    behaviour (``Path.rglob`` does not follow dir symlinks by default,
    which is what we want -- avoids cycles on network shares).
    """
    if not search_root.exists():
        raise FileNotFoundError(f"search root does not exist: {search_root}")
    if not search_root.is_dir():
        raise NotADirectoryError(f"search root is not a directory: {search_root}")
    index: dict[str, list[Path]] = {}
    for entry in search_root.rglob("*"):
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        index.setdefault(entry.name.lower(), []).append(entry.resolve())
    # Stable order so dry-run output is deterministic for tests.
    for paths in index.values():
        paths.sort()
    return index


def plan_relink(
    links: list[LinkInfo],
    index: dict[str, list[Path]],
) -> list[RelinkCandidate]:
    """Build a relink plan: each registered video gets the candidate
    paths found in the search root (matched by lowercase basename).

    ``chosen_path`` is filled in only when exactly one candidate exists
    *and* it differs from the current target. The UI can override on
    apply.
    """
    out: list[RelinkCandidate] = []
    for info in links:
        candidates = list(index.get(info.name.lower(), []))
        chosen: Path | None = None
        if len(candidates) == 1:
            single = candidates[0]
            # Skip the no-op case so the apply step doesn't re-write
            # an already-correct symlink.
            if info.target is None or info.target.resolve() != single:
                chosen = single
        out.append(
            RelinkCandidate(
                video_id=info.video_id,
                name=info.name,
                link_path=info.link_path,
                current_target=info.target,
                current_status=info.status,
                candidates=candidates,
                chosen_path=chosen,
            )
        )
    return out


def apply_relink(
    decisions: list[tuple[Path, Path]],
) -> list[AppliedRelink]:
    """Rewrite each symlink to its new target (``ln -sfn`` equivalent).

    ``decisions`` is a list of ``(link_path, new_target)`` pairs. The
    new target is stored as an absolute path so the symlink survives
    project-root moves. The previous target is captured for the
    response so the UI can show a "was -> now" diff.

    Refuses to operate on entries that exist and are not symlinks (the
    ``not_a_symlink`` status). Callers should filter those out first.
    """
    applied: list[AppliedRelink] = []
    for link_path, new_target in decisions:
        new_target_abs = new_target if new_target.is_absolute() else new_target.resolve()
        previous: Path | None = None
        if link_path.is_symlink():
            previous = Path(link_path.readlink())
            link_path.unlink()
        elif link_path.exists():
            raise ValueError(f"refusing to overwrite non-symlink at {link_path} (status not_a_symlink)")
        link_path.parent.mkdir(parents=True, exist_ok=True)
        link_path.symlink_to(new_target_abs)
        applied.append(
            AppliedRelink(
                video_id="",  # filled in by callers that know the project
                name=link_path.name,
                link_path=link_path,
                previous_target=previous,
                new_target=new_target_abs,
            )
        )
    return applied
