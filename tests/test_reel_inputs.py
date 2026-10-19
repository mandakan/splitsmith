import json
from pathlib import Path

from splitsmith.config import VideoMetadata
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.reel.inputs import load_reel_inputs

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
    monkeypatch.setattr(MatchProject, "stage_statuses", lambda self, **kw: {1: "audited", 2: "audited"})
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
    assert inputs.cameras == (META,)


def _render_spy(calls: list):
    from splitsmith.reel.render import ReelRenderResult

    def render(items, *, sequence, output_path, work_dir, **kw):
        calls.append((items, sequence))
        return ReelRenderResult(output_path, 12.0, ())

    return render


def test_a_locked_match_names_its_blockers_and_renders_nothing(tmp_path, monkeypatch):
    from splitsmith.reel import inputs as reel_inputs

    project, root = _project(tmp_path)
    monkeypatch.setattr(MatchProject, "stage_statuses", lambda self, **kw: {1: "audited", 2: "audited"})
    calls: list = []
    monkeypatch.setattr(reel_inputs, "render_reel", _render_spy(calls))
    outcome = reel_inputs.make_reel(
        project, root, label="Anna", output=tmp_path / "r.mp4", upright=True, probe=lambda p: META
    )
    assert outcome.result is None and calls == []
    assert [(r.stage_number, r.state) for r in outcome.blockers] == [(2, "proposals_left")]


def test_force_renders_the_ready_stages_upright_or_landscape(tmp_path, monkeypatch):
    from splitsmith.reel import inputs as reel_inputs

    project, root = _project(tmp_path)
    monkeypatch.setattr(MatchProject, "stage_statuses", lambda self, **kw: {1: "audited", 2: "audited"})
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
        # A forced reel still says which stages it left out.
        assert [(r.stage_number, r.state) for r in outcome.blockers] == [(2, "proposals_left")]
    (items_up, seq_up), (_, seq_wide) = calls
    assert seq_up.height > seq_up.width and seq_wide.width > seq_wide.height
    assert items_up[-1].kind == "end" and items_up[-1].lines


def test_the_chosen_moments_and_the_shorts_cut_reach_the_plan(tmp_path, monkeypatch):
    from splitsmith.reel import inputs as reel_inputs

    project, root = _project(tmp_path)
    monkeypatch.setattr(MatchProject, "stage_statuses", lambda self, **kw: {1: "audited", 2: "audited"})
    seen: dict = {}

    def plan(picks, footage, **kw):
        seen["picks"], seen["short"] = picks, kw.get("short")
        return ()

    monkeypatch.setattr(reel_inputs, "plan_reel", plan)
    monkeypatch.setattr(reel_inputs, "render_reel", _render_spy([]))
    reel_inputs.make_reel(
        project,
        root,
        label="Anna",
        output=tmp_path / "r.mp4",
        force=True,
        moments=("fastest_draw", "most_shots"),
        short=True,
        probe=lambda p: META,
    )
    kinds = {m.kind for m in seen["picks"].beats} | {seen["picks"].cold_open.kind}
    assert kinds <= {"fastest_draw", "most_shots"}
    assert seen["short"] is True
    assert len(seen["picks"].montage) <= 4


def test_a_done_stage_whose_trim_is_missing_blocks(tmp_path, monkeypatch):
    project, root = _project(tmp_path)
    a = project.stage(1).primary().video_id
    (project.trimmed_path(root) / f"stage1_cam_{a}_trimmed.mp4").unlink()
    monkeypatch.setattr(MatchProject, "stage_statuses", lambda self, **kw: {1: "audited", 2: "audited"})
    inputs = load_reel_inputs(project, root, label="Anna", probe=lambda p: META)
    assert inputs.readiness[0].state == "trim_missing"
    assert inputs.footage == {}


def test_an_untouched_stage_blocks_the_reel(tmp_path, monkeypatch):
    from splitsmith.reel import inputs as reel_inputs

    project, root = _project(tmp_path)
    project.stage(2).primary().beep_time = None
    (project.audit_path(root) / "stage2.json").unlink()
    monkeypatch.setattr(MatchProject, "stage_statuses", lambda self, **kw: {1: "audited", 2: "ready"})
    calls: list = []
    monkeypatch.setattr(reel_inputs, "render_reel", _render_spy(calls))
    outcome = reel_inputs.make_reel(
        project, root, label="Anna", output=tmp_path / "r.mp4", probe=lambda p: META
    )
    assert outcome.result is None and calls == []
    assert [(r.stage_number, r.state) for r in outcome.blockers] == [(2, "not_audited")]


def test_a_trim_cut_for_another_beep_is_not_used(tmp_path, monkeypatch):
    project, root = _project(tmp_path)
    a = project.stage(1).primary().video_id
    sidecar = project.trimmed_path(root) / f"stage1_cam_{a}_trimmed.params.json"
    sidecar.write_text(json.dumps({"beep_time": 11.5, "pre_buffer_seconds": 5.0}))
    monkeypatch.setattr(MatchProject, "stage_statuses", lambda self, **kw: {1: "audited", 2: "audited"})
    inputs = load_reel_inputs(project, root, label="Anna", probe=lambda p: META)
    assert inputs.readiness[0].state == "trim_missing"


def test_an_unreviewed_secondary_beep_gives_no_replay(tmp_path, monkeypatch):
    project, root = _project(tmp_path)
    project.stage(1).videos[1].beep_reviewed = False
    monkeypatch.setattr(MatchProject, "stage_statuses", lambda self, **kw: {1: "audited", 2: "audited"})
    inputs = load_reel_inputs(project, root, label="Anna", probe=lambda p: META)
    assert inputs.footage[1].secondary is None


def _statuses(monkeypatch) -> None:
    monkeypatch.setattr(MatchProject, "stage_statuses", lambda self, **kw: {1: "audited", 2: "audited"})


def test_a_chosen_camera_is_the_picture_and_the_primary_the_replay(tmp_path, monkeypatch):
    project, root = _project(tmp_path)
    project.stage(1).videos[1].camera_mount = "head"
    _statuses(monkeypatch)
    for camera in ("head", "secondary"):
        inputs = load_reel_inputs(project, root, label="Anna", probe=lambda p: META, camera=camera)
        stage = inputs.footage[1]
        assert stage.primary.path.name.endswith("_web.mp4"), camera
        assert stage.primary.beep_offset == 3.0  # the head camera's own beep
        assert stage.secondary is not None and stage.secondary.path.name.endswith("_trimmed.mp4")
        assert inputs.camera_fallbacks == ()
    # The audit stays the primary's: the shots are the same either way.
    assert [len(t.shots) for t in inputs.stages] == [3]


def test_no_camera_or_the_primary_reads_as_before(tmp_path, monkeypatch):
    project, root = _project(tmp_path)
    project.stage(1).videos[1].camera_mount = "head"
    _statuses(monkeypatch)
    for camera in (None, "primary"):
        stage = load_reel_inputs(project, root, label="Anna", probe=lambda p: META, camera=camera).footage[1]
        assert stage.primary.path.name.endswith("_trimmed.mp4")
        assert stage.secondary is not None and stage.secondary.path.name.endswith("_web.mp4")


def test_a_stage_the_camera_cannot_serve_shows_the_primary_and_says_so(tmp_path, monkeypatch):
    _statuses(monkeypatch)
    # Unconfirmed beep: the moment could land on the wrong shot.
    project, root = _project(tmp_path)
    project.stage(1).videos[1].camera_mount = "head"
    project.stage(1).videos[1].beep_reviewed = False
    inputs = load_reel_inputs(project, root, label="Anna", probe=lambda p: META, camera="head")
    assert inputs.footage[1].primary.path.name.endswith("_trimmed.mp4")
    assert inputs.camera_fallbacks == (1,)
    # Not on the stage at all.
    project, root = _project(tmp_path / "b")
    inputs = load_reel_inputs(project, root, label="Anna", probe=lambda p: META, camera="chest")
    assert inputs.footage[1].primary.path.name.endswith("_trimmed.mp4")
    assert inputs.camera_fallbacks == (1,)
    # Two cameras with the one mount: ambiguous, never a guess.
    project, root = _project(tmp_path / "c")
    for video in project.stage(1).videos:
        video.camera_mount = "head"
    inputs = load_reel_inputs(project, root, label="Anna", probe=lambda p: META, camera="head")
    assert inputs.footage[1].primary.path.name.endswith("_trimmed.mp4")
    assert inputs.camera_fallbacks == (1,)
    # Its trim is gone.
    project, root = _project(tmp_path / "d")
    project.stage(1).videos[1].camera_mount = "head"
    b = project.stage(1).videos[1].video_id
    (project.trimmed_path(root) / f"stage1_cam_{b}_web.mp4").unlink()
    inputs = load_reel_inputs(project, root, label="Anna", probe=lambda p: META, camera="head")
    assert inputs.footage[1].primary.path.name.endswith("_trimmed.mp4")
    assert inputs.camera_fallbacks == (1,)


def test_make_reel_takes_the_camera_and_reports_its_fallbacks(tmp_path, monkeypatch):
    from splitsmith.reel import inputs as reel_inputs

    project, root = _project(tmp_path)
    project.stage(1).videos[1].camera_mount = "head"
    _statuses(monkeypatch)
    seen: list = []
    monkeypatch.setattr(reel_inputs, "plan_reel", lambda picks, footage, **kw: seen.append(footage) or ())
    monkeypatch.setattr(reel_inputs, "render_reel", _render_spy([]))
    kw = {"label": "Anna", "output": tmp_path / "r.mp4", "force": True, "probe": lambda p: META}
    outcome = reel_inputs.make_reel(project, root, camera="head", **kw)
    assert seen[0][1].primary.path.name.endswith("_web.mp4") and outcome.camera_fallbacks == ()
    outcome = reel_inputs.make_reel(project, root, camera="chest", **kw)
    assert outcome.camera_fallbacks == (1,)
