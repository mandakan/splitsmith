"""Storage round-trip for export deliverables (worker fleet).

The ``export`` / ``match_export`` job bodies write their deliverables --
lossless ``exports/*_trimmed.mp4``, ``*.fcpxml``, ``*_splits.csv``,
``*_report.txt``, ``*_overlay.mov``, per-cam trims, and the stitched
``<project>-match.fcpxml`` -- to the job's local ``exports/`` dir. On a
hosted worker that filesystem is ephemeral and invisible to the API
container, so the artifacts have to push to object storage on produce and
pull back on download.

This mirrors the audit-trim cache in :mod:`splitsmith.ui.audio`
(``_storage_trim_key`` / ``_try_pull_trim_from_storage`` /
``_try_push_trim_to_storage``). The key scheme is ``<scope>/exports/<name>``,
mirroring the on-disk ``exports/`` subdir, consistent with the ``trimmed/``
scheme. The exporters themselves (:mod:`splitsmith.ui.exports` /
:mod:`splitsmith.ui.match_exports`) stay pure ``Path`` I/O with no storage
seam -- the push happens here, after they return.

Every helper is a no-op in local mode (no storage bound or no per-project
scope) so desktop behaviour is unchanged.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .project import MatchProject

if TYPE_CHECKING:
    from .exports import StageExportResult

logger = logging.getLogger(__name__)

# Suffixes pushed/pulled as streams (large binaries) rather than buffered
# whole into memory via read_bytes/write_bytes. Everything else (FCPXML,
# CSV, report, SRT, YouTube JSON) is small text.
_BINARY_SUFFIXES = {".mp4", ".mov"}


def _storage_export_key(project: MatchProject | None, local_file: Path) -> str | None:
    """Storage key for an export deliverable, or ``None`` in local mode.

    Key is ``<scope>/exports/<basename>``. Basenames already carry the
    stage number + slug (+ ``video_id`` for per-cam trims), so two shooters
    in different matches can't collide -- the same guarantee the
    ``trimmed/`` cache relies on.
    """
    if project is None or project._storage is None or project._storage_scope is None:
        return None
    return f"{project._storage_scope}/exports/{local_file.name}"


def pull_export_file(project: MatchProject | None, local_file: Path) -> bool:
    """Mirror an export deliverable down from storage when it isn't local.

    Returns True when the file is present locally afterwards (already there,
    or pulled). Best-effort: a storage hiccup logs and returns False rather
    than raising, so the caller (re-cut path or a 404) behaves as in local
    mode. No-op + False when storage/scope is unbound.
    """
    if local_file.exists() and local_file.stat().st_size > 0:
        return True
    key = _storage_export_key(project, local_file)
    if key is None:
        return False
    storage = project._storage  # type: ignore[union-attr]
    try:
        # _mirror_from_storage HEADs the key (no-op if absent) and does a
        # temp+rename so a torn pull never lands as a complete file.
        MatchProject._mirror_from_storage(storage, key, local_file)
    except Exception as exc:
        logger.info("export cache: pull from %s failed: %s", key, exc)
        return False
    return local_file.exists() and local_file.stat().st_size > 0


def push_export_file(project: MatchProject | None, local_file: Path) -> None:
    """Push one export deliverable to storage.

    Binaries (``.mp4`` / ``.mov``) stream via ``upload_stream`` (multipart,
    never buffered whole -- overlays can be multi-GB); small text goes via
    ``write_bytes``. Best-effort: a push failure logs at INFO and returns;
    the local file is the source of truth for the current job.
    """
    key = _storage_export_key(project, local_file)
    if key is None:
        return
    if not (local_file.exists() and local_file.stat().st_size > 0):
        return
    storage = project._storage  # type: ignore[union-attr]
    try:
        if local_file.suffix.lower() in _BINARY_SUFFIXES:
            with local_file.open("rb") as f:
                storage.upload_stream(key, f)
        else:
            storage.write_bytes(key, local_file.read_bytes())
    except Exception as exc:
        logger.info("export cache: push to %s failed: %s", key, exc)


def push_stage_export_outputs(project: MatchProject | None, result: StageExportResult) -> None:
    """Push every artifact a :func:`exports.export_stage` run produced.

    Iterates the populated single-file paths plus each per-cam secondary
    trim. ``None`` entries (a skipped artifact) are ignored.
    """
    paths: list[Path] = []
    for p in (
        result.trimmed_video_path,
        result.csv_path,
        result.fcpxml_path,
        result.report_path,
        result.overlay_path,
    ):
        if p is not None:
            paths.append(p)
    paths.extend(result.secondary_trimmed_paths.values())
    for p in paths:
        push_export_file(project, p)
