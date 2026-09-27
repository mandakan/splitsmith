"""Auto-sync service driver (spec 2026-09-27 s2)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx

from splitsmith.sync.auto_state import update_auto_prefs
from splitsmith.sync.state import SyncState, save_sync_state
from splitsmith.ui.auto_sync import AutoSyncService
from splitsmith.ui.jobs import JobRegistry
from splitsmith.user_config import GlobalPrefs

from .test_ui_server import _seed_match_export_project


class _Matches:
    def __init__(self, roots: dict[str, Path]) -> None:
        self.roots = roots

    def refresh_from_recent_projects(self) -> int:
        return len(self.roots)

    def known_ids(self) -> list[str]:
        return list(self.roots)

    def resolve(self, match_id: str) -> Path:
        return self.roots[match_id]


def _synced(root: Path, versions: dict[str, int]) -> None:
    save_sync_state(root, SyncState(last_synced_at=datetime(2026, 9, 27, tzinfo=UTC), doc_versions=versions))


def _service(
    tmp_path: Path, fetch, submitted: list, *, token: dict | None = None
) -> tuple[AutoSyncService, Path]:
    root = tmp_path / "m1"
    root.mkdir()
    _synced(root, {"match": 1, "project/me": 2})
    token = token if token is not None else {"v": "t"}

    async def submit(match_id: str, match_root: Path) -> None:
        submitted.append(match_id)

    svc = AutoSyncService(
        jobs=JobRegistry(max_concurrent=1),
        matches=_Matches({"m1": root}),
        submit_auto_sync=submit,
        load_prefs=lambda: GlobalPrefs(hosted_base_url="https://h", hosted_token=token["v"]),
        fetch_fingerprints=fetch,
        clock=lambda: 1000.0,
    )
    return svc, root


def test_first_tick_pulls_every_enabled_match(tmp_path: Path) -> None:
    submitted: list[str] = []
    svc, _ = _service(tmp_path, lambda prefs: {"m1": (2, 3)}, submitted)
    asyncio.run(svc.tick())
    assert submitted == ["m1"]


def test_remote_change_submits_an_auto_sync(tmp_path: Path) -> None:
    submitted: list[str] = []
    server = {"m1": (2, 3)}
    svc, _ = _service(tmp_path, lambda prefs: dict(server), submitted)
    asyncio.run(svc.tick())  # startup pull, fingerprints equal
    server["m1"] = (2, 4)  # the phone confirms a beep
    svc.core.on_sync_done("m1", 1000.0, ok=True)
    submitted.clear()
    svc.core.next_poll_at = 0.0
    asyncio.run(svc.tick())
    assert submitted == ["m1"]


def test_matching_fingerprint_and_disabled_match_do_nothing(tmp_path: Path) -> None:
    submitted: list[str] = []
    svc, root = _service(tmp_path, lambda prefs: {"m1": (2, 3)}, submitted)
    asyncio.run(svc.tick())
    svc.core.on_sync_done("m1", 1000.0, ok=True)
    submitted.clear()
    svc.core.next_poll_at = 0.0
    asyncio.run(svc.tick())
    assert submitted == []
    update_auto_prefs(root, lambda p: setattr(p, "enabled", False))
    svc.core.mark_pull_due("m1")
    asyncio.run(svc.tick())
    assert submitted == []


def test_toggle_survives_a_running_sync(tmp_path: Path) -> None:
    """The flag is in auto_sync.json; run_sync's sync_state saves can't undo it."""
    svc, root = _service(tmp_path, lambda prefs: {}, [])
    update_auto_prefs(root, lambda p: setattr(p, "enabled", False))
    _synced(root, {"match": 5})  # what a running sync writes
    assert svc.status_for(root)["enabled"] is False


def test_auth_failure_pauses_until_the_token_changes(tmp_path: Path) -> None:
    submitted: list[str] = []
    token = {"v": "t"}

    def fetch(prefs):
        if prefs.hosted_token == "t":
            request = httpx.Request("GET", "https://h/api/sync/fingerprints")
            raise httpx.HTTPStatusError("401", request=request, response=httpx.Response(401, request=request))
        return {"m1": (9, 9)}

    svc, _ = _service(tmp_path, fetch, submitted, token=token)
    svc.core.on_sync_done("m1", 0.0, ok=True)
    svc._started = True  # skip the startup pull; this test is about the poll
    asyncio.run(svc.tick())
    assert submitted == [] and svc.core.auth_blocked()
    assert svc.status_for(tmp_path / "m1")["paused_reason"] == "sign in again in hosted sync settings"
    token["v"] = "t2"
    asyncio.run(svc.tick())
    assert submitted == ["m1"]


def test_offline_poll_pauses_with_a_reason(tmp_path: Path) -> None:
    def fetch(prefs):
        raise httpx.ConnectError("down")

    svc, root = _service(tmp_path, fetch, [])
    svc._started = True
    asyncio.run(svc.tick())
    assert svc.status_for(root)["paused_reason"] == "offline: could not reach the hosted server"


def test_local_write_route_marks_the_match_dirty(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SPLITSMITH_AUTO_SYNC", "1")
    client, _ = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state
    svc = state.auto_sync
    assert svc is not None
    loaded = client.get("/api/shooters/me/stages/1/audit").json()
    assert client.put("/api/shooters/me/stages/1/audit", json=loaded).status_code == 200
    (match_id,) = state.matches.known_ids()
    assert svc.core._matches[match_id].push_due_at is not None


def test_job_completion_marks_dirty_and_sync_completion_does_not(tmp_path: Path) -> None:
    from splitsmith.ui.jobs import Job, JobStatus

    svc, _ = _service(tmp_path, lambda prefs: {}, [])
    now = datetime.now(UTC)
    svc.on_job_terminal(
        Job(
            id="a",
            kind="auto_sync",
            match_id="m1",
            status=JobStatus.SUCCEEDED,
            created_at=now,
            updated_at=now,
        )
    )
    assert svc.core._matches["m1"].push_due_at is None
    svc.on_job_terminal(
        Job(id="b", kind="trim", match_id="m1", status=JobStatus.SUCCEEDED, created_at=now, updated_at=now)
    )
    assert svc.core._matches["m1"].push_due_at == 1000.0


def test_edit_during_a_manual_sync_is_not_forgotten(tmp_path: Path) -> None:
    from splitsmith.ui.jobs import Job, JobStatus

    clock = [110.0]
    svc, _ = _service(tmp_path, lambda prefs: {}, [])
    svc._clock = lambda: clock[0]
    svc.mark_dirty("m1")  # saved while the manual sync was uploading
    clock[0] = 130.0
    started = datetime.fromtimestamp(100.0, tz=UTC)
    now = datetime.fromtimestamp(130.0, tz=UTC)
    svc.on_job_terminal(
        Job(
            id="s",
            kind="sync_match",
            match_id="m1",
            status=JobStatus.SUCCEEDED,
            created_at=started,
            updated_at=now,
            started_at=started,
        )
    )
    assert svc.core._matches["m1"].push_due_at == 110.0


def test_read_only_posts_and_unsynced_jobs_do_not_mark_dirty(tmp_path: Path) -> None:
    """Each false dirty mark is one pointless full sync 45 s later. The
    Export page fires export-preview on every Look change."""
    from splitsmith.ui.auto_sync import write_marks_dirty
    from splitsmith.ui.jobs import Job, JobStatus

    assert write_marks_dirty("shooters/me/stages/1/audit")
    assert write_marks_dirty("shooters/me/videos/scan")  # registers videos
    for rest in (
        "shooters/me/export-preview",
        "shooters/me/videos/suggest-coverage",
        "shooters/me/videos/relink/scan",
        "match/merge/plan",
        "match/sync",
        "match/sync/auto",
        "jobs/abc/cancel",
    ):
        assert not write_marks_dirty(rest), rest

    svc, _ = _service(tmp_path, lambda prefs: {}, [])
    now = datetime.now(UTC)
    for kind in (
        "match_export",
        "export",
        "youtube_upload",
        "compare-grid",
        "generate_proxy",
        "model_download",
    ):
        svc.on_job_terminal(
            Job(id=kind, kind=kind, match_id="m1", status=JobStatus.SUCCEEDED, created_at=now, updated_at=now)
        )
    assert "m1" not in svc.core._matches or svc.core._matches["m1"].push_due_at is None


def test_a_failed_auto_run_is_not_reported_after_a_later_successful_sync(tmp_path: Path) -> None:
    from splitsmith.sync.auto_state import AutoRunSummary

    svc, root = _service(tmp_path, lambda prefs: {}, [])
    failed_at = datetime(2026, 9, 26, tzinfo=UTC)
    update_auto_prefs(
        root, lambda p: setattr(p, "last_auto", AutoRunSummary(at=failed_at, ok=False, message="offline"))
    )
    # _synced() recorded last_synced_at 2026-09-27: a manual sync after the failure.
    assert svc.status_for(root)["last_auto"] is None
    save_sync_state(root, SyncState(last_synced_at=datetime(2026, 9, 25, tzinfo=UTC)))
    assert svc.status_for(root)["last_auto"]["message"] == "offline"


def test_status_reports_full_media_and_full_trims_on_hosted(tmp_path: Path) -> None:
    from splitsmith.sync.state import SyncedItem

    svc, root = _service(tmp_path, lambda prefs: {}, [])
    trimmed = root / "shooters" / "a" / "trimmed"
    trimmed.mkdir(parents=True)
    (trimmed / "s1_cam_x_trimmed.mp4").write_bytes(b"t")
    (trimmed / "s1_cam_x_web.mp4").write_bytes(b"w")
    item = SyncedItem(sha256="a", size=1, mtime_ns=1)
    save_sync_state(
        root,
        SyncState(
            last_synced_at=datetime(2026, 9, 27, tzinfo=UTC),
            items={
                "matches/m/shooters/a/trimmed/s1_cam_x_trimmed.mp4": item,
                "matches/m/shooters/a/trimmed/s1_cam_x_web.mp4": item,
            },
        ),
    )
    status = svc.status_for(root)
    assert status["full_media"] is False
    assert status["full_trims_on_hosted"] == 1


def test_put_auto_changes_only_the_fields_sent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SPLITSMITH_AUTO_SYNC", "1")
    client, _ = _seed_match_export_project(tmp_path, stage_count=1)
    (match_id,) = client.app.state.splitsmith_state.matches.known_ids()
    url = f"/api/matches/{match_id}/match/sync/auto"
    resp = client._client.put(url, json={"enabled": False})
    assert resp.status_code == 200, resp.text
    body = client._client.put(url, json={"full_media": True}).json()
    assert body["full_media"] is True and body["setting"] is False
    body = client._client.put(url, json={"enabled": None}).json()
    assert body["setting"] is None and body["full_media"] is True


def test_full_trims_on_hosted_counts_only_what_the_next_push_removes(tmp_path: Path) -> None:
    """A clip whose rendition failed keeps its full trim on hosted; the card
    must not promise to remove it."""
    from splitsmith.sync.state import SyncedItem

    svc, root = _service(tmp_path, lambda prefs: {}, [])
    trimmed = root / "shooters" / "a" / "trimmed"
    trimmed.mkdir(parents=True)
    (trimmed / "s1_cam_x_trimmed.mp4").write_bytes(b"t")
    item = SyncedItem(sha256="a", size=1, mtime_ns=1)
    save_sync_state(
        root,
        SyncState(
            last_synced_at=datetime(2026, 9, 27, tzinfo=UTC),
            items={
                "matches/m/shooters/a/trimmed/s1_cam_x_trimmed.mp4": item,
                "matches/m/shooters/a/trimmed/s1_cam_x_web.mp4": item,
            },
        ),
    )
    assert svc.status_for(root)["full_trims_on_hosted"] == 0  # no local rendition
    (trimmed / "s1_cam_x_web.mp4").write_bytes(b"w")
    assert svc.status_for(root)["full_trims_on_hosted"] == 1


def test_global_switch_rejects_per_match_fields(tmp_path: Path, monkeypatch) -> None:
    """``{"full_media": true}`` on the global route used to read as
    ``enabled`` absent -> on, silently re-enabling auto-sync everywhere."""
    monkeypatch.setenv("SPLITSMITH_AUTO_SYNC", "1")
    client, _ = _seed_match_export_project(tmp_path, stage_count=1)
    assert client._client.put("/api/settings/auto-sync", json={"enabled": False}).status_code == 200
    assert client._client.put("/api/settings/auto-sync", json={"full_media": True}).status_code == 422


class _ActiveJobs:
    def __init__(self, jobs: list) -> None:
        self.jobs = jobs

    async def list(self) -> list:
        return self.jobs


def test_a_render_on_another_match_holds_the_sync(tmp_path: Path) -> None:
    """0.43.0 started HFO Masters' sync, and its web-clip backfill, seven
    minutes into a Hostfinalen render: the busy rule was per match."""
    from splitsmith.ui.jobs import Job, JobStatus

    submitted: list[str] = []
    svc, _ = _service(tmp_path, lambda prefs: {"m1": (2, 3)}, submitted)
    now = datetime.now(UTC)

    def running(kind: str) -> _ActiveJobs:
        return _ActiveJobs(
            [
                Job(
                    id="j",
                    kind=kind,
                    match_id="other",
                    status=JobStatus.RUNNING,
                    created_at=now,
                    updated_at=now,
                )
            ]
        )

    for kind in ("match_export", "compare-grid", "export"):
        svc._jobs = running(kind)
        asyncio.run(svc.tick())
        assert submitted == [], kind
    svc._jobs = running("trim")
    asyncio.run(svc.tick())
    assert submitted == ["m1"]


def test_status_says_a_failed_match_waits_for_a_change(tmp_path: Path) -> None:
    """#1070: one visible failure, then quiet; the card says why."""
    svc, root = _service(tmp_path, lambda prefs: {"m1": (2, 3)}, [])
    assert svc.status_for(root)["waiting_for_change"] is False
    svc.core.on_sync_done("m1", 1000.0, ok=False)
    assert svc.status_for(root)["waiting_for_change"] is True
    svc.mark_dirty("m1")
    assert svc.status_for(root)["waiting_for_change"] is False


def test_a_failed_auto_sync_is_not_resubmitted_on_later_ticks(tmp_path: Path) -> None:
    submitted: list[str] = []
    svc, _ = _service(tmp_path, lambda prefs: {"m1": (2, 3)}, submitted)
    asyncio.run(svc.tick())  # the startup pull
    assert submitted == ["m1"]
    svc.core.on_sync_done("m1", 1000.0, ok=False)
    submitted.clear()
    svc._clock = lambda: 1000.0 + 24 * 3600  # a day of ticks and polls later
    for _ in range(3):
        svc.core.next_poll_at = 0.0
        asyncio.run(svc.tick())
    assert submitted == []


def test_republish_resets_the_old_mirror_and_starts_a_sync(tmp_path: Path, monkeypatch) -> None:
    """A match deleted on hosted stays off until the user publishes it again;
    that forgets the dead mirror's versions, hashes, uploads and bases."""
    from splitsmith.sync.auto_state import load_auto_prefs
    from splitsmith.sync.state import load_sync_state
    from splitsmith.ui import server as server_mod

    monkeypatch.setenv("SPLITSMITH_AUTO_SYNC", "1")
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state
    (match_id,) = state.matches.known_ids()
    prefs = GlobalPrefs(hosted_base_url="https://h", hosted_token="t")
    monkeypatch.setattr(server_mod.user_config, "load_global_prefs", lambda: prefs)
    submitted: list[str] = []
    url = f"/api/matches/{match_id}/match/sync/republish"

    # Not deleted on hosted: nothing to republish.
    assert client._client.post(url).status_code == 409

    _synced(project_root, {"match": 4, "project/me": 7})
    (project_root / "sync_base").mkdir()
    (project_root / "sync_base" / "match.json").write_text("{}")
    update_auto_prefs(
        project_root, lambda p: setattr(p, "hosted_deleted_at", datetime(2026, 9, 27, tzinfo=UTC))
    )
    assert client._client.get(f"/api/matches/{match_id}/match/sync/auto").json()["hosted_deleted"] is True

    async def capture(**kw):
        submitted.append(kw["kind"])
        return _job_stub(kw["kind"])

    monkeypatch.setattr(state.jobs, "submit", capture)
    resp = client._client.post(url)
    assert resp.status_code == 200, resp.text
    assert submitted == ["sync_match"]
    fresh = load_sync_state(project_root)
    assert fresh.doc_versions == {} and fresh.last_synced_at is None and fresh.items == {}
    assert not (project_root / "sync_base").exists()
    assert load_auto_prefs(project_root).hosted_deleted_at is None


def _job_stub(kind: str):
    from splitsmith.ui.jobs import Job, JobStatus

    now = datetime.now(UTC)
    return Job(id="j1", kind=kind, status=JobStatus.PENDING, created_at=now, updated_at=now)
