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


# ---------------------------------------------------------------------------
# Coverage suggestion endpoint (Task 7)
# ---------------------------------------------------------------------------


def _setup_with_scorecard(tmp_path: Path, scorecard_iso: str = "2026-06-01T10:20:00+00:00"):
    """Like _setup but returns a client whose stage 1 has scorecard_updated_at set."""
    from tests.test_ui_server import _match_create_app, _MatchClient

    project_root = tmp_path / "match"
    app = _match_create_app(project_root=project_root, project_name="Suggest Test")
    client = _MatchClient(app)

    sb = {
        "match": {"id": "1", "name": "Suggest Test"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "Tester",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "Stage One",
                        "time_seconds": 10.0,
                        "scorecard_updated_at": scorecard_iso,
                    },
                    {
                        "stage_number": 2,
                        "stage_name": "Stage Two",
                        "time_seconds": 12.0,
                        "scorecard_updated_at": "2026-06-01T10:45:00+00:00",
                    },
                ],
            }
        ],
    }
    client.post("/api/shooters/me/scoreboard/import", json={"data": sb})
    return client


def test_suggest_coverage_explicit_span_returns_stages(tmp_path: Path) -> None:
    """POST suggest-coverage with explicit recorded_start + duration_s returns covered stages."""
    from datetime import datetime, timedelta

    client = _setup_with_scorecard(tmp_path)

    # Stage 1 scorecard = 10:20 UTC -> window [10:05, 10:20].
    # Stage 2 scorecard = 10:45 UTC -> window [10:30, 10:45].
    # Span: 10:06 -> 10:32 covers both windows.
    recorded_start_iso = "2026-06-01T10:06:00+00:00"
    duration_s = 1560.0  # 26 minutes -> ends at 10:32
    resp = client.post(
        "/api/shooters/me/videos/suggest-coverage",
        json={
            "recorded_start": recorded_start_iso,
            "duration_s": duration_s,
            "path": None,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["covers_stages"] == [1, 2]
    assert body["span"] is not None

    # Verify the span values round-trip correctly.
    expected_start = datetime.fromisoformat(recorded_start_iso)
    expected_end = expected_start + timedelta(seconds=duration_s)
    actual_start = datetime.fromisoformat(body["span"]["start"])
    actual_end = datetime.fromisoformat(body["span"]["end"])
    assert actual_start == expected_start
    assert actual_end == expected_end


def test_suggest_coverage_all_null_returns_empty(tmp_path: Path) -> None:
    """POST suggest-coverage with all-null fields returns covers_stages=[] and span=null."""
    client = _setup_with_scorecard(tmp_path)

    resp = client.post(
        "/api/shooters/me/videos/suggest-coverage",
        json={"recorded_start": None, "duration_s": None, "path": None},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["covers_stages"] == []
    assert body["span"] is None


def test_suggest_coverage_naive_datetime_returns_422(tmp_path: Path) -> None:
    """POST suggest-coverage with naive recorded_start returns 422."""
    client = _setup_with_scorecard(tmp_path)

    # Naive datetime (no timezone offset) should fail validation.
    resp = client.post(
        "/api/shooters/me/videos/suggest-coverage",
        json={
            "recorded_start": "2026-06-01T10:06:00",
            "duration_s": 1560.0,
            "path": None,
        },
    )
    assert resp.status_code == 422
