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


# --- registration -----------------------------------------------------------------


def _project(tmp_path: Path):
    from splitsmith.match_project import MatchProject, StageEntry

    root = tmp_path / "shooter"
    project = MatchProject.init(root, name="M")
    project.stages = [StageEntry(stage_number=1, stage_name="S1", time_seconds=10.0)]
    return project, root


def test_registration_stores_the_fingerprint(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    clip = _write(tmp_path / "phone" / "IMG_1.MOV", os.urandom(3 * CHUNK))
    video = project.register_video(clip, root)
    assert video.fingerprint == clip_fingerprint(clip)


def test_the_same_clip_at_a_second_path_is_not_registered_twice(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    data = os.urandom(3 * CHUNK)
    first = project.register_video(_write(tmp_path / "phone" / "IMG_1.MOV", data), root)
    project.assign_video(first.path, to_stage_number=1, role="primary")
    again = project.register_video(_write(tmp_path / "nas" / "match" / "stage1-anna.mov", data), root)
    assert again is first
    assert project.unassigned_videos == []
    assert [str(v.path) for v in project.stage(1).videos] == [str(first.path)]


def test_backfill_fills_videos_registered_before_fingerprints(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    clip = _write(tmp_path / "phone" / "IMG_1.MOV", os.urandom(3 * CHUNK))
    video = project.register_video(clip, root)
    video.fingerprint = None  # as saved by an older version
    assert project.backfill_fingerprints(root) == 1
    assert video.fingerprint == clip_fingerprint(clip)
    assert project.backfill_fingerprints(root) == 0


def test_backfill_skips_an_unreachable_source(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    clip = _write(tmp_path / "usb" / "IMG_1.MOV", os.urandom(3 * CHUNK))
    video = project.register_video(clip, root)
    video.fingerprint = None
    clip.unlink()  # the cam is unplugged
    assert project.backfill_fingerprints(root) == 0
    assert video.fingerprint is None


def test_empty_files_are_never_matched_by_content(tmp_path: Path) -> None:
    """A zero-byte file is a failed copy, not a recording: two of them are
    two different clips that did not arrive, never the same one."""
    project, root = _project(tmp_path)
    a = project.register_video(_write(tmp_path / "a" / "VID_a.mp4", b""), root)
    b = project.register_video(_write(tmp_path / "b" / "VID_b.mp4", b""), root)
    assert a is not b
    assert a.fingerprint is None and b.fingerprint is None
