"""Tiny MP4 byte strings for tests: structurally valid (what valid_mp4 accepts)
and the corrupt shape found on 2026-10-12 (a moov whose first child claims the
size of the media data)."""

from __future__ import annotations

import struct


def _box(typ: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + typ + payload


def _full(typ: bytes, payload: bytes) -> bytes:
    return _box(typ, b"\x00\x00\x00\x00" + payload)


def _trak(handler: bytes, samples: int) -> bytes:
    hdlr = _full(b"hdlr", b"\x00\x00\x00\x00" + handler + b"\x00" * 12 + b"\x00")
    stsz = _full(b"stsz", struct.pack(">II", 1000, samples))
    stbl = _box(b"stbl", stsz)
    minf = _box(b"minf", stbl)
    mdia = _box(b"mdia", hdlr + minf)
    return _box(b"trak", mdia)


def tiny_mp4(*, duration_s: float = 1.0, video_samples: int = 25, with_video: bool = True) -> bytes:
    timescale = 1000
    mvhd = _full(b"mvhd", struct.pack(">IIII", 0, 0, timescale, int(duration_s * timescale)) + b"\x00" * 80)
    traks = (_trak(b"vide", video_samples) if with_video else b"") + _trak(b"soun", 40)
    moov = _box(b"moov", mvhd + traks)
    ftyp = _box(b"ftyp", b"isom" + b"\x00\x00\x02\x00" + b"isomiso2avc1mp41")
    mdat = _box(b"mdat", b"\x00" * 64)
    return ftyp + moov + mdat


def corrupt_moov_mp4() -> bytes:
    """The Stockholm stage 1 shape: a full-length file whose moov's first
    child claims far more bytes than the moov holds."""
    ftyp = _box(b"ftyp", b"isom" + b"\x00\x00\x02\x00" + b"isomiso2avc1mp41")
    bogus_child = struct.pack(">I", 16_770_410) + b"mvhd" + b"\x00" * 92
    moov = _box(b"moov", bogus_child)
    mdat = _box(b"mdat", b"\x00" * 64)
    return ftyp + moov + mdat
