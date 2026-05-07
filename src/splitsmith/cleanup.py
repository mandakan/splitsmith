"""Tiered project cleanup -- plan + apply (issue: reclaim disk space).

The disk footprint of a project grows fast: rendered overlays and lossless
trims are hundreds of MB to multi-GB each, audit-mode trims and extracted
audio are similar order. Most of these are recreatable from the source
video + audit JSON, but recomputing them costs minutes of ffmpeg time, so
the user picks which categories to drop.

Two-phase API:

- :func:`plan_cleanup` walks the project's resolved directories and returns
  a :class:`CleanupPlan` (file list + per-category totals). Pure: no
  deletion happens here. Callers can preview the plan, render it, decide.
- :func:`apply_cleanup` walks the plan, unlinks each file, and returns a
  :class:`CleanupResult`. Records to ``<root>/.cleanup.log`` (JSONL) when
  ``root`` is given so the user has an audit trail of what was reclaimed.

Categories are independent toggles, NOT a strict hierarchy. The CLI and
SPA both build the requested set from per-category flags / checkboxes.

What is NEVER touched:

- ``project.json`` -- contains user's video assignments and beep times.
- ``raw/`` -- the symlinks that point at the user's original sources.
- The original source video files themselves.

The :class:`CleanupCategory.AUDIT_DATA` bucket *is* destructive (drops
the user's audit work). It is excluded from the convenience ``--all`` /
"select all" affordance and gated by an explicit opt-in.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from .ui.project import MatchProject

# Filename for the per-project cleanup audit trail. JSONL so multiple
# cleanups append cleanly. Hidden so it doesn't clutter Finder.
CLEANUP_LOG_FILENAME = ".cleanup.log"


class CleanupCategory(StrEnum):
    """Logical buckets the user can independently toggle.

    The string values are the wire format -- CLI flags use them with the
    ``-`` separator (``exports-light``, ``audit-data``) and the SPA passes
    them through unchanged. Adding a new bucket means: extend this enum,
    extend the glob mapping in :func:`_iter_paths`, and add the SPA
    checkbox + CLI flag.
    """

    CACHES = "caches"
    EXPORTS_LIGHT = "exports-light"
    EXPORTS_OVERLAYS = "exports-overlays"
    EXPORTS_TRIMS = "exports-trims"
    AUDIT_TRIMS = "audit-trims"
    AUDIO = "audio"
    AUDIT_DATA = "audit-data"


# Categories considered safe enough to include in --all / "select all".
# AUDIT_DATA is excluded; users opt in explicitly via --include-audit.
SAFE_CATEGORIES: frozenset[CleanupCategory] = frozenset(
    c for c in CleanupCategory if c is not CleanupCategory.AUDIT_DATA
)


class CleanupItem(BaseModel):
    """One file the plan would unlink."""

    path: Path
    size_bytes: int
    category: CleanupCategory


class CleanupTotals(BaseModel):
    """Per-category roll-up surfaced in the plan + UI dialog."""

    file_count: int = 0
    bytes: int = 0


class CleanupPlan(BaseModel):
    """Side-effect description returned by :func:`plan_cleanup`.

    The plan is sortable and JSON-serialisable; the SPA renders totals
    and the CLI prints them via Rich. ``items`` is sorted by (category,
    path) so the CLI plan output and the SPA preview agree.
    """

    items: list[CleanupItem] = Field(default_factory=list)
    totals_by_category: dict[CleanupCategory, CleanupTotals] = Field(default_factory=dict)
    total_bytes: int = 0
    total_file_count: int = 0


class CleanupResult(BaseModel):
    """Outcome of :func:`apply_cleanup`."""

    deleted: list[Path] = Field(default_factory=list)
    failed: list[tuple[Path, str]] = Field(default_factory=list)
    bytes_freed: int = 0


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _iter_paths(
    project: MatchProject,
    root: Path,
    category: CleanupCategory,
) -> Iterable[Path]:
    """Yield every file the given category would target.

    All directory access goes through ``MatchProject`` resolvers so path
    overrides (audio_dir, exports_dir, etc.) are respected. Missing dirs
    yield nothing rather than raising -- a fresh project that has never
    run a job has empty cache dirs and the cleanup should report zero,
    not crash.

    Symlinks are NOT yielded -- defence-in-depth so a user-placed
    symlink (e.g. someone pointing audio_dir at a shared drive with a
    softlink convention) can never resolve into the original source.
    """
    if category is CleanupCategory.CACHES:
        # Thumbnails (jpg + small preview MP4s), ffprobe JSONs, scoreboard
        # API cache, waveform peaks JSON sitting next to the audio cache.
        for p in _glob(project.thumbs_path(root), "*"):
            yield p
        for p in _glob(project.probes_path(root), "*.json"):
            yield p
        for p in _glob(root / "scoreboard" / "cache", "**/*"):
            yield p
        for p in _glob(project.audio_path(root), "*.peaks-*.json"):
            yield p

    elif category is CleanupCategory.EXPORTS_LIGHT:
        exp = project.exports_path(root)
        for pat in ("*.fcpxml", "*.csv", "*_report.txt"):
            for p in _glob(exp, pat):
                yield p

    elif category is CleanupCategory.EXPORTS_OVERLAYS:
        for p in _glob(project.exports_path(root), "*_overlay.mov"):
            yield p

    elif category is CleanupCategory.EXPORTS_TRIMS:
        # Captures both ``stage<N>_<slug>_trimmed.mp4`` (primary) and
        # ``stage<N>_<slug>_cam_<id>_trimmed.mp4`` (per-camera trims).
        for p in _glob(project.exports_path(root), "*_trimmed.mp4"):
            yield p

    elif category is CleanupCategory.AUDIT_TRIMS:
        for p in _glob(project.trimmed_path(root), "*.mp4"):
            yield p

    elif category is CleanupCategory.AUDIO:
        # Peaks JSONs deliberately live in the CACHES bucket (they're
        # tiny and re-derivable from the audio); the AUDIO bucket only
        # carries the heavyweight extracted WAVs.
        for p in _glob(project.audio_path(root), "*.wav"):
            yield p

    elif category is CleanupCategory.AUDIT_DATA:
        audit = project.audit_path(root)
        for pat in ("stage*.json", "stage*.json.bak"):
            for p in _glob(audit, pat):
                yield p


def _glob(directory: Path, pattern: str) -> Iterable[Path]:
    """Glob ``directory`` for ``pattern`` while tolerating missing dirs.

    ``rglob`` is used when the pattern starts with ``**`` so the
    scoreboard cache (which has subdirs by content_type) is fully
    swept. Symlinks and non-files are skipped at the source.
    """
    if not directory.exists():
        return
    if pattern.startswith("**"):
        # rglob('**/*') over a missing dir would have raised; we guarded
        # above. Strip the leading '**/' so rglob does not double-prefix.
        suffix = pattern[3:] or "*"
        iterator = directory.rglob(suffix)
    else:
        iterator = directory.glob(pattern)
    for p in iterator:
        if p.is_symlink():
            continue
        if not p.is_file():
            continue
        yield p


def _safe_under_raw(project: MatchProject, root: Path, candidate: Path) -> bool:
    """Defence-in-depth: refuse any item that resolves under ``raw/``.

    The cleanup never globs into ``raw/``, so this should never fire,
    but a typo in a future glob (or a symlink we missed) shouldn't be
    able to delete a source-video reference.
    """
    try:
        raw = project.raw_path(root).resolve()
    except OSError:
        return True
    try:
        candidate.resolve().relative_to(raw)
    except (OSError, ValueError):
        return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def plan_cleanup(
    project: MatchProject,
    root: Path,
    categories: Iterable[CleanupCategory],
) -> CleanupPlan:
    """Build a :class:`CleanupPlan` for the given categories.

    Idempotent and read-only: never deletes, never mutates the project.
    Empty selection returns an empty plan. Categories whose target
    directory is missing contribute zero items but still appear in
    ``totals_by_category`` (with zeros) so the SPA can show the row
    without re-checking.
    """
    requested: set[CleanupCategory] = set(categories)

    items: list[CleanupItem] = []
    totals: dict[CleanupCategory, CleanupTotals] = {c: CleanupTotals() for c in requested}

    for category in requested:
        for path in _iter_paths(project, root, category):
            if not _safe_under_raw(project, root, path):
                # Should never happen with the current globs; guard kept
                # so a future bug can't escalate into deleting raw refs.
                continue
            try:
                size = path.lstat().st_size
            except OSError:
                continue
            items.append(CleanupItem(path=path, size_bytes=size, category=category))
            t = totals[category]
            t.file_count += 1
            t.bytes += size

    items.sort(key=lambda it: (it.category.value, str(it.path)))
    return CleanupPlan(
        items=items,
        totals_by_category=totals,
        total_bytes=sum(t.bytes for t in totals.values()),
        total_file_count=sum(t.file_count for t in totals.values()),
    )


def apply_cleanup(
    plan: CleanupPlan,
    *,
    root: Path | None = None,
) -> CleanupResult:
    """Delete every file in ``plan``; never raises on individual failures.

    Errors are recorded per-file in :attr:`CleanupResult.failed` so the
    caller can surface them. Already-missing files (e.g. concurrent
    delete by another process) are not failures: ``unlink(missing_ok=True)``
    silently succeeds. Bytes are tallied from the planned size, not
    re-stat'd post-delete.

    When ``root`` is given, appends one JSONL line to
    ``<root>/.cleanup.log`` summarising the run. Missing log directory
    is created. Logging is best-effort: a write failure does not
    invalidate an otherwise-successful cleanup.
    """
    deleted: list[Path] = []
    failed: list[tuple[Path, str]] = []
    bytes_freed = 0

    for item in plan.items:
        try:
            item.path.unlink(missing_ok=True)
        except OSError as exc:
            failed.append((item.path, str(exc)))
            continue
        deleted.append(item.path)
        bytes_freed += item.size_bytes

    result = CleanupResult(deleted=deleted, failed=failed, bytes_freed=bytes_freed)

    if root is not None:
        try:
            _append_log(root, plan, result)
        except OSError:
            pass

    return result


def _append_log(root: Path, plan: CleanupPlan, result: CleanupResult) -> None:
    """Append one JSONL summary line to ``<root>/.cleanup.log``.

    Schema is intentionally compact: the file is for human review, not
    rehydration. Bumping fields here is safe -- old lines stay valid.
    """
    log_path = root / CLEANUP_LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "categories": sorted({item.category.value for item in plan.items}),
        "deleted_count": len(result.deleted),
        "failed_count": len(result.failed),
        "bytes_freed": result.bytes_freed,
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
