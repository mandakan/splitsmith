"""Tests for ``splitsmith.relink``: filesystem inspection, recursive
basename indexing, plan building, and apply-time symlink rewriting.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.relink import (
    apply_relink,
    index_search_root,
    inspect_links,
    plan_relink,
)
from splitsmith.ui.project import MatchProject, StageEntry, StageVideo


def _project_with_videos(root: Path, names: list[str]) -> MatchProject:
    """Init a project with one stage and a symlinked entry per name.

    Each ``raw/<name>`` symlink targets a stub file under
    ``root.parent/originals/`` so :func:`inspect_links` reports
    ``status=ok``.
    """
    project = MatchProject.init(root, name="relink-test")
    raw = project.raw_path(root)
    raw.mkdir(parents=True, exist_ok=True)
    originals = root.parent / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    videos: list[StageVideo] = []
    for name in names:
        original = originals / name
        original.write_bytes(b"\x00")
        link = raw / name
        link.symlink_to(original)
        videos.append(StageVideo(path=Path("raw") / name, role="primary"))
    project.stages = [StageEntry(stage_number=1, stage_name="Stage 1", time_seconds=0.0, videos=videos)]
    project.save(root)
    return project


def test_inspect_links_reports_ok_when_target_exists(tmp_path: Path) -> None:
    root = tmp_path / "match"
    project = _project_with_videos(root, ["a.mp4"])
    [info] = inspect_links(project, root)
    assert info.status == "ok"
    assert info.is_symlink
    assert info.target_exists


def test_inspect_links_reports_broken_when_target_gone(tmp_path: Path) -> None:
    root = tmp_path / "match"
    project = _project_with_videos(root, ["a.mp4"])
    (tmp_path / "originals" / "a.mp4").unlink()
    [info] = inspect_links(project, root)
    assert info.status == "broken"
    assert info.is_symlink
    assert not info.target_exists


def test_inspect_links_reports_missing_when_link_gone(tmp_path: Path) -> None:
    root = tmp_path / "match"
    project = _project_with_videos(root, ["a.mp4"])
    (project.raw_path(root) / "a.mp4").unlink()
    [info] = inspect_links(project, root)
    assert info.status == "missing_link"
    assert info.target is None


def test_inspect_links_reports_not_a_symlink_for_regular_files(tmp_path: Path) -> None:
    root = tmp_path / "match"
    project = _project_with_videos(root, ["a.mp4"])
    raw = project.raw_path(root)
    (raw / "a.mp4").unlink()
    (raw / "a.mp4").write_bytes(b"copied")
    [info] = inspect_links(project, root)
    assert info.status == "not_a_symlink"


def test_index_search_root_walks_recursively_and_groups_basenames(tmp_path: Path) -> None:
    root = tmp_path / "share"
    (root / "headcams").mkdir(parents=True)
    (root / "handhelds").mkdir(parents=True)
    (root / "ignore-me.txt").write_text("not a video")
    (root / "headcams" / "IMG_3131.MOV").write_bytes(b"")
    (root / "handhelds" / "IMG_3131.MOV").write_bytes(b"")  # same name, different dir
    (root / "handhelds" / "VID_001.mp4").write_bytes(b"")
    index = index_search_root(root)
    assert set(index.keys()) == {"img_3131.mov", "vid_001.mp4"}
    assert len(index["img_3131.mov"]) == 2
    assert len(index["vid_001.mp4"]) == 1


def test_plan_relink_auto_chooses_unique_candidate(tmp_path: Path) -> None:
    root = tmp_path / "match"
    project = _project_with_videos(root, ["a.mp4"])
    # Move the original to a "share" dir so the auto-pick has somewhere to go.
    share = tmp_path / "share" / "headcams"
    share.mkdir(parents=True)
    (share / "a.mp4").write_bytes(b"new home")
    links = inspect_links(project, root)
    plan = plan_relink(links, index_search_root(tmp_path / "share"))
    [entry] = plan
    assert entry.chosen_path == (share / "a.mp4").resolve()
    assert not entry.ambiguous


def test_plan_relink_leaves_chosen_none_when_ambiguous(tmp_path: Path) -> None:
    root = tmp_path / "match"
    project = _project_with_videos(root, ["a.mp4"])
    share = tmp_path / "share"
    (share / "x").mkdir(parents=True)
    (share / "y").mkdir(parents=True)
    (share / "x" / "a.mp4").write_bytes(b"")
    (share / "y" / "a.mp4").write_bytes(b"")
    plan = plan_relink(inspect_links(project, root), index_search_root(share))
    [entry] = plan
    assert entry.ambiguous
    assert entry.chosen_path is None
    assert len(entry.candidates) == 2


def test_plan_relink_skips_already_correct_targets(tmp_path: Path) -> None:
    """If the symlink already points at the only candidate found in the
    search root, the plan should not propose rewriting it."""
    root = tmp_path / "match"
    project = _project_with_videos(root, ["a.mp4"])
    # The only candidate in the search root is the existing target.
    share = tmp_path / "originals"
    plan = plan_relink(inspect_links(project, root), index_search_root(share))
    [entry] = plan
    assert entry.candidates == [(share / "a.mp4").resolve()]
    assert entry.chosen_path is None


def test_apply_relink_rewrites_symlink_and_records_previous_target(tmp_path: Path) -> None:
    root = tmp_path / "match"
    project = _project_with_videos(root, ["a.mp4"])
    raw = project.raw_path(root)
    new_target = tmp_path / "share" / "subdir" / "a.mp4"
    new_target.parent.mkdir(parents=True)
    new_target.write_bytes(b"new")
    [applied] = apply_relink([(raw / "a.mp4", new_target)])
    assert applied.previous_target == tmp_path / "originals" / "a.mp4"
    assert applied.new_target == new_target.resolve()
    # And the link now resolves to the new target.
    assert (raw / "a.mp4").resolve() == new_target.resolve()


def test_apply_relink_refuses_to_overwrite_regular_file(tmp_path: Path) -> None:
    root = tmp_path / "match"
    project = _project_with_videos(root, ["a.mp4"])
    raw = project.raw_path(root)
    (raw / "a.mp4").unlink()
    (raw / "a.mp4").write_bytes(b"copied, not symlinked")
    new_target = tmp_path / "share" / "a.mp4"
    new_target.parent.mkdir()
    new_target.write_bytes(b"")
    with pytest.raises(ValueError, match="not_a_symlink"):
        apply_relink([(raw / "a.mp4", new_target)])


def test_apply_relink_creates_link_when_missing(tmp_path: Path) -> None:
    """``missing_link`` entries -- the symlink itself was deleted --
    should still be relinkable."""
    root = tmp_path / "match"
    project = _project_with_videos(root, ["a.mp4"])
    raw = project.raw_path(root)
    (raw / "a.mp4").unlink()
    new_target = tmp_path / "share" / "a.mp4"
    new_target.parent.mkdir()
    new_target.write_bytes(b"")
    [applied] = apply_relink([(raw / "a.mp4", new_target)])
    assert applied.previous_target is None
    assert (raw / "a.mp4").is_symlink()
