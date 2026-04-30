"""Lossless video trim via ffmpeg subprocess.

Produces a clip of ``[beep_time - buffer, beep_time + stage_time + buffer]`` from
the source video using stream copy. Per SPEC.md, ``-ss`` before ``-i`` is used
for fast (non-keyframe-exact) seeking; the buffer absorbs any seek imprecision.

Pure orchestration: validation + command construction + a single subprocess call.
The runner is injectable so unit tests can verify the ffmpeg invocation without
shelling out.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from .config import TrimResult

Runner = Callable[..., subprocess.CompletedProcess]


class FFmpegError(RuntimeError):
    """ffmpeg exited non-zero or could not be invoked."""


def trim_video(
    input_path: Path,
    output_path: Path,
    beep_time: float,
    stage_time: float,
    *,
    buffer_seconds: float = 5.0,
    ffmpeg_binary: str = "ffmpeg",
    overwrite: bool = False,
    runner: Runner = subprocess.run,
) -> TrimResult:
    """Losslessly cut ``input_path`` to ``output_path`` around ``beep_time``.

    Returns the absolute-source-time window of the cut. Raises
    ``FFmpegError`` if ffmpeg fails or is not installed.
    """
    if beep_time < 0.0:
        raise ValueError(f"beep_time must be non-negative, got {beep_time}")
    if stage_time < 0.0:
        raise ValueError(f"stage_time must be non-negative, got {stage_time}")
    if buffer_seconds < 0.0:
        raise ValueError(f"buffer_seconds must be non-negative, got {buffer_seconds}")
    if not input_path.exists():
        raise FileNotFoundError(f"input video not found: {input_path}")

    start = max(0.0, beep_time - buffer_seconds)
    end = beep_time + stage_time + buffer_seconds
    duration = end - start

    cmd = [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y" if overwrite else "-n",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(input_path),
        "-t",
        f"{duration:.3f}",
        "-c",
        "copy",
        str(output_path),
    ]

    try:
        runner(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise FFmpegError(f"ffmpeg binary not found: {ffmpeg_binary}") from exc
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(
            f"ffmpeg failed (exit {exc.returncode}): {exc.stderr or exc.stdout!r}"
        ) from exc

    return TrimResult(output_path=output_path, start_time=start, end_time=end)
