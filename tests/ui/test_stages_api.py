"""Tests for the per-stage routes (``ui/stages_api.py``)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np
import pytest

from splitsmith.match_project import MatchProject, StageEntry
from tests.media_bytes import tiny_mp4
from tests.ui._helpers import (
    _BEEP_FIXTURE_TRUTH,
    _as_placement,
    _export_csv_only,
    _fake_ensemble_result,
    _match_create_app,
    _MatchClient,
    _primary_cache_paths,
    _seed_match_export_project,
    _seed_project_with_primary,
    _seed_project_with_secondary,
    _seed_second_video,
    _seed_trims_only_stage,
    _stub_detect,
    _stub_video_audio_with_fixture,
    _three_candidates,
    _video_id_for,
    _wait_for_job,
    _wait_for_jobs_to_drain,
)


def test_detect_beep_persists_auto_result(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=12.453)

    resp = client.post("/api/shooters/me/stages/1/detect-beep")
    assert resp.status_code == 200
    job = resp.json()
    assert job["kind"] == "detect_beep"
    final = _wait_for_job(client, job["id"])
    assert final["status"] == "succeeded", final
    primary = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    assert primary["beep_time"] == 12.453
    assert primary["beep_source"] == "auto"
    assert primary["beep_peak_amplitude"] == 0.42
    assert primary["beep_duration_ms"] == 320.0
    assert primary["processed"]["beep"] is True


def test_detect_beep_409_over_manual_unless_forced(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    # User has already set a manual override.
    client.post("/api/shooters/me/stages/1/beep", json={"beep_time": 12.5})
    _stub_detect(monkeypatch, beep_time=99.0)

    resp = client.post("/api/shooters/me/stages/1/detect-beep")
    assert resp.status_code == 409
    primary = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    assert primary["beep_time"] == 12.5
    assert primary["beep_source"] == "manual"

    # ?force=true replaces it.
    resp = client.post("/api/shooters/me/stages/1/detect-beep?force=true")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final
    primary = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    assert primary["beep_time"] == 99.0
    assert primary["beep_source"] == "auto"


def test_detect_beep_400_when_no_primary(tmp_path: Path) -> None:
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
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
    client.post("/api/shooters/me/scoreboard/import", json={"data": sb})
    resp = client.post("/api/shooters/me/stages/1/detect-beep")
    assert resp.status_code == 400


def test_override_beep_sets_manual_source(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.post("/api/shooters/me/stages/1/beep", json={"beep_time": 12.5})
    assert resp.status_code == 200
    primary = resp.json()["stages"][0]["videos"][0]
    assert primary["beep_time"] == 12.5
    assert primary["beep_source"] == "manual"
    assert primary["processed"]["beep"] is True


def test_override_beep_clears_with_null(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=10.0)
    client.post("/api/shooters/me/stages/1/detect-beep")

    resp = client.post("/api/shooters/me/stages/1/beep", json={"beep_time": None})
    assert resp.status_code == 200
    primary = resp.json()["stages"][0]["videos"][0]
    assert primary["beep_time"] is None
    assert primary["beep_source"] is None
    assert primary["processed"]["beep"] is False


def test_override_beep_400_on_negative(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.post("/api/shooters/me/stages/1/beep", json={"beep_time": -1.0})
    assert resp.status_code == 400


def test_set_stage_time_manual_unblocks_no_scoreboard_project(tmp_path: Path) -> None:
    """Placeholder stage with no scoreboard data: manual time entry stamps
    time_seconds + flags it so a later sync won't clobber, and unblocks the
    trim / shot-detect gates that look at ``time_seconds > 0``."""
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)
    client.post(
        "/api/shooters/me/project/placeholder-stages",
        json={"stage_count": 1, "match_name": "Manual Times"},
    )
    resp = client.post("/api/shooters/me/stages/1/time", json={"time_seconds": 18.42})
    assert resp.status_code == 200
    stage = resp.json()["stages"][0]
    assert stage["time_seconds"] == 18.42
    assert stage["time_seconds_manual"] is True


def test_set_stage_time_null_clears_back_to_placeholder(tmp_path: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)
    client.post("/api/shooters/me/project/placeholder-stages", json={"stage_count": 1})
    client.post("/api/shooters/me/stages/1/time", json={"time_seconds": 12.0})
    resp = client.post("/api/shooters/me/stages/1/time", json={"time_seconds": None})
    assert resp.status_code == 200
    stage = resp.json()["stages"][0]
    assert stage["time_seconds"] == 0.0
    assert stage["time_seconds_manual"] is False


def test_set_stage_time_400_on_non_positive(tmp_path: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)
    client.post("/api/shooters/me/project/placeholder-stages", json={"stage_count": 1})
    for bad in (0.0, -1.0):
        resp = client.post("/api/shooters/me/stages/1/time", json={"time_seconds": bad})
        assert resp.status_code == 400, f"expected 400 for {bad}, got {resp.status_code}"


def test_detect_beep_persists_candidate_list(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=12.453, candidates=_three_candidates())

    resp = client.post("/api/shooters/me/stages/1/detect-beep")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final
    primary = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    assert len(primary["beep_candidates"]) == 3
    assert primary["beep_candidates"][0]["time"] == 12.453
    assert primary["beep_candidates"][1]["time"] == 8.100


def test_select_beep_candidate_promotes_alternate(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=12.453, candidates=_three_candidates())
    final = _wait_for_job(client, client.post("/api/shooters/me/stages/1/detect-beep").json()["id"])
    assert final["status"] == "succeeded"

    resp = client.post("/api/shooters/me/stages/1/beep/select", json={"time": 8.100})
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
    _wait_for_job(client, client.post("/api/shooters/me/stages/1/detect-beep").json()["id"])

    resp = client.post("/api/shooters/me/stages/1/beep/select", json={"time": 8.1004})
    assert resp.status_code == 200
    primary = resp.json()["stages"][0]["videos"][0]
    assert primary["beep_time"] == 8.100


def test_select_beep_candidate_400_when_no_candidates(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.post("/api/shooters/me/stages/1/beep/select", json={"time": 0.0})
    assert resp.status_code == 400


def test_select_beep_candidate_400_when_time_no_match(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=12.453, candidates=_three_candidates())
    _wait_for_job(client, client.post("/api/shooters/me/stages/1/detect-beep").json()["id"])

    resp = client.post("/api/shooters/me/stages/1/beep/select", json={"time": 99.999})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    # The server lists what's available so the user can copy a real time.
    assert "12.453" in detail
    assert "8.100" in detail


def test_snap_beep_refines_user_hint_to_truth(tmp_path: Path, monkeypatch) -> None:
    """User dropped a marker ~70 ms after the real beep; the snap endpoint
    pulls it back to the rise-foot leading edge within tolerance."""
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_video_audio_with_fixture(monkeypatch)

    primary = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    video_id = primary["video_id"]

    hint = _BEEP_FIXTURE_TRUTH + 0.072
    resp = client.post(
        f"/api/shooters/me/stages/1/videos/{video_id}/beep/snap",
        json={"hint_time": hint, "window_s": 0.5},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 20 ms tolerance matches the fixture's ground-truth tolerance.
    assert abs(body["snapped_time"] - _BEEP_FIXTURE_TRUTH) <= 0.020
    assert body["delta"] == pytest.approx(body["snapped_time"] - hint, abs=1e-9)
    assert body["peak_amplitude"] > 0.0
    assert body["duration_ms"] > 0.0


def test_snap_beep_400_on_negative_hint(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_video_audio_with_fixture(monkeypatch)
    primary = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    resp = client.post(
        f"/api/shooters/me/stages/1/videos/{primary['video_id']}/beep/snap",
        json={"hint_time": -0.1, "window_s": 0.5},
    )
    assert resp.status_code == 400


def test_snap_beep_400_on_zero_window(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_video_audio_with_fixture(monkeypatch)
    primary = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    resp = client.post(
        f"/api/shooters/me/stages/1/videos/{primary['video_id']}/beep/snap",
        json={"hint_time": 4.0, "window_s": 0.0},
    )
    assert resp.status_code == 400


def test_snap_beep_404_when_window_excludes_beep(tmp_path: Path, monkeypatch) -> None:
    """A hint nowhere near the actual beep with a tight window returns 404
    so the SPA can prompt the user to widen the window or move the marker."""
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_video_audio_with_fixture(monkeypatch)
    primary = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    # Place the hint into the post-beep silence; 50 ms half-window has
    # nothing crossing the run-duration threshold.
    resp = client.post(
        f"/api/shooters/me/stages/1/videos/{primary['video_id']}/beep/snap",
        json={"hint_time": 0.30, "window_s": 0.05},
    )
    assert resp.status_code == 404


def test_manual_override_clears_candidate_list(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=12.453, candidates=_three_candidates())
    _wait_for_job(client, client.post("/api/shooters/me/stages/1/detect-beep").json()["id"])

    resp = client.post("/api/shooters/me/stages/1/beep", json={"beep_time": 12.5})
    assert resp.status_code == 200
    primary = resp.json()["stages"][0]["videos"][0]
    assert primary["beep_source"] == "manual"
    assert primary["beep_candidates"] == []


def test_audio_endpoint_serves_cached_wav(tmp_path: Path, monkeypatch) -> None:
    """With no trim, /audio serves the primary's full WAV a job published."""
    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)

    project_root = tmp_path / "match"
    full_wav, _audit, _trim = _primary_cache_paths(project_root)
    full_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(full_wav, np.zeros(4_800, dtype="float32"), 48_000)

    resp = client.get("/api/shooters/me/stages/1/audio")
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
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 12.5
    project.save(_shooter_root)

    # Drop a fake clip in the thumbs cache and stub ensure_clip to return it.
    thumbs_dir = _shooter_root / "thumbs"
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

    resp = client.get("/api/shooters/me/stages/1/beep-preview")
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
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 12.5
    project.save(_shooter_root)

    thumbs_dir = _shooter_root / "thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    fake_clip = thumbs_dir / "fake_t27500_d1000.mp4"
    fake_clip.write_bytes(b"\x00\x00\x00\x18ftypmp42fake-mp4")

    captured: dict = {}

    def fake_ensure_clip(source, *, cache_dir, center_time, duration_s=1.0, **kwargs):  # type: ignore[no-untyped-def]
        captured["center_time"] = center_time
        return fake_clip

    from splitsmith.ui import server as server_module

    monkeypatch.setattr(server_module.thumbnail_helpers, "ensure_clip", fake_ensure_clip)

    resp = client.get("/api/shooters/me/stages/1/beep-preview?t=27.5")
    assert resp.status_code == 200
    assert captured["center_time"] == pytest.approx(27.5)
    # primary.beep_time is unchanged.
    after = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    assert after["beep_time"] == 12.5
    # ``src`` is the seeded source video; sanity-check it exists.
    assert src.exists()


def test_beep_preview_400_on_negative_t(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.save(_shooter_root)

    resp = client.get("/api/shooters/me/stages/1/beep-preview?t=-1")
    assert resp.status_code == 400


def test_beep_preview_404_when_no_beep(tmp_path: Path) -> None:
    """No beep_time yet -> 404 with a clear detail. The SPA hides the preview
    until detection has run."""
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.get("/api/shooters/me/stages/1/beep-preview")
    assert resp.status_code == 404
    assert "beep_time" in resp.json()["detail"]


def test_beep_preview_404_when_no_primary(tmp_path: Path) -> None:
    """A stage without a primary has no source video to clip from."""
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
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
    client.post("/api/shooters/me/scoreboard/import", json={"data": sb})
    resp = client.get("/api/shooters/me/stages/1/beep-preview")
    assert resp.status_code == 404


def test_beep_preview_500_on_ffmpeg_failure(tmp_path: Path, monkeypatch) -> None:
    """ensure_clip raises on missing ffmpeg / encode failure -- surface as
    500 so the SPA can fall back to the 'preview unavailable' hint."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.save(_shooter_root)

    from splitsmith import thumbnail
    from splitsmith.ui import server as server_module

    def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise thumbnail.ThumbnailError("ffmpeg exploded")

    monkeypatch.setattr(server_module.thumbnail_helpers, "ensure_clip", boom)
    resp = client.get("/api/shooters/me/stages/1/beep-preview")
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
    _shooter_root = project_root / "shooters" / "me"

    # Stage primary already has beep_time set by the seeder. Force-set to a
    # known value so we can assert the clip-local beep math.
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 30.0  # source-time beep; trim default buffer is 5s
    project.save(_shooter_root)

    # A trim and the audit WAV the trim job published beside it.
    from tests.media_bytes import tiny_mp4

    trimmed_dir = _shooter_root / "trimmed"
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    _full, audit_wav, trimmed_mp4 = _primary_cache_paths(project_root)
    trimmed_mp4.write_bytes(tiny_mp4())

    audio_dir = _shooter_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio = np.zeros(48_000, dtype="float32")
    audio[20_000:21_000] = 0.7
    sf.write(audit_wav, audio, 48_000)

    resp = client.get("/api/shooters/me/stages/1/peaks?bins=64")
    assert resp.status_code == 200
    body = resp.json()
    assert body["trimmed"] is True
    # beep_time at 30s in source -> clamped to buffer (5.0s) inside clip.
    assert body["beep_time"] == pytest.approx(5.0)
    assert len(body["peaks"]) == 64
    assert body["duration"] == pytest.approx(1.0)


def test_video_peaks_endpoint_serves_secondary_full_wav(tmp_path: Path, monkeypatch) -> None:
    """The per-video peaks endpoint resolves the secondary's own WAV
    (``stage<N>_cam_<vid>.wav``) and returns the same payload shape as
    the stage-level endpoint, so the SPA's waveform picker can take the
    same code path for primary + secondary."""
    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_secondary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    audio_dir = _shooter_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    sec_id = _video_id_for(client, 1, "secondary")
    sec_wav = audio_dir / f"stage1_cam_{sec_id}.wav"
    audio = np.zeros(48_000, dtype="float32")
    audio[10_000:11_000] = 0.5
    sf.write(sec_wav, audio, 48_000)

    from splitsmith.ui import audio as audio_helpers

    def fake_ensure_video(root, n, video, source, **kwargs):  # type: ignore[no-untyped-def]
        return sec_wav

    monkeypatch.setattr(audio_helpers, "ensure_video_audio", fake_ensure_video)

    # Set a beep_time on the secondary so beep_time is reflected in the payload.
    proj = client.post(
        f"/api/shooters/me/stages/1/videos/{sec_id}/beep",
        json={"beep_time": 0.42},
    ).json()
    assert next(v for v in proj["stages"][0]["videos"] if v["video_id"] == sec_id)[
        "beep_time"
    ] == pytest.approx(0.42)

    resp = client.get(f"/api/shooters/me/stages/1/videos/{sec_id}/peaks?bins=64")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bins"] == 64
    assert len(body["peaks"]) == 64
    # No per-secondary trim exists -> trimmed=False, beep_time = source time.
    assert body["trimmed"] is False
    assert body["beep_time"] == pytest.approx(0.42)


def test_video_peaks_endpoint_serves_a_window_at_fine_resolution(tmp_path: Path, monkeypatch) -> None:
    """``start``/``end`` ask for one window of the video's WAV: the beep
    picker's 1 ms detail around the beep (2026-10-13: 1,200 bins over the
    whole source were 25-50 ms bars, too coarse to place a beep)."""
    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_secondary(tmp_path)
    audio_dir = tmp_path / "match" / "shooters" / "me" / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    sec_id = _video_id_for(client, 1, "secondary")
    sec_wav = audio_dir / f"stage1_cam_{sec_id}.wav"
    audio = np.zeros(48_000, dtype="float32")
    audio[24_000:24_480] = 0.5  # 0.500-0.510 s
    sf.write(sec_wav, audio, 48_000)

    from splitsmith.ui import audio as audio_helpers

    monkeypatch.setattr(audio_helpers, "ensure_video_audio", lambda *a, **k: sec_wav)

    resp = client.get(f"/api/shooters/me/stages/1/videos/{sec_id}/peaks?bins=200&start=0.4&end=0.6")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["start"] == pytest.approx(0.4)
    assert body["duration"] == pytest.approx(0.2)
    loud = [i for i, p in enumerate(body["peaks"]) if p > 0.3]
    assert loud and loud[0] == 100  # 0.5 s is 100 ms into the window
    assert "source_size" not in body


def test_video_audio_endpoint_serves_secondary_wav(tmp_path: Path, monkeypatch) -> None:
    """The per-video audio endpoint returns the secondary's own WAV bytes."""
    client, _ = _seed_project_with_secondary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    audio_dir = _shooter_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    sec_id = _video_id_for(client, 1, "secondary")
    sec_wav = audio_dir / f"stage1_cam_{sec_id}.wav"
    import numpy as np
    import soundfile as sf

    sf.write(sec_wav, np.zeros(4_800, dtype="float32"), 48_000)

    resp = client.get(f"/api/shooters/me/stages/1/videos/{sec_id}/audio")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    assert resp.content.startswith(b"RIFF")


def test_video_peaks_endpoint_serves_primary_full_wav_even_when_trimmed_exists(
    tmp_path: Path, monkeypatch
) -> None:
    """The per-video picker endpoint always returns the full source WAV,
    even for primaries that already have a trimmed audit clip cached.

    Regression: the picker is where the user *questions* the current beep,
    so a beep that fell outside the trim window must still be visible on
    the waveform. The audit screen reaches for the trimmed clip via the
    per-stage endpoint -- different consumer, different cache.
    """
    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    audio_dir = _shooter_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    primary = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    primary_id = primary["video_id"]

    # Cache both the trimmed audit WAV (short) and the full primary WAV
    # (long). If the picker mistakenly uses the audit clip the response
    # duration will be 1s; with the fix it's 2s.
    primary_wav, audit_wav, trimmed_mp4 = _primary_cache_paths(project_root)
    sf.write(audit_wav, np.zeros(48_000, dtype="float32"), 48_000)
    sf.write(primary_wav, np.zeros(96_000, dtype="float32"), 48_000)

    trimmed_dir = _shooter_root / "trimmed"
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    trimmed_mp4.write_bytes(b"\x00fake_mp4")

    from splitsmith.ui import audio as audio_helpers

    def fake_ensure_video(root, n, video, source, **kwargs):  # type: ignore[no-untyped-def]
        return primary_wav

    monkeypatch.setattr(audio_helpers, "ensure_video_audio", fake_ensure_video)

    resp = client.get(f"/api/shooters/me/stages/1/videos/{primary_id}/peaks?bins=64")
    assert resp.status_code == 200
    body = resp.json()
    assert body["trimmed"] is False
    assert body["duration"] == pytest.approx(2.0)


def test_video_peaks_endpoint_404_for_unknown_video(tmp_path: Path) -> None:
    """The per-video endpoint 404s when video_id isn't on the stage --
    same shape as the per-video beep endpoints."""
    client, _ = _seed_project_with_secondary(tmp_path)
    resp = client.get("/api/shooters/me/stages/1/videos/deadbeef0000/peaks")
    assert resp.status_code == 404


def test_peaks_endpoint_falls_back_to_full_when_no_trim(tmp_path: Path, monkeypatch) -> None:
    """No trimmed file -> /peaks serves the full primary WAV. Response says
    trimmed=false so the SPA can hint that the user is on an untrimmed clip."""
    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    audio_dir = _shooter_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav, _audit, _trim = _primary_cache_paths(project_root)
    audio = np.zeros(24_000, dtype="float32")
    audio[5_000:6_000] = 0.3
    sf.write(wav, audio, 48_000)

    from splitsmith.ui import audio as audio_helpers

    def fake_ensure(root, n, source, **kwargs):  # type: ignore[no-untyped-def]
        return wav

    monkeypatch.setattr(audio_helpers, "ensure_primary_audio", fake_ensure)

    resp = client.get("/api/shooters/me/stages/1/peaks?bins=64")
    assert resp.status_code == 200
    body = resp.json()
    assert body["trimmed"] is False


def test_peaks_endpoint_returns_normalized_bins(tmp_path: Path, monkeypatch) -> None:
    """/peaks asks the audio helper for the cached WAV, then computes peaks.

    We stub ``ensure_primary_audio`` to return a real WAV written by
    ``soundfile`` so the peaks pipeline runs end-to-end without ffmpeg.
    """
    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    audio_dir = _shooter_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav, _audit, _trim = _primary_cache_paths(project_root)
    audio = np.zeros(48_000, dtype="float32")
    audio[10_000:11_000] = 0.5
    sf.write(wav, audio, 48_000)

    from splitsmith.ui import audio as audio_helpers

    def fake_ensure(root, n, source, **kwargs):  # type: ignore[no-untyped-def]
        return wav

    monkeypatch.setattr(audio_helpers, "ensure_primary_audio", fake_ensure)

    resp = client.get("/api/shooters/me/stages/1/peaks?bins=64")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bins"] == 64
    assert len(body["peaks"]) == 64
    assert all(0.0 <= p <= 1.0 for p in body["peaks"])
    assert body["duration"] == pytest.approx(1.0)


def test_peaks_endpoint_404_when_no_primary(tmp_path: Path) -> None:
    """A stage without a primary video can't have peaks; surface 404 cleanly."""
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
    project = MatchProject.load(_shooter_root)
    project.init_placeholder_stages(2)
    project.save(_shooter_root)

    resp = client.get("/api/shooters/me/stages/1/peaks")
    assert resp.status_code == 404
    assert "no primary" in resp.json()["detail"]


def test_detect_beep_auto_trims(tmp_path: Path, monkeypatch) -> None:
    """After a successful beep detect, the production UI chains the audit-mode
    trim (a ``trim`` job since #1680) so the audit screen lands with
    frame-accurate scrubbing."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    project.stages[0].time_seconds = 12.0
    project.save(_shooter_root)

    resolved = project.resolve_video_path(_shooter_root, primary.path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_bytes(b"FAKE_SOURCE_MP4")

    from splitsmith import trim
    from splitsmith.beep import detect as beep_detect
    from splitsmith.ui import audio as audio_helpers

    class FakeBeep:
        time = 6.5
        peak_amplitude = 0.42
        duration_ms = 110.0
        confidence = 0.85
        candidates: list = []

    monkeypatch.setattr(audio_helpers, "ensure_primary_audio", lambda *a, **kw: tmp_path / "x.wav")
    (tmp_path / "x.wav").write_bytes(b"\x00")
    monkeypatch.setattr(beep_detect, "load_audio", lambda p: ([0.0] * 100, 48_000))
    monkeypatch.setattr(beep_detect, "detect_beep", lambda *a, **kw: FakeBeep())

    trim_calls: list[dict] = []

    def fake_trim_video(**kwargs):  # type: ignore[no-untyped-def]
        trim_calls.append(kwargs)
        Path(kwargs["output_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["output_path"]).write_bytes(tiny_mp4())
        return trim.TrimResult(output_path=kwargs["output_path"], start_time=1.5, end_time=23.5)

    monkeypatch.setattr(trim, "trim_video", fake_trim_video)

    resp = client.post("/api/shooters/me/stages/1/detect-beep")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final
    jobs = _wait_for_jobs_to_drain(client)
    assert [j["status"] for j in jobs if j["kind"] == "trim"] == ["succeeded"]
    project_after = client.get("/api/shooters/me/project").json()
    assert project_after["stages"][0]["videos"][0]["beep_time"] == pytest.approx(6.5)
    assert project_after["stages"][0]["videos"][0]["processed"]["trim"] is True
    _full, _audit, expected_trim = _primary_cache_paths(project_root)
    assert expected_trim.exists()
    assert len(trim_calls) == 1
    assert trim_calls[0]["mode"] == "audit"
    assert trim_calls[0]["beep_time"] == pytest.approx(6.5)
    assert trim_calls[0]["stage_time"] == pytest.approx(12.0)


def test_detect_beep_high_confidence_auto_trusts_into_beep_reviewed(tmp_path: Path, monkeypatch) -> None:
    """confidence >= threshold flips beep_reviewed to True without a click (#219)."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    project.stages[0].time_seconds = 12.0
    project.save(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"X")

    from splitsmith import trim
    from splitsmith.beep import detect as beep_detect
    from splitsmith.ui import audio as audio_helpers

    class FakeBeep:
        time = 6.5
        peak_amplitude = 0.42
        duration_ms = 350.0
        confidence = 0.98  # above the 0.97 default threshold
        candidates: list = []
        ranker_version = "beep-ranker-lr-test"

    monkeypatch.setattr(audio_helpers, "ensure_primary_audio", lambda *a, **kw: tmp_path / "z.wav")
    (tmp_path / "z.wav").write_bytes(b"\x00")
    monkeypatch.setattr(beep_detect, "load_audio", lambda p: ([0.0] * 100, 48_000))
    monkeypatch.setattr(beep_detect, "detect_beep", lambda *a, **kw: FakeBeep())
    monkeypatch.setattr(
        trim,
        "trim_video",
        lambda **kw: trim.TrimResult(output_path=Path(kw["output_path"]), start_time=1.0, end_time=20.0),
    )
    Path(_shooter_root / "trimmed").mkdir(parents=True, exist_ok=True)

    resp = client.post("/api/shooters/me/stages/1/detect-beep")
    assert resp.status_code == 200
    _wait_for_job(client, resp.json()["id"])
    primary_after = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    assert primary_after["beep_confidence"] == pytest.approx(0.98)
    assert primary_after["beep_reviewed"] is True
    # Which ranker chose it rides with the stored candidates (#949).
    assert primary_after["beep_ranker_version"] == "beep-ranker-lr-test"


def test_detect_beep_low_confidence_leaves_beep_for_hitl(tmp_path: Path, monkeypatch) -> None:
    """Below-threshold beep keeps beep_reviewed=False so it lands in HITL queue."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    project.stages[0].time_seconds = 12.0
    project.save(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"X")

    from splitsmith import trim
    from splitsmith.beep import detect as beep_detect
    from splitsmith.ui import audio as audio_helpers

    class FakeBeep:
        time = 6.5
        peak_amplitude = 0.42
        duration_ms = 200.0
        confidence = 0.45  # below default threshold of 0.6
        candidates: list = []

    monkeypatch.setattr(audio_helpers, "ensure_primary_audio", lambda *a, **kw: tmp_path / "w.wav")
    (tmp_path / "w.wav").write_bytes(b"\x00")
    monkeypatch.setattr(beep_detect, "load_audio", lambda p: ([0.0] * 100, 48_000))
    monkeypatch.setattr(beep_detect, "detect_beep", lambda *a, **kw: FakeBeep())
    monkeypatch.setattr(
        trim,
        "trim_video",
        lambda **kw: trim.TrimResult(output_path=Path(kw["output_path"]), start_time=1.0, end_time=20.0),
    )
    Path(_shooter_root / "trimmed").mkdir(parents=True, exist_ok=True)

    resp = client.post("/api/shooters/me/stages/1/detect-beep")
    assert resp.status_code == 200
    _wait_for_job(client, resp.json()["id"])
    primary_after = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    assert primary_after["beep_confidence"] == pytest.approx(0.45)
    assert primary_after["beep_reviewed"] is False
    # And it should now show up in the HITL queue.
    queue = client.get("/api/shooters/me/hitl-queue").json()
    assert len(queue["items"]) == 1
    assert queue["items"][0]["kind"] == "beep_low_confidence"


def test_detect_beep_skips_trim_when_stage_time_zero(tmp_path: Path, monkeypatch) -> None:
    """Source-first / placeholder stages with time_seconds=0 (no scoreboard
    yet) should still let the user detect a beep -- trim just gets deferred
    until a stage time is known."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    project.stages[0].time_seconds = 0.0
    project.save(_shooter_root)

    primary = project.stages[0].primary()
    assert primary is not None
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"X")

    from splitsmith import trim
    from splitsmith.beep import detect as beep_detect
    from splitsmith.ui import audio as audio_helpers

    class FakeBeep:
        time = 4.0
        peak_amplitude = 0.5
        duration_ms = 100.0
        confidence = 0.85
        candidates: list = []

    monkeypatch.setattr(audio_helpers, "ensure_primary_audio", lambda *a, **kw: tmp_path / "y.wav")
    (tmp_path / "y.wav").write_bytes(b"\x00")
    monkeypatch.setattr(beep_detect, "load_audio", lambda p: ([0.0] * 100, 48_000))
    monkeypatch.setattr(beep_detect, "detect_beep", lambda *a, **kw: FakeBeep())

    called = []
    monkeypatch.setattr(trim, "trim_video", lambda **kw: called.append(kw))

    resp = client.post("/api/shooters/me/stages/1/detect-beep")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final
    project_after = client.get("/api/shooters/me/project").json()
    assert project_after["stages"][0]["videos"][0]["beep_time"] == pytest.approx(4.0)
    assert project_after["stages"][0]["videos"][0]["processed"]["trim"] is False
    assert called == []


def test_post_trim_endpoint_produces_clip(tmp_path: Path, monkeypatch) -> None:
    """Manual /trim endpoint runs audit-mode trim and flips processed.trim."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(_shooter_root)
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"S")

    from splitsmith import trim

    def fake_trim_video(**kwargs):  # type: ignore[no-untyped-def]
        Path(kwargs["output_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["output_path"]).write_bytes(tiny_mp4())
        return trim.TrimResult(output_path=kwargs["output_path"], start_time=0.0, end_time=20.0)

    monkeypatch.setattr(trim, "trim_video", fake_trim_video)

    resp = client.post("/api/shooters/me/stages/1/trim")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final
    project_after = client.get("/api/shooters/me/project").json()
    assert project_after["stages"][0]["videos"][0]["processed"]["trim"] is True
    _full, _audit, expected_trim = _primary_cache_paths(project_root)
    assert expected_trim.exists()


def test_trim_invalidates_when_beep_changes(tmp_path: Path, monkeypatch) -> None:
    """Changing beep_time without touching the source must re-encode the
    trim. Cache key includes a sidecar params JSON so a new beep is
    detected even when the source mtime is unchanged."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(_shooter_root)
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"S")

    from splitsmith import trim

    invocations: list[dict] = []

    def fake_trim_video(**kwargs):  # type: ignore[no-untyped-def]
        invocations.append(kwargs)
        out = Path(kwargs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(tiny_mp4())
        return trim.TrimResult(output_path=out, start_time=0.0, end_time=15.0)

    monkeypatch.setattr(trim, "trim_video", fake_trim_video)

    # Run #1: cold cache, ffmpeg fires.
    resp = client.post("/api/shooters/me/stages/1/trim")
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded"
    assert len(invocations) == 1

    # Run #2: same params -> cache hit, ffmpeg does NOT fire again.
    resp = client.post("/api/shooters/me/stages/1/trim")
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded"
    assert len(invocations) == 1, "second run should be a cache hit"

    # Change beep_time via manual override. override_beep clears the trim
    # cache and auto-fires a re-trim job; we wait for it to finish.
    resp = client.post("/api/shooters/me/stages/1/beep", json={"beep_time": 7.5})
    assert resp.status_code == 200
    # Pick the most-recently-submitted trim job, not the first match
    # in the list. ``_wait_for_job`` on Run #1 (already SUCCEEDED)
    # would return immediately and race the auto-fired Run #3
    # worker; the latest entry is the one we actually need to wait
    # on. (JobRegistry preserves insertion order in ``list()``.)
    jobs = client.get("/api/me/jobs").json()
    trims = [j for j in jobs if j["kind"] == "trim" and j["stage_number"] == 1]
    assert trims, "override_beep should auto-fire a trim job"
    final = _wait_for_job(client, trims[-1]["id"])
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
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(_shooter_root)
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"S")

    from splitsmith import trim

    captured: list[Path] = []

    def fake_trim_video(**kwargs):  # type: ignore[no-untyped-def]
        out = Path(kwargs["output_path"])
        captured.append(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(tiny_mp4())
        return trim.TrimResult(output_path=out, start_time=0.0, end_time=15.0)

    monkeypatch.setattr(trim, "trim_video", fake_trim_video)

    resp = client.post("/api/shooters/me/stages/1/trim")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final
    assert captured, "trim_video should have been invoked"
    assert captured[0].suffix == ".mp4", (
        f"partial path must keep .mp4 so ffmpeg infers the muxer; " f"got {captured[0].name}"
    )
    assert ".partial" in captured[0].stem


def test_trim_endpoint_returns_existing_job_when_one_is_running(tmp_path: Path, monkeypatch) -> None:
    """A second submit while the first is still running adopts the existing
    job instead of spawning a parallel ffmpeg that races on the partial
    file. Symptom we're guarding against: clicking "Trim now" twice (or
    after a reload) shouldn't double-submit."""
    import threading

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(_shooter_root)
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"S")

    from splitsmith import trim

    proceed = threading.Event()
    invocations = []

    def slow_trim(**kwargs):  # type: ignore[no-untyped-def]
        invocations.append(kwargs)
        proceed.wait(timeout=2.0)
        out = Path(kwargs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(tiny_mp4())
        return trim.TrimResult(output_path=out, start_time=0.0, end_time=15.0)

    monkeypatch.setattr(trim, "trim_video", slow_trim)

    first = client.post("/api/shooters/me/stages/1/trim").json()
    second = client.post("/api/shooters/me/stages/1/trim").json()
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
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(_shooter_root)
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"S")

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
            runner(["sleep", "30"], check=True, capture_output=True, text=True, encoding="utf-8")
        except JobCancelled:
            cancel_observed.set()
            raise
        out = Path(kwargs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(tiny_mp4())
        return trim.TrimResult(output_path=out, start_time=0.0, end_time=15.0)

    monkeypatch.setattr(trim, "trim_video", slow_trim)

    job = client.post("/api/shooters/me/stages/1/trim").json()
    assert started.wait(timeout=3.0), "worker should have started by now"

    cancel_resp = client.post(f"/api/me/jobs/{job['id']}/cancel")
    assert cancel_resp.status_code == 200
    snapshot = cancel_resp.json()
    assert snapshot["cancel_requested"] is True

    final = _wait_for_job(client, job["id"])
    assert final["status"] == "cancelled", final
    assert cancel_observed.is_set(), "worker must observe the cancel via JobCancelled"


def test_retry_endpoint_409_for_non_failed_job(tmp_path: Path, monkeypatch) -> None:
    """A succeeded job isn't retryable - retry() raises JobNotRetryableError,
    which the endpoint maps to 409."""
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=12.453)

    resp = client.post("/api/shooters/me/stages/1/detect-beep")
    assert resp.status_code == 200
    job_id = resp.json()["id"]
    final = _wait_for_job(client, job_id)
    assert final["status"] == "succeeded", final

    retry_resp = client.post(f"/api/me/jobs/{job_id}/retry")
    assert retry_resp.status_code == 409


def test_retry_endpoint_409_for_already_acknowledged_job(tmp_path: Path, monkeypatch) -> None:
    """``acknowledged`` doubles as the retry claim bit: a failed job that
    was already retried (or dismissed) can't be retried a second time."""
    client, _ = _seed_project_with_primary(tmp_path)
    from splitsmith.ui import audio as audio_helpers

    def boom(*a, **kw):  # type: ignore[no-untyped-def]
        raise audio_helpers.AudioExtractionError("kaboom")

    monkeypatch.setattr(audio_helpers, "detect_primary_beep", boom)
    monkeypatch.setattr(audio_helpers, "detect_video_beep", boom)

    submit = client.post("/api/shooters/me/stages/1/detect-beep")
    assert submit.status_code == 200
    job_id = submit.json()["id"]
    final = _wait_for_job(client, job_id)
    assert final["status"] == "failed", final

    ack = client.post(f"/api/me/jobs/{job_id}/acknowledge")
    assert ack.status_code == 200
    assert ack.json()["acknowledged"] is True

    retry_resp = client.post(f"/api/me/jobs/{job_id}/retry")
    assert retry_resp.status_code == 409


def test_retry_endpoint_reenqueues_failed_job_via_bare_path(tmp_path: Path, monkeypatch) -> None:
    """retry() rebinds the ORIGINAL submitting request's match context
    (stashed at submit time) before resubmitting, not whatever match
    context is ambient on the retry request itself - the jobs list is
    global (cross-match), so URL scoping the retry call must not matter.
    This test drives the ORIGINAL submit through the match-scoped client
    (as usual) but hits retry on the BARE ``/api/me/jobs/{id}/retry``
    path, with no alias prefix and no match ContextVars ambient, and
    asserts the retried job still reaches SUCCEEDED - proving the body
    ran with the original match's context, rebound server-side.
    """
    client, _ = _seed_project_with_primary(tmp_path)
    from splitsmith.config import BeepDetection
    from splitsmith.ui import audio as audio_helpers

    calls = {"n": 0}

    def flaky(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise audio_helpers.AudioExtractionError("transient failure")
        return BeepDetection(time=12.453, peak_amplitude=0.42, duration_ms=320.0, candidates=[])

    monkeypatch.setattr(audio_helpers, "detect_primary_beep", flaky)
    monkeypatch.setattr(audio_helpers, "detect_video_beep", flaky)

    submit = client.post("/api/shooters/me/stages/1/detect-beep")
    assert submit.status_code == 200
    job_id = submit.json()["id"]
    kind = submit.json()["kind"]
    first = _wait_for_job(client, job_id)
    assert first["status"] == "failed", first

    # Bare path: no alias prefix, so no match ContextVars are ambient on
    # this request. retry() must rebind the job's own original match.
    retry_resp = client.post(f"/api/me/jobs/{job_id}/retry")
    assert retry_resp.status_code == 200, retry_resp.text
    new = retry_resp.json()
    assert new["id"] != job_id
    assert new["kind"] == kind

    old = client.get(f"/api/me/jobs/{job_id}").json()
    assert old["acknowledged"] is True

    final = _wait_for_job(client, new["id"])
    assert final["status"] == "succeeded", final


def test_shot_detect_endpoint_writes_candidates(tmp_path: Path, monkeypatch) -> None:
    """The shot-detect job runs the 3-voter ensemble on the audit clip and
    merges per-voter signals into <project>/audit/stage<N>.json. Heavy
    models (CLAP, GBDT) are stubbed via the ensemble module entry
    points so the test runs offline."""
    import json as _json

    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(_shooter_root)
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"S")

    # Pretend the trim already produced an audit WAV.
    audio_dir = _shooter_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav = audio_dir / "stage1_audit.wav"
    sf.write(wav, np.zeros(48_000, dtype="float32"), 48_000)
    trimmed_dir = _shooter_root / "trimmed"
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
    # Skip CLAP/GBDT loading; the stub below ignores the runtime arg.
    monkeypatch.setattr(server_module, "_get_ensemble_runtime", lambda: None)
    fake_result = _fake_ensemble_result(
        [
            {"time": 5.5, "confidence": 0.8, "ensemble_score": 3.0, "kept": True},
            {"time": 6.1, "confidence": 0.6, "ensemble_score": 2.0, "kept": True},
            {"time": 6.9, "confidence": 0.9, "ensemble_score": 3.0, "kept": True},
        ],
        consensus=2,
    )
    monkeypatch.setattr(ensemble_module, "detect_shots_ensemble", lambda *a, **kw: fake_result)

    resp = client.post("/api/shooters/me/stages/1/shot-detect")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final
    audit_file = _shooter_root / "audit" / "stage1.json"
    assert audit_file.exists()
    saved = _json.loads(audit_file.read_text(encoding="utf-8"))
    block = saved["_candidates_pending_audit"]
    assert block["consensus"] == 2
    cands = block["candidates"]
    assert len(cands) == 3
    assert cands[0]["candidate_number"] == 1
    assert cands[0]["time"] == pytest.approx(5.5)
    assert cands[0]["ms_after_beep"] == 500
    # Per-voter signals should reach the audit JSON.
    assert cands[0]["vote_a"] == 1
    assert cands[0]["vote_total"] == 3
    assert "score_c" in cands[0]
    # shots[] is seeded from the consensus subset.
    shots = saved["shots"]
    assert len(shots) == 3
    assert shots[0]["source"] == "detected"
    assert shots[0]["ensemble_votes"] == 3
    # processed.shot_detect flips on the primary so the SPA can show status.
    proj_after = client.get("/api/shooters/me/project").json()
    assert proj_after["stages"][0]["videos"][0]["processed"]["shot_detect"] is True


def test_shot_detect_reset_records_a_delete_for_every_shot_it_wipes(tmp_path: Path, monkeypatch) -> None:
    """#842: a reset re-detect used to wipe ``shots[]`` in silence.

    ``audit_events`` is append-only and the sync merge reads it for shot
    membership, so a superseded shot whose id still carried a
    ``marker_kept`` stayed "present" as far as the merge was concerned
    and was re-adopted from the other side's document. Recording the
    delete makes the newest verdict for those ids the delete.

    The events must land *before* this run's ``shot_detect_run`` entry:
    they describe the state the run superseded, and the merge orders a
    shot's verdicts by ``ts``.
    """
    import json as _json

    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(_shooter_root)
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"S")

    audio_dir = _shooter_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav = audio_dir / "stage1_audit.wav"
    sf.write(wav, np.zeros(48_000, dtype="float32"), 48_000)
    trimmed_dir = _shooter_root / "trimmed"
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    (trimmed_dir / "stage1_trimmed.mp4").write_bytes(b"\x00")

    from splitsmith import ensemble as ensemble_module
    from splitsmith.ui import audio as audio_helpers
    from splitsmith.ui import server as server_module

    class FakeAudit:
        audio_path = wav
        beep_in_clip = 5.0
        trimmed = True

    monkeypatch.setattr(audio_helpers, "ensure_audit_audio", lambda *a, **kw: FakeAudit())
    monkeypatch.setattr(server_module, "_get_ensemble_runtime", lambda: None)
    fake_result = _fake_ensemble_result(
        [{"time": 5.5, "confidence": 0.8, "ensemble_score": 3.0, "kept": True}],
        consensus=2,
    )
    monkeypatch.setattr(ensemble_module, "detect_shots_ensemble", lambda *a, **kw: fake_result)

    # An audited stage, saved through the real boundary so its shots carry
    # ids -- the whole point is which ids the reset names.
    saved = client.put(
        "/api/shooters/me/stages/1/audit",
        json={
            "stage_number": 1,
            "beep_time": 5.0,
            "shots": [
                {"shot_number": 1, "candidate_number": 4, "time": 5.5, "source": "detected"},
                {"shot_number": 2, "candidate_number": 9, "time": 6.5, "source": "detected"},
            ],
            "audit_events": [{"ts": "2026-08-12T12:00:00Z", "kind": "save", "payload": {}}],
        },
    )
    assert saved.status_code == 200
    assert [s["id"] for s in saved.json()["shots"]] == ["cand-4", "cand-9"]

    resp = client.post("/api/shooters/me/stages/1/shot-detect?reset=true")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final

    doc = _json.loads((_shooter_root / "audit" / "stage1.json").read_text(encoding="utf-8"))
    events = doc["audit_events"]
    deleted = [e for e in events if e["kind"] == "marker_deleted"]
    assert [e["payload"]["id"] for e in deleted] == ["cand-4", "cand-9"]
    assert all(e["id"] for e in deleted), "every event needs an id -- the merge unions on it"
    kinds = [e["kind"] for e in events]
    assert kinds.index("marker_deleted") < kinds.index("shot_detect_run")
    # #1329: no auto proposal was dropped, so nothing claims one was.
    assert "events_reset" not in kinds


def test_shot_detect_reset_drops_auto_regions_and_keeps_manual_ones(tmp_path: Path, monkeypatch) -> None:
    """Stage events (spec 2026-10-08): a reset re-detect wipes the shots the
    seeder's proposals sat between, so the untouched ``auto`` proposals go
    and the stage may be seeded afresh; a region the user drew stays."""
    import json as _json

    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(_shooter_root)
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"S")

    audio_dir = _shooter_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav = audio_dir / "stage1_audit.wav"
    sf.write(wav, np.zeros(48_000, dtype="float32"), 48_000)
    trimmed_dir = _shooter_root / "trimmed"
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    (trimmed_dir / "stage1_trimmed.mp4").write_bytes(b"\x00")

    from splitsmith import ensemble as ensemble_module
    from splitsmith.ui import audio as audio_helpers
    from splitsmith.ui import server as server_module

    class FakeAudit:
        audio_path = wav
        beep_in_clip = 5.0
        trimmed = True

    monkeypatch.setattr(audio_helpers, "ensure_audit_audio", lambda *a, **kw: FakeAudit())
    monkeypatch.setattr(server_module, "_get_ensemble_runtime", lambda: None)
    fake_result = _fake_ensemble_result(
        [{"time": 5.5, "confidence": 0.8, "ensemble_score": 3.0, "kept": True}],
        consensus=2,
    )
    monkeypatch.setattr(ensemble_module, "detect_shots_ensemble", lambda *a, **kw: fake_result)

    manual = {"id": "evt-2", "kind": "movement", "start": 0.2, "end": 0.9, "source": "manual"}
    saved = client.put(
        "/api/shooters/me/stages/1/audit",
        json={
            "stage_number": 1,
            "beep_time": 5.0,
            "shots": [
                {"shot_number": 1, "candidate_number": 4, "time": 5.5, "source": "detected"},
                {"shot_number": 2, "candidate_number": 9, "time": 6.5, "source": "detected"},
            ],
            "events": [
                {"id": "evt-1", "kind": "reload", "start": 0.5, "end": 1.5, "source": "auto"},
                manual,
            ],
            "events_seeded": True,
            "audit_events": [{"ts": "2026-08-12T12:00:00Z", "kind": "save", "payload": {}}],
        },
    )
    assert saved.status_code == 200, saved.text

    resp = client.post("/api/shooters/me/stages/1/shot-detect?reset=true")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final

    doc = _json.loads((_shooter_root / "audit" / "stage1.json").read_text(encoding="utf-8"))
    assert doc["events"] == [manual]
    assert "events_seeded" not in doc
    # #1329: the dropped proposals are logged, like the shots the reset wipes.
    reset_logs = [e for e in doc["audit_events"] if e["kind"] == "events_reset"]
    assert len(reset_logs) == 1
    assert reset_logs[0]["id"]
    assert reset_logs[0]["payload"] == {"count": 1, "ids": ["evt-1"], "reason": "shot_detect_reset"}
    kinds = [e["kind"] for e in doc["audit_events"]]
    assert kinds.index("events_reset") < kinds.index("shot_detect_run")


def test_shot_detect_without_reset_records_no_deletes(tmp_path: Path, monkeypatch) -> None:
    """The other half of #842's guard: a non-reset run supersedes nothing,
    so it must not claim to have deleted anything. ``shots[]`` is only
    seeded when empty, so an audited stage's shots survive untouched."""
    import json as _json

    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(_shooter_root)
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"S")

    audio_dir = _shooter_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav = audio_dir / "stage1_audit.wav"
    sf.write(wav, np.zeros(48_000, dtype="float32"), 48_000)
    trimmed_dir = _shooter_root / "trimmed"
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    (trimmed_dir / "stage1_trimmed.mp4").write_bytes(b"\x00")

    from splitsmith import ensemble as ensemble_module
    from splitsmith.ui import audio as audio_helpers
    from splitsmith.ui import server as server_module

    class FakeAudit:
        audio_path = wav
        beep_in_clip = 5.0
        trimmed = True

    monkeypatch.setattr(audio_helpers, "ensure_audit_audio", lambda *a, **kw: FakeAudit())
    monkeypatch.setattr(server_module, "_get_ensemble_runtime", lambda: None)
    fake_result = _fake_ensemble_result(
        [{"time": 5.5, "confidence": 0.8, "ensemble_score": 3.0, "kept": True}],
        consensus=2,
    )
    monkeypatch.setattr(ensemble_module, "detect_shots_ensemble", lambda *a, **kw: fake_result)

    saved = client.put(
        "/api/shooters/me/stages/1/audit",
        json={
            "stage_number": 1,
            "beep_time": 5.0,
            "shots": [{"shot_number": 1, "candidate_number": 4, "time": 5.5, "source": "detected"}],
            "audit_events": [{"ts": "2026-08-12T12:00:00Z", "kind": "save", "payload": {}}],
        },
    )
    assert saved.status_code == 200

    final = _wait_for_job(client, client.post("/api/shooters/me/stages/1/shot-detect").json()["id"])
    assert final["status"] == "succeeded", final

    doc = _json.loads((_shooter_root / "audit" / "stage1.json").read_text(encoding="utf-8"))
    assert [e for e in doc["audit_events"] if e["kind"] == "marker_deleted"] == []
    assert [s["id"] for s in doc["shots"]] == ["cand-4"]


def test_a_second_detection_run_never_reuses_a_candidate_number(tmp_path: Path, monkeypatch) -> None:
    """#842: ``cand-<n>`` must not name two different physical shots.

    The ensemble numbers its candidates 1..K every run, so before this a
    re-detect handed ``cand-1`` to whatever the new run's first candidate
    happened to be -- and the sync merge keys shot membership on that id.
    The second run now numbers from the document's high-water mark, so
    the alias cannot form.
    """
    import json as _json

    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(_shooter_root)
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"S")

    audio_dir = _shooter_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav = audio_dir / "stage1_audit.wav"
    sf.write(wav, np.zeros(48_000, dtype="float32"), 48_000)
    trimmed_dir = _shooter_root / "trimmed"
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    (trimmed_dir / "stage1_trimmed.mp4").write_bytes(b"\x00")

    from splitsmith import ensemble as ensemble_module
    from splitsmith.ui import audio as audio_helpers
    from splitsmith.ui import server as server_module

    class FakeAudit:
        audio_path = wav
        beep_in_clip = 5.0
        trimmed = True

    monkeypatch.setattr(audio_helpers, "ensure_audit_audio", lambda *a, **kw: FakeAudit())
    monkeypatch.setattr(server_module, "_get_ensemble_runtime", lambda: None)
    fake_result = _fake_ensemble_result(
        [
            {"time": 5.5, "confidence": 0.8, "ensemble_score": 3.0, "kept": True},
            {"time": 6.1, "confidence": 0.6, "ensemble_score": 2.0, "kept": True},
            {"time": 6.9, "confidence": 0.9, "ensemble_score": 3.0, "kept": True},
        ],
        consensus=2,
    )
    monkeypatch.setattr(ensemble_module, "detect_shots_ensemble", lambda *a, **kw: fake_result)
    audit_file = _shooter_root / "audit" / "stage1.json"

    first = _wait_for_job(client, client.post("/api/shooters/me/stages/1/shot-detect").json()["id"])
    assert first["status"] == "succeeded", first
    doc = _json.loads(audit_file.read_text(encoding="utf-8"))
    run_one = [c["candidate_number"] for c in doc["_candidates_pending_audit"]["candidates"]]
    assert run_one == [1, 2, 3], "a first run on a fresh stage still numbers from 1"
    assert [s["candidate_number"] for s in doc["shots"]] == [1, 2, 3]

    second = _wait_for_job(
        client, client.post("/api/shooters/me/stages/1/shot-detect?reset=true").json()["id"]
    )
    assert second["status"] == "succeeded", second
    doc = _json.loads(audit_file.read_text(encoding="utf-8"))
    run_two = [c["candidate_number"] for c in doc["_candidates_pending_audit"]["candidates"]]
    assert run_two == [4, 5, 6]
    assert set(run_one).isdisjoint(run_two), "the whole point: no id names two physical shots"
    # The reseeded shots carry the new numbers, so their derived ids match
    # the candidates they actually came from.
    assert [s["candidate_number"] for s in doc["shots"]] == [4, 5, 6]

    # And a third run clears the second, which is what makes it inductive:
    # the block is overwritten each run, so each run has to record numbers
    # the next one can clear.
    third = _wait_for_job(
        client, client.post("/api/shooters/me/stages/1/shot-detect?reset=true").json()["id"]
    )
    assert third["status"] == "succeeded", third
    doc = _json.loads(audit_file.read_text(encoding="utf-8"))
    assert [c["candidate_number"] for c in doc["_candidates_pending_audit"]["candidates"]] == [7, 8, 9]


def test_shot_detect_clears_beep_confirm_stub_marker(tmp_path: Path, monkeypatch) -> None:
    """A beep-confirm run seeds ``{"shots": [], "detection": "none"}`` and
    shot-detect merges its real results into that same doc rather than
    replacing it (issue: lost optimistic-lock races must re-merge, not
    clobber). If the stub sentinel survives the merge,
    ``stage_audit_status`` reads the now-real, shot_detect_run-logged doc
    as "no audit yet" forever -- the opposite failure mode from the one
    stubs exist to prevent."""
    import json as _json

    import numpy as np
    import soundfile as sf

    from splitsmith.match_project import StageStatus, is_stub_audit, stage_audit_status

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(_shooter_root)
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"S")

    # Seed the beep-confirm stub exactly as set_beep_reviewed does.
    audit_file = _shooter_root / "audit" / "stage1.json"
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    audit_file.write_text(_json.dumps({"shots": [], "detection": "none"}), encoding="utf-8")

    audio_dir = _shooter_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav = audio_dir / "stage1_audit.wav"
    sf.write(wav, np.zeros(48_000, dtype="float32"), 48_000)
    trimmed_dir = _shooter_root / "trimmed"
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    (trimmed_dir / "stage1_trimmed.mp4").write_bytes(b"\x00")

    from splitsmith import ensemble as ensemble_module
    from splitsmith.ui import audio as audio_helpers
    from splitsmith.ui import server as server_module

    class FakeAudit:
        audio_path = wav
        beep_in_clip = 5.0
        trimmed = True

    monkeypatch.setattr(audio_helpers, "ensure_audit_audio", lambda *a, **kw: FakeAudit())
    monkeypatch.setattr(server_module, "_get_ensemble_runtime", lambda: None)
    fake_result = _fake_ensemble_result(
        [{"time": 5.5, "confidence": 0.8, "ensemble_score": 3.0, "kept": True}],
        consensus=2,
    )
    monkeypatch.setattr(ensemble_module, "detect_shots_ensemble", lambda *a, **kw: fake_result)

    resp = client.post("/api/shooters/me/stages/1/shot-detect")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final

    saved = _json.loads(audit_file.read_text(encoding="utf-8"))
    assert saved["shots"], "real detection should have seeded shots"
    assert not is_stub_audit(saved)
    # The stub had none of the base fields (unknown at beep-confirm time);
    # the merge must backfill them so downstream readers keying on
    # beep_time (e.g. the compare-timeline exporter) don't silently drop
    # this stage's shots.
    assert saved["beep_time"] == pytest.approx(5.0)

    project_after = MatchProject.load(_shooter_root)
    status = stage_audit_status(
        project_after.stages[0], audit_docs=project_after.audit_docs_from_disk(_shooter_root)
    )
    assert status == StageStatus.in_progress


def test_shot_detect_job_records_phase_timings(tmp_path: Path, monkeypatch) -> None:
    """The instrumented ``shot_detect`` body opens a phase per natural
    boundary; the backend persists ``timer.build()`` onto the job so the
    snapshot carries the full timings dict (phases in order + meta). The
    runtime is warm here (``_get_ensemble_runtime`` stubbed to a value)
    so there is NO ``cold_model_load`` phase and ``meta.cold_model_load``
    is False."""
    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(_shooter_root)
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"S")

    audio_dir = _shooter_root / "audio"
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
    monkeypatch.setattr(audio_helpers, "ensure_video_audit_trim", lambda *a, **kw: None)
    # Warm runtime: the singleton resolver returns a value, and the warm
    # probe reports True -> the body skips the cold_model_load phase.
    monkeypatch.setattr(server_module, "_get_ensemble_runtime", lambda: object())
    monkeypatch.setattr(server_module, "_ensemble_runtime_is_warm", lambda: True)
    monkeypatch.setattr(
        ensemble_module,
        "detect_shots_ensemble",
        lambda *a, **kw: _fake_ensemble_result(
            [{"time": 5.5, "confidence": 0.8, "ensemble_score": 3.0, "kept": True}],
            consensus=2,
        ),
    )

    resp = client.post("/api/shooters/me/stages/1/shot-detect")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final

    timings = final["timings"]
    assert timings is not None, final
    phase_names = [p["name"] for p in timings["phases"]]
    # Every shot_detect phase that runs on the warm path, in completion order.
    assert phase_names == [
        "load_project",
        "ensure_trim",
        "prepare_audio",
        "load_audit_state",
        "audio_load",
        "ensemble_infer",
        "build_candidates",
        "save_audit",
        "persist_project",
    ]
    assert "cold_model_load" not in phase_names
    assert all(isinstance(p["ms"], (int, float)) and p["ms"] >= 0 for p in timings["phases"])
    assert isinstance(timings["total_ms"], (int, float))
    # total_ms covers the whole body, so it is at least the sum of phases.
    assert timings["total_ms"] >= sum(p["ms"] for p in timings["phases"]) - 1e-6
    meta = timings["meta"]
    assert meta["cold_model_load"] is False
    assert meta["candidate_count"] == 1
    assert meta["consensus"] == 2


def test_shot_detect_job_times_cold_model_load_when_runtime_cold(tmp_path: Path, monkeypatch) -> None:
    """When the ensemble singleton is not yet warm, the body forces the
    resolution inside an explicit ``cold_model_load`` phase and records
    ``meta.cold_model_load=True`` -- so the weight-load cost is visible in
    timings instead of hiding inside ``ensemble_infer``."""
    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(_shooter_root)
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"S")

    audio_dir = _shooter_root / "audio"
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

    cold_loads: list[int] = []

    monkeypatch.setattr(audio_helpers, "ensure_audit_audio", lambda *a, **kw: FakeAudit())
    monkeypatch.setattr(audio_helpers, "ensure_video_audit_trim", lambda *a, **kw: None)
    monkeypatch.setattr(server_module, "_ensemble_runtime_is_warm", lambda: False)
    monkeypatch.setattr(
        server_module,
        "_get_ensemble_runtime",
        lambda: cold_loads.append(1) or object(),
    )
    monkeypatch.setattr(
        ensemble_module,
        "detect_shots_ensemble",
        lambda *a, **kw: _fake_ensemble_result([], consensus=2),
    )

    resp = client.post("/api/shooters/me/stages/1/shot-detect")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final

    timings = final["timings"]
    phase_names = [p["name"] for p in timings["phases"]]
    assert "cold_model_load" in phase_names
    # cold_model_load lands between audio_load and ensemble_infer.
    assert phase_names.index("cold_model_load") == phase_names.index("audio_load") + 1
    assert phase_names.index("cold_model_load") < phase_names.index("ensemble_infer")
    assert timings["meta"]["cold_model_load"] is True
    # The body forced the resolution inside the cold_model_load phase; the
    # compute backend's runtime_loader resolves again inside detect_stage
    # (a no-op warm-cache hit in production), so at least one load fired.
    assert len(cold_loads) >= 1


def test_shot_detect_endpoint_ensures_trim_before_detection(tmp_path: Path, monkeypatch) -> None:
    """Re-detect on a project whose trims were wiped (e.g. by the v1->v2
    cache migration in #298) must rebuild the trimmed MP4. Without this
    the audit page falls through to streaming the raw source clip and
    long-GOP MOVs wedge in <video> buffering on first play."""
    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(_shooter_root)
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"S")

    audio_dir = _shooter_root / "audio"
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

    trim_calls: list[tuple] = []

    def fake_trim(root, stage_n, video, source, beep, stage_t, **kwargs):
        trim_calls.append((stage_n, video.video_id, beep, stage_t))
        return None

    monkeypatch.setattr(audio_helpers, "ensure_audit_audio", lambda *a, **kw: FakeAudit())
    monkeypatch.setattr(audio_helpers, "ensure_video_audit_trim", fake_trim)
    monkeypatch.setattr(server_module, "_get_ensemble_runtime", lambda: None)
    monkeypatch.setattr(
        ensemble_module,
        "detect_shots_ensemble",
        lambda *a, **kw: _fake_ensemble_result([]),
    )

    resp = client.post("/api/shooters/me/stages/1/shot-detect")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final

    # Trim was invoked once with the right (stage, video, beep, stage_time).
    assert len(trim_calls) == 1
    stage_n, video_id, beep, stage_t = trim_calls[0]
    assert stage_n == 1
    assert video_id == primary.video_id
    assert beep == pytest.approx(5.0)
    assert stage_t == pytest.approx(10.0)

    # processed.trim flips on the primary so the SPA reflects the rebuilt cache.
    proj_after = client.get("/api/shooters/me/project").json()
    assert proj_after["stages"][0]["videos"][0]["processed"].get("trim") is True


def test_shot_detect_endpoint_passes_camera_class_from_primary_mount(tmp_path: Path, monkeypatch) -> None:
    """Issue #143: ``StageVideo.camera_mount`` flows through ``camera_class_from_mount``
    into ``detect_shots_ensemble``. Verifies the wiring without exercising
    the actual ensemble (which is monkeypatched).
    """
    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    primary.camera_mount = "hand"  # iPhone-style; should map to handheld class
    project.stages[0].time_seconds = 10.0
    project.save(_shooter_root)
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"S")

    audio_dir = _shooter_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav = audio_dir / "stage1_audit.wav"
    sf.write(wav, np.zeros(48_000, dtype="float32"), 48_000)
    trimmed_dir = _shooter_root / "trimmed"
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    (trimmed_dir / "stage1_trimmed.mp4").write_bytes(b"\x00")

    from splitsmith import ensemble as ensemble_module
    from splitsmith.ui import audio as audio_helpers
    from splitsmith.ui import server as server_module

    class FakeAudit:
        audio_path = wav
        beep_in_clip = 5.0
        trimmed = True

    monkeypatch.setattr(audio_helpers, "ensure_audit_audio", lambda *a, **kw: FakeAudit())
    monkeypatch.setattr(audio_helpers, "ensure_video_audit_trim", lambda *a, **kw: None)
    monkeypatch.setattr(server_module, "_get_ensemble_runtime", lambda: None)

    captured: dict = {}

    def _fake_detect(audio_array, sr, beep, stage_t, runtime, **kwargs):
        captured.update(kwargs)
        return _fake_ensemble_result([])

    monkeypatch.setattr(ensemble_module, "detect_shots_ensemble", _fake_detect)

    resp = client.post("/api/shooters/me/stages/1/shot-detect")
    assert resp.status_code == 200
    _wait_for_job(client, resp.json()["id"])
    assert captured.get("camera_class") == "handheld"


def test_shot_detect_endpoint_passes_default_class_when_mount_missing(tmp_path: Path, monkeypatch) -> None:
    """``camera_mount=None`` -> default class (``headcam``) reaches the ensemble."""
    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    primary.camera_mount = None
    project.stages[0].time_seconds = 10.0
    project.save(_shooter_root)
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"S")

    audio_dir = _shooter_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav = audio_dir / "stage1_audit.wav"
    sf.write(wav, np.zeros(48_000, dtype="float32"), 48_000)
    trimmed_dir = _shooter_root / "trimmed"
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    (trimmed_dir / "stage1_trimmed.mp4").write_bytes(b"\x00")

    from splitsmith import ensemble as ensemble_module
    from splitsmith.ui import audio as audio_helpers
    from splitsmith.ui import server as server_module

    class FakeAudit:
        audio_path = wav
        beep_in_clip = 5.0
        trimmed = True

    monkeypatch.setattr(audio_helpers, "ensure_audit_audio", lambda *a, **kw: FakeAudit())
    monkeypatch.setattr(audio_helpers, "ensure_video_audit_trim", lambda *a, **kw: None)
    monkeypatch.setattr(server_module, "_get_ensemble_runtime", lambda: None)

    captured: dict = {}

    def _fake_detect(audio_array, sr, beep, stage_t, runtime, **kwargs):
        captured.update(kwargs)
        return _fake_ensemble_result([])

    monkeypatch.setattr(ensemble_module, "detect_shots_ensemble", _fake_detect)

    resp = client.post("/api/shooters/me/stages/1/shot-detect")
    assert resp.status_code == 200
    _wait_for_job(client, resp.json()["id"])
    # ``camera_class_from_mount(None)`` -> ``headcam`` (the default class).
    assert captured.get("camera_class") == "headcam"


def test_shot_detect_all_endpoint_submits_per_eligible_stage(tmp_path: Path, monkeypatch) -> None:
    """``POST /api/stages/shot-detect`` submits a job per eligible stage and
    reports skipped stages with reasons. Eligible: primary + beep + time_seconds."""
    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"

    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0

    src2 = tmp_path / "extra" / "VID_S2.mp4"
    src2.parent.mkdir(parents=True, exist_ok=True)
    src2.write_bytes(src2.name.encode())
    project.register_video(src2, project_root)
    src3 = tmp_path / "extra" / "VID_S3.mp4"
    src3.write_bytes(src3.name.encode())
    project.register_video(src3, project_root)

    project.stages.append(
        StageEntry(stage_number=2, stage_name="B", time_seconds=8.0, scorecard_updated_at=None)
    )
    project.stages.append(
        StageEntry(stage_number=3, stage_name="C", time_seconds=8.0, scorecard_updated_at=None)
    )
    project.stages.append(
        StageEntry(stage_number=4, stage_name="D", time_seconds=0.0, scorecard_updated_at=None)
    )
    project.assign_video(Path("raw") / "VID_S2.mp4", to_stage_number=2, role="primary")
    s2_primary = project.stages[1].primary()
    assert s2_primary is not None
    s2_primary.beep_time = 4.0
    project.assign_video(Path("raw") / "VID_S3.mp4", to_stage_number=3, role="primary")

    project.save(_shooter_root)
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"S")

    audio_dir = _shooter_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    for n in (1, 2):
        sf.write(audio_dir / f"stage{n}_audit.wav", np.zeros(48_000, dtype="float32"), 48_000)
    trimmed_dir = _shooter_root / "trimmed"
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    for n in (1, 2):
        (trimmed_dir / f"stage{n}_trimmed.mp4").write_bytes(b"\x00")

    from splitsmith import ensemble as ensemble_module
    from splitsmith.ui import audio as audio_helpers
    from splitsmith.ui import server as server_module

    class FakeAudit:
        def __init__(self, n: int) -> None:
            self.audio_path = audio_dir / f"stage{n}_audit.wav"
            self.beep_in_clip = 5.0 if n == 1 else 4.0
            self.trimmed = True

    monkeypatch.setattr(audio_helpers, "ensure_audit_audio", lambda root, n, *a, **kw: FakeAudit(n))
    monkeypatch.setattr(audio_helpers, "ensure_video_audit_trim", lambda *a, **kw: None)
    monkeypatch.setattr(server_module, "_get_ensemble_runtime", lambda: None)
    monkeypatch.setattr(
        ensemble_module,
        "detect_shots_ensemble",
        lambda *a, **kw: _fake_ensemble_result([]),
    )

    resp = client.post("/api/shooters/me/stages/shot-detect")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    submitted = sorted(j["stage_number"] for j in body["jobs"])
    assert submitted == [1, 2]
    skipped = {s["stage_number"]: s["reason"] for s in body["skipped"]}
    assert skipped == {3: "no_beep", 4: "no_primary"}

    for j in body["jobs"]:
        _wait_for_job(client, j["id"])


def test_shot_detect_endpoint_writes_stage_rounds_into_audit_json(tmp_path: Path, monkeypatch) -> None:
    """Stage's ``stage_rounds`` (from scoreboard import) lands in the audit
    JSON and reaches the ensemble as ``expected_rounds``.

    Validates the end-to-end path: project state -> seeded audit -> ensemble.
    """
    import json as _json

    import numpy as np
    import soundfile as sf

    from splitsmith.config import StageRounds

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.stages[0].stage_rounds = StageRounds(expected=12, paper_targets=6, steel_targets=0)
    project.save(_shooter_root)
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"S")

    audio_dir = _shooter_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav = audio_dir / "stage1_audit.wav"
    sf.write(wav, np.zeros(48_000, dtype="float32"), 48_000)
    trimmed_dir = _shooter_root / "trimmed"
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    (trimmed_dir / "stage1_trimmed.mp4").write_bytes(b"\x00")

    from splitsmith import ensemble as ensemble_module
    from splitsmith.ui import audio as audio_helpers
    from splitsmith.ui import server as server_module

    class FakeAudit:
        audio_path = wav
        beep_in_clip = 5.0
        trimmed = True

    monkeypatch.setattr(audio_helpers, "ensure_audit_audio", lambda *a, **kw: FakeAudit())
    monkeypatch.setattr(audio_helpers, "ensure_video_audit_trim", lambda *a, **kw: None)
    monkeypatch.setattr(server_module, "_get_ensemble_runtime", lambda: None)

    captured: dict = {}

    def _fake_detect(audio_array, sr, beep, stage_t, runtime, **kwargs):
        captured.update(kwargs)
        return _fake_ensemble_result([])

    monkeypatch.setattr(ensemble_module, "detect_shots_ensemble", _fake_detect)

    resp = client.post("/api/shooters/me/stages/1/shot-detect")
    assert resp.status_code == 200
    _wait_for_job(client, resp.json()["id"])

    assert captured.get("expected_rounds") == 12

    audit = _json.loads((_shooter_root / "audit" / "stage1.json").read_text(encoding="utf-8"))
    assert audit["stage_rounds"]["expected"] == 12
    assert audit["stage_rounds"]["paper_targets"] == 6


def test_camera_mount_patch_endpoint_round_trips(tmp_path: Path) -> None:
    """PATCH /camera-mount validates against the CameraMount enum and persists."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    video_id = primary.video_id

    # Override.
    resp = client.patch(
        f"/api/shooters/me/stages/1/videos/{video_id}/camera-mount",
        json={"mount": "hand"},
    )
    assert resp.status_code == 200, resp.text
    refreshed = MatchProject.load(_shooter_root)
    assert refreshed.stages[0].primary().camera_mount == "hand"

    # Bad value rejected.
    resp = client.patch(
        f"/api/shooters/me/stages/1/videos/{video_id}/camera-mount",
        json={"mount": "shoulder"},
    )
    assert resp.status_code == 400, resp.text

    # Clear back to None.
    resp = client.patch(
        f"/api/shooters/me/stages/1/videos/{video_id}/camera-mount",
        json={"mount": None},
    )
    assert resp.status_code == 200
    refreshed = MatchProject.load(_shooter_root)
    assert refreshed.stages[0].primary().camera_mount is None


def test_camera_model_patch_endpoint_round_trips(tmp_path: Path) -> None:
    """PATCH /camera-model writes both make and model and clears them together."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    video_id = primary.video_id

    resp = client.patch(
        f"/api/shooters/me/stages/1/videos/{video_id}/camera-model",
        json={"make": "Meta", "model": "Vanguard"},
    )
    assert resp.status_code == 200, resp.text
    refreshed = MatchProject.load(_shooter_root)
    refreshed_primary = refreshed.stages[0].primary()
    assert refreshed_primary.camera_make == "Meta"
    assert refreshed_primary.camera_model == "Vanguard"

    # Clear back to None on both fields.
    resp = client.patch(
        f"/api/shooters/me/stages/1/videos/{video_id}/camera-model",
        json={"make": None, "model": None},
    )
    assert resp.status_code == 200, resp.text
    refreshed = MatchProject.load(_shooter_root)
    refreshed_primary = refreshed.stages[0].primary()
    assert refreshed_primary.camera_make is None
    assert refreshed_primary.camera_model is None


def test_camera_model_patch_rejects_half_filled_pair(tmp_path: Path) -> None:
    """Per-model lookup keys both fields, so a half-filled body is meaningless."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    video_id = primary.video_id

    resp = client.patch(
        f"/api/shooters/me/stages/1/videos/{video_id}/camera-model",
        json={"make": "Meta", "model": None},
    )
    assert resp.status_code == 400, resp.text
    assert "supplied together" in resp.json()["detail"]

    resp = client.patch(
        f"/api/shooters/me/stages/1/videos/{video_id}/camera-model",
        json={"make": None, "model": "Vanguard"},
    )
    assert resp.status_code == 400, resp.text


def test_set_compare_camera_persists(tmp_path: Path) -> None:
    """A mount that exists on the shooter's footage round-trips to disk."""
    client, _ = _seed_project_with_primary(tmp_path)
    shooter_root = tmp_path / "match" / "shooters" / "me"
    primary = MatchProject.load(shooter_root).stages[0].primary()
    assert primary is not None
    client.patch(
        f"/api/shooters/me/stages/1/videos/{primary.video_id}/camera-mount",
        json={"mount": "chest"},
    )

    resp = client.patch("/api/shooters/me/compare-camera", json={"camera": "chest"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["compare_camera"] == "chest"
    assert MatchProject.load(shooter_root).compare_camera == "chest"


def test_bulk_camera_set_applies_mount_and_model_across_videos(tmp_path: Path) -> None:
    """bulk-set with set_mount + set_model true writes both fields on every listed video."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    shooter_root = project_root / "shooters" / "me"

    project = MatchProject.load(shooter_root)
    primary_id = project.stages[0].primary().video_id
    sec_id = _seed_second_video(tmp_path, project_root)

    resp = client.post(
        "/api/shooters/me/stages/camera/bulk-set",
        json={
            "items": [
                {"stage_number": 1, "video_id": primary_id},
                {"stage_number": 1, "video_id": sec_id},
            ],
            "set_mount": True,
            "mount": "head",
            "set_model": True,
            "make": "Insta360",
            "model": "GO 3S",
        },
    )
    assert resp.status_code == 200, resp.text

    refreshed = MatchProject.load(shooter_root)
    for video in refreshed.stages[0].videos:
        if video.video_id in (primary_id, sec_id):
            assert video.camera_mount == "head", f"video {video.video_id} mount wrong"
            assert video.camera_make == "Insta360", f"video {video.video_id} make wrong"
            assert video.camera_model == "GO 3S", f"video {video.video_id} model wrong"


def test_bulk_camera_set_mount_only_leaves_model(tmp_path: Path) -> None:
    """set_mount=True, set_model=False leaves an existing model intact."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    shooter_root = project_root / "shooters" / "me"

    # Pre-set a model on the primary.
    project = MatchProject.load(shooter_root)
    primary = project.stages[0].primary()
    primary.camera_make = "Meta"
    primary.camera_model = "Vanguard"
    project.save(shooter_root)
    primary_id = primary.video_id

    resp = client.post(
        "/api/shooters/me/stages/camera/bulk-set",
        json={
            "items": [{"stage_number": 1, "video_id": primary_id}],
            "set_mount": True,
            "mount": "chest",
            "set_model": False,
        },
    )
    assert resp.status_code == 200, resp.text

    refreshed = MatchProject.load(shooter_root)
    vid = refreshed.stages[0].primary()
    assert vid.camera_mount == "chest"
    assert vid.camera_make == "Meta"  # untouched
    assert vid.camera_model == "Vanguard"  # untouched


def test_bulk_camera_set_model_only_leaves_mount(tmp_path: Path) -> None:
    """set_model=True, set_mount=False leaves an existing mount intact."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    shooter_root = project_root / "shooters" / "me"

    project = MatchProject.load(shooter_root)
    primary = project.stages[0].primary()
    primary.camera_mount = "hand"
    project.save(shooter_root)
    primary_id = primary.video_id

    resp = client.post(
        "/api/shooters/me/stages/camera/bulk-set",
        json={
            "items": [{"stage_number": 1, "video_id": primary_id}],
            "set_mount": False,
            "set_model": True,
            "make": "Meta",
            "model": "Vanguard",
        },
    )
    assert resp.status_code == 200, resp.text

    refreshed = MatchProject.load(shooter_root)
    vid = refreshed.stages[0].primary()
    assert vid.camera_mount == "hand"  # untouched
    assert vid.camera_make == "Meta"
    assert vid.camera_model == "Vanguard"


def test_bulk_camera_set_auto_clears_override(tmp_path: Path) -> None:
    """Passing null mount + null make/model with the set flags clears overrides."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    shooter_root = project_root / "shooters" / "me"

    project = MatchProject.load(shooter_root)
    primary = project.stages[0].primary()
    primary.camera_mount = "head"
    primary.camera_make = "Meta"
    primary.camera_model = "Vanguard"
    project.save(shooter_root)
    primary_id = primary.video_id

    resp = client.post(
        "/api/shooters/me/stages/camera/bulk-set",
        json={
            "items": [{"stage_number": 1, "video_id": primary_id}],
            "set_mount": True,
            "mount": None,
            "set_model": True,
            "make": None,
            "model": None,
        },
    )
    assert resp.status_code == 200, resp.text

    refreshed = MatchProject.load(shooter_root)
    vid = refreshed.stages[0].primary()
    assert vid.camera_mount is None
    assert vid.camera_make is None
    assert vid.camera_model is None


def test_bulk_camera_set_skips_unknown_video_id(tmp_path: Path) -> None:
    """An unknown video_id in the batch is skipped; the batch is not aborted."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    shooter_root = project_root / "shooters" / "me"

    project = MatchProject.load(shooter_root)
    primary_id = project.stages[0].primary().video_id

    resp = client.post(
        "/api/shooters/me/stages/camera/bulk-set",
        json={
            "items": [
                {"stage_number": 1, "video_id": primary_id},
                {"stage_number": 1, "video_id": "nonexistent-id-xyz"},
            ],
            "set_mount": True,
            "mount": "head",
        },
    )
    assert resp.status_code == 200, resp.text

    refreshed = MatchProject.load(shooter_root)
    # The real video was updated.
    assert refreshed.stages[0].primary().camera_mount == "head"


def test_bulk_camera_set_skips_unknown_stage(tmp_path: Path) -> None:
    """An unknown stage_number is skipped without aborting the batch."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    shooter_root = project_root / "shooters" / "me"

    project = MatchProject.load(shooter_root)
    primary_id = project.stages[0].primary().video_id

    resp = client.post(
        "/api/shooters/me/stages/camera/bulk-set",
        json={
            "items": [
                {"stage_number": 99, "video_id": primary_id},  # stage 99 doesn't exist
                {"stage_number": 1, "video_id": primary_id},
            ],
            "set_mount": True,
            "mount": "belt",
        },
    )
    assert resp.status_code == 200, resp.text

    refreshed = MatchProject.load(shooter_root)
    assert refreshed.stages[0].primary().camera_mount == "belt"


def test_bulk_camera_set_rejects_bad_mount(tmp_path: Path) -> None:
    """An unrecognised mount value returns 400 before touching any video."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    shooter_root = project_root / "shooters" / "me"

    project = MatchProject.load(shooter_root)
    primary_id = project.stages[0].primary().video_id

    resp = client.post(
        "/api/shooters/me/stages/camera/bulk-set",
        json={
            "items": [{"stage_number": 1, "video_id": primary_id}],
            "set_mount": True,
            "mount": "shoulder",
        },
    )
    assert resp.status_code == 400, resp.text
    assert "shoulder" in resp.json()["detail"]


def test_bulk_camera_set_rejects_half_filled_model(tmp_path: Path) -> None:
    """set_model=True with only one of make/model supplied returns 400."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    shooter_root = project_root / "shooters" / "me"

    project = MatchProject.load(shooter_root)
    primary_id = project.stages[0].primary().video_id

    resp = client.post(
        "/api/shooters/me/stages/camera/bulk-set",
        json={
            "items": [{"stage_number": 1, "video_id": primary_id}],
            "set_model": True,
            "make": "Meta",
            "model": None,
        },
    )
    assert resp.status_code == 400, resp.text
    assert "supplied together" in resp.json()["detail"]

    resp = client.post(
        "/api/shooters/me/stages/camera/bulk-set",
        json={
            "items": [{"stage_number": 1, "video_id": primary_id}],
            "set_model": True,
            "make": None,
            "model": "Vanguard",
        },
    )
    assert resp.status_code == 400, resp.text


def test_bulk_camera_set_noop_batch_rejected(tmp_path: Path) -> None:
    """set_mount=False and set_model=False is rejected as a client bug."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    shooter_root = project_root / "shooters" / "me"

    project = MatchProject.load(shooter_root)
    primary_id = project.stages[0].primary().video_id

    resp = client.post(
        "/api/shooters/me/stages/camera/bulk-set",
        json={
            "items": [{"stage_number": 1, "video_id": primary_id}],
            "set_mount": False,
            "set_model": False,
        },
    )
    assert resp.status_code == 400, resp.text


def test_bulk_camera_set_hosted_state_docs_parity(tmp_path: Path) -> None:
    """Hosted mode: bulk-set persists via state_docs and bumps version exactly once.

    Drives the endpoint through a real ProjectStateStore backed by an
    in-memory SQLite database, injected into the singleton state object so
    the alias middleware's ``current_match_id`` wiring works normally. Asserts
    that version increments exactly once (a per-video save loop would bump it
    N times or raise a 409 on the second bump).
    """
    import asyncio as _asyncio

    from splitsmith import match_model as _match_model
    from splitsmith.db import Base, ProjectStateStore, User, create_engine, sessionmaker

    engine = create_engine("sqlite+aiosqlite:///:memory:")
    sf = sessionmaker(engine)

    async def _setup_db() -> str:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sf() as s:
            user = User(email="hosted-cam@test.se")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            return user.id

    uid = _asyncio.run(_setup_db())
    store = ProjectStateStore(sf, user_id=uid)

    # Build the local project and grab its match_id so we can seed the store
    # under the same id the alias middleware will set on the request.
    project_root = tmp_path / "match"
    client, _ = _seed_project_with_primary(tmp_path)
    shooter_root = project_root / "shooters" / "me"
    local_match = _match_model.Match.load(project_root)
    match_id = local_match.match_id

    local_match_doc = local_match.model_dump(mode="json")
    _asyncio.run(store.save_match(match_id, local_match_doc, expected_version=0))

    project = MatchProject.load(shooter_root)
    primary_id = project.stages[0].primary().video_id

    _asyncio.run(store.save_project(match_id, "me", project.model_dump(mode="json"), expected_version=0))
    # Version is now 1 in the store.

    # Inject the store into the singleton state.  project_state checks
    # current_tenant first; in a test that has no tenant pinned it falls
    # back to _project_state.  The setter writes that backing field.
    _state = client.app.state.splitsmith_state

    old_project_state = _state._project_state
    _state._project_state = store
    try:
        resp = client.post(
            "/api/shooters/me/stages/camera/bulk-set",
            json={
                "items": [{"stage_number": 1, "video_id": primary_id}],
                "set_mount": True,
                "mount": "head",
                "set_model": True,
                "make": "Meta",
                "model": "Vanguard",
            },
        )
    finally:
        _state._project_state = old_project_state

    assert resp.status_code == 200, resp.text

    # Version must have incremented exactly once (1 -> 2).
    doc, version = _asyncio.run(store.load_project(match_id, "me"))
    assert doc is not None
    assert version == 2, f"expected version 2 after one save, got {version}"

    # The primary video in the stored doc has the new values.
    stage_doc = next(s for s in doc["stages"] if s["stage_number"] == 1)
    vid_doc = next(v for v in stage_doc["videos"] if v["video_id"] == primary_id)
    assert vid_doc["camera_mount"] == "head"
    assert vid_doc["camera_make"] == "Meta"
    assert vid_doc["camera_model"] == "Vanguard"


def test_beep_confirm_seeds_the_stub_in_hosted_state_docs(tmp_path: Path) -> None:
    """Hosted mode is production, and the beep-confirm stub was only ever
    tested against local files (#620).

    ``set_beep_reviewed`` seeds ``{"shots": [], "detection": "none"}`` so
    status surfaces read a document instead of inferring from absence. In
    hosted mode that write goes to ``state_docs``, not disk, and the
    "already has a doc" check reads back through the same store -- so a
    re-confirm on an audited stage must not wipe shot data there either.
    Neither half had a hosted test; only the status *reader* did.

    The store is a file-backed SQLite with a NullPool, not ``:memory:``
    (#998). The first confirm chains a trim job (UX PR 5) that runs on a
    worker thread with its own event loop and persists the project
    through this same store; an in-memory SQLite is one shared
    connection, so that thread's session and the test's shared one
    transaction and its rollback could discard the test's audit write,
    which then read back as the stub. A file DB gives every session its
    own connection, the same shape the hosted boot path uses.
    """
    import asyncio as _asyncio

    from splitsmith import match_model as _match_model
    from splitsmith.db import Base, ProjectStateStore, User, create_engine, sessionmaker
    from splitsmith.match_project import is_stub_audit

    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'state.sqlite'}", pool_disabled=True)
    sf = sessionmaker(engine)

    async def _setup_db() -> str:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sf() as s:
            user = User(email="hosted-stub@test.se")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            return user.id

    uid = _asyncio.run(_setup_db())
    store = ProjectStateStore(sf, user_id=uid)

    project_root = tmp_path / "match"
    client, _ = _seed_project_with_primary(tmp_path)
    shooter_root = project_root / "shooters" / "me"
    local_match = _match_model.Match.load(project_root)
    match_id = local_match.match_id
    _asyncio.run(store.save_match(match_id, local_match.model_dump(mode="json"), expected_version=0))

    project = MatchProject.load(shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.save(shooter_root)
    primary_id = primary.video_id
    _asyncio.run(store.save_project(match_id, "me", project.model_dump(mode="json"), expected_version=0))

    _state = client.app.state.splitsmith_state
    old_project_state = _state._project_state
    _state._project_state = store
    try:
        resp = client.post(
            f"/api/shooters/me/stages/1/videos/{primary_id}/beep/review",
            json={"reviewed": True},
        )
        assert resp.status_code == 200, resp.text

        doc, version = _asyncio.run(store.load_audit(match_id, "me", 1))
        assert doc is not None, "the stub must land in state_docs, not on disk"
        assert is_stub_audit(doc)
        assert version > 0

        # No stub file was written locally -- hosted writes go to the store.
        assert not (shooter_root / "audit" / "stage1.json").exists()

        # Re-confirming must not overwrite a real audit. Put one in the
        # store and confirm again.
        real_doc = {"shots": [{"shot_number": 1, "ms_after_beep": 500}], "beep_time": 5.0}
        _asyncio.run(store.save_audit(match_id, "me", 1, real_doc, expected_version=version))
        resp = client.post(
            f"/api/shooters/me/stages/1/videos/{primary_id}/beep/review",
            json={"reviewed": True},
        )
        assert resp.status_code == 200, resp.text
        after, _ = _asyncio.run(store.load_audit(match_id, "me", 1))
        assert after is not None
        assert after["shots"], "a re-confirm must never wipe audited shots"
    finally:
        _state._project_state = old_project_state


def test_shot_detect_endpoint_400_when_no_beep(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    project.stages[0].time_seconds = 10.0
    project.save(_shooter_root)
    resp = client.post("/api/shooters/me/stages/1/shot-detect")
    assert resp.status_code == 400
    assert "beep_time" in resp.json()["detail"]


def test_shot_detect_endpoint_dedupes_active_jobs(tmp_path: Path, monkeypatch) -> None:
    """Second submit while first is running returns the same job."""
    import threading

    import numpy as np
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.stages[0].time_seconds = 10.0
    project.save(_shooter_root)
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"S")
    audio_dir = _shooter_root / "audio"
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
    monkeypatch.setattr(audio_helpers, "ensure_video_audit_trim", lambda *a, **kw: None)
    monkeypatch.setattr(server_module, "_get_ensemble_runtime", lambda: None)

    proceed = threading.Event()
    invocations = []

    def slow_detect(*a, **kw):
        invocations.append(1)
        proceed.wait(timeout=2.0)
        return _fake_ensemble_result([])

    monkeypatch.setattr(ensemble_module, "detect_shots_ensemble", slow_detect)

    first = client.post("/api/shooters/me/stages/1/shot-detect").json()
    second = client.post("/api/shooters/me/stages/1/shot-detect").json()
    assert first["id"] == second["id"]
    proceed.set()
    final = _wait_for_job(client, first["id"])
    assert final["status"] == "succeeded"
    assert len(invocations) == 1


def test_jobs_endpoints_list_and_get(tmp_path: Path, monkeypatch) -> None:
    """/api/me/jobs lists active + recent; /api/me/jobs/{id} polls one. detect-beep
    and trim both surface here so the SPA has a single status surface."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    project.stages[0].time_seconds = 5.0
    project.save(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"S")

    from splitsmith import trim
    from splitsmith.beep import detect as beep_detect
    from splitsmith.ui import audio as audio_helpers

    class FakeBeep:
        time = 1.0
        peak_amplitude = 0.5
        duration_ms = 80.0
        confidence = 0.85
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

    resp = client.post("/api/shooters/me/stages/1/detect-beep")
    assert resp.status_code == 200
    job_id = resp.json()["id"]
    final = _wait_for_job(client, job_id)
    assert final["status"] == "succeeded"
    assert final["progress"] == pytest.approx(1.0)
    assert final["kind"] == "detect_beep"
    assert final["stage_number"] == 1

    listing = client.get("/api/me/jobs").json()
    assert any(j["id"] == job_id for j in listing)


def test_detect_beep_job_records_failure_when_ffmpeg_blows_up(tmp_path: Path, monkeypatch) -> None:
    """ffmpeg failures inside a job populate Job.error rather than 500-ing
    the submit request. The SPA learns about the failure via polling."""
    client, _ = _seed_project_with_primary(tmp_path)
    from splitsmith.ui import audio as audio_helpers

    def boom(*a, **kw):  # type: ignore[no-untyped-def]
        raise audio_helpers.AudioExtractionError("ffmpeg fell over")

    monkeypatch.setattr(audio_helpers, "detect_primary_beep", boom)
    monkeypatch.setattr(audio_helpers, "detect_video_beep", boom)

    resp = client.post("/api/shooters/me/stages/1/detect-beep")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "failed"
    assert "ffmpeg fell over" in final["error"]


def test_acknowledge_endpoint_marks_failed_job_as_seen(tmp_path: Path, monkeypatch) -> None:
    """POST /api/jobs/{id}/acknowledge flips ``acknowledged`` on a failed
    job (issue #73). The SPA drives the per-row "Dismiss" button against
    this; subsequent /api/jobs snapshots return acknowledged=True so the
    badge stops counting it."""
    client, _ = _seed_project_with_primary(tmp_path)
    from splitsmith.ui import audio as audio_helpers

    def boom(*a, **kw):  # type: ignore[no-untyped-def]
        raise audio_helpers.AudioExtractionError("kaboom")

    monkeypatch.setattr(audio_helpers, "detect_primary_beep", boom)
    monkeypatch.setattr(audio_helpers, "detect_video_beep", boom)

    submit = client.post("/api/shooters/me/stages/1/detect-beep")
    assert submit.status_code == 200
    job_id = submit.json()["id"]
    final = _wait_for_job(client, job_id)
    assert final["status"] == "failed"
    assert final["acknowledged"] is False

    ack = client.post(f"/api/me/jobs/{job_id}/acknowledge")
    assert ack.status_code == 200
    assert ack.json()["acknowledged"] is True

    listing = client.get("/api/me/jobs").json()
    assert next(j for j in listing if j["id"] == job_id)["acknowledged"] is True


def test_acknowledge_endpoint_noop_for_succeeded_job(tmp_path: Path, monkeypatch) -> None:
    """Acknowledging a non-failed job is a quiet no-op so the SPA's
    optimistic flow doesn't have to special-case statuses."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    project.stages[0].time_seconds = 5.0
    project.save(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    project.resolve_video_path(_shooter_root, primary.path).resolve().write_bytes(b"S")

    from splitsmith.beep import detect as beep_detect
    from splitsmith.ui import audio as audio_helpers

    class FakeBeep:
        time = 1.0
        peak_amplitude = 0.5
        duration_ms = 80.0
        confidence = 0.85
        candidates: list = []

    monkeypatch.setattr(audio_helpers, "ensure_primary_audio", lambda *a, **kw: tmp_path / "z.wav")
    (tmp_path / "z.wav").write_bytes(b"\x00")
    monkeypatch.setattr(beep_detect, "load_audio", lambda p: ([0.0] * 100, 48_000))
    monkeypatch.setattr(beep_detect, "detect_beep", lambda *a, **kw: FakeBeep())

    submit = client.post("/api/shooters/me/stages/1/detect-beep")
    job_id = submit.json()["id"]
    assert _wait_for_job(client, job_id)["status"] == "succeeded"
    resp = client.post(f"/api/me/jobs/{job_id}/acknowledge")
    assert resp.status_code == 200
    assert resp.json()["acknowledged"] is False


def test_acknowledge_failures_bulk_endpoint(tmp_path: Path, monkeypatch) -> None:
    """POST /api/jobs/acknowledge-failures dismisses every unacked failure
    in one call and returns the snapshots that flipped, so the SPA can
    apply the diff without an extra GET."""
    client, _ = _seed_project_with_primary(tmp_path)
    from splitsmith.ui import audio as audio_helpers

    def boom(*a, **kw):  # type: ignore[no-untyped-def]
        raise audio_helpers.AudioExtractionError("kaboom")

    monkeypatch.setattr(audio_helpers, "detect_primary_beep", boom)
    monkeypatch.setattr(audio_helpers, "detect_video_beep", boom)

    ids: list[str] = []
    for _ in range(2):
        submit = client.post("/api/shooters/me/stages/1/detect-beep")
        jid = submit.json()["id"]
        assert _wait_for_job(client, jid)["status"] == "failed"
        ids.append(jid)

    # Pre-ack one so the bulk endpoint reports only the other.
    client.post(f"/api/me/jobs/{ids[0]}/acknowledge")

    bulk = client.post("/api/me/jobs/acknowledge-failures")
    assert bulk.status_code == 200
    affected = bulk.json()
    assert {j["id"] for j in affected} == {ids[1]}

    listing = client.get("/api/me/jobs").json()
    by_id = {j["id"]: j for j in listing}
    assert by_id[ids[0]]["acknowledged"] is True
    assert by_id[ids[1]]["acknowledged"] is True


def test_post_trim_400_when_no_beep(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    project.stages[0].time_seconds = 10.0
    project.save(_shooter_root)
    resp = client.post("/api/shooters/me/stages/1/trim")
    assert resp.status_code == 400
    assert "beep_time" in resp.json()["detail"]


def test_get_stage_audit_returns_payload_when_file_exists(tmp_path: Path) -> None:
    """Audit endpoint reads <project>/audit/stage<N>.json verbatim."""
    import json

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    audit_dir = _shooter_root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "stage_number": 1,
        "stage_name": "Stage 1",
        "shots": [{"shot_number": 1, "candidate_number": 4, "time": 1.5, "ms_after_beep": 1500}],
        "_candidates_pending_audit": {"candidates": []},
    }
    (audit_dir / "stage1.json").write_text(json.dumps(payload), encoding="utf-8")

    resp = client.get("/api/shooters/me/stages/1/audit")
    assert resp.status_code == 200
    assert resp.json()["shots"][0]["candidate_number"] == 4


def test_get_stage_audit_returns_null_when_missing(tmp_path: Path) -> None:
    """No audit JSON yet -> 200 null. The SPA treats this as 'start
    fresh' and we don't want the request showing up as a failure in
    DevTools on every audit-page mount."""
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.get("/api/shooters/me/stages/1/audit")
    assert resp.status_code == 200
    assert resp.json() is None


def test_get_stage_audit_404_when_stage_unknown(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.get("/api/shooters/me/stages/99/audit")
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
        "audit_events": [{"ts": "2026-05-02T12:00:00Z", "kind": "marker_kept", "payload": {"id": "cand-4"}}],
    }
    resp = client.put("/api/shooters/me/stages/1/audit", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["shots"][0]["candidate_number"] == 4
    on_disk = (tmp_path / "match" / "shooters" / "me" / "audit" / "stage1.json").read_text(encoding="utf-8")
    import json as _json

    assert _json.loads(on_disk)["shots"][0]["candidate_number"] == 4


def test_put_stage_audit_keeps_previous_version_as_bak(tmp_path: Path) -> None:
    """A second PUT preserves the prior contents at stage<N>.json.bak so a
    bad save can be recovered manually."""
    client, _ = _seed_project_with_primary(tmp_path)
    first = {"stage_number": 1, "shots": [{"shot_number": 1, "time": 0.5}]}
    second = {"stage_number": 1, "shots": [{"shot_number": 1, "time": 1.5}]}
    audit_path = tmp_path / "match" / "shooters" / "me" / "audit"

    assert client.put("/api/shooters/me/stages/1/audit", json=first).status_code == 200
    assert client.put("/api/shooters/me/stages/1/audit", json=second).status_code == 200

    import json as _json

    final = _json.loads((audit_path / "stage1.json").read_text(encoding="utf-8"))
    backup = _json.loads((audit_path / "stage1.json.bak").read_text(encoding="utf-8"))
    assert final["shots"][0]["time"] == 1.5
    assert backup["shots"][0]["time"] == 0.5


@pytest.mark.parametrize(
    ("token", "field"),
    [
        ("Infinity", "candidate_number"),
        ("-Infinity", "time"),
        ("NaN", "ms_after_beep"),
    ],
)
def test_put_stage_audit_rejects_non_finite_float(tmp_path: Path, token: str, field: str) -> None:
    """#843: a non-finite float used to persist and *then* 500 on the way
    out, because ``json.loads`` accepts the bare token but
    ``JSONResponse`` encodes with ``allow_nan=False``. Every later read of
    that stage 500'd too. Reject at the save boundary instead, so the
    stage keeps its last good document.

    Hand-crafted body on purpose: ``httpx``'s ``json=`` refuses to encode
    these, exactly as a browser's ``JSON.stringify`` does.
    """
    import json as _json

    client, _ = _seed_project_with_primary(tmp_path)
    good = {"stage_number": 1, "shots": [{"shot_number": 1, "time": 1.5, "source": "detected"}]}
    assert client.put("/api/shooters/me/stages/1/audit", json=good).status_code == 200

    raw = f'{{"stage_number": 1, "shots": [{{"shot_number": 1, "{field}": {token}}}]}}'
    resp = client.put(
        "/api/shooters/me/stages/1/audit",
        content=raw,
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 422
    assert "non-finite" in resp.json()["detail"]

    # The bad document never reached disk, so the stage is still readable.
    on_disk = tmp_path / "match" / "shooters" / "me" / "audit" / "stage1.json"
    assert _json.loads(on_disk.read_text(encoding="utf-8"))["shots"][0]["time"] == 1.5
    reread = client.get("/api/shooters/me/stages/1/audit")
    assert reread.status_code == 200
    assert reread.json()["shots"][0]["time"] == 1.5


def test_put_stage_audit_404_when_stage_unknown(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.put("/api/shooters/me/stages/99/audit", json={"stage_number": 99, "shots": []})
    assert resp.status_code == 404


def test_put_stage_audit_classifies_intervals(tmp_path: Path) -> None:
    """#775: an audited stage is fully classified. The save endpoint runs
    the auto-classifier so statistic_splits never sees a partially
    classified stage."""
    client, _ = _seed_project_with_primary(tmp_path)
    payload = {
        "stage_number": 1,
        "shots": [
            {"shot_number": 1, "ms_after_beep": 1500, "source": "detected"},
            {"shot_number": 2, "ms_after_beep": 1800, "source": "detected"},  # 0.30 -> split
            {"shot_number": 3, "ms_after_beep": 3300, "source": "detected"},  # 1.50 -> transition
            {"shot_number": 4, "ms_after_beep": 5900, "source": "detected"},  # 2.60 -> movement
        ],
        "audit_events": [{"ts": "2026-08-09T12:00:00Z", "kind": "save", "payload": {}}],
    }
    resp = client.put("/api/shooters/me/stages/1/audit", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert [s["interval_class"] for s in body["shots"]] == [
        "first_shot",
        "split",
        "transition",
        "movement",
    ]
    assert all(s["interval_class_source"] == "auto" for s in body["shots"])

    on_disk = json.loads(
        (tmp_path / "match" / "shooters" / "me" / "audit" / "stage1.json").read_text(encoding="utf-8")
    )
    assert [s["interval_class"] for s in on_disk["shots"]] == [
        "first_shot",
        "split",
        "transition",
        "movement",
    ]


def test_put_stage_audit_preserves_manual_classes(tmp_path: Path) -> None:
    """Manual classifications survive the save-time auto-classify."""
    client, _ = _seed_project_with_primary(tmp_path)
    payload = {
        "stage_number": 1,
        "shots": [
            {"shot_number": 1, "ms_after_beep": 1500},
            {
                "shot_number": 2,
                "ms_after_beep": 1800,
                "interval_class": "reload",
                "interval_class_source": "manual",
            },
        ],
    }
    resp = client.put("/api/shooters/me/stages/1/audit", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["shots"][0]["interval_class"] == "first_shot"
    assert body["shots"][0]["interval_class_source"] == "auto"
    assert body["shots"][1]["interval_class"] == "reload"
    assert body["shots"][1]["interval_class_source"] == "manual"


def test_put_stage_audit_skips_shots_without_ms(tmp_path: Path) -> None:
    """A shot with no ms_after_beep stays unclassified (it never reaches
    statistics - audit_shots_to_engine_shots drops it). It sorts to the
    end of the classifier's walk, so the ms-bearing shots still classify
    against each other's gaps."""
    client, _ = _seed_project_with_primary(tmp_path)
    payload = {
        "stage_number": 1,
        "shots": [
            {"shot_number": 1, "ms_after_beep": 1500},
            {"shot_number": 2, "time": 2.0},  # no ms_after_beep
            {"shot_number": 3, "ms_after_beep": 3700},
        ],
    }
    resp = client.put("/api/shooters/me/stages/1/audit", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["shots"][0]["interval_class"] == "first_shot"
    assert "interval_class" not in body["shots"][1] or body["shots"][1]["interval_class"] is None
    # gap vs shot 1 = 1.2s -> movement; the ms-less shot doesn't break the chain
    assert body["shots"][2]["interval_class"] == "movement"


def test_get_stage_anomalies_empty_when_no_audit_file(tmp_path: Path) -> None:
    """No audit JSON yet -> empty anomalies list (issue #42).

    The audit screen calls this on mount; pre-detection there's nothing
    useful to flag, so the panel renders the "looks clean" empty state.
    """
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.get("/api/shooters/me/stages/1/anomalies")
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
    _shooter_root = project_root / "shooters" / "me"
    audit_dir = _shooter_root / "audit"
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

    resp = client.get("/api/shooters/me/stages/1/anomalies")
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
    resp = client.get("/api/shooters/me/stages/99/anomalies")
    assert resp.status_code == 404


def test_peaks_endpoint_rejects_extreme_bins(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    assert client.get("/api/shooters/me/stages/1/peaks?bins=8").status_code == 422
    assert client.get("/api/shooters/me/stages/1/peaks?bins=999999").status_code == 422


def test_stage_peaks_reach_one_millisecond(tmp_path: Path) -> None:
    """Audit snaps to the leading edge from 1 ms peaks: a 60 s stage asks
    for 60 000 bins, past the old 8192 cap."""
    client, _ = _seed_project_with_primary(tmp_path)
    assert client.get("/api/shooters/me/stages/1/peaks?bins=60000").status_code != 422


def test_per_video_detect_beep_runs_on_secondary(tmp_path: Path, monkeypatch) -> None:
    """The /api/stages/{n}/videos/{video_id}/detect-beep endpoint runs the
    beep pipeline on a secondary and persists the result onto that
    secondary -- not the primary."""
    client, _ = _seed_project_with_secondary(tmp_path)
    _stub_detect(monkeypatch, beep_time=7.250)

    sec_id = _video_id_for(client, 1, "secondary")
    resp = client.post(f"/api/shooters/me/stages/1/videos/{sec_id}/detect-beep")
    assert resp.status_code == 200
    job = resp.json()
    assert job["video_id"] == sec_id
    final = _wait_for_job(client, job["id"])
    assert final["status"] == "succeeded", final

    proj = client.get("/api/shooters/me/project").json()
    primary = next(v for v in proj["stages"][0]["videos"] if v["role"] == "primary")
    secondary = next(v for v in proj["stages"][0]["videos"] if v["role"] == "secondary")
    assert primary["beep_time"] is None  # primary untouched
    assert secondary["beep_time"] == pytest.approx(7.250)
    assert secondary["beep_source"] == "auto"
    assert secondary["processed"]["beep"] is True


def test_secondary_beep_not_found_soft_fails(tmp_path: Path, monkeypatch) -> None:
    """BeepNotFoundError on a secondary completes the job successfully and
    flags ``beep_auto_detect_failed=True`` so the SPA can render a manual-
    align prompt instead of a job-failed toast.

    Many non-headcam cameras (iPhone tripod, RO position, AGC'd) just
    don't capture a sustained 2-5 kHz tone. Aborting the import on every
    one of those is the bad UX we're avoiding here.
    """
    client, _ = _seed_project_with_secondary(tmp_path)
    from splitsmith.beep import detect as bd
    from splitsmith.ui import audio as audio_helpers

    def boom(*a, **kw):  # type: ignore[no-untyped-def]
        raise bd.BeepNotFoundError("no candidate")

    monkeypatch.setattr(audio_helpers, "detect_video_beep", boom)

    sec_id = _video_id_for(client, 1, "secondary")
    resp = client.post(f"/api/shooters/me/stages/1/videos/{sec_id}/detect-beep")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final

    proj = client.get("/api/shooters/me/project").json()
    secondary = next(v for v in proj["stages"][0]["videos"] if v["role"] == "secondary")
    assert secondary["beep_time"] is None
    assert secondary["beep_source"] == "auto"
    assert secondary["beep_auto_detect_failed"] is True
    assert secondary["beep_candidates"] == []
    assert secondary["processed"]["beep"] is True
    assert secondary["processed"]["trim"] is False


def test_primary_beep_not_found_still_fails_job(tmp_path: Path, monkeypatch) -> None:
    """Primary failure stays loud: the whole pipeline hangs off the primary's
    beep_time, so silently completing would mask a real problem."""
    client, _ = _seed_project_with_primary(tmp_path)
    from splitsmith.beep import detect as bd
    from splitsmith.ui import audio as audio_helpers

    def boom(*a, **kw):  # type: ignore[no-untyped-def]
        raise bd.BeepNotFoundError("no candidate")

    monkeypatch.setattr(audio_helpers, "detect_primary_beep", boom)
    monkeypatch.setattr(audio_helpers, "detect_video_beep", boom)

    resp = client.post("/api/shooters/me/stages/1/detect-beep")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "failed"
    assert "no candidate" in final["error"]


def test_secondary_falls_back_to_cross_align(tmp_path: Path, monkeypatch) -> None:
    """When in-stream beep detection fails on a secondary, the worker should
    fall back to ``cross_align`` and -- if confidence clears the floor --
    set ``beep_source='aligned'`` with ``beep_time`` pulled from the
    cross-correlation result. This is the path that recovers iPhone /
    tripod-cam imports where the buzzer wasn't picked up cleanly."""
    client, _ = _seed_project_with_secondary(tmp_path)
    from splitsmith import cross_align as ca
    from splitsmith.beep import cross as beep_cross_mod
    from splitsmith.beep import detect as bd
    from splitsmith.ui import audio as audio_helpers

    # Prime the primary with a beep so the alignment branch has something
    # to project onto.
    _stub_detect(monkeypatch, beep_time=4.0)
    primary_id = _video_id_for(client, 1, "primary")
    _wait_for_job(
        client,
        client.post(f"/api/shooters/me/stages/1/videos/{primary_id}/detect-beep").json()["id"],
    )

    # Make the secondary's beep detection raise + the audio paths "exist"
    # + the aligner return a high-confidence result. We don't touch real
    # ffmpeg / soundfile -- the helper inside _try_align_secondary_to_primary
    # gates on file existence, so we patch that gate too.
    def boom(*a, **kw):  # type: ignore[no-untyped-def]
        raise bd.BeepNotFoundError("no candidate")

    def fake_load(_path):  # type: ignore[no-untyped-def]
        return np.zeros(48000, dtype=np.float32), 48000

    def fake_align(*a, **kw):  # type: ignore[no-untyped-def]
        return ca.CrossAlignResult(
            secondary_beep_time=7.250,
            confidence=4.2,
            peak_correlation=0.85,
            lag_seconds=3.25,
        )

    monkeypatch.setattr(audio_helpers, "detect_video_beep", boom)
    monkeypatch.setattr(bd, "load_audio", fake_load)
    monkeypatch.setattr(beep_cross_mod, "place_beep", _as_placement(fake_align))
    # Pretend both audio caches are on disk so the helper proceeds past
    # its existence guard.
    monkeypatch.setattr(Path, "exists", lambda self: True)

    sec_id = _video_id_for(client, 1, "secondary")
    final = _wait_for_job(
        client,
        client.post(f"/api/shooters/me/stages/1/videos/{sec_id}/detect-beep").json()["id"],
    )
    assert final["status"] == "succeeded", final

    proj = client.get("/api/shooters/me/project").json()
    secondary = next(v for v in proj["stages"][0]["videos"] if v["role"] == "secondary")
    assert secondary["beep_time"] == pytest.approx(7.250)
    assert secondary["beep_source"] == "aligned"
    assert secondary["beep_alignment_confidence"] == pytest.approx(4.2)
    assert secondary["beep_auto_detect_failed"] is False
    assert secondary["processed"]["beep"] is True


def test_secondary_low_confidence_alignment_stays_soft_failed(tmp_path: Path, monkeypatch) -> None:
    """A cross-align result below the confidence floor must not be promoted
    to ``beep_source='aligned'`` -- the secondary stays in the
    soft-failed state so the user gets the manual-align prompt instead
    of a wrong auto-set timestamp. The confidence is still saved as a
    diagnostic so the SPA can render "tried, sub-floor" distinctly from
    "never tried"."""
    client, _ = _seed_project_with_secondary(tmp_path)
    from splitsmith import cross_align as ca
    from splitsmith.beep import cross as beep_cross_mod
    from splitsmith.beep import detect as bd
    from splitsmith.ui import audio as audio_helpers

    _stub_detect(monkeypatch, beep_time=4.0)
    primary_id = _video_id_for(client, 1, "primary")
    _wait_for_job(
        client,
        client.post(f"/api/shooters/me/stages/1/videos/{primary_id}/detect-beep").json()["id"],
    )

    def boom(*a, **kw):  # type: ignore[no-untyped-def]
        raise bd.BeepNotFoundError("no candidate")

    def fake_load(_path):  # type: ignore[no-untyped-def]
        return np.zeros(48000, dtype=np.float32), 48000

    def fake_align(*a, **kw):  # type: ignore[no-untyped-def]
        return ca.CrossAlignResult(
            secondary_beep_time=7.250,
            confidence=1.05,  # below floor 1.10
            peak_correlation=0.42,
            lag_seconds=3.25,
        )

    monkeypatch.setattr(audio_helpers, "detect_video_beep", boom)
    monkeypatch.setattr(bd, "load_audio", fake_load)
    monkeypatch.setattr(beep_cross_mod, "place_beep", _as_placement(fake_align))
    monkeypatch.setattr(Path, "exists", lambda self: True)

    sec_id = _video_id_for(client, 1, "secondary")
    final = _wait_for_job(
        client,
        client.post(f"/api/shooters/me/stages/1/videos/{sec_id}/detect-beep").json()["id"],
    )
    assert final["status"] == "succeeded"

    proj = client.get("/api/shooters/me/project").json()
    secondary = next(v for v in proj["stages"][0]["videos"] if v["role"] == "secondary")
    assert secondary["beep_time"] is None
    assert secondary["beep_source"] == "auto"
    assert secondary["beep_auto_detect_failed"] is True
    # Even on rejection the confidence is preserved -- the SPA needs it to
    # render "tried, conf 1.05" distinctly from "never tried".
    assert secondary["beep_alignment_confidence"] == pytest.approx(1.05)


def test_secondary_in_stream_success_runs_cross_align_sanity_check(tmp_path: Path, monkeypatch) -> None:
    """When in-stream beep_detect succeeds on a secondary, cross-correlation
    still runs as a sanity check. When the two methods agree (small delta),
    the in-stream result wins, ``beep_source`` stays ``"auto"``, but the
    confidence + delta are surfaced as diagnostics so the SPA can tell
    "we double-checked" from "we never tried"."""
    client, _ = _seed_project_with_secondary(tmp_path)
    from splitsmith import cross_align as ca
    from splitsmith.beep import cross as beep_cross_mod
    from splitsmith.beep import detect as bd

    _stub_detect(monkeypatch, beep_time=4.0)
    primary_id = _video_id_for(client, 1, "primary")
    _wait_for_job(
        client,
        client.post(f"/api/shooters/me/stages/1/videos/{primary_id}/detect-beep").json()["id"],
    )

    # Secondary in-stream succeeds at 7.250 s; cross-align lands within
    # 15 ms. The fake_load + Path.exists patches let _try_align run on
    # synthetic audio without touching real WAVs.
    _stub_detect(monkeypatch, beep_time=7.250)

    def fake_load(_path):  # type: ignore[no-untyped-def]
        return np.zeros(48000, dtype=np.float32), 48000

    def fake_align(*a, **kw):  # type: ignore[no-untyped-def]
        return ca.CrossAlignResult(
            secondary_beep_time=7.265,
            confidence=1.30,
            peak_correlation=0.65,
            lag_seconds=3.265,
        )

    monkeypatch.setattr(bd, "load_audio", fake_load)
    monkeypatch.setattr(beep_cross_mod, "place_beep", _as_placement(fake_align))
    monkeypatch.setattr(Path, "exists", lambda self: True)

    sec_id = _video_id_for(client, 1, "secondary")
    final = _wait_for_job(
        client,
        client.post(f"/api/shooters/me/stages/1/videos/{sec_id}/detect-beep").json()["id"],
    )
    assert final["status"] == "succeeded"

    proj = client.get("/api/shooters/me/project").json()
    secondary = next(v for v in proj["stages"][0]["videos"] if v["role"] == "secondary")
    assert secondary["beep_time"] == pytest.approx(7.250)
    assert secondary["beep_source"] == "auto"
    assert secondary["beep_alignment_confidence"] == pytest.approx(1.30)
    # delta = in_stream - cross_align = 7.250 - 7.265 = -15 ms
    assert secondary["beep_alignment_delta_ms"] == pytest.approx(-15.0)


def test_secondary_in_stream_disagreement_flagged_not_overridden(tmp_path: Path, monkeypatch) -> None:
    """When in-stream and cross-align disagree by hundreds of ms (typical
    of a steel-strike mistaken for the buzzer), the in-stream result is
    KEPT as ``beep_time`` -- in-stream has frequency-domain info
    cross-align doesn't, so we don't auto-override. The delta is
    surfaced so the SPA can show the user a "use cross-align?" prompt."""
    client, _ = _seed_project_with_secondary(tmp_path)
    from splitsmith import cross_align as ca
    from splitsmith.beep import cross as beep_cross_mod
    from splitsmith.beep import detect as bd

    _stub_detect(monkeypatch, beep_time=4.0)
    primary_id = _video_id_for(client, 1, "primary")
    _wait_for_job(
        client,
        client.post(f"/api/shooters/me/stages/1/videos/{primary_id}/detect-beep").json()["id"],
    )

    _stub_detect(monkeypatch, beep_time=10.000)

    def fake_load(_path):  # type: ignore[no-untyped-def]
        return np.zeros(48000, dtype=np.float32), 48000

    def fake_align(*a, **kw):  # type: ignore[no-untyped-def]
        return ca.CrossAlignResult(
            secondary_beep_time=7.500,
            confidence=1.40,
            peak_correlation=0.70,
            lag_seconds=3.500,
        )

    monkeypatch.setattr(bd, "load_audio", fake_load)
    monkeypatch.setattr(beep_cross_mod, "place_beep", _as_placement(fake_align))
    monkeypatch.setattr(Path, "exists", lambda self: True)

    sec_id = _video_id_for(client, 1, "secondary")
    final = _wait_for_job(
        client,
        client.post(f"/api/shooters/me/stages/1/videos/{sec_id}/detect-beep").json()["id"],
    )
    assert final["status"] == "succeeded"

    proj = client.get("/api/shooters/me/project").json()
    secondary = next(v for v in proj["stages"][0]["videos"] if v["role"] == "secondary")
    # In-stream wins -- not overridden.
    assert secondary["beep_time"] == pytest.approx(10.000)
    assert secondary["beep_source"] == "auto"
    # 2.5 s disagreement, surfaced for the SPA banner.
    assert secondary["beep_alignment_confidence"] == pytest.approx(1.40)
    assert secondary["beep_alignment_delta_ms"] == pytest.approx(2500.0)
    # The placement from the primary is the review's other candidate (#1442).
    assert secondary["beep_aligned_time"] == pytest.approx(7.500)


def test_a_confident_secondary_beep_that_disagrees_with_the_primary_is_not_auto_trusted(
    tmp_path: Path, monkeypatch
) -> None:
    """#1442: an in-stream beep confident enough to be auto-trusted is left
    for review when a confident placement from the primary disagrees by more
    than 60 ms, and auto-trusted when they agree."""
    client, _ = _seed_project_with_secondary(tmp_path)
    from splitsmith import cross_align as ca
    from splitsmith.beep import cross as beep_cross_mod
    from splitsmith.beep import detect as bd

    _stub_detect(monkeypatch, beep_time=4.0)
    primary_id = _video_id_for(client, 1, "primary")
    _wait_for_job(
        client, client.post(f"/api/shooters/me/stages/1/videos/{primary_id}/detect-beep").json()["id"]
    )
    monkeypatch.setattr(bd, "load_audio", lambda _p: (np.zeros(48000, dtype=np.float32), 48000))
    monkeypatch.setattr(Path, "exists", lambda self: True)
    sec_id = _video_id_for(client, 1, "secondary")

    def run(placed_at: float) -> dict:
        def fake_align(*a, **kw):  # type: ignore[no-untyped-def]
            return ca.CrossAlignResult(
                secondary_beep_time=placed_at, confidence=1.5, peak_correlation=0.0, lag_seconds=0.0
            )

        monkeypatch.setattr(beep_cross_mod, "place_beep", _as_placement(fake_align))
        _stub_detect(monkeypatch, beep_time=7.0, confidence=0.99)
        job = client.post(f"/api/shooters/me/stages/1/videos/{sec_id}/detect-beep").json()["id"]
        assert _wait_for_job(client, job)["status"] == "succeeded"
        proj = client.get("/api/shooters/me/project").json()
        return next(v for v in proj["stages"][0]["videos"] if v["role"] == "secondary")

    assert run(7.02)["beep_reviewed"] is True
    disagreeing = run(7.20)
    assert disagreeing["beep_reviewed"] is False
    assert disagreeing["beep_time"] == pytest.approx(7.0)
    assert disagreeing["beep_aligned_time"] == pytest.approx(7.20)


def test_confirming_the_primary_beep_places_the_unconfirmed_secondaries_again(
    tmp_path: Path, monkeypatch
) -> None:
    """#1442: a confirmed primary beep is the best reference for the stage's
    other cameras, so confirming it runs beep detection again for every
    secondary nobody confirmed; a confirmed secondary is left alone."""
    client, _ = _seed_project_with_secondary(tmp_path)
    primary_id = _video_id_for(client, 1, "primary")
    sec_id = _video_id_for(client, 1, "secondary")
    shooter_root = tmp_path / "match" / "shooters" / "me"
    project = MatchProject.load(shooter_root)
    for v in project.stages[0].videos:
        v.beep_time = 5.0
        v.beep_source = "auto"
    project.save(shooter_root)

    state = client.app.state.splitsmith_state
    submitted: list[tuple[str, str | None]] = []
    real_submit = state.jobs.submit

    async def spy(*, kind, **kw):  # noqa: ANN001, ANN003, ANN202
        submitted.append((kind, kw.get("video_id")))
        return await real_submit(kind=kind, **kw)

    monkeypatch.setattr(state.jobs, "submit", spy)

    def confirm() -> None:
        resp = client.post(
            f"/api/shooters/me/stages/1/videos/{primary_id}/beep/review", json={"reviewed": True}
        )
        assert resp.status_code == 200, resp.text

    confirm()
    assert ("detect_beep", sec_id) in submitted

    submitted.clear()
    project = MatchProject.load(shooter_root)
    secondary = project.stages[0].find_video_by_id(sec_id)
    assert secondary is not None
    secondary.beep_reviewed = True
    project.save(shooter_root)
    confirm()
    assert ("detect_beep", sec_id) not in submitted


def test_secondary_soft_fail_clears_on_manual_override(tmp_path: Path, monkeypatch) -> None:
    """Manual beep override clears ``beep_auto_detect_failed`` so the
    "align manually" prompt disappears once the user does so."""
    client, _ = _seed_project_with_secondary(tmp_path)
    from splitsmith.beep import detect as bd
    from splitsmith.ui import audio as audio_helpers

    def boom(*a, **kw):  # type: ignore[no-untyped-def]
        raise bd.BeepNotFoundError("no candidate")

    monkeypatch.setattr(audio_helpers, "detect_video_beep", boom)
    sec_id = _video_id_for(client, 1, "secondary")
    final = _wait_for_job(
        client,
        client.post(f"/api/shooters/me/stages/1/videos/{sec_id}/detect-beep").json()["id"],
    )
    assert final["status"] == "succeeded"

    proj = client.post(
        f"/api/shooters/me/stages/1/videos/{sec_id}/beep",
        json={"beep_time": 2.5},
    ).json()
    secondary = next(v for v in proj["stages"][0]["videos"] if v["role"] == "secondary")
    assert secondary["beep_time"] == pytest.approx(2.5)
    assert secondary["beep_source"] == "manual"
    assert secondary["beep_auto_detect_failed"] is False


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
    j1 = client.post(f"/api/shooters/me/stages/1/videos/{primary_id}/detect-beep").json()
    j2 = client.post(f"/api/shooters/me/stages/1/videos/{primary_id}/detect-beep").json()
    j3 = client.post(f"/api/shooters/me/stages/1/videos/{sec_id}/detect-beep").json()
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

    resp = client.post(f"/api/shooters/me/stages/1/videos/{sec_id}/beep", json={"beep_time": 4.5})
    assert resp.status_code == 200
    proj = resp.json()
    secondary = next(v for v in proj["stages"][0]["videos"] if v["role"] == "secondary")
    assert secondary["beep_time"] == pytest.approx(4.5)
    assert secondary["beep_source"] == "manual"

    # Clear flips back to "no beep yet" and resets processed.beep
    resp = client.post(f"/api/shooters/me/stages/1/videos/{sec_id}/beep", json={"beep_time": None})
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

    resp = client.post("/api/shooters/me/stages/1/detect-beep")
    assert resp.status_code == 200
    job = resp.json()
    primary_id = _video_id_for(client, 1, "primary")
    assert job["video_id"] == primary_id  # forwarded to per-video pipeline
    final = _wait_for_job(client, job["id"])
    assert final["status"] == "succeeded", final


def test_per_video_endpoint_404_for_unknown_video_id(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.post("/api/shooters/me/stages/1/videos/deadbeef0000/detect-beep")
    assert resp.status_code == 404


def test_beep_review_default_false_after_auto_detect(tmp_path: Path, monkeypatch) -> None:
    """A freshly auto-detected beep needs explicit user review -- defaults
    to ``beep_reviewed=False`` so the SPA can render the warning pill."""
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=12.453)

    job = client.post("/api/shooters/me/stages/1/detect-beep").json()
    final = _wait_for_job(client, job["id"])
    assert final["status"] == "succeeded"
    primary = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    assert primary["beep_time"] == 12.453
    assert primary["beep_source"] == "auto"
    assert primary["beep_reviewed"] is False


def test_beep_review_manual_entry_auto_marks_reviewed(tmp_path: Path) -> None:
    """Manual override implies the user looked at the waveform to type
    the value -- skip the review pill (#71)."""
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.post("/api/shooters/me/stages/1/beep", json={"beep_time": 12.5})
    assert resp.status_code == 200
    primary = resp.json()["stages"][0]["videos"][0]
    assert primary["beep_source"] == "manual"
    assert primary["beep_reviewed"] is True


def test_beep_review_endpoint_flips_flag(tmp_path: Path, monkeypatch) -> None:
    """The dedicated review endpoint flips the flag without re-running
    detection or trim."""
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=12.453)
    job = client.post("/api/shooters/me/stages/1/detect-beep").json()
    _wait_for_job(client, job["id"])
    primary = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    video_id = primary["video_id"]
    assert primary["beep_reviewed"] is False

    resp = client.post(
        f"/api/shooters/me/stages/1/videos/{video_id}/beep/review",
        json={"reviewed": True},
    )
    assert resp.status_code == 200
    assert resp.json()["stages"][0]["videos"][0]["beep_reviewed"] is True

    resp = client.post(
        f"/api/shooters/me/stages/1/videos/{video_id}/beep/review",
        json={"reviewed": False},
    )
    assert resp.status_code == 200
    assert resp.json()["stages"][0]["videos"][0]["beep_reviewed"] is False


def test_beep_review_seeds_stub_audit(tmp_path: Path) -> None:
    """Confirming a beep leaves a concrete audit document behind so status
    surfaces read a document rather than inferring from absence."""
    client, _ = _seed_project_with_primary(tmp_path)
    shooter_root = tmp_path / "match" / "shooters" / "me"
    project = MatchProject.load(shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 12.453
    project.save(shooter_root)

    audit_file = shooter_root / "audit" / "stage1.json"
    assert not audit_file.exists()

    video_id = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]["video_id"]
    resp = client.post(
        f"/api/shooters/me/stages/1/videos/{video_id}/beep/review",
        json={"reviewed": True},
    )
    assert resp.status_code == 200

    payload = json.loads(audit_file.read_text(encoding="utf-8"))
    assert payload == {"shots": [], "detection": "none"}


def test_beep_review_does_not_clobber_existing_audit(tmp_path: Path) -> None:
    """Re-confirming a beep on an audited stage must not wipe shot data."""
    client, _ = _seed_project_with_primary(tmp_path)
    shooter_root = tmp_path / "match" / "shooters" / "me"
    project = MatchProject.load(shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 12.453
    project.save(shooter_root)

    audit_file = shooter_root / "audit" / "stage1.json"
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    audit_file.write_text(json.dumps({"shots": [{"shot_number": 1, "time": 5.5}]}), encoding="utf-8")

    video_id = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]["video_id"]
    resp = client.post(
        f"/api/shooters/me/stages/1/videos/{video_id}/beep/review",
        json={"reviewed": True},
    )
    assert resp.status_code == 200

    payload = json.loads(audit_file.read_text(encoding="utf-8"))
    assert payload["shots"] == [{"shot_number": 1, "time": 5.5}]


def test_beep_review_400_when_no_beep_yet(tmp_path: Path) -> None:
    """Can't mark a beep reviewed before one has been detected -- the
    server refuses with 400 so the SPA isn't tempted to show a green
    pill on a video with no beep_time."""
    client, _ = _seed_project_with_primary(tmp_path)
    primary = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    video_id = primary["video_id"]

    resp = client.post(
        f"/api/shooters/me/stages/1/videos/{video_id}/beep/review",
        json={"reviewed": True},
    )
    assert resp.status_code == 400


def test_beep_review_resets_on_candidate_switch(tmp_path: Path, monkeypatch) -> None:
    """Switching to a different ranked candidate is a fresh claim about
    which moment is the beep -- prior approval doesn't carry over (#71)."""
    from splitsmith.config import BeepCandidate

    candidates = [
        BeepCandidate(time=12.453, score=0.9, peak_amplitude=0.8, duration_ms=300.0),
        BeepCandidate(time=11.000, score=0.7, peak_amplitude=0.6, duration_ms=280.0),
    ]
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=12.453, candidates=candidates)
    job = client.post("/api/shooters/me/stages/1/detect-beep").json()
    _wait_for_job(client, job["id"])
    primary = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    video_id = primary["video_id"]

    # User reviews candidate #1.
    client.post(f"/api/shooters/me/stages/1/videos/{video_id}/beep/review", json={"reviewed": True})
    # Then switches to candidate #2.
    resp = client.post("/api/shooters/me/stages/1/beep/select", json={"time": 11.000})
    assert resp.status_code == 200
    primary = resp.json()["stages"][0]["videos"][0]
    assert primary["beep_time"] == 11.000
    assert primary["beep_reviewed"] is False  # reset on switch


def test_beep_review_resets_on_redetect(tmp_path: Path, monkeypatch) -> None:
    """Re-running detection is a fresh claim -- prior approval doesn't
    carry over."""
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=12.453)
    job = client.post("/api/shooters/me/stages/1/detect-beep").json()
    _wait_for_job(client, job["id"])
    primary = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    video_id = primary["video_id"]
    client.post(f"/api/shooters/me/stages/1/videos/{video_id}/beep/review", json={"reviewed": True})

    _stub_detect(monkeypatch, beep_time=10.0)
    job2 = client.post("/api/shooters/me/stages/1/detect-beep").json()
    _wait_for_job(client, job2["id"])
    primary = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    assert primary["beep_time"] == 10.0
    assert primary["beep_reviewed"] is False


def test_shot_detect_gated_on_beep_review(tmp_path: Path, monkeypatch) -> None:
    """Auto-detected beeps don't trigger the shot-detect ensemble until
    the user reviews the beep (#71). Wasting CLAP / GBDT cycles
    on an unconfirmed beep is the failure mode this gate prevents."""
    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=12.453)

    job = client.post("/api/shooters/me/stages/1/detect-beep").json()
    _wait_for_job(client, job["id"])

    # No shot_detect job queued -- the chain stops at trim until the
    # user confirms.
    jobs = client.get("/api/me/jobs").json()
    assert not any(j["kind"] == "shot_detect" for j in jobs), [(j["kind"], j["status"]) for j in jobs]


def test_marking_reviewed_kicks_off_shot_detect(tmp_path: Path, monkeypatch) -> None:
    """Once the user confirms the beep on a primary that's already
    trimmed, the gated shot-detect fires -- this is the explicit
    unblock point. Stub the heavy ensemble entrypoint so the test
    doesn't pay model-loading cost; we only assert a job was queued."""
    import splitsmith.ui.server as server_mod
    from splitsmith.ui import audio as audio_helpers_mod

    monkeypatch.setattr(
        audio_helpers_mod,
        "ensure_video_audit_trim",
        lambda *a, **kw: Path("dummy.mp4"),
    )
    monkeypatch.setattr(server_mod, "_get_ensemble_runtime", lambda: object())

    client, _ = _seed_project_with_primary(tmp_path)
    _stub_detect(monkeypatch, beep_time=12.453)
    job = client.post("/api/shooters/me/stages/1/detect-beep").json()
    _wait_for_job(client, job["id"])
    _wait_for_jobs_to_drain(client)  # the chained trim

    primary = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    assert primary["processed"]["trim"] is True  # trim cached
    video_id = primary["video_id"]
    jobs_before = client.get("/api/me/jobs").json()
    assert not any(j["kind"] == "shot_detect" for j in jobs_before)

    resp = client.post(
        f"/api/shooters/me/stages/1/videos/{video_id}/beep/review",
        json={"reviewed": True},
    )
    assert resp.status_code == 200
    jobs_after = client.get("/api/me/jobs").json()
    assert any(j["kind"] == "shot_detect" for j in jobs_after)


def test_trim_chains_shot_detect_when_reviewed_lands_mid_trim(tmp_path: Path, monkeypatch) -> None:
    """A "Mark reviewed" click that lands WHILE the trim is encoding must
    still unblock shot detection.

    Repro for "approved a beep, then nothing to audit": the user selects a
    ranked candidate (beep_reviewed -> False, trim cache cleared), the
    re-trim starts, and the user immediately marks the beep reviewed. At
    that moment ``set_beep_reviewed`` no-ops because the trim isn't cached
    yet, so the running trim worker is the only thing left that can chain
    shot-detect. It must read ``beep_reviewed`` from the freshly reloaded
    project, not the stale snapshot it captured at job start.
    """
    import splitsmith.ui.server as server_mod
    from splitsmith.ui import audio as audio_helpers_mod

    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    primary.beep_reviewed = False  # candidate just selected; not yet reviewed
    project.stages[0].time_seconds = 10.0
    project.save(shooter_root)
    project.resolve_video_path(shooter_root, primary.path).resolve().write_bytes(b"S")

    monkeypatch.setattr(server_mod, "_get_ensemble_runtime", lambda: object())

    def fake_trim(root, *a, **kw):  # type: ignore[no-untyped-def]
        # Simulate the user clicking "Mark reviewed" while ffmpeg runs:
        # the flag flips on disk, but set_beep_reviewed has already
        # no-op'd because processed["trim"] was still False.
        proj = MatchProject.load(root)
        prim = proj.stages[0].primary()
        assert prim is not None
        prim.beep_reviewed = True
        proj.save(root)
        return Path("dummy.mp4")

    monkeypatch.setattr(audio_helpers_mod, "ensure_video_audit_trim", fake_trim)

    resp = client.post("/api/shooters/me/stages/1/trim")
    assert resp.status_code == 200
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final

    jobs_after = client.get("/api/me/jobs").json()
    assert any(j["kind"] == "shot_detect" for j in jobs_after), [(j["kind"], j["status"]) for j in jobs_after]


def test_export_stage_allows_a_manually_timed_stage(tmp_path: Path) -> None:
    """Manual matches (no scoreboard) set the duration via
    ``set_stage_time``; the export gate must then accept the stage rather
    than demanding a scoreboard import. Regression for manual matches
    being able to detect but not export."""
    client, _ = _seed_match_export_project(tmp_path, stage_count=1)
    # The no-scoreboard flow: stamp a manual duration.
    r = client.post("/api/shooters/me/stages/1/time", json={"time_seconds": 12.0})
    assert r.status_code == 200, r.text
    resp = _export_csv_only(client)
    # Gate passed -> the job is queued (not a 400 placeholder rejection).
    assert resp.status_code == 200, resp.text
    assert resp.json()["kind"] == "export"
    # And it runs to completion: the engine StageData requires a non-None
    # scorecard_updated_at, which a manual stage lacks -- the job body must
    # substitute the sentinel rather than crash. Without that fix this job
    # fails with a pydantic datetime validation error.
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final


def test_export_stage_reports_secondary_trim_filenames(tmp_path: Path, monkeypatch) -> None:
    """A per-stage export that writes a secondary-cam trim must report the
    trim's *filename* in ``result.secondary_trims``.

    ``StageExportResult.secondary_trimmed_paths`` is a ``{video_id: Path}``
    mapping; iterating it yields video-id strings, so building the result
    payload from the bare iteration raised ``'str' object has no attribute
    'name'`` and failed the whole job -- every multi-cam Generate from the
    SPA, after the trims had already been written to disk.
    """
    from splitsmith import trim
    from splitsmith.match_project import MatchProject

    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    shooter_root = project_root / "shooters" / "me"

    # Give the stage a second camera with its own beep and reachable source.
    project = MatchProject.load(shooter_root)
    secondary_src = shooter_root / "raw" / "VID1_cam2.mp4"
    secondary_src.write_bytes(b"\x00")
    registered = project.register_video(secondary_src, project_root)
    project.assign_video(registered.path, to_stage_number=1, role="secondary")
    secondary = next(v for v in project.stage(1).videos if v.role == "secondary")
    secondary.beep_time = 5.0
    secondary_id = secondary.video_id
    project.save(shooter_root)

    def fake_trim_video(source, output_path, **kwargs):  # type: ignore[no-untyped-def]
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"TRIMMED")
        return trim.TrimResult(output_path=Path(output_path), start_time=0.0, end_time=10.0)

    monkeypatch.setattr(trim, "trim_video", fake_trim_video)

    assert client.post("/api/shooters/me/stages/1/time", json={"time_seconds": 10.0}).status_code == 200
    resp = client.post(
        "/api/shooters/me/stages/1/export",
        json={
            "write_trim": True,
            "write_csv": False,
            "write_fcpxml": False,
            "write_report": False,
            "write_overlay": False,
        },
    )
    assert resp.status_code == 200, resp.text
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final
    assert final["result"]["secondary_trims"] == [f"stage1_stage-1_cam_{secondary_id}_trimmed.mp4"]


def test_full_bundle_export_still_reports_shot_anomalies(tmp_path: Path, monkeypatch) -> None:
    """The filter is scoped to requests that consume no shots.

    A CSV/report run reads the audit, so "no shots" is a genuine finding
    there and must keep surfacing -- with the report.txt pointer, which
    that run does write.
    """
    client, _ = _seed_trims_only_stage(tmp_path, monkeypatch)

    resp = client.post(
        "/api/shooters/me/stages/1/export",
        json={
            "write_trim": True,
            "write_csv": True,
            "write_fcpxml": False,
            "write_report": True,
            "write_overlay": False,
        },
    )
    assert resp.status_code == 200, resp.text
    final = _wait_for_job(client, resp.json()["id"])

    assert final["status"] == "succeeded", final
    assert final["result"]["anomalies"] == [
        "No shots detected in the stage window.",
        # The CSV was asked for and could not be written -- an export
        # failure, reported alongside the shot finding rather than instead
        # of it.
        "csv not written: no shots audited",
    ]
    assert "2 anomalies -- see report.txt" in final["message"]


def test_shot_detect_does_not_adopt_other_shooters_job(two_shooter_match: Path) -> None:
    """Issue #664: with shooter A's shot-detect still in flight on stage 1,
    shooter B's POST for stage 1 must start B's own job - the dedupe used
    to match on (kind, stage_number) alone and handed B a snapshot of A's
    job, so B's detection could never start."""
    app = _match_create_app(project_root=two_shooter_match)
    client = _MatchClient(app)
    state = app.state.splitsmith_state

    release = threading.Event()

    def _blocked(_handle, **_args) -> None:
        release.wait(timeout=10.0)

    state.jobs.bodies.register("shot_detect", _blocked)

    try:
        resp_a = client.post("/api/shooters/anders/stages/1/shot-detect")
        assert resp_a.status_code == 200, resp_a.text
        job_a = resp_a.json()

        resp_b = client.post("/api/shooters/mathias/stages/1/shot-detect")
        assert resp_b.status_code == 200, resp_b.text
        job_b = resp_b.json()
        assert job_b["id"] != job_a["id"], "shooter B adopted shooter A's job"
        assert job_a["shooter_slug"] == "anders"
        assert job_b["shooter_slug"] == "mathias"

        # The same shooter resubmitting still adopts their own active job.
        resp_a2 = client.post("/api/shooters/anders/stages/1/shot-detect")
        assert resp_a2.status_code == 200, resp_a2.text
        assert resp_a2.json()["id"] == job_a["id"]
    finally:
        release.set()
    _wait_for_job(client, job_a["id"])
    _wait_for_job(client, job_b["id"])


def test_export_stage_summary_card_flag_reaches_the_exporter_and_the_run_record(
    tmp_path: Path, monkeypatch
) -> None:
    """The endpoint forwards the card flags plus the scoreboard's scorecard,
    the shooter's name and the manual-time flag into ``export_stage``, and
    the export-run record lists the card it produced (#972)."""
    from splitsmith import summary_card
    from splitsmith.match_project import MatchProject, StageScorecard

    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(shooter_root)
    project.competitor_name = "M. Axell"
    project.stage(1).scorecard = StageScorecard(hit_factor=12.0, alphas=10)
    project.save(shooter_root)

    captured: dict[str, object] = {}

    def fake_card(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        kwargs["png_path"].write_bytes(b"")
        kwargs["mov_path"].write_bytes(b"")
        return summary_card.SummaryCardResult(png_path=kwargs["png_path"], mov_path=kwargs["mov_path"])

    monkeypatch.setattr(summary_card, "render_summary_card", fake_card)

    resp = client.post(
        "/api/shooters/me/stages/1/export",
        json={
            "write_trim": False,
            "write_csv": False,
            "write_fcpxml": False,
            "write_report": False,
            "write_overlay": False,
            "write_summary_card": True,
            "summary_hold_seconds": 2.5,
        },
    )
    assert resp.status_code == 200, resp.text
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final
    assert final["result"]["summary_card"] == "stage1_stage-1_summary.mov"
    assert captured["label"] == "M. Axell"
    assert captured["seconds"] == 2.5
    assert captured["data"].scorecard.hit_factor == 12.0
    assert captured["data"].stage_time_seconds == 10.0

    latest = client.get("/api/shooters/me/exports/runs").json()["runs"][0]
    assert "summary_card" in latest["formats"]
    assert {"filename": "stage1_stage-1_summary.mov", "kind": "summary_card"} in [
        {"filename": a["filename"], "kind": a["kind"]} for a in latest["artifacts"]
    ]


def test_export_stage_forwards_the_overlay_style(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.overlay import render as overlay_render

    client, _root = _seed_match_export_project(tmp_path, stage_count=1)
    seen: dict[str, object] = {}

    def fake_render(**kwargs: object) -> Path:
        seen.update(kwargs)
        out = kwargs["output_path"]
        assert isinstance(out, Path)
        out.write_bytes(b"mov")
        return out

    monkeypatch.setattr(overlay_render, "render_overlay", fake_render)
    resp = client.post(
        "/api/shooters/me/stages/1/export",
        json={
            "write_trim": False,
            "write_csv": False,
            "write_fcpxml": False,
            "write_report": False,
            "write_overlay": True,
            "overlay_variant": "ticker",
            "overlay_class_labels": False,
            "overlay_position": "top-left",
        },
    )
    assert resp.status_code == 200, resp.text
    assert _wait_for_job(client, resp.json()["id"])["status"] == "succeeded"
    assert seen["variant"] == "ticker"
    from splitsmith.overlay.hud import HudOptions

    options = seen["hud_options"]
    assert isinstance(options, HudOptions)
    assert options.class_labels is False and options.position == "top-left"


def test_a_secondary_beep_may_sit_before_its_clip_a_primary_never(tmp_path: Path) -> None:
    """A secondary camera that started during the beep has its beep before
    0; a primary's beep is where its shots are measured from and must be in
    its clip. More than 2 s before the clip is no cut-off beep."""
    client, _ = _seed_project_with_secondary(tmp_path)
    sec_id = _video_id_for(client, 1, "secondary")
    prim_id = _video_id_for(client, 1, "primary")

    ok = client.post(f"/api/shooters/me/stages/1/videos/{sec_id}/beep", json={"beep_time": -0.21})
    assert ok.status_code == 200, ok.text
    assert next(v for v in ok.json()["stages"][0]["videos"] if v["video_id"] == sec_id)[
        "beep_time"
    ] == pytest.approx(-0.21)

    assert (
        client.post(f"/api/shooters/me/stages/1/videos/{prim_id}/beep", json={"beep_time": -0.21}).status_code
        == 400
    )
    assert (
        client.post(f"/api/shooters/me/stages/1/videos/{sec_id}/beep", json={"beep_time": -2.5}).status_code
        == 400
    )


def test_audit_is_served_and_saved_on_the_clips_clock(tmp_path: Path, monkeypatch) -> None:
    """#1497: an audit made on the source's clock is served moved onto the
    clip Audit plays, and a save stores the clip's beep whatever the page
    fell back to, so ``ms_after_beep`` never reads as before the beep."""
    import json

    from splitsmith.ui import server as _srv

    client, _ = _seed_project_with_primary(tmp_path)
    monkeypatch.setattr(_srv, "_video_beep_in_clip", lambda *a, **k: 5.0)
    audit_dir = tmp_path / "match" / "shooters" / "me" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    stored = {
        "stage_number": 1,
        "beep_time": 19.244,
        "shots": [{"shot_number": 1, "time": 21.0, "ms_after_beep": 1756, "source": "detected"}],
        "_candidates_pending_audit": {"candidates": [{"candidate_number": 1, "time": 21.0}]},
    }
    (audit_dir / "stage1.json").write_text(json.dumps(stored), encoding="utf-8")

    served = client.get("/api/shooters/me/stages/1/audit").json()
    assert served["beep_time"] == 5.0
    assert served["shots"][0]["time"] == 6.756
    assert served["_candidates_pending_audit"]["candidates"][0]["time"] == 6.756
    on_disk = json.loads((audit_dir / "stage1.json").read_text(encoding="utf-8"))
    assert on_disk["beep_time"] == 19.244, "a read never writes"

    # The page saves what it was served, but with the beep it fell back to.
    served["beep_time"] = 19.244
    saved = client.put("/api/shooters/me/stages/1/audit", json=served)
    assert saved.status_code == 200, saved.text
    on_disk = json.loads((audit_dir / "stage1.json").read_text(encoding="utf-8"))
    assert on_disk["beep_time"] == 5.0
    assert on_disk["shots"][0]["time"] == 6.756
    assert on_disk["shots"][0]["ms_after_beep"] == 1756
