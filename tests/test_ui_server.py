"""Tests for the production UI's FastAPI backend."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from splitsmith.ui.project import MatchProject, StageEntry, StageVideo
from splitsmith.ui.server import create_app


def test_health_returns_project_info(tmp_path: Path) -> None:
    app = create_app(project_root=tmp_path / "match", project_name="Test Match")
    client = TestClient(app)

    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["project_name"] == "Test Match"
    assert body["schema_version"] == 1


def test_get_project_returns_full_dump(tmp_path: Path) -> None:
    root = tmp_path / "match"
    project = MatchProject.init(root, name="Get Project Match")
    project.competitor_name = "API Tester"
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=10.0,
            videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=5.0)],
        )
    ]
    project.save(root)

    app = create_app(project_root=root, project_name="ignored, project already exists")
    client = TestClient(app)

    resp = client.get("/api/project")
    assert resp.status_code == 200
    body = resp.json()
    # The project name from disk wins, not the create_app argument.
    assert body["name"] == "Get Project Match"
    assert body["competitor_name"] == "API Tester"
    assert len(body["stages"]) == 1
    assert body["stages"][0]["videos"][0]["role"] == "primary"


def test_spa_serves_index_html_when_built(tmp_path: Path) -> None:
    """When ui_static/dist is built (the post-`npm run build` state), `/`
    must serve the SPA's index.html so the browser bootstraps the React app.

    Skipped automatically if the dev hasn't built the SPA yet; CI builds it
    before running tests, so the post-build path is the one we care about
    asserting against."""
    from splitsmith.ui.server import STATIC_DIR

    if not (STATIC_DIR / "index.html").exists():
        import pytest as _pytest

        _pytest.skip("SPA not built; run `pnpm build` in src/splitsmith/ui_static/")

    app = create_app(project_root=tmp_path / "match", project_name="SPA Match")
    client = TestClient(app)

    # The /api/health route always works.
    assert client.get("/api/health").status_code == 200

    # SPA index for the root.
    resp = client.get("/")
    assert resp.status_code == 200
    assert b'<div id="root"></div>' in resp.content

    # SPA fallback for unknown client routes (so React Router can handle them).
    resp = client.get("/some/deep/route")
    assert resp.status_code == 200
    assert b'<div id="root"></div>' in resp.content

    # API routes still return 404 from the fallback rather than the SPA.
    resp = client.get("/api/does-not-exist")
    assert resp.status_code == 404


def test_import_scoreboard_endpoint(tmp_path: Path) -> None:
    app = create_app(project_root=tmp_path / "match", project_name="x")
    client = TestClient(app)

    sb = {
        "match": {"id": "27046", "name": "Imported Match"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "Tester",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "S1",
                        "time_seconds": 10.0,
                        "scorecard_updated_at": "2026-01-01T00:00:00+00:00",
                    }
                ],
            }
        ],
    }
    resp = client.post("/api/scoreboard/import", json={"data": sb})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Imported Match"
    assert body["scoreboard_match_id"] == "27046"
    assert body["competitor_name"] == "Tester"
    assert len(body["stages"]) == 1


def test_import_scoreboard_409_on_conflict(tmp_path: Path) -> None:
    app = create_app(project_root=tmp_path / "match", project_name="x")
    client = TestClient(app)

    sb = {
        "match": {"id": "1", "name": "First"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "Tester",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "S",
                        "time_seconds": 10.0,
                        "scorecard_updated_at": "2026-01-01T00:00:00+00:00",
                    }
                ],
            }
        ],
    }
    assert client.post("/api/scoreboard/import", json={"data": sb}).status_code == 200
    resp = client.post("/api/scoreboard/import", json={"data": sb})
    assert resp.status_code == 409

    # overwrite=True succeeds.
    resp = client.post("/api/scoreboard/import", json={"data": sb, "overwrite": True})
    assert resp.status_code == 200


def test_scan_videos_registers_and_auto_assigns(tmp_path: Path) -> None:
    """End-to-end test for the scan endpoint: create a stage, drop a video into a
    source folder with a matching mtime, scan, expect auto-assignment as primary."""
    import os as _os
    from datetime import datetime

    project_root = tmp_path / "match"
    app = create_app(project_root=project_root, project_name="Scan Match")
    client = TestClient(app)

    scorecard_iso = "2026-04-12T11:30:00+00:00"
    scorecard_dt = datetime.fromisoformat(scorecard_iso)
    sb = {
        "match": {"id": "1", "name": "Scan Match Loaded"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "Tester",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "First",
                        "time_seconds": 12.0,
                        "scorecard_updated_at": scorecard_iso,
                    }
                ],
            }
        ],
    }
    client.post("/api/scoreboard/import", json={"data": sb})

    src_dir = tmp_path / "videos"
    src_dir.mkdir()
    vid = src_dir / "VID.mp4"
    vid.write_bytes(b"")
    target_ts = scorecard_dt.timestamp() - 60  # 1 minute before scorecard
    _os.utime(vid, (target_ts, target_ts))

    resp = client.post(
        "/api/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["registered"] == ["raw/VID.mp4"]
    assert body["auto_assigned"] == {"1": "raw/VID.mp4"}

    # Verify project state.
    project = client.get("/api/project").json()
    assert project["stages"][0]["videos"][0]["role"] == "primary"
    assert project["unassigned_videos"] == []


def test_scan_videos_400_on_missing_dir(tmp_path: Path) -> None:
    app = create_app(project_root=tmp_path / "match", project_name="x")
    client = TestClient(app)
    resp = client.post("/api/videos/scan", json={"source_dir": str(tmp_path / "does-not-exist")})
    assert resp.status_code == 400


def test_move_assignment_endpoint(tmp_path: Path) -> None:
    """Set role to ignored, verify, then move back to a stage."""
    project_root = tmp_path / "match"
    app = create_app(project_root=project_root, project_name="Move Match")
    client = TestClient(app)

    # Set up: 1 stage, 1 video assigned as primary.
    sb = {
        "match": {"id": "1", "name": "x"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "Tester",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "First",
                        "time_seconds": 10.0,
                        "scorecard_updated_at": "2026-01-01T00:00:00+00:00",
                    }
                ],
            }
        ],
    }
    client.post("/api/scoreboard/import", json={"data": sb})

    src_dir = tmp_path / "videos"
    src_dir.mkdir()
    vid = src_dir / "VID.mp4"
    vid.write_bytes(b"")
    client.post(
        "/api/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": False},
    )

    # Should have one unassigned video.
    project = client.get("/api/project").json()
    assert len(project["unassigned_videos"]) == 1

    # Move to stage 1 as primary.
    resp = client.post(
        "/api/assignments/move",
        json={
            "video_path": "raw/VID.mp4",
            "to_stage_number": 1,
            "role": "primary",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["unassigned_videos"] == []
    assert body["stages"][0]["videos"][0]["role"] == "primary"

    # Mark as ignored (still on stage 1).
    resp = client.post(
        "/api/assignments/move",
        json={
            "video_path": "raw/VID.mp4",
            "to_stage_number": 1,
            "role": "ignored",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["stages"][0]["videos"][0]["role"] == "ignored"

    # Back to unassigned.
    resp = client.post(
        "/api/assignments/move",
        json={"video_path": "raw/VID.mp4", "to_stage_number": None},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["stages"][0]["videos"] == []
    assert len(body["unassigned_videos"]) == 1


def test_move_assignment_404_on_unknown_video(tmp_path: Path) -> None:
    app = create_app(project_root=tmp_path / "match", project_name="x")
    client = TestClient(app)
    resp = client.post(
        "/api/assignments/move",
        json={"video_path": "raw/nope.mp4", "to_stage_number": None},
    )
    assert resp.status_code == 404


def test_fs_list_returns_directory_entries(tmp_path: Path) -> None:
    app = create_app(project_root=tmp_path / "match", project_name="x")
    client = TestClient(app)

    listing_root = tmp_path / "browseable"
    listing_root.mkdir()
    (listing_root / "subdir-a").mkdir()
    (listing_root / "subdir-b").mkdir()
    (listing_root / "VID_1.mp4").write_bytes(b"")
    (listing_root / "VID_2.mov").write_bytes(b"")
    (listing_root / "notes.txt").write_text("hi")
    (listing_root / ".hidden").mkdir()  # filtered out

    resp = client.get("/api/fs/list", params={"path": str(listing_root)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == str(listing_root.resolve())
    assert body["parent"] == str(listing_root.parent.resolve())
    names = {e["name"]: e for e in body["entries"]}
    assert ".hidden" not in names
    assert names["subdir-a"]["kind"] == "dir"
    assert names["VID_1.mp4"]["kind"] == "video"
    assert names["notes.txt"]["kind"] == "file"
    # Directories sort first.
    kinds = [e["kind"] for e in body["entries"]]
    assert kinds.index("dir") < kinds.index("video")


def test_fs_list_video_count_for_directories(tmp_path: Path) -> None:
    app = create_app(project_root=tmp_path / "match", project_name="x")
    client = TestClient(app)

    parent = tmp_path / "p"
    inner = parent / "match-day"
    inner.mkdir(parents=True)
    (inner / "v1.mp4").write_bytes(b"")
    (inner / "v2.mov").write_bytes(b"")
    (inner / "notes.txt").write_text("hi")

    resp = client.get("/api/fs/list", params={"path": str(parent)})
    assert resp.status_code == 200
    entries = {e["name"]: e for e in resp.json()["entries"]}
    assert entries["match-day"]["kind"] == "dir"
    assert entries["match-day"]["video_count"] == 2


def test_fs_list_default_path_uses_last_scanned_dir(tmp_path: Path) -> None:
    """Saving last_scanned_dir on the project should change the default path."""
    project_root = tmp_path / "match"
    app = create_app(project_root=project_root, project_name="x")
    client = TestClient(app)

    seeded = tmp_path / "videos"
    seeded.mkdir()
    project = MatchProject.load(project_root)
    project.last_scanned_dir = str(seeded.resolve())
    project.save(project_root)

    resp = client.get("/api/fs/list")
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == str(seeded.resolve())
    # The seeded dir is suggested first in bookmarks.
    assert body["suggested_starts"][0] == str(seeded.resolve())


def test_fs_list_404_on_missing_path(tmp_path: Path) -> None:
    app = create_app(project_root=tmp_path / "match", project_name="x")
    client = TestClient(app)
    resp = client.get("/api/fs/list", params={"path": str(tmp_path / "nope")})
    assert resp.status_code == 404


def test_scan_persists_last_scanned_dir(tmp_path: Path) -> None:
    project_root = tmp_path / "match"
    app = create_app(project_root=project_root, project_name="x")
    client = TestClient(app)

    src_dir = tmp_path / "videos"
    src_dir.mkdir()
    (src_dir / "VID.mp4").write_bytes(b"")

    resp = client.post(
        "/api/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": False},
    )
    assert resp.status_code == 200

    project = client.get("/api/project").json()
    assert project["last_scanned_dir"] == str(src_dir.resolve())


def test_external_edit_visible_without_restart(tmp_path: Path) -> None:
    """External edits to project.json must appear on the next request -- the
    server doesn't cache the model in memory."""
    root = tmp_path / "match"
    app = create_app(project_root=root, project_name="External Edit Match")
    client = TestClient(app)

    assert client.get("/api/project").json()["competitor_name"] is None

    project = MatchProject.load(root)
    project.competitor_name = "Edited Externally"
    project.save(root)

    assert client.get("/api/project").json()["competitor_name"] == "Edited Externally"
