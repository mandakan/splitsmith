"""Hosted-mode JSON storage seam (PR-delta).

Proves the bidirectional round-trip that lets a separate worker process
run match-carrying kinds: the API seeds S3 with a match's JSON, a worker
(here: a second local working root pointed at the same storage) reads it,
writes results back, and the API sees them on its next read. S3 is the
source of truth for the small JSON files, so loads pull fresh.

Uses :class:`FilesystemStorage` against a temp dir as the S3 stand-in --
it implements the full ``Storage`` protocol, so the seam exercises real
push/pull, not a mock.
"""

from __future__ import annotations

import json
from pathlib import Path

from splitsmith.match_model import Match
from splitsmith.storage import FilesystemStorage
from splitsmith.ui import server as srv
from splitsmith.ui.project import PROJECT_FILE, MatchProject
from splitsmith.ui.server import AppState, current_match_id, current_match_root


def _scaffold_match(root: Path, *, name: str = "Test Match", slug: str = "alpha") -> str:
    """Create an on-disk match with one shooter; return its match_id."""
    match = Match.init(root, name=name)
    shooter_root = Match.shooter_root(root, slug)
    shooter_root.mkdir(parents=True, exist_ok=True)
    MatchProject.init(shooter_root, name=slug)
    match.shooters.append(slug)
    match.save(root)
    assert match.match_id is not None
    return match.match_id


class _BoundMatch:
    """Context manager: set the per-request match ContextVars."""

    def __init__(self, root: Path, match_id: str) -> None:
        self._root = root
        self._match_id = match_id

    def __enter__(self) -> None:
        self._rt = current_match_root.set(self._root)
        self._it = current_match_id.set(self._match_id)

    def __exit__(self, *exc: object) -> None:
        current_match_id.reset(self._it)
        current_match_root.reset(self._rt)


def test_save_pushes_project_json_when_storage_bound(tmp_path: Path) -> None:
    storage_root = tmp_path / "s3"
    storage = FilesystemStorage(storage_root)
    shooter_root = tmp_path / "local" / "shooters" / "alpha"
    proj = MatchProject.init(shooter_root, name="alpha")
    proj.bind_storage(storage, scope="matches/m1/shooters/alpha")

    proj.competitor_name = "Pushed"
    proj.save(shooter_root)

    key = storage_root / "matches" / "m1" / "shooters" / "alpha" / PROJECT_FILE
    assert key.exists()
    assert json.loads(key.read_text())["competitor_name"] == "Pushed"


def test_save_is_noop_in_local_mode(tmp_path: Path) -> None:
    """No storage bound (desktop) -> save just writes locally, no error."""
    shooter_root = tmp_path / "shooters" / "alpha"
    proj = MatchProject.init(shooter_root, name="alpha")
    proj.competitor_name = "Local"
    proj.save(shooter_root)  # _storage is None -> push branch skipped
    assert json.loads((shooter_root / PROJECT_FILE).read_text())["competitor_name"] == "Local"


def test_project_json_round_trips_api_worker_api(tmp_path: Path) -> None:
    """API write -> worker read+write -> API re-read sees the worker's
    value (always-fresh load beats the API's stale local copy)."""
    storage = FilesystemStorage(tmp_path / "s3")
    state = AppState()
    state.storage = storage

    api_root = tmp_path / "api"
    match_id = _scaffold_match(api_root)
    # API "opens" the match: seed S3 with match.json + project.json.
    srv._sync_match_json_to_storage(state, api_root, Match.load(api_root))

    with _BoundMatch(api_root, match_id):
        proj = state.shooter_project("alpha")
        proj.competitor_name = "ApiWritten"
        proj.save(state.shooter_root("alpha"))

    # Worker: a fresh, empty working root pointed at the same storage.
    worker_root = tmp_path / "worker"
    worker_root.mkdir()
    with _BoundMatch(worker_root, match_id):
        wproj = state.shooter_project("alpha")  # pulls match.json + project.json down
        assert wproj.competitor_name == "ApiWritten"
        wproj.competitor_name = "WorkerWritten"
        wproj.save(state.shooter_root("alpha"))  # pushes the worker's result

    # API re-reads: its local copy still says "ApiWritten", but the load
    # pulls fresh from storage and sees the worker's write.
    assert (
        json.loads((api_root / "shooters" / "alpha" / PROJECT_FILE).read_text())["competitor_name"]
        == "ApiWritten"
    )
    with _BoundMatch(api_root, match_id):
        reread = state.shooter_project("alpha")
        assert reread.competitor_name == "WorkerWritten"


def test_audit_json_round_trips(tmp_path: Path) -> None:
    """shot_detect's result (audit JSON) written on the worker is visible
    to the API via push_audit / pull_audit."""
    storage = FilesystemStorage(tmp_path / "s3")
    state = AppState()
    state.storage = storage

    api_root = tmp_path / "api"
    match_id = _scaffold_match(api_root)

    worker_root = tmp_path / "worker"
    with _BoundMatch(worker_root, match_id):
        audit_dir = Match.shooter_root(worker_root, "alpha") / "audit"
        audit_dir.mkdir(parents=True)
        (audit_dir / "stage1.json").write_text('{"shots": [0.21, 0.55]}')
        state.push_audit("alpha", 1)

    with _BoundMatch(api_root, match_id):
        state.pull_audit("alpha", 1)
        api_audit = Match.shooter_root(api_root, "alpha") / "audit" / "stage1.json"
        assert json.loads(api_audit.read_text())["shots"] == [0.21, 0.55]


def test_seam_is_noop_without_storage(tmp_path: Path) -> None:
    """Local mode (storage None): pull/push audit + sync are no-ops, no error."""
    state = AppState()  # storage stays None
    api_root = tmp_path / "api"
    match_id = _scaffold_match(api_root)
    srv._sync_match_json_to_storage(state, api_root, Match.load(api_root))  # no-op
    with _BoundMatch(api_root, match_id):
        state.pull_audit("alpha", 1)  # no-op
        state.push_audit("alpha", 1)  # no-op
