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


def _seed_project_with_primary(tmp_path: Path) -> tuple[TestClient, Path]:
    """Boot a server with one stage and a primary video assigned. Returns
    ``(client, video_source_path)`` so the caller can mutate the source if
    needed (e.g. to control mtime for video_match)."""
    project_root = tmp_path / "match"
    app = create_app(project_root=project_root, project_name="Beep Match")
    client = TestClient(app)
    sb = {
        "match": {"id": "1", "name": "Beep Match"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "Tester",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "Stage One",
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
    src = src_dir / "VID.mp4"
    src.write_bytes(b"")
    client.post(
        "/api/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": False},
    )
    client.post(
        "/api/assignments/move",
        json={"video_path": "raw/VID.mp4", "to_stage_number": 1, "role": "primary"},
    )
    return client, src


def _stub_detect(monkeypatch, beep_time: float = 12.453) -> None:
    """Replace audio_helpers.detect_primary_beep with a fast stub returning a
    deterministic BeepDetection. The endpoint is otherwise wrapped around
    ffmpeg + librosa, which we don't want to invoke from a unit test."""
    from splitsmith.config import BeepDetection
    from splitsmith.ui import audio as audio_helpers

    def fake(*args, **kwargs):  # type: ignore[no-untyped-def]
        return BeepDetection(time=beep_time, peak_amplitude=0.42, duration_ms=320.0)

    monkeypatch.setattr(audio_helpers, "detect_primary_beep", fake)


def test_detect_beep_persists_auto_result(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=12.453)

    resp = client.post("/api/stages/1/detect-beep")
    assert resp.status_code == 200
    body = resp.json()
    primary = body["stages"][0]["videos"][0]
    assert primary["beep_time"] == 12.453
    assert primary["beep_source"] == "auto"
    assert primary["beep_peak_amplitude"] == 0.42
    assert primary["beep_duration_ms"] == 320.0
    assert primary["processed"]["beep"] is True


def test_detect_beep_409_over_manual_unless_forced(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    # User has already set a manual override.
    client.post("/api/stages/1/beep", json={"beep_time": 12.5})
    _stub_detect(monkeypatch, beep_time=99.0)

    resp = client.post("/api/stages/1/detect-beep")
    assert resp.status_code == 409
    primary = client.get("/api/project").json()["stages"][0]["videos"][0]
    assert primary["beep_time"] == 12.5
    assert primary["beep_source"] == "manual"

    # ?force=true replaces it.
    resp = client.post("/api/stages/1/detect-beep?force=true")
    assert resp.status_code == 200
    primary = resp.json()["stages"][0]["videos"][0]
    assert primary["beep_time"] == 99.0
    assert primary["beep_source"] == "auto"


def test_detect_beep_400_when_no_primary(tmp_path: Path) -> None:
    project_root = tmp_path / "match"
    app = create_app(project_root=project_root, project_name="x")
    client = TestClient(app)
    sb = {
        "match": {"id": "1", "name": "x"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "T",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "Empty",
                        "time_seconds": 10.0,
                        "scorecard_updated_at": "2026-01-01T00:00:00+00:00",
                    }
                ],
            }
        ],
    }
    client.post("/api/scoreboard/import", json={"data": sb})
    resp = client.post("/api/stages/1/detect-beep")
    assert resp.status_code == 400


def test_override_beep_sets_manual_source(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.post("/api/stages/1/beep", json={"beep_time": 12.5})
    assert resp.status_code == 200
    primary = resp.json()["stages"][0]["videos"][0]
    assert primary["beep_time"] == 12.5
    assert primary["beep_source"] == "manual"
    assert primary["processed"]["beep"] is True


def test_override_beep_clears_with_null(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=10.0)
    client.post("/api/stages/1/detect-beep")

    resp = client.post("/api/stages/1/beep", json={"beep_time": None})
    assert resp.status_code == 200
    primary = resp.json()["stages"][0]["videos"][0]
    assert primary["beep_time"] is None
    assert primary["beep_source"] is None
    assert primary["processed"]["beep"] is False


def test_override_beep_400_on_negative(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.post("/api/stages/1/beep", json={"beep_time": -1.0})
    assert resp.status_code == 400


def test_audio_endpoint_serves_cached_wav(tmp_path: Path, monkeypatch) -> None:
    """The /audio endpoint asks the helper for the cached WAV; we stub the
    helper to drop a small WAV file in place rather than running ffmpeg."""
    client, _ = _seed_project_with_primary(tmp_path)

    project_root = tmp_path / "match"
    fake_wav = project_root / "audio" / "stage1_primary.wav"
    fake_wav.parent.mkdir(parents=True, exist_ok=True)
    # Minimal RIFF header + tiny data chunk; the test only checks that bytes flow.
    fake_wav.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00" + b"\x00" * 16)

    from splitsmith.ui import audio as audio_helpers

    def fake_ensure(root, n, source, **kwargs):  # type: ignore[no-untyped-def]
        return fake_wav

    monkeypatch.setattr(audio_helpers, "ensure_primary_audio", fake_ensure)

    resp = client.get("/api/stages/1/audio")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    assert resp.content.startswith(b"RIFF")


def test_scan_videos_with_explicit_source_paths(tmp_path: Path) -> None:
    """source_paths picks specific files (USB-cam workflow). Only the listed
    files are registered, even if other videos sit in the same directory."""
    project_root = tmp_path / "match"
    app = create_app(project_root=project_root, project_name="x")
    client = TestClient(app)
    sb = {
        "match": {"id": "1", "name": "x"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "T",
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
    client.post("/api/scoreboard/import", json={"data": sb})

    src_dir = tmp_path / "videos"
    src_dir.mkdir()
    keep1 = src_dir / "keep1.mp4"
    keep2 = src_dir / "keep2.mp4"
    skip = src_dir / "skip.mp4"
    for f in (keep1, keep2, skip):
        f.write_bytes(b"")

    resp = client.post(
        "/api/videos/scan",
        json={
            "source_paths": [str(keep1), str(keep2)],
            "auto_assign_primary": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert sorted(body["registered"]) == ["raw/keep1.mp4", "raw/keep2.mp4"]
    project = client.get("/api/project").json()
    assert len(project["unassigned_videos"]) == 2
    # last_scanned_dir set to the parent of the first picked file.
    assert project["last_scanned_dir"] == str(src_dir.resolve())


def test_scan_400_when_neither_source_dir_nor_paths(tmp_path: Path) -> None:
    app = create_app(project_root=tmp_path / "match", project_name="x")
    client = TestClient(app)
    resp = client.post("/api/videos/scan", json={"auto_assign_primary": False})
    assert resp.status_code == 400


def test_scan_400_when_both_source_dir_and_paths(tmp_path: Path) -> None:
    app = create_app(project_root=tmp_path / "match", project_name="x")
    client = TestClient(app)
    resp = client.post(
        "/api/videos/scan",
        json={"source_dir": str(tmp_path), "source_paths": [str(tmp_path / "a.mp4")]},
    )
    assert resp.status_code == 400


def test_settings_endpoint_persists_overrides(tmp_path: Path) -> None:
    app = create_app(project_root=tmp_path / "match", project_name="x")
    client = TestClient(app)
    resp = client.post(
        "/api/project/settings",
        json={
            "audio_dir": str(tmp_path / "scratch" / "audio"),
            "trimmed_dir": str(tmp_path / "scratch" / "trimmed"),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["audio_dir"] == str(tmp_path / "scratch" / "audio")
    assert body["trimmed_dir"] == str(tmp_path / "scratch" / "trimmed")
    # raw_dir / exports_dir untouched.
    assert body["raw_dir"] is None
    assert body["exports_dir"] is None
    # Directories were created.
    assert (tmp_path / "scratch" / "audio").is_dir()
    assert (tmp_path / "scratch" / "trimmed").is_dir()


def test_settings_empty_string_clears_override(tmp_path: Path) -> None:
    app = create_app(project_root=tmp_path / "match", project_name="x")
    client = TestClient(app)
    client.post(
        "/api/project/settings",
        json={"audio_dir": str(tmp_path / "audio-config")},
    )
    body = client.post("/api/project/settings", json={"audio_dir": ""}).json()
    assert body["audio_dir"] is None


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
