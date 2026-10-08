"""The server passes and holds ``AppState.audit_lock`` where the auto-sync
spec (2026-09-27 s5) says it does (#1073).

``test_audit_revision.py::test_apply_pull_holds_the_audit_lock`` pins
``_apply_pull`` itself. These pin the wiring around it: removing the
``audit_lock=`` argument from the sync job, or replacing either ``with
state.audit_lock:`` with a no-op, fails one of them.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

from splitsmith import user_config
from splitsmith.ui import server as server_mod

from .test_audit_local_save import _match_context
from .test_ui_server import _seed_match_export_project

AUDIT = "/api/shooters/me/stages/1/audit"


class _ProbeLock:
    """An RLock that knows whether the current thread holds it."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._owner: int | None = None
        self._depth = 0

    def __enter__(self) -> _ProbeLock:
        self._lock.acquire()
        self._owner = threading.get_ident()
        self._depth += 1
        return self

    def __exit__(self, *exc: object) -> None:
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
        self._lock.release()

    def held(self) -> bool:
        return self._owner == threading.get_ident() and self._depth > 0


def _probe_saves(state, monkeypatch) -> tuple[_ProbeLock, list[bool], list[bool]]:
    """Swap in a probe lock; record whether it is held at each audit load
    and save."""
    probe = _ProbeLock()
    monkeypatch.setattr(state, "audit_lock", probe)
    loads: list[bool] = []
    saves: list[bool] = []
    real_load, real_save = state.load_audit, state.save_audit

    def load(*a, **kw):
        loads.append(probe.held())
        return real_load(*a, **kw)

    def save(*a, **kw):
        saves.append(probe.held())
        return real_save(*a, **kw)

    monkeypatch.setattr(state, "load_audit", load)
    monkeypatch.setattr(state, "save_audit", save)
    return probe, loads, saves


def test_the_sync_job_hands_the_pull_the_server_audit_lock(tmp_path: Path, monkeypatch) -> None:
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state
    prefs = user_config.GlobalPrefs(hosted_base_url="http://127.0.0.1:9", hosted_token="t")
    monkeypatch.setattr(server_mod.user_config, "load_global_prefs", lambda: prefs)
    seen: list[object] = []

    def fake_sync(match_root, **kw):
        seen.append(kw.get("audit_lock"))
        raise server_mod.SyncClientError("stop here")

    monkeypatch.setattr(server_mod, "run_bidirectional_sync", fake_sync)
    id_token = server_mod.current_match_id.set("m-lock")
    try:
        with _match_context(project_root):
            job = asyncio.run(state.jobs.submit(kind="sync_match"))
    finally:
        server_mod.current_match_id.reset(id_token)
    deadline = time.time() + 5.0
    while time.time() < deadline and not seen:
        time.sleep(0.02)
    while time.time() < deadline:
        if asyncio.run(state.jobs.get(job.id)).status.value in ("failed", "succeeded", "cancelled"):
            break
        time.sleep(0.02)
    assert seen and seen[0] is state.audit_lock


def test_the_audit_put_compares_and_saves_under_the_lock(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state
    loaded = client.get(AUDIT).json()
    _, loads, saves = _probe_saves(state, monkeypatch)
    loaded["shots"] = [{"shot_number": 1, "time": 1.5, "source": "manual"}]
    assert client.put(AUDIT, json=loaded).status_code == 200
    assert loads and all(loads), loads
    assert saves == [True]


def test_the_remerge_save_reloads_and_saves_under_the_lock(tmp_path: Path, monkeypatch) -> None:
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state
    _, loads, saves = _probe_saves(state, monkeypatch)
    with _match_context(project_root):
        server_mod._save_audit_with_remerge(
            state,
            "me",
            1,
            doc={},
            version=0,
            merge=lambda d: {**d, "probe": True},
            default=dict,
        )
    assert loads == [True] and saves == [True]
    on_disk = json.loads((project_root / "shooters" / "me" / "audit" / "stage1.json").read_text())
    assert on_disk["probe"] is True


# -- every other local audit writer (#1075) --------------------------------
#
# The coach save, the triage flag and accept, the beep-confirm stub and the
# sync's shot-id migration each load, edit and save an audit doc. A pull
# landing between one of those loads and its save lost either side.


def test_the_triage_flag_writes_under_the_lock(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_match_export_project(tmp_path, stage_count=1)
    _, loads, saves = _probe_saves(client.app.state.splitsmith_state, monkeypatch)
    resp = client.post("/api/shooters/me/stages/1/attention", json={"flagged": True, "note": "check"})
    assert resp.status_code == 200, resp.text
    assert saves == [True] and loads[0] is True


def test_the_triage_accept_writes_under_the_lock(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_match_export_project(tmp_path, stage_count=1)
    _, loads, saves = _probe_saves(client.app.state.splitsmith_state, monkeypatch)
    resp = client.post("/api/shooters/me/stages/1/audit/accept")
    assert resp.status_code == 200, resp.text
    assert saves == [True] and loads[0] is True


def test_coach_writes_hold_the_lock(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_match_export_project(tmp_path, stage_count=1)
    _, loads, saves = _probe_saves(client.app.state.splitsmith_state, monkeypatch)
    assert client.post("/api/shooters/me/stages/1/coach/reclassify").status_code == 200
    resp = client.patch("/api/shooters/me/stages/1/shots/1/coach", json={"coaching_note": "smoother"})
    assert resp.status_code == 200, resp.text
    assert saves == [True, True]
    # Each save's own load ran under the lock too (not only the save).
    assert loads[-1] is True


def test_the_beep_confirm_stub_is_written_under_the_lock(tmp_path: Path, monkeypatch) -> None:
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state
    (project_root / "shooters" / "me" / "audit" / "stage1.json").unlink()

    async def no_submit(**kw):
        return None

    monkeypatch.setattr(state.jobs, "submit", no_submit)
    _, _, saves = _probe_saves(state, monkeypatch)
    project = client.get("/api/shooters/me/project").json()
    video_id = project["stages"][0]["videos"][0]["video_id"]
    resp = client.post(f"/api/shooters/me/stages/1/videos/{video_id}/beep/review", json={"reviewed": True})
    assert resp.status_code == 200, resp.text
    assert saves == [True]


def test_the_shot_id_migration_stamps_under_the_lock(tmp_path: Path, monkeypatch) -> None:
    import splitsmith.sync.run as run_mod

    _, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    path = project_root / "shooters" / "me" / "audit" / "stage1.json"
    doc = json.loads(path.read_text())
    for shot in doc["shots"]:
        shot.pop("id", None)
    path.write_text(json.dumps(doc))
    probe = _ProbeLock()
    held: list[bool] = []
    real_write = run_mod.atomic_write_json

    def spy(*a, **kw):
        held.append(probe.held())
        return real_write(*a, **kw)

    monkeypatch.setattr(run_mod, "atomic_write_json", spy)
    assert run_mod.migrate_shot_ids(project_root, audit_lock=probe) == 1
    assert held == [True]


def test_events_put_and_coach_get_seed_hold_the_lock(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_match_export_project(tmp_path, stage_count=1)
    _, loads, saves = _probe_saves(client.app.state.splitsmith_state, monkeypatch)
    body = client.get("/api/shooters/me/stages/1/coach")
    assert body.status_code == 200, body.text
    resp = client.put(
        "/api/shooters/me/stages/1/events",
        json={
            "events": [{"id": "evt-1", "kind": "movement", "start": 0.2, "end": 0.9, "source": "manual"}],
            "_version": body.json()["_version"],
        },
    )
    assert resp.status_code == 200, resp.text
    assert all(saves), saves
    assert loads[-1] is True
