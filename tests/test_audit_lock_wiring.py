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
