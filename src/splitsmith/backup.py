"""Project export/import.

Tars the non-regeneratable parts of a :class:`MatchProject` into a single
``.tar.gz`` so a project can be moved between machines or stashed as a
disaster-recovery copy.

Inclusion policy:

* Always: ``project.json``.
* Default: ``audit/`` (hand-labeled shot corrections) and ``scoreboard/``
  (cached SSI data). Together these are the only artefacts that are
  *truly* irreplaceable.
* Optional via flags: ``trimmed/``, ``exports/``, ``raw/``, ``audio/`` -- all
  regeneratable from the raw footage, so the user opts in when they
  actually need them in the archive.
* Never: ``probes/`` and ``thumbs/`` -- pure caches, trivially regenerated.

Subdirectories whose path resolves outside the project root (via the absolute
override fields on :class:`MatchProject`) are skipped: the archive intentionally
only captures project-local data, so an import on a different machine has a
chance of working.

The archive contains a single top-level directory whose name matches the
project directory's name, plus a ``BACKUP_MANIFEST.json`` describing what was
included and which splitsmith version produced the archive.
"""

from __future__ import annotations

import io
import json
import shutil
import tarfile
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from . import __version__
from .ui.project import PROJECT_FILE, MatchProject

DEFAULT_DIRS: tuple[str, ...] = ("audit", "scoreboard")
OPTIONAL_DIRS: frozenset[str] = frozenset({"raw", "audio", "trimmed", "exports"})
NEVER_DIRS: frozenset[str] = frozenset({"probes", "thumbs"})

MANIFEST_NAME = "BACKUP_MANIFEST.json"


class SkippedDir(BaseModel):
    name: str
    reason: str
    resolved_path: str | None = None


class ExportResult(BaseModel):
    archive_path: Path
    bytes_written: int
    included: list[str]
    skipped: list[SkippedDir]


class ImportResult(BaseModel):
    project_root: Path
    project_name: str
    manifest: dict[str, object] | None = Field(default=None)


class BackupError(Exception):
    """Raised when an export or import cannot proceed."""


def _resolve_subdir(project: MatchProject, root: Path, name: str) -> Path:
    """Resolve ``name`` against ``root`` using the project's override fields."""
    if name == "raw":
        return project.raw_path(root)
    if name == "audio":
        return project.audio_path(root)
    if name == "trimmed":
        return project.trimmed_path(root)
    if name == "exports":
        return project.exports_path(root)
    if name == "audit":
        return project.audit_path(root)
    # No override field for scoreboard; always project-local.
    return root / name


def export_project(
    project_root: Path,
    output: Path,
    *,
    include_trimmed: bool = False,
    include_exports: bool = False,
    include_raw: bool = False,
    include_audio: bool = False,
) -> ExportResult:
    """Write a ``.tar.gz`` archive of ``project_root`` to ``output``.

    ``output`` may be a directory (the archive filename is derived from the
    project name + today's date) or a file path. Returns an :class:`ExportResult`
    describing what was included and which subdirectories were skipped.
    """
    project_root = project_root.expanduser().resolve()
    if not (project_root / PROJECT_FILE).exists():
        raise BackupError(f"no {PROJECT_FILE} in {project_root}")
    project = MatchProject.load(project_root)

    output = output.expanduser()
    if output.is_dir():
        stamp = datetime.now(UTC).strftime("%Y%m%d")
        slug = _slug(project.name) or project_root.name
        output = output / f"{slug}-backup-{stamp}.tar.gz"
    output.parent.mkdir(parents=True, exist_ok=True)

    wanted = list(DEFAULT_DIRS)
    if include_trimmed:
        wanted.append("trimmed")
    if include_exports:
        wanted.append("exports")
    if include_raw:
        wanted.append("raw")
    if include_audio:
        wanted.append("audio")

    arc_root = project_root.name  # top-level dir inside the archive
    included: list[str] = []
    skipped: list[SkippedDir] = []

    with tarfile.open(output, "w:gz") as tf:
        tf.add(project_root / PROJECT_FILE, arcname=f"{arc_root}/{PROJECT_FILE}")
        for name in wanted:
            src = _resolve_subdir(project, project_root, name)
            try:
                src_resolved = src.resolve()
            except OSError:
                skipped.append(SkippedDir(name=name, reason="missing"))
                continue
            if not src_resolved.exists():
                skipped.append(SkippedDir(name=name, reason="missing", resolved_path=str(src_resolved)))
                continue
            if not _is_inside(src_resolved, project_root):
                skipped.append(
                    SkippedDir(
                        name=name,
                        reason="outside_project_root",
                        resolved_path=str(src_resolved),
                    )
                )
                continue
            tf.add(src_resolved, arcname=f"{arc_root}/{name}")
            included.append(name)

        manifest = {
            "splitsmith_version": __version__,
            "created_at": datetime.now(UTC).isoformat(),
            "project_name": project.name,
            "project_root_name": project_root.name,
            "included": ["project.json", *included],
            "skipped": [s.model_dump() for s in skipped],
            "options": {
                "include_trimmed": include_trimmed,
                "include_exports": include_exports,
                "include_raw": include_raw,
                "include_audio": include_audio,
            },
        }
        _add_bytes(tf, f"{arc_root}/{MANIFEST_NAME}", json.dumps(manifest, indent=2).encode())

    size = output.stat().st_size
    return ExportResult(
        archive_path=output,
        bytes_written=size,
        included=["project.json", *included],
        skipped=skipped,
    )


def import_project(
    archive: Path,
    dest_root: Path,
    *,
    overwrite: bool = False,
) -> ImportResult:
    """Extract ``archive`` into ``dest_root``.

    The archive must contain exactly one top-level directory holding a
    ``project.json``. Returns an :class:`ImportResult` pointing at the new
    project root.
    """
    archive = archive.expanduser().resolve()
    dest_root = dest_root.expanduser().resolve()
    if not archive.exists():
        raise BackupError(f"archive not found: {archive}")
    dest_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="splitsmith-import-") as staging_str:
        staging = Path(staging_str)
        with tarfile.open(archive, "r:*") as tf:
            _safe_extract(tf, staging)

        top = _single_top_dir(staging)
        if top is None:
            raise BackupError("archive must contain exactly one top-level directory")
        if not (top / PROJECT_FILE).exists():
            raise BackupError(f"archive is missing {PROJECT_FILE}")

        # Validate project.json parses before we move anything.
        try:
            MatchProject.load(top)
        except Exception as exc:  # noqa: BLE001 - surface the underlying failure
            raise BackupError(f"invalid project.json in archive: {exc}") from exc

        manifest: dict[str, object] | None = None
        manifest_path = top / MANIFEST_NAME
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
            except json.JSONDecodeError:
                manifest = None

        target = dest_root / top.name
        if target.exists():
            if not overwrite:
                raise BackupError(f"destination already exists: {target}")
            shutil.rmtree(target)
        shutil.move(str(top), str(target))

    project = MatchProject.load(target)
    return ImportResult(project_root=target, project_name=project.name, manifest=manifest)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _add_bytes(tf: tarfile.TarFile, arcname: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    info.mtime = int(datetime.now(UTC).timestamp())
    tf.addfile(info, io.BytesIO(data))


def _safe_extract(tf: tarfile.TarFile, dest: Path) -> None:
    """Extract guarding against path-traversal entries."""
    dest_resolved = dest.resolve()
    members: list[tarfile.TarInfo] = []
    for m in tf.getmembers():
        target = (dest_resolved / m.name).resolve()
        if not _is_inside(target, dest_resolved):
            raise BackupError(f"archive contains unsafe path: {m.name!r}")
        # Block symlinks/hardlinks pointing outside the staging dir.
        if m.issym() or m.islnk():
            link_target = (dest_resolved / m.name).parent / (m.linkname or "")
            try:
                link_resolved = link_target.resolve()
            except OSError as exc:
                raise BackupError(f"unsafe link in archive: {m.name!r}") from exc
            if not _is_inside(link_resolved, dest_resolved):
                raise BackupError(f"unsafe link in archive: {m.name!r}")
        members.append(m)
    tf.extractall(dest_resolved, members=members)


def _single_top_dir(staging: Path) -> Path | None:
    entries = [p for p in staging.iterdir() if not p.name.startswith(".")]
    if len(entries) != 1 or not entries[0].is_dir():
        return None
    return entries[0]


def _slug(name: str) -> str:
    out = "".join(c if c.isalnum() or c in "-_" else "-" for c in name.strip())
    return out.strip("-").lower()


__all__: Iterable[str] = (
    "BackupError",
    "ExportResult",
    "ImportResult",
    "SkippedDir",
    "DEFAULT_DIRS",
    "OPTIONAL_DIRS",
    "NEVER_DIRS",
    "MANIFEST_NAME",
    "export_project",
    "import_project",
)
