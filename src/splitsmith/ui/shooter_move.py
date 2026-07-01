"""Cross-shooter video relocation service (wrong-shooter correction).

Implements :func:`move_shooter` -- the I/O-bearing service that lifts
a set of :class:`~splitsmith.ui.project.StageVideo` records from one
shooter's :class:`~splitsmith.ui.project.MatchProject` and inserts them
into another's, carrying all human-reviewed state (beep, audit shots)
while reproducing machine-generated artifacts in the background.

Architecture note (CLAUDE.md compliance)
-----------------------------------------
- Analysis logic stays out of ``cli.py``.
- Pure model mutation is on ``MatchProject``; I/O here, in the
  ui/ sibling pattern of ``cleanup.py`` / ``relink.py``.
- Pydantic models for all data crossing module boundaries.
- Audit load/save/clear are injected as callables so this function
  stays agnostic of local-file vs ``state_docs`` persistence.

Caller (server.py endpoint) is responsible for:
- Persisting both project docs (``target_project.save`` before
  ``source_project.save`` -- target-first ensures a crash leaves a
  duplicate rather than a lost record).
- Auto-queuing beep for moved primaries that landed assigned.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ..storage import Storage
from .audio import trimmed_video_path, video_audio_path, video_audit_audio_path
from .project import MatchProject, RawVideo, StageVideo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class MoveShooterResultItem(BaseModel):
    """One video that successfully moved."""

    video_path: str
    stage_number: int | None  # None if the video was unassigned
    demoted_to_secondary: bool = False  # primary-collision rule fired


class MoveShooterBlocked(BaseModel):
    """One video that was refused (occupied-stage rule)."""

    video_path: str
    stage_number: int | None
    reason: str
    code: Literal["occupied_stage", "unknown_path", "filename_collision"]


class MoveShooterOutcome(BaseModel):
    moved: list[MoveShooterResultItem]
    blocked: list[MoveShooterBlocked]


# ---------------------------------------------------------------------------
# Service function
# ---------------------------------------------------------------------------


def move_shooter(
    *,
    source_project: MatchProject,
    source_root: Path,
    target_project: MatchProject,
    target_root: Path,
    video_paths: list[str],
    load_target_audit: Callable[[int], dict | None],
    save_target_audit: Callable[[int, dict], None],
    load_source_audit: Callable[[int], dict | None],
    clear_source_audit: Callable[[int], None],
    storage: Storage | None,
) -> MoveShooterOutcome:
    """Relocate ``video_paths`` from ``source_project`` to ``target_project``.

    Mutation order
    --------------
    1. Validate all requested paths against ``source_project`` before
       mutating anything (fail-fast: unknown paths are pre-mutation errors).
    2. Per video: apply occupied-stage rule (block if target has a primary +
       audited shots); lift the StageVideo from source; insert into target
       applying the primary-collision rule; carry the audit doc; relocate
       raw link / copy; move path-independent derived caches; update
       raw_videos[].

    The function mutates both project models **in memory** only. The caller
    persists both docs after returning (target first, then source).

    Parameters
    ----------
    source_project / source_root :
        The shooter footage is moving away from.
    target_project / target_root :
        The shooter footage is moving toward.
    video_paths :
        ``str(StageVideo.path)`` values -- the same strings stored on the
        project, typically ``raw/<filename>`` (project-relative).
    load_target_audit / save_target_audit / load_source_audit / clear_source_audit :
        Injected callables so this function is agnostic of local-file vs
        ``state_docs`` audit persistence. Each takes ``(stage_number, ...)``
        arguments matching the docstring below.
    storage :
        ``None`` in local mode; a live ``Storage`` in hosted mode. Controls
        whether raw bytes or symlinks are relocated.

    Callable signatures
    -------------------
    ``load_*_audit(stage_number) -> dict | None``
        Return the audit doc for ``stage_number`` on the respective shooter,
        or ``None`` when absent.
    ``save_target_audit(stage_number, doc) -> None``
        Persist ``doc`` as the target shooter's stage-N audit.
    ``clear_source_audit(stage_number) -> None``
        Delete / wipe the source shooter's stage-N audit (called only after
        the target write succeeds).
    """
    moved: list[MoveShooterResultItem] = []
    blocked: list[MoveShooterBlocked] = []

    # ------------------------------------------------------------------
    # Phase 1: validate all requested paths before mutating anything.
    # ------------------------------------------------------------------
    located: list[tuple[str, object, StageVideo]] = []  # (path_str, stage_or_None, video)
    for path_str in video_paths:
        result = source_project.find_video(Path(path_str))
        if result is None:
            blocked.append(
                MoveShooterBlocked(
                    video_path=path_str,
                    stage_number=None,
                    reason=f"video {path_str!r} not found on source project",
                    code="unknown_path",
                )
            )
            continue
        stage, video = result
        located.append((path_str, stage, video))

    # If any unknown paths were found, fail the entire batch before mutating.
    if any(b.code == "unknown_path" for b in blocked):
        return MoveShooterOutcome(moved=[], blocked=blocked)

    # ------------------------------------------------------------------
    # Phase 2: per-video mutation.
    # ------------------------------------------------------------------
    for path_str, stage, video in located:
        stage_number = stage.stage_number if stage is not None else None

        # ---- Occupied-stage rule (block, not merge) ----
        # For a primary video moving to an assigned stage: if the target
        # stage already has a primary AND has audited shots, block.
        if stage_number is not None and video.role == "primary":
            target_stage = _find_target_stage(target_project, stage_number)
            if target_stage is not None and target_stage.primary() is not None:
                target_audit = load_target_audit(stage_number)
                has_shots = bool(
                    target_audit and isinstance(target_audit.get("shots"), list) and target_audit["shots"]
                )
                if has_shots:
                    blocked.append(
                        MoveShooterBlocked(
                            video_path=path_str,
                            stage_number=stage_number,
                            reason=(
                                f"target stage {stage_number} already has a primary with "
                                "audited shots; move refused to avoid clobbering reviewed data"
                            ),
                            code="occupied_stage",
                        )
                    )
                    continue

        # ---- Filename-collision guard on target raw dir (local mode) ----
        if storage is None and stage_number is not None:
            name = Path(path_str).name
            dst_raw = target_project.raw_path(target_root) / name
            src_raw = source_project.raw_path(source_root) / name
            if dst_raw.exists() and not _same_raw_target(dst_raw, src_raw):
                blocked.append(
                    MoveShooterBlocked(
                        video_path=path_str,
                        stage_number=stage_number,
                        reason=(
                            f"target raw/{name} already exists and points to a different "
                            "source -- would clobber an unrelated file"
                        ),
                        code="filename_collision",
                    )
                )
                continue

        # ---- Lift from source ----
        if stage is None:
            source_project.unassigned_videos = [
                v for v in source_project.unassigned_videos if str(v.path) != path_str
            ]
        else:
            stage.videos = [v for v in stage.videos if str(v.path) != path_str]

        # ---- Insert into target (verbatim -- do NOT call assign_video) ----
        demoted = False
        # Capture the role before any demotion: a primary always vacates the
        # source stage's audit, even when it lands as a secondary.
        moved_was_primary = video.role == "primary"
        if stage_number is None:
            target_project.unassigned_videos.append(video)
        else:
            target_stage = _find_target_stage(target_project, stage_number)
            if target_stage is None:
                # Target doesn't have this stage yet -- treat as a no-primary slot.
                from .project import StageEntry

                target_stage = StageEntry(
                    stage_number=stage_number,
                    stage_name=f"Stage {stage_number}",
                    time_seconds=0.0,
                )
                target_project.stages.append(target_stage)
                target_project.stages.sort(key=lambda s: s.stage_number)

            # Primary-collision rule: target already has a primary but no
            # audited shots -> demote the moved video to secondary.
            if moved_was_primary and target_stage.primary() is not None:
                # (We already know there are no audited shots -- the
                # occupied-stage block above would have stopped us.)
                video = video.model_copy(update={"role": "secondary"})
                demoted = True

            target_stage.videos.append(video)

        # ---- Audit doc: a moved primary always vacates the source stage ----
        # If it stays primary on the target, carry the reviewed shots over.
        # If it was demoted to secondary, the shots don't follow (secondaries
        # have no stage audit) but the source audit must still be cleared --
        # otherwise the source stage shows reviewed shots for a video that
        # has left, which would read as work the user never has to touch but
        # can no longer explain. (Transparency: no orphaned review state.)
        if stage_number is not None and moved_was_primary:
            src_audit = load_source_audit(stage_number)
            if src_audit is not None:
                if not demoted:
                    save_target_audit(stage_number, src_audit)
                clear_source_audit(stage_number)

        # ---- Relocate raw (local mode only) ----
        if storage is None:
            _relocate_raw(source_project, source_root, target_project, target_root, video, path_str)
        # Hosted: no raw movement -- both shooters share the same tenant object.

        # ---- Relocate path-independent derived caches (local mode only) ----
        if storage is None and stage_number is not None:
            _relocate_derived_caches(
                source_project, source_root, target_project, target_root, video, stage_number
            )
        # Hosted: caches are ephemeral; jobs regenerate them.

        # ---- raw_videos[] bookkeeping ----
        _move_raw_video_entry(source_project, target_project, video, stage_number)

        moved.append(
            MoveShooterResultItem(
                video_path=path_str,
                stage_number=stage_number,
                demoted_to_secondary=demoted,
            )
        )

    return MoveShooterOutcome(moved=moved, blocked=blocked)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_target_stage(project: MatchProject, stage_number: int):
    """Return the stage with ``stage_number`` on ``project``, or ``None``."""
    for s in project.stages:
        if s.stage_number == stage_number:
            return s
    return None


def _same_raw_target(dst: Path, src: Path) -> bool:
    """Return True when ``dst`` and ``src`` refer to the same underlying file.

    Covers: both symlinks pointing to the same target; dst is a symlink
    to src; or both are real files that resolve to the same inode.
    """
    try:
        if dst.is_symlink() and src.is_symlink():
            return dst.readlink() == src.readlink()
        if dst.is_symlink():
            return dst.readlink().resolve() == src.resolve()
        return dst.resolve() == src.resolve()
    except OSError:
        return False


def _relocate_raw(
    source_project: MatchProject,
    source_root: Path,
    target_project: MatchProject,
    target_root: Path,
    video: StageVideo,
    path_str: str,
) -> None:
    """Relink or rename the raw file from source shooter dir to target."""
    name = Path(path_str).name
    src_link = source_project.raw_path(source_root) / name
    dst_link = target_project.raw_path(target_root) / name

    if not src_link.exists() and not src_link.is_symlink():
        # Source raw missing -- nothing to relocate; skip silently.
        logger.debug("move_shooter: raw not found at %s, skipping relocation", src_link)
        return

    dst_link.parent.mkdir(parents=True, exist_ok=True)

    if dst_link.exists() or dst_link.is_symlink():
        # Already in place (e.g. collision guard passed because it's the
        # same target). Just drop the source link.
        try:
            src_link.unlink()
        except OSError as exc:
            logger.warning("move_shooter: could not unlink source raw %s: %s", src_link, exc)
        return

    if src_link.is_symlink():
        # Symlink ingest: repoint, no bytes moved.
        real_target = src_link.readlink()
        try:
            dst_link.symlink_to(real_target)
            src_link.unlink()
        except OSError as exc:
            logger.warning("move_shooter: symlink relink %s -> %s failed: %s", src_link, dst_link, exc)
    else:
        # Copy ingest (real file): same-fs rename; fall back to shutil.move.
        try:
            src_link.replace(dst_link)
        except OSError:
            try:
                shutil.move(str(src_link), str(dst_link))
            except OSError as exc:
                logger.warning("move_shooter: could not move raw file %s -> %s: %s", src_link, dst_link, exc)


def _relocate_derived_caches(
    source_project: MatchProject,
    source_root: Path,
    target_project: MatchProject,
    target_root: Path,
    video: StageVideo,
    stage_number: int,
) -> None:
    """Best-effort rename of path-independent per-video cache files.

    Missing caches are silently skipped -- the reproduction path (worker
    jobs) regenerates them on next access.
    """
    # audio WAV
    src_wav = video_audio_path(source_root, stage_number, video, project=source_project)
    dst_wav = video_audio_path(target_root, stage_number, video, project=target_project)
    _try_replace(src_wav, dst_wav)

    # audit WAV (extracted from the trimmed clip)
    src_audit_wav = video_audit_audio_path(source_root, stage_number, video, project=source_project)
    dst_audit_wav = video_audit_audio_path(target_root, stage_number, video, project=target_project)
    _try_replace(src_audit_wav, dst_audit_wav)

    # trimmed MP4
    src_trim = trimmed_video_path(source_root, stage_number, video, project=source_project)
    dst_trim = trimmed_video_path(target_root, stage_number, video, project=target_project)
    _try_replace(src_trim, dst_trim)

    # Peaks / params sidecars (*.peaks-*.json and *.params.json) next to WAV
    for sidecar_src in _sidecar_paths(src_wav):
        sidecar_dst = dst_wav.parent / sidecar_src.name
        _try_replace(sidecar_src, sidecar_dst)


def _sidecar_paths(base: Path):
    """Yield existing sidecar files (peaks / params) adjacent to ``base``."""
    stem = base.stem
    for p in base.parent.glob(f"{stem}*.json"):
        if p.exists():
            yield p


def _try_replace(src: Path, dst: Path) -> None:
    """Rename ``src`` to ``dst`` when ``src`` exists; skip otherwise."""
    if not src.exists():
        return
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dst)
    except OSError as exc:
        logger.debug("move_shooter: cache rename %s -> %s skipped: %s", src, dst, exc)


def _move_raw_video_entry(
    source_project: MatchProject,
    target_project: MatchProject,
    video: StageVideo,
    stage_number: int | None,
) -> None:
    """Move the matching ``RawVideo`` entry from source to target.

    Drops from source only when no other StageVideo in the source still
    references the same ``storage_path`` (a raw may cover multiple stages
    and not all may have been requested for this move).
    """
    storage_path = str(video.path)
    rv = source_project.find_raw_video(storage_path)
    if rv is None:
        return

    # Check whether any remaining source StageVideo still references this raw.
    still_referenced = any(str(v.path) == storage_path for v in source_project.all_videos())
    if not still_referenced:
        source_project.raw_videos = [r for r in source_project.raw_videos if r.storage_path != storage_path]

    # Attach to target (merges covers_stages, dedupes by storage_path).
    target_rv = RawVideo(
        original_filename=rv.original_filename,
        size_bytes=rv.size_bytes,
        sha256=rv.sha256,
        uploaded_at=rv.uploaded_at,
        storage_path=rv.storage_path,
        covers_stages=([stage_number] if stage_number is not None else []),
    )
    target_project.attach_raw_video(target_rv)
