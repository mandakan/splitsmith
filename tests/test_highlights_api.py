"""The highlight reel over the API (part 2 plan, 2026-10-18)."""

from __future__ import annotations

import json
from pathlib import Path

from splitsmith.config import DEFAULT_MOMENTS
from splitsmith.highlights import MOMENT_GROUPS, MOMENT_KINDS, readiness_for_project
from splitsmith.match_project import MatchProject, StageStatus

from .ui._helpers import _seed_match_export_project

SHOOTER = Path("shooters") / "me"


def _broken_down(root: Path, n: int, *, sources: tuple[str, ...] = ("manual",)) -> None:
    path = root / SHOOTER / "audit" / f"stage{n}.json"
    doc = json.loads(path.read_text())
    doc["audit_events"] = [{"kind": "save"}]  # what makes a stage audited
    doc["events_seeded"] = 4
    doc["events"] = [
        {"id": f"evt-{i}", "kind": "movement", "start": 0.6 + i, "end": 0.9 + i, "source": s}
        for i, s in enumerate(sources)
    ]
    path.write_text(json.dumps(doc))


def _trimmed(root: Path) -> None:
    project = MatchProject.load(root / SHOOTER)
    for stage in project.stages:
        stage.primary().processed["trim"] = True
    project.save(root / SHOOTER)


def test_readiness_names_each_stage_and_counts_proposals(tmp_path):
    client, root = _seed_match_export_project(tmp_path, stage_count=2)
    _trimmed(root)
    _broken_down(root, 1)
    _broken_down(root, 2, sources=("auto", "manual", "auto"))
    body = client.get("/api/shooters/me/highlights").json()
    assert body["unlocked"] is False
    assert (body["ready"], body["total"]) == (1, 2)
    assert [(s["stage_number"], s["state"], s["proposals"]) for s in body["stages"]] == [
        (1, "ready", 0),
        (2, "proposals_left", 2),
    ]


def test_an_untrimmed_stage_reads_as_not_ready_from_the_request(tmp_path):
    client, root = _seed_match_export_project(tmp_path, stage_count=1)
    _broken_down(root, 1)
    body = client.get("/api/shooters/me/highlights").json()
    assert [s["state"] for s in body["stages"]] == ["trim_missing"]


def test_the_menu_lists_every_moment_once_with_its_group_and_default(tmp_path):
    client, _root = _seed_match_export_project(tmp_path, stage_count=1)
    menu = client.get("/api/shooters/me/highlights").json()["menu"]
    assert [m["kind"] for m in menu] == list(menu_order())
    assert {m["kind"] for m in menu} == set(MOMENT_KINDS)
    assert {m["kind"] for m in menu if m["default"]} == set(DEFAULT_MOMENTS)
    assert all(m["group"] == MOMENT_GROUPS[m["kind"]] for m in menu)
    assert not any(m["default"] for m in menu if m["group"] == "lolz")
    assert next(m for m in menu if m["kind"] == "thinker")["label"] == "THE THINKER"


def menu_order() -> tuple[str, ...]:
    from splitsmith.highlights import BEAT_ORDER

    return BEAT_ORDER


def _ready_match(tmp_path, stage_count=2):
    client, root = _seed_match_export_project(tmp_path, stage_count=stage_count)
    _trimmed(root)
    for n in range(1, stage_count + 1):
        _broken_down(root, n)
    return client, root


def _fake_make_reel(calls: list, *, gate=None):
    from splitsmith.reel.inputs import ReelOutcome
    from splitsmith.reel.render import ReelRenderResult

    def make(project, root, *, output, **kw):
        if gate is not None:
            gate.wait(5)
        calls.append({"output": output, **kw})
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"mp4")
        return ReelOutcome((), ReelRenderResult(output, 31.5, ("a note",)), stages=(1, 2))

    return make


def test_submit_queues_a_highlight_reel_job_once(tmp_path, monkeypatch):
    import threading

    client, _root = _ready_match(tmp_path)
    gate = threading.Event()
    calls: list = []
    monkeypatch.setattr("splitsmith.ui.highlights_api.make_reel", _fake_make_reel(calls, gate=gate))
    body = {"moments": ["fastest_draw", "thinker"], "short": True, "upright": False}
    first = client.post("/api/shooters/me/highlights", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["kind"] == "highlight_reel"
    again = client.post("/api/shooters/me/highlights", json=body)
    assert again.json()["id"] == first.json()["id"]  # the running one, not a second render
    gate.set()


def test_submit_refuses_an_empty_menu_and_a_locked_match(tmp_path, monkeypatch):
    client, root = _ready_match(tmp_path)
    assert client.post("/api/shooters/me/highlights", json={"moments": []}).status_code == 422
    _broken_down(root, 2, sources=("auto",))
    locked = client.post("/api/shooters/me/highlights", json={"moments": ["fastest_draw"]})
    assert locked.status_code == 409
    assert locked.json()["detail"]["code"] == "locked"
    assert [s["stage_number"] for s in locked.json()["detail"]["stages"]] == [2]
    monkeypatch.setattr("splitsmith.ui.highlights_api.make_reel", _fake_make_reel([]))
    forced = client.post("/api/shooters/me/highlights", json={"moments": ["fastest_draw"], "force": True})
    assert forced.status_code == 200


def _run_job(client, body: dict) -> dict:
    from .ui._helpers import _wait_for_job

    submitted = client.post("/api/shooters/me/highlights", json=body)
    assert submitted.status_code == 200, submitted.text
    job = _wait_for_job(client, submitted.json()["id"])
    assert job["status"] == "succeeded", job
    return job


def test_the_job_renders_records_and_writes_a_sidecar(tmp_path, monkeypatch):
    from splitsmith import youtube_sidecar

    client, root = _ready_match(tmp_path)
    calls: list = []
    monkeypatch.setattr("splitsmith.ui.highlights_api.make_reel", _fake_make_reel(calls))
    job = _run_job(client, {"moments": ["fastest_draw", "thinker"], "short": True, "upright": False})
    result = job["result"]

    (call,) = calls
    assert (call["moments"], call["short"], call["upright"], call["force"]) == (
        ["fastest_draw", "thinker"],
        True,
        False,
        False,
    )
    output = call["output"]
    assert output.name.endswith("-highlights-short.mp4")
    assert result["filename"] == output.name
    assert result["duration_seconds"] == 31.5 and result["degradations"] == ["a note"]

    # A landscape Shorts cut is not a YouTube Short: no #Shorts in the title.
    sidecar = youtube_sidecar.load_sidecar(youtube_sidecar.sidecar_path_for(output))
    assert sidecar.title.endswith(" highlights")
    assert "Made with splitsmith" in sidecar.description
    upright = _run_job(client, {"moments": ["fastest_draw"], "short": True})
    assert youtube_sidecar.load_sidecar(youtube_sidecar.sidecar_path_for(calls[-1]["output"])).title.endswith(
        "highlights #Shorts"
    )
    assert upright["result"]["filename"] == result["filename"]


def test_the_reel_follows_the_saved_camera_unless_one_is_chosen(tmp_path, monkeypatch):
    from splitsmith.reel.inputs import ReelOutcome
    from splitsmith.reel.render import ReelRenderResult

    client, root = _ready_match(tmp_path)
    calls: list = []

    def make(project, root, *, output, **kw):
        calls.append(kw.get("camera"))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"mp4")
        return ReelOutcome((), ReelRenderResult(output, 31.5, ()), stages=(1, 2), camera_fallbacks=(2,))

    monkeypatch.setattr("splitsmith.ui.highlights_api.make_reel", make)
    first = _run_job(client, {"moments": ["fastest_draw"]})
    assert calls == [None]  # nothing saved: the primary
    assert first["result"]["camera_fallbacks"] == [2]
    project = MatchProject.load(root / SHOOTER)
    project.compare_camera = "head"
    project.save(root / SHOOTER)
    _run_job(client, {"moments": ["fastest_draw"]})
    _run_job(client, {"moments": ["fastest_draw"], "main_camera": "default"})
    _run_job(client, {"moments": ["fastest_draw"], "main_camera": "primary"})
    assert calls[1:] == ["head", "head", "primary"]


def _runs_from_route(client):
    return client.get("/api/shooters/me/exports/runs").json()["runs"]


def test_the_job_pulls_each_stages_clips_before_rendering(tmp_path, monkeypatch):
    from splitsmith.match_project import MatchProject

    client, root = _ready_match(tmp_path, stage_count=1)
    project = MatchProject.load(root / SHOOTER)
    primary = project.stage(1).primary()
    order: list[str] = []

    def pull(project_root, n, video, *, project):
        order.append(f"pull {n} {video.video_id}")
        return root / "missing.mp4"

    def web(project, trim_path):
        order.append("web")
        return None

    def make(project, root, **kw):
        order.append("render")
        return _fake_make_reel([])(project, root, **kw)

    monkeypatch.setattr("splitsmith.ui.highlights_api.audio.pull_trimmed_video", pull)
    monkeypatch.setattr("splitsmith.ui.highlights_api.audio.try_pull_web_trim", web)
    monkeypatch.setattr("splitsmith.ui.highlights_api.make_reel", make)
    _run_job(client, {"moments": ["fastest_draw"]})
    assert order == [f"pull 1 {primary.video_id}", "web", "render"]


def test_the_reel_uploads_to_youtube_when_asked(tmp_path, monkeypatch):
    client, _root = _ready_match(tmp_path)
    monkeypatch.setattr("splitsmith.ui.highlights_api.make_reel", _fake_make_reel([]))
    jobs = client.app.state.splitsmith_state.jobs
    submitted: list[dict] = []
    real_submit = jobs.submit

    async def spy(*, kind, shooter_slug=None, args=None, **kw):
        if kind == "youtube_upload":
            submitted.append(args)
            return None
        return await real_submit(kind=kind, shooter_slug=shooter_slug, args=args, **kw)

    monkeypatch.setattr(jobs, "submit", spy)
    _run_job(client, {"moments": ["fastest_draw"]})
    assert submitted == []  # not asked: no upload
    _run_job(client, {"moments": ["fastest_draw"], "youtube_upload": True, "youtube_privacy": "private"})
    (args,) = submitted
    assert args["filename"].endswith("-highlights.mp4")
    assert (args["privacy"], args["again"]) == ("private", True)


def test_a_stage_the_job_finds_unusable_fails_with_the_way_out(tmp_path, monkeypatch):
    """The route counts a trimmed stage as ready; the job checks the file. A
    stale trim found there must say which stage and how to leave it out."""
    from splitsmith.highlights import StageReadiness
    from splitsmith.reel.inputs import ReelOutcome

    from .ui._helpers import _wait_for_job

    client, _root = _ready_match(tmp_path)
    monkeypatch.setattr(
        "splitsmith.ui.highlights_api.make_reel",
        lambda *a, **kw: ReelOutcome((StageReadiness(2, "trim_missing"),), None),
    )
    submitted = client.post("/api/shooters/me/highlights", json={"moments": ["fastest_draw"]})
    job = _wait_for_job(client, submitted.json()["id"])
    assert job["status"] == "failed"
    assert "stage 2" in job["error"] and "Render the ready ones" in job["error"]


def test_two_submits_at_once_queue_one_reel(monkeypatch):
    """Two clicks on one server loop: the second must find the first's job,
    even with the readiness read (a database read hosted) between the dedupe
    and the submit."""
    import asyncio
    import time
    from types import SimpleNamespace

    from splitsmith.highlights import StageReadiness
    from splitsmith.ui import highlights_api

    class Jobs:
        def __init__(self) -> None:
            self.active: dict | None = None
            self.submitted = 0

        async def find_active(self, *, kind, shooter_slug):
            return self.active if kind == highlights_api.JOB_KIND else None

        async def submit(self, *, kind, shooter_slug, args):
            self.submitted += 1
            self.active = SimpleNamespace(model_dump=lambda mode=None, n=self.submitted: {"id": f"job-{n}"})
            return self.active

    def slow_rows(state, slug):
        time.sleep(0.2)  # the window between the dedupe and the submit
        return (StageReadiness(1, "ready"),)

    monkeypatch.setattr(highlights_api, "stage_rows", slow_rows)
    jobs = Jobs()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(splitsmith_state=SimpleNamespace(jobs=jobs)))
    )
    body = highlights_api.HighlightReelRequest(moments=["fastest_draw"])

    async def both():
        return await asyncio.gather(
            highlights_api.submit_highlight_reel("me", body, request),
            highlights_api.submit_highlight_reel("me", body, request),
        )

    responses = asyncio.run(both())
    assert jobs.submitted == 1
    assert [r.body for r in responses][0] == [r.body for r in responses][1]


def test_a_reel_run_reads_back_from_the_history_route(tmp_path, monkeypatch):
    client, _root = _ready_match(tmp_path)
    monkeypatch.setattr("splitsmith.ui.highlights_api.make_reel", _fake_make_reel([]))
    _run_job(client, {"moments": ["fastest_draw"]})
    (run,) = _runs_from_route(client)
    assert run["kind"] == "match" and run["formats"] == ["highlight_reel"]
    assert [a["kind"] for a in run["artifacts"]] == ["match_video", "sidecar"]
    assert run["stage_numbers"] == [1, 2]


def test_readiness_reads_docs_handed_in_like_hosted(tmp_path):
    _client, root = _seed_match_export_project(tmp_path, stage_count=2)
    _trimmed(root)
    project = MatchProject.load(root / SHOOTER)
    docs = {1: {"shots": [], "events_seeded": 4, "events": []}, 2: None}
    statuses = {1: StageStatus.audited, 2: StageStatus.ready}
    rows = readiness_for_project(project, statuses, docs)
    assert [(r.stage_number, r.state) for r in rows] == [(1, "ready"), (2, "not_audited")]
