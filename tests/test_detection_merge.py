"""A detection run records which model set produced it (thin detection spec 2026-10-26)."""

from __future__ import annotations

from types import SimpleNamespace

from splitsmith.detection_merge import merge_detection_into

_FIELDS = (
    "peak_amplitude confidence vote_a vote_b vote_c vote_e vote_total apriori_boost ensemble_score "
    "score_c clap_diff voter_e_signal"
).split()

BASE = {"stage_number": 1, "beep_time": 5.0, "clip_video_id": "head", "shots": []}


def _result(*, model_set: str, calibration_built_at: str) -> SimpleNamespace:
    cand = SimpleNamespace(
        candidate_number=1, time=6.0, ms_after_beep=1000, kept=True, **dict.fromkeys(_FIELDS, 0)
    )
    return SimpleNamespace(
        candidates=[cand],
        consensus=2,
        expected_rounds=None,
        model_set=model_set,
        calibration_built_at=calibration_built_at,
    )


def test_the_merge_records_the_model_set_on_the_block_and_the_run_event() -> None:
    doc = merge_detection_into(
        {},
        _result(model_set="base", calibration_built_at="2026-10-26T00:00:00+00:00"),
        base=BASE,
        stage_rounds=None,
        reset=False,
    )
    block = doc["_candidates_pending_audit"]
    assert block["_model_set"] == "base"
    assert block["_calibration_built_at"] == "2026-10-26T00:00:00+00:00"
    run = [e for e in doc["audit_events"] if e["kind"] == "shot_detect_run"][-1]
    assert run["payload"]["model_set"] == "base"
    assert run["payload"]["calibration_built_at"] == "2026-10-26T00:00:00+00:00"


def test_a_redetection_with_another_set_replaces_the_stamp() -> None:
    doc = merge_detection_into(
        {}, _result(model_set="clap", calibration_built_at="a"), base=BASE, stage_rounds=None, reset=False
    )
    doc = merge_detection_into(
        doc, _result(model_set="base", calibration_built_at="b"), base=BASE, stage_rounds=None, reset=False
    )
    assert doc["_candidates_pending_audit"]["_model_set"] == "base"
    runs = [e["payload"]["model_set"] for e in doc["audit_events"] if e["kind"] == "shot_detect_run"]
    assert runs == ["clap", "base"]
