"""Video trim via ffmpeg subprocess.

Two modes:

- ``lossless`` (default): stream copy with ``-c copy``. Instant, archival-quality,
  but inherits the source GOP. Insta360 head-cam footage typically has keyframes
  every 1-4s, which makes browser-side scrubbing chunky in the production UI.

- ``audit`` (#16): re-encodes the video with a short GOP (default 0.5s at 30fps)
  so browser ``<video>`` seeks land on a keyframe within ~1 frame of the pointer.
  Audio is stream-copied so the detector's input is bit-exact regardless of mode.

Per SPEC.md, ``-ss`` before ``-i`` is used for fast (non-keyframe-exact) seeking;
the buffer absorbs any seek imprecision. In audit mode, the re-encode also
re-aligns frames, so the seek-imprecision concern is moot anyway.

Pure orchestration: validation + command construction + a single subprocess call.
The runner is injectable so unit tests can verify the ffmpeg invocation without
shelling out.
"""

from __future__ import annotations

import platform
import subprocess
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Literal

from .config import TrimResult

Runner = Callable[..., subprocess.CompletedProcess]
TrimMode = Literal["lossless", "audit"]

# Encoders that don't take libx264-style ``-preset`` / ``-crf`` knobs. When the
# audit-mode trim uses one of these we drop those flags from the command line
# and let the encoder defaults handle quality (good enough for cache files).
_HARDWARE_ENCODERS: frozenset[str] = frozenset({"h264_videotoolbox"})


class FFmpegError(RuntimeError):
    """ffmpeg exited non-zero or could not be invoked."""


@lru_cache(maxsize=4)
def _probe_available_encoders(ffmpeg_binary: str = "ffmpeg") -> frozenset[str]:
    """Return the set of video encoder names ffmpeg reports it can use.

    Cached per-binary because ``ffmpeg -encoders`` adds a noticeable ~50ms
    cold-start to each call. Returns an empty set if ffmpeg can't be invoked
    so callers can fall back to ``libx264`` (which is the universal default).
    """
    try:
        out = subprocess.run(
            [ffmpeg_binary, "-hide_banner", "-encoders"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return frozenset()
    names: set[str] = set()
    # Lines look like ``" V..... libx264               H.264 / ...``; the
    # encoder name is the second whitespace-separated token.
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            names.add(parts[1])
    return frozenset(names)


def select_audit_encoder(
    requested: str = "auto",
    *,
    ffmpeg_binary: str = "ffmpeg",
) -> str:
    """Pick the best audit-mode video encoder available.

    ``requested`` is the user's preference from ``OutputConfig.trim_audit_encoder``:

    - ``"auto"``: probe ffmpeg for ``h264_videotoolbox`` on macOS (~10x speedup
      on 4K Insta360 footage), otherwise ``libx264``.
    - explicit name (``"libx264"``, ``"h264_videotoolbox"``, ...): used as-is
      when the binary advertises it; falls back to ``libx264`` when not.

    Returns a usable encoder name. ``libx264`` is the universal fallback because
    every realistic ffmpeg build ships it.
    """
    if requested != "auto":
        encoders = _probe_available_encoders(ffmpeg_binary)
        # If we couldn't probe (no ffmpeg on PATH for the probe call), trust
        # the explicit choice -- the trim itself will surface a clear error
        # if the encoder really is unavailable.
        if encoders and requested not in encoders:
            return "libx264"
        return requested
    encoders = _probe_available_encoders(ffmpeg_binary)
    if platform.system() == "Darwin" and "h264_videotoolbox" in encoders:
        return "h264_videotoolbox"
    return "libx264"


def trim_video(
    input_path: Path,
    output_path: Path,
    beep_time: float,
    stage_time: float,
    *,
    buffer_seconds: float = 5.0,
    pre_buffer_seconds: float | None = None,
    post_buffer_seconds: float | None = None,
    mode: TrimMode = "lossless",
    gop_frames: int = 15,
    crf: int = 20,
    preset: str = "ultrafast",
    video_encoder: str = "libx264",
    ffmpeg_binary: str = "ffmpeg",
    overwrite: bool = False,
    runner: Runner = subprocess.run,
) -> TrimResult:
    """Cut ``input_path`` to ``output_path`` around ``beep_time``.

    Buffer can be set asymmetrically: ``pre_buffer_seconds`` controls the
    pad before ``beep_time`` (anything from the source before that is cut),
    ``post_buffer_seconds`` controls the pad after ``beep_time + stage_time``.
    Both default to ``buffer_seconds`` when omitted -- so callers that want
    a symmetric buffer keep the old single-knob shape. Asymmetric buffers
    are useful for FCP exports where post-stage padding wants to be longer
    than the pre-roll (room for fades and transitions).

    ``mode`` selects the encoding strategy:

    - ``"lossless"``: stream copy (instant, archival).
    - ``"audit"``: re-encode video with a short GOP for scrub-friendly playback
      in the production UI's audit screen. Audio is stream-copied.

    Returns the absolute-source-time window of the cut. Raises ``FFmpegError``
    if ffmpeg fails or is not installed.
    """
    pre = pre_buffer_seconds if pre_buffer_seconds is not None else buffer_seconds
    post = post_buffer_seconds if post_buffer_seconds is not None else buffer_seconds
    if beep_time < 0.0:
        raise ValueError(f"beep_time must be non-negative, got {beep_time}")
    if stage_time < 0.0:
        raise ValueError(f"stage_time must be non-negative, got {stage_time}")
    if pre < 0.0:
        raise ValueError(f"pre_buffer_seconds must be non-negative, got {pre}")
    if post < 0.0:
        raise ValueError(f"post_buffer_seconds must be non-negative, got {post}")
    if mode not in ("lossless", "audit"):
        raise ValueError(f"mode must be 'lossless' or 'audit', got {mode!r}")
    if gop_frames < 1:
        raise ValueError(f"gop_frames must be >= 1, got {gop_frames}")
    if not 0 <= crf <= 51:
        raise ValueError(f"crf must be 0..51, got {crf}")
    if not input_path.exists():
        raise FileNotFoundError(f"input video not found: {input_path}")

    start = max(0.0, beep_time - pre)
    end = beep_time + stage_time + post
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
    ]
    if mode == "lossless":
        cmd += ["-c", "copy"]
    else:  # audit
        cmd += ["-c:v", video_encoder]
        # libx264-style knobs only apply to software encoders. Hardware
        # encoders (videotoolbox / nvenc / qsv) reject ``-preset`` / ``-crf``
        # or interpret them differently; let the encoder default the
        # quality knob and rely on its built-in speed/quality tradeoff
        # (videotoolbox's default is already fast and good enough for a
        # cache file). Keep GOP + pixel format flags -- those are
        # codec-level and apply to every H.264 encoder.
        if video_encoder not in _HARDWARE_ENCODERS:
            cmd += ["-preset", preset, "-crf", str(crf)]
        cmd += [
            "-g",
            str(gop_frames),
            "-keyint_min",
            str(gop_frames),
            "-sc_threshold",
            "0",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
        ]
    cmd.append(str(output_path))

    try:
        runner(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise FFmpegError(f"ffmpeg binary not found: {ffmpeg_binary}") from exc
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(
            f"ffmpeg failed (exit {exc.returncode}): {exc.stderr or exc.stdout!r}"
        ) from exc

    return TrimResult(output_path=output_path, start_time=start, end_time=end)
