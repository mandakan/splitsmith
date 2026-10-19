"""The confirmed beep's distance from its tone, stored on the video
(spec 2026-10-17; the measured-for rule is the plan's ruling)."""

from __future__ import annotations

from splitsmith.match_project import StageVideo, tone_flag_ms

from .test_beep_tone_routes import overlap  # noqa: F401


def _video(**kw) -> StageVideo:
    base = {"video_id": "v1", "path": "raw/v.mov", "role": "primary", "beep_time": 5.0, "beep_reviewed": True}
    return StageVideo(**{**base, **kw})


def test_a_measured_reviewed_beep_counts() -> None:
    assert tone_flag_ms(_video(beep_tone_offset_ms=-51.0, beep_tone_for=5.0)) == -51.0


def test_a_moved_beep_does_not_flag() -> None:
    assert tone_flag_ms(_video(beep_tone_offset_ms=-51.0, beep_tone_for=4.9)) is None


def test_an_unreviewed_beep_does_not_flag() -> None:
    assert tone_flag_ms(_video(beep_reviewed=False, beep_tone_offset_ms=-51.0, beep_tone_for=5.0)) is None


def test_an_older_project_without_the_fields_loads() -> None:
    v = StageVideo.model_validate({"video_id": "v1", "path": "raw/v.mov", "role": "primary"})
    assert v.beep_tone_offset_ms is None and tone_flag_ms(v) is None


def test_a_manual_beep_stores_its_tone_offset(overlap) -> None:  # noqa: F811
    from splitsmith.match_project import MatchProject

    client, vid = overlap
    r = client.post(f"/api/shooters/me/stages/1/videos/{vid}/beep", json={"beep_time": 1.0})
    assert r.status_code == 200, r.text
    video = MatchProject.model_validate(r.json()).stage(1).find_video_by_id(vid)
    assert video.beep_tone_for == 1.0
    assert abs(tone_flag_ms(video)) < 6


def test_triage_carries_the_worst_counted_offset(overlap) -> None:  # noqa: F811
    client, vid = overlap
    client.post(f"/api/shooters/me/stages/1/videos/{vid}/beep", json={"beep_time": 0.96})
    body = client.get("/api/match/triage").json()
    cell = next(c for c in body["cells"] if c["stage_number"] == 1)
    assert body["beep_tone_flag_ms"] == 20.0
    assert cell["beep_tone_offset_ms"] is not None and cell["beep_tone_offset_ms"] > 20


def test_a_wav_too_short_to_filter_never_fails_the_confirm(tmp_path) -> None:
    """The offset is derived and optional: a WAV the band-pass cannot take
    (shorter than its padding) leaves it unset, the confirm still succeeds."""
    import numpy as np
    import soundfile as sf

    from splitsmith.match_project import MatchProject
    from splitsmith.ui import audio as audio_helpers

    from .ui._helpers import _seed_match_export_project

    client, root = _seed_match_export_project(tmp_path, stage_count=1)
    shooter = root / "shooters" / "me"
    project = MatchProject.load(shooter)
    video = project.stage(1).primary()
    wav = audio_helpers.video_audio_path(shooter, 1, video, project=project)
    wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(wav), np.zeros(20, dtype=np.float32), 48000)
    r = client.post(f"/api/shooters/me/stages/1/videos/{video.video_id}/beep", json={"beep_time": 0.0001})
    assert r.status_code == 200, r.text


def test_the_audio_job_never_saves_over_an_edit_made_while_it_ran(tmp_path, monkeypatch) -> None:
    """The video derive job measures the tone once its WAV exists. It must
    write those two fields onto the project as it is then, not save the copy
    it loaded before ffmpeg ran: a beep confirmed meanwhile would be lost."""
    import numpy as np
    import soundfile as sf

    from splitsmith.match_project import MatchProject
    from splitsmith.ui import audio as audio_helpers

    from .ui._helpers import _seed_match_export_project, _wait_for_job

    client, root = _seed_match_export_project(tmp_path, stage_count=1)
    shooter = root / "shooters" / "me"
    project = MatchProject.load(shooter)
    video = project.stage(1).primary()
    project.stage(1).primary().beep_reviewed = True
    project.save(shooter)

    def fake_extract(project_root, stage_number, vid, source, **_kw):  # noqa: ANN001, ANN202
        wav = audio_helpers.video_audio_path(
            project_root, stage_number, vid, project=MatchProject.load(project_root)
        )
        wav.parent.mkdir(parents=True, exist_ok=True)
        t = np.arange(3 * 48000) / 48000
        sf.write(
            str(wav), np.where(t >= 1.0, 0.5 * np.sin(2 * np.pi * 2800 * t), 0.0).astype(np.float32), 48000
        )
        # An edit made on disk while the job runs.
        fresh = MatchProject.load(project_root)
        fresh.stage(1).time_seconds = 99.5
        fresh.save(project_root)
        return wav

    monkeypatch.setattr(audio_helpers, "ensure_video_audio", fake_extract)
    r = client.get(f"/api/shooters/me/stages/1/videos/{video.video_id}/peaks?bins=100")
    assert r.status_code == 202, r.text
    assert _wait_for_job(client, r.json()["job_id"])["status"] == "succeeded"
    after = MatchProject.load(shooter)
    assert after.stage(1).time_seconds == 99.5
    assert after.stage(1).primary().beep_tone_for == after.stage(1).primary().beep_time
