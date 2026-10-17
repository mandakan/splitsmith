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
