"""Reconciler (spec 2026-09-27 s3): derive missing pipeline steps from state."""

from __future__ import annotations

from pathlib import Path

from splitsmith.match_project import STUB_AUDIT_DETECTION, MatchProject, StageEntry, StageVideo
from splitsmith.sync.reconcile import ReconcileStep, plan_reconcile, step_input_key, video_step


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
    key = "beep=12.3400 stage=20.0000 pre=5 post=5 src=raw/a.mp4"
    assert steps == [ReconcileStep(kind="trim", slug="me", stage_number=1, video_id=video_id, input_key=key)]
    assert plan_reconcile({"me": project}, {"me": {}}, {steps[0].key: key}) == []
    # A key recorded before #1069 (beep only) is a new input: try again once.
    assert len(plan_reconcile({"me": project}, {"me": {}}, {steps[0].key: "12.3400"})) == 1


def _failed_trim_memo(project: MatchProject, **kw) -> dict[str, str]:
    (step,) = plan_reconcile({"me": project}, {"me": {}}, {}, **kw)
    return {step.key: step.input_key}


def test_a_failed_trim_is_retried_when_any_trim_input_changes() -> None:
    """#1069: the memo used to key on the beep alone, so only a moved beep
    retried a failed trim."""

    def fresh() -> MatchProject:
        return MatchProject(name="Me", stages=[_stage(_video(processed={"trim": False}))])

    project = fresh()
    memo = _failed_trim_memo(project)
    assert plan_reconcile({"me": project}, {"me": {}}, memo) == []

    project.stages[0].time_seconds = 21.5  # stage time fixed on the phone
    assert len(plan_reconcile({"me": project}, {"me": {}}, memo)) == 1

    project = fresh()
    memo = _failed_trim_memo(project)
    project.trim_post_buffer_seconds = 2.0
    assert len(plan_reconcile({"me": project}, {"me": {}}, memo)) == 1

    project = fresh()
    memo = _failed_trim_memo(project)
    project.stages[0].videos[0].path = Path("/Volumes/X9/relinked.mp4")
    assert len(plan_reconcile({"me": project}, {"me": {}}, memo)) == 1


def test_a_trim_that_failed_on_a_missing_source_is_retried_once_it_is_back() -> None:
    project = MatchProject(name="Me", stages=[_stage(_video(processed={"trim": False}))])
    video_id = project.stages[0].videos[0].video_id
    memo = _failed_trim_memo(project, present_sources=set())
    # Still unmounted: stay quiet.
    assert plan_reconcile({"me": project}, {"me": {}}, memo, present_sources=set()) == []
    # Remounted: retry.
    back = plan_reconcile({"me": project}, {"me": {}}, memo, present_sources={("me", video_id)})
    assert [s.kind for s in back] == ["trim"]


def test_detect_key_ignores_trim_only_inputs() -> None:
    v = _video(processed={"trim": True, "shot_detect": False})
    project = MatchProject(name="Me", stages=[_stage(v)])
    key = step_input_key("shot_detect", project, project.stages[0], project.stages[0].videos[0])
    assert key == "beep=12.3400 stage=20.0000"
