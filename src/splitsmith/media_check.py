"""Is an MP4 structurally playable? A pure walk of its boxes, no ffprobe.

Derived files (audit trims, web renditions) are published and trusted only
when this passes (spec 2026-10-12 derived-media self-heal): on 2026-09-27 two
processes interleaved their bytes in one partial file and 62 renditions with a
corrupt ``moov`` were renamed into place, trusted for being non-empty, and
uploaded. The check needs the top-level boxes to tile the file, a ``moov``
whose children parse inside it, a ``mvhd`` duration, and a video track
(``hdlr`` ``vide``) with samples. A few milliseconds per file.
"""

from __future__ import annotations

import struct
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"dinf"}


class MediaCheck(BaseModel):
    ok: bool
    reason: str | None = None
    duration_s: float | None = None
    video_samples: int = 0


class _StructureError(Exception):
    pass


def _boxes(data: bytes, start: int, end: int):
    pos = start
    while pos < end:
        if end - pos < 8:
            raise _StructureError("box header runs past its parent")
        size, typ = struct.unpack(">I4s", data[pos : pos + 8])
        header = 8
        if size == 1:
            if end - pos < 16:
                raise _StructureError("box header runs past its parent")
            size = struct.unpack(">Q", data[pos + 8 : pos + 16])[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            raise _StructureError(f"{typ.decode('latin1')} box overruns its parent")
        yield typ, pos + header, pos + size
        pos += size


def _track(data: bytes, start: int, end: int) -> tuple[bytes | None, int]:
    handler: bytes | None = None
    samples = 0
    for typ, s, e in _boxes(data, start, end):
        if typ == b"hdlr" and e - s >= 12:
            handler = data[s + 8 : s + 12]
        elif typ == b"stsz" and e - s >= 12:
            samples = struct.unpack(">I", data[s + 8 : s + 12])[0]
        elif typ in _CONTAINERS:
            h, n = _track(data, s, e)
            handler = handler or h
            samples = samples or n
    return handler, samples


def _check(data: bytes) -> MediaCheck:
    if not data:
        return MediaCheck(ok=False, reason="empty file")
    moov = None
    for typ, s, e in _boxes(data, 0, len(data)):
        if typ == b"moov":
            moov = (s, e)
    if moov is None:
        return MediaCheck(ok=False, reason="no moov box")
    duration: float | None = None
    video_samples = 0
    for typ, s, e in _boxes(data, *moov):
        if typ == b"mvhd" and e - s >= 20:
            version = data[s]
            if version == 1 and e - s >= 32:
                timescale, dur = struct.unpack(">IQ", data[s + 20 : s + 32])
            else:
                timescale, dur = struct.unpack(">II", data[s + 12 : s + 20])
            if timescale:
                duration = dur / timescale
        elif typ == b"trak":
            handler, samples = _track(data, s, e)
            if handler == b"vide":
                video_samples = max(video_samples, samples)
    if video_samples <= 0:
        return MediaCheck(ok=False, reason="no video track with samples", duration_s=duration)
    return MediaCheck(ok=True, duration_s=duration, video_samples=video_samples)


def valid_mp4(path: Path) -> MediaCheck:
    """Structural check of the MP4 at ``path``; a missing or unreadable file
    is invalid, never an error."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return MediaCheck(ok=False, reason=f"unreadable: {exc.strerror or exc}")
    try:
        return _check(data)
    except _StructureError as exc:
        return MediaCheck(ok=False, reason=f"moov or box structure broken: {exc}")


@lru_cache(maxsize=4096)
def _cached(path: str, size: int, mtime_ns: int) -> MediaCheck:
    return valid_mp4(Path(path))


def valid_mp4_cached(path: Path) -> MediaCheck:
    """``valid_mp4`` once per file version (path, size, mtime)."""
    try:
        st = path.stat()
    except OSError as exc:
        return MediaCheck(ok=False, reason=f"unreadable: {exc.strerror or exc}")
    return _cached(str(path), st.st_size, st.st_mtime_ns)
