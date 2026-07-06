"""LRU, size-capped eviction for the self-hosted worker's source cache.

The self-hosted ``splitsmith agent`` mirrors raw videos (and any derived
artifacts) from R2 into ``SPLITSMITH_PROJECTS_DIR`` so that successive jobs on
the same file skip the download (see
``MatchProject.resolve_video_path``). On the agent that directory is a *pure
cache*: every byte is reconstructable from Postgres + R2, so it can be evicted
freely. Left uncapped it grows until the box's disk fills - raw head-cam files
run tens of MB to multi-GB and a match has many - which has produced I/O
errors before. This module runs a post-drain sweep that keeps the cache under a
byte budget, evicting least-recently-used files first.

Recency is the file mtime. ``resolve_video_path`` bumps it on every cache hit
(``os.utime``), so mtime tracks last-use even on ``noatime`` mounts where atime
never advances. Eviction is keyed on total bytes, not file count: one stale
multi-GB raw is a better thing to drop than a hundred small fresh artifacts.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Operator knob for the cache budget, in gigabytes. Default is generous enough
# for a handful of full matches yet bounded so a home box's disk cannot fill
# (raw head-cam files run tens of MB to multi-GB). Set to 0 (or negative) to
# disable eviction entirely.
ENV_MAX_GB = "SPLITSMITH_SOURCE_CACHE_MAX_GB"
_DEFAULT_MAX_GB = 20.0
_BYTES_PER_GB = 1024**3


def configured_cache_max_bytes(env: Mapping[str, str] = os.environ) -> int | None:
    """Resolve the cache byte budget from the environment.

    Returns the cap in bytes, or ``None`` when eviction is disabled (a cap of
    zero or below). An unparseable value falls back to the default rather than
    failing the drain - a typo in an env var must not take the worker down.
    """
    raw = env.get(ENV_MAX_GB, "").strip()
    if not raw:
        gb = _DEFAULT_MAX_GB
    else:
        try:
            gb = float(raw)
        except ValueError:
            logger.warning("%s=%r is not a number; using default %.1f GB", ENV_MAX_GB, raw, _DEFAULT_MAX_GB)
            gb = _DEFAULT_MAX_GB
    if gb <= 0:
        return None
    return int(gb * _BYTES_PER_GB)


@dataclass(frozen=True)
class SweepResult:
    """Outcome of one :func:`sweep_source_cache` pass, for logging/audit."""

    scanned_files: int
    total_bytes_before: int
    evicted_files: int
    evicted_bytes: int
    total_bytes_after: int


@dataclass(frozen=True)
class _Entry:
    path: Path
    size: int
    mtime: float


def sweep_source_cache(cache_root: Path, max_bytes: int) -> SweepResult:
    """Evict least-recently-used files under ``cache_root`` down to ``max_bytes``.

    Walks every regular file below ``cache_root``, and while the total exceeds
    ``max_bytes`` deletes files oldest-mtime-first until the budget is met.
    Directories left empty by eviction are pruned. A missing ``cache_root`` is a
    no-op. Deletion errors are logged and skipped - a single unlink failure
    must not abort the sweep or fail the drain that triggered it.
    """
    root = Path(cache_root)
    if not root.exists():
        return SweepResult(0, 0, 0, 0, 0)

    entries = _scan(root)
    total_before = sum(e.size for e in entries)

    if total_before <= max_bytes:
        return SweepResult(
            scanned_files=len(entries),
            total_bytes_before=total_before,
            evicted_files=0,
            evicted_bytes=0,
            total_bytes_after=total_before,
        )

    # Oldest first: the least-recently-used file is the first to go.
    entries.sort(key=lambda e: e.mtime)
    total = total_before
    evicted_files = 0
    evicted_bytes = 0
    for entry in entries:
        if total <= max_bytes:
            break
        try:
            entry.path.unlink()
        except OSError as exc:  # pragma: no cover - defensive; logged not fatal
            logger.warning("source-cache eviction failed for %s: %s", entry.path, exc)
            continue
        total -= entry.size
        evicted_files += 1
        evicted_bytes += entry.size

    _prune_empty_dirs(root)

    return SweepResult(
        scanned_files=len(entries),
        total_bytes_before=total_before,
        evicted_files=evicted_files,
        evicted_bytes=evicted_bytes,
        total_bytes_after=total,
    )


def _scan(root: Path) -> list[_Entry]:
    """Collect every regular file under ``root`` with its size and mtime.

    Symlinks are skipped (``lstat`` + ``is_symlink``) so the cache can never be
    tricked into unlinking a target outside itself. Files that vanish mid-scan
    are simply dropped.
    """
    entries: list[_Entry] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            try:
                stat = path.lstat()
            except OSError:
                continue
            if path.is_symlink():
                continue
            entries.append(_Entry(path=path, size=stat.st_size, mtime=stat.st_mtime))
    return entries


def _prune_empty_dirs(root: Path) -> None:
    """Remove directories left empty by eviction, deepest-first, keeping ``root``."""
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        directory = Path(dirpath)
        if directory == root:
            continue
        try:
            directory.rmdir()
        except OSError:
            # Not empty (still holds live files) or already gone - both fine.
            pass
