"""End-to-end tests for the relink API endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith.ui.project import MatchProject, StageEntry, StageVideo
from splitsmith.ui.server import create_app


@pytest.fixture(autouse=True)
def _disable_auto_beep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")


def _setup_project(tmp_path: Path, names: list[str]) -> tuple[Path, dict[str, str]]:
    """Init a project with one stage and a symlinked entry per name.

    Returns ``(project_root, video_id_by_name)`` so tests can address the
    relink endpoint by ``video_id``.
    """
    root = tmp_path / "match"
    project = MatchProject.init(root, name="relink-api")
    raw = project.raw_path(root)
    raw.mkdir(parents=True, exist_ok=True)
    originals = tmp_path / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    videos: list[StageVideo] = []
    ids: dict[str, str] = {}
    for name in names:
        original = originals / name
        original.write_bytes(b"\x00")
        link = raw / name
        link.symlink_to(original)
        sv = StageVideo(path=Path("raw") / name, role="primary")
        videos.append(sv)
        ids[name] = sv.video_id
    project.stages = [StageEntry(stage_number=1, stage_name="Stage 1", time_seconds=0.0, videos=videos)]
    project.save(root)
    return root, ids


def test_link_status_reports_ok_for_intact_links(tmp_path: Path) -> None:
    root, ids = _setup_project(tmp_path, ["a.mp4"])
    client = TestClient(create_app(project_root=root, project_name="x"))
    resp = client.get("/api/shooters/me/videos/link-status")
    assert resp.status_code == 200
    [entry] = resp.json()["entries"]
    assert entry["video_id"] == ids["a.mp4"]
    assert entry["status"] == "ok"
    assert entry["current_target"] == str(tmp_path / "originals" / "a.mp4")


def test_link_status_reports_broken_after_target_removed(tmp_path: Path) -> None:
    root, _ = _setup_project(tmp_path, ["a.mp4"])
    (tmp_path / "originals" / "a.mp4").unlink()
    client = TestClient(create_app(project_root=root, project_name="x"))
    [entry] = client.get("/api/shooters/me/videos/link-status").json()["entries"]
    assert entry["status"] == "broken"


def test_relink_scan_reports_unique_candidate(tmp_path: Path) -> None:
    root, ids = _setup_project(tmp_path, ["a.mp4"])
    share = tmp_path / "share" / "headcams"
    share.mkdir(parents=True)
    (share / "a.mp4").write_bytes(b"")
    client = TestClient(create_app(project_root=root, project_name="x"))
    resp = client.post(
        "/api/shooters/me/videos/relink/scan",
        json={"search_root": str(tmp_path / "share")},
    )
    assert resp.status_code == 200
    body = resp.json()
    [entry] = body["entries"]
    assert entry["video_id"] == ids["a.mp4"]
    assert entry["found"]
    assert not entry["ambiguous"]
    assert entry["chosen_path"] == str((share / "a.mp4").resolve())


def test_relink_scan_reports_ambiguous_candidates(tmp_path: Path) -> None:
    root, _ = _setup_project(tmp_path, ["a.mp4"])
    share = tmp_path / "share"
    (share / "x").mkdir(parents=True)
    (share / "y").mkdir(parents=True)
    (share / "x" / "a.mp4").write_bytes(b"")
    (share / "y" / "a.mp4").write_bytes(b"")
    client = TestClient(create_app(project_root=root, project_name="x"))
    [entry] = client.post(
        "/api/shooters/me/videos/relink/scan",
        json={"search_root": str(share)},
    ).json()["entries"]
    assert entry["ambiguous"]
    assert entry["chosen_path"] is None
    assert len(entry["candidates"]) == 2


def test_relink_scan_400_on_missing_root(tmp_path: Path) -> None:
    root, _ = _setup_project(tmp_path, ["a.mp4"])
    client = TestClient(create_app(project_root=root, project_name="x"))
    resp = client.post(
        "/api/shooters/me/videos/relink/scan",
        json={"search_root": str(tmp_path / "nope")},
    )
    assert resp.status_code == 400


def test_relink_apply_rewrites_symlinks(tmp_path: Path) -> None:
    root, ids = _setup_project(tmp_path, ["a.mp4"])
    new_target = tmp_path / "share" / "subdir" / "a.mp4"
    new_target.parent.mkdir(parents=True)
    new_target.write_bytes(b"")
    client = TestClient(create_app(project_root=root, project_name="x"))
    resp = client.post(
        "/api/shooters/me/videos/relink/apply",
        json={"decisions": {ids["a.mp4"]: str(new_target)}},
    )
    assert resp.status_code == 200
    [applied] = resp.json()["applied"]
    assert applied["video_id"] == ids["a.mp4"]
    assert applied["new_target"] == str(new_target.resolve())
    # And the link on disk now resolves to the new target.
    link = root / "raw" / "a.mp4"
    assert link.resolve() == new_target.resolve()


def test_relink_apply_rejects_missing_target(tmp_path: Path) -> None:
    root, ids = _setup_project(tmp_path, ["a.mp4"])
    client = TestClient(create_app(project_root=root, project_name="x"))
    resp = client.post(
        "/api/shooters/me/videos/relink/apply",
        json={"decisions": {ids["a.mp4"]: str(tmp_path / "nope.mp4")}},
    )
    assert resp.status_code == 400


def test_relink_apply_rejects_unknown_video_id(tmp_path: Path) -> None:
    root, _ = _setup_project(tmp_path, ["a.mp4"])
    target = tmp_path / "originals" / "a.mp4"
    client = TestClient(create_app(project_root=root, project_name="x"))
    resp = client.post(
        "/api/shooters/me/videos/relink/apply",
        json={"decisions": {"deadbeefcafe": str(target)}},
    )
    assert resp.status_code == 400
