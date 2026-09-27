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
