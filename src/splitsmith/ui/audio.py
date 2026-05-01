"""Audio extraction + caching for the production UI.

The CLI does this inline in ``cli.py:_extract_or_load_audio`` (caching the WAV
next to the source video). The production UI prefers project-local caching so
the project directory stays self-contained:

  <project>/audio/stage<N>_primary.wav

The cache is invalidated when the source video's mtime changes.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .. import beep_detect
from ..config import BeepDetectConfig, BeepDetection
from .project import MatchProject


class AudioExtractionError(RuntimeError):
    """ffmpeg or ffprobe failed during audio extraction."""


def primary_audio_path(
    project_root: Path,
    stage_number: int,
    *,
    project: MatchProject | None = None,
) -> Path:
    """Resolve the cached primary-audio path for a stage.

    When ``project`` is provided, its configured ``audio_dir`` (issue #23) is
    honoured. When omitted, defaults to ``<project_root>/audio/`` for backwards
    compatibility with callers that don't have the project model handy.
    """
    audio_dir = project.audio_path(project_root) if project else project_root / "audio"
    return audio_dir / f"stage{stage_number}_primary.wav"


def ensure_primary_audio(
    project_root: Path,
    stage_number: int,
    source_video: Path,
    *,
    sample_rate: int = 48000,
    ffmpeg_binary: str = "ffmpeg",
    project: MatchProject | None = None,
) -> Path:
    """Extract a mono WAV from ``source_video`` if not already cached.

    Cache location is ``<audio_dir>/stage<N>_primary.wav`` where ``audio_dir``
    comes from the project's configured ``audio_dir`` override (issue #23) or
    defaults to ``<project_root>/audio/``. Re-extracts when the source mtime
    is newer than the cached file's. Returns the path to the cached WAV.
    """
    audio_path = primary_audio_path(project_root, stage_number, project=project)
    audio_path.parent.mkdir(parents=True, exist_ok=True)

    src_resolved = source_video.resolve()
    if not src_resolved.exists():
        raise FileNotFoundError(f"primary video missing on disk: {src_resolved}")

    if audio_path.exists() and audio_path.stat().st_mtime >= src_resolved.stat().st_mtime:
        return audio_path

    if not shutil.which(ffmpeg_binary):
        raise AudioExtractionError(f"ffmpeg binary not found: {ffmpeg_binary}")

    cmd = [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src_resolved),
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-vn",
        str(audio_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise AudioExtractionError(
            f"ffmpeg failed (exit {exc.returncode}): {exc.stderr or exc.stdout!r}"
        ) from exc
    return audio_path


def detect_primary_beep(
    project_root: Path,
    stage_number: int,
    source_video: Path,
    *,
    config: BeepDetectConfig | None = None,
    ffmpeg_binary: str = "ffmpeg",
    project: MatchProject | None = None,
) -> BeepDetection:
    """Run ``beep_detect.detect_beep`` against the primary's cached audio.

    Audio extraction happens transparently if not yet cached. Returns the
    ``BeepDetection`` result; the caller is responsible for persisting the
    relevant fields to the ``StageVideo``.
    """
    audio_path = ensure_primary_audio(
        project_root,
        stage_number,
        source_video,
        ffmpeg_binary=ffmpeg_binary,
        project=project,
    )
    audio, sr = beep_detect.load_audio(audio_path)
    cfg = config or BeepDetectConfig()
    return beep_detect.detect_beep(audio, sr, cfg)
