"""Reference footage for the footage sort's compare panel (spec
2026-10-21): the file that stands for a shooter's stage in their
project, and the frame that represents it on a tile.

Pure over a loaded project plus a cache directory; the sort API
(``footage_sort_api``) decides when frames are built (the scan's
background pass or the ``footage_sort_frames`` job), never a request.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .. import thumbnail
from ..match_project import MatchProject
from . import audio

logger = logging.getLogger(__name__)

#: Seconds after the beep for a trimmed stage's frame: the stage is
#: visible early, so a couple of seconds into the run shows it without
#: the shooter's hands and gun in the way yet.
FRAME_AFTER_BEEP_S = 2.0
#: An untrimmed file's frame, the same pick a sort thumbnail makes.
FRAME_RAW_S = 1.0
#: When the first pick seeks past the clip's end (a trim shorter than
#: the beep plus the offset), the retry.
FRAME_FALLBACK_S = 0.5
FRAME_WIDTH = 480


class ProjectReference(BaseModel):
    """A shooter's stage as the panel can show it: the primary video, the
    file to play (trim or raw) and where in it the frame is taken."""

    shooter: str
    stage: int
    #: ``StageVideo.path`` as the stream route wants it.
    video_path: str
    video_id: str
    stream_kind: Literal["trim", "auto"]
    source_file: Path
    frame_at: float


def project_reference(
    project: MatchProject, root: Path, slug: str, stage_number: int
) -> ProjectReference | None:
    """The stage's primary as reference footage, or None: no such stage, no
    primary, or a raw file that is not reachable and has no trim."""
    try:
        stage = project.stage(stage_number)
    except KeyError:
        return None
    primary = stage.primary()
    if primary is None:
        return None
    trim, beep = audio.stage_trim_for_read(project, root, stage_number)
    if trim is not None:
        # The frame may come from the lossless export trim, but the stream
        # route's ``trim`` serves only the audit trim (it never substitutes),
        # so a stage with the export trim alone plays as ``auto``.
        audit = audio.resolve_trim_for_read(root, stage_number, primary, project=project)
        return ProjectReference(
            shooter=slug,
            stage=stage_number,
            video_path=primary.path.as_posix(),
            video_id=primary.video_id,
            stream_kind="trim" if audit is not None else "auto",
            source_file=trim,
            frame_at=beep + FRAME_AFTER_BEEP_S,
        )
    raw = project.resolve_video_path(root, primary.path)
    if not raw.is_file():
        return None
    return ProjectReference(
        shooter=slug,
        stage=stage_number,
        video_path=primary.path.as_posix(),
        video_id=primary.video_id,
        stream_kind="auto",
        source_file=raw,
        frame_at=FRAME_RAW_S,
    )


def frame_path(cache_dir: Path, ref: ProjectReference) -> Path:
    """Where the frame lives: keyed by the source file's mtime, so a re-trim
    gets a fresh frame and the old one ages out with the sort directory."""
    mtime = ref.source_file.stat().st_mtime_ns
    return cache_dir / f"{ref.shooter}-stage{ref.stage:02d}-{ref.video_id}-{mtime}.jpg"


def cached_frame(cache_dir: Path, ref: ProjectReference) -> Path | None:
    try:
        candidate = frame_path(cache_dir, ref)
    except OSError:
        return None
    return candidate if candidate.exists() else None


def failed_path(cache_dir: Path, ref: ProjectReference) -> Path:
    """The marker for a frame that could not be made (a corrupt or
    unreadable file): keyed like the frame, so a re-trim tries again and
    the view stops counting it as pending."""
    return frame_path(cache_dir, ref).with_suffix(".failed")


def mark_failed(cache_dir: Path, ref: ProjectReference) -> None:
    dest = failed_path(cache_dir, ref)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.touch()


def frame_failed(cache_dir: Path, ref: ProjectReference) -> bool:
    try:
        return failed_path(cache_dir, ref).exists()
    except OSError:
        return False


def extract_frame(source: Path, dest: Path, *, at_seconds: float, ffmpeg_binary: str = "ffmpeg") -> None:
    """One JPEG frame ``at_seconds`` into ``source``, published whole; a
    seek past the end yields nothing, so the retry takes the start."""

    def cmd(t: float) -> list[str]:
        return [
            ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{t:.3f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            f"scale={FRAME_WIDTH}:-2",
            "-q:v",
            "4",
        ]

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        thumbnail._publish(dest, cmd(at_seconds), timeout=10.0, what=f"reference frame for {source}")
    except thumbnail.ThumbnailError:
        if at_seconds <= FRAME_FALLBACK_S:
            raise
        thumbnail._publish(dest, cmd(FRAME_FALLBACK_S), timeout=10.0, what=f"reference frame for {source}")


def ensure_frame(cache_dir: Path, ref: ProjectReference, *, ffmpeg_binary: str = "ffmpeg") -> Path:
    """The frame for ``ref``, extracted unless cached."""
    hit = cached_frame(cache_dir, ref)
    if hit is not None:
        return hit
    dest = frame_path(cache_dir, ref)
    extract_frame(ref.source_file, dest, at_seconds=ref.frame_at, ffmpeg_binary=ffmpeg_binary)
    return dest
