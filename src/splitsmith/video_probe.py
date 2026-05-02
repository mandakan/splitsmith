"""ffprobe wrapper with mtime/size-keyed disk cache.

Used by the production UI's folder picker so video rows can show duration
alongside size + filename (issue #24). Caching keeps repeat listings of a
USB-mounted directory cheap once the first scan completes.

The cache key is ``sha1(absolute_path + mtime + size)`` truncated to 16 hex
chars; any source-side change flips the key naturally, so cache invalidation
is automatic. Stale cache entries are harmless leftovers -- they're never
read (the lookup path always reflects current source state).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel


class ProbeError(RuntimeError):
    """ffprobe failed or timed out."""


class ProbeResult(BaseModel):
    """Subset of ffprobe output we care about for the picker + tray."""

    duration: float | None = None
    width: int | None = None
    height: int | None = None
    codec: str | None = None


def source_cache_key(path: Path) -> str:
    """Return a short cache key for ``path`` based on its absolute path,
    mtime, and size. Returns an empty string when the file can't be stat'd
    (broken symlink etc.) so callers can skip caching cleanly."""
    try:
        stat = path.stat()
    except OSError:
        return ""
    payload = f"{path.resolve()}\n{stat.st_mtime}\n{stat.st_size}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def cached(path: Path, cache_dir: Path) -> ProbeResult | None:
    """Look up a previously-cached probe for ``path``. Returns ``None`` on miss.

    Reading is cheap: one stat to compute the key, one open for the JSON.
    """
    key = source_cache_key(path)
    if not key:
        return None
    cache_file = cache_dir / f"{key}.json"
    if not cache_file.exists():
        return None
    try:
        return ProbeResult.model_validate_json(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def probe(
    path: Path,
    *,
    cache_dir: Path,
    ffprobe_binary: str = "ffprobe",
    timeout: float = 4.0,
) -> ProbeResult:
    """Probe ``path`` and persist the result under ``cache_dir``.

    Caches on success; raises :class:`ProbeError` on failure (ffprobe missing,
    non-zero exit, timeout, malformed output). The caller is responsible for
    deciding whether a probe failure is worth surfacing -- the picker treats
    ``ProbeError`` as "leave duration null and move on".
    """
    hit = cached(path, cache_dir)
    if hit is not None:
        return hit

    if not shutil.which(ffprobe_binary):
        raise ProbeError(f"ffprobe binary not found: {ffprobe_binary}")

    cmd = [
        ffprobe_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-select_streams",
        "v:0",
        str(path),
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"ffprobe timed out on {path}") from exc
    except subprocess.CalledProcessError as exc:
        raise ProbeError(
            f"ffprobe failed (exit {exc.returncode}): {exc.stderr or exc.stdout!r}"
        ) from exc

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe returned invalid JSON for {path}") from exc

    result = _parse(payload)
    key = source_cache_key(path)
    if key:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{key}.json").write_text(
            result.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
    return result


def _parse(payload: dict) -> ProbeResult:
    fmt = payload.get("format") or {}
    streams = payload.get("streams") or []
    video = streams[0] if streams else {}

    duration_raw = fmt.get("duration") or video.get("duration")
    duration: float | None
    try:
        duration = float(duration_raw) if duration_raw is not None else None
    except (TypeError, ValueError):
        duration = None

    return ProbeResult(
        duration=duration,
        width=video.get("width"),
        height=video.get("height"),
        codec=video.get("codec_name"),
    )
