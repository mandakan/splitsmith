import json
from pathlib import Path

from splitsmith.config import VideoMetadata
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.reel_inputs import load_reel_inputs

META = VideoMetadata(width=1920, height=1080, duration_seconds=30.0, frame_rate_num=30, frame_rate_den=1)


def _video(vid: str, role: str, beep: float | None = 12.0) -> StageVideo:
    return StageVideo(video_id=vid, path=f"raw/{vid}.mp4", role=role, beep_time=beep, beep_reviewed=True)


def _audit(events: list[dict]) -> dict:
    shots = [
        {"shot_number": i + 1, "time_absolute": 12.0 + t, "time_from_beep": t, "ms_after_beep": t * 1000}
        for i, t in enumerate([1.0, 1.25, 1.5])
    ]
    return {"shots": shots, "events": events, "events_seeded": 4}


def _project(tmp_path: Path) -> tuple[MatchProject, Path]:
    root = tmp_path / "shooter"
    project = MatchProject(
        name="Demo",
        stages=[
            StageEntry(
                stage_number=1,
                stage_name="One",
                time_seconds=8.0,
                videos=[_video("a", "primary"), _video("b", "secondary", beep=3.0)],
            ),
            StageEntry(stage_number=2, stage_name="Two", time_seconds=9.0, videos=[_video("c", "primary")]),
        ],
    )
    trimmed = project.trimmed_path(root)
    trimmed.mkdir(parents=True)
    a, b = (v.video_id for v in project.stage(1).videos)
    c = project.stage(2).videos[0].video_id
    for name in (f"stage1_cam_{a}_trimmed.mp4", f"stage1_cam_{b}_web.mp4", f"stage2_cam_{c}_trimmed.mp4"):
        (trimmed / name).write_bytes(b"x")
    audit = project.audit_path(root)
    audit.mkdir(parents=True)
    manual = {"id": "evt-1", "kind": "movement", "start": 1.3, "end": 1.4, "source": "manual"}
    auto = dict(manual, id="evt-2", source="auto")
    (audit / "stage1.json").write_text(json.dumps(_audit([manual])))
    (audit / "stage2.json").write_text(json.dumps(_audit([auto])))
    return project, root


def test_loads_ready_stages_with_footage_and_reports_the_rest(tmp_path, monkeypatch):
    project, root = _project(tmp_path)
    monkeypatch.setattr(MatchProject, "stage_statuses", lambda self, root, **kw: {1: "audited", 2: "audited"})
    inputs = load_reel_inputs(project, root, label="Anna", probe=lambda p: META)
    assert [(r.stage_number, r.state, r.proposals) for r in inputs.readiness] == [
        (1, "ready", 0),
        (2, "proposals_left", 1),
    ]
    assert list(inputs.footage) == [1]
    stage = inputs.footage[1]
    assert stage.primary.path.name.endswith("_trimmed.mp4")
    assert stage.primary.beep_offset == 5.0  # the project's pre-buffer, the beep sat later in the source
    assert stage.secondary is not None and stage.secondary.path.name.endswith("_web.mp4")
    assert stage.secondary.beep_offset == 3.0  # the beep sat inside the pre-buffer
    assert [len(t.shots) for t in inputs.stages] == [3]
    assert [e.id for e in inputs.stages[0].events] == ["evt-1"]
    assert inputs.primaries == (META,)


def _render_spy(calls: list):
    from splitsmith.reel_render import ReelRenderResult

    def render(items, *, sequence, output_path, work_dir, **kw):
        calls.append((items, sequence))
        return ReelRenderResult(output_path, 12.0, ())

    return render


def test_a_locked_match_names_its_blockers_and_renders_nothing(tmp_path, monkeypatch):
    from splitsmith import reel_inputs

    project, root = _project(tmp_path)
    monkeypatch.setattr(MatchProject, "stage_statuses", lambda self, root, **kw: {1: "audited", 2: "audited"})
    calls: list = []
    monkeypatch.setattr(reel_inputs, "render_reel", _render_spy(calls))
    outcome = reel_inputs.make_reel(
        project, root, label="Anna", output=tmp_path / "r.mp4", upright=True, probe=lambda p: META
    )
    assert outcome.result is None and calls == []
    assert [(r.stage_number, r.state) for r in outcome.blockers] == [(2, "proposals_left")]


def test_force_renders_the_ready_stages_upright_or_landscape(tmp_path, monkeypatch):
    from splitsmith import reel_inputs

    project, root = _project(tmp_path)
    monkeypatch.setattr(MatchProject, "stage_statuses", lambda self, root, **kw: {1: "audited", 2: "audited"})
    calls: list = []
    monkeypatch.setattr(reel_inputs, "render_reel", _render_spy(calls))
    for upright in (True, False):
        outcome = reel_inputs.make_reel(
            project,
            root,
            label="Anna",
            output=tmp_path / "r.mp4",
            upright=upright,
            force=True,
            probe=lambda p: META,
        )
        assert outcome.result is not None
    (items_up, seq_up), (_, seq_wide) = calls
    assert seq_up.height > seq_up.width and seq_wide.width > seq_wide.height
    assert items_up[-1].kind == "end" and items_up[-1].lines


def test_a_stage_whose_trim_is_missing_has_no_footage(tmp_path, monkeypatch):
    project, root = _project(tmp_path)
    a = project.stage(1).primary().video_id
    (project.trimmed_path(root) / f"stage1_cam_{a}_trimmed.mp4").unlink()
    monkeypatch.setattr(MatchProject, "stage_statuses", lambda self, root, **kw: {1: "audited", 2: "audited"})
    inputs = load_reel_inputs(project, root, label="Anna", probe=lambda p: META)
    assert inputs.readiness[0].state == "no_footage"
    assert inputs.footage == {}
