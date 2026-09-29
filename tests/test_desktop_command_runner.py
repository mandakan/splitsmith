"""Desktop command runner (#1100 S2, spec 2026-09-28): claim after the
sync, the revision guard, heartbeats, cancel, and completing only after
the result has been synced."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from splitsmith import match_model, youtube_sidecar
from splitsmith.audit_revision import audit_revision
from splitsmith.match_project import MatchProject
from splitsmith.sync.commands import (
    STAGE_CHANGED,
    YOUTUBE_NOT_CONNECTED,
    local_stage_revision,
    prior_result,
    refuse_reason,
)
from splitsmith.ui.command_runner import CommandRunner
from splitsmith.ui.jobs import Job, JobStatus

SLUG = "me"


def _match(tmp_path: Path, audit: dict | None) -> Path:
    root = tmp_path / "match"
    match = match_model.Match.init(root, name="M")
    match.save(root)
    match.add_shooter(root, match_model.Shooter(slug=SLUG, name="Me"))
    MatchProject.init(match_model.Match.shooter_root(root, SLUG), name="M")
    if audit is not None:
        audit_dir = match_model.Match.shooter_root(root, SLUG) / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        (audit_dir / "stage1.json").write_text(json.dumps(audit), encoding="utf-8")
    return root


def _command(**over) -> dict:
    base = {
        "id": "c1",
        "kind": "shot_detect",
        "slug": SLUG,
        "stage_number": 1,
        "args": {"reset": True},
        "expected_revision": None,
    }
    base.update(over)
    return base


# -- the guard -------------------------------------------------------------


def test_the_local_revision_is_the_hash_hosted_recorded(tmp_path: Path) -> None:
    doc = {"shots": [{"id": "a", "time": 1.0}], "detection": "ensemble"}
    root = _match(tmp_path, doc)
    assert local_stage_revision(root, SLUG, 1) == audit_revision(doc)
    assert local_stage_revision(root, SLUG, 2) == audit_revision(None)


def test_the_guard_refuses_a_changed_stage_and_unknown_requests(tmp_path: Path) -> None:
    doc = {"shots": [{"id": "a", "time": 1.0}]}
    root = _match(tmp_path, doc)
    assert refuse_reason(root, _command(expected_revision=audit_revision(doc))) is None
    assert refuse_reason(root, _command(expected_revision=audit_revision({"shots": []}))) == STAGE_CHANGED
    assert "cannot run" in refuse_reason(root, _command(kind="render_export"))
    assert "not in this match" in refuse_reason(root, _command(slug="nobody"))


def _upload_command(**over) -> dict:
    base = {
        "id": "c9",
        "kind": "render_upload",
        "slug": SLUG,
        "stage_number": None,
        "args": {
            "request": {
                "stage_numbers": [1],
                "output_format": "mp4",
                "youtube_sidecar": True,
                "youtube_upload": True,
            }
        },
        "expected_revision": None,
    }
    base.update(over)
    return base


def _sidecar(root: Path, name: str, *, command_id: str | None) -> None:
    shooter_root = match_model.Match.shooter_root(root, SLUG)
    exports = MatchProject.load(shooter_root).exports_path(shooter_root)
    exports.mkdir(parents=True, exist_ok=True)
    record = youtube_sidecar.UploadRecord(
        video_id="vid1",
        url="https://youtu.be/vid1",
        privacy="unlisted",
        uploaded_at=datetime(2026, 9, 29, tzinfo=UTC),
        channel_title="My channel",
        command_id=command_id,
    )
    sidecar = youtube_sidecar.YouTubeSidecar(title="T", description="D", upload=record)
    youtube_sidecar.write_sidecar(sidecar, exports / f"{name}-youtube.json")


def test_a_render_upload_is_refused_without_a_youtube_connection(tmp_path: Path, monkeypatch) -> None:
    root = _match(tmp_path, None)
    monkeypatch.setattr("splitsmith.sync.commands.oauth.load_connection", lambda: None)
    assert refuse_reason(root, _upload_command()) == YOUTUBE_NOT_CONNECTED
    monkeypatch.setattr("splitsmith.sync.commands.oauth.load_connection", lambda: object())
    assert refuse_reason(root, _upload_command()) is None
    assert "not in this match" in refuse_reason(root, _upload_command(slug="nobody"))


def test_prior_result_finds_this_commands_upload_only(tmp_path: Path) -> None:
    root = _match(tmp_path, None)
    assert prior_result(root, _upload_command()) is None
    _sidecar(root, "other", command_id="someone-else")
    _sidecar(root, "plain", command_id=None)
    assert prior_result(root, _upload_command()) is None
    _sidecar(root, "mine", command_id="c9")
    assert prior_result(root, _upload_command()) == {
        "video_id": "vid1",
        "url": "https://youtu.be/vid1",
        "channel_title": "My channel",
    }
    assert prior_result(root, _command(id="c9")) is None  # a re-detect never has one


def test_prior_result_skips_a_malformed_sidecar_sorted_before_the_match(tmp_path: Path) -> None:
    root = _match(tmp_path, None)
    shooter_root = match_model.Match.shooter_root(root, SLUG)
    exports = MatchProject.load(shooter_root).exports_path(shooter_root)
    exports.mkdir(parents=True, exist_ok=True)
    (exports / "broken-youtube.json").write_text("{not json", encoding="utf-8")
    _sidecar(root, "mine", command_id="c9")
    assert prior_result(root, _upload_command()) == {
        "video_id": "vid1",
        "url": "https://youtu.be/vid1",
        "channel_title": "My channel",
    }


# -- the runner, with fakes ------------------------------------------------


def _job(
    job_id: str,
    status: JobStatus,
    *,
    error: str | None = None,
    message: str | None = None,
    result: dict | None = None,
) -> Job:
    now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    return Job(
        id=job_id,
        kind="shot_detect",
        status=status,
        created_at=now,
        updated_at=now,
        finished_at=now if status not in (JobStatus.PENDING, JobStatus.RUNNING) else None,
        error=error,
        message=message,
        result=result,
    )


class _Jobs:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.cancelled: list[str] = []

    async def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    async def cancel(self, job_id: str) -> Job | None:
        self.cancelled.append(job_id)
        return self.jobs.get(job_id)


class _Api:
    def __init__(self, commands: list[dict]) -> None:
        self.to_claim = commands
        self.completed: list[tuple[str, str, str | None]] = []
        self.heartbeats: list[tuple[str, str | None]] = []
        self.reply: dict | None = {"cancel_requested": False}
        self.results: dict[str, dict | None] = {}

    def claim(self, match_ids: list[str]) -> list[dict]:
        out, self.to_claim = self.to_claim, []
        return out

    def heartbeat(self, command_id: str, message: str | None) -> dict | None:
        self.heartbeats.append((command_id, message))
        return self.reply

    def complete(self, command_id, *, status, error=None, result=None) -> None:
        self.completed.append((command_id, status, error))
        self.results[command_id] = result


def _runner(jobs: _Jobs, started: list, synced_now: list, *, start_ok: bool = True) -> CommandRunner:
    async def start(match_id: str, root: Path, command: dict):
        started.append(command["id"])
        if not start_ok:
            return None, "busy"
        jobs.jobs["j1"] = _job("j1", JobStatus.RUNNING, message="detecting")
        return "j1", None

    return CommandRunner(jobs=jobs, start=start, request_sync_now=synced_now.append, clock=lambda: 0.0)


def test_a_command_completes_only_after_a_sync_that_carried_its_result(tmp_path: Path) -> None:
    root = _match(tmp_path, None)
    jobs, started, synced_now = _Jobs(), [], []
    runner = _runner(jobs, started, synced_now)
    api = _Api([_command(expected_revision=audit_revision(None))])

    # Nothing is claimed until a sync of that match succeeded.
    asyncio.run(runner.tick(api, {"m1": root}))
    assert started == []
    runner.claim_after_sync("m1")
    asyncio.run(runner.tick(api, {"m1": root}))
    assert started == ["c1"] and api.heartbeats == [("c1", "detecting")]

    jobs.jobs["j1"] = _job("j1", JobStatus.SUCCEEDED, message="Done -- 23 candidates")
    asyncio.run(runner.tick(api, {"m1": root}))
    assert synced_now == ["m1"] and api.completed == []
    finished = jobs.jobs["j1"].finished_at.timestamp()

    # A sync that started before the job ended does not carry its result.
    runner.on_sync_done("m1", ok=True, started_at=finished - 5, error=None)
    asyncio.run(runner.tick(api, {"m1": root}))
    assert api.completed == []
    runner.on_sync_done("m1", ok=True, started_at=finished + 1, error=None)
    asyncio.run(runner.tick(api, {"m1": root}))
    assert api.completed == [("c1", "succeeded", None)]
    assert runner.tracked_ids() == []


def test_a_changed_stage_is_refused_without_running(tmp_path: Path) -> None:
    root = _match(tmp_path, {"shots": [{"id": "edited-on-the-desktop"}]})
    jobs, started, synced_now = _Jobs(), [], []
    runner = _runner(jobs, started, synced_now)
    api = _Api([_command(expected_revision=audit_revision(None))])
    runner.claim_after_sync("m1")
    asyncio.run(runner.tick(api, {"m1": root}))
    assert started == []
    assert api.completed == [("c1", "failed", STAGE_CHANGED)]


def test_a_failed_job_or_start_fails_the_command(tmp_path: Path) -> None:
    root = _match(tmp_path, None)
    jobs, started, synced_now = _Jobs(), [], []
    runner = _runner(jobs, started, synced_now, start_ok=False)
    api = _Api([_command()])
    runner.claim_after_sync("m1")
    asyncio.run(runner.tick(api, {"m1": root}))
    assert api.completed == [("c1", "failed", "busy")]

    runner = _runner(jobs, started, synced_now)
    api = _Api([_command()])
    runner.claim_after_sync("m1")
    asyncio.run(runner.tick(api, {"m1": root}))
    jobs.jobs["j1"] = _job("j1", JobStatus.FAILED, error="no trim yet")
    asyncio.run(runner.tick(api, {"m1": root}))
    assert api.completed == [("c1", "failed", "no trim yet")]


def test_a_failed_result_sync_fails_the_command_with_the_reason(tmp_path: Path) -> None:
    root = _match(tmp_path, None)
    jobs, started, synced_now = _Jobs(), [], []
    runner = _runner(jobs, started, synced_now)
    api = _Api([_command()])
    runner.claim_after_sync("m1")
    asyncio.run(runner.tick(api, {"m1": root}))
    jobs.jobs["j1"] = _job("j1", JobStatus.SUCCEEDED)
    asyncio.run(runner.tick(api, {"m1": root}))
    runner.on_sync_done("m1", ok=False, started_at=None, error="offline")
    asyncio.run(runner.tick(api, {"m1": root}))
    ((_, status, error),) = api.completed
    assert status == "failed" and "could not be synced: offline" in error


def test_a_cancel_from_the_phone_stops_the_local_job(tmp_path: Path) -> None:
    root = _match(tmp_path, None)
    jobs, started, synced_now = _Jobs(), [], []
    runner = _runner(jobs, started, synced_now)
    api = _Api([_command()])
    runner.claim_after_sync("m1")
    asyncio.run(runner.tick(api, {"m1": root}))
    api.reply = {"cancel_requested": True}
    asyncio.run(runner.tick(api, {"m1": root}))
    assert jobs.cancelled == ["j1"]
    jobs.jobs["j1"] = _job("j1", JobStatus.CANCELLED)
    asyncio.run(runner.tick(api, {"m1": root}))
    assert api.completed == [("c1", "cancelled", None)]


def test_losing_the_claim_stops_and_forgets_the_command(tmp_path: Path) -> None:
    """Hosted answers 409 when the lease lapsed to another desktop or the
    command finished: this desktop must not keep running a duplicate."""
    root = _match(tmp_path, None)
    jobs, started, synced_now = _Jobs(), [], []
    runner = _runner(jobs, started, synced_now)
    api = _Api([_command()])
    runner.claim_after_sync("m1")
    asyncio.run(runner.tick(api, {"m1": root}))
    api.reply = None
    asyncio.run(runner.tick(api, {"m1": root}))
    assert jobs.cancelled == ["j1"] and runner.tracked_ids() == []


def test_a_failed_completion_is_retried_not_lost(tmp_path: Path) -> None:
    """The sync event that decides a command is consumed once; if hosted
    does not take the completion that tick, the next tick must retry it
    rather than leave the command claimed until some later sync."""
    root = _match(tmp_path, None)
    jobs, started, synced_now = _Jobs(), [], []
    runner = _runner(jobs, started, synced_now)
    api = _Api([_command()])
    runner.claim_after_sync("m1")
    asyncio.run(runner.tick(api, {"m1": root}))
    jobs.jobs["j1"] = _job("j1", JobStatus.SUCCEEDED)
    asyncio.run(runner.tick(api, {"m1": root}))
    finished = jobs.jobs["j1"].finished_at.timestamp()

    real_complete = api.complete
    calls = {"n": 0}

    def flaky_complete(command_id, *, status, error=None, result=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("hosted unreachable")
        real_complete(command_id, status=status, error=error, result=result)

    api.complete = flaky_complete
    runner.on_sync_done("m1", ok=True, started_at=finished + 1, error=None)
    asyncio.run(runner.tick(api, {"m1": root}))
    assert api.completed == [] and runner.tracked_ids() == ["c1"]
    asyncio.run(runner.tick(api, {"m1": root}))  # no new sync event needed
    assert api.completed == [("c1", "succeeded", None)]
    assert runner.tracked_ids() == []


def test_a_render_upload_already_made_completes_without_running(tmp_path: Path, monkeypatch) -> None:
    """The upload happened, its completion was lost, the desktop restarted
    and re-claimed it: report the existing video, render nothing."""
    root = _match(tmp_path, None)
    _sidecar(root, "mine", command_id="c9")
    jobs, started, synced_now = _Jobs(), [], []
    runner = _runner(jobs, started, synced_now)
    api = _Api([_upload_command()])
    runner.claim_after_sync("m1")
    asyncio.run(runner.tick(api, {"m1": root}))
    assert started == []
    assert api.completed == [("c9", "succeeded", None)]
    assert api.results["c9"]["url"] == "https://youtu.be/vid1"


def test_a_render_upload_completes_when_its_job_succeeds(tmp_path: Path, monkeypatch) -> None:
    """Its result travels in the command, so no sync is awaited."""
    root = _match(tmp_path, None)
    monkeypatch.setattr("splitsmith.sync.commands.oauth.load_connection", lambda: object())
    jobs, started, synced_now = _Jobs(), [], []
    runner = _runner(jobs, started, synced_now)
    api = _Api([_upload_command()])
    runner.claim_after_sync("m1")
    asyncio.run(runner.tick(api, {"m1": root}))
    assert started == ["c9"]
    done = _job("j1", JobStatus.SUCCEEDED, message="Uploaded")
    done.result = {"video_id": "v2", "url": "https://youtu.be/v2", "channel_title": "C"}
    jobs.jobs["j1"] = done
    asyncio.run(runner.tick(api, {"m1": root}))
    assert synced_now == []
    assert api.completed == [("c9", "succeeded", None)]
    assert api.results["c9"] == {"video_id": "v2", "url": "https://youtu.be/v2", "channel_title": "C"}
