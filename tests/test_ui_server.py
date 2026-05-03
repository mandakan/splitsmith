"""Tests for the production UI's FastAPI backend."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from splitsmith.thumbnail import ThumbnailError
from splitsmith.ui.project import MatchProject, StageEntry, StageVideo
from splitsmith.ui.server import create_app
from splitsmith.video_probe import ProbeError, ProbeResult


def _wait_for_job(client: TestClient, job_id: str, *, timeout: float = 5.0) -> dict:
    """Poll /api/jobs/{id} until the job is no longer running.

    Returns the final job snapshot. Raises ``AssertionError`` if the job
    doesn't finish in time -- ample margin since unit tests stub ffmpeg
    and beep_detect.
    """
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] in ("succeeded", "failed", "cancelled"):
            return body
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


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


def test_placeholder_stages_endpoint_creates_stages(tmp_path: Path) -> None:
    app = create_app(project_root=tmp_path / "match", project_name="x")
    client = TestClient(app)
    resp = client.post(
        "/api/project/placeholder-stages",
        json={"stage_count": 5, "match_name": "Test Match", "match_date": "2026-04-12"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Test Match"
    assert body["match_date"] == "2026-04-12"
    assert len(body["stages"]) == 5
    assert all(s["placeholder"] for s in body["stages"])


def test_placeholder_stages_endpoint_409_when_real_stages_exist(tmp_path: Path) -> None:
    app = create_app(project_root=tmp_path / "match", project_name="x")
    client = TestClient(app)
    # Real scoreboard import first.
    sb = {
        "match": {"id": "1", "name": "Real"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "A",
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
    client.post("/api/scoreboard/import", json={"data": sb})
    resp = client.post(
        "/api/project/placeholder-stages",
        json={"stage_count": 5},
    )
    assert resp.status_code == 409


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


def _stub_detect(
    monkeypatch,
    beep_time: float = 12.453,
    *,
    candidates: list | None = None,
    gate: threading.Event | None = None,
) -> None:
    """Replace the per-video beep detector with a fast stub returning a
    deterministic BeepDetection. The endpoint is otherwise wrapped around
    ffmpeg + librosa, which we don't want to invoke from a unit test.
    Both the legacy ``detect_primary_beep`` shim and the new
    ``detect_video_beep`` are patched so callers on either path get the
    stubbed result.

    ``gate`` lets a test hold the fake detector until a barrier is
    released, simulating the real 2-10s detect-beep window so dedup tests
    aren't a race against a synchronous return.
    """
    from splitsmith.config import BeepCandidate, BeepDetection
    from splitsmith.ui import audio as audio_helpers

    def fake(*args, **kwargs):  # type: ignore[no-untyped-def]
        if gate is not None:
            assert gate.wait(timeout=5.0), "_stub_detect gate never released"
        cands: list[BeepCandidate] = candidates or []
        return BeepDetection(
            time=beep_time,
            peak_amplitude=0.42,
            duration_ms=320.0,
            candidates=cands,
        )

    monkeypatch.setattr(audio_helpers, "detect_primary_beep", fake)
    monkeypatch.setattr(audio_helpers, "detect_video_beep", fake)


def test_detect_beep_persists_auto_result(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=12.453)

    resp = client.post("/api/stages/1/detect-beep")
    assert resp.status_code == 200
    job = resp.json()
    assert job["kind"] == "detect_beep"
    final = _wait_for_job(client, job["id"])
    assert final["status"] == "succeeded", final
    primary = client.get("/api/project").json()["stages"][0]["videos"][0]
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
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final
    primary = client.get("/api/project").json()["stages"][0]["videos"][0]
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


def _three_candidates() -> list:
    from splitsmith.config import BeepCandidate

    return [
        BeepCandidate(time=12.453, score=18.0, peak_amplitude=0.42, duration_ms=320.0),
        BeepCandidate(time=8.100, score=9.0, peak_amplitude=0.30, duration_ms=180.0),
        BeepCandidate(time=27.500, score=4.5, peak_amplitude=0.55, duration_ms=210.0),
    ]


def test_detect_beep_persists_candidate_list(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=12.453, candidates=_three_candidates())

    resp = client.post("/api/stages/1/detect-beep")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final
    primary = client.get("/api/project").json()["stages"][0]["videos"][0]
    assert len(primary["beep_candidates"]) == 3
    assert primary["beep_candidates"][0]["time"] == 12.453
    assert primary["beep_candidates"][1]["time"] == 8.100


def test_select_beep_candidate_promotes_alternate(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=12.453, candidates=_three_candidates())
    final = _wait_for_job(client, client.post("/api/stages/1/detect-beep").json()["id"])
    assert final["status"] == "succeeded"

    resp = client.post("/api/stages/1/beep/select", json={"time": 8.100})
    assert resp.status_code == 200
    primary = resp.json()["stages"][0]["videos"][0]
    assert primary["beep_time"] == 8.100
    assert primary["beep_source"] == "auto"
    assert primary["beep_peak_amplitude"] == 0.30
    assert primary["beep_duration_ms"] == 180.0
    # Selecting an alternate keeps the candidate list intact so the user
    # can switch again without re-running detection.
    assert len(primary["beep_candidates"]) == 3
    # New beep -> previous trim is stale.
    assert primary["processed"]["trim"] is False


def test_select_beep_candidate_tolerates_sub_ms_drift(tmp_path: Path, monkeypatch) -> None:
    """The SPA may send a slightly-rounded time (3 decimals) while the
    server still has the full-precision candidate. The 1 ms tolerance
    keeps the click working."""
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=12.453, candidates=_three_candidates())
    _wait_for_job(client, client.post("/api/stages/1/detect-beep").json()["id"])

    resp = client.post("/api/stages/1/beep/select", json={"time": 8.1004})
    assert resp.status_code == 200
    primary = resp.json()["stages"][0]["videos"][0]
    assert primary["beep_time"] == 8.100


def test_select_beep_candidate_400_when_no_candidates(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.post("/api/stages/1/beep/select", json={"time": 0.0})
    assert resp.status_code == 400


def test_select_beep_candidate_400_when_time_no_match(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=12.453, candidates=_three_candidates())
    _wait_for_job(client, client.post("/api/stages/1/detect-beep").json()["id"])

    resp = client.post("/api/stages/1/beep/select", json={"time": 99.999})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    # The server lists what's available so the user can copy a real time.
    assert "12.453" in detail
    assert "8.100" in detail


def test_manual_override_clears_candidate_list(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=12.453, candidates=_three_candidates())
    _wait_for_job(client, client.post("/api/stages/1/detect-beep").json()["id"])

    resp = client.post("/api/stages/1/beep", json={"beep_time": 12.5})
    assert resp.status_code == 200
    primary = resp.json()["stages"][0]["videos"][0]
    assert primary["beep_source"] == "manual"
    assert primary["beep_candidates"] == []


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


def test_beep_preview_serves_cached_clip(tmp_path: Path, monkeypatch) -> None:
    """When a primary has beep_time set, the endpoint asks ensure_clip for a
    short MP4 around the beep and serves it as video/mp4. We stub the helper
    so the test doesn't need real ffmpeg."""
    client, src = _seed_project_with_primary(tmp_path)

    # Set beep_time on the primary so the endpoint has something to anchor on.
    project_root = tmp_path / "match"
    project = MatchProject.load(project_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 12.5
    project.save(project_root)

    # Drop a fake clip in the thumbs cache and stub ensure_clip to return it.
    thumbs_dir = project_root / "thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    fake_clip = thumbs_dir / "fake_t12500_d1000.mp4"
    fake_clip.write_bytes(b"\x00\x00\x00\x18ftypmp42fake-mp4")

    captured: dict = {}

    def fake_ensure_clip(source, *, cache_dir, center_time, duration_s=1.0, **kwargs):  # type: ignore[no-untyped-def]
        captured["source"] = source
        captured["center_time"] = center_time
        captured["duration_s"] = duration_s
        return fake_clip

    from splitsmith.ui import server as server_module

    monkeypatch.setattr(server_module.thumbnail_helpers, "ensure_clip", fake_ensure_clip)

    resp = client.get("/api/stages/1/beep-preview")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.content.startswith(b"\x00\x00\x00\x18ftyp")
    assert captured["center_time"] == pytest.approx(12.5)
    assert captured["duration_s"] == pytest.approx(1.0)
    assert Path(captured["source"]).name == src.name


def test_beep_preview_t_query_overrides_center(tmp_path: Path, monkeypatch) -> None:
    """``?t=<seconds>`` re-centres the preview clip on an arbitrary time
    (used by the candidate picker before the user has committed a choice).
    """
    client, src = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    project = MatchProject.load(project_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 12.5
    project.save(project_root)

    thumbs_dir = project_root / "thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    fake_clip = thumbs_dir / "fake_t27500_d1000.mp4"
    fake_clip.write_bytes(b"\x00\x00\x00\x18ftypmp42fake-mp4")

    captured: dict = {}

    def fake_ensure_clip(source, *, cache_dir, center_time, duration_s=1.0, **kwargs):  # type: ignore[no-untyped-def]
        captured["center_time"] = center_time
        return fake_clip

    from splitsmith.ui import server as server_module

    monkeypatch.setattr(server_module.thumbnail_helpers, "ensure_clip", fake_ensure_clip)

    resp = client.get("/api/stages/1/beep-preview?t=27.5")
    assert resp.status_code == 200
    assert captured["center_time"] == pytest.approx(27.5)
    # primary.beep_time is unchanged.
    after = client.get("/api/project").json()["stages"][0]["videos"][0]
    assert after["beep_time"] == 12.5
    # ``src`` is the seeded source video; sanity-check it exists.
    assert src.exists()


def test_beep_preview_400_on_negative_t(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    project = MatchProject.load(project_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.save(project_root)

    resp = client.get("/api/stages/1/beep-preview?t=-1")
    assert resp.status_code == 400


def test_beep_preview_404_when_no_beep(tmp_path: Path) -> None:
    """No beep_time yet -> 404 with a clear detail. The SPA hides the preview
    until detection has run."""
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.get("/api/stages/1/beep-preview")
    assert resp.status_code == 404
    assert "beep_time" in resp.json()["detail"]


def test_beep_preview_404_when_no_primary(tmp_path: Path) -> None:
    """A stage without a primary has no source video to clip from."""
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
    resp = client.get("/api/stages/1/beep-preview")
    assert resp.status_code == 404


def test_beep_preview_500_on_ffmpeg_failure(tmp_path: Path, monkeypatch) -> None:
    """ensure_clip raises on missing ffmpeg / encode failure -- surface as
    500 so the SPA can fall back to the 'preview unavailable' hint."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    project = MatchProject.load(project_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.save(project_root)

    from splitsmith import thumbnail
    from splitsmith.ui import server as server_module

    def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise thumbnail.ThumbnailError("ffmpeg exploded")

    monkeypatch.setattr(server_module.thumbnail_helpers, "ensure_clip", boom)
    resp = client.get("/api/stages/1/beep-preview")
    assert resp.status_code == 500
    assert "ffmpeg exploded" in resp.json()["detail"]


def test_peaks_endpoint_uses_trimmed_audio_when_present(tmp_path: Path, monkeypatch) -> None:
    """When stage<N>_trimmed.mp4 exists the peaks come from its WAV; the
    response carries the clip-local beep_time and trimmed=true so the SPA
    can render the beep marker correctly."""
    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"

    # Stage primary already has beep_time set by the seeder. Force-set to a
    # known value so we can assert the clip-local beep math.
    project = MatchProject.load(project_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 30.0  # source-time beep; trim default buffer is 5s
    project.save(project_root)

    # Create a fake "trimmed" mp4 placeholder so the existence check passes.
    trimmed_dir = project_root / "trimmed"
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    (trimmed_dir / "stage1_trimmed.mp4").write_bytes(b"\x00fake_mp4")

    # Stub _extract_audio so we don't actually shell out; drop a known WAV
    # at the audit-cache path.
    audio_dir = project_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audit_wav = audio_dir / "stage1_audit.wav"
    audio = np.zeros(48_000, dtype="float32")
    audio[20_000:21_000] = 0.7
    sf.write(audit_wav, audio, 48_000)

    from splitsmith.ui import audio as audio_helpers

    def fake_extract(source, dest, sample_rate, ffmpeg_binary):  # type: ignore[no-untyped-def]
        # Already wrote audit_wav above; pretend ffmpeg ran.
        return None

    monkeypatch.setattr(audio_helpers, "_extract_audio", fake_extract)

    resp = client.get("/api/stages/1/peaks?bins=64")
    assert resp.status_code == 200
    body = resp.json()
    assert body["trimmed"] is True
    # beep_time at 30s in source -> clamped to buffer (5.0s) inside clip.
    assert body["beep_time"] == pytest.approx(5.0)
    assert len(body["peaks"]) == 64
    assert body["duration"] == pytest.approx(1.0)


def test_peaks_endpoint_falls_back_to_full_when_no_trim(tmp_path: Path, monkeypatch) -> None:
    """No trimmed file -> /peaks serves the full primary WAV. Response says
    trimmed=false so the SPA can hint that the user is on an untrimmed clip."""
    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    audio_dir = project_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav = audio_dir / "stage1_primary.wav"
    audio = np.zeros(24_000, dtype="float32")
    audio[5_000:6_000] = 0.3
    sf.write(wav, audio, 48_000)

    from splitsmith.ui import audio as audio_helpers

    def fake_ensure(root, n, source, **kwargs):  # type: ignore[no-untyped-def]
        return wav

    monkeypatch.setattr(audio_helpers, "ensure_primary_audio", fake_ensure)

    resp = client.get("/api/stages/1/peaks?bins=64")
    assert resp.status_code == 200
    body = resp.json()
    assert body["trimmed"] is False


def test_stream_video_serves_trimmed_for_primary(tmp_path: Path) -> None:
    """When the trimmed MP4 exists for a stage, /stream returns its bytes
    rather than the source. This is what gives the audit screen its
    short-GOP scrub-friendly playback."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    project = MatchProject.load(project_root)
    primary = project.stages[0].primary()
    assert primary is not None
    resolved = project.resolve_video_path(project_root, primary.path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_bytes(b"SOURCE_MP4")

    trimmed_dir = project_root / "trimmed"
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    (trimmed_dir / "stage1_trimmed.mp4").write_bytes(b"TRIMMED_MP4")

    resp = client.get(f"/api/videos/stream?path={primary.path}")
    assert resp.status_code == 200
    assert resp.content == b"TRIMMED_MP4"


def test_peaks_endpoint_returns_normalized_bins(tmp_path: Path, monkeypatch) -> None:
    """/peaks asks the audio helper for the cached WAV, then computes peaks.

    We stub ``ensure_primary_audio`` to return a real WAV written by
    ``soundfile`` so the peaks pipeline runs end-to-end without ffmpeg.
    """
    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    audio_dir = project_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav = audio_dir / "stage1_primary.wav"
    audio = np.zeros(48_000, dtype="float32")
    audio[10_000:11_000] = 0.5
    sf.write(wav, audio, 48_000)

    from splitsmith.ui import audio as audio_helpers

    def fake_ensure(root, n, source, **kwargs):  # type: ignore[no-untyped-def]
        return wav

    monkeypatch.setattr(audio_helpers, "ensure_primary_audio", fake_ensure)

    resp = client.get("/api/stages/1/peaks?bins=64")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bins"] == 64
    assert len(body["peaks"]) == 64
    assert all(0.0 <= p <= 1.0 for p in body["peaks"])
    assert body["duration"] == pytest.approx(1.0)


def test_peaks_endpoint_404_when_no_primary(tmp_path: Path) -> None:
    """A stage without a primary video can't have peaks; surface 404 cleanly."""
    project_root = tmp_path / "match"
    app = create_app(project_root=project_root, project_name="x")
    client = TestClient(app)
    project = MatchProject.load(project_root)
    project.init_placeholder_stages(2)
    project.save(project_root)

    resp = client.get("/api/stages/1/peaks")
    assert resp.status_code == 404
    assert "no primary" in resp.json()["detail"]


def test_detect_beep_auto_trims(tmp_path: Path, monkeypatch) -> None:
    """After a successful beep detect, the production UI runs the audit-mode
    trim inline so the audit screen lands with frame-accurate scrubbing."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    project = MatchProject.load(project_root)
    primary = project.stages[0].primary()
    assert primary is not None
    project.stages[0].time_seconds = 12.0
    project.save(project_root)

    resolved = project.resolve_video_path(project_root, primary.path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_bytes(b"FAKE_SOURCE_MP4")

    from splitsmith import beep_detect, trim
    from splitsmith.ui import audio as audio_helpers

    class FakeBeep:
        time = 6.5
        peak_amplitude = 0.42
        duration_ms = 110.0
        candidates: list = []

    monkeypatch.setattr(audio_helpers, "ensure_primary_audio", lambda *a, **kw: tmp_path / "x.wav")
    (tmp_path / "x.wav").write_bytes(b"\x00")
    monkeypatch.setattr(beep_detect, "load_audio", lambda p: ([0.0] * 100, 48_000))
    monkeypatch.setattr(beep_detect, "detect_beep", lambda *a, **kw: FakeBeep())

    trim_calls: list[dict] = []

    def fake_trim_video(**kwargs):  # type: ignore[no-untyped-def]
        trim_calls.append(kwargs)
        Path(kwargs["output_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["output_path"]).write_bytes(b"TRIMMED_MP4")
        return trim.TrimResult(output_path=kwargs["output_path"], start_time=1.5, end_time=23.5)

    monkeypatch.setattr(trim, "trim_video", fake_trim_video)

    resp = client.post("/api/stages/1/detect-beep")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final
    project_after = client.get("/api/project").json()
    assert project_after["stages"][0]["videos"][0]["beep_time"] == pytest.approx(6.5)
    assert project_after["stages"][0]["videos"][0]["processed"]["trim"] is True
    assert (project_root / "trimmed" / "stage1_trimmed.mp4").exists()
    assert len(trim_calls) == 1
    assert trim_calls[0]["mode"] == "audit"
    assert trim_calls[0]["beep_time"] == pytest.approx(6.5)
    assert trim_calls[0]["stage_time"] == pytest.approx(12.0)


def test_detect_beep_skips_trim_when_stage_time_zero(tmp_path: Path, monkeypatch) -> None:
    """Source-first / placeholder stages with time_seconds=0 (no scoreboard
    yet) should still let the user detect a beep -- trim just gets deferred
    until a stage time is known."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    project = MatchProject.load(project_root)
    project.stages[0].time_seconds = 0.0
    project.save(project_root)

    primary = project.stages[0].primary()
    assert primary is not None
    project.resolve_video_path(project_root, primary.path).resolve().write_bytes(b"X")

    from splitsmith import beep_detect, trim
    from splitsmith.ui import audio as audio_helpers

    class FakeBeep:
        time = 4.0
        peak_amplitude = 0.5
        duration_ms = 100.0
        candidates: list = []

    monkeypatch.setattr(audio_helpers, "ensure_primary_audio", lambda *a, **kw: tmp_path / "y.wav")
    (tmp_path / "y.wav").write_bytes(b"\x00")
    monkeypatch.setattr(beep_detect, "load_audio", lambda p: ([0.0] * 100, 48_000))
    monkeypatch.setattr(beep_detect, "detect_beep", lambda *a, **kw: FakeBeep())

    called = []
    monkeypatch.setattr(trim, "trim_video", lambda **kw: called.append(kw))

    resp = client.post("/api/stages/1/detect-beep")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final
    project_after = client.get("/api/project").json()
    assert project_after["stages"][0]["videos"][0]["beep_time"] == pytest.approx(4.0)
    assert project_after["stages"][0]["videos"][0]["processed"]["trim"] is False
    assert called == []


def test_post_trim_endpoint_produces_clip(tmp_path: Path, monkeypatch) -> None:
    """Manual /trim endpoint runs audit-mode trim and flips processed.trim."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    project = MatchProject.load(project_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(project_root)
    project.resolve_video_path(project_root, primary.path).resolve().write_bytes(b"S")

    from splitsmith import trim

    def fake_trim_video(**kwargs):  # type: ignore[no-untyped-def]
        Path(kwargs["output_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["output_path"]).write_bytes(b"TRIMMED")
        return trim.TrimResult(output_path=kwargs["output_path"], start_time=0.0, end_time=20.0)

    monkeypatch.setattr(trim, "trim_video", fake_trim_video)

    resp = client.post("/api/stages/1/trim")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final
    project_after = client.get("/api/project").json()
    assert project_after["stages"][0]["videos"][0]["processed"]["trim"] is True
    assert (project_root / "trimmed" / "stage1_trimmed.mp4").exists()


def test_trim_invalidates_when_beep_changes(tmp_path: Path, monkeypatch) -> None:
    """Changing beep_time without touching the source must re-encode the
    trim. Cache key includes a sidecar params JSON so a new beep is
    detected even when the source mtime is unchanged."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    project = MatchProject.load(project_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(project_root)
    project.resolve_video_path(project_root, primary.path).resolve().write_bytes(b"S")

    from splitsmith import trim

    invocations: list[dict] = []

    def fake_trim_video(**kwargs):  # type: ignore[no-untyped-def]
        invocations.append(kwargs)
        out = Path(kwargs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"T")
        return trim.TrimResult(output_path=out, start_time=0.0, end_time=15.0)

    monkeypatch.setattr(trim, "trim_video", fake_trim_video)

    # Run #1: cold cache, ffmpeg fires.
    resp = client.post("/api/stages/1/trim")
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded"
    assert len(invocations) == 1

    # Run #2: same params -> cache hit, ffmpeg does NOT fire again.
    resp = client.post("/api/stages/1/trim")
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded"
    assert len(invocations) == 1, "second run should be a cache hit"

    # Change beep_time via manual override. override_beep clears the trim
    # cache and auto-fires a re-trim job; we wait for it to finish.
    resp = client.post("/api/stages/1/beep", json={"beep_time": 7.5})
    assert resp.status_code == 200
    # The auto-fired job is the active trim job.
    jobs = client.get("/api/jobs").json()
    active = next((j for j in jobs if j["kind"] == "trim" and j["stage_number"] == 1), None)
    assert active is not None, "override_beep should auto-fire a trim job"
    final = _wait_for_job(client, active["id"])
    assert final["status"] == "succeeded"
    assert len(invocations) == 2, "new beep_time must invalidate the trim cache"
    assert invocations[1]["beep_time"] == pytest.approx(7.5)


def test_trim_partial_filename_keeps_mp4_extension(tmp_path: Path, monkeypatch) -> None:
    """ffmpeg infers the muxer from the output extension. The atomic-write
    partial must stay an .mp4 (e.g. stage1_trimmed.partial.mp4) -- if it
    becomes stage1_trimmed.mp4.partial ffmpeg fails with "Unable to choose
    an output format". Regression test for that bug."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    project = MatchProject.load(project_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(project_root)
    project.resolve_video_path(project_root, primary.path).resolve().write_bytes(b"S")

    from splitsmith import trim

    captured: list[Path] = []

    def fake_trim_video(**kwargs):  # type: ignore[no-untyped-def]
        out = Path(kwargs["output_path"])
        captured.append(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"T")
        return trim.TrimResult(output_path=out, start_time=0.0, end_time=15.0)

    monkeypatch.setattr(trim, "trim_video", fake_trim_video)

    resp = client.post("/api/stages/1/trim")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final
    assert captured, "trim_video should have been invoked"
    assert captured[0].suffix == ".mp4", (
        f"partial path must keep .mp4 so ffmpeg infers the muxer; " f"got {captured[0].name}"
    )
    assert ".partial" in captured[0].stem


def test_trim_endpoint_returns_existing_job_when_one_is_running(
    tmp_path: Path, monkeypatch
) -> None:
    """A second submit while the first is still running adopts the existing
    job instead of spawning a parallel ffmpeg that races on the partial
    file. Symptom we're guarding against: clicking "Trim now" twice (or
    after a reload) shouldn't double-submit."""
    import threading

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    project = MatchProject.load(project_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(project_root)
    project.resolve_video_path(project_root, primary.path).resolve().write_bytes(b"S")

    from splitsmith import trim

    proceed = threading.Event()
    invocations = []

    def slow_trim(**kwargs):  # type: ignore[no-untyped-def]
        invocations.append(kwargs)
        proceed.wait(timeout=2.0)
        out = Path(kwargs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"T")
        return trim.TrimResult(output_path=out, start_time=0.0, end_time=15.0)

    monkeypatch.setattr(trim, "trim_video", slow_trim)

    first = client.post("/api/stages/1/trim").json()
    second = client.post("/api/stages/1/trim").json()
    assert first["id"] == second["id"], "second submit should return the same job"
    proceed.set()
    final = _wait_for_job(client, first["id"])
    assert final["status"] == "succeeded"
    assert len(invocations) == 1, "ffmpeg must run only once"


def test_cancel_endpoint_aborts_running_trim(tmp_path: Path, monkeypatch) -> None:
    """POST /api/jobs/{id}/cancel cooperatively cancels a running trim
    so the user can bail out of a slow encode without waiting for it to
    finish (issue #26)."""
    import threading

    from splitsmith.ui.jobs import JobCancelled

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    project = MatchProject.load(project_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(project_root)
    project.resolve_video_path(project_root, primary.path).resolve().write_bytes(b"S")

    from splitsmith import trim

    started = threading.Event()
    cancel_observed = threading.Event()

    def slow_trim(**kwargs):  # type: ignore[no-untyped-def]
        # The server passes a runner that bridges to the JobHandle. We
        # call it with a short fake command to register the "subprocess",
        # then wait for the registry to terminate it.
        runner = kwargs["runner"]
        # ``true`` exits 0; we want a process that blocks until cancelled.
        # ``sleep 30`` is portable and gets killed by the registry's
        # terminate() when /cancel arrives.
        started.set()
        try:
            runner(["sleep", "30"], check=True, capture_output=True, text=True)
        except JobCancelled:
            cancel_observed.set()
            raise
        out = Path(kwargs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"T")
        return trim.TrimResult(output_path=out, start_time=0.0, end_time=15.0)

    monkeypatch.setattr(trim, "trim_video", slow_trim)

    job = client.post("/api/stages/1/trim").json()
    assert started.wait(timeout=3.0), "worker should have started by now"

    cancel_resp = client.post(f"/api/jobs/{job['id']}/cancel")
    assert cancel_resp.status_code == 200
    snapshot = cancel_resp.json()
    assert snapshot["cancel_requested"] is True

    final = _wait_for_job(client, job["id"])
    assert final["status"] == "cancelled", final
    assert cancel_observed.is_set(), "worker must observe the cancel via JobCancelled"


def test_cancel_endpoint_returns_404_for_unknown_job(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.post("/api/jobs/does-not-exist/cancel")
    assert resp.status_code == 404


def _fake_ensemble_result(candidates: list[dict], consensus: int = 3):
    """Build an ``EnsembleResult`` from terse candidate dicts for tests."""
    from splitsmith.ensemble import EnsembleCandidate, EnsembleResult

    cands = [
        EnsembleCandidate(
            candidate_number=i + 1,
            time=c["time"],
            ms_after_beep=c.get("ms_after_beep", round((c["time"] - 5.0) * 1000)),
            peak_amplitude=c.get("peak_amplitude", 0.5),
            confidence=c.get("confidence", 0.8),
            vote_a=c.get("vote_a", 1),
            vote_b=c.get("vote_b", 1),
            vote_c=c.get("vote_c", 1),
            vote_d=c.get("vote_d", 1),
            vote_total=c.get("vote_total", 4),
            apriori_boost=c.get("apriori_boost", 0.0),
            ensemble_score=c.get("ensemble_score", 4.0),
            score_c=c.get("score_c", 0.9),
            clap_diff=c.get("clap_diff", 0.5),
            gunshot_prob=c.get("gunshot_prob", 0.7),
            kept=c.get("kept", True),
        )
        for i, c in enumerate(candidates)
    ]
    return EnsembleResult(candidates=cands, consensus=consensus, expected_rounds=None)


def test_shot_detect_endpoint_writes_candidates(tmp_path: Path, monkeypatch) -> None:
    """The shot-detect job runs the 4-voter ensemble on the audit clip and
    merges per-voter signals into <project>/audit/stage<N>.json. Heavy
    models (CLAP, PANN, GBDT) are stubbed via the ensemble module entry
    points so the test runs offline."""
    import json as _json

    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    project = MatchProject.load(project_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(project_root)
    project.resolve_video_path(project_root, primary.path).resolve().write_bytes(b"S")

    # Pretend the trim already produced an audit WAV.
    audio_dir = project_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav = audio_dir / "stage1_audit.wav"
    sf.write(wav, np.zeros(48_000, dtype="float32"), 48_000)
    trimmed_dir = project_root / "trimmed"
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    (trimmed_dir / "stage1_trimmed.mp4").write_bytes(b"\x00")

    from splitsmith import ensemble as ensemble_module
    from splitsmith.ui import audio as audio_helpers
    from splitsmith.ui import server as server_module

    class FakeAudit:
        audio_path = wav
        beep_in_clip = 5.0
        trimmed = True

    # Stub the audit-audio resolver so the test doesn't depend on ffmpeg.
    monkeypatch.setattr(audio_helpers, "ensure_audit_audio", lambda *a, **kw: FakeAudit())
    # Skip CLAP/PANN/GBDT loading; the stub below ignores the runtime arg.
    monkeypatch.setattr(server_module, "_get_ensemble_runtime", lambda: None)
    fake_result = _fake_ensemble_result(
        [
            {"time": 5.5, "confidence": 0.8, "ensemble_score": 4.0, "kept": True},
            {"time": 6.1, "confidence": 0.6, "ensemble_score": 3.0, "kept": True},
            {"time": 6.9, "confidence": 0.9, "ensemble_score": 4.0, "kept": True},
        ]
    )
    monkeypatch.setattr(ensemble_module, "detect_shots_ensemble", lambda *a, **kw: fake_result)

    resp = client.post("/api/stages/1/shot-detect")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final
    audit_file = project_root / "audit" / "stage1.json"
    assert audit_file.exists()
    saved = _json.loads(audit_file.read_text(encoding="utf-8"))
    block = saved["_candidates_pending_audit"]
    assert block["consensus"] == 3
    cands = block["candidates"]
    assert len(cands) == 3
    assert cands[0]["candidate_number"] == 1
    assert cands[0]["time"] == pytest.approx(5.5)
    assert cands[0]["ms_after_beep"] == 500
    # Per-voter signals should reach the audit JSON.
    assert cands[0]["vote_a"] == 1
    assert cands[0]["vote_total"] == 4
    assert "score_c" in cands[0]
    # shots[] is seeded from the consensus subset.
    shots = saved["shots"]
    assert len(shots) == 3
    assert shots[0]["source"] == "detected"
    assert shots[0]["ensemble_votes"] == 4
    # processed.shot_detect flips on the primary so the SPA can show status.
    proj_after = client.get("/api/project").json()
    assert proj_after["stages"][0]["videos"][0]["processed"]["shot_detect"] is True


def test_shot_detect_endpoint_400_when_no_beep(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    project = MatchProject.load(project_root)
    project.stages[0].time_seconds = 10.0
    project.save(project_root)
    resp = client.post("/api/stages/1/shot-detect")
    assert resp.status_code == 400
    assert "beep_time" in resp.json()["detail"]


def test_shot_detect_endpoint_dedupes_active_jobs(tmp_path: Path, monkeypatch) -> None:
    """Second submit while first is running returns the same job."""
    import threading

    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    project = MatchProject.load(project_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(project_root)
    project.resolve_video_path(project_root, primary.path).resolve().write_bytes(b"S")
    audio_dir = project_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav = audio_dir / "stage1_audit.wav"
    sf.write(wav, np.zeros(48_000, dtype="float32"), 48_000)

    from splitsmith import ensemble as ensemble_module
    from splitsmith.ui import audio as audio_helpers
    from splitsmith.ui import server as server_module

    class FakeAudit:
        audio_path = wav
        beep_in_clip = 5.0
        trimmed = True

    monkeypatch.setattr(audio_helpers, "ensure_audit_audio", lambda *a, **kw: FakeAudit())
    monkeypatch.setattr(server_module, "_get_ensemble_runtime", lambda: None)

    proceed = threading.Event()
    invocations = []

    def slow_detect(*a, **kw):
        invocations.append(1)
        proceed.wait(timeout=2.0)
        return _fake_ensemble_result([])

    monkeypatch.setattr(ensemble_module, "detect_shots_ensemble", slow_detect)

    first = client.post("/api/stages/1/shot-detect").json()
    second = client.post("/api/stages/1/shot-detect").json()
    assert first["id"] == second["id"]
    proceed.set()
    final = _wait_for_job(client, first["id"])
    assert final["status"] == "succeeded"
    assert len(invocations) == 1


def test_jobs_endpoints_list_and_get(tmp_path: Path, monkeypatch) -> None:
    """/api/jobs lists active + recent; /api/jobs/{id} polls one. detect-beep
    and trim both surface here so the SPA has a single status surface."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    project = MatchProject.load(project_root)
    project.stages[0].time_seconds = 5.0
    project.save(project_root)
    primary = project.stages[0].primary()
    assert primary is not None
    project.resolve_video_path(project_root, primary.path).resolve().write_bytes(b"S")

    from splitsmith import beep_detect, trim
    from splitsmith.ui import audio as audio_helpers

    class FakeBeep:
        time = 1.0
        peak_amplitude = 0.5
        duration_ms = 80.0
        candidates: list = []

    monkeypatch.setattr(audio_helpers, "ensure_primary_audio", lambda *a, **kw: tmp_path / "z.wav")
    (tmp_path / "z.wav").write_bytes(b"\x00")
    monkeypatch.setattr(beep_detect, "load_audio", lambda p: ([0.0] * 100, 48_000))
    monkeypatch.setattr(beep_detect, "detect_beep", lambda *a, **kw: FakeBeep())

    def fake_trim(**kwargs):  # type: ignore[no-untyped-def]
        Path(kwargs["output_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["output_path"]).write_bytes(b"T")
        return trim.TrimResult(output_path=kwargs["output_path"], start_time=0.0, end_time=10.0)

    monkeypatch.setattr(trim, "trim_video", fake_trim)

    resp = client.post("/api/stages/1/detect-beep")
    assert resp.status_code == 200
    job_id = resp.json()["id"]
    final = _wait_for_job(client, job_id)
    assert final["status"] == "succeeded"
    assert final["progress"] == pytest.approx(1.0)
    assert final["kind"] == "detect_beep"
    assert final["stage_number"] == 1

    listing = client.get("/api/jobs").json()
    assert any(j["id"] == job_id for j in listing)


def test_get_job_404_when_unknown(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.get("/api/jobs/does-not-exist")
    assert resp.status_code == 404


def test_detect_beep_job_records_failure_when_ffmpeg_blows_up(tmp_path: Path, monkeypatch) -> None:
    """ffmpeg failures inside a job populate Job.error rather than 500-ing
    the submit request. The SPA learns about the failure via polling."""
    client, _ = _seed_project_with_primary(tmp_path)
    from splitsmith.ui import audio as audio_helpers

    def boom(*a, **kw):  # type: ignore[no-untyped-def]
        raise audio_helpers.AudioExtractionError("ffmpeg fell over")

    monkeypatch.setattr(audio_helpers, "detect_primary_beep", boom)
    monkeypatch.setattr(audio_helpers, "detect_video_beep", boom)

    resp = client.post("/api/stages/1/detect-beep")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "failed"
    assert "ffmpeg fell over" in final["error"]


def test_post_trim_400_when_no_beep(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    project = MatchProject.load(project_root)
    project.stages[0].time_seconds = 10.0
    project.save(project_root)
    resp = client.post("/api/stages/1/trim")
    assert resp.status_code == 400
    assert "beep_time" in resp.json()["detail"]


def test_get_stage_audit_returns_payload_when_file_exists(tmp_path: Path) -> None:
    """Audit endpoint reads <project>/audit/stage<N>.json verbatim."""
    import json

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    audit_dir = project_root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "stage_number": 1,
        "stage_name": "Stage 1",
        "shots": [{"shot_number": 1, "candidate_number": 4, "time": 1.5, "ms_after_beep": 1500}],
        "_candidates_pending_audit": {"candidates": []},
    }
    (audit_dir / "stage1.json").write_text(json.dumps(payload), encoding="utf-8")

    resp = client.get("/api/stages/1/audit")
    assert resp.status_code == 200
    assert resp.json()["shots"][0]["candidate_number"] == 4


def test_get_stage_audit_404_when_missing(tmp_path: Path) -> None:
    """No audit JSON yet -> 404. The SPA treats this as 'start fresh'."""
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.get("/api/stages/1/audit")
    assert resp.status_code == 404
    assert "no audit" in resp.json()["detail"]


def test_get_stage_audit_404_when_stage_unknown(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.get("/api/stages/99/audit")
    assert resp.status_code == 404


def test_put_stage_audit_writes_payload_and_returns_it(tmp_path: Path) -> None:
    """PUT writes the JSON under <project>/audit/stage<N>.json. The body
    round-trips so the SPA can keep a single source of truth."""
    client, _ = _seed_project_with_primary(tmp_path)
    payload = {
        "stage_number": 1,
        "stage_name": "Stage 1",
        "shots": [
            {
                "shot_number": 1,
                "candidate_number": 4,
                "time": 1.5,
                "ms_after_beep": 1500,
                "source": "detected",
            }
        ],
        "audit_events": [
            {"ts": "2026-05-02T12:00:00Z", "kind": "marker_kept", "payload": {"id": "cand-4"}}
        ],
    }
    resp = client.put("/api/stages/1/audit", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["shots"][0]["candidate_number"] == 4
    on_disk = (tmp_path / "match" / "audit" / "stage1.json").read_text(encoding="utf-8")
    import json as _json

    assert _json.loads(on_disk)["shots"][0]["candidate_number"] == 4


def test_put_stage_audit_keeps_previous_version_as_bak(tmp_path: Path) -> None:
    """A second PUT preserves the prior contents at stage<N>.json.bak so a
    bad save can be recovered manually."""
    client, _ = _seed_project_with_primary(tmp_path)
    first = {"stage_number": 1, "shots": [{"shot_number": 1, "time": 0.5}]}
    second = {"stage_number": 1, "shots": [{"shot_number": 1, "time": 1.5}]}
    audit_path = tmp_path / "match" / "audit"

    assert client.put("/api/stages/1/audit", json=first).status_code == 200
    assert client.put("/api/stages/1/audit", json=second).status_code == 200

    import json as _json

    final = _json.loads((audit_path / "stage1.json").read_text(encoding="utf-8"))
    backup = _json.loads((audit_path / "stage1.json.bak").read_text(encoding="utf-8"))
    assert final["shots"][0]["time"] == 1.5
    assert backup["shots"][0]["time"] == 0.5


def test_put_stage_audit_404_when_stage_unknown(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.put("/api/stages/99/audit", json={"stage_number": 99, "shots": []})
    assert resp.status_code == 404


def test_get_stage_anomalies_empty_when_no_audit_file(tmp_path: Path) -> None:
    """No audit JSON yet -> empty anomalies list (issue #42).

    The audit screen calls this on mount; pre-detection there's nothing
    useful to flag, so the panel renders the "looks clean" empty state.
    """
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.get("/api/stages/1/anomalies")
    assert resp.status_code == 200
    assert resp.json() == {"anomalies": []}


def test_get_stage_anomalies_flags_double_detection_with_shot_number(
    tmp_path: Path,
) -> None:
    """The endpoint returns structured anomalies that carry the offending
    shot's 1-based number so the SPA can scroll to that marker on click."""
    import json as _json

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    audit_dir = project_root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    # Two shots 50 ms apart -> double-detection rule fires on shot 2.
    payload = {
        "stage_number": 1,
        "stage_name": "Stage One",
        "shots": [
            {"shot_number": 1, "candidate_number": 1, "time": 1.0, "ms_after_beep": 1000},
            {"shot_number": 2, "candidate_number": 2, "time": 1.05, "ms_after_beep": 1050},
        ],
    }
    (audit_dir / "stage1.json").write_text(_json.dumps(payload), encoding="utf-8")

    resp = client.get("/api/stages/1/anomalies")
    assert resp.status_code == 200
    anomalies = resp.json()["anomalies"]
    kinds = {a["kind"] for a in anomalies}
    assert "double_detection" in kinds
    double = next(a for a in anomalies if a["kind"] == "double_detection")
    assert double["shot_number"] == 2
    assert double["severity"] == "warn"
    assert double["time"] == 1.05


def test_get_stage_anomalies_404_when_stage_unknown(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.get("/api/stages/99/anomalies")
    assert resp.status_code == 404


def test_fixture_audit_round_trip(tmp_path: Path) -> None:
    """The fixture endpoints read + write a JSON file in place. Closes #19's
    standalone review SPA -- localhost-only, no project context."""
    import json as _json

    client, _ = _seed_project_with_primary(tmp_path)
    fixture = tmp_path / "blacksmith-h1.json"
    fixture.write_text(
        _json.dumps(
            {
                "stage_number": 3,
                "stage_name": "H1",
                "beep_time": 0.5,
                "shots": [{"shot_number": 1, "time": 0.6}],
            }
        ),
        encoding="utf-8",
    )

    resp = client.get(f"/api/fixture/audit?path={fixture}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["beep_time"] == 0.5
    assert body["shots"][0]["time"] == 0.6

    resp = client.put(
        f"/api/fixture/audit?path={fixture}",
        json={"stage_number": 3, "shots": [{"shot_number": 1, "time": 0.7}]},
    )
    assert resp.status_code == 200
    on_disk = _json.loads(fixture.read_text(encoding="utf-8"))
    assert on_disk["shots"][0]["time"] == 0.7
    backup = fixture.with_suffix(fixture.suffix + ".bak")
    assert backup.exists(), "previous version should be retained as .bak"
    backup_data = _json.loads(backup.read_text(encoding="utf-8"))
    assert backup_data["shots"][0]["time"] == 0.6


def test_fixture_audit_404_on_missing_path(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.get(f"/api/fixture/audit?path={tmp_path}/does-not-exist.json")
    assert resp.status_code == 404


def test_fixture_peaks_serves_sibling_wav(tmp_path: Path) -> None:
    """Peaks endpoint reads <path>.with_suffix('.wav') and returns the
    same shape the project peaks endpoint does."""
    import json as _json

    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    fixture = tmp_path / "review.json"
    fixture.write_text(
        _json.dumps({"beep_time": 5.0, "shots": []}),
        encoding="utf-8",
    )
    audio = np.zeros(48_000, dtype="float32")
    audio[10_000:11_000] = 0.5
    sf.write(fixture.with_suffix(".wav"), audio, 48_000)

    resp = client.get(f"/api/fixture/peaks?path={fixture}&bins=64")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bins"] == 64
    assert body["trimmed"] is True
    assert body["beep_time"] == 5.0
    assert len(body["peaks"]) == 64


def test_fixture_audio_serves_sibling_wav(tmp_path: Path) -> None:
    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    fixture = tmp_path / "review.json"
    fixture.write_text("{}", encoding="utf-8")
    sf.write(fixture.with_suffix(".wav"), np.zeros(100, dtype="float32"), 48_000)

    resp = client.get(f"/api/fixture/audio?path={fixture}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    assert resp.content[:4] == b"RIFF"


def test_fixture_video_serves_arbitrary_path(tmp_path: Path) -> None:
    """Localhost convention: any path the user passes for --video is served
    through. The standalone review SPA had the same trust model."""
    client, _ = _seed_project_with_primary(tmp_path)
    video = tmp_path / "movie.mp4"
    video.write_bytes(b"FAKE_VIDEO_BYTES")
    resp = client.get(f"/api/fixture/video?path={video}")
    assert resp.status_code == 200
    assert resp.content == b"FAKE_VIDEO_BYTES"
    assert resp.headers["content-type"].startswith("video/")


def test_stream_video_serves_registered_file(tmp_path: Path) -> None:
    """Stream endpoint serves bytes for a path that's registered with the project."""
    client, _ = _seed_project_with_primary(tmp_path)
    project = MatchProject.load(tmp_path / "match")
    primary = project.stages[0].primary()
    assert primary is not None
    resolved = project.resolve_video_path(tmp_path / "match", primary.path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_bytes(b"FAKE_MP4_BYTES")

    resp = client.get(f"/api/videos/stream?path={primary.path}")
    assert resp.status_code == 200
    assert resp.content == b"FAKE_MP4_BYTES"
    assert resp.headers["content-type"].startswith("video/")


def test_stream_video_404_on_unregistered_path(tmp_path: Path) -> None:
    """Stream endpoint refuses to serve arbitrary filesystem paths."""
    client, _ = _seed_project_with_primary(tmp_path)
    secret = tmp_path / "secret.mp4"
    secret.write_bytes(b"SECRET")
    resp = client.get(f"/api/videos/stream?path={secret}")
    assert resp.status_code == 404
    assert "not registered" in resp.json()["detail"]


def test_stream_video_424_when_target_missing(tmp_path: Path) -> None:
    """Registered path that no longer exists on disk surfaces as 424
    (Failed Dependency) with a structured detail. The SPA reads
    ``detail.code == "source_unreachable"`` to render a consistent
    "reconnect the USB / SD card" message across detect-beep, trim,
    beep preview, video stream, and export."""
    client, _ = _seed_project_with_primary(tmp_path)
    project = MatchProject.load(tmp_path / "match")
    primary = project.stages[0].primary()
    assert primary is not None
    resolved = project.resolve_video_path(tmp_path / "match", primary.path).resolve()
    if resolved.exists() or resolved.is_symlink():
        resolved.unlink()
    resp = client.get(f"/api/videos/stream?path={primary.path}")
    assert resp.status_code == 424
    body = resp.json()
    assert body["detail"]["code"] == "source_unreachable"
    assert "not reachable" in body["detail"]["message"]


def test_peaks_endpoint_rejects_extreme_bins(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    assert client.get("/api/stages/1/peaks?bins=8").status_code == 422
    assert client.get("/api/stages/1/peaks?bins=999999").status_code == 422


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


def test_settings_409_when_old_dir_non_empty(tmp_path: Path) -> None:
    """Changing a path field must surface a 409 ``non_empty_old_dirs`` warning
    when the old directory still has files -- splitsmith does not migrate."""
    root = tmp_path / "match"
    app = create_app(project_root=root, project_name="x")
    client = TestClient(app)

    # Default audio_dir is <project>/audio. Drop a stray cache file there.
    audio_default = root / "audio"
    audio_default.mkdir(parents=True, exist_ok=True)
    (audio_default / "stage_1.wav").write_bytes(b"fake")

    new_audio = tmp_path / "scratch-audio"
    resp = client.post(
        "/api/project/settings",
        json={"audio_dir": str(new_audio)},
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "non_empty_old_dirs"
    assert len(detail["dirs"]) == 1
    assert detail["dirs"][0]["field"] == "audio_dir"
    assert detail["dirs"][0]["path"] == str(audio_default)
    assert detail["dirs"][0]["file_count"] == 1
    # Project unchanged on disk.
    assert MatchProject.load(root).audio_dir is None

    # With confirm=true the change goes through, leaving old files behind.
    resp = client.post(
        "/api/project/settings",
        json={"audio_dir": str(new_audio), "confirm": True},
    )
    assert resp.status_code == 200
    assert resp.json()["audio_dir"] == str(new_audio)
    assert (audio_default / "stage_1.wav").exists()  # not migrated


def test_settings_no_warning_when_old_dir_empty(tmp_path: Path) -> None:
    """An empty old directory should not trigger the warning."""
    root = tmp_path / "match"
    app = create_app(project_root=root, project_name="x")
    client = TestClient(app)
    # Default <project>/audio doesn't even exist yet.
    resp = client.post(
        "/api/project/settings",
        json={"audio_dir": str(tmp_path / "elsewhere")},
    )
    assert resp.status_code == 200


def test_remove_video_unassigned(tmp_path: Path) -> None:
    """Removing an unassigned video drops it from the project and unlinks
    its symlink under raw_dir. The original source on USB / external storage
    is never touched."""
    root = tmp_path / "match"
    src_dir = tmp_path / "ext"
    src_dir.mkdir()
    src = src_dir / "clip.mp4"
    src.write_bytes(b"fake")

    app = create_app(project_root=root, project_name="x")
    client = TestClient(app)
    resp = client.post(
        "/api/videos/scan",
        json={"source_paths": [str(src)], "auto_assign_primary": False},
    )
    assert resp.status_code == 200
    registered_path = resp.json()["registered"][0]
    raw_link = root / "raw" / "clip.mp4"
    assert raw_link.is_symlink()

    resp = client.post("/api/videos/remove", json={"video_path": registered_path})
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"]["was_primary"] is False
    assert body["project"]["unassigned_videos"] == []
    assert not raw_link.exists()
    assert src.exists(), "original source must never be touched"


def test_remove_primary_clears_caches_and_keeps_audit(tmp_path: Path) -> None:
    root = tmp_path / "match"
    src_dir = tmp_path / "ext"
    src_dir.mkdir()
    src = src_dir / "clip.mp4"
    src.write_bytes(b"fake")

    app = create_app(project_root=root, project_name="x")
    client = TestClient(app)
    sb = {
        "match": {"id": "1", "name": "M"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "A",
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
    client.post("/api/scoreboard/import", json={"data": sb})
    client.post(
        "/api/videos/scan",
        json={"source_paths": [str(src)], "auto_assign_primary": False},
    )
    project = MatchProject.load(root)
    project.assign_video(Path("raw/clip.mp4"), to_stage_number=1, role="primary")
    # Simulate processed state: write fake cached files audit/audio/trimmed.
    audio_cache = root / "audio" / "stage1_primary.wav"
    audio_cache.parent.mkdir(parents=True, exist_ok=True)
    audio_cache.write_bytes(b"wav")
    trimmed_cache = root / "trimmed" / "stage1_trimmed.mp4"
    trimmed_cache.parent.mkdir(parents=True, exist_ok=True)
    trimmed_cache.write_bytes(b"mp4")
    audit = root / "audit" / "stage1.json"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text("{}")
    project.stages[0].videos[0].processed = {
        "beep": True,
        "shot_detect": True,
        "trim": True,
    }
    project.save(root)

    resp = client.post(
        "/api/videos/remove",
        json={"video_path": "raw/clip.mp4"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"]["was_primary"] is True
    assert body["plan"]["audit_reset"] is False
    assert not audio_cache.exists()
    assert not trimmed_cache.exists()
    assert audit.exists(), "audit JSON must be preserved by default"


def test_remove_primary_with_reset_audit_clears_audit(tmp_path: Path) -> None:
    root = tmp_path / "match"
    src_dir = tmp_path / "ext"
    src_dir.mkdir()
    src = src_dir / "clip.mp4"
    src.write_bytes(b"fake")

    app = create_app(project_root=root, project_name="x")
    client = TestClient(app)
    sb = {
        "match": {"id": "1", "name": "M"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "A",
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
    client.post("/api/scoreboard/import", json={"data": sb})
    client.post(
        "/api/videos/scan",
        json={"source_paths": [str(src)], "auto_assign_primary": False},
    )
    project = MatchProject.load(root)
    project.assign_video(Path("raw/clip.mp4"), to_stage_number=1, role="primary")
    project.save(root)
    audit = root / "audit" / "stage1.json"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text("{}")

    resp = client.post(
        "/api/videos/remove",
        json={"video_path": "raw/clip.mp4", "reset_audit": True},
    )
    assert resp.status_code == 200
    assert resp.json()["plan"]["audit_reset"] is True
    assert not audit.exists()


def test_remove_video_404_when_unknown(tmp_path: Path) -> None:
    app = create_app(project_root=tmp_path / "match", project_name="x")
    client = TestClient(app)
    resp = client.post(
        "/api/videos/remove",
        json={"video_path": "raw/no-such.mp4"},
    )
    assert resp.status_code == 404


def test_fs_list_probe_populates_duration_and_thumbnail(tmp_path: Path) -> None:
    """When ``probe=true`` is passed, video entries get duration + thumbnail."""
    root = tmp_path / "match"
    folder = tmp_path / "videos"
    folder.mkdir()
    clip = folder / "clip.mp4"
    clip.write_bytes(b"fake")

    app = create_app(project_root=root, project_name="x")
    client = TestClient(app)

    def _fake_probe(path: Path, *, cache_dir: Path, **kwargs):  # noqa: ANN001
        cache_dir.mkdir(parents=True, exist_ok=True)
        return ProbeResult(duration=12.5, width=1920, height=1080, codec="h264")

    def _fake_thumb(source: Path, *, cache_dir: Path, **kwargs):  # noqa: ANN001
        cache_dir.mkdir(parents=True, exist_ok=True)
        from splitsmith.video_probe import source_cache_key

        dest = cache_dir / f"{source_cache_key(source)}.jpg"
        dest.write_bytes(b"\xff\xd8\xff")
        return dest

    with (
        patch("splitsmith.ui.server.video_probe.probe", side_effect=_fake_probe),
        patch("splitsmith.ui.server.thumbnail_helpers.ensure", side_effect=_fake_thumb),
    ):
        resp = client.get(f"/api/fs/list?path={folder}&probe=true")

    assert resp.status_code == 200
    body = resp.json()
    [entry] = body["entries"]
    assert entry["kind"] == "video"
    assert entry["duration"] == 12.5
    assert entry["thumbnail_url"].startswith("/api/thumbnails/")


def test_fs_probe_endpoint_runs_on_demand(tmp_path: Path) -> None:
    root = tmp_path / "match"
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake")

    app = create_app(project_root=root, project_name="x")
    client = TestClient(app)

    def _fake_probe(path: Path, *, cache_dir: Path, **kwargs):  # noqa: ANN001
        cache_dir.mkdir(parents=True, exist_ok=True)
        return ProbeResult(duration=8.0)

    def _fake_thumb(source: Path, *, cache_dir: Path, **kwargs):  # noqa: ANN001
        cache_dir.mkdir(parents=True, exist_ok=True)
        from splitsmith.video_probe import source_cache_key

        dest = cache_dir / f"{source_cache_key(source)}.jpg"
        dest.write_bytes(b"\xff")
        return dest

    with (
        patch("splitsmith.ui.server.video_probe.probe", side_effect=_fake_probe),
        patch("splitsmith.ui.server.thumbnail_helpers.ensure", side_effect=_fake_thumb),
    ):
        resp = client.get(f"/api/fs/probe?path={clip}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["duration"] == 8.0
    assert body["thumbnail_url"].startswith("/api/thumbnails/")


def test_thumbnail_endpoint_serves_cached(tmp_path: Path) -> None:
    root = tmp_path / "match"
    app = create_app(project_root=root, project_name="x")
    client = TestClient(app)

    thumbs_dir = root / "thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    (thumbs_dir / "abc123.jpg").write_bytes(b"\xff\xd8\xff jpeg")

    resp = client.get("/api/thumbnails/abc123.jpg")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/jpeg")

    resp_404 = client.get("/api/thumbnails/missing.jpg")
    assert resp_404.status_code == 404


def test_thumbnail_endpoint_rejects_path_traversal(tmp_path: Path) -> None:
    app = create_app(project_root=tmp_path / "match", project_name="x")
    client = TestClient(app)
    # Slashes in the path component aren't possible thanks to FastAPI's
    # path matcher, but '..' as a key should be rejected by our shape check.
    resp = client.get("/api/thumbnails/..jpg")
    assert resp.status_code == 400


def test_fs_list_probe_skipped_when_falsy(tmp_path: Path) -> None:
    """Without ?probe=true and without cached results, video entries should
    have null duration / thumbnail and probe / ensure must not run."""
    root = tmp_path / "match"
    folder = tmp_path / "videos"
    folder.mkdir()
    (folder / "clip.mp4").write_bytes(b"fake")

    app = create_app(project_root=root, project_name="x")
    client = TestClient(app)

    with (
        patch(
            "splitsmith.ui.server.video_probe.probe",
            side_effect=AssertionError("probe must not run"),
        ),
        patch(
            "splitsmith.ui.server.thumbnail_helpers.ensure",
            side_effect=AssertionError("thumb must not run"),
        ),
    ):
        resp = client.get(f"/api/fs/list?path={folder}")

    assert resp.status_code == 200
    [entry] = resp.json()["entries"]
    assert entry["duration"] is None
    assert entry["thumbnail_url"] is None


def test_fs_list_probe_failure_swallowed(tmp_path: Path) -> None:
    """A probe failure must not break the listing; the entry just gets nulls."""
    root = tmp_path / "match"
    folder = tmp_path / "videos"
    folder.mkdir()
    (folder / "clip.mp4").write_bytes(b"fake")

    app = create_app(project_root=root, project_name="x")
    client = TestClient(app)

    with (
        patch(
            "splitsmith.ui.server.video_probe.probe",
            side_effect=ProbeError("boom"),
        ),
        patch(
            "splitsmith.ui.server.thumbnail_helpers.ensure",
            side_effect=ThumbnailError("thumb fails too"),
        ),
    ):
        resp = client.get(f"/api/fs/list?path={folder}&probe=true")

    assert resp.status_code == 200
    [entry] = resp.json()["entries"]
    assert entry["duration"] is None


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


# Per-video beep endpoints (multi-cam ingest) -------------------------------


def _seed_project_with_secondary(tmp_path: Path) -> tuple[TestClient, Path]:
    """Boot a server with one stage, a primary, and a secondary assigned.

    Returns ``(client, secondary_source_path)`` so the caller can mutate
    the secondary source if needed.
    """
    client, _ = _seed_project_with_primary(tmp_path)
    src_dir = tmp_path / "videos"
    cam2 = src_dir / "CAM2.mp4"
    cam2.write_bytes(b"")
    client.post(
        "/api/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": False},
    )
    client.post(
        "/api/assignments/move",
        json={"video_path": "raw/CAM2.mp4", "to_stage_number": 1, "role": "secondary"},
    )
    return client, cam2


def _video_id_for(client: TestClient, stage_number: int, role: str) -> str:
    """Pull the ``video_id`` for the first video matching ``role`` on a stage."""
    proj = client.get("/api/project").json()
    stage = next(s for s in proj["stages"] if s["stage_number"] == stage_number)
    video = next(v for v in stage["videos"] if v["role"] == role)
    return video["video_id"]


def test_video_id_is_stable_and_exposed(tmp_path: Path) -> None:
    """The computed ``video_id`` is on the wire and identical across reloads."""
    client, _ = _seed_project_with_primary(tmp_path)
    proj1 = client.get("/api/project").json()
    proj2 = client.get("/api/project").json()
    vid1 = proj1["stages"][0]["videos"][0]["video_id"]
    vid2 = proj2["stages"][0]["videos"][0]["video_id"]
    assert vid1 == vid2
    assert len(vid1) == 12  # 6-byte blake2s -> 12 hex chars


def test_per_video_detect_beep_runs_on_secondary(tmp_path: Path, monkeypatch) -> None:
    """The /api/stages/{n}/videos/{video_id}/detect-beep endpoint runs the
    beep pipeline on a secondary and persists the result onto that
    secondary -- not the primary."""
    client, _ = _seed_project_with_secondary(tmp_path)
    _stub_detect(monkeypatch, beep_time=7.250)

    sec_id = _video_id_for(client, 1, "secondary")
    resp = client.post(f"/api/stages/1/videos/{sec_id}/detect-beep")
    assert resp.status_code == 200
    job = resp.json()
    assert job["video_id"] == sec_id
    final = _wait_for_job(client, job["id"])
    assert final["status"] == "succeeded", final

    proj = client.get("/api/project").json()
    primary = next(v for v in proj["stages"][0]["videos"] if v["role"] == "primary")
    secondary = next(v for v in proj["stages"][0]["videos"] if v["role"] == "secondary")
    assert primary["beep_time"] is None  # primary untouched
    assert secondary["beep_time"] == pytest.approx(7.250)
    assert secondary["beep_source"] == "auto"
    assert secondary["processed"]["beep"] is True


def test_per_video_detect_beep_dedupes_per_video(tmp_path: Path, monkeypatch) -> None:
    """Two simultaneous detect-beep posts for the same video adopt one job;
    a post for a different video on the same stage runs in parallel."""
    client, _ = _seed_project_with_secondary(tmp_path)
    # Gate the fake detector so the first job stays RUNNING long enough
    # for the second POST to land in find_active. Without this the stub
    # returns synchronously and the worker can flip the job to SUCCEEDED
    # before the second submit checks for an active dedup target.
    gate = threading.Event()
    _stub_detect(monkeypatch, beep_time=3.0, gate=gate)

    primary_id = _video_id_for(client, 1, "primary")
    sec_id = _video_id_for(client, 1, "secondary")
    j1 = client.post(f"/api/stages/1/videos/{primary_id}/detect-beep").json()
    j2 = client.post(f"/api/stages/1/videos/{primary_id}/detect-beep").json()
    j3 = client.post(f"/api/stages/1/videos/{sec_id}/detect-beep").json()
    assert j1["id"] == j2["id"]  # same video -> same job
    assert j1["id"] != j3["id"]  # different video -> different job
    gate.set()
    _wait_for_job(client, j1["id"])
    _wait_for_job(client, j3["id"])


def test_per_video_manual_override(tmp_path: Path) -> None:
    """The /beep override endpoint sets ``beep_source="manual"`` on the
    targeted video and clears its diagnostic fields."""
    client, _ = _seed_project_with_secondary(tmp_path)
    sec_id = _video_id_for(client, 1, "secondary")

    resp = client.post(f"/api/stages/1/videos/{sec_id}/beep", json={"beep_time": 4.5})
    assert resp.status_code == 200
    proj = resp.json()
    secondary = next(v for v in proj["stages"][0]["videos"] if v["role"] == "secondary")
    assert secondary["beep_time"] == pytest.approx(4.5)
    assert secondary["beep_source"] == "manual"

    # Clear flips back to "no beep yet" and resets processed.beep
    resp = client.post(f"/api/stages/1/videos/{sec_id}/beep", json={"beep_time": None})
    assert resp.status_code == 200
    proj = resp.json()
    secondary = next(v for v in proj["stages"][0]["videos"] if v["role"] == "secondary")
    assert secondary["beep_time"] is None
    assert secondary["beep_source"] is None
    assert secondary["processed"]["beep"] is False


def test_legacy_primary_endpoints_still_work(tmp_path: Path, monkeypatch) -> None:
    """The pre-existing /api/stages/{n}/detect-beep endpoint forwards to
    the per-video pipeline and operates on the stage's primary."""
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=12.453)

    resp = client.post("/api/stages/1/detect-beep")
    assert resp.status_code == 200
    job = resp.json()
    primary_id = _video_id_for(client, 1, "primary")
    assert job["video_id"] == primary_id  # forwarded to per-video pipeline
    final = _wait_for_job(client, job["id"])
    assert final["status"] == "succeeded", final


def test_per_video_endpoint_404_for_unknown_video_id(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.post("/api/stages/1/videos/deadbeef0000/detect-beep")
    assert resp.status_code == 404
