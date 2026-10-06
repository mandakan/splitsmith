"""A motion template as an alpha clip ffmpeg can lay on a card.

``write_motion_clip`` pipes the raw RGBA frames of a
:class:`~splitsmith.overlay_raster.TemplateFrames` into ffmpeg and writes
a lossless MOV (the ``png`` encoder, ``rgba``) in the render's work
directory. Both MP4 renderers then take that file as one more input and
overlay it on the card's backdrop with ``tpad=stop_mode=clone``, so the
template's last frame holds for the rest of the card
(:func:`motion_overlay_filters`). An intermediate file rather than a
pipe into the segment encode keeps the renderers' ``Runner`` protocol and
their argv-keyed segment cache as they are; the cache keys the clip by
``look_template.template_digest`` (a virtual input), so a cached segment
is found before any frame is rendered.

The piping follows ``overlay_render``'s proven shape: kill, reap and
remove the fragment on any exception, read stderr only after the wait.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .overlay_raster import TemplateFrames

logger = logging.getLogger(__name__)


class MotionClipError(RuntimeError):
    """The clip could not be written: frames failed or ffmpeg refused."""


@dataclass(frozen=True)
class MotionClip:
    path: Path
    seconds: float
    frame_count: int


def motion_clip_command(
    *, out: Path, width: int, height: int, fps: float, ffmpeg_binary: str
) -> tuple[str, ...]:
    """Raw RGBA on stdin at ``fps`` to a lossless alpha MOV at ``out``."""
    return (
        ffmpeg_binary,
        "-hide_banner",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "-s",
        f"{width}x{height}",
        "-r",
        f"{fps:g}",
        "-i",
        "-",
        "-c:v",
        "png",
        "-pix_fmt",
        "rgba",
        str(out),
    )


def write_motion_clip(frames: TemplateFrames, *, out: Path, fps: float, ffmpeg_binary: str) -> MotionClip:
    """Consume ``frames`` into ``out``. Raises :class:`MotionClipError`
    and leaves no file behind when a frame raises, is the wrong size, or
    ffmpeg exits non-zero."""
    expected = frames.width * frames.height * 4
    cmd = motion_clip_command(
        out=out, width=frames.width, height=frames.height, fps=fps, ffmpeg_binary=ffmpeg_binary
    )
    try:
        first = next(iter(frames.frames), None)
    except Exception as exc:  # noqa: BLE001 -- the template's own failure is the clip's
        frames.close()
        raise MotionClipError(f"{out.name}: {exc}") from exc
    if first is None:
        raise MotionClipError(f"{out.name}: the template yielded no frames")
    if len(first) != expected:
        frames.close()
        raise MotionClipError(f"{out.name}: frame 1 is {len(first)} bytes, expected {expected}")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    written = 0
    try:
        proc.stdin.write(first)
        written = 1
        for frame in frames.frames:
            if len(frame) != expected:
                raise MotionClipError(
                    f"{out.name}: frame {written + 1} is {len(frame)} bytes, expected {expected}"
                )
            proc.stdin.write(frame)
            written += 1
        proc.stdin.close()
    except BaseException as exc:
        proc.kill()
        proc.wait()
        out.unlink(missing_ok=True)
        frames.close()
        if not isinstance(exc, Exception) or isinstance(exc, MotionClipError):
            raise
        raise MotionClipError(f"{out.name}: {exc}") from exc
    rc = proc.wait()
    stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    if rc != 0:
        out.unlink(missing_ok=True)
        raise MotionClipError(f"{out.name}: ffmpeg exited with {rc}: {stderr.strip()[-2000:]}")
    return MotionClip(path=out, seconds=written / fps, frame_count=written)


def motion_overlay_filters(
    input_index: int, *, rate: str, seconds: float, source_label: str, out_label: str = "withmotion"
) -> tuple[list[str], str]:
    """Lay input ``input_index`` (the clip) over ``source_label`` for
    ``seconds``: conformed to ``rate``, re-based to zero, its last frame
    cloned to the end and the whole trimmed to the hold, the same shape
    the grid uses for its sprite sequence."""
    return [
        f"[{input_index}:v]format=rgba,fps={rate},setpts=PTS-STARTPTS,"
        f"tpad=stop_mode=clone:stop_duration={seconds:g},trim=0:{seconds:g}[motion]",
        f"[{source_label}][motion]overlay=0:0:format=auto[{out_label}]",
    ], out_label


__all__ = [
    "MotionClip",
    "MotionClipError",
    "motion_clip_command",
    "motion_overlay_filters",
    "write_motion_clip",
]
