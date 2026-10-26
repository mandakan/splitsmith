"""Reference in place on Windows: every raw/ consumer follows a pointer file.

Windows cannot make a symlink without Developer Mode, so "Reference in
place" writes ``raw/<name>.ref`` (``splitsmith.raw_link``) instead of a
symlink, and before this the register fell back to copying the whole clip.
These run in pointer mode on any host, so the Windows path is exercised on
the Mac and Linux suite too; the stored path stays ``raw/<name>``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith import match_model, raw_link
from splitsmith.match_project import MatchProject, StageEntry
from splitsmith.relink import apply_relink, inspect_links
from splitsmith.ui.shooter_move import move_shooter
from tests.test_shooter_move import _local_audit_closures, _two_shooter_match
from tests.ui._helpers import _match_create_app, _MatchClient


@pytest.fixture(autouse=True)
def _pointers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(raw_link, "use_pointers", lambda: True)


def _project(tmp_path: Path) -> tuple[MatchProject, Path]:
    root = tmp_path / "shooter"
    project = MatchProject.init(root, name="M")
    project.stages = [StageEntry(stage_number=n, stage_name=f"S{n}", time_seconds=10.0) for n in (1, 2)]
    return project, root


def _clip(tmp_path: Path, folder: str, data: bytes = b"footage", name: str = "VID.mp4") -> Path:
    f = tmp_path / folder / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    return f


def test_register_writes_a_pointer_not_a_copy(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    src = _clip(tmp_path, "cam")

    video = project.register_video(src, root)

    entry = root / "raw" / "VID.mp4"
    assert video.path.as_posix() == "raw/VID.mp4"
    assert not entry.exists() and not entry.is_symlink()  # no copy, no symlink
    assert raw_link.target(entry) == src
    assert project.resolve_video_path(root, video.path) == src
    assert project.source_present(root, video.path)


def test_the_same_source_twice_is_one_entry_and_a_namesake_gets_its_own(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    src = _clip(tmp_path, "cam")
    other = _clip(tmp_path, "club-mate", b"another clip")

    first = project.register_video(src, root)
    again = project.register_video(src, root)
    namesake = project.register_video(other, root)

    assert again.path == first.path
    assert namesake.path.as_posix() == "raw/VID-2.mp4"
    assert project.resolve_video_path(root, namesake.path) == other


def test_an_unplugged_source_is_reported_missing(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    src = _clip(tmp_path, "cam")
    video = project.register_video(src, root)
    src.unlink()

    assert not project.source_present(root, video.path)
    assert not project.resolve_video_path(root, video.path).exists()


def test_relink_reports_and_repoints_a_pointer(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    src = _clip(tmp_path, "cam")
    project.register_video(src, root)
    moved = _clip(tmp_path, "archive")
    src.unlink()

    [info] = inspect_links(project, root)
    assert info.status == "broken"
    assert info.target == src

    [applied] = apply_relink([(info.link_path, moved)])

    assert applied.previous_target == src
    [after] = inspect_links(project, root)
    assert after.status == "ok"
    assert project.resolve_video_path(root, project.unassigned_videos[0].path) == moved


def test_a_shooter_move_carries_the_pointer(tmp_path: Path) -> None:
    root, slug_a, slug_b = _two_shooter_match(tmp_path)
    root_a = match_model.Match.shooter_root(root, slug_a)
    root_b = match_model.Match.shooter_root(root, slug_b)
    proj_a, proj_b = MatchProject.load(root_a), MatchProject.load(root_b)
    src = _clip(tmp_path, "cam", name="GH01.MP4")
    video = proj_a.register_video(src, root_a)
    proj_a.assign_video(video.path, to_stage_number=1, role="primary")
    proj_a.save(root_a)

    load_tgt, save_tgt, load_src, clear_src = _local_audit_closures(proj_a, root_a, proj_b, root_b)
    outcome = move_shooter(
        source_project=proj_a,
        source_root=root_a,
        target_project=proj_b,
        target_root=root_b,
        video_paths=["raw/GH01.MP4"],
        load_target_audit=load_tgt,
        save_target_audit=save_tgt,
        load_source_audit=load_src,
        clear_source_audit=clear_src,
        storage=None,
    )

    assert len(outcome.moved) == 1
    assert not raw_link.present(proj_a.raw_path(root_a) / "GH01.MP4")
    assert raw_link.target(proj_b.raw_path(root_b) / "GH01.MP4") == src
    assert proj_b.resolve_video_path(root_b, proj_b.stage(1).primary().path) == src  # type: ignore[union-attr]
    assert src.read_bytes() == b"footage"


def test_removing_a_video_drops_its_pointer_and_never_the_source(tmp_path: Path) -> None:
    root = tmp_path / "match"
    src = _clip(tmp_path, "ext", name="clip.mp4")
    client = _MatchClient(_match_create_app(project_root=root, project_name="x"))
    resp = client.post(
        "/api/shooters/me/videos/scan",
        json={"source_paths": [str(src)], "auto_assign_primary": False},
    )
    assert resp.status_code == 200
    registered = resp.json()["registered"][0]
    entry = root / "shooters" / "me" / "raw" / "clip.mp4"
    assert raw_link.target(entry) == src

    resp = client.post("/api/shooters/me/videos/remove", json={"video_path": registered})

    assert resp.status_code == 200
    assert not raw_link.present(entry)
    assert src.read_bytes() == b"footage"


def test_a_confined_project_never_follows_a_pointer(tmp_path: Path) -> None:
    # Hosted: a pointer's text is an arbitrary absolute path on the worker.
    project, root = _project(tmp_path)
    src = _clip(tmp_path, "cam")
    video = project.register_video(src, root)
    project.confine_paths()

    assert project.resolve_video_path(root, video.path) == root / "raw" / "VID.mp4"
    assert not project.source_present(root, video.path)


def test_a_raw_dir_outside_the_project_follows_its_pointer(tmp_path: Path) -> None:
    # The stored path is then the entry's absolute path.
    project, root = _project(tmp_path)
    project.raw_dir = str(tmp_path / "scratch-ssd" / "raw")
    src = _clip(tmp_path, "cam")

    video = project.register_video(src, root)

    assert video.path.is_absolute()
    assert project.resolve_video_path(root, video.path) == src
    assert project.source_present(root, video.path)
