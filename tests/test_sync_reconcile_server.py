"""Reconcile after sync and the shared confirm rule (spec 2026-09-27 s3)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from splitsmith.match_project import MatchProject
from splitsmith.sync.auto_state import load_auto_prefs
from splitsmith.sync.reconcile import ReconcileStep
from splitsmith.ui import server as server_mod
from splitsmith.ui.jobs import Job, JobStatus

from .test_audit_local_save import _match_context
from .test_ui_server import _seed_match_export_project


def _job(job_id: str, kind: str, status: JobStatus) -> Job:
    now = datetime.now(UTC)
    return Job(id=job_id, kind=kind, status=status, created_at=now, updated_at=now)


def _untrim_stage_one(project_root: Path) -> str:
    """Put stage 1 in the state a phone confirm leaves after a pull:
    reviewed beep, no trim."""
    root = project_root / "shooters" / "me"
    project = MatchProject.load(root)
    video = project.stage(1).primary()
    video.beep_reviewed = True
    video.processed["trim"] = False
    project.save(root)
    return video.video_id


def test_submit_reconcile_steps_queues_a_trim_for_a_pulled_confirm(tmp_path: Path, monkeypatch) -> None:
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state
    video_id = _untrim_stage_one(project_root)
    submitted: list[dict] = []

    async def fake_submit(**kw):
        submitted.append(kw)
        return _job(f"j{len(submitted)}", kw["kind"], JobStatus.PENDING)

    monkeypatch.setattr(state.jobs, "submit", fake_submit)
    with _match_context(project_root):
        server_mod._submit_reconcile_steps(state, project_root)
    assert [(s["kind"], s["video_id"]) for s in submitted] == [("trim", video_id)]
    ((_, _, slug, stage),) = state.reconcile_jobs
    assert (slug, stage) == ("me", 1)
    assert next(iter(state.reconcile_jobs.values()))[1].kind == "trim"


def test_failed_reconcile_step_is_recorded_and_skipped(tmp_path: Path, monkeypatch) -> None:
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state
    video_id = _untrim_stage_one(project_root)
    step = ReconcileStep(kind="trim", slug="me", stage_number=1, video_id=video_id, input_key="5.0000")
    state.reconcile_jobs[server_mod._reconcile_key("m-f", "trim", "me", 1)] = (project_root, step)
    failed = _job("jf", "trim", JobStatus.FAILED)
    failed.match_id, failed.shooter_slug, failed.stage_number = "m-f", "me", 1
    server_mod._record_reconcile_outcome(state, failed)
    assert load_auto_prefs(project_root).reconcile_failures == {step.key: "5.0000"}
    assert state.reconcile_jobs == {}

    # The next pass skips the step while its inputs are unchanged.
    submitted: list[dict] = []

    async def fake_submit(**kw):
        submitted.append(kw)
        return _job("jx", kw["kind"], JobStatus.PENDING)

    monkeypatch.setattr(state.jobs, "submit", fake_submit)
    project = MatchProject.load(project_root / "shooters" / "me")
    beep = project.stage(1).primary().beep_time
    assert f"{beep:.4f}" == "5.0000", "seed beep moved; update input_key above"
    with _match_context(project_root):
        server_mod._submit_reconcile_steps(state, project_root)
    assert submitted == []


def test_a_step_that_fails_before_submit_returns_is_still_memoized(tmp_path: Path, monkeypatch) -> None:
    """A trim whose source is missing fails in milliseconds on the second
    worker, which can beat submit() back to the caller. The memo must not
    depend on the caller registering the job first."""
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state
    _untrim_stage_one(project_root)

    async def submit_that_fails_at_once(**kw):
        job = _job("fast", kw["kind"], JobStatus.FAILED)
        job.match_id = server_mod.current_match_id.get()
        job.shooter_slug = kw["shooter_slug"]
        job.stage_number = kw["stage_number"]
        job.video_id = kw.get("video_id")
        server_mod._record_reconcile_outcome(state, job)  # the listener runs first
        return job

    monkeypatch.setattr(state.jobs, "submit", submit_that_fails_at_once)
    id_token = server_mod.current_match_id.set("m-fast")
    try:
        with _match_context(project_root):
            server_mod._submit_reconcile_steps(state, project_root)
    finally:
        server_mod.current_match_id.reset(id_token)
    assert list(load_auto_prefs(project_root).reconcile_failures.values()) == ["5.0000"]
    assert state.reconcile_jobs == {}


def test_a_running_auto_sync_stops_at_its_next_progress_report_when_cancelled(
    tmp_path: Path, monkeypatch
) -> None:
    """Quitting cancels an auto_sync (embedded._RESUMABLE_JOB_KINDS); that
    only helps if the sync body notices, since cancellation is cooperative."""
    import asyncio
    import threading
    import time

    from splitsmith import user_config

    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state
    prefs = user_config.GlobalPrefs(hosted_base_url="http://127.0.0.1:9", hosted_token="t")
    monkeypatch.setattr(server_mod.user_config, "load_global_prefs", lambda: prefs)
    in_sync = threading.Event()
    go_on = threading.Event()

    def fake_sync(match_root, *, on_progress, **kw):
        in_sync.set()
        go_on.wait(timeout=5.0)
        on_progress(0.5, "uploading")
        raise AssertionError("the sync kept going after a cancel")

    monkeypatch.setattr(server_mod, "run_bidirectional_sync", fake_sync)
    id_token = server_mod.current_match_id.set("m-cancel")
    try:
        with _match_context(project_root):
            job = asyncio.run(state.jobs.submit(kind="auto_sync"))
    finally:
        server_mod.current_match_id.reset(id_token)
    assert in_sync.wait(timeout=5.0)
    asyncio.run(state.jobs.cancel(job.id))
    go_on.set()
    deadline = time.time() + 5.0
    while time.time() < deadline:
        final = asyncio.run(state.jobs.get(job.id))
        if final.status.value in ("cancelled", "failed", "succeeded"):
            break
        time.sleep(0.02)
    assert final.status.value == "cancelled", final.error
    assert load_auto_prefs(project_root).last_auto is None  # a cancel is not a failure
