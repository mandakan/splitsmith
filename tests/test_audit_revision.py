"""Audit revisions (spec 2026-09-27 s5): a stale editor gets 409, never
overwrites a doc a pull wrote under it."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from splitsmith.audit_revision import audit_revision
from splitsmith.sync.pull import RemoteDoc
from splitsmith.sync.state import SyncState

from .test_ui_server import _seed_match_export_project

AUDIT = "/api/shooters/me/stages/1/audit"


def _audit_file(project_root: Path) -> Path:
    return project_root / "shooters" / "me" / "audit" / "stage1.json"


def test_revision_is_stable_and_order_independent() -> None:
    assert audit_revision(None) == "none"
    a = audit_revision({"shots": [], "b": 1})
    assert a == audit_revision({"b": 1, "shots": []})
    assert len(a) == 16 and a != audit_revision({"shots": [], "b": 2})


def test_get_carries_version_and_put_with_stale_version_409s(tmp_path: Path) -> None:
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    loaded = client.get(AUDIT).json()
    assert loaded["_version"] == audit_revision({k: v for k, v in loaded.items() if k != "_version"})

    # A pull rewrites the file under the open editor.
    pulled = json.loads(_audit_file(project_root).read_text(encoding="utf-8"))
    pulled["shots"] = [{"shot_number": 1, "time": 2.5, "source": "manual", "id": "s-phone"}]
    _audit_file(project_root).write_text(json.dumps(pulled), encoding="utf-8")

    stale = dict(loaded)
    stale["shots"] = []
    resp = client.put(AUDIT, json=stale)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "version_conflict"
    on_disk = json.loads(_audit_file(project_root).read_text(encoding="utf-8"))
    assert on_disk["shots"][0]["id"] == "s-phone"


def test_put_with_current_version_saves_and_returns_the_new_one(tmp_path: Path) -> None:
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    loaded = client.get(AUDIT).json()
    loaded["shots"] = [{"shot_number": 1, "time": 1.5, "source": "manual"}]
    resp = client.put(AUDIT, json=loaded)
    assert resp.status_code == 200, resp.text
    saved = resp.json()
    on_disk = json.loads(_audit_file(project_root).read_text(encoding="utf-8"))
    assert "_version" not in on_disk
    assert saved["_version"] == audit_revision(on_disk)
    # The returned version is immediately usable for the next save.
    saved["shots"][0]["time"] = 1.6
    assert client.put(AUDIT, json=saved).status_code == 200


def test_put_without_version_is_last_writer_wins(tmp_path: Path) -> None:
    client, _ = _seed_match_export_project(tmp_path, stage_count=1)
    loaded = client.get(AUDIT).json()
    loaded.pop("_version", None)
    loaded["shots"] = []
    assert client.put(AUDIT, json=loaded).status_code == 200


def test_apply_pull_holds_the_audit_lock(tmp_path: Path, monkeypatch) -> None:
    """The pull's read-merge-write of an audit doc runs under the lock the
    PUT's compare-and-save holds, so neither can interleave the other."""
    import splitsmith.sync.run as run_mod

    _, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    held: list[bool] = []

    class _Probe:
        def __init__(self) -> None:
            self._lock = threading.RLock()
            self.depth = 0

        def __enter__(self):
            self._lock.acquire()
            self.depth += 1

        def __exit__(self, *exc):
            self.depth -= 1
            self._lock.release()

    probe = _Probe()
    real = run_mod.merge_audit_doc

    def spy(*a, **kw):
        held.append(probe.depth > 0)
        return real(*a, **kw)

    monkeypatch.setattr(run_mod, "merge_audit_doc", spy)
    remote = json.loads(_audit_file(project_root).read_text(encoding="utf-8"))
    rd = RemoteDoc(kind="audit", slug="me", stage_number=1, version=2, updated_at=datetime.now(UTC))
    run_mod._apply_pull(project_root, "m", SyncState(), [(rd, remote, 2)], audit_lock=probe)
    assert held == [True]
