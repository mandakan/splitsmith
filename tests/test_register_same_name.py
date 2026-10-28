"""Two different clips with the same file name both import (#1124, part 1).

``register_video`` keyed a video on ``raw/<source.name>`` and returned the
existing entry for any second file with that name. The footage sort then
assigned that entry to the second clip's stage -- moving the first clip off
its own stage -- and the second clip was never imported. Phone counters run
0001-9999 and club mates' folders repeat names, so this is the common case
when one shooter's footage comes from several sources.
"""

from __future__ import annotations

import os
from pathlib import Path

from splitsmith import raw_link
from splitsmith.match_project import MatchProject, StageEntry


def _project(tmp_path: Path) -> tuple[MatchProject, Path]:
    root = tmp_path / "shooter"
    project = MatchProject.init(root, name="M")
    project.stages = [StageEntry(stage_number=n, stage_name=f"S{n}", time_seconds=10.0) for n in (1, 2, 3)]
    return project, root


def _clip(tmp_path: Path, folder: str, data: bytes, name: str = "IMG_1234.MOV") -> Path:
    f = tmp_path / folder / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    return f


def test_same_name_different_clips_each_import(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    magnus = _clip(tmp_path, "from-magnus", b"magnus's clip")
    albin = _clip(tmp_path, "from-albin", b"albin's clip, longer")

    first = project.register_video(magnus, root)
    project.assign_video(first.path, to_stage_number=1, role="primary")
    second = project.register_video(albin, root)
    project.assign_video(second.path, to_stage_number=2, role="primary")

    assert second is not first
    assert first.path.as_posix() == "raw/IMG_1234.MOV"
    assert second.path.as_posix() == "raw/IMG_1234-2.MOV"
    assert [v.path.as_posix() for v in project.stage(1).videos] == ["raw/IMG_1234.MOV"]
    assert [v.path.as_posix() for v in project.stage(2).videos] == ["raw/IMG_1234-2.MOV"]
    assert raw_link.target(root / "raw" / "IMG_1234.MOV") == magnus
    assert raw_link.target(root / "raw" / "IMG_1234-2.MOV") == albin


def test_a_third_same_name_clip_takes_the_next_free_name(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    paths = [
        project.register_video(_clip(tmp_path, f"src{i}", f"clip {i}".encode()), root).path for i in range(3)
    ]
    assert [p.as_posix() for p in paths] == ["raw/IMG_1234.MOV", "raw/IMG_1234-2.MOV", "raw/IMG_1234-3.MOV"]


def test_the_same_source_twice_is_one_entry(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    magnus = _clip(tmp_path, "from-magnus", b"magnus's clip")
    first = project.register_video(magnus, root)
    again = project.register_video(magnus, root)
    assert again is first
    assert len(project.unassigned_videos) == 1


def test_a_link_left_behind_for_the_same_source_is_reused(tmp_path: Path) -> None:
    """The entry was removed but its raw link stayed (unplugged and
    re-plugged USB cam, a cleared tray): re-registering the same source
    reuses the link instead of inventing ``-2``. The link is matched by its
    target, not by size and mtime -- see the same-length clips in
    ``test_a_third_same_name_clip_takes_the_next_free_name``, which a
    size-and-mtime comparison of symlinked files would conflate."""
    project, root = _project(tmp_path)
    magnus = _clip(tmp_path, "usb", b"magnus's clip")
    first = project.register_video(magnus, root)
    project.unassigned_videos.clear()
    again = project.register_video(magnus, root)
    assert again.path.as_posix() == first.path.as_posix() == "raw/IMG_1234.MOV"


def test_a_copy_of_the_same_source_is_reused(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    magnus = _clip(tmp_path, "from-magnus", b"magnus's clip")
    first = project.register_video(magnus, root, link_mode="copy")
    project.unassigned_videos.clear()
    again = project.register_video(magnus, root, link_mode="copy")
    assert again.path.as_posix() == first.path.as_posix() == "raw/IMG_1234.MOV"


def test_a_different_file_already_in_raw_is_not_adopted(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    raw = root / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "IMG_1234.MOV").write_bytes(b"something the user put here")
    os.utime(raw / "IMG_1234.MOV", (1_000_000, 1_000_000))
    albin = _clip(tmp_path, "from-albin", b"albin's clip")

    video = project.register_video(albin, root)

    assert video.path.as_posix() == "raw/IMG_1234-2.MOV"
    assert (raw / "IMG_1234.MOV").read_bytes() == b"something the user put here"
    assert raw_link.target(raw / "IMG_1234-2.MOV") == albin
