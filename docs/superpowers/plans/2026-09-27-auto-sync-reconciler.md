# Auto-sync and the Desktop Reconciler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A desktop that is running Splitsmith picks up review actions made on hosted (phone), runs trim and shot detection locally, and pushes the results back without a click.

**Architecture:** Hosted gains one read route (`/api/sync/fingerprints`). The local server gains an asyncio auto-sync service (pure scheduling core in `sync/auto.py`, FastAPI driver in `ui/auto_sync.py`) that polls fingerprints, tracks local dirtiness through an HTTP middleware and a job-terminal listener, and submits an `auto_sync` job. Every sync (manual or automatic) ends with a pure reconciler (`sync/reconcile.py`) that derives missing trim / shot_detect steps from state and submits them. Audit saves gain a `_version` revision so a pull cannot be overwritten by an open editor.

**Tech Stack:** Python 3.11, FastAPI, pydantic, httpx, SQLAlchemy (hosted), pytest (+xdist), React + TypeScript + vitest (SPA under `src/splitsmith/ui_static`).

**Spec:** `docs/superpowers/specs/2026-09-27-auto-sync-reconciler-design.md`

## Global Constraints

- `uv` only; never `pip`. Black line length 110, ruff clean. Type hints everywhere, `pathlib.Path` for paths.
- No new dependencies (Python or npm).
- Nothing under the local `create_app` path may import `splitsmith.db` at module level (`scripts/ci/assert_slim_import_surface.py`).
- No new `state_docs` kind; no change to `PULLABLE_DOC_KINDS`.
- Automatic pushes keep today's full media policy (full trims + `_web.mp4`). Web-only is v1.1, not here.
- UI: primitives from `components/ui` only; `Segmented` for the closed choice; no issue numbers or flavour copy in UI strings; errors as outline + text.
- Prose and UI copy: ASCII punctuation, no dashes as punctuation.
- Timings: poll 60 s active, 300 s after 1800 s idle; push quiet period 45 s; backoff 60 s doubling to 900 s.
- Tests: targeted files with `-n0` while iterating (host Python 3.14 segfaults on the full run on this Mac); every new test must fail against the pre-change code.

## Review Focus

- An Audit page left open while a pull lands: the next save must 409 and reload, never overwrite. (Task 4 pins the 409; Task 10 pins the SPA reload.)
- A synced match whose hosted docs were deleted: the fingerprint never matches again, and auto-sync must not re-sync every minute. (Task 6 `test_settled_fingerprint_does_not_retrigger`.)
- Two matches with the same shooter slug and stage number: the reconciler in match A must not be deduped by a running job in match B. (Task 1.)
- A clip whose trim fails every time: the reconciler must not resubmit it on every poll. (Task 5 failure memo, Task 7 `test_failed_reconcile_step_is_recorded_and_skipped`.)
- The user toggles auto-sync off while an auto sync is running: the toggle must survive the run. (Task 3 file split; Task 8 `test_toggle_survives_a_running_sync`.)

---

### Task 1: Scope `find_active` to the calling match

**Files:**
- Modify: `src/splitsmith/ui/jobs.py` (`JobRegistry.find_active`, ~line 760)
- Test: `tests/test_jobs.py`

**Interfaces:**
- Produces: `JobRegistry.find_active(...)` also filters on `Job.match_id == current_match_id.get()` when that ContextVar is set. Signature unchanged.

- [ ] **Step 1: Write the failing test** (append to `tests/test_jobs.py`, next to `test_find_active_scopes_by_shooter_slug`)

```python
def test_find_active_scopes_by_calling_match() -> None:
    """A shooter slug repeats across matches (the owner is in every one),
    so a lookup made in match A's context must not adopt match B's job.
    Auto-sync processes background matches, which makes this reachable."""
    from splitsmith.ui.server import current_match_id

    reg = _Sync(JobRegistry(max_concurrent=1))
    release = threading.Event()

    def work(_handle):
        release.wait(timeout=5.0)

    token = current_match_id.set("match-b")
    try:
        job_b = reg.submit(kind="trim", fn=work, stage_number=3, shooter_slug="me", video_id="v1")
    finally:
        current_match_id.reset(token)
    try:
        assert job_b.match_id == "match-b"
        token = current_match_id.set("match-a")
        try:
            assert reg.find_active(kind="trim", stage_number=3, shooter_slug="me", video_id="v1") is None
        finally:
            current_match_id.reset(token)
        token = current_match_id.set("match-b")
        try:
            found = reg.find_active(kind="trim", stage_number=3, shooter_slug="me", video_id="v1")
            assert found is not None and found.id == job_b.id
        finally:
            current_match_id.reset(token)
        # No match context: today's behaviour, match-agnostic.
        assert reg.find_active(kind="trim", stage_number=3, shooter_slug="me", video_id="v1") is not None
    finally:
        release.set()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_jobs.py::test_find_active_scopes_by_calling_match -n0 -q`
Expected: FAIL at the `is None` assertion (match B's job is adopted).

- [ ] **Step 3: Implement**

In `find_active`, before `with self._lock:`:

```python
        # In-method import: server.py imports this module (see submit()).
        from .server import current_match_id

        calling_match = current_match_id.get()
```

and inside the loop after the slug check:

```python
                if calling_match is not None and j.match_id != calling_match:
                    continue
```

Add one sentence to the docstring: "``current_match_id``, when set, scopes the lookup to that match: shooter slugs repeat across matches."

- [ ] **Step 4: Run the jobs tests**

Run: `uv run pytest tests/test_jobs.py tests/test_take_detect_job.py tests/test_generate_proxy_job.py -n0 -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/jobs.py tests/test_jobs.py
git commit -m "fix(jobs): scope find_active to the calling match"
```

---

### Task 2: Hosted fingerprint route and client method

**Files:**
- Modify: `src/splitsmith/db/project_state.py` (new method after `list_doc_meta`)
- Modify: `src/splitsmith/ui/sync_api.py` (models + route after `get_doc_manifest`)
- Modify: `src/splitsmith/sync/client.py` (new method after `get_doc_manifest`)
- Test: `tests/test_sync_api.py`

**Interfaces:**
- Produces: `ProjectStateStore.list_fingerprints(kinds: Collection[str]) -> list[tuple[str, int, int]]` as `(match_id, doc_count, version_sum)`.
- Produces: `GET /api/sync/fingerprints` -> `{"matches": [{"match_id": str, "doc_count": int, "version_sum": int}]}`.
- Produces: `HostedSyncClient.get_fingerprints() -> dict[str, tuple[int, int]]`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_sync_api.py`)

```python
FINGERPRINTS_URL = "/api/sync/fingerprints"


def test_fingerprints_move_with_a_put_and_ignore_export_runs(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The desktop wants a pull exactly when (doc_count, version_sum) over
    the pullable kinds differs from its own doc_versions. An export_runs
    write must not move it: the desktop would wake into a pull that
    finds nothing."""
    import asyncio

    from sqlalchemy import select as _select

    from splitsmith.db import ProjectStateStore, create_engine, sessionmaker
    from splitsmith.db.models import User

    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    assert client.post(CREATE_URL, json={"match_id": "m1", "name": "Match 1"}).status_code == 200
    project_doc = MatchProject(name="Anna").model_dump(mode="json")
    assert _put_doc(client, "m1", "project/anna", body=project_doc, expected_version=0).status_code == 200

    first = client.get(FINGERPRINTS_URL)
    assert first.status_code == 200, first.text
    assert first.json() == {"matches": [{"match_id": "m1", "doc_count": 1, "version_sum": 1}]}

    assert _put_doc(client, "m1", "project/anna", body=project_doc, expected_version=1).status_code == 200
    assert client.get(FINGERPRINTS_URL).json()["matches"] == [
        {"match_id": "m1", "doc_count": 1, "version_sum": 2}
    ]

    engine = create_engine(_db_url_for(client))
    sf = sessionmaker(engine)

    async def _seed_export_runs() -> None:
        async with sf() as s:
            user_id = (
                (await s.execute(_select(User).where(User.email == "owner@example.com"))).scalar_one().id
            )
        store = ProjectStateStore(sf, user_id=user_id)
        await store.save_export_runs("m1", "anna", {"schema_version": 1, "runs": []}, expected_version=0)

    asyncio.run(_seed_export_runs())
    assert client.get(FINGERPRINTS_URL).json()["matches"] == [
        {"match_id": "m1", "doc_count": 1, "version_sum": 2}
    ]


def test_fingerprints_are_per_user(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    assert client.post(CREATE_URL, json={"match_id": "m1", "name": "Match 1"}).status_code == 200
    body = MatchProject(name="Anna").model_dump(mode="json")
    assert _put_doc(client, "m1", "project/anna", body=body, expected_version=0).status_code == 200
    client.cookies.clear()
    login(client, sender, "other@example.com")
    assert client.get(FINGERPRINTS_URL).json() == {"matches": []}


def test_fingerprints_route_404s_locally() -> None:
    from splitsmith.ui.server import create_app

    with TestClient(create_app(), follow_redirects=False) as client:
        assert client.get(FINGERPRINTS_URL).status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_sync_api.py -k fingerprints -n0 -q`
Expected: FAIL (404 / 405 on the route).

- [ ] **Step 3: Implement the store method** (`db/project_state.py`, after `list_doc_meta`; add `func` to the `sqlalchemy` import and `from collections.abc import Collection`)

```python
    async def list_fingerprints(self, kinds: Collection[str]) -> list[tuple[str, int, int]]:
        """``(match_id, doc_count, version_sum)`` per match, over ``kinds``.

        The auto-sync change signal: ``version`` only increases, so any
        write moves the sum and any insert moves the count. One grouped
        query for all of the user's matches, no doc payloads.
        """
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        StateDocRow.match_id,
                        func.count(StateDocRow.id),
                        func.coalesce(func.sum(StateDocRow.version), 0),
                    )
                    .where(
                        StateDocRow.user_id == self._user_id,
                        StateDocRow.doc_kind.in_(list(kinds)),
                    )
                    .group_by(StateDocRow.match_id)
                    .order_by(StateDocRow.match_id)
                )
            ).all()
        return [(str(m), int(c), int(v)) for m, c, v in rows]
```

- [ ] **Step 4: Implement the route** (`ui/sync_api.py`; models next to `SyncDocManifestResponse`, route after `get_doc_manifest`)

```python
class SyncFingerprint(BaseModel):
    match_id: str
    doc_count: int
    version_sum: int


class SyncFingerprintsResponse(BaseModel):
    """Per-match change signal for desktop auto-sync (spec 2026-09-27)."""

    matches: list[SyncFingerprint]


@router.get("/fingerprints", response_model=SyncFingerprintsResponse)
async def get_fingerprints(
    request: Request,
    user: Any = Depends(_current_user),
) -> SyncFingerprintsResponse:
    """``(doc_count, version_sum)`` over the pullable kinds of every match.

    The desktop compares this with the same pair computed from its own
    ``sync_state.doc_versions`` and pulls when they differ. Filtered to
    ``PULLABLE_DOC_KINDS`` for the reason ``get_doc_manifest`` is: a kind
    the desktop never pulls must not wake it.
    """
    _hosted_gate()
    rows = await _project_state(request).list_fingerprints(PULLABLE_DOC_KINDS)
    return SyncFingerprintsResponse(
        matches=[SyncFingerprint(match_id=m, doc_count=c, version_sum=v) for m, c, v in rows]
    )
```

- [ ] **Step 5: Implement the client method** (`sync/client.py`, after `get_doc_manifest`)

```python
    def get_fingerprints(self) -> dict[str, tuple[int, int]]:
        """``match_id -> (doc_count, version_sum)`` for every hosted match."""
        resp = self._http.get("/api/sync/fingerprints")
        self._raise_for_status(resp)
        return {
            row["match_id"]: (int(row["doc_count"]), int(row["version_sum"]))
            for row in resp.json()["matches"]
        }
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_sync_api.py -n0 -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/db/project_state.py src/splitsmith/ui/sync_api.py src/splitsmith/sync/client.py tests/test_sync_api.py
git commit -m "feat(sync): fingerprint route for desktop auto-sync"
```

---

### Task 3: Local auto-sync state file and global switch

**Files:**
- Create: `src/splitsmith/sync/auto_state.py`
- Modify: `src/splitsmith/user_config.py` (`GlobalPrefs`)
- Modify: `src/splitsmith/sync/state.py` (add `local_fingerprint`)
- Test: `tests/test_sync_auto_state.py`

**Interfaces:**
- Produces: `AUTO_SYNC_FILE = "auto_sync.json"`; `AutoRunSummary(at: datetime, ok: bool, message: str, conflicts: int = 0, notes: int = 0)`; `AutoSyncPrefs(enabled: bool | None = None, reconcile_failures: dict[str, str] = {}, last_auto: AutoRunSummary | None = None)`; `load_auto_prefs(match_root: Path) -> AutoSyncPrefs`; `update_auto_prefs(match_root: Path, fn: Callable[[AutoSyncPrefs], None]) -> AutoSyncPrefs`; `auto_sync_effective(prefs: AutoSyncPrefs, sync_state: SyncState, *, global_enabled: bool) -> bool`.
- Produces: `local_fingerprint(sync_state: SyncState) -> tuple[int, int]`.
- Produces: `GlobalPrefs.auto_sync_enabled: bool = True`.

- [ ] **Step 1: Write the failing tests** (`tests/test_sync_auto_state.py`)

```python
"""Local auto-sync state (spec 2026-09-27 s6)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from splitsmith.sync.auto_state import (
    AUTO_SYNC_FILE,
    AutoSyncPrefs,
    auto_sync_effective,
    load_auto_prefs,
    update_auto_prefs,
)
from splitsmith.sync.state import SyncState, local_fingerprint, save_sync_state


def test_missing_or_corrupt_file_loads_defaults(tmp_path: Path) -> None:
    assert load_auto_prefs(tmp_path) == AutoSyncPrefs()
    (tmp_path / AUTO_SYNC_FILE).write_text("{not json", encoding="utf-8")
    assert load_auto_prefs(tmp_path) == AutoSyncPrefs()


def test_update_round_trips(tmp_path: Path) -> None:
    update_auto_prefs(tmp_path, lambda p: setattr(p, "enabled", False))
    update_auto_prefs(tmp_path, lambda p: p.reconcile_failures.update({"trim/me/1/v1": "12.3400"}))
    loaded = load_auto_prefs(tmp_path)
    assert loaded.enabled is False
    assert loaded.reconcile_failures == {"trim/me/1/v1": "12.3400"}


def test_default_is_on_once_synced_and_global_switch_wins() -> None:
    never = SyncState()
    synced = SyncState(last_synced_at=datetime(2026, 9, 27, tzinfo=UTC))
    assert auto_sync_effective(AutoSyncPrefs(), never, global_enabled=True) is False
    assert auto_sync_effective(AutoSyncPrefs(), synced, global_enabled=True) is True
    assert auto_sync_effective(AutoSyncPrefs(enabled=False), synced, global_enabled=True) is False
    assert auto_sync_effective(AutoSyncPrefs(), synced, global_enabled=False) is False
    # An explicit on still needs a first manual sync: there is no mirror yet.
    assert auto_sync_effective(AutoSyncPrefs(enabled=True), never, global_enabled=True) is False


def test_toggle_is_not_clobbered_by_sync_state_saves(tmp_path: Path) -> None:
    """run_sync saves sync_state.json from its in-memory copy many times per
    run. The auto flag lives in its own file so those saves cannot undo it."""
    update_auto_prefs(tmp_path, lambda p: setattr(p, "enabled", False))
    save_sync_state(tmp_path, SyncState(last_synced_at=datetime(2026, 9, 27, tzinfo=UTC)))
    assert load_auto_prefs(tmp_path).enabled is False


def test_local_fingerprint_counts_and_sums_doc_versions() -> None:
    state = SyncState(doc_versions={"match": 3, "project/me": 2, "audit/me/1": 5})
    assert local_fingerprint(state) == (3, 10)
    assert local_fingerprint(SyncState()) == (0, 0)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_sync_auto_state.py -n0 -q`
Expected: FAIL with `ModuleNotFoundError: splitsmith.sync.auto_state`.

- [ ] **Step 3: Implement `sync/auto_state.py`**

```python
"""Per-match local auto-sync state (spec 2026-09-27 s6).

Lives in ``<match-root>/auto_sync.json``, not in ``sync_state.json``:
``run_sync`` saves that file repeatedly from its in-memory copy, so a
toggle made during a sync would be overwritten. Root-level files are
never in the push plan, so nothing here syncs. One writer helper
load-modify-saves under a module lock; every writer lives in the local
server process.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from ..match_project import atomic_write_json
from .state import SyncState

AUTO_SYNC_FILE = "auto_sync.json"

_lock = threading.Lock()


class AutoRunSummary(BaseModel):
    """The last automatic run, for the SyncCard."""

    at: datetime
    ok: bool
    message: str
    conflicts: int = 0
    notes: int = 0


class AutoSyncPrefs(BaseModel):
    #: ``None`` = default: on once the match has been synced.
    enabled: bool | None = None
    #: reconcile step key -> input key the step last failed with.
    reconcile_failures: dict[str, str] = Field(default_factory=dict)
    last_auto: AutoRunSummary | None = None


def load_auto_prefs(match_root: Path) -> AutoSyncPrefs:
    """Missing or unreadable file -> defaults, never an error."""
    path = match_root / AUTO_SYNC_FILE
    try:
        return AutoSyncPrefs.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError):
        return AutoSyncPrefs()


def update_auto_prefs(match_root: Path, fn: Callable[[AutoSyncPrefs], None]) -> AutoSyncPrefs:
    """Load, apply ``fn`` in place, save atomically; returns the saved prefs."""
    with _lock:
        prefs = load_auto_prefs(match_root)
        fn(prefs)
        atomic_write_json(match_root / AUTO_SYNC_FILE, prefs.model_dump(mode="json"))
        return prefs


def auto_sync_effective(prefs: AutoSyncPrefs, sync_state: SyncState, *, global_enabled: bool) -> bool:
    """Whether the service should watch this match. Needs a first sync
    (there is no hosted mirror to watch before it), the global switch,
    and the per-match flag unless that is left at its default."""
    if not global_enabled or sync_state.last_synced_at is None:
        return False
    return prefs.enabled is not False
```

- [ ] **Step 4: Add `local_fingerprint`** (`sync/state.py`, after `save_sync_state`)

```python
def local_fingerprint(state: SyncState) -> tuple[int, int]:
    """``(doc_count, version_sum)`` over ``doc_versions``: the desktop half
    of the auto-sync change signal. Its keys are exactly the pullable doc
    identities, which is what ``GET /api/sync/fingerprints`` counts."""
    return len(state.doc_versions), sum(state.doc_versions.values())
```

- [ ] **Step 5: Add the global switch** (`user_config.py`, in `GlobalPrefs` after `hosted_account`)

```python
    # Desktop auto-sync (spec 2026-09-27). One machine-level off switch;
    # the per-match flag lives in the match's auto_sync.json.
    auto_sync_enabled: bool = True
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_sync_auto_state.py tests/test_sync_state_v2.py -n0 -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/sync/auto_state.py src/splitsmith/sync/state.py src/splitsmith/user_config.py tests/test_sync_auto_state.py
git commit -m "feat(sync): local auto-sync state file and global switch"
```

---

### Task 4: Audit revisions on GET/PUT and the pull apply lock

**Files:**
- Create: `src/splitsmith/audit_revision.py`
- Modify: `src/splitsmith/ui/server.py` (`get_stage_audit` ~11742, `put_stage_audit` ~11765, exception handler next to `_state_conflict_handler` ~7569, `_run_sync_match` ~4368)
- Modify: `src/splitsmith/sync/run.py` (`run_sync`, `_apply_pull`)
- Test: `tests/test_audit_revision.py`

**Interfaces:**
- Produces: `audit_revision(doc: dict | None) -> str` ("none" for no doc, else 16 hex chars); `REVISION_FIELD = "_version"`; `class AuditRevisionConflict(Exception)`.
- Produces: `run_sync(..., audit_lock: AbstractContextManager | None = None)`.

- [ ] **Step 1: Write the failing tests** (`tests/test_audit_revision.py`)

```python
"""Audit revisions (spec 2026-09-27 s5): a stale editor gets 409, never
overwrites a doc a pull wrote under it."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from splitsmith.audit_revision import audit_revision
from splitsmith.sync.run import _apply_pull

from .test_ui_server import _seed_match_export_project

AUDIT = "/api/shooters/me/stages/1/audit"


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
    audit_file = project_root / "shooters" / "me" / "audit" / "stage1.json"
    pulled = json.loads(audit_file.read_text(encoding="utf-8"))
    pulled["shots"] = [{"shot_number": 1, "time": 2.5, "source": "manual", "id": "s-phone"}]
    audit_file.write_text(json.dumps(pulled), encoding="utf-8")

    stale = dict(loaded)
    stale["shots"] = []
    resp = client.put(AUDIT, json=stale)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "version_conflict"
    on_disk = json.loads(audit_file.read_text(encoding="utf-8"))
    assert on_disk["shots"][0]["id"] == "s-phone"


def test_put_with_current_version_saves_and_returns_the_new_one(tmp_path: Path) -> None:
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    loaded = client.get(AUDIT).json()
    loaded["shots"] = [{"shot_number": 1, "time": 1.5, "source": "manual"}]
    resp = client.put(AUDIT, json=loaded)
    assert resp.status_code == 200, resp.text
    saved = resp.json()
    audit_file = project_root / "shooters" / "me" / "audit" / "stage1.json"
    on_disk = json.loads(audit_file.read_text(encoding="utf-8"))
    assert "_version" not in on_disk
    assert saved["_version"] == audit_revision(on_disk)
    # The returned version is immediately usable for the next save.
    saved["shots"][0]["time"] = 1.6
    assert client.put(AUDIT, json=saved).status_code == 200


def test_put_without_version_is_last_writer_wins(tmp_path: Path) -> None:
    client, _ = _seed_match_export_project(tmp_path, stage_count=1)
    loaded = client.get(AUDIT).json()
    loaded.pop("_version")
    loaded["shots"] = []
    assert client.put(AUDIT, json=loaded).status_code == 200


def test_apply_pull_holds_the_audit_lock(tmp_path: Path, monkeypatch) -> None:
    """The pull's read-merge-write of an audit doc runs under the lock the
    PUT's compare-and-save holds, so neither can interleave the other."""
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
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
    import splitsmith.sync.run as run_mod

    real = run_mod.merge_audit_doc

    def spy(*a, **kw):
        held.append(probe.depth > 0)
        return real(*a, **kw)

    monkeypatch.setattr(run_mod, "merge_audit_doc", spy)
    from splitsmith.sync.pull import RemoteDoc
    from splitsmith.sync.state import SyncState

    audit_file = project_root / "shooters" / "me" / "audit" / "stage1.json"
    remote = json.loads(audit_file.read_text(encoding="utf-8"))
    rd = RemoteDoc(kind="audit", slug="me", stage_number=1, version=2, updated_at=None)
    _apply_pull(project_root, "m", SyncState(), [(rd, remote, 2)], audit_lock=probe)
    assert held == [True]
```

Before writing Step 3, read `sync/pull.py` for `RemoteDoc`'s real constructor fields and fix the `rd = RemoteDoc(...)` line to match (the dataclass is ~15 lines; the test must construct it the way `plan_pull` does).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_audit_revision.py -n0 -q`
Expected: FAIL (`ModuleNotFoundError: splitsmith.audit_revision`).

- [ ] **Step 3: Implement `audit_revision.py`**

```python
"""Audit document revisions (spec 2026-09-27 s5).

A content hash, identical in local and hosted mode, that rides the audit
GET/PUT as ``_version`` so a save made from a stale copy is refused
instead of overwriting whatever a sync pull (or another tab) wrote.
Deliberately outside ``splitsmith.db``: the slim local install raises and
maps it (#1057 import-surface rule).
"""

from __future__ import annotations

import hashlib
import json

REVISION_FIELD = "_version"


class AuditRevisionConflict(Exception):
    """A PUT carried a ``_version`` that no longer matches the stored doc."""


def audit_revision(doc: dict | None) -> str:
    if doc is None:
        return "none"
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
```

- [ ] **Step 4: Wire the routes** (`ui/server.py`)

In `get_stage_audit`, replace the final `return JSONResponse(payload)` with:

```python
        return JSONResponse({**payload, REVISION_FIELD: audit_revision(payload)})
```

In `put_stage_audit`, first statement after the stage lookup:

```python
        client_revision = payload.pop(REVISION_FIELD, None)
```

Then wrap everything from `stored, version = state.load_audit(slug, stage_number)` through the `state.save_audit(slug, stage_number, payload, version=version)` call in `with state.audit_lock:` and insert right after the load:

```python
            if client_revision is not None and client_revision != audit_revision(stored):
                raise AuditRevisionConflict(f"stage {stage_number} audit changed since it was loaded")
```

Make the route's return value carry `REVISION_FIELD: audit_revision(payload)` (the dict that was saved) the same way GET does. Update the route comment that says "The SPA PUT doesn't carry a version (it assumes last-writer-wins)" to describe `_version`.

Imports at the top of `server.py` (with the other local imports):

```python
from ..audit_revision import REVISION_FIELD, AuditRevisionConflict, audit_revision
```

Register the handler unconditionally, right before the `try: from ..db import StateConflictError` block:

```python
    @app.exception_handler(AuditRevisionConflict)
    async def _audit_revision_conflict_handler(request: Request, exc: Exception) -> JSONResponse:
        """Same body as the hosted optimistic-lock 409, so every client
        handles both with one branch."""
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "code": "version_conflict",
                    "message": "this match state changed since you loaded it; reload and try again",
                }
            },
        )
```

- [ ] **Step 5: Lock the pull apply** (`sync/run.py`)

Add `audit_lock: AbstractContextManager | None = None` (import `from contextlib import AbstractContextManager, nullcontext`) to `run_sync`'s keyword arguments and to `_apply_pull(match_root, match_id, sync_state, pulled, *, audit_lock=None)`; `run_sync` passes it through. In `_apply_pull`, open `lock = audit_lock if audit_lock is not None else nullcontext()` once, and wrap the whole `else:  # audit` branch body in `with lock:`.

In `ui/server.py` `_run_sync_match`, pass `audit_lock=state.audit_lock` to `run_bidirectional_sync(...)`.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_audit_revision.py tests/test_audit_local_save.py tests/test_audit_event_ids.py tests/test_audit.py tests/test_sync_integration.py tests/test_mirror_read_only.py tests/test_triage_api.py -n0 -q`
Expected: PASS. If an existing test compares a GET audit body for equality with a saved dict, pop `_version` from the GET body in that test (the field is response-only by design); do not remove the field.

- [ ] **Step 7: Verify the slim import surface**

Run: `uv run python scripts/ci/assert_slim_import_surface.py`
Expected: exit 0.

- [ ] **Step 8: Commit**

```bash
git add src/splitsmith/audit_revision.py src/splitsmith/ui/server.py src/splitsmith/sync/run.py tests/test_audit_revision.py tests/
git commit -m "feat(audit): revision token on audit saves; pull apply takes the audit lock"
```

---

### Task 5: The pure reconciler

**Files:**
- Create: `src/splitsmith/sync/reconcile.py`
- Test: `tests/test_sync_reconcile.py`

**Interfaces:**
- Produces:

```python
StepKind = Literal["trim", "shot_detect"]

class ReconcileStep(BaseModel):
    kind: StepKind
    slug: str
    stage_number: int
    video_id: str
    input_key: str

    @property
    def key(self) -> str: ...  # f"{kind}/{slug}/{stage_number}/{video_id}"

def video_step(stage: StageEntry, video: StageVideo, audit_doc: dict | None, *,
               detect_enabled: bool = True, explicit: bool = False) -> StepKind | None
def plan_reconcile(projects: Mapping[str, MatchProject], audits: Mapping[str, Mapping[int, dict]],
                   failures: Mapping[str, str]) -> list[ReconcileStep]
def load_reconcile_inputs(match_root: Path) -> tuple[dict[str, MatchProject], dict[str, dict[int, dict]]]
```

- [ ] **Step 1: Write the failing tests** (`tests/test_sync_reconcile.py`)

```python
"""Reconciler (spec 2026-09-27 s3): derive missing pipeline steps from state."""

from __future__ import annotations

from splitsmith.match_project import STUB_AUDIT_DETECTION, MatchProject, StageEntry, StageVideo
from splitsmith.sync.reconcile import ReconcileStep, plan_reconcile, video_step


def _video(**kw) -> StageVideo:
    base = dict(path="raw/a.mp4", role="primary", beep_time=12.34, beep_reviewed=True)
    base.update(kw)
    return StageVideo(**base)


def _stage(video: StageVideo, time_seconds: float = 20.0) -> StageEntry:
    return StageEntry(stage_number=1, stage_name="S1", time_seconds=time_seconds, videos=[video])


def test_confirm_only_on_an_untrimmed_stage_wants_a_trim() -> None:
    """The phone's most common action: confirm the detected beep as-is.
    The merge flags nothing for it; the reconciler must still act."""
    v = _video(processed={"beep": True, "trim": False})
    assert video_step(_stage(v), v, None) == "trim"


def test_unreviewed_or_timeless_stage_wants_nothing() -> None:
    v = _video(beep_reviewed=False, processed={"trim": False})
    assert video_step(_stage(v), v, None) is None
    v = _video(processed={"trim": False})
    assert video_step(_stage(v, time_seconds=0.0), v, None) is None


def test_trimmed_primary_without_detection_wants_detect() -> None:
    v = _video(processed={"trim": True, "shot_detect": False})
    assert video_step(_stage(v), v, None) == "shot_detect"
    stub = {"shots": [], "detection": STUB_AUDIT_DETECTION}
    assert video_step(_stage(v), v, stub) == "shot_detect"


def test_never_detects_over_real_audit_content() -> None:
    v = _video(processed={"trim": True, "shot_detect": False})
    worked = {"shots": [{"time": 1.0}], "audit_events": [{"kind": "save"}]}
    assert video_step(_stage(v), v, worked) is None


def test_explicit_confirm_always_detects_a_trimmed_primary() -> None:
    """_after_beep_reviewed semantics: a local confirm re-runs detection."""
    v = _video(processed={"trim": True, "shot_detect": True})
    assert video_step(_stage(v), v, {"shots": [{"time": 1.0}]}, explicit=True) == "shot_detect"


def test_detect_honours_the_automation_gate_and_secondaries_never_detect() -> None:
    v = _video(processed={"trim": True, "shot_detect": False})
    assert video_step(_stage(v), v, None, detect_enabled=False) is None
    s = _video(role="secondary", processed={"trim": True})
    assert video_step(_stage(s), s, None) is None


def test_plan_skips_a_step_that_failed_with_the_same_inputs() -> None:
    v = _video(processed={"trim": False})
    project = MatchProject(name="Me", stages=[_stage(v)])
    steps = plan_reconcile({"me": project}, {"me": {}}, {})
    assert steps == [
        ReconcileStep(kind="trim", slug="me", stage_number=1, video_id=v.video_id, input_key="12.3400")
    ]
    assert plan_reconcile({"me": project}, {"me": {}}, {steps[0].key: "12.3400"}) == []
    # A moved beep is a new input: try again.
    assert len(plan_reconcile({"me": project}, {"me": {}}, {steps[0].key: "11.0000"})) == 1
```

Before Step 3, check `StageVideo`'s required fields and how `video_id` is derived (`match_project.py` ~line 280); adjust `_video` so it constructs (e.g. add `added_at` or whatever is required) without changing what the tests assert.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_sync_reconcile.py -n0 -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `sync/reconcile.py`**

```python
"""Derive the pipeline steps a match is missing (spec 2026-09-27 s3).

Pure: reads projects and audit docs the caller loaded, returns steps,
submits nothing. Driven by the ``processed`` flags the merge already
maintains (a pulled beep_time change clears trim and, on a primary,
shot_detect), so a phone-side confirm and a desktop-side confirm leave
the same state behind. ``_after_beep_reviewed`` evaluates the same
``video_step`` with ``explicit=True``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .. import automation
from ..match_model import load_match_or_legacy
from ..match_project import MatchProject, StageEntry, StageVideo, is_stub_audit
from .plan import AUDIT_FILENAME_RE

StepKind = Literal["trim", "shot_detect"]


class ReconcileStep(BaseModel):
    kind: StepKind
    slug: str
    stage_number: int
    video_id: str
    #: The inputs the step runs with; a recorded failure is skipped only
    #: while these are unchanged.
    input_key: str

    @property
    def key(self) -> str:
        return f"{self.kind}/{self.slug}/{self.stage_number}/{self.video_id}"


def video_step(
    stage: StageEntry,
    video: StageVideo,
    audit_doc: dict | None,
    *,
    detect_enabled: bool = True,
    explicit: bool = False,
) -> StepKind | None:
    """The next step for one video, or None.

    ``explicit`` is a confirm the user just made on this machine: a
    trimmed primary re-runs detection even over existing results, as the
    beep-review endpoint always has. Without it (a reconcile pass) the
    step never detects over an audit doc with real content, and honours
    the project's ``shot_detect_on_beep_verified`` gate.
    """
    if not video.beep_reviewed or video.beep_time is None:
        return None
    if not video.processed.get("trim"):
        return "trim" if stage.time_seconds > 0 else None
    if video.role != "primary":
        return None
    if explicit:
        return "shot_detect"
    if not detect_enabled or video.processed.get("shot_detect"):
        return None
    if audit_doc is not None and not is_stub_audit(audit_doc):
        return None
    return "shot_detect"


def plan_reconcile(
    projects: Mapping[str, MatchProject],
    audits: Mapping[str, Mapping[int, dict]],
    failures: Mapping[str, str],
) -> list[ReconcileStep]:
    steps: list[ReconcileStep] = []
    for slug in sorted(projects):
        project = projects[slug]
        detect_enabled = automation.resolve_automation(
            project_override=project.automation
        ).settings.shot_detect_on_beep_verified
        for stage in project.stages:
            audit_doc = audits.get(slug, {}).get(stage.stage_number)
            for video in stage.videos:
                kind = video_step(stage, video, audit_doc, detect_enabled=detect_enabled)
                if kind is None:
                    continue
                step = ReconcileStep(
                    kind=kind,
                    slug=slug,
                    stage_number=stage.stage_number,
                    video_id=video.video_id,
                    input_key=f"{video.beep_time:.4f}",
                )
                if failures.get(step.key) == step.input_key:
                    continue
                steps.append(step)
    return steps


def load_reconcile_inputs(
    match_root: Path,
) -> tuple[dict[str, MatchProject], dict[str, dict[int, dict]]]:
    """Read every shooter's project and audit docs. An unreadable audit
    file is left out, which reads as "no audit" and at worst queues a
    detection the job itself will refuse to clobber."""
    match, shooter_roots = load_match_or_legacy(match_root)
    projects: dict[str, MatchProject] = {}
    audits: dict[str, dict[int, dict]] = {}
    for slug in match.shooters:
        root = shooter_roots[slug]
        projects[slug] = MatchProject.load(root)
        docs: dict[int, dict] = {}
        audit_dir = root / "audit"
        if audit_dir.is_dir():
            for path in audit_dir.iterdir():
                m = AUDIT_FILENAME_RE.match(path.name)
                if not m:
                    continue
                try:
                    docs[int(m.group(1))] = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
        audits[slug] = docs
    return projects, audits
```

The `project.automation` attribute name must match what `_run_trim` passes as `project_override=fresh.automation`; confirm it before running.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_sync_reconcile.py -n0 -q`
Expected: PASS.

- [ ] **Step 5: Verify each test fails without its rule**

Temporarily delete the `is_stub_audit` guard and run `test_never_detects_over_real_audit_content` (expect FAIL); restore. Same for the `failures.get(...)` skip with `test_plan_skips_a_step_that_failed_with_the_same_inputs`. Restore both and rerun the file.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/sync/reconcile.py tests/test_sync_reconcile.py
git commit -m "feat(sync): pure reconciler deriving missing trim and detect steps"
```

---

### Task 6: The pure auto-sync scheduling core

**Files:**
- Create: `src/splitsmith/sync/auto.py`
- Test: `tests/test_sync_auto_core.py`

**Interfaces:**
- Produces:

```python
Fingerprint = tuple[int, int]

class AutoSyncCore:
    POLL_ACTIVE_S = 60.0; POLL_IDLE_S = 300.0; IDLE_AFTER_S = 1800.0
    QUIET_S = 45.0; BACKOFF_MIN_S = 60.0; BACKOFF_MAX_S = 900.0
    paused_reason: str | None
    next_poll_at: float
    def mark_dirty(self, match_id: str, now: float) -> None
    def mark_pull_due(self, match_id: str) -> None
    def on_poll_ok(self, now: float, server: Mapping[str, Fingerprint], local: Mapping[str, Fingerprint]) -> None
    def on_poll_error(self, now: float, reason: str, *, auth: bool) -> None
    def auth_blocked(self) -> bool
    def clear_auth_block(self) -> None
    def on_sync_started(self, match_id: str, now: float) -> None
    def on_sync_done(self, match_id: str, now: float, *, ok: bool) -> None
    def pick(self, now: float, *, enabled: Collection[str], busy: Collection[str], sync_active: bool) -> str | None
```

All times are wall-clock seconds (`time.time()`), injected by the caller.

- [ ] **Step 1: Write the failing tests** (`tests/test_sync_auto_core.py`)

```python
"""Auto-sync scheduling core (spec 2026-09-27 s2). Pure, fake clock."""

from __future__ import annotations

from splitsmith.sync.auto import AutoSyncCore

E = {"m1"}


def test_push_waits_for_the_quiet_period() -> None:
    core = AutoSyncCore()
    core.mark_dirty("m1", now=100.0)
    assert core.pick(130.0, enabled=E, busy=(), sync_active=False) is None
    core.mark_dirty("m1", now=130.0)  # another write resets the clock
    assert core.pick(170.0, enabled=E, busy=(), sync_active=False) is None
    assert core.pick(175.0, enabled=E, busy=(), sync_active=False) == "m1"


def test_remote_change_is_pulled_without_waiting() -> None:
    core = AutoSyncCore()
    core.on_poll_ok(10.0, server={"m1": (3, 9)}, local={"m1": (3, 8)})
    assert core.pick(10.0, enabled=E, busy=(), sync_active=False) == "m1"


def test_equal_fingerprints_do_nothing() -> None:
    core = AutoSyncCore()
    core.on_poll_ok(10.0, server={"m1": (3, 9)}, local={"m1": (3, 9)})
    assert core.pick(10.0, enabled=E, busy=(), sync_active=False) is None


def test_busy_match_disabled_match_and_active_sync_defer() -> None:
    core = AutoSyncCore()
    core.mark_pull_due("m1")
    assert core.pick(0.0, enabled=E, busy={"m1"}, sync_active=False) is None
    assert core.pick(0.0, enabled=set(), busy=(), sync_active=False) is None
    assert core.pick(0.0, enabled=E, busy=(), sync_active=True) is None
    assert core.pick(0.0, enabled=E, busy=(), sync_active=False) == "m1"


def test_success_clears_only_what_it_covered() -> None:
    core = AutoSyncCore()
    core.mark_dirty("m1", now=0.0)
    core.on_sync_started("m1", now=50.0)
    core.mark_dirty("m1", now=60.0)  # a write during the sync
    core.on_sync_done("m1", now=70.0, ok=True)
    assert core.pick(80.0, enabled=E, busy=(), sync_active=False) is None
    assert core.pick(105.0, enabled=E, busy=(), sync_active=False) == "m1"


def test_failure_backs_off_exponentially_to_the_cap() -> None:
    core = AutoSyncCore()
    core.mark_pull_due("m1")
    waits = []
    now = 0.0
    for _ in range(6):
        core.on_sync_started("m1", now=now)
        core.on_sync_done("m1", now=now, ok=False)
        t = now
        while core.pick(t, enabled=E, busy=(), sync_active=False) is None:
            t += 1.0
        waits.append(t - now)
        now = t
    assert waits == [60.0, 120.0, 240.0, 480.0, 900.0, 900.0]


def test_settled_fingerprint_does_not_retrigger() -> None:
    """A doc deleted hosted-side leaves the pair different forever. After a
    successful sync the scheduler remembers that server fingerprint and
    waits for it to move."""
    core = AutoSyncCore()
    core.on_poll_ok(0.0, server={"m1": (2, 5)}, local={"m1": (3, 6)})
    core.on_sync_started("m1", now=1.0)
    core.on_sync_done("m1", now=2.0, ok=True)
    core.on_poll_ok(60.0, server={"m1": (2, 5)}, local={"m1": (3, 6)})
    assert core.pick(60.0, enabled=E, busy=(), sync_active=False) is None
    core.on_poll_ok(120.0, server={"m1": (2, 6)}, local={"m1": (3, 6)})
    assert core.pick(120.0, enabled=E, busy=(), sync_active=False) == "m1"


def test_poll_interval_relaxes_when_idle_and_tightens_on_activity() -> None:
    core = AutoSyncCore()
    core.on_poll_ok(0.0, server={}, local={})
    assert core.next_poll_at == 60.0
    core.on_poll_ok(1900.0, server={}, local={})
    assert core.next_poll_at == 2200.0
    core.mark_dirty("m1", now=2000.0)
    core.on_poll_ok(2010.0, server={}, local={})
    assert core.next_poll_at == 2070.0


def test_auth_error_blocks_until_cleared_and_transport_errors_back_off() -> None:
    core = AutoSyncCore()
    core.on_poll_error(0.0, "could not reach the hosted server", auth=False)
    assert core.paused_reason == "could not reach the hosted server"
    assert core.next_poll_at == 60.0
    core.on_poll_error(60.0, "could not reach the hosted server", auth=False)
    assert core.next_poll_at == 180.0
    core.mark_pull_due("m1")
    core.on_poll_error(200.0, "sign in again", auth=True)
    assert core.auth_blocked()
    assert core.pick(1000.0, enabled=E, busy=(), sync_active=False) is None
    core.clear_auth_block()
    assert core.pick(1000.0, enabled=E, busy=(), sync_active=False) == "m1"
    core.on_poll_ok(1000.0, server={}, local={})
    assert core.paused_reason is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_sync_auto_core.py -n0 -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `sync/auto.py`**

```python
"""Desktop auto-sync scheduling core (spec 2026-09-27 s2).

Pure and clock-injected: the FastAPI driver (``ui/auto_sync.py``) feeds
it poll results, local writes and job outcomes, and asks ``pick`` which
match to sync next. Nothing here touches the network, the filesystem or
the job registry.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass

Fingerprint = tuple[int, int]


@dataclass
class _MatchState:
    pull_due: bool = False
    push_due_at: float | None = None
    sync_started_at: float | None = None
    retry_at: float = 0.0
    failures: int = 0
    pending_fp: Fingerprint | None = None
    settled_fp: Fingerprint | None = None


class AutoSyncCore:
    POLL_ACTIVE_S = 60.0
    POLL_IDLE_S = 300.0
    IDLE_AFTER_S = 1800.0
    QUIET_S = 45.0
    BACKOFF_MIN_S = 60.0
    BACKOFF_MAX_S = 900.0

    def __init__(self) -> None:
        self._matches: dict[str, _MatchState] = {}
        self._last_activity = 0.0
        self._poll_failures = 0
        self._auth_blocked = False
        self.paused_reason: str | None = None
        self.next_poll_at = 0.0

    def _m(self, match_id: str) -> _MatchState:
        return self._matches.setdefault(match_id, _MatchState())

    def _backoff(self, failures: int) -> float:
        return min(self.BACKOFF_MIN_S * 2 ** (failures - 1), self.BACKOFF_MAX_S)

    def mark_dirty(self, match_id: str, now: float) -> None:
        self._m(match_id).push_due_at = now
        self._last_activity = now

    def mark_pull_due(self, match_id: str) -> None:
        self._m(match_id).pull_due = True

    def on_poll_ok(
        self, now: float, server: Mapping[str, Fingerprint], local: Mapping[str, Fingerprint]
    ) -> None:
        self._poll_failures = 0
        self.paused_reason = None
        for match_id, local_fp in local.items():
            server_fp = server.get(match_id)
            if server_fp is None:
                continue
            st = self._m(match_id)
            if server_fp == local_fp:
                st.settled_fp = None
                continue
            if server_fp == st.settled_fp:
                continue
            st.pull_due = True
            st.pending_fp = server_fp
            self._last_activity = now
        idle = now - self._last_activity >= self.IDLE_AFTER_S
        self.next_poll_at = now + (self.POLL_IDLE_S if idle else self.POLL_ACTIVE_S)

    def on_poll_error(self, now: float, reason: str, *, auth: bool) -> None:
        self.paused_reason = reason
        if auth:
            self._auth_blocked = True
            return
        self._poll_failures += 1
        self.next_poll_at = now + self._backoff(self._poll_failures)

    def auth_blocked(self) -> bool:
        return self._auth_blocked

    def clear_auth_block(self) -> None:
        self._auth_blocked = False
        self.paused_reason = None
        self.next_poll_at = 0.0

    def on_sync_started(self, match_id: str, now: float) -> None:
        self._m(match_id).sync_started_at = now

    def on_sync_done(self, match_id: str, now: float, *, ok: bool) -> None:
        st = self._m(match_id)
        started = st.sync_started_at if st.sync_started_at is not None else now
        st.sync_started_at = None
        if not ok:
            st.failures += 1
            st.retry_at = now + self._backoff(st.failures)
            return
        st.failures = 0
        st.retry_at = 0.0
        st.pull_due = False
        if st.pending_fp is not None:
            st.settled_fp = st.pending_fp
            st.pending_fp = None
        if st.push_due_at is not None and st.push_due_at <= started:
            st.push_due_at = None

    def pick(
        self, now: float, *, enabled: Collection[str], busy: Collection[str], sync_active: bool
    ) -> str | None:
        if self._auth_blocked or sync_active:
            return None
        ready: list[tuple[int, str]] = []
        for match_id in sorted(enabled):
            st = self._matches.get(match_id)
            if st is None or match_id in busy or now < st.retry_at:
                continue
            if st.pull_due:
                ready.append((0, match_id))
            elif st.push_due_at is not None and now - st.push_due_at >= self.QUIET_S:
                ready.append((1, match_id))
        return min(ready)[1] if ready else None
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_sync_auto_core.py -n0 -q`
Expected: PASS. If `test_failure_backs_off_exponentially_to_the_cap` is off by the loop's 1 s step, fix the implementation, not the expected list.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/sync/auto.py tests/test_sync_auto_core.py
git commit -m "feat(sync): pure auto-sync scheduling core"
```

---

### Task 7: Job terminal listener, reconcile after sync, and `auto_sync` job kind

**Files:**
- Modify: `src/splitsmith/ui/jobs.py` (`JobRegistry.__init__`, `_run`)
- Modify: `src/splitsmith/ui/server.py` (`_run_sync_match` ~4368, body registration ~4439, `_after_beep_reviewed` ~11196)
- Test: `tests/test_jobs.py`, `tests/test_sync_reconcile_server.py`

**Interfaces:**
- Consumes: `plan_reconcile`, `load_reconcile_inputs`, `video_step`, `ReconcileStep` (Task 5); `load_auto_prefs`, `update_auto_prefs`, `AutoRunSummary` (Task 3).
- Produces: `JobRegistry.add_terminal_listener(fn: Callable[[Job], None]) -> None`, called once per job on the worker thread after the terminal status is set, with a snapshot; listener exceptions are logged and swallowed.
- Produces: job kind `auto_sync` (same body as `sync_match` with `auto=True`); both kinds end by submitting reconcile steps. `AppState.reconcile_jobs: dict[str, tuple[Path, ReconcileStep]]` (job id -> step) for the failure memo.

- [ ] **Step 1: Write the failing listener test** (append to `tests/test_jobs.py`)

```python
def test_terminal_listener_sees_each_job_once_and_cannot_break_it() -> None:
    reg = JobRegistry(max_concurrent=1)
    seen: list[tuple[str, str]] = []
    reg.add_terminal_listener(lambda job: seen.append((job.kind, job.status.value)))

    def boom(_job):
        raise RuntimeError("listener bug")

    reg.add_terminal_listener(boom)
    sync = _Sync(reg)
    ok = sync.submit(kind="k_ok", fn=lambda h: None)
    bad = sync.submit(kind="k_bad", fn=lambda h: (_ for _ in ()).throw(ValueError("x")))
    assert _wait_until(lambda: len(seen) == 2)
    assert sorted(seen) == [("k_bad", "failed"), ("k_ok", "succeeded")]
    assert sync.get(ok.id).status.value == "succeeded"
    assert sync.get(bad.id).status.value == "failed"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_jobs.py::test_terminal_listener_sees_each_job_once_and_cannot_break_it -n0 -q`
Expected: FAIL (`AttributeError: add_terminal_listener`).

- [ ] **Step 3: Implement the listener**

In `JobRegistry.__init__`: `self._terminal_listeners: list[Callable[[Job], None]] = []`. Add:

```python
    def add_terminal_listener(self, fn: Callable[[Job], None]) -> None:
        """Call ``fn(job_snapshot)`` once per job after it reaches a terminal
        status, on the worker thread. Used by desktop auto-sync to mark a
        match dirty and to record reconcile failures. A raising listener is
        logged and never affects the job."""
        self._terminal_listeners.append(fn)

    def _notify_terminal(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            snapshot = job.model_copy(deep=True) if job is not None else None
        if snapshot is None:
            return
        for fn in list(self._terminal_listeners):
            try:
                fn(snapshot)
            except Exception:  # noqa: BLE001 - a listener must never fail a job
                logger.exception("job terminal listener failed for %s", job_id)
```

Call `self._notify_terminal(job_id)` right after each of the three `self._emit_terminal_event(...)` calls in `_run` (they are the terminal paths).

- [ ] **Step 4: Run it**

Run: `uv run pytest tests/test_jobs.py -n0 -q`
Expected: PASS.

- [ ] **Step 5: Write the failing server tests** (`tests/test_sync_reconcile_server.py`)

```python
"""Reconcile after sync and the shared confirm rule (spec 2026-09-27 s3)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from splitsmith.match_project import MatchProject
from splitsmith.sync.auto_state import load_auto_prefs
from splitsmith.sync.reconcile import ReconcileStep
from splitsmith.ui import server as server_mod

from .test_audit_local_save import _match_context
from .test_ui_server import _seed_match_export_project


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
        from splitsmith.ui.jobs import Job, JobStatus
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        return Job(id=f"j{len(submitted)}", kind=kw["kind"], status=JobStatus.PENDING, created_at=now, updated_at=now)

    monkeypatch.setattr(state.jobs, "submit", fake_submit)
    with _match_context(project_root):
        server_mod._submit_reconcile_steps(state, project_root)
    assert [(s["kind"], s["video_id"]) for s in submitted] == [("trim", video_id)]
    assert state.reconcile_jobs["j1"][1].kind == "trim"


def test_failed_reconcile_step_is_recorded_and_skipped(tmp_path: Path) -> None:
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state
    video_id = _untrim_stage_one(project_root)
    step = ReconcileStep(kind="trim", slug="me", stage_number=1, video_id=video_id, input_key="x")
    state.reconcile_jobs["jf"] = (project_root, step)
    from datetime import UTC, datetime

    from splitsmith.ui.jobs import Job, JobStatus

    now = datetime.now(UTC)
    failed = Job(id="jf", kind="trim", status=JobStatus.FAILED, created_at=now, updated_at=now)
    server_mod._record_reconcile_outcome(state, failed)
    assert load_auto_prefs(project_root).reconcile_failures == {step.key: "x"}
    assert "jf" not in state.reconcile_jobs
```

Adjust the `Job(...)` constructor calls to the model's required fields (`ui/jobs.py` `class Job`) before running.

- [ ] **Step 6: Run to verify failure**

Run: `uv run pytest tests/test_sync_reconcile_server.py -n0 -q`
Expected: FAIL (`AttributeError: _submit_reconcile_steps`).

- [ ] **Step 7: Implement in `ui/server.py`**

Add `reconcile_jobs: dict[str, tuple[Path, ReconcileStep]] = field(default_factory=dict)` to `AppState` (next to `audit_lock`), with `ReconcileStep` imported under `TYPE_CHECKING` or directly from `..sync.reconcile` (it imports no db).

Module-level helpers (near `_save_audit_with_remerge`):

```python
def _submit_reconcile_steps(state: AppState, match_root: Path) -> list[ReconcileStep]:
    """Queue every step the reconciler finds missing for ``match_root``.

    Runs on a job worker thread (the tail of a sync), so it bridges to the
    async registry with ``asyncio.run`` like the trim chain does. The
    caller's ContextVars carry the match, which is what scopes both the
    ``find_active`` dedupe and the submitted jobs.
    """
    projects, audits = load_reconcile_inputs(match_root)
    failures = load_auto_prefs(match_root).reconcile_failures
    steps = plan_reconcile(projects, audits, failures)
    for step in steps:
        dedupe = {"video_id": step.video_id} if step.kind == "trim" else {}
        if asyncio.run(
            state.jobs.find_active(
                kind=step.kind, stage_number=step.stage_number, shooter_slug=step.slug, **dedupe
            )
        ):
            continue
        args: dict[str, Any] = {"slug": step.slug, "stage_number": step.stage_number}
        if step.kind == "trim":
            args["video_id"] = step.video_id
        job = asyncio.run(
            state.jobs.submit(
                kind=step.kind,
                stage_number=step.stage_number,
                shooter_slug=step.slug,
                video_id=step.video_id if step.kind == "trim" else None,
                args=args,
            )
        )
        state.reconcile_jobs[job.id] = (match_root, step)
    return steps


def _record_reconcile_outcome(state: AppState, job: Job) -> None:
    """Terminal listener half of the failure memo: a failed reconcile step
    is remembered with its inputs so the next pass skips it; a success
    clears any old entry for the same step."""
    tracked = state.reconcile_jobs.pop(job.id, None)
    if tracked is None:
        return
    match_root, step = tracked
    if job.status == JobStatus.FAILED:
        update_auto_prefs(match_root, lambda p: p.reconcile_failures.__setitem__(step.key, step.input_key))
    elif job.status == JobStatus.SUCCEEDED:
        update_auto_prefs(match_root, lambda p: p.reconcile_failures.pop(step.key, None))
```

Register `state.jobs.add_terminal_listener(functools.partial(_record_reconcile_outcome, state))` where the job bodies are registered (local mode only: guard with `if not _hosted_mode_active():`).

Change `_run_sync_match(handle: JobHandle) -> None` to `_run_sync_match(handle: JobHandle, *, auto: bool = False) -> None`. After `handle.set_result(report.model_dump())`, add:

```python
        steps = _submit_reconcile_steps(state, match_root)
        if steps:
            handle.update(progress=1.0, message=f"{format_sync_message(report)}; queued {len(steps)} step(s)")
        if auto:
            update_auto_prefs(
                match_root,
                lambda p: setattr(
                    p,
                    "last_auto",
                    AutoRunSummary(
                        at=datetime.now(UTC),
                        ok=True,
                        message=format_sync_message(report),
                        conflicts=len(report.conflicts),
                        notes=len(report.notes),
                    ),
                ),
            )
```

and in the body's error path (wrap the existing `try` so every exception reaching the job also records, then re-raises) when `auto` is true:

```python
        except Exception as exc:
            if auto:
                update_auto_prefs(
                    match_root,
                    lambda p: setattr(
                        p, "last_auto", AutoRunSummary(at=datetime.now(UTC), ok=False, message=str(exc))
                    ),
                )
            raise
```

Register: `state.jobs.bodies.register("auto_sync", functools.partial(_run_sync_match, auto=True))` next to `sync_match`.

In `_after_beep_reviewed` (keep the desktop-origin early return and the stub write), replace the trim / shot_detect branch with the shared rule:

```python
        stage = state.shooter_project(slug).stage(stage_number)
        step = video_step(stage, video, None, explicit=True)
        if step == "trim":
            await _maybe_chain_trim(slug, stage, video)
        elif (
            step == "shot_detect"
            and await state.jobs.find_active(kind="shot_detect", stage_number=stage_number, shooter_slug=slug)
            is None
        ):
            await state.jobs.submit(
                kind="shot_detect",
                stage_number=stage_number,
                shooter_slug=slug,
                args={"slug": slug, "stage_number": stage_number},
            )
```

(`video_step(..., explicit=True)` returns `"trim"` exactly when `processed["trim"]` is false and a trim is possible, and `"shot_detect"` for a trimmed primary: today's behaviour. A trimmed secondary returns None, as before.)

- [ ] **Step 8: Run the tests**

Run: `uv run pytest tests/test_sync_reconcile_server.py tests/test_jobs.py tests/test_sync_local_endpoints.py -n0 -q` then `uv run pytest tests/test_ui_server.py -k "beep_review or reviewed or chain" -n0 -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/splitsmith/ui/jobs.py src/splitsmith/ui/server.py tests/test_jobs.py tests/test_sync_reconcile_server.py
git commit -m "feat(sync): reconcile after every sync; auto_sync job kind; terminal listeners"
```

---

### Task 8: The auto-sync service, middleware, lifespan and routes

**Files:**
- Create: `src/splitsmith/ui/auto_sync.py`
- Modify: `src/splitsmith/ui/server.py` (`create_app` lifespan ~7026, middleware registration, routes next to `/api/match/sync/status` ~7501)
- Test: `tests/test_auto_sync_service.py`

**Interfaces:**
- Consumes: `AutoSyncCore` (Task 6), `auto_state` (Task 3), `HostedSyncClient.get_fingerprints` (Task 2), `local_fingerprint` (Task 3), `add_terminal_listener` (Task 7).
- Produces:

```python
class AutoSyncService:
    def __init__(self, *, jobs, matches, submit_auto_sync: Callable[[str, Path], Awaitable[None]],
                 load_prefs=user_config.load_global_prefs, fetch_fingerprints=None, clock=time.time) -> None
    core: AutoSyncCore
    def mark_dirty(self, match_id: str) -> None           # thread-safe
    def on_job_terminal(self, job: Job) -> None           # thread-safe, listener
    async def tick(self) -> None                          # one scheduling pass
    async def run(self, interval_s: float = 5.0) -> None  # loop until cancelled
    def status_for(self, match_root: Path) -> dict
AUTO_SYNC_ENV = "SPLITSMITH_AUTO_SYNC"   # "0" disables the service
SYNC_KINDS = frozenset({"sync_match", "auto_sync"})
```

- `GET /api/match/sync/auto` -> `{"enabled": bool, "setting": bool | None, "global_enabled": bool, "paused_reason": str | None, "last_auto": AutoRunSummary | None}`; `PUT /api/match/sync/auto` body `{"enabled": bool | None}`; `PUT /api/settings/auto-sync` body `{"enabled": bool}`. All 404 in hosted mode.

- [ ] **Step 1: Write the failing tests** (`tests/test_auto_sync_service.py`)

```python
"""Auto-sync service driver (spec 2026-09-27 s2)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

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


def _service(tmp_path: Path, server_fps: dict, submitted: list, clock) -> tuple[AutoSyncService, Path]:
    root = tmp_path / "m1"
    root.mkdir()
    _synced(root, {"match": 1, "project/me": 2})

    async def submit(match_id: str, match_root: Path) -> None:
        submitted.append(match_id)

    svc = AutoSyncService(
        jobs=JobRegistry(max_concurrent=1),
        matches=_Matches({"m1": root}),
        submit_auto_sync=submit,
        load_prefs=lambda: GlobalPrefs(hosted_base_url="https://h", hosted_token="t"),
        fetch_fingerprints=lambda prefs: server_fps,
        clock=clock,
    )
    return svc, root


def test_remote_change_submits_an_auto_sync(tmp_path: Path) -> None:
    submitted: list[str] = []
    svc, _ = _service(tmp_path, {"m1": (2, 4)}, submitted, clock=lambda: 1000.0)
    asyncio.run(svc.tick())
    assert submitted == ["m1"]


def test_matching_fingerprint_and_disabled_match_do_nothing(tmp_path: Path) -> None:
    submitted: list[str] = []
    svc, root = _service(tmp_path, {"m1": (2, 3)}, submitted, clock=lambda: 1000.0)
    asyncio.run(svc.tick())
    assert submitted == ["m1"]  # the first tick marks every enabled match pull-due
    submitted.clear()
    update_auto_prefs(root, lambda p: setattr(p, "enabled", False))
    svc.core.mark_pull_due("m1")
    asyncio.run(svc.tick())
    assert submitted == []


def test_toggle_survives_a_running_sync(tmp_path: Path) -> None:
    """The flag is in auto_sync.json; run_sync's sync_state saves can't undo it."""
    submitted: list[str] = []
    svc, root = _service(tmp_path, {}, submitted, clock=lambda: 1000.0)
    update_auto_prefs(root, lambda p: setattr(p, "enabled", False))
    _synced(root, {"match": 5})  # what a running sync writes
    assert svc.status_for(root)["enabled"] is False


def test_auth_failure_pauses_until_the_token_changes(tmp_path: Path) -> None:
    import httpx

    submitted: list[str] = []
    token = {"v": "t"}

    def fetch(prefs):
        if prefs.hosted_token == "t":
            request = httpx.Request("GET", "https://h/api/sync/fingerprints")
            raise httpx.HTTPStatusError("401", request=request, response=httpx.Response(401, request=request))
        return {"m1": (9, 9)}

    root = tmp_path / "m1"
    root.mkdir()
    _synced(root, {"match": 1})

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
    asyncio.run(svc.tick())
    assert submitted == [] and svc.core.auth_blocked()
    token["v"] = "t2"
    asyncio.run(svc.tick())
    assert submitted == ["m1"]


def test_local_write_route_marks_the_match_dirty(tmp_path: Path) -> None:
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state
    svc = state.auto_sync
    assert svc is not None
    loaded = client.get("/api/shooters/me/stages/1/audit").json()
    assert client.put("/api/shooters/me/stages/1/audit", json=loaded).status_code == 200
    match_id = next(iter(state.matches.known_ids()))
    assert svc.core._matches[match_id].push_due_at is not None
```

The `_seed_match_export_project` client must be created in local mode with the auto-sync service constructed; the service does no work without hosted-sync prefs, so constructing it in tests is safe. If `_MatchClient` does not route through `/api/matches/{id}/`, read it and adjust the dirty-middleware test to call the prefixed path.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_auto_sync_service.py -n0 -q`
Expected: FAIL (`ModuleNotFoundError: splitsmith.ui.auto_sync`).

- [ ] **Step 3: Implement `ui/auto_sync.py`**

```python
"""Desktop auto-sync driver (spec 2026-09-27 s2).

Feeds :class:`splitsmith.sync.auto.AutoSyncCore` from the outside world:
fingerprint polls against hosted, local writes (middleware), job
outcomes (terminal listener), and submits the ``auto_sync`` job the core
picks. Local mode only; ``create_app`` never builds it hosted.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from .. import user_config
from ..sync.auto import AutoSyncCore, Fingerprint
from ..sync.auto_state import auto_sync_effective, load_auto_prefs
from ..sync.client import HostedSyncClient
from ..sync.state import load_sync_state, local_fingerprint
from .jobs import Job, JobStatus

logger = logging.getLogger(__name__)

AUTO_SYNC_ENV = "SPLITSMITH_AUTO_SYNC"
SYNC_KINDS = frozenset({"sync_match", "auto_sync"})


def auto_sync_disabled_by_env() -> bool:
    return os.environ.get(AUTO_SYNC_ENV, "").strip() in ("0", "false", "False")


def _fetch_fingerprints(prefs: user_config.GlobalPrefs) -> dict[str, Fingerprint]:
    http = httpx.Client(
        base_url=prefs.hosted_base_url or "",
        headers={"Authorization": f"Bearer {prefs.hosted_token}"},
        timeout=15.0,
    )
    try:
        return HostedSyncClient(http=http).get_fingerprints()
    finally:
        http.close()


class AutoSyncService:
    def __init__(
        self,
        *,
        jobs: Any,
        matches: Any,
        submit_auto_sync: Callable[[str, Path], Awaitable[None]],
        load_prefs: Callable[[], user_config.GlobalPrefs] = user_config.load_global_prefs,
        fetch_fingerprints: Callable[[user_config.GlobalPrefs], dict[str, Fingerprint]] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.core = AutoSyncCore()
        self._jobs = jobs
        self._matches = matches
        self._submit = submit_auto_sync
        self._load_prefs = load_prefs
        self._fetch = fetch_fingerprints or _fetch_fingerprints
        self._clock = clock
        self._lock = threading.Lock()
        self._started = False
        self._auth_failed_token: str | None = None

    # -- thread-safe inputs ------------------------------------------------

    def mark_dirty(self, match_id: str) -> None:
        with self._lock:
            self.core.mark_dirty(match_id, self._clock())

    def on_job_terminal(self, job: Job) -> None:
        if job.match_id is None:
            return
        with self._lock:
            if job.kind in SYNC_KINDS:
                self.core.on_sync_done(job.match_id, self._clock(), ok=job.status == JobStatus.SUCCEEDED)
            elif job.status == JobStatus.SUCCEEDED:
                self.core.mark_dirty(job.match_id, self._clock())

    # -- scheduling --------------------------------------------------------

    def _enabled_roots(self, prefs: user_config.GlobalPrefs) -> dict[str, Path]:
        self._matches.refresh_from_recent_projects()
        roots: dict[str, Path] = {}
        for match_id in self._matches.known_ids():
            try:
                root = self._matches.resolve(match_id)
            except KeyError:
                continue
            if auto_sync_effective(
                load_auto_prefs(root), load_sync_state(root), global_enabled=prefs.auto_sync_enabled
            ):
                roots[match_id] = root
        return roots

    async def tick(self) -> None:
        prefs = self._load_prefs()
        if not prefs.hosted_base_url or not prefs.hosted_token:
            return
        now = self._clock()
        if self.core.auth_blocked() and prefs.hosted_token != self._auth_failed_token:
            with self._lock:
                self.core.clear_auth_block()
        roots = await asyncio.to_thread(self._enabled_roots, prefs)
        if not self._started:
            self._started = True
            with self._lock:
                for match_id in roots:
                    self.core.mark_pull_due(match_id)
        if now >= self.core.next_poll_at and not self.core.auth_blocked():
            await self._poll(prefs, roots, now)
        jobs = await self._jobs.list()
        active = [j for j in jobs if j.status in (JobStatus.PENDING, JobStatus.RUNNING)]
        busy = {j.match_id for j in active if j.match_id}
        sync_active = any(j.kind in SYNC_KINDS for j in active)
        with self._lock:
            match_id = self.core.pick(now, enabled=roots, busy=busy, sync_active=sync_active)
            if match_id is not None:
                self.core.on_sync_started(match_id, now)
        if match_id is not None:
            await self._submit(match_id, roots[match_id])

    async def _poll(self, prefs: user_config.GlobalPrefs, roots: dict[str, Path], now: float) -> None:
        try:
            server = await asyncio.to_thread(self._fetch, prefs)
        except httpx.HTTPStatusError as exc:
            auth = exc.response.status_code in (401, 403)
            if auth:
                self._auth_failed_token = prefs.hosted_token
            reason = "sign in again in hosted sync settings" if auth else f"hosted returned {exc.response.status_code}"
            with self._lock:
                self.core.on_poll_error(now, reason, auth=auth)
            return
        except (httpx.HTTPError, OSError) as exc:
            with self._lock:
                self.core.on_poll_error(now, "offline: could not reach the hosted server", auth=False)
            logger.info("auto-sync poll failed: %s", exc)
            return
        local = {mid: local_fingerprint(load_sync_state(root)) for mid, root in roots.items()}
        with self._lock:
            self.core.on_poll_ok(now, server, local)

    async def run(self, interval_s: float = 5.0) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must outlive one bad tick
                logger.exception("auto-sync tick failed")
            await asyncio.sleep(interval_s)

    # -- status ------------------------------------------------------------

    def status_for(self, match_root: Path) -> dict[str, Any]:
        prefs = self._load_prefs()
        auto = load_auto_prefs(match_root)
        return {
            "enabled": auto_sync_effective(
                auto, load_sync_state(match_root), global_enabled=prefs.auto_sync_enabled
            ),
            "setting": auto.enabled,
            "global_enabled": prefs.auto_sync_enabled,
            "paused_reason": self.core.paused_reason,
            "last_auto": auto.last_auto.model_dump(mode="json") if auto.last_auto else None,
        }
```

- [ ] **Step 4: Wire it into `create_app`** (`ui/server.py`)

Add `auto_sync: AutoSyncService | None = None` to `AppState`. In `create_app`, before `app = FastAPI(...)`:

```python
    if not _hosted_mode_active() and not auto_sync_disabled_by_env():

        async def _submit_auto_sync(match_id: str, match_root: Path) -> None:
            id_token = current_match_id.set(match_id)
            root_token = current_match_root.set(match_root)
            try:
                await state.jobs.submit(kind="auto_sync")
            finally:
                current_match_root.reset(root_token)
                current_match_id.reset(id_token)

        state.auto_sync = AutoSyncService(jobs=state.jobs, matches=state.matches, submit_auto_sync=_submit_auto_sync)
        state.jobs.add_terminal_listener(state.auto_sync.on_job_terminal)
```

Lifespan: `lifespan=_hosted_boot_lifespan(state) or _local_boot_lifespan(state)`, with:

```python
def _local_boot_lifespan(state: Any) -> Any | None:
    """Start desktop auto-sync (spec 2026-09-27) for the app's lifetime;
    None when there is no service (hosted, or SPLITSMITH_AUTO_SYNC=0)."""
    service = getattr(state, "auto_sync", None)
    if service is None:
        return None

    @asynccontextmanager
    async def _lifespan(_app: Any) -> AsyncIterator[None]:
        task = asyncio.create_task(service.run())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return _lifespan
```

Dirty middleware (register after the alias middleware so it is outermost and sees the original path):

```python
    _MATCH_WRITE_RE = re.compile(r"\A/api/matches/([^/]+)/(.*)\Z")

    if state.auto_sync is not None:

        @app.middleware("http")
        async def _auto_sync_dirty(request: Request, call_next: Any) -> Any:
            response = await call_next(request)
            if request.method in ("POST", "PUT", "PATCH", "DELETE") and response.status_code < 400:
                m = _MATCH_WRITE_RE.match(request.url.path)
                if m and not m.group(2).startswith(("match/sync", "jobs")):
                    state.auto_sync.mark_dirty(m.group(1))
            return response
```

Routes, next to `get_match_sync_status`:

```python
    class AutoSyncSettingRequest(BaseModel):
        enabled: bool | None = None

    @app.get("/api/match/sync/auto")
    async def get_match_auto_sync() -> JSONResponse:
        if _hosted_mode_active() or state.auto_sync is None:
            raise HTTPException(status_code=404, detail="not found")
        return JSONResponse(state.auto_sync.status_for(state.match_root))

    @app.put("/api/match/sync/auto")
    async def put_match_auto_sync(req: AutoSyncSettingRequest) -> JSONResponse:
        if _hosted_mode_active() or state.auto_sync is None:
            raise HTTPException(status_code=404, detail="not found")
        update_auto_prefs(state.match_root, lambda p: setattr(p, "enabled", req.enabled))
        return JSONResponse(state.auto_sync.status_for(state.match_root))

    @app.put("/api/settings/auto-sync")
    async def put_global_auto_sync(req: AutoSyncSettingRequest) -> JSONResponse:
        if _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        prefs = user_config.load_global_prefs()
        prefs.auto_sync_enabled = req.enabled is not False
        user_config.save_global_prefs(prefs)
        return JSONResponse({"global_enabled": prefs.auto_sync_enabled})
```

Also make `get_match_sync_status` treat an `auto_sync` job like `sync_match` wherever it checks for an active sync (grep for `"sync_match"` in `server.py`).

Set `SPLITSMITH_AUTO_SYNC=0` in `tests/conftest.py`'s isolated env (`_ISOLATED_PASSTHROUGH_ENV` handling or a session autouse fixture) so no other test starts the loop; the service tests construct `AutoSyncService` directly, and the middleware test must opt back in with `monkeypatch.setenv("SPLITSMITH_AUTO_SYNC", "1")` before seeding.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_auto_sync_service.py tests/test_sync_local_endpoints.py tests/test_sync_api.py::test_local_mode_404 -n0 -q`
Expected: PASS.

- [ ] **Step 6: Slim import surface and lint**

Run: `uv run python scripts/ci/assert_slim_import_surface.py && uv run ruff check src tests && uv run black --check src tests`
Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/ui/auto_sync.py src/splitsmith/ui/server.py tests/test_auto_sync_service.py tests/conftest.py
git commit -m "feat(sync): desktop auto-sync service, dirty tracking and routes"
```

---

### Task 9: SPA API, jobs strip and labels

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (types near `SyncStatusResponse` ~5039, methods near `getSyncStatus` ~4571)
- Modify: `src/splitsmith/ui_static/src/lib/jobs.ts` (buckets ~112)
- Modify: `src/splitsmith/ui_static/src/lib/jobLabels.ts`, `src/splitsmith/ui_static/src/components/Jobs.tsx` (icon map ~71)
- Test: `src/splitsmith/ui_static/src/lib/jobs.test.ts` (create if absent)

**Interfaces:**
- Produces: `AutoSyncStatus` type; `api.getAutoSync()`, `api.setAutoSync(enabled: boolean | null)`, `api.setGlobalAutoSync(enabled: boolean)`; `export function stripVisible(jobs: Job[]): Job[]`.

- [ ] **Step 1: Write the failing test** (`lib/jobs.test.ts`)

```ts
import { describe, expect, it } from "vitest";

import type { Job } from "./api";
import { stripVisible } from "./jobs";

const job = (kind: string, status: Job["status"]): Job =>
  ({ id: `${kind}-${status}`, kind, status, match_id: "m" }) as Job;

describe("stripVisible", () => {
  it("hides automatic syncs unless they failed", () => {
    const jobs = [
      job("auto_sync", "running"),
      job("auto_sync", "pending"),
      job("auto_sync", "failed"),
      job("sync_match", "running"),
      job("trim", "running"),
    ];
    expect(stripVisible(jobs).map((j) => j.id)).toEqual([
      "auto_sync-failed",
      "sync_match-running",
      "trim-running",
    ]);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/lib/jobs.test.ts`
Expected: FAIL (`stripVisible` is not exported).

- [ ] **Step 3: Implement**

`lib/jobs.ts`:

```ts
/** Jobs the progress strip shows. Automatic syncs run every few minutes
 *  and would make the strip flicker; they surface only when they fail. */
export function stripVisible(jobs: Job[]): Job[] {
  return jobs.filter((j) => j.kind !== "auto_sync" || j.status === "failed");
}
```

and derive the buckets from it:

```ts
  const visible = stripVisible(jobs);
  const running = visible.filter((j) => j.status === "running");
  const pending = visible.filter((j) => j.status === "pending");
  const failed = visible.filter((j) => j.status === "failed" && !j.acknowledged);
```

`lib/jobLabels.ts`: add `auto_sync: "Auto-sync",`. `components/Jobs.tsx` icon map: add `auto_sync: <CloudUpload className="size-3.5" />,`.

`lib/api.ts` types:

```ts
/** GET/PUT /api/match/sync/auto (spec 2026-09-27). ``setting`` null means
 *  the default, which is on once the match has synced. */
export interface AutoSyncStatus {
  enabled: boolean;
  setting: boolean | null;
  global_enabled: boolean;
  paused_reason: string | null;
  last_auto: {
    at: string;
    ok: boolean;
    message: string;
    conflicts: number;
    notes: number;
  } | null;
}
```

methods next to `startSync`:

```ts
  getAutoSync: () => request<AutoSyncStatus>("/api/match/sync/auto"),
  setAutoSync: (enabled: boolean | null) =>
    request<AutoSyncStatus>("/api/match/sync/auto", { method: "PUT", json: { enabled } }),
  setGlobalAutoSync: (enabled: boolean) =>
    request<{ global_enabled: boolean }>("/api/settings/auto-sync", {
      method: "PUT",
      json: { enabled },
    }),
```

- [ ] **Step 4: Run it**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/lib/jobs.test.ts && pnpm tsc --noEmit`
Expected: PASS, no type errors.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/lib src/splitsmith/ui_static/src/components/Jobs.tsx
git commit -m "feat(ui): auto-sync api, hide automatic syncs from the jobs strip"
```

---

### Task 10: SyncCard, settings switch, and desktop Audit 409 handling

**Files:**
- Modify: `src/splitsmith/ui_static/src/components/match/SyncCard.tsx`
- Modify: `src/splitsmith/ui_static/src/components/match/SyncSettingsDialog.tsx`
- Modify: `src/splitsmith/ui_static/src/pages/Audit.tsx` (save `catch` ~1230)
- Test: `src/splitsmith/ui_static/src/components/match/SyncCard.test.tsx`

**Interfaces:**
- Consumes: `api.getAutoSync`, `api.setAutoSync`, `api.setGlobalAutoSync`, `AutoSyncStatus` (Task 9).

- [ ] **Step 1: Write the failing tests** (append to `SyncCard.test.tsx`, following its existing mock setup for `api.getSyncStatus` / `api.getSyncSettings`; add `getAutoSync` and `setAutoSync` to the same mock object)

```tsx
it("shows auto-sync on and lets the user switch to manual", async () => {
  mockApi.getSyncStatus.mockResolvedValue({ ...syncedStatus });
  mockApi.getAutoSync.mockResolvedValue({
    enabled: true, setting: null, global_enabled: true, paused_reason: null, last_auto: null,
  });
  mockApi.setAutoSync.mockResolvedValue({
    enabled: false, setting: false, global_enabled: true, paused_reason: null, last_auto: null,
  });
  render(<SyncCard jobs={[]} matchId="m1" />);
  const group = await screen.findByRole("group", { name: "Sync mode" });
  expect(within(group).getByRole("button", { name: "Auto" })).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(within(group).getByRole("button", { name: "Manual" }));
  expect(mockApi.setAutoSync).toHaveBeenCalledWith(false);
});

it("says why auto-sync is paused", async () => {
  mockApi.getSyncStatus.mockResolvedValue({ ...syncedStatus });
  mockApi.getAutoSync.mockResolvedValue({
    enabled: true, setting: null, global_enabled: true,
    paused_reason: "offline: could not reach the hosted server", last_auto: null,
  });
  render(<SyncCard jobs={[]} matchId="m1" />);
  expect(await screen.findByText(/Auto-sync paused: offline/)).toBeInTheDocument();
});

it("treats a running auto_sync job as syncing", async () => {
  mockApi.getSyncStatus.mockResolvedValue({ ...syncedStatus });
  mockApi.getAutoSync.mockResolvedValue({
    enabled: true, setting: null, global_enabled: true, paused_reason: null, last_auto: null,
  });
  const running = { id: "a", kind: "auto_sync", status: "running", match_id: "m1", message: "uploading" } as Job;
  render(<SyncCard jobs={[running]} matchId="m1" />);
  expect(await screen.findByText("uploading")).toBeInTheDocument();
});
```

Use the file's existing names for the mock object and the "synced" fixture (read the top of `SyncCard.test.tsx` first; rename `mockApi` / `syncedStatus` to match).

- [ ] **Step 2: Run to verify failure**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/components/match/SyncCard.test.tsx`
Expected: the three new tests FAIL.

- [ ] **Step 3: Implement SyncCard**

- State: `const [auto, setAuto] = useState<AutoSyncStatus | null>(null);` Load it in `load()` alongside the other two with `api.getAutoSync().catch(() => null)` (an older sidecar without the route must not break the card).
- `isActiveSync`: `(j: Job) => (j.kind === "sync_match" || j.kind === "auto_sync") && isJobActive(j)`.
- In the actions row, before "Settings", when `status?.configured && status.last_synced_at`:

```tsx
<Segmented
  label="Sync mode"
  value={auto?.enabled ? "auto" : "manual"}
  options={[
    { value: "auto", label: "Auto", disabled: auto?.global_enabled === false },
    { value: "manual", label: "Manual" },
  ]}
  onChange={(v) => void handleAutoChange(v === "auto")}
/>
```

with

```tsx
async function handleAutoChange(on: boolean) {
  try {
    setAuto(await api.setAutoSync(on ? null : false));
  } catch (e) {
    setStartError(apiErrorText(e, "Could not change auto-sync."));
  }
}
```

(`null` restores the default rather than pinning `true`, so the per-match file does not hold a redundant value.)

- `SyncStatusLine` gains an `auto` prop. Right after the `syncing` / `otherMatchSyncing` branches:

```tsx
  if (auto?.enabled && auto.paused_reason) {
    return (
      <p className={cn(lineClass, "text-led-text")} aria-live="polite">
        <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
        Auto-sync paused: {auto.paused_reason}
      </p>
    );
  }
```

and in the final "Synced" branch, prefix with `Auto-sync on, ` when `auto?.enabled`, and when `auto?.last_auto` has `conflicts + notes > 0` append `, ${n} note${n === 1 ? "" : "s"} on the last run`. When `auto?.last_auto && !auto.last_auto.ok`, render the error branch with `Last auto-sync failed: {auto.last_auto.message}`.

- [ ] **Step 4: Settings dialog global switch**

In `SyncSettingsDialog.tsx`, inside `CardContent` after the existing fields, render (only when the dialog has settings loaded):

```tsx
<div className="flex items-center justify-between gap-3 border-t border-rule pt-3">
  <span className="text-sm text-ink-2">Auto-sync on this computer</span>
  <Segmented
    label="Auto-sync on this computer"
    value={globalAuto ? "on" : "off"}
    options={[
      { value: "on", label: "On" },
      { value: "off", label: "Off" },
    ]}
    onChange={(v) => void handleGlobalAuto(v === "on")}
  />
</div>
```

`globalAuto` is initialised from a `getAutoSync()` call when the dialog opens (`global_enabled`), and `handleGlobalAuto` calls `api.setGlobalAutoSync` and updates it; failures reuse the dialog's existing error line.

- [ ] **Step 5: Desktop Audit 409**

In `Audit.tsx`'s save `catch`, before the generic branch, mirror `MobileAudit`:

```tsx
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          try {
            const fresh = await api.getStageAudit(slug, stageNumber);
            setAudit(fresh);
            setMarkers(deriveMarkers(fresh));
            sessionEventsRef.current = [];
            isDirtyRef.current = false;
            setSaveStatus({
              kind: "error",
              message: "This stage changed elsewhere (synced from another device). Reloaded; your unsaved edits were discarded.",
            });
          } catch (reloadErr) {
            setSaveStatus({
              kind: "error",
              message: `Save conflicted and the reload failed. Check your connection and retry (${apiErrorText(reloadErr, "reload failed")}).`,
            });
          }
          return false;
        }
        const message = err instanceof ApiError ? err.detail : String(err);
        setSaveStatus({ kind: "error", message });
        return false;
      }
```

Use whichever of `apiErrorText` / `saveErrorMessage` the file already imports.

- [ ] **Step 6: Run the SPA checks**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/components/match && pnpm vitest run src/pages && pnpm tsc --noEmit && pnpm lint`
Expected: PASS, no lint errors (no arbitrary `text-[...]`).

- [ ] **Step 7: Build and commit**

```bash
cd src/splitsmith/ui_static && pnpm build && cd -
git add src/splitsmith/ui_static/src src/splitsmith/ui_static/dist
git commit -m "feat(ui): auto-sync controls on SyncCard and settings; audit reloads on conflict"
```

(Check `git log -3 --stat` on an earlier UI PR first: commit `dist/` only if the repo tracks it.)

---

### Task 11: Docs and end-to-end verification

**Files:**
- Modify: `CLAUDE.md` (new section after "State doc kinds and the sync allowlist")
- Modify: `docs/superpowers/specs/2026-09-27-auto-sync-reconciler-design.md` (Status line)

- [ ] **Step 1: CLAUDE.md section**

```markdown
## Desktop auto-sync (spec 2026-09-27)

A running desktop (app or ``splitsmith ui``) syncs every match it has
synced before, both ways, with no clicks. ``ui/auto_sync.AutoSyncService``
polls ``GET /api/sync/fingerprints`` (``(doc_count, version_sum)`` over
the pullable kinds per match; compared with ``sync.state.local_fingerprint``)
and marks a match dirty from two hooks: an HTTP middleware on successful
writes under ``/api/matches/{id}/`` and a job terminal listener. The pure
core (``sync/auto.py``) decides when: pulls immediately, pushes after 45 s
of quiet, never while the match has a job running, one sync at a time,
backoff to 15 min, a 401 pauses until the token changes. Automatic runs
are the ``auto_sync`` job kind (the ``sync_match`` body), hidden from the
jobs strip unless they fail.

Every sync ends with the reconciler (``sync/reconcile.py``, pure): from
the ``processed`` flags and audit docs it queues missing ``trim`` and
``shot_detect`` steps, which is how a beep confirmed on the phone gets
trimmed and detected on the desktop. ``_after_beep_reviewed`` uses the
same ``video_step`` with ``explicit=True``; a new pipeline step belongs
there, not in a second rule. A reconcile step that fails is remembered
with its inputs in ``auto_sync.json`` and skipped until they change.

Per-match state (``enabled``, the failure memo, the last run) lives in
``<match>/auto_sync.json``, never ``sync_state.json``: ``run_sync`` saves
that file from memory during a run and would undo a toggle. The global
switch is ``GlobalPrefs.auto_sync_enabled``. ``SPLITSMITH_AUTO_SYNC=0``
disables the service (the test suite sets it).

Audit GET/PUT carry ``_version`` (``audit_revision``, a content hash, the
same in both modes); a PUT with a stale one is a 409 ``version_conflict``
and both audit pages reload. It is never stored. The pull's audit
read-merge-write holds ``AppState.audit_lock``, the lock the PUT's
compare-and-save holds.

Automatic pushes still upload full trims; the web-only policy is v1.1
because hosted playback of a mirror keys off the full trim object.
```

- [ ] **Step 2: Mark the spec implemented**

Change `Status: design approved in conversation, awaiting spec review` to `Status: implemented in v1 (auto-sync + reconciler); v1.1 web-only media and v2 command queue pending`.

- [ ] **Step 3: Full targeted Python run**

Run: `uv run pytest tests/test_jobs.py tests/test_sync_api.py tests/test_sync_auto_state.py tests/test_audit_revision.py tests/test_sync_reconcile.py tests/test_sync_auto_core.py tests/test_sync_reconcile_server.py tests/test_auto_sync_service.py tests/test_sync_local_endpoints.py tests/test_sync_integration.py tests/test_sync_pull.py tests/test_sync_push.py tests/test_audit_local_save.py tests/test_audit.py tests/test_mirror_read_only.py -q`
Expected: PASS. Report the exact counts.

- [ ] **Step 4: End-to-end by hand against staging**

1. `uv run python scripts/seed_demo_match.py ~/.claude-tmp/auto-sync-demo --media`
2. `uv run splitsmith ui --project ~/.claude-tmp/auto-sync-demo --skip-system-check --no-browser --port 5174`; link hosted sync to staging (use the `staging-login` skill for the account) and press Sync once.
3. Through the staging API as the same user, un-review and then confirm a stage's beep (`POST .../beep/review` with `{"reviewed": true}` on the mirror).
4. Within about 60 s the local jobs list shows `auto_sync`, then `trim` and `shot_detect` for that stage; after they finish and 45 s pass, another `auto_sync` runs.
5. Read the stage's audit back from staging: shots present, `detection` not `"none"`.
6. Screenshot the SyncCard in the Auto state and in "Auto-sync paused" (stop the network briefly), and look at both.

Record timings and anything surprising in the PR description.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-09-27-auto-sync-reconciler-design.md
git commit -m "docs: desktop auto-sync and the reconciler"
```
