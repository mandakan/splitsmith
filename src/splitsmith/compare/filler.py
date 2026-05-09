"""Black filler video for empty grid tiles in compare exports."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

Runner = Callable[..., subprocess.CompletedProcess]


class FillerRenderError(RuntimeError):
    """ffmpeg refused to render the black filler clip."""


def filler_filename(
    *, width: int, height: int, frame_rate_num: int, frame_rate_den: int, duration_seconds: float
) -> str:
    """Deterministic filename so two stages with matching geometry reuse the same file.

    Encodes ``(W, H, fps_num, fps_den, duration_ms)``; duration is rounded
    to milliseconds because the same compound clip should resolve to the
    same filler file across runs even when the source duration differs by
    sub-millisecond rounding.
    """
    duration_ms = int(round(duration_seconds * 1000))
    return (
        f"_compare_filler_{width}x{height}_{frame_rate_num}-{frame_rate_den}"
        f"_{duration_ms}ms.mp4"
    )


def ensure_filler(
    *,
    width: int,
    height: int,
    frame_rate_num: int,
    frame_rate_den: int,
    duration_seconds: float,
    output_dir: Path,
    ffmpeg_binary: str = "ffmpeg",
    runner: Runner = subprocess.run,
) -> Path:
    """Render (or reuse) a silent black filler video.

    Idempotent: the filename encodes geometry + duration, so a second call
    with matching arguments returns the existing file without re-running
    ffmpeg. ``output_dir`` is created if needed.

    The clip has no audio (``-an``) so the emitter doesn't need to mute it
    explicitly. ``libx264 -pix_fmt yuv420p`` is used for cross-platform
    reproducibility -- this stays predictable in CI / Linux even though
    the rest of the pipeline can use VideoToolbox locally.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"width/height must be positive, got {width}x{height}")
    if frame_rate_num <= 0 or frame_rate_den <= 0:
        raise ValueError(f"frame rate must be positive, got {frame_rate_num}/{frame_rate_den}")
    if duration_seconds <= 0.0:
        raise ValueError(f"duration_seconds must be positive, got {duration_seconds}")

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / filler_filename(
        width=width,
        height=height,
        frame_rate_num=frame_rate_num,
        frame_rate_den=frame_rate_den,
        duration_seconds=duration_seconds,
    )
    if target.exists():
        return target

    fps = frame_rate_num / frame_rate_den
    cmd = [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={width}x{height}:r={fps:.6f}",
        "-t",
        f"{duration_seconds:.3f}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(target),
    ]
    try:
        runner(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise FillerRenderError(f"ffmpeg binary not found: {ffmpeg_binary}") from exc
    except subprocess.CalledProcessError as exc:
        raise FillerRenderError(
            f"ffmpeg failed rendering filler ({exc.returncode}): " f"{exc.stderr or exc.stdout!r}"
        ) from exc
    return target
