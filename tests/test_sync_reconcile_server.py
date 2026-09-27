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
    assert state.reconcile_jobs["j1"][1].kind == "trim"


def test_failed_reconcile_step_is_recorded_and_skipped(tmp_path: Path, monkeypatch) -> None:
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state
    video_id = _untrim_stage_one(project_root)
    step = ReconcileStep(kind="trim", slug="me", stage_number=1, video_id=video_id, input_key="5.0000")
    state.reconcile_jobs["jf"] = (project_root, step)
    server_mod._record_reconcile_outcome(state, _job("jf", "trim", JobStatus.FAILED))
    assert load_auto_prefs(project_root).reconcile_failures == {step.key: "5.0000"}
    assert "jf" not in state.reconcile_jobs

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
