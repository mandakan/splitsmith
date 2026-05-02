"""Audio extraction + caching for the production UI.

The CLI does this inline in ``cli.py:_extract_or_load_audio`` (caching the WAV
next to the source video). The production UI prefers project-local caching so
the project directory stays self-contained:

  <project>/audio/stage<N>_primary.wav   -- extracted from the full source
  <project>/audio/stage<N>_audit.wav     -- extracted from the trimmed MP4

The audit screen (#15) prefers the audit WAV when a trimmed clip exists,
falling back to the full primary WAV. The cache is invalidated when its
source's mtime changes.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .. import beep_detect
from ..config import BeepDetectConfig, BeepDetection
from .project import MatchProject

# Standard trim buffer (config.OutputConfig.trim_buffer_seconds default).
# Used to derive the beep's position inside a trimmed clip when no trim
# metadata sidecar exists. Custom buffers will round-trip incorrectly here;
# wire trim through the UI to record exact metadata.
DEFAULT_TRIM_BUFFER_SECONDS: float = 5.0


@dataclass(frozen=True)
class AuditAudioResult:
    """What :func:`ensure_audit_audio` resolves to.

    ``audio_path`` is the WAV the audit screen should read. ``beep_in_clip``
    is where the beep falls in that clip's local timeline (used as the
    waveform's beep marker). ``trimmed`` is True when the audio came from a
    short-GOP trimmed MP4; False when the audit is operating on the full
    source for the lack of a trim.
    """

    audio_path: Path
    beep_in_clip: float | None
    trimmed: bool


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


def trimmed_primary_path(project_root: Path, stage_number: int, *, project: MatchProject) -> Path:
    """Resolve the cached short-GOP trimmed MP4 path for a stage's primary."""
    return project.trimmed_path(project_root) / f"stage{stage_number}_trimmed.mp4"


def audit_audio_path(
    project_root: Path,
    stage_number: int,
    *,
    project: MatchProject,
) -> Path:
    """Cache path for the audit WAV (extracted from the trimmed MP4)."""
    audio_dir = project.audio_path(project_root)
    return audio_dir / f"stage{stage_number}_audit.wav"


def ensure_audit_audio(
    project_root: Path,
    stage_number: int,
    primary_source: Path,
    primary_beep_time: float | None,
    *,
    project: MatchProject,
    sample_rate: int = 48000,
    ffmpeg_binary: str = "ffmpeg",
) -> AuditAudioResult:
    """Resolve the WAV the audit screen should serve for ``stage_number``.

    Prefers the trimmed clip's audio (short-GOP MP4 produced by
    :mod:`splitsmith.trim`). When the trimmed clip is missing, falls back
    to the full primary WAV so the audit screen still loads -- you'll see
    the entire source clip with the original (slow) scrub feel. Either way
    the result is mtime-cached.
    """
    trimmed_video = trimmed_primary_path(project_root, stage_number, project=project)
    # If a prior trim was interrupted, ensure_audit_trim leaves no .partial
    # behind, but be defensive: ignore an obviously-broken trim by checking
    # for a non-empty file. Anything more sophisticated (full ffprobe round
    # trip) would slow down the hot path; the partial file is now atomic.
    if trimmed_video.exists() and trimmed_video.stat().st_size > 0:
        audio_path = audit_audio_path(project_root, stage_number, project=project)
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        if not audio_path.exists() or audio_path.stat().st_mtime < trimmed_video.stat().st_mtime:
            _extract_audio(trimmed_video, audio_path, sample_rate, ffmpeg_binary)
        # Beep position inside the trimmed clip = beep_in_source minus
        # trim_start. trim_start = max(0, beep - pre_buffer); so:
        #   beep_in_clip = min(beep_in_source, pre_buffer)
        beep_in_clip = (
            min(primary_beep_time, project.trim_pre_buffer_seconds)
            if primary_beep_time is not None
            else None
        )
        return AuditAudioResult(audio_path=audio_path, beep_in_clip=beep_in_clip, trimmed=True)

    fallback = ensure_primary_audio(
        project_root,
        stage_number,
        primary_source,
        sample_rate=sample_rate,
        ffmpeg_binary=ffmpeg_binary,
        project=project,
    )
    return AuditAudioResult(
        audio_path=fallback,
        beep_in_clip=primary_beep_time,
        trimmed=False,
    )


def _extract_audio(
    source: Path,
    dest: Path,
    sample_rate: int,
    ffmpeg_binary: str,
) -> None:
    """Run ffmpeg to drop a mono WAV at ``dest`` from ``source``."""
    if not shutil.which(ffmpeg_binary):
        raise AudioExtractionError(f"ffmpeg binary not found: {ffmpeg_binary}")
    cmd = [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-vn",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise AudioExtractionError(
            f"ffmpeg failed (exit {exc.returncode}): {exc.stderr or exc.stdout!r}"
        ) from exc


def ensure_audit_trim(
    project_root: Path,
    stage_number: int,
    primary_source: Path,
    primary_beep_time: float,
    stage_time_seconds: float,
    *,
    project: MatchProject,
    ffmpeg_binary: str = "ffmpeg",
) -> Path:
    """Produce ``stage<N>_trimmed.mp4`` for the audit screen if not cached.

    Re-encodes the primary's source with a short GOP (Sub 5 / #16 audit
    mode) so the audit screen scrubs frame-accurately. The trim window is
    ``[max(0, beep - buffer), beep + stage_time + buffer]``. Cache key is
    the primary source's mtime: a re-recorded source triggers a re-trim,
    and the audit-WAV cache invalidates from there on its own mtime.

    Returns the path to the trimmed MP4. Raises ``AudioExtractionError`` on
    ffmpeg failure (kept under the same error type the audio helpers
    already raise so the endpoint can map them with one ``except``).
    """
    from .. import trim as trim_module

    output = trimmed_primary_path(project_root, stage_number, project=project)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: ffmpeg encodes into .partial.mp4, then we rename on success.
    # The extra ".partial" sits before the real suffix so ffmpeg can still
    # infer the MP4 muxer from the extension. (An earlier version put
    # ".partial" *after* the suffix and ffmpeg failed with "Unable to choose
    # an output format".) Without this, a page reload mid-trim leaves a
    # half-written .mp4 with no moov atom; the audit endpoints then fail
    # to extract audio because the cache check trusts the broken file.
    partial = output.with_name(f"{output.stem}.partial{output.suffix}")

    src_resolved = primary_source.resolve()
    if not src_resolved.exists():
        raise FileNotFoundError(f"primary video missing on disk: {src_resolved}")

    if output.exists() and output.stat().st_mtime >= src_resolved.stat().st_mtime:
        # Cleanup any orphaned .partial from a prior crashed run.
        if partial.exists():
            partial.unlink()
        return output

    # Stale or missing final + any orphaned partial -> remove and re-run.
    if output.exists():
        output.unlink()
    if partial.exists():
        partial.unlink()

    try:
        trim_module.trim_video(
            input_path=src_resolved,
            output_path=partial,
            beep_time=primary_beep_time,
            stage_time=stage_time_seconds,
            pre_buffer_seconds=project.trim_pre_buffer_seconds,
            post_buffer_seconds=project.trim_post_buffer_seconds,
            mode="audit",
            ffmpeg_binary=ffmpeg_binary,
            overwrite=True,
        )
    except trim_module.FFmpegError as exc:
        # ffmpeg may have written some bytes before bailing; delete them.
        if partial.exists():
            partial.unlink()
        raise AudioExtractionError(str(exc)) from exc

    partial.replace(output)
    return output


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
