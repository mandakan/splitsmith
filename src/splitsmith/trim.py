"""Video trim via ffmpeg subprocess.

Two modes:

- ``lossless`` (default): stream copy with ``-c copy``. Instant, archival-quality,
  but inherits the source GOP. Insta360 head-cam footage typically has keyframes
  every 1-4s, which makes browser-side scrubbing chunky in the production UI.

- ``audit`` (#16): re-encodes the video with a short GOP (default 0.5s at 30fps)
  so browser ``<video>`` seeks land on a keyframe within ~1 frame of the pointer.
  Audio is stream-copied so the detector's input is bit-exact regardless of mode.

A third file, the *web rendition* (#1031), is not a mode of ``trim_video``:
``transcode_web_trim`` re-encodes an audit trim down to a 720p faststart
MP4 for hosted players, which stream it from object storage. It covers the
same window as the trim it was cut from, so the two share a beep anchor.

Per SPEC.md, ``-ss`` before ``-i`` is used for fast (non-keyframe-exact) seeking;
the buffer absorbs any seek imprecision. In audit mode, the re-encode also
re-aligns frames, so the seek-imprecision concern is moot anyway.

Pure orchestration: validation + command construction + a single subprocess call.
The runner is injectable so unit tests can verify the ffmpeg invocation without
shelling out.
"""

from __future__ import annotations

import os
import platform
import subprocess
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Literal

from .config import Config, TrimResult, WebTrimConfig
from .runtime import ENV_CONFIG_FILE

Runner = Callable[..., subprocess.CompletedProcess]
TrimMode = Literal["lossless", "audit"]

# Encoders that don't take libx264-style ``-preset`` / ``-crf`` knobs. When the
# audit-mode trim uses one of these we drop those flags from the command line
# and let the encoder defaults handle quality (good enough for cache files).
# ``h264_nvenc`` interprets ``-preset``/``-cq`` differently from libx264 and
# rejects a libx264 ``-crf`` outright, so it belongs here too (issue #796).
_HARDWARE_ENCODERS: frozenset[str] = frozenset({"h264_videotoolbox", "h264_nvenc"})


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


@lru_cache(maxsize=4)
def _nvenc_encode_usable(ffmpeg_binary: str, runner: Runner) -> bool:
    """Can this host *actually* encode with ``h264_nvenc``?

    ffmpeg advertises ``h264_nvenc`` in ``-encoders`` on any build compiled
    with NVENC support, whether or not an NVIDIA GPU is present or usable --
    it fails only at encode time, with ``Cannot load nvcuda.dll`` /
    ``No capable devices found``. So the ``-encoders`` string is necessary
    but not sufficient; the only honest test is a real trial encode.

    Encodes one 640x480 ``lavfi`` frame to the null muxer (~tens of ms, once
    per process thanks to the cache) and reports whether ffmpeg exited
    cleanly. The frame is deliberately well above NVENC's minimum encode
    resolution: measured on an RTX 2070 SUPER, ``h264_nvenc`` rejects 128x128
    with ``InitializeEncoder failed: invalid param`` but accepts 256x256+, so
    a tiny probe frame is a false negative that would advertise a genuinely
    capable GPU as unusable. ``-pix_fmt yuv420p`` matches the real audit
    encode so the trial exercises the same path. Any failure -- non-zero exit,
    missing binary, a runner that raises -- is reported as "not usable" so the
    caller falls back to ``libx264`` rather than emitting a job that dies at
    encode time.
    """
    cmd = [
        ffmpeg_binary,
        "-hide_banner",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=640x480:r=25",
        "-c:v",
        "h264_nvenc",
        "-pix_fmt",
        "yuv420p",
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
    ]
    try:
        completed = runner(cmd, capture_output=True, text=True)
    except (FileNotFoundError, OSError):
        return False
    return getattr(completed, "returncode", 1) == 0


def select_audit_encoder(
    requested: str = "auto",
    *,
    ffmpeg_binary: str = "ffmpeg",
    runner: Runner = subprocess.run,
) -> str:
    """Pick the best audit-mode video encoder available.

    ``requested`` is the user's preference from ``OutputConfig.trim_audit_encoder``:

    - ``"auto"``: prefer ``h264_videotoolbox`` on macOS (~10x speedup on 4K
      Insta360 footage), else ``h264_nvenc`` on an NVIDIA host that passes a
      real trial encode (issue #796), else ``libx264``.
    - explicit name (``"libx264"``, ``"h264_videotoolbox"``, ...): used as-is
      when the binary advertises it; falls back to ``libx264`` when not.

    ``runner`` is injected so unit tests can drive the NVENC trial encode
    without shelling out; it is only consulted on the ``auto`` branch when
    ``h264_nvenc`` is advertised.

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
    # NVENC is advertised even with no usable GPU, so the string is a
    # prerequisite for the trial, not a decision on its own.
    if "h264_nvenc" in encoders and _nvenc_encode_usable(ffmpeg_binary, runner):
        return "h264_nvenc"
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
        raise FFmpegError(f"ffmpeg failed (exit {exc.returncode}): {exc.stderr or exc.stdout!r}") from exc

    return TrimResult(output_path=output_path, start_time=start, end_time=end)


def web_trim_config() -> WebTrimConfig:
    """The web rendition's encode knobs, from ``SPLITSMITH_CONFIG`` when
    set, else the shipped defaults (the same rule as
    ``coach.auto_classify_config``)."""
    raw = os.environ.get(ENV_CONFIG_FILE, "").strip()
    if not raw:
        return WebTrimConfig()
    return Config.load(Path(raw).expanduser()).web_trim


def web_trim_path(trimmed: Path) -> Path:
    """The web rendition that belongs to the audit trim at ``trimmed``:
    ``stage<N>_cam_<id>_trimmed.mp4`` -> ``stage<N>_cam_<id>_web.mp4``,
    in the same directory."""
    stem = trimmed.stem
    if stem.endswith("_trimmed"):
        stem = stem[: -len("_trimmed")]
    return trimmed.with_name(f"{stem}_web.mp4")


def transcode_web_trim(
    input_path: Path,
    output_path: Path,
    config: WebTrimConfig,
    *,
    ffmpeg_binary: str = "ffmpeg",
    runner: Runner = subprocess.run,
) -> None:
    """Re-encode an audit trim into its streaming rendition (#1031).

    Scales to ``config.height`` (width follows, kept even), ``libx264``
    at the config's crf/preset with a fixed GOP, AAC audio, and
    ``+faststart`` so the ``moov`` atom leads the file and a browser can
    start decoding after the first few hundred kilobytes. Raises
    :class:`FFmpegError` when ffmpeg fails or is missing.
    """
    if not input_path.exists():
        raise FFmpegError(f"input video not found: {input_path}")
    cmd = [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        f"scale=-2:{config.height}",
        "-c:v",
        config.video_codec,
        "-preset",
        config.preset,
        "-crf",
        str(config.crf),
        "-g",
        str(config.gop),
        "-keyint_min",
        str(config.gop),
        "-sc_threshold",
        "0",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        config.audio_bitrate,
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    try:
        runner(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise FFmpegError(f"ffmpeg binary not found: {ffmpeg_binary}") from exc
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(f"ffmpeg failed (exit {exc.returncode}): {exc.stderr or exc.stdout!r}") from exc


#: ``trimmed/stage<N>_cam_<video_id>_trimmed.mp4`` -- the audit trims a
#: ``trimmed/`` dir holds, the same shape ``sync.plan`` pushes.
TRIMMED_CLIP_GLOB = "stage*_cam_*_trimmed.mp4"


def backfill_web_trims(
    trimmed_dir: Path,
    config: WebTrimConfig,
    *,
    ffmpeg_binary: str = "ffmpeg",
    runner: Runner = subprocess.run,
    on_progress: Callable[[int, int, Path], None] = lambda i, n, p: None,
) -> tuple[list[Path], list[str]]:
    """Give every audit trim under ``trimmed_dir`` a web rendition (#1031).

    A rendition at least as new as its trim is kept; a missing or older
    one is transcoded from the trim. Returns ``(cut, errors)``: the
    renditions written this call, and one message per trim whose
    transcode failed (the rest still run). A missing dir is a no-op.
    ``on_progress(i, n, trim)`` fires before each transcode.
    """
    cut: list[Path] = []
    errors: list[str] = []
    if not trimmed_dir.is_dir():
        return cut, errors
    todo: list[Path] = []
    for trimmed in sorted(trimmed_dir.glob(TRIMMED_CLIP_GLOB)):
        web = web_trim_path(trimmed)
        if web.exists() and web.stat().st_size > 0 and web.stat().st_mtime >= trimmed.stat().st_mtime:
            continue
        todo.append(trimmed)
    for i, trimmed in enumerate(todo, start=1):
        on_progress(i, len(todo), trimmed)
        web = web_trim_path(trimmed)
        partial = web.with_name(f"{web.stem}.partial{web.suffix}")
        try:
            transcode_web_trim(trimmed, partial, config, ffmpeg_binary=ffmpeg_binary, runner=runner)
            if not (partial.exists() and partial.stat().st_size > 0):
                raise FFmpegError("ffmpeg produced no output")
            partial.replace(web)
        except FFmpegError as exc:
            if partial.exists():
                partial.unlink()
            errors.append(f"{trimmed.name}: web rendition not cut ({exc})")
            continue
        cut.append(web)
    return cut, errors
