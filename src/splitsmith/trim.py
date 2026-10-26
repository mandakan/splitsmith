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

import json
import os
import platform
import subprocess
import uuid
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Literal

from .config import Config, TrimResult, WebTrimConfig
from .derived import locked
from .media_check import valid_mp4, valid_mp4_cached
from .runtime import ENV_CONFIG_FILE, ffmpeg_path

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


class SourceReadError(FFmpegError):
    """ffmpeg reported a problem reading a URL source (#1674).

    A dropped connection that cannot resume, or a range read that hits EOF,
    exits 0 with a short or damaged cut and an error line on stderr (measured
    for the proxy, ``proxy.ProxySourceReadError``). The caller discards the
    cut and cuts again from a downloaded copy.
    """


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
    ffmpeg_binary = ffmpeg_path(ffmpeg_binary)
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


def beep_in_trim(beep_time: float, pre_buffer_seconds: float) -> float:
    """Where the beep sits in a trim :func:`trim_video` cut with
    ``pre_buffer_seconds`` around a beep at ``beep_time`` in the source.

    The cut starts at ``max(0, beep_time - pre_buffer_seconds)``, so the beep
    is the pre-buffer into the trim, unless the source had less than that
    before the beep: then the trim starts at the source's first frame and the
    beep sits ``beep_time`` into it (issue #1430). The same anchor the
    players use (``ui.audio``, ``_video_clip_anchor``)."""
    return min(pre_buffer_seconds, beep_time)


def trim_video(
    input_path: Path | str,
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

    ``input_path`` may be a ``str`` URL that ffmpeg reads over HTTP (#1674):
    with ``-ss`` before ``-i`` it reads about the window's bytes, not the
    whole object. Any stderr output then raises :class:`SourceReadError`,
    since a failed read does not change the exit code, and no error message
    carries the URL.

    Returns the absolute-source-time window of the cut. Raises ``FFmpegError``
    if ffmpeg fails or is not installed.
    """
    from .proxy import HTTP_INPUT_ARGS, redact_urls

    is_url = isinstance(input_path, str)
    ffmpeg_binary = ffmpeg_path(ffmpeg_binary)
    pre = pre_buffer_seconds if pre_buffer_seconds is not None else buffer_seconds
    post = post_buffer_seconds if post_buffer_seconds is not None else buffer_seconds
    # A beep before 0 is a secondary camera that started during the beep
    # (2026-10-15): the cut starts at its first frame, as for any beep
    # nearer the start than the pre-buffer.
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
    if not is_url and not input_path.exists():
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
        *(HTTP_INPUT_ARGS if is_url else ()),
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
        completed = runner(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise FFmpegError(f"ffmpeg binary not found: {ffmpeg_binary}") from exc
    except subprocess.CalledProcessError as exc:
        if is_url:
            detail = redact_urls(exc.stderr or exc.stdout)
            raise SourceReadError(f"ffmpeg failed (exit {exc.returncode}): {detail!r}") from None
        raise FFmpegError(f"ffmpeg failed (exit {exc.returncode}): {exc.stderr or exc.stdout!r}") from exc
    stderr = getattr(completed, "stderr", None)
    if is_url and isinstance(stderr, str) and stderr.strip():
        raise SourceReadError(f"ffmpeg reported errors reading the source: {redact_urls(stderr).strip()!r}")

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


def unique_partial(final: Path) -> Path:
    """A partial name no other process can share (the 2026-09-27 corruption
    came from every process writing ``<stem>.partial.mp4``). Ends in
    ``.partial.mp4`` so every glob that skips or sweeps partials still does."""
    return final.with_name(f"{final.stem}.{os.getpid()}-{uuid.uuid4().hex[:8]}.partial{final.suffix}")


def rendition_ok(trimmed: Path) -> bool:
    """The trim's web rendition is present, not older than the trim, and
    structurally valid. Validity adds to the mtime rule, never replaces it:
    a valid rendition of an older window is still stale."""
    web = web_trim_path(trimmed)
    try:
        web_st = web.stat()
        if web_st.st_size == 0 or web_st.st_mtime < trimmed.stat().st_mtime:
            return False
    except OSError:
        return False
    return valid_mp4_cached(web).ok


def write_web_rendition(
    trimmed: Path,
    config: WebTrimConfig,
    *,
    ffmpeg_binary: str = "ffmpeg",
    runner: Runner = subprocess.run,
) -> Path:
    """Cut the trim's web rendition and publish it only when it passes the
    structural check (spec 2026-10-12 derived-media self-heal). A per-file
    lock makes a second writer wait and then reuse the first's rendition; the
    partial name is per process. Raises ``FFmpegError`` when ffmpeg fails or
    its output fails the check; the partial is always removed and a previous
    good rendition is never replaced by a bad one."""
    ffmpeg_binary = ffmpeg_path(ffmpeg_binary)
    web = web_trim_path(trimmed)
    # ``<stem>.lock`` beside the rendition, the lock a pull of it also takes.
    with locked(web):
        if rendition_ok(trimmed):
            return web
        partial = unique_partial(web)
        try:
            transcode_web_trim(trimmed, partial, config, ffmpeg_binary=ffmpeg_binary, runner=runner)
            check = valid_mp4(partial)
            if not check.ok:
                raise FFmpegError(f"web rendition failed its check: {check.reason}")
            partial.replace(web)
        finally:
            if partial.exists():
                partial.unlink()
    return web


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
    ffmpeg_binary = ffmpeg_path(ffmpeg_binary)
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


def audit_trim_name(stage_number: int, video_id: str) -> str:
    """Basename of a video's audit trim under ``trimmed/`` (see
    :data:`TRIMMED_CLIP_GLOB`)."""
    return f"stage{stage_number}_cam_{video_id}_trimmed.mp4"


def trim_params_path(output: Path) -> Path:
    """Sidecar JSON next to a trimmed MP4 recording the inputs that
    produced it. Used to invalidate the cache when beep_time, stage_time,
    or the buffer settings change without the source file moving."""
    return output.with_name(f"{output.stem}.params.json")


def audit_trim_params(
    *,
    beep_time: float,
    stage_time_seconds: float,
    pre_buffer_seconds: float,
    post_buffer_seconds: float,
) -> dict[str, float]:
    """What the params sidecar of an audit trim cut now would record: the
    one spelling the trim job writes and every reader that asks "is this
    trim current" compares against."""
    return {
        "beep_time": round(float(beep_time), 4),
        "stage_time_seconds": round(float(stage_time_seconds), 4),
        "pre_buffer_seconds": float(pre_buffer_seconds),
        "post_buffer_seconds": float(post_buffer_seconds),
    }


def source_mtime(source: Path | None) -> float | None:
    """``source``'s mtime when it is on this disk, else ``None`` (no source
    named, an unplugged drive, a hosted container without the raw file)."""
    if source is None:
        return None
    try:
        return source.stat().st_mtime
    except OSError:
        return None


def audit_trim_matches(trim: Path, params: dict[str, float], *, source: Path | None = None) -> bool:
    """Whether the audit trim at ``trim`` is current on this disk: non-empty,
    its params sidecar records exactly ``params`` (:func:`audit_trim_params`),
    and it is not older than ``source`` when that is on this disk. The rule
    the trim job reuses a cut by (``ui.audio.ensure_video_audit_trim``); a
    missing or unreadable sidecar is a mismatch, since nothing says what the
    trim was cut for."""
    try:
        stat = trim.stat()
        if stat.st_size == 0:
            return False
        newer_source = source_mtime(source)
        if newer_source is not None and stat.st_mtime < newer_source:
            return False
        recorded = json.loads(trim_params_path(trim).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return recorded == params


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
    ffmpeg_binary = ffmpeg_path(ffmpeg_binary)
    cut: list[Path] = []
    errors: list[str] = []
    if not trimmed_dir.is_dir():
        return cut, errors
    todo: list[Path] = []
    for trimmed in sorted(trimmed_dir.glob(TRIMMED_CLIP_GLOB)):
        if rendition_ok(trimmed):
            continue
        todo.append(trimmed)
    for i, trimmed in enumerate(todo, start=1):
        on_progress(i, len(todo), trimmed)
        try:
            write_web_rendition(trimmed, config, ffmpeg_binary=ffmpeg_binary, runner=runner)
        except FFmpegError as exc:
            errors.append(f"{trimmed.name}: web rendition not cut ({exc})")
            continue
        cut.append(web_trim_path(trimmed))
    return cut, errors
