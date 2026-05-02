"""ffmpeg thumbnail + short-clip extraction with mtime/size-keyed disk cache.

Pairs with :mod:`splitsmith.video_probe` for the production UI (issue #24):
the picker and tray render thumbnails so the user can tell which clip is
which without filenames doing all the work.

Cache layout: one ``<sha1>.jpg`` per source video under ``cache_dir``, where
the SHA1 keys on absolute path + mtime + size. A source-side change flips
the key automatically.

Frame selection: 1.0 s by default; clamped to ``min(1.0, duration * 0.1)``
when the source is shorter so we don't seek past the end. Caller passes
``duration`` if known (saves a redundant ffprobe).

Short clip extraction (issue #27): :func:`ensure_clip` produces a tiny MP4
around a target time -- the Ingest screen uses it to render an inline beep
preview so the user can eyeball detection without leaving the page. Cache
key extends the still-frame key with center-time + duration so re-detection
naturally invalidates the preview.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .video_probe import source_cache_key


class ThumbnailError(RuntimeError):
    """ffmpeg failed or timed out."""


def cached(path: Path, cache_dir: Path) -> Path | None:
    """Return the cached thumbnail path for ``path`` if it exists, else ``None``."""
    key = source_cache_key(path)
    if not key:
        return None
    candidate = cache_dir / f"{key}.jpg"
    return candidate if candidate.exists() else None


def thumbnail_path(path: Path, cache_dir: Path) -> Path | None:
    """Resolve the thumbnail path (existing or expected) for ``path``.

    Returns ``None`` only when ``path`` cannot be stat'd. The returned path
    may not exist yet -- use :func:`cached` for an existence-aware lookup.
    """
    key = source_cache_key(path)
    if not key:
        return None
    return cache_dir / f"{key}.jpg"


def ensure(
    source: Path,
    *,
    cache_dir: Path,
    duration: float | None = None,
    width: int = 320,
    ffmpeg_binary: str = "ffmpeg",
    timeout: float = 6.0,
) -> Path:
    """Extract a thumbnail for ``source`` (or return the cached one).

    Picks ``t = min(1.0, duration * 0.1)`` when ``duration`` is known and
    short, otherwise 1.0 s. Always clamps to >= 0.1 s so the seek isn't
    negative for very short clips.

    Returns the absolute path to the thumbnail jpg. Raises
    :class:`ThumbnailError` on ffmpeg failure / missing binary / timeout.
    """
    hit = cached(source, cache_dir)
    if hit is not None:
        return hit

    dest = thumbnail_path(source, cache_dir)
    if dest is None:
        raise ThumbnailError(f"cannot stat source: {source}")

    if not shutil.which(ffmpeg_binary):
        raise ThumbnailError(f"ffmpeg binary not found: {ffmpeg_binary}")

    if duration is not None and duration > 0:
        t = max(0.1, min(1.0, duration * 0.1))
    else:
        t = 1.0

    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
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
        f"scale={width}:-2",
        "-q:v",
        "4",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ThumbnailError(f"ffmpeg timed out extracting thumbnail for {source}") from exc
    except subprocess.CalledProcessError as exc:
        raise ThumbnailError(
            f"ffmpeg failed (exit {exc.returncode}): {exc.stderr or exc.stdout!r}"
        ) from exc
    if not dest.exists():
        raise ThumbnailError(f"ffmpeg produced no output for {source}")
    return dest


def clip_cache_key(source: Path, *, center_time: float, duration_s: float) -> str:
    """Return a content-addressed key for a short clip cached around
    ``center_time``.

    Extends :func:`source_cache_key` with millisecond-rounded center-time
    and duration so a re-detected beep generates a fresh preview without
    stomping the previous one (kept around for cache eviction at leisure).
    Returns an empty string when the source can't be stat'd.
    """
    base = source_cache_key(source)
    if not base:
        return ""
    return f"{base}_t{int(round(center_time * 1000)):d}_d{int(round(duration_s * 1000)):d}"


def cached_clip(
    source: Path,
    cache_dir: Path,
    *,
    center_time: float,
    duration_s: float,
    ext: str = "mp4",
) -> Path | None:
    """Return the cached short clip for ``source`` at ``center_time`` if it
    exists, else ``None``."""
    key = clip_cache_key(source, center_time=center_time, duration_s=duration_s)
    if not key:
        return None
    candidate = cache_dir / f"{key}.{ext}"
    return candidate if candidate.exists() else None


def ensure_clip(
    source: Path,
    *,
    cache_dir: Path,
    center_time: float,
    duration_s: float = 1.0,
    width: int = 240,
    ffmpeg_binary: str = "ffmpeg",
    timeout: float = 12.0,
) -> Path:
    """Extract a short MP4 around ``center_time`` (or return the cached one).

    The clip spans roughly ``[center_time - duration_s/2, +duration_s/2]``,
    clamped at zero so a beep close to the start of a recording still gets
    a preview. Audio is kept (AAC) -- verifying a beep needs both eyes and
    ears, especially when the detector landed on a steel ring or another
    bay's distant beep that *looks* wrong but only the audio confirms it.

    Returns the absolute path to the resulting MP4. Raises
    :class:`ThumbnailError` on ffmpeg failure / missing binary / timeout.
    """
    if duration_s <= 0:
        raise ThumbnailError(f"duration_s must be positive, got {duration_s}")

    hit = cached_clip(source, cache_dir, center_time=center_time, duration_s=duration_s)
    if hit is not None:
        return hit

    key = clip_cache_key(source, center_time=center_time, duration_s=duration_s)
    if not key:
        raise ThumbnailError(f"cannot stat source: {source}")
    dest = cache_dir / f"{key}.mp4"

    if not shutil.which(ffmpeg_binary):
        raise ThumbnailError(f"ffmpeg binary not found: {ffmpeg_binary}")

    start = max(0.0, center_time - duration_s / 2.0)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(source),
        "-t",
        f"{duration_s:.3f}",
        "-vf",
        f"scale={width}:-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-ac",
        "1",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ThumbnailError(f"ffmpeg timed out extracting clip for {source}") from exc
    except subprocess.CalledProcessError as exc:
        raise ThumbnailError(
            f"ffmpeg failed (exit {exc.returncode}): {exc.stderr or exc.stdout!r}"
        ) from exc
    if not dest.exists():
        raise ThumbnailError(f"ffmpeg produced no clip for {source}")
    return dest
