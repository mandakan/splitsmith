"""Endpoint tests for the multi-stage single-take feature."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _disable_auto_beep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")


def _setup(tmp_path: Path):
    """Scaffold a project with one stage and a primary video.

    Returns (client, video_id).
    """
    from tests.test_ui_server import _match_create_app, _MatchClient

    project_root = tmp_path / "match"
    app = _match_create_app(project_root=project_root, project_name="Take Test")
    client = _MatchClient(app)

    # Import scoreboard so stage 1 exists with a time.
    sb = {
        "match": {"id": "1", "name": "Take Test"},
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
    client.post("/api/shooters/me/scoreboard/import", json={"data": sb})

    # Register a source video and assign it to stage 1 as primary.
    src_dir = tmp_path / "videos"
    src_dir.mkdir()
    (src_dir / "VID.mp4").write_bytes(b"")
    client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": False},
    )
    client.post(
        "/api/shooters/me/assignments/move",
        json={"video_path": "raw/VID.mp4", "to_stage_number": 1, "role": "primary"},
    )

    # Read back the video_id from the reloaded project.
    proj_resp = client.get("/api/shooters/me/project")
    assert proj_resp.status_code == 200
    video_id = proj_resp.json()["stages"][0]["videos"][0]["video_id"]
    return client, video_id


def test_set_beep_window_persists_and_submits_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PUT /beep-window sets the window, wipes beep state, and enqueues detect_beep."""
    client, video_id = _setup(tmp_path)

    # Re-enable auto-beep so _submit_detect_beep runs.
    monkeypatch.delenv("SPLITSMITH_AUTO_BEEP_DISABLED", raising=False)

    resp = client.put(
        f"/api/shooters/me/stages/1/videos/{video_id}/beep-window",
        json={"start_s": 30.0, "end_s": 210.0},
    )
    assert resp.status_code == 200
    job = resp.json()
    assert job["kind"] == "detect_beep"

    # Project fields set correctly.
    proj = client.get("/api/shooters/me/project").json()
    vid = proj["stages"][0]["videos"][0]
    assert vid["beep_window"] == [30.0, 210.0]
    assert vid["beep_window_source"] == "manual"
    assert vid["beep_time"] is None
    assert vid["beep_candidates"] == []
    assert vid["beep_reviewed"] is False
    assert vid["processed"]["beep"] is False


def test_set_beep_window_clears_existing_beep_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Window endpoint wipes beep_time and resets processed flags."""
    client, video_id = _setup(tmp_path)

    # Manually seed beep state so we can verify it gets wiped.
    client.post(
        "/api/shooters/me/stages/1/beep",
        json={"beep_time": 5.0},
    )

    monkeypatch.delenv("SPLITSMITH_AUTO_BEEP_DISABLED", raising=False)

    resp = client.put(
        f"/api/shooters/me/stages/1/videos/{video_id}/beep-window",
        json={"start_s": 0.0, "end_s": 60.0},
    )
    assert resp.status_code == 200

    proj = client.get("/api/shooters/me/project").json()
    vid = proj["stages"][0]["videos"][0]
    assert vid["beep_window"] == [0.0, 60.0]
    assert vid["beep_window_source"] == "manual"
    assert vid["beep_time"] is None
    assert vid["processed"]["beep"] is False
    assert vid["processed"]["trim"] is False
    assert vid["beep_reviewed"] is False


def test_set_beep_window_422_when_end_not_after_start(tmp_path: Path) -> None:
    """422 when end_s <= start_s."""
    client, video_id = _setup(tmp_path)

    resp = client.put(
        f"/api/shooters/me/stages/1/videos/{video_id}/beep-window",
        json={"start_s": 10.0, "end_s": 10.0},
    )
    assert resp.status_code == 422

    resp = client.put(
        f"/api/shooters/me/stages/1/videos/{video_id}/beep-window",
        json={"start_s": 20.0, "end_s": 5.0},
    )
    assert resp.status_code == 422


def test_set_beep_window_422_when_start_negative(tmp_path: Path) -> None:
    """422 when start_s < 0."""
    client, video_id = _setup(tmp_path)

    resp = client.put(
        f"/api/shooters/me/stages/1/videos/{video_id}/beep-window",
        json={"start_s": -1.0, "end_s": 60.0},
    )
    assert resp.status_code == 422
