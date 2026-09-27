"""Reconciler (spec 2026-09-27 s3): derive missing pipeline steps from state."""

from __future__ import annotations

from splitsmith.match_project import STUB_AUDIT_DETECTION, MatchProject, StageEntry, StageVideo
from splitsmith.sync.reconcile import ReconcileStep, plan_reconcile, video_step


def _video(**kw) -> StageVideo:
    base: dict = {"path": "raw/a.mp4", "role": "primary", "beep_time": 12.34, "beep_reviewed": True}
    base.update(kw)
    return StageVideo(**base)


def _stage(video: StageVideo, time_seconds: float = 20.0) -> StageEntry:
    return StageEntry(stage_number=1, stage_name="S1", time_seconds=time_seconds, videos=[video])


def test_confirm_only_on_an_untrimmed_stage_wants_a_trim() -> None:
    """The phone's most common action: confirm the detected beep as-is.
    The merge flags nothing for it; the reconciler must still act."""
    v = _video(processed={"beep": True, "trim": False})
    assert video_step(_stage(v), v, None) == "trim"


def test_unreviewed_or_timeless_stage_wants_nothing() -> None:
    v = _video(beep_reviewed=False, processed={"trim": False})
    assert video_step(_stage(v), v, None) is None
    v = _video(processed={"trim": False})
    assert video_step(_stage(v, time_seconds=0.0), v, None) is None


def test_trimmed_primary_without_detection_wants_detect() -> None:
    v = _video(processed={"trim": True, "shot_detect": False})
    assert video_step(_stage(v), v, None) == "shot_detect"
    stub = {"shots": [], "detection": STUB_AUDIT_DETECTION}
    assert video_step(_stage(v), v, stub) == "shot_detect"


def test_never_detects_over_real_audit_content() -> None:
    v = _video(processed={"trim": True, "shot_detect": False})
    worked = {"shots": [{"time": 1.0}], "audit_events": [{"kind": "save"}]}
    assert video_step(_stage(v), v, worked) is None


def test_explicit_confirm_always_detects_a_trimmed_primary() -> None:
    """_after_beep_reviewed semantics: a local confirm re-runs detection."""
    v = _video(processed={"trim": True, "shot_detect": True})
    assert video_step(_stage(v), v, {"shots": [{"time": 1.0}]}, explicit=True) == "shot_detect"


def test_detect_honours_the_automation_gate_and_secondaries_never_detect() -> None:
    v = _video(processed={"trim": True, "shot_detect": False})
    assert video_step(_stage(v), v, None, detect_enabled=False) is None
    s = _video(role="secondary", processed={"trim": True})
    assert video_step(_stage(s), s, None) is None


def test_plan_skips_a_step_that_failed_with_the_same_inputs() -> None:
    v = _video(processed={"trim": False})
    project = MatchProject(name="Me", stages=[_stage(v)])
    video_id = project.stages[0].videos[0].video_id
    steps = plan_reconcile({"me": project}, {"me": {}}, {})
    assert steps == [
        ReconcileStep(kind="trim", slug="me", stage_number=1, video_id=video_id, input_key="12.3400")
    ]
    assert plan_reconcile({"me": project}, {"me": {}}, {steps[0].key: "12.3400"}) == []
    # A moved beep is a new input: try again.
    assert len(plan_reconcile({"me": project}, {"me": {}}, {steps[0].key: "11.0000"})) == 1
