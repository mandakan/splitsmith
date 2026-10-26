"""A detection run stamps the camera of the shots it seeds (#1496)."""

from __future__ import annotations

from types import SimpleNamespace

from splitsmith.detection_merge import merge_detection_into

_FIELDS = (
    "peak_amplitude confidence vote_a vote_b vote_c vote_e vote_total apriori_boost ensemble_score "
    "score_c clap_diff voter_e_signal"
).split()


def _run(times: list[float]) -> SimpleNamespace:
    cands = [
        SimpleNamespace(
            candidate_number=i,
            time=t,
            ms_after_beep=round((t - 5.0) * 1000),
            kept=True,
            **dict.fromkeys(_FIELDS, 0),
        )
        for i, t in enumerate(times, start=1)
    ]
    return SimpleNamespace(candidates=cands, consensus=2, expected_rounds=None)


BASE = {"stage_number": 1, "beep_time": 5.0, "clip_video_id": "head", "shots": []}


def test_seeded_shots_are_stamped_with_the_camera_detection_read() -> None:
    doc = merge_detection_into(
        {"clip_video_id": "phone"}, _run([6.0]), base=BASE, stage_rounds=None, reset=True
    )
    assert doc["clip_video_id"] == "head"


def test_a_run_that_keeps_the_audits_shots_keeps_its_stamp() -> None:
    doc = {"clip_video_id": "phone", "shots": [{"shot_number": 1, "time": 6.0}]}
    out = merge_detection_into(doc, _run([6.0]), base=BASE, stage_rounds=None, reset=False)
    assert out["clip_video_id"] == "phone"


def test_seeding_takes_the_runs_beep_over_a_candidates_only_docs() -> None:
    """A candidates-only doc from another clip kept its beep through the
    base's setdefault while the shots were seeded on this run's clip."""
    doc = merge_detection_into(
        {"beep_time": 20.0, "_candidates_pending_audit": {"candidates": []}},
        _run([6.0]),
        base=BASE,
        stage_rounds=None,
        reset=False,
    )
    assert doc["beep_time"] == 5.0
    assert doc["shots"][0]["ms_after_beep"] == 1000
