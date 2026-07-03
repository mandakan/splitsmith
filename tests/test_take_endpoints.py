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


# ---------------------------------------------------------------------------
# Take overview endpoint (Task 8)
# ---------------------------------------------------------------------------


def _setup_take(tmp_path: Path, n_stages: int = 2):
    """Scaffold a project with n_stages and one raw video covering all stages.

    Uses PATCH /raw-videos/coverage to register the video, which creates the
    RawVideo entry with covers_stages (required by the overview + peaks endpoints).

    Returns (client, filename).
    """
    from splitsmith.match_model import Match
    from splitsmith.ui.project import MatchProject, StageEntry
    from tests.test_ui_server import _match_create_app, _MatchClient

    project_root = tmp_path / "match"
    app = _match_create_app(project_root=project_root, project_name="Take Overview Test")
    client = _MatchClient(app)

    # Add stages directly so we control the shape without going through scoreboard import.
    shooter_root = Match.shooter_root(project_root, "me")
    project = MatchProject.load(shooter_root)
    for i in range(1, n_stages + 1):
        project.stages.append(
            StageEntry(
                stage_number=i,
                stage_name=f"Stage {i}",
                time_seconds=10.0 + i,
            )
        )
    project.save(shooter_root)

    # Place the video on disk and scan so it appears in unassigned_videos.
    src_dir = tmp_path / "videos"
    src_dir.mkdir()
    filename = "VID_TAKE.mp4"
    (src_dir / filename).write_bytes(b"")
    client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": False},
    )

    # Register coverage - this creates the RawVideo entry and StageVideos.
    cover_stages = list(range(1, n_stages + 1))
    resp = client.patch(
        "/api/shooters/me/raw-videos/coverage",
        json={"filename": filename, "covers_stages": cover_stages},
    )
    assert resp.status_code == 200, f"coverage patch failed: {resp.json()}"

    return client, filename


def test_take_overview_404_unknown_filename(tmp_path: Path) -> None:
    """GET /raw-videos/overview returns 404 when filename is not registered."""
    client, _fn = _setup_take(tmp_path, n_stages=1)

    resp = client.get("/api/shooters/me/raw-videos/overview?filename=UNKNOWN.mp4")
    assert resp.status_code == 404


def test_take_overview_returns_shape(tmp_path: Path) -> None:
    """GET /raw-videos/overview returns the expected JSON shape."""
    client, filename = _setup_take(tmp_path, n_stages=2)

    resp = client.get(f"/api/shooters/me/raw-videos/overview?filename={filename}")
    assert resp.status_code == 200
    body = resp.json()

    assert "raw_video" in body
    assert "duration_seconds" in body
    assert "stages" in body
    assert "conflicts" in body

    # Two stages are covered by this take.
    assert len(body["stages"]) == 2
    stage_numbers = {s["stage_number"] for s in body["stages"]}
    assert stage_numbers == {1, 2}

    for s in body["stages"]:
        assert "stage_number" in s
        assert "stage_name" in s
        assert "video_id" in s
        assert "role" in s
        assert "beep_time" in s
        assert "beep_confidence" in s
        assert "beep_reviewed" in s
        assert "beep_window" in s
        assert "beep_window_source" in s
        assert "status" in s

    # No beep detected yet - status should be "pending".
    for s in body["stages"]:
        assert s["status"] == "pending"

    # No conflicts (no beep times set).
    assert body["conflicts"] == []


def test_take_overview_status_found_after_beep(tmp_path: Path) -> None:
    """Stage status is 'found' when beep_time is set."""
    client, filename = _setup_take(tmp_path, n_stages=1)

    # Manually set a beep via the existing beep endpoint.
    client.post("/api/shooters/me/stages/1/beep", json={"beep_time": 5.0})

    resp = client.get(f"/api/shooters/me/raw-videos/overview?filename={filename}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["stages"][0]["status"] == "found"
    assert body["stages"][0]["beep_time"] == pytest.approx(5.0)


def test_take_overview_status_none_on_failed_detect(tmp_path: Path) -> None:
    """Stage status is 'none' when beep_auto_detect_failed is True and no beep_time."""
    from splitsmith.match_model import Match
    from splitsmith.ui.project import MatchProject

    client, filename = _setup_take(tmp_path, n_stages=1)

    # Default state: no beep and not failed - status is "pending".
    resp = client.get(f"/api/shooters/me/raw-videos/overview?filename={filename}")
    assert resp.status_code == 200
    assert resp.json()["stages"][0]["status"] == "pending"

    # Seed beep_auto_detect_failed=True on the stage-video to simulate a
    # detect job that ran and produced no candidate.
    shooter_root = Match.shooter_root(tmp_path / "match", "me")
    project = MatchProject.load(shooter_root)
    sv = next(v for v in project.stage(1).videos if str(v.path) == f"raw/{filename}")
    sv.beep_auto_detect_failed = True
    project.save(shooter_root)

    resp = client.get(f"/api/shooters/me/raw-videos/overview?filename={filename}")
    assert resp.status_code == 200
    assert resp.json()["stages"][0]["status"] == "none"


def test_take_overview_conflicts_flagged(tmp_path: Path) -> None:
    """Stages with beep times within the conflict threshold are flagged."""
    client, filename = _setup_take(tmp_path, n_stages=2)

    # Set nearly identical beep times - within the default 2 s conflict threshold.
    client.post("/api/shooters/me/stages/1/beep", json={"beep_time": 100.0})
    client.post("/api/shooters/me/stages/2/beep", json={"beep_time": 100.5})

    resp = client.get(f"/api/shooters/me/raw-videos/overview?filename={filename}")
    assert resp.status_code == 200
    body = resp.json()

    # Both stages should appear in conflicts.
    conflicts = sorted(body["conflicts"])
    assert 1 in conflicts
    assert 2 in conflicts


# ---------------------------------------------------------------------------
# Take peaks endpoint (Task 8)
# ---------------------------------------------------------------------------


def test_take_peaks_404_unknown_filename(tmp_path: Path) -> None:
    """GET /raw-videos/peaks returns 404 when filename is not registered."""
    client, _fn = _setup_take(tmp_path, n_stages=1)

    resp = client.get("/api/shooters/me/raw-videos/peaks?filename=UNKNOWN.mp4")
    assert resp.status_code == 404


def test_take_peaks_local_mode_extracts_and_returns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /raw-videos/peaks in local mode extracts audio + returns peaks payload."""
    from unittest.mock import patch  # noqa: F401 (imported for monkeypatching side-effects)

    import numpy as np
    import soundfile as sf

    from splitsmith.ui import audio as audio_helpers

    client, filename = _setup_take(tmp_path, n_stages=1)

    # Write a small real WAV so ensure_take_audio has something to return.
    sr = 8000
    data = np.zeros(sr * 2, dtype=np.float32)  # 2 s silence
    real_wav = tmp_path / "fake_take.wav"
    sf.write(real_wav, data, sr)

    # Stub ensure_take_audio to return our pre-baked WAV.
    with patch.object(audio_helpers, "ensure_take_audio", return_value=real_wav):
        resp = client.get(f"/api/shooters/me/raw-videos/peaks?filename={filename}&bins=100")

    assert resp.status_code == 200
    body = resp.json()
    assert body["bins"] == 100
    assert len(body["peaks"]) == 100
    assert "duration" in body
    assert "sample_rate" in body


def _setup_hosted_peaks(tmp_path: Path, project_subdir: str = "match", filename: str = "VID_HOSTED.mp4"):
    """Scaffold a hosted-mode peaks test: local coverage registration + fake storage injected.

    Returns (app, client, filename).  state.storage is a MagicMock with
    exists() returning False so the hosted branch always misses the JSON.
    All background detect_beep jobs submitted during coverage registration are
    drained before returning (the empty test MP4 makes them fail almost
    immediately), so callers start with no active jobs.
    """
    import asyncio
    import time
    from unittest.mock import MagicMock

    from splitsmith.match_model import Match
    from splitsmith.ui.project import MatchProject, StageEntry
    from tests.test_ui_server import _match_create_app, _MatchClient

    project_root = tmp_path / project_subdir
    app = _match_create_app(project_root=project_root, project_name="Hosted Peaks Test")
    client = _MatchClient(app)

    shooter_root = Match.shooter_root(project_root, "me")
    project = MatchProject.load(shooter_root)
    project.stages.append(StageEntry(stage_number=1, stage_name="Stage One", time_seconds=10.0))
    project.save(shooter_root)

    src_dir = tmp_path / "videos"
    src_dir.mkdir(exist_ok=True)
    (src_dir / filename).write_bytes(b"")
    client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": False},
    )
    resp = client.patch(
        "/api/shooters/me/raw-videos/coverage",
        json={"filename": filename, "covers_stages": [1]},
    )
    assert resp.status_code == 200

    # Coverage PATCH submits a detect_beep job via _queue_take_detects. Drain it
    # before injecting fake storage - the empty test MP4 makes ffmpeg fail fast.
    state_ref = app.state.splitsmith_state
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if asyncio.run(state_ref.jobs.find_active(kind="detect_beep", stage_number=1)) is None:
            break
        time.sleep(0.05)

    fake_storage = MagicMock()
    fake_storage.exists.return_value = False
    app.state.splitsmith_state.storage = fake_storage

    return app, client, filename


def test_take_peaks_hosted_mode_pending_when_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /raw-videos/peaks returns 202 in hosted mode when peaks not in storage.

    With no active detect_beep job, active_job must be False so the SPA can
    offer a manual re-run rather than spinning indefinitely.
    """
    app, client, filename = _setup_hosted_peaks(tmp_path)

    # When state.storage is not None and the middleware sets the match_id,
    # shooter_project() binds scope=f"matches/{match_id}/shooters/{slug}".
    # That makes project._storage_scope non-None so the hosted path is taken.
    resp = client.get(f"/api/shooters/me/raw-videos/peaks?filename={filename}&bins=100")
    assert resp.status_code == 202
    body = resp.json()
    assert body["pending"] is True
    # No active detect_beep job - SPA should offer re-run, not spin.
    assert body["active_job"] is False


def test_take_peaks_hosted_mode_active_job_true(tmp_path: Path) -> None:
    """GET /raw-videos/peaks returns 202 with active_job=True when a detect_beep job is pending.

    Submits a blocking detect_beep job for stage 1 (one of the stages covered
    by the take) so find_active returns a non-None job while the GET is in flight.
    """
    import asyncio
    import threading

    from tests.conftest import submit_fn

    app, client, filename = _setup_hosted_peaks(tmp_path, project_subdir="match2", filename="VID2.mp4")

    state = app.state.splitsmith_state

    # Submit a detect_beep job for stage 1 that blocks until we release it.
    gate = threading.Event()

    def _blocking_worker(handle):  # noqa: ANN001
        gate.wait(timeout=10)

    job_id = asyncio.run(
        submit_fn(
            state.jobs,
            kind="detect_beep",
            fn=_blocking_worker,
            stage_number=1,
        )
    )
    assert job_id is not None

    try:
        resp = client.get(f"/api/shooters/me/raw-videos/peaks?filename={filename}&bins=100")
        assert resp.status_code == 202
        body = resp.json()
        assert body["pending"] is True
        assert body["active_job"] is True
    finally:
        gate.set()  # let the worker thread finish so the test teardown is clean
