"""A cheap content fingerprint for a video file (#1124, part 2).

The same clip can reach a match from two places (a phone export and later
a file server, or one copy in two folders); the import keyed on the
resolved path and took it twice. ``size + sha256(first 1 MiB + last 1 MiB)``
identifies a camera file without reading it whole.
"""

from __future__ import annotations

import os
from pathlib import Path

from splitsmith.fingerprint import CHUNK, clip_fingerprint


def _write(p: Path, data: bytes) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_the_same_bytes_at_two_paths_match(tmp_path: Path) -> None:
    data = os.urandom(3 * CHUNK)
    a = _write(tmp_path / "phone" / "IMG_1.MOV", data)
    b = _write(tmp_path / "nas" / "renamed.mov", data)
    assert clip_fingerprint(a) == clip_fingerprint(b)


def test_a_change_in_the_tail_differs(tmp_path: Path) -> None:
    data = bytearray(os.urandom(3 * CHUNK))
    a = _write(tmp_path / "a.mov", bytes(data))
    data[-1] ^= 0xFF
    b = _write(tmp_path / "b.mov", bytes(data))
    assert clip_fingerprint(a) != clip_fingerprint(b)


def test_a_change_in_the_head_differs(tmp_path: Path) -> None:
    data = bytearray(os.urandom(3 * CHUNK))
    a = _write(tmp_path / "a.mov", bytes(data))
    data[0] ^= 0xFF
    b = _write(tmp_path / "b.mov", bytes(data))
    assert clip_fingerprint(a) != clip_fingerprint(b)


def test_same_head_and_tail_but_another_size_differs(tmp_path: Path) -> None:
    head, tail = os.urandom(CHUNK), os.urandom(CHUNK)
    a = _write(tmp_path / "a.mov", head + b"x" * 10 + tail)
    b = _write(tmp_path / "b.mov", head + b"x" * 11 + tail)
    assert clip_fingerprint(a) != clip_fingerprint(b)


def test_a_file_shorter_than_two_chunks_is_read_whole(tmp_path: Path) -> None:
    a = _write(tmp_path / "a.mov", b"short clip")
    b = _write(tmp_path / "b.mov", b"short clip")
    c = _write(tmp_path / "c.mov", b"short clip!")
    assert clip_fingerprint(a) == clip_fingerprint(b) != clip_fingerprint(c)
