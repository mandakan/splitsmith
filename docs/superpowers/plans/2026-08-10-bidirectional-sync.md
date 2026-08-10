# Bidirectional Sync Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the desktop sync pull-then-push so hosted-side edits (the coming mobile write surfaces) survive a desktop push, per spec `docs/superpowers/specs/2026-08-10-bidirectional-sync-design.md`.

**Architecture:** Hosted gains read routes (doc manifest + per-doc GET) and a required `expected_version` guard on the three doc PUTs; it stays passive (never merges, never derives for mirrors). Desktop's `sync_match` job becomes pull -> three-way merge (against base snapshots in `sync_base/`) -> push, with a bounded retry on version conflict. A merged-in beep change invalidates local derivations the same way a local beep override does.

**Tech Stack:** Python 3.12, FastAPI, pydantic v2, SQLAlchemy 2 async (hosted store), httpx, pytest; React/TypeScript SPA (pnpm only), vitest.

## Global Constraints

- New prose/comments use single ASCII dash "-", never "--" or em dash. Grep added lines before committing.
- No fallback/compat paths: `expected_version` is required on the PUT routes, the old unconditional `_mirror_save` is deleted, desktop is the only client.
- Every new `ProjectStateStore` method gets a tenant-isolation test in `tests/test_project_state_store.py` (store docstring discipline).
- SPA is pnpm-only (`src/splitsmith/ui_static/`); never touch npm/package-lock.
- Gates before PR: `ruff check . && black --check . && pytest` plus `cd src/splitsmith/ui_static && pnpm typecheck && pnpm test` and scoped eslint. Local `pytest -m docker` required (DB paths change); needs `export PATH="$HOME/.claude-tmp/bin:$PATH"` for docker.
- Audit event ids are `uuid.uuid4().hex`, not ULID - `python-ulid` lives in the hosted-only extra (`pyproject.toml:111`) and event ids must stamp on slim local installs too. Ordering comes from `ts`; ids only need uniqueness. (Deliberate deviation from the spec's "ULID" wording.)
- Work on branch `feat/sync-pull-merge` forked from `main` (spec/plan docs merge in from `docs/sync-slice-spec`).

## File Structure

- Modify `src/splitsmith/ui/server.py` - event-id helper + append sites, `SyncStatusResponse.remote_changes`, status handler, `_run_sync_match` -> `run_sync`.
- Modify `src/splitsmith/db/project_state.py` - `list_doc_meta`.
- Modify `src/splitsmith/ui/sync_api.py` - GET manifest/doc routes, strict PUT.
- Modify `src/splitsmith/sync/state.py` - schema v2 (`doc_versions`).
- Create `src/splitsmith/sync/base.py` - base-snapshot store (`sync_base/`).
- Create `src/splitsmith/sync/merge.py` - pure three-way merge engine.
- Create `src/splitsmith/sync/pull.py` - manifest diff + pull plan.
- Create `src/splitsmith/sync/run.py` - `run_sync` orchestration (pull -> merge -> push retry loop).
- Modify `src/splitsmith/sync/client.py` - `get_doc_manifest`, `get_doc`, versioned `put_doc`, `SyncVersionConflict`.
- Modify `src/splitsmith/sync/push.py` - send `expected_version`, record returned versions.
- Modify `src/splitsmith/ui_static/src/lib/api.ts` + `src/components/match/SyncCard.tsx` - remote-changes hint.

---

### Task 1: Audit event ids

Every `audit_events` append gets a unique `id`; the audit PUT stamps ids onto client-authored events that lack one. The merge engine (Task 5) unions events by this id.

**Files:**
- Modify: `src/splitsmith/ui/server.py` (helper next to `_now_iso` at :613; append sites - find all with the grep in Step 1)
- Test: `tests/test_audit_event_ids.py` (create)

**Interfaces:**
- Produces: `_new_event_id() -> str` (uuid4 hex) in `server.py`; every server-appended audit event dict now carries `"id"`; `PUT /api/shooters/{slug}/stages/{n}/audit` stamps missing `id`s before saving.

- [ ] **Step 1: Enumerate append sites**

Run: `grep -n '"kind":' src/splitsmith/ui/server.py`
Expected: the server-side event appends - `shot_detect_run` (~:2912), `coach_reclassify` (~:10547), `coach_patch` (~:10607). If the grep shows more event-dict appends, they all get the same one-line `"id"` addition in Step 4.

- [ ] **Step 2: Write the failing test**

```python
"""Audit event ids (bidirectional sync slice).

Every audit_events entry needs a unique ``id`` so the sync merge can
union event lists from two sides without double-appending. Server-side
appends stamp it at creation; the audit PUT stamps any client-authored
event that arrives without one (the SPA's "save" events).
"""

from fastapi.testclient import TestClient


def test_put_stage_audit_stamps_missing_event_ids(local_app_with_stage):
    """Client-authored events without ids get one; existing ids survive."""
    client: TestClient = local_app_with_stage
    payload = {
        "shots": [],
        "audit_events": [
            {"ts": "2026-08-10T10:00:00+00:00", "kind": "save", "payload": {}},
            {"id": "keepme", "ts": "2026-08-10T10:01:00+00:00", "kind": "save", "payload": {}},
        ],
    }
    resp = client.put("/api/shooters/main/stages/1/audit", json=payload)
    assert resp.status_code == 200
    events = resp.json()["audit_events"]
    assert events[1]["id"] == "keepme"
    new_id = events[0]["id"]
    assert isinstance(new_id, str) and len(new_id) == 32  # uuid4 hex
    # Re-reading returns the stamped doc, not the raw input.
    saved = client.get("/api/shooters/main/stages/1/audit").json()
    assert saved["audit_events"][0]["id"] == new_id
```

Fixture note: `tests/` already has fixtures that build a local-mode `TestClient` with one shooter + stage (see how `tests/test_sync_local_endpoints.py` and the coach tests construct theirs - reuse the same fixture module rather than writing a new app factory; name the fixture whatever the existing one is called and adjust the test signature).

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_audit_event_ids.py -v`
Expected: FAIL - `KeyError: 'id'` (events come back without ids).

- [ ] **Step 4: Implement**

In `server.py`, next to `_now_iso()` (:613):

```python
def _new_event_id() -> str:
    """Unique id for audit_events entries - the sync merge unions event
    lists by this id, so every event needs one at creation time. uuid4
    hex, not ULID: ordering comes from ``ts``, and the ulid package is a
    hosted-only extra while events are stamped on slim local installs too."""
    return uuid.uuid4().hex
```

(`import uuid` is likely already present; add it if not.)

At every append site found in Step 1, add the id as the first key, e.g. the shot_detect_run site becomes:

```python
events.append(
    {
        "id": _new_event_id(),
        "ts": _now_iso(),
        "kind": "shot_detect_run",
        "payload": {...},  # unchanged existing payload
    }
)
```

In `put_stage_audit` (:10240), after the classify-on-save block and before the version load:

```python
        # Sync merge unions audit_events by id (bidirectional sync
        # slice); the SPA authors events without one, so stamp them here
        # at the save boundary.
        events = payload.get("audit_events")
        if isinstance(events, list):
            for event in events:
                if isinstance(event, dict) and not event.get("id"):
                    event["id"] = _new_event_id()
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_audit_event_ids.py -v && pytest tests/ -k "coach or audit" -q`
Expected: PASS, no regressions in the coach/audit suites.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_audit_event_ids.py
git commit -m "feat(sync): stamp unique ids on audit_events entries"
```

---

### Task 2: Store manifest query

**Files:**
- Modify: `src/splitsmith/db/project_state.py`
- Test: `tests/test_project_state_store.py` (extend)

**Interfaces:**
- Produces: `ProjectStateStore.list_doc_meta(match_id) -> list[DocMeta]` where `DocMeta` is a small frozen dataclass `(doc_kind: str, slug: str | None, stage_number: int | None, version: int, updated_at: datetime)` defined in `project_state.py`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_project_state_store.py`, following its existing per-method pattern (it builds two stores for two user ids over an aiosqlite engine):

```python
async def test_list_doc_meta_returns_all_kinds(store_a):
    await store_a.save_match("m1", {"name": "x"}, expected_version=0)
    await store_a.save_project("m1", "anna", {"stages": []}, expected_version=0)
    await store_a.save_audit("m1", "anna", 3, {"shots": []}, expected_version=0)
    meta = await store_a.list_doc_meta("m1")
    keys = {(m.doc_kind, m.slug, m.stage_number) for m in meta}
    assert keys == {("match", None, None), ("project", "anna", None), ("audit", "anna", 3)}
    assert all(m.version == 1 for m in meta)
    assert all(m.updated_at is not None for m in meta)


async def test_list_doc_meta_tenant_isolation(store_a, store_b):
    await store_a.save_match("m1", {"name": "x"}, expected_version=0)
    assert await store_b.list_doc_meta("m1") == []
```

(Adjust fixture names to match the file's existing ones - it already has two-user fixtures for the isolation tests.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_project_state_store.py -k list_doc_meta -v`
Expected: FAIL - `AttributeError: 'ProjectStateStore' object has no attribute 'list_doc_meta'`.

- [ ] **Step 3: Implement**

In `project_state.py`:

```python
@dataclass(frozen=True)
class DocMeta:
    """Identity + version of one state doc, for the sync manifest."""

    doc_kind: str
    slug: str | None
    stage_number: int | None
    version: int
    updated_at: datetime
```

(add `from dataclasses import dataclass` to imports) and, in `ProjectStateStore` next to `list_audit_docs`:

```python
    async def list_doc_meta(self, match_id: str) -> list[DocMeta]:
        """Identity, version, and updated_at of every doc in a match.

        The sync pull manifest: desktop diffs these versions against the
        ones it recorded at last sync to find remotely-changed docs
        without shipping doc bodies. One indexed query, no doc payloads.
        """
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            StateDocRow.doc_kind,
                            StateDocRow.slug,
                            StateDocRow.stage_number,
                            StateDocRow.version,
                            StateDocRow.updated_at,
                        ).where(
                            StateDocRow.user_id == self._user_id,
                            StateDocRow.match_id == match_id,
                        )
                    )
                )
                .all()
            )
        return [DocMeta(*row) for row in rows]
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_project_state_store.py -v`
Expected: PASS (all, including the pre-existing isolation suite).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/db/project_state.py tests/test_project_state_store.py
git commit -m "feat(sync): state-doc manifest query on ProjectStateStore"
```

---

### Task 3: Hosted read routes + strict PUT versioning

**Files:**
- Modify: `src/splitsmith/ui/sync_api.py`
- Modify: `src/splitsmith/sync/client.py`
- Test: `tests/test_sync_api.py` (extend)

**Interfaces:**
- Consumes: `ProjectStateStore.list_doc_meta` (Task 2).
- Produces (HTTP): `GET /api/sync/matches/{id}/docs` -> `{"docs": [{doc_kind, slug, stage_number, version, updated_at}]}`; `GET .../docs/match|project/{slug}|audit/{slug}/{stage}` -> `{"doc": {...}, "version": int}` (404 when absent); `PUT` doc routes now require query param `expected_version: int` and 409 `{"detail": "version_conflict"}` on a lost race.
- Produces (client): `HostedSyncClient.get_doc_manifest(match_id) -> list[dict]`, `HostedSyncClient.get_doc(match_id, kind, slug, stage_number) -> tuple[dict, int]`, `put_doc(match_id, item, *, expected_version: int) -> int`, and `class SyncVersionConflict(SyncClientError)` raised on the PUT 409.

- [ ] **Step 1: Write the failing tests**

`tests/test_sync_api.py` already builds a hosted-mode test app with a desktop-token client and pushes docs; follow its fixtures. Add:

```python
async def test_doc_manifest_lists_versions(sync_client, adopted_match):
    await put_doc(sync_client, "match", body=MATCH_DOC, expected_version=0)
    await put_doc(sync_client, "project", slug="anna", body=PROJECT_DOC, expected_version=0)
    resp = await sync_client.get(f"/api/sync/matches/{MATCH_ID}/docs")
    assert resp.status_code == 200
    docs = resp.json()["docs"]
    by_key = {(d["doc_kind"], d["slug"], d["stage_number"]): d for d in docs}
    assert by_key[("match", None, None)]["version"] == 1
    assert by_key[("project", "anna", None)]["version"] == 1
    assert "updated_at" in by_key[("match", None, None)]


async def test_get_doc_roundtrip_and_404(sync_client, adopted_match):
    await put_doc(sync_client, "match", body=MATCH_DOC, expected_version=0)
    resp = await sync_client.get(f"/api/sync/matches/{MATCH_ID}/docs/match")
    assert resp.status_code == 200
    assert resp.json() == {"doc": MATCH_DOC, "version": 1}
    resp = await sync_client.get(f"/api/sync/matches/{MATCH_ID}/docs/audit/anna/9")
    assert resp.status_code == 404


async def test_put_doc_requires_expected_version(sync_client, adopted_match):
    resp = await sync_client.put(
        f"/api/sync/matches/{MATCH_ID}/docs/match", json=MATCH_DOC
    )  # no expected_version query param
    assert resp.status_code == 422


async def test_put_doc_version_conflict_409(sync_client, adopted_match):
    await put_doc(sync_client, "match", body=MATCH_DOC, expected_version=0)
    resp = await sync_client.put(
        f"/api/sync/matches/{MATCH_ID}/docs/match",
        params={"expected_version": 0},  # stale: row is at version 1
        json=MATCH_DOC,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "version_conflict"
```

Write a small module-level `put_doc(client, kind, *, slug=None, stage_number=None, body, expected_version)` helper in the test file (or extend the file's existing PUT helper) that always sends `params={"expected_version": ...}` - then update the file's pre-existing PUT tests to pass `expected_version` since the param is now required (no compat path).

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_sync_api.py -v`
Expected: new tests FAIL (404 on the GET routes; PUT without param currently 200s). Pre-existing PUT tests still pass until Step 3 flips the requirement, then they need their `expected_version` updates from Step 1.

- [ ] **Step 3: Implement routes**

In `sync_api.py` - response models:

```python
class SyncDocMeta(BaseModel):
    """One manifest row: doc identity + version."""

    doc_kind: str
    slug: str | None = None
    stage_number: int | None = None
    version: int
    updated_at: datetime


class SyncDocManifestResponse(BaseModel):
    """Response for ``GET /api/sync/matches/{match_id}/docs``."""

    docs: list[SyncDocMeta]


class SyncDocResponse(BaseModel):
    """Response for the three per-doc GET routes."""

    doc: dict[str, Any]
    version: int
```

(`from datetime import datetime` added to imports.)

Delete `_mirror_save` entirely and replace the save closure pattern in all three PUT handlers. Each PUT handler gains a required query parameter and calls the store directly; `StateConflictError` maps to the app-level 409 handler that already exists (`server.py` registers it -> `{"detail": {"code": "version_conflict", ...}}`), so the handler needs no try/except. `put_match_doc` becomes:

```python
@router.put("/matches/{match_id}/docs/match", response_model=SyncDocVersionResponse)
async def put_match_doc(
    match_id: str,
    expected_version: int,
    request: Request,
    body: dict[str, Any] = Body(...),
    user: Any = Depends(_current_user),
) -> SyncDocVersionResponse:
    """Upsert the match doc at ``expected_version`` (0 = create).

    409 ``version_conflict`` when the row moved on - the desktop client
    re-pulls, re-merges, and retries; the hosted side never resolves the
    race itself (that was the pre-pull ``_mirror_save`` clobber, deleted
    with the bidirectional slice).
    """
    _hosted_gate()
    await _resolve_mirror(request, match_id)
    try:
        match_model.Match.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    store = _project_state(request)
    version = await store.save_match(match_id, body, expected_version=expected_version)
    return SyncDocVersionResponse(version=version)
```

`put_project_doc` and `put_audit_doc` change identically (same `expected_version: int` param, direct `store.save_project(...)` / `store.save_audit(...)` call, docstring pointing at the match-doc one). Check whether `server.py`'s `StateConflictError` exception handler covers routes registered via this router (it does - app-level handlers are global); the docstring claim in Step 3 relies on it, and `test_put_doc_version_conflict_409` proves it.

New GET routes:

```python
@router.get("/matches/{match_id}/docs", response_model=SyncDocManifestResponse)
async def get_doc_manifest(
    match_id: str,
    request: Request,
    user: Any = Depends(_current_user),
) -> SyncDocManifestResponse:
    """Identity + version of every state doc in this mirror.

    The pull side of the bidirectional sync: desktop diffs these
    versions against the ones recorded at its last sync and GETs only
    the docs that moved. Doc bodies never ride in the manifest.
    """
    _hosted_gate()
    await _resolve_mirror(request, match_id)
    store = _project_state(request)
    meta = await store.list_doc_meta(match_id)
    return SyncDocManifestResponse(
        docs=[
            SyncDocMeta(
                doc_kind=m.doc_kind,
                slug=m.slug,
                stage_number=m.stage_number,
                version=m.version,
                updated_at=m.updated_at,
            )
            for m in meta
        ]
    )


@router.get("/matches/{match_id}/docs/match", response_model=SyncDocResponse)
async def get_match_doc(
    match_id: str, request: Request, user: Any = Depends(_current_user)
) -> SyncDocResponse:
    """Fetch the match doc + its version. 404 when it does not exist."""
    _hosted_gate()
    await _resolve_mirror(request, match_id)
    doc, version = await _project_state(request).load_match(match_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="not found")
    return SyncDocResponse(doc=doc, version=version)


@router.get("/matches/{match_id}/docs/project/{slug}", response_model=SyncDocResponse)
async def get_project_doc(
    match_id: str, slug: str, request: Request, user: Any = Depends(_current_user)
) -> SyncDocResponse:
    """Fetch one shooter's project doc + version. 404 when absent."""
    _hosted_gate()
    await _resolve_mirror(request, match_id)
    doc, version = await _project_state(request).load_project(match_id, slug)
    if doc is None:
        raise HTTPException(status_code=404, detail="not found")
    return SyncDocResponse(doc=doc, version=version)


@router.get("/matches/{match_id}/docs/audit/{slug}/{stage_number}", response_model=SyncDocResponse)
async def get_audit_doc(
    match_id: str,
    slug: str,
    stage_number: int,
    request: Request,
    user: Any = Depends(_current_user),
) -> SyncDocResponse:
    """Fetch one stage's audit doc + version. 404 when absent."""
    _hosted_gate()
    await _resolve_mirror(request, match_id)
    doc, version = await _project_state(request).load_audit(match_id, slug, stage_number)
    if doc is None:
        raise HTTPException(status_code=404, detail="not found")
    return SyncDocResponse(doc=doc, version=version)
```

Route ordering note: FastAPI matches in registration order; register `GET /docs` and `GET /docs/match` before the parameterized `GET /docs/project/{slug}` etc., mirroring how the PUTs are laid out. `/docs/match` is a literal segment so there is no capture ambiguity.

- [ ] **Step 4: Implement client methods**

In `sync/client.py`:

```python
class SyncVersionConflict(SyncClientError):
    """A doc PUT lost the optimistic-lock race (hosted 409
    ``version_conflict``) - the caller re-pulls, re-merges, retries."""
```

Replace `put_doc` and add the two GETs:

```python
    def put_doc(self, match_id: str, item: DocItem, *, expected_version: int) -> int:
        """Upsert one doc at ``expected_version`` (0 = create), returning
        the version the hosted side assigned. Raises
        :class:`SyncVersionConflict` when the row moved on since the
        manifest/pull this ``expected_version`` came from."""
        resp = self._http.put(
            self._doc_url(match_id, item),
            params={"expected_version": expected_version},
            json=item.body,
        )
        if resp.status_code == 409:
            raise SyncVersionConflict(
                f"doc {doc_identity_key(item.kind, item.slug, item.stage_number)} "
                "changed on the hosted side during this sync"
            )
        self._raise_for_status(resp)
        return resp.json()["version"]

    def get_doc_manifest(self, match_id: str) -> list[dict]:
        """Identity + version of every hosted doc for this match."""
        resp = self._http.get(f"/api/sync/matches/{match_id}/docs")
        self._raise_for_status(resp)
        return resp.json()["docs"]

    def get_doc(
        self, match_id: str, kind: str, slug: str | None, stage_number: int | None
    ) -> tuple[dict, int]:
        """Fetch one doc body + version by identity."""
        item = DocItem(kind=kind, slug=slug, stage_number=stage_number, body={})
        resp = self._http.get(self._doc_url(match_id, item))
        self._raise_for_status(resp)
        payload = resp.json()
        return payload["doc"], payload["version"]
```

(`from .plan import doc_identity_key` joins the existing plan imports. `DocItem(body={})` is only a URL-builder vehicle; if that reads too cute, extract `_doc_url`'s path logic into a small `_doc_path(kind, slug, stage_number)` and have both call it.)

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_sync_api.py -v`
Expected: all PASS. Do NOT patch `push.py`'s `put_doc` call site here - threading real versions through the executor needs Task 4's `doc_versions` field and is Task 6's job, and any interim `expected_version=0` bridge would silently break re-pushes. `tests/test_sync_push.py` and `tests/test_sync_integration.py` are expected red from this commit until Task 6 lands; say so in the commit body (below).

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui/sync_api.py src/splitsmith/sync/client.py tests/test_sync_api.py
git commit -m "feat(sync): hosted doc manifest + GET routes, version-guarded PUTs

test_sync_push/test_sync_integration are red until the push executor
threads expected_version (next commits); tracked in the sync-slice plan."
```

---

### Task 4: SyncState v2 + base snapshots

**Files:**
- Modify: `src/splitsmith/sync/state.py`
- Create: `src/splitsmith/sync/base.py`
- Test: `tests/test_sync_state_v2.py` (create)

**Interfaces:**
- Produces: `SyncState.doc_versions: dict[str, int]` (doc identity key -> last-seen remote version), `schema_version` default 2; `sync/base.py` with `load_base_doc(match_root, key) -> dict | None`, `save_base_doc(match_root, key, body) -> None`, `BASE_DIR = "sync_base"`. Keys are the existing `doc_identity_key` strings (`"match"`, `"project/<slug>"`, `"audit/<slug>/<stage>"`), mapped to nested files `sync_base/match.json`, `sync_base/project/<slug>.json`, `sync_base/audit/<slug>/<stage>.json`.

- [ ] **Step 1: Write the failing tests**

```python
"""SyncState v2 (doc_versions) + sync_base/ snapshot store."""

from pathlib import Path

from splitsmith.sync.base import load_base_doc, save_base_doc
from splitsmith.sync.state import SyncState, load_sync_state, save_sync_state


def test_sync_state_v2_roundtrip(tmp_path: Path):
    state = SyncState()
    assert state.schema_version == 2
    state.doc_versions["project/anna"] = 4
    save_sync_state(tmp_path, state)
    loaded = load_sync_state(tmp_path)
    assert loaded.doc_versions == {"project/anna": 4}


def test_sync_state_v1_file_loads_with_empty_versions(tmp_path: Path):
    (tmp_path / "sync_state.json").write_text(
        '{"schema_version": 1, "items": {}, "doc_hashes": {"match": "ab"}}',
        encoding="utf-8",
    )
    loaded = load_sync_state(tmp_path)
    assert loaded.doc_versions == {}
    assert loaded.doc_hashes == {"match": "ab"}


def test_base_doc_roundtrip_and_missing(tmp_path: Path):
    assert load_base_doc(tmp_path, "audit/anna/3") is None
    save_base_doc(tmp_path, "audit/anna/3", {"shots": [1]})
    assert load_base_doc(tmp_path, "audit/anna/3") == {"shots": [1]}
    assert (tmp_path / "sync_base" / "audit" / "anna" / "3.json").exists()
    save_base_doc(tmp_path, "match", {"name": "x"})
    assert (tmp_path / "sync_base" / "match.json").exists()


def test_base_doc_corrupt_reads_as_missing(tmp_path: Path):
    p = tmp_path / "sync_base" / "match.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    assert load_base_doc(tmp_path, "match") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_sync_state_v2.py -v`
Expected: FAIL - `ModuleNotFoundError: splitsmith.sync.base`, then the schema_version assertion.

- [ ] **Step 3: Implement**

`state.py`: change the model (and extend the module docstring with a `doc_versions` paragraph mirroring the `doc_hashes` one):

```python
class SyncState(BaseModel):
    """The full local sync digest cache for one match."""

    schema_version: int = 2
    last_synced_at: datetime | None = None
    items: dict[str, SyncedItem] = Field(default_factory=dict)  # remote key -> digest
    #: doc identity ("match" / "project/<slug>" / "audit/<slug>/<stage>") ->
    #: sha256 of the last-pushed canonical JSON body. Absent key = push it.
    doc_hashes: dict[str, str] = Field(default_factory=dict)
    #: doc identity -> the hosted ``state_docs.version`` last seen for it
    #: (recorded from PUT responses and pulls). The pull planner diffs
    #: the hosted manifest against this; the push executor sends it as
    #: ``expected_version``. Absent key = never seen (expected_version 0).
    doc_versions: dict[str, int] = Field(default_factory=dict)
```

(v1 files load fine: pydantic defaults the missing field, and `schema_version` is data, not a gate - same lenient posture `load_sync_state` already has.)

New `src/splitsmith/sync/base.py`:

```python
"""Base-snapshot store for the three-way sync merge.

``sync_base/`` under the match root holds each doc's body exactly as of
the last completed sync leg - the common ancestor the merge diffs both
sides against. Updated at two points in a sync run: after applying a
pull (base := the pulled remote snapshot) and after each successful doc
PUT (base := the pushed body). A missing or corrupt file reads as "never
synced", which the merge treats as an empty base: everything on each
side counts as that side's change - correct, just less discriminating.

Keys are :func:`splitsmith.sync.plan.doc_identity_key` strings; the
slash-separated segments become nested directories, so the layout reads
as ``sync_base/match.json``, ``sync_base/project/<slug>.json``,
``sync_base/audit/<slug>/<stage>.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..match_project import atomic_write_json

BASE_DIR = "sync_base"


def _base_path(match_root: Path, key: str) -> Path:
    return (match_root / BASE_DIR / key).with_suffix(".json")


def load_base_doc(match_root: Path, key: str) -> dict | None:
    """The base snapshot for ``key``, or None when absent/corrupt."""
    path = _base_path(match_root, key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_base_doc(match_root: Path, key: str, body: dict) -> None:
    """Atomically persist ``body`` as the base snapshot for ``key``."""
    path = _base_path(match_root, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, body)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_sync_state_v2.py tests/test_sync_plan.py -v`
Expected: PASS (plan tests confirm no doc_hashes regression).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/sync/state.py src/splitsmith/sync/base.py tests/test_sync_state_v2.py
git commit -m "feat(sync): sync_state v2 doc_versions + sync_base snapshot store"
```

---

### Task 5: Three-way merge engine

Pure functions, no I/O. The heart of the slice; the conflict matrix lives here.

**Files:**
- Create: `src/splitsmith/sync/merge.py`
- Test: `tests/test_sync_merge.py` (create)

**Interfaces:**
- Consumes: `COACH_FIELDS` from `splitsmith.coach` (`("interval_class", "interval_class_source", "improvement_flag", "coaching_note")`).
- Produces:
  - `@dataclass MergeConflict(doc_key: str, unit: str, winner: str)` - winner is `"local"` or `"remote"`.
  - `@dataclass MergeResult(doc: dict, conflicts: list[MergeConflict], notes: list[str], reprocess_video_ids: list[str], changed_vs_local: bool)`.
  - `merge_project_doc(base: dict | None, local: dict, remote: dict, *, doc_key: str, local_ts: datetime, remote_ts: datetime) -> MergeResult`.
  - `merge_audit_doc(base: dict | None, local: dict, remote: dict, *, doc_key: str, local_ts: datetime, remote_ts: datetime) -> MergeResult`.
- Merge semantics (from the spec): start from a deep copy of local; per whitelisted unit do three-way resolution; beep unit = every `beep_*`-prefixed key on a video dict, video identity = `video_id` within stage identity = `stage_number`, taking remote's beep unit also applies derivation invalidation (`processed["trim"] = False`, and `processed["shot_detect"] = False` on `role == "primary"`, `processed["beep"] = remote beep_time is not None`) and records the video in `reprocess_video_ids`; coach unit = `COACH_FIELDS` per shot keyed by `shot_number`; `audit_events` unions by `id` (fallback key `(ts, kind)` for legacy id-less events), result sorted by `ts`; remote-side structural changes (videos/shots/stages present remotely but not locally, or vice versa) are desktop-authoritative - local wins, a note is recorded.

- [ ] **Step 1: Write the failing tests (the conflict matrix)**

```python
"""Three-way merge engine conflict matrix (bidirectional sync slice)."""

from datetime import UTC, datetime

from splitsmith.sync.merge import merge_audit_doc, merge_project_doc

T_OLD = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
T_NEW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def _video(**over):
    v = {
        "video_id": "vid1",
        "role": "primary",
        "beep_time": 1.0,
        "beep_source": "auto",
        "beep_reviewed": False,
        "beep_confidence": 0.5,
        "processed": {"beep": True, "trim": True, "shot_detect": True},
    }
    v.update(over)
    return v


def _project(video):
    return {"stages": [{"stage_number": 3, "videos": [video]}]}


def test_remote_only_beep_change_wins_and_invalidates():
    base = _project(_video())
    local = _project(_video())
    remote = _project(_video(beep_time=2.5, beep_source="manual", beep_reviewed=True))
    r = merge_project_doc(base, local, remote, doc_key="project/anna", local_ts=T_OLD, remote_ts=T_NEW)
    v = r.doc["stages"][0]["videos"][0]
    assert v["beep_time"] == 2.5 and v["beep_source"] == "manual"
    assert v["processed"] == {"beep": True, "trim": False, "shot_detect": False}
    assert r.reprocess_video_ids == ["vid1"]
    assert r.conflicts == [] and r.changed_vs_local is True


def test_local_only_beep_change_kept_no_reprocess():
    base = _project(_video())
    local = _project(_video(beep_time=9.9, beep_source="manual"))
    remote = _project(_video())
    r = merge_project_doc(base, local, remote, doc_key="project/anna", local_ts=T_NEW, remote_ts=T_OLD)
    assert r.doc == local and r.reprocess_video_ids == [] and r.changed_vs_local is False


def test_both_same_value_no_conflict():
    base = _project(_video())
    changed = _video(beep_time=2.5, beep_source="manual")
    r = merge_project_doc(
        _project(_video()), _project(changed), _project(dict(changed)),
        doc_key="project/anna", local_ts=T_OLD, remote_ts=T_NEW,
    )
    assert r.conflicts == [] and r.reprocess_video_ids == []


def test_true_conflict_remote_newer_wins_and_logs():
    base = _project(_video())
    local = _project(_video(beep_time=5.0, beep_source="manual"))
    remote = _project(_video(beep_time=2.5, beep_source="manual"))
    r = merge_project_doc(base, local, remote, doc_key="project/anna", local_ts=T_OLD, remote_ts=T_NEW)
    assert r.doc["stages"][0]["videos"][0]["beep_time"] == 2.5
    assert len(r.conflicts) == 1 and r.conflicts[0].winner == "remote"
    assert r.reprocess_video_ids == ["vid1"]


def test_true_conflict_local_newer_wins_and_logs():
    base = _project(_video())
    local = _project(_video(beep_time=5.0, beep_source="manual"))
    remote = _project(_video(beep_time=2.5, beep_source="manual"))
    r = merge_project_doc(base, local, remote, doc_key="project/anna", local_ts=T_NEW, remote_ts=T_OLD)
    assert r.doc["stages"][0]["videos"][0]["beep_time"] == 5.0
    assert len(r.conflicts) == 1 and r.conflicts[0].winner == "local"
    assert r.reprocess_video_ids == []


def test_empty_base_treats_both_sides_as_changed():
    local = _project(_video(beep_time=5.0))
    remote = _project(_video(beep_time=2.5))
    r = merge_project_doc(None, local, remote, doc_key="project/anna", local_ts=T_NEW, remote_ts=T_OLD)
    assert r.doc["stages"][0]["videos"][0]["beep_time"] == 5.0
    assert len(r.conflicts) == 1


def test_remote_extra_video_is_noted_not_merged():
    base = _project(_video())
    local = _project(_video())
    remote = {"stages": [{"stage_number": 3, "videos": [_video(), _video(video_id="vid2")]}]}
    r = merge_project_doc(base, local, remote, doc_key="project/anna", local_ts=T_OLD, remote_ts=T_NEW)
    assert len(r.doc["stages"][0]["videos"]) == 1
    assert any("vid2" in n for n in r.notes)


def test_non_whitelisted_remote_change_local_wins_with_note():
    base = _project(_video())
    local = _project(_video())
    remote = _project(_video())
    remote["stages"][0]["skipped"] = True  # not in any whitelist
    r = merge_project_doc(base, local, remote, doc_key="project/anna", local_ts=T_OLD, remote_ts=T_NEW)
    assert "skipped" not in r.doc["stages"][0]
    assert any("non-whitelisted" in n for n in r.notes)


# -- audit docs ------------------------------------------------------


def _shot(n, **over):
    s = {"shot_number": n, "time": float(n), "interval_class": "split", "interval_class_source": "auto"}
    s.update(over)
    return s


def _audit(shots, events):
    return {"shots": shots, "audit_events": events}


E1 = {"id": "e1", "ts": "2026-08-10T10:00:00+00:00", "kind": "save", "payload": {}}
E2 = {"id": "e2", "ts": "2026-08-10T11:00:00+00:00", "kind": "coach_patch", "payload": {}}
E3 = {"id": "e3", "ts": "2026-08-10T12:00:00+00:00", "kind": "accept", "payload": {}}


def test_event_union_by_id_sorted_by_ts():
    base = _audit([], [E1])
    local = _audit([], [E1, E2])
    remote = _audit([], [E1, E3])
    r = merge_audit_doc(base, local, remote, doc_key="audit/anna/3", local_ts=T_OLD, remote_ts=T_NEW)
    assert [e["id"] for e in r.doc["audit_events"]] == ["e1", "e2", "e3"]
    assert r.conflicts == [] and r.changed_vs_local is True


def test_event_union_legacy_idless_dedupes_by_ts_kind():
    legacy = {"ts": "2026-08-10T09:00:00+00:00", "kind": "save", "payload": {}}
    base = _audit([], [legacy])
    local = _audit([], [legacy])
    remote = _audit([], [dict(legacy), E3])  # same legacy event round-tripped, plus one new
    r = merge_audit_doc(base, local, remote, doc_key="audit/anna/3", local_ts=T_OLD, remote_ts=T_NEW)
    ids = [(e.get("id"), e["ts"], e["kind"]) for e in r.doc["audit_events"]]
    assert len(ids) == 2  # legacy not doubled


def test_coach_fields_remote_only_change_wins():
    base = _audit([_shot(1)], [])
    local = _audit([_shot(1)], [])
    remote = _audit([_shot(1, interval_class="draw", interval_class_source="manual", coaching_note="slow")], [])
    r = merge_audit_doc(base, local, remote, doc_key="audit/anna/3", local_ts=T_OLD, remote_ts=T_NEW)
    s = r.doc["shots"][0]
    assert s["interval_class"] == "draw" and s["coaching_note"] == "slow"


def test_coach_conflict_lww_and_shot_membership_is_local():
    base = _audit([_shot(1)], [])
    local = _audit([_shot(1, coaching_note="mine"), _shot(2)], [])
    remote = _audit([_shot(1, coaching_note="theirs")], [])
    r = merge_audit_doc(base, local, remote, doc_key="audit/anna/3", local_ts=T_OLD, remote_ts=T_NEW)
    assert r.doc["shots"][0]["coaching_note"] == "theirs"  # remote newer
    assert len(r.doc["shots"]) == 2  # local shot list authoritative
    assert len(r.conflicts) == 1


def test_audit_non_whitelisted_remote_change_noted_local_wins():
    base = _audit([_shot(1)], [])
    local = _audit([_shot(1)], [])
    remote = _audit([_shot(1, time=9.9)], [])  # shot time is not whitelisted
    r = merge_audit_doc(base, local, remote, doc_key="audit/anna/3", local_ts=T_OLD, remote_ts=T_NEW)
    assert r.doc["shots"][0]["time"] == 1.0
    assert any("non-whitelisted" in n for n in r.notes)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_sync_merge.py -v`
Expected: FAIL - `ModuleNotFoundError: splitsmith.sync.merge`.

- [ ] **Step 3: Implement**

`src/splitsmith/sync/merge.py`:

```python
"""Pure three-way merge for the bidirectional sync slice.

Desktop is authoritative for everything except the narrow whitelist
mobile is allowed to write (spec 2026-08-10-bidirectional-sync-design):
per-video beep field-groups in project docs, per-shot coach fields and
the append-only ``audit_events`` log in audit docs. Each merge starts
from a deep copy of the local doc and resolves whitelisted units
three-way against the base snapshot: changed on one side wins outright;
changed on both is a true conflict resolved last-writer-wins by doc
timestamp and always surfaced on :attr:`MergeResult.conflicts` - never
silent. Structural membership (stages, videos, shots) is
desktop-authoritative: remote-only additions/removals are noted, not
merged.

Taking a remote beep group applies the same derivation invalidation a
local beep override does (``_apply_beep_override`` in ui/server.py):
trim and (for primaries) shot_detect flags drop, and the video lands on
:attr:`MergeResult.reprocess_video_ids` so the sync report can say "N
videos need re-processing". Hosted never re-derives for mirrors - raw
media never leaves the desktop - so this is where re-derivation gets
scheduled.

No I/O in this module; callers own loading, timestamps, and writes.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime

from ..coach import COACH_FIELDS

#: Every key on a video dict starting with this prefix moves as one
#: atomic merge unit - beep_time without its confidence/candidates
#: would be incoherent. Prefix rule, not a field list, so a future
#: beep_* field never silently splits the group.
_BEEP_PREFIX = "beep_"


@dataclass(frozen=True)
class MergeConflict:
    """One true conflict: both sides changed the same unit since base."""

    doc_key: str
    unit: str
    winner: str  # "local" | "remote"


@dataclass
class MergeResult:
    """Outcome of merging one doc."""

    doc: dict
    conflicts: list[MergeConflict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    reprocess_video_ids: list[str] = field(default_factory=list)
    changed_vs_local: bool = False


def _resolve_unit(
    base_u: object, local_u: object, remote_u: object, *, local_ts: datetime, remote_ts: datetime
) -> tuple[str, bool]:
    """Three-way verdict for one unit: (winner, is_conflict).

    An empty/missing base makes both sides look changed - correct, just
    less discriminating (spec: missing base = never synced).
    """
    local_changed = local_u != base_u
    remote_changed = remote_u != base_u
    if not remote_changed:
        return "local", False
    if not local_changed:
        return "remote", False
    if local_u == remote_u:
        return "local", False
    return ("remote" if remote_ts > local_ts else "local"), True


def _beep_group(video: dict) -> dict:
    return {k: v for k, v in video.items() if k.startswith(_BEEP_PREFIX)}


def _videos_by_id(doc: dict | None) -> dict[tuple[int, str], dict]:
    out: dict[tuple[int, str], dict] = {}
    for stage in (doc or {}).get("stages") or []:
        if not isinstance(stage, dict):
            continue
        for video in stage.get("videos") or []:
            if isinstance(video, dict) and video.get("video_id"):
                out[(stage.get("stage_number"), video["video_id"])] = video
    return out


def merge_project_doc(
    base: dict | None,
    local: dict,
    remote: dict,
    *,
    doc_key: str,
    local_ts: datetime,
    remote_ts: datetime,
) -> MergeResult:
    """Merge one shooter's project doc (beep groups per video)."""
    merged = copy.deepcopy(local)
    result = MergeResult(doc=merged)

    base_videos = _videos_by_id(base)
    remote_videos = _videos_by_id(remote)
    merged_videos = _videos_by_id(merged)

    for key, merged_video in merged_videos.items():
        stage_number, video_id = key
        remote_video = remote_videos.get(key)
        if remote_video is None:
            continue  # remote lacks it; local membership is authoritative
        base_u = _beep_group(base_videos.get(key, {}))
        local_u = _beep_group(merged_video)
        remote_u = _beep_group(remote_video)
        winner, is_conflict = _resolve_unit(
            base_u, local_u, remote_u, local_ts=local_ts, remote_ts=remote_ts
        )
        unit_name = f"stage {stage_number} video {video_id} beep"
        if is_conflict:
            result.conflicts.append(MergeConflict(doc_key=doc_key, unit=unit_name, winner=winner))
        if winner == "remote" and remote_u != local_u:
            for k in list(merged_video):
                if k.startswith(_BEEP_PREFIX):
                    del merged_video[k]
            merged_video.update(copy.deepcopy(remote_u))
            processed = merged_video.setdefault("processed", {})
            processed["beep"] = remote_u.get("beep_time") is not None
            processed["trim"] = False
            if merged_video.get("role") == "primary":
                processed["shot_detect"] = False
            result.reprocess_video_ids.append(video_id)

    for key in remote_videos.keys() - merged_videos.keys():
        result.notes.append(
            f"{doc_key}: remote has video {key[1]} in stage {key[0]} that local lacks - "
            "video membership is desktop-owned; ignored"
        )
    _note_non_whitelisted_remote_changes(result, base, merged, remote, doc_key)
    result.changed_vs_local = merged != local
    return result


def _event_key(event: dict) -> object:
    """Union identity for one audit event: its id, else (ts, kind) for
    legacy events written before ids existed."""
    return event.get("id") or (event.get("ts"), event.get("kind"))


def _shots_by_number(doc: dict | None) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for shot in (doc or {}).get("shots") or []:
        if isinstance(shot, dict) and shot.get("shot_number") is not None:
            out[int(shot["shot_number"])] = shot
    return out


def _coach_unit(shot: dict) -> dict:
    return {k: shot.get(k) for k in COACH_FIELDS}


def merge_audit_doc(
    base: dict | None,
    local: dict,
    remote: dict,
    *,
    doc_key: str,
    local_ts: datetime,
    remote_ts: datetime,
) -> MergeResult:
    """Merge one stage's audit doc (event union + coach fields per shot)."""
    merged = copy.deepcopy(local)
    result = MergeResult(doc=merged)

    # Append-only event union by id, ordered by ts (stable for ties).
    # Only rewrite the list when remote actually adds events - re-sorting
    # a legacy out-of-ts-order local list on its own would churn the doc
    # (and trigger a push) with no remote change to justify it.
    local_events = [e for e in merged.get("audit_events") or [] if isinstance(e, dict)]
    seen = {_event_key(e) for e in local_events}
    remote_new = [
        e
        for e in (remote.get("audit_events") or [])
        if isinstance(e, dict) and _event_key(e) not in seen
    ]
    if remote_new:
        merged["audit_events"] = sorted(
            local_events + copy.deepcopy(remote_new), key=lambda e: str(e.get("ts") or "")
        )

    base_shots = _shots_by_number(base)
    remote_shots = _shots_by_number(remote)
    for shot_number, merged_shot in _shots_by_number(merged).items():
        remote_shot = remote_shots.get(shot_number)
        if remote_shot is None:
            continue
        winner, is_conflict = _resolve_unit(
            _coach_unit(base_shots.get(shot_number, {})),
            _coach_unit(merged_shot),
            _coach_unit(remote_shot),
            local_ts=local_ts,
            remote_ts=remote_ts,
        )
        unit_name = f"shot {shot_number} coach"
        if is_conflict:
            result.conflicts.append(MergeConflict(doc_key=doc_key, unit=unit_name, winner=winner))
        if winner == "remote" and _coach_unit(remote_shot) != _coach_unit(merged_shot):
            for k in COACH_FIELDS:
                if k in remote_shot:
                    merged_shot[k] = copy.deepcopy(remote_shot[k])
                else:
                    merged_shot.pop(k, None)

    for shot_number in remote_shots.keys() - _shots_by_number(merged).keys():
        result.notes.append(
            f"{doc_key}: remote has shot {shot_number} that local lacks - "
            "shot membership is desktop-owned; ignored"
        )

    # Same tripwire as the project merge: remote edits outside the audit
    # whitelist (events + coach fields) should be impossible while the
    # mirror write gate is closed - note them loudly, local wins.
    def _strip_audit(doc: dict | None) -> dict:
        clone = copy.deepcopy(doc or {})
        clone.pop("audit_events", None)
        for shot in clone.get("shots") or []:
            if isinstance(shot, dict):
                for k in COACH_FIELDS:
                    shot.pop(k, None)
        return clone

    if base is not None and _strip_audit(remote) != _strip_audit(base):
        result.notes.append(
            f"{doc_key}: remote changed non-whitelisted audit fields; local wins "
            "(mirror write gate should make this impossible - investigate)"
        )

    result.changed_vs_local = merged != local
    return result


def _note_non_whitelisted_remote_changes(
    result: MergeResult, base: dict | None, merged: dict, remote: dict, doc_key: str
) -> None:
    """Tripwire for remote edits outside the whitelist.

    While the mirror write gate is closed nothing hosted-side can touch
    non-whitelisted fields, so any diff here is a bug or a future
    surface shipping without a whitelist entry - worth a loud note, not
    silence. Comparison trick: strip the whitelisted units from both
    docs and compare the rest against base's rest.
    """
    if base is None:
        return

    def _strip(doc: dict) -> dict:
        clone = copy.deepcopy(doc)
        for stage in clone.get("stages") or []:
            if not isinstance(stage, dict):
                continue
            for video in stage.get("videos") or []:
                if isinstance(video, dict):
                    for k in list(video):
                        if k.startswith(_BEEP_PREFIX):
                            del video[k]
                    video.pop("processed", None)
        return clone

    if _strip(remote) != _strip(base) and _strip(remote) != _strip(merged):
        result.notes.append(
            f"{doc_key}: remote changed non-whitelisted fields; local wins "
            "(mirror write gate should make this impossible - investigate)"
        )
```

- [ ] **Step 4: Run tests, iterate to green**

Run: `pytest tests/test_sync_merge.py -v`
Expected: PASS (all 14). The two strip-compare tripwires are the subtlest part - if a `non_whitelisted` test fails, debug the strip helpers first.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/sync/merge.py tests/test_sync_merge.py
git commit -m "feat(sync): pure three-way merge engine with conflict matrix"
```

---

### Task 6: Pull plan + run_sync orchestration

**Files:**
- Create: `src/splitsmith/sync/pull.py`
- Create: `src/splitsmith/sync/run.py`
- Modify: `src/splitsmith/sync/push.py`
- Modify: `src/splitsmith/ui/server.py` (`_run_sync_match`, ~:3434)
- Test: `tests/test_sync_pull.py` (create), `tests/test_sync_push.py` + `tests/test_sync_integration.py` (update)

**Interfaces:**
- Consumes: Tasks 3-5 (`get_doc_manifest`/`get_doc`/`SyncVersionConflict`, `SyncState.doc_versions`, `load_base_doc`/`save_base_doc`, `merge_project_doc`/`merge_audit_doc`).
- Produces:
  - `pull.py`: `@dataclass RemoteDoc(kind: str, slug: str | None, stage_number: int | None, version: int, updated_at: datetime)`, `plan_pull(manifest: list[dict], sync_state: SyncState) -> list[RemoteDoc]` (entries whose version differs from `doc_versions`), `remote_doc_key(rd) -> str` (via `doc_identity_key`).
  - `run.py`: `run_sync(match_root, *, client, on_progress, timer) -> SyncReport` and `format_sync_message(report) -> str`. `SyncReport` extends `PushReport` with `pulled: int = 0`, `merged: int = 0`, `conflicts: list[dict] = []` (dataclass-dumped `MergeConflict`s), `notes: list[str] = []`, `reprocess_videos: int = 0`, `attempts: int = 1`.
  - `push.py`: `run_push(..., )` docs phase sends `expected_version=sync_state.doc_versions.get(key, 0)`, records the returned version into `doc_versions[key]`, saves the pushed body as base via a new optional `match_root`-scoped hook - concretely: push already has `match_root`; after a successful PUT add `save_base_doc(match_root, key, doc.body)` next to the existing `doc_hashes` record.
- `run_sync` phases (all timed like push's, same `_timed_phase` helper - move it to `run.py` or import from `push.py`): `preflight` (build_push_plan for errors only) -> `ensure_match` -> up to 3 attempts of [`pull` -> `merge` -> push's `plan`+`media`+`docs`], retrying only on `SyncVersionConflict`.

- [ ] **Step 1: Write failing tests for plan_pull**

```python
"""Pull planning: manifest diff against recorded doc_versions."""

from splitsmith.sync.pull import plan_pull, remote_doc_key
from splitsmith.sync.state import SyncState

M = [
    {"doc_kind": "match", "slug": None, "stage_number": None, "version": 3, "updated_at": "2026-08-10T10:00:00+00:00"},
    {"doc_kind": "project", "slug": "anna", "stage_number": None, "version": 7, "updated_at": "2026-08-10T10:00:00+00:00"},
    {"doc_kind": "audit", "slug": "anna", "stage_number": 3, "version": 2, "updated_at": "2026-08-10T10:00:00+00:00"},
]


def test_plan_pull_diffs_versions():
    state = SyncState(doc_versions={"match": 3, "project/anna": 6})
    changed = plan_pull(M, state)
    keys = {remote_doc_key(rd) for rd in changed}
    assert keys == {"project/anna", "audit/anna/3"}  # match unchanged; audit never seen


def test_plan_pull_empty_manifest():
    assert plan_pull([], SyncState()) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_sync_pull.py -v`
Expected: FAIL - `ModuleNotFoundError: splitsmith.sync.pull`.

- [ ] **Step 3: Implement pull.py**

```python
"""Pull planning for the bidirectional sync (docs only - media never
flows hosted-to-desktop; desktop re-derives instead, see the slice spec).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .plan import doc_identity_key
from .state import SyncState


@dataclass(frozen=True)
class RemoteDoc:
    """One remotely-changed doc identity from the hosted manifest."""

    kind: str
    slug: str | None
    stage_number: int | None
    version: int
    updated_at: datetime


def remote_doc_key(rd: RemoteDoc) -> str:
    return doc_identity_key(rd.kind, rd.slug, rd.stage_number)


def plan_pull(manifest: list[dict], sync_state: SyncState) -> list[RemoteDoc]:
    """Manifest entries whose version differs from the recorded one.

    A key absent from ``doc_versions`` means "never seen" - pull it.
    Equality (not less-than) is deliberate: versions only move forward,
    and a recorded version that is somehow *ahead* of the manifest means
    local state is confused - re-pulling and re-merging is the safe
    answer either way.
    """
    changed: list[RemoteDoc] = []
    for entry in manifest:
        rd = RemoteDoc(
            kind=entry["doc_kind"],
            slug=entry.get("slug"),
            stage_number=entry.get("stage_number"),
            version=entry["version"],
            updated_at=datetime.fromisoformat(entry["updated_at"]),
        )
        if sync_state.doc_versions.get(remote_doc_key(rd)) != rd.version:
            changed.append(rd)
    return changed
```

Run: `pytest tests/test_sync_pull.py -v` -> PASS. Commit checkpoint:

```bash
git add src/splitsmith/sync/pull.py tests/test_sync_pull.py
git commit -m "feat(sync): pull planning via manifest version diff"
```

- [ ] **Step 4: Write failing orchestration tests**

New section in `tests/test_sync_pull.py`. The one piece to borrow rather than invent: a helper that builds a minimal valid local match tree (match.json with a match_id + one shooter with one stage/video/audit doc) - `tests/test_sync_push.py` already builds exactly this for `run_push`; copy or import its builder as `make_synced_match(tmp_path) -> Path` and adjust only if its shape differs. Everything else below is complete:

```python
from datetime import UTC, datetime

import httpx
import pytest

from splitsmith.sync.base import load_base_doc
from splitsmith.sync.client import SyncClientError, SyncVersionConflict
from splitsmith.sync.plan import doc_identity_key
from splitsmith.sync.run import run_sync
from splitsmith.sync.state import load_sync_state


class FakeSyncClient:
    """In-memory HostedSyncClient stand-in - no HTTP.

    ``docs`` maps doc identity key -> (body, version); the manifest and
    get_doc serve from it, put_doc version-bumps into it. ``fail_puts``
    scripts the first N put_doc calls to raise SyncVersionConflict.
    """

    def __init__(self) -> None:
        self.docs: dict[str, tuple[dict, int]] = {}
        self.put_calls: list[tuple[str, int]] = []
        self.fail_puts = 0
        self.get_doc_error: Exception | None = None

    def _identity(self, key: str) -> tuple[str, str | None, int | None]:
        parts = key.split("/")
        if parts[0] == "match":
            return "match", None, None
        if parts[0] == "project":
            return "project", parts[1], None
        return "audit", parts[1], int(parts[2])

    def ensure_match(self, match_id: str, name: str) -> None:
        pass

    def get_doc_manifest(self, match_id: str) -> list[dict]:
        out = []
        for key, (_body, version) in self.docs.items():
            kind, slug, stage = self._identity(key)
            out.append(
                {
                    "doc_kind": kind,
                    "slug": slug,
                    "stage_number": stage,
                    "version": version,
                    "updated_at": datetime(2026, 8, 10, 12, 0, tzinfo=UTC).isoformat(),
                }
            )
        return out

    def get_doc(self, match_id, kind, slug, stage_number):
        if self.get_doc_error is not None:
            raise self.get_doc_error
        return self.docs[doc_identity_key(kind, slug, stage_number)]

    def put_doc(self, match_id, item, *, expected_version: int) -> int:
        if self.fail_puts:
            self.fail_puts -= 1
            raise SyncVersionConflict("scripted conflict")
        key = doc_identity_key(item.kind, item.slug, item.stage_number)
        _, current = self.docs.get(key, ({}, 0))
        self.docs[key] = (item.body, current + 1)
        self.put_calls.append((key, expected_version))
        return current + 1

    def upload_media(self, match_id, item, *, progress) -> str:
        progress(item.size)
        return "0" * 64


def _first_sync(match_root) -> FakeSyncClient:
    """Baseline: one full push so state/bases/versions exist."""
    client = FakeSyncClient()
    run_sync(match_root, client=client)
    return client


def test_run_sync_pulls_and_merges_remote_coach_note(tmp_path):
    match_root = make_synced_match(tmp_path)
    client = _first_sync(match_root)
    # Hosted-side edit: coach note lands on shot 1 of the audit doc.
    audit_key = next(k for k in client.docs if k.startswith("audit/"))
    body, version = client.docs[audit_key]
    body = {**body, "shots": [{**body["shots"][0], "coaching_note": "from-hosted"}]}
    client.docs[audit_key] = (body, version + 1)

    report = run_sync(match_root, client=client)

    slug, stage = audit_key.split("/")[1], audit_key.split("/")[2]
    audit_path = match_root / "shooters" / slug / "audit" / f"stage{stage}.json"
    assert '"from-hosted"' in audit_path.read_text(encoding="utf-8")
    assert report.pulled == 1 and report.conflicts == []
    state = load_sync_state(match_root)
    # Pushed-back merged doc: base == pushed body, version == PUT response.
    assert load_base_doc(match_root, audit_key) == client.docs[audit_key][0]
    assert state.doc_versions[audit_key] == client.docs[audit_key][1]


def test_run_sync_remote_beep_change_invalidates_and_reports(tmp_path):
    match_root = make_synced_match(tmp_path)
    client = _first_sync(match_root)
    project_key = next(k for k in client.docs if k.startswith("project/"))
    body, version = client.docs[project_key]
    import copy as _copy

    body = _copy.deepcopy(body)
    video = body["stages"][0]["videos"][0]
    video["beep_time"] = 2.5
    video["beep_source"] = "manual"
    video["beep_reviewed"] = True
    client.docs[project_key] = (body, version + 1)

    report = run_sync(match_root, client=client)

    assert report.reprocess_videos == 1
    merged_remote, _ = client.docs[project_key]  # pushed back within same run
    v = merged_remote["stages"][0]["videos"][0]
    assert v["beep_time"] == 2.5 and v["processed"]["trim"] is False


def test_run_sync_version_conflict_retries_then_succeeds(tmp_path):
    match_root = make_synced_match(tmp_path)
    client = _first_sync(match_root)
    audit_key = next(k for k in client.docs if k.startswith("audit/"))
    body, version = client.docs[audit_key]
    client.docs[audit_key] = (
        {**body, "shots": [{**body["shots"][0], "coaching_note": "x"}]},
        version + 1,
    )
    client.fail_puts = 1  # first PUT of attempt 1 loses the race

    report = run_sync(match_root, client=client)
    assert report.attempts == 2


def test_run_sync_version_conflict_exhausts_after_3(tmp_path):
    match_root = make_synced_match(tmp_path)
    client = _first_sync(match_root)
    audit_key = next(k for k in client.docs if k.startswith("audit/"))
    body, version = client.docs[audit_key]
    client.docs[audit_key] = (
        {**body, "shots": [{**body["shots"][0], "coaching_note": "x"}]},
        version + 1,
    )
    client.fail_puts = 99

    with pytest.raises(SyncClientError, match="could not converge"):
        run_sync(match_root, client=client)


def test_run_sync_pull_failure_aborts_before_local_writes(tmp_path):
    match_root = make_synced_match(tmp_path)
    client = _first_sync(match_root)
    audit_key = next(k for k in client.docs if k.startswith("audit/"))
    body, version = client.docs[audit_key]
    client.docs[audit_key] = (body, version + 1)  # something to pull
    client.get_doc_error = httpx.TransportError("offline")

    snapshot = {
        p: p.read_bytes() for p in sorted(match_root.rglob("*.json")) if p.is_file()
    }
    with pytest.raises(httpx.TransportError):
        run_sync(match_root, client=client)
    after = {p: p.read_bytes() for p in sorted(match_root.rglob("*.json")) if p.is_file()}
    assert after == snapshot  # nothing local was touched


def test_run_sync_crash_replay_after_merge_before_push(tmp_path):
    """Crash window: merge applied + bases updated, push never ran. The
    next run must push the merged docs as plain local changes - no
    double-merge, no lost data."""
    match_root = make_synced_match(tmp_path)
    client = _first_sync(match_root)
    audit_key = next(k for k in client.docs if k.startswith("audit/"))
    body, version = client.docs[audit_key]
    client.docs[audit_key] = (
        {**body, "shots": [{**body["shots"][0], "coaching_note": "survives"}]},
        version + 1,
    )
    client.fail_puts = 99  # every PUT dies -> run raises after merge+base update
    with pytest.raises(SyncClientError):
        run_sync(match_root, client=client)

    client.fail_puts = 0  # "restart": same remote, healthy network
    report = run_sync(match_root, client=client)
    assert report.pulled == 0  # remote version already recorded during crash run
    pushed, _ = client.docs[audit_key]
    assert pushed["shots"][0]["coaching_note"] == "survives"
```

Note on the crash test: it encodes the spec's base invariant precisely - the crashed run already set `base := remote` and recorded `doc_versions`, so the restart pulls nothing and the merged local doc pushes as an ordinary hash-diff change. If `report.pulled == 0` fails, the base-update ordering in `_apply_pull` is wrong.

Run: `pytest tests/test_sync_pull.py -v` -> the new tests FAIL (`ImportError: splitsmith.sync.run`).

- [ ] **Step 5: Implement run.py + push.py changes**

`push.py` docs phase (the only change there, inside the existing loop):

```python
    with _timed_phase(timings, timer, "docs"):
        for doc in plan.docs:
            label = doc.kind if doc.slug is None else f"{doc.kind} ({doc.slug})"
            on_progress(1.0, f"syncing {label}")
            key = doc_identity_key(doc.kind, doc.slug, doc.stage_number)
            new_version = client.put_doc(
                plan.match_id, doc, expected_version=sync_state.doc_versions.get(key, 0)
            )
            # Record hash + version + base only after the PUT succeeds -
            # same crash-safety invariant as media: a failed push must
            # retry this doc next time, not skip it forever.
            sync_state.doc_hashes[key] = hash_doc_body(doc.body)
            sync_state.doc_versions[key] = new_version
            save_base_doc(match_root, key, doc.body)
            save_sync_state(match_root, sync_state)
```

(`from .base import save_base_doc` joins push.py's imports.) `run_push` also gains a keyword arg `sync_state: SyncState | None = None` so `run_sync` can hand it the state it already loaded and mutated during pull (when None, load as today - keeps `run_push` callable standalone for the tests that use it directly; the plan phase becomes `sync_state = sync_state or load_sync_state(match_root)`).

`src/splitsmith/sync/run.py`:

```python
"""Bidirectional sync orchestration: pull -> merge -> push.

One ``run_sync`` call drives the whole cycle the slice spec defines:
preflight the local plan for push-blocking errors, adopt the mirror,
then up to three attempts of [pull changed docs -> three-way merge ->
apply locally -> push]. Only a lost optimistic-lock race
(:class:`SyncVersionConflict` - a hosted write landed mid-sync) retries;
every other error propagates. Base snapshots follow the spec's
invariant: base := pulled remote snapshot at apply time, base := pushed
body after each successful PUT, so a crash anywhere replays correctly.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from ..match_model import load_match_or_legacy
from ..match_project import MatchProject, atomic_write_json
from ..observability import PhaseTimer
from .base import load_base_doc, save_base_doc
from .client import HostedSyncClient, SyncClientError, SyncVersionConflict
from .merge import MergeResult, merge_audit_doc, merge_project_doc
from .plan import build_push_plan, doc_identity_key
from .pull import RemoteDoc, plan_pull, remote_doc_key
from .push import PushReport, _timed_phase, run_push
from .state import SyncState, load_sync_state, save_sync_state

_MAX_ATTEMPTS = 3


class SyncReport(PushReport):
    """PushReport plus the pull/merge side of a bidirectional run."""

    pulled: int = 0
    merged: int = 0
    conflicts: list[dict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    reprocess_videos: int = 0
    attempts: int = 1


def format_sync_message(report: SyncReport) -> str:
    """One-line summary for the sync job's final progress message."""
    message = (
        f"Synced: {report.pulled} pulled, {report.uploaded} uploaded, "
        f"{report.skipped} skipped, {report.docs} docs"
    )
    if report.docs_skipped:
        message += f" ({report.docs_skipped} unchanged)"
    if report.conflicts:
        message += f"; {len(report.conflicts)} conflict(s) resolved - see job details"
    if report.reprocess_videos:
        message += f"; {report.reprocess_videos} video(s) need re-processing"
    return message


def _local_doc_ts(path: Path) -> datetime:
    """LWW tiebreak timestamp for a local doc: its file mtime. Uniform
    across doc kinds (audit docs carry no updated_at field of their own)."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return datetime.fromtimestamp(0, tz=UTC)


def run_sync(
    match_root: Path,
    *,
    client: HostedSyncClient,
    on_progress: Callable[[float, str], None] = lambda p, m: None,
    timer: PhaseTimer | None = None,
) -> SyncReport:
    """Pull hosted changes, merge, then push - the bidirectional cycle."""
    timings: dict[str, float] = {}

    with _timed_phase(timings, timer, "preflight"):
        sync_state = load_sync_state(match_root)
        preflight = build_push_plan(match_root, sync_state=sync_state)
        if preflight.errors:
            raise SyncClientError("\n".join(preflight.errors))
        match_id, match_name = preflight.match_id, preflight.match_name

    with _timed_phase(timings, timer, "ensure_match"):
        client.ensure_match(match_id, match_name)

    pulled_total = 0
    all_conflicts: list[dict] = []
    all_notes: list[str] = []
    reprocess: set[str] = set()
    merged_docs = 0

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        with _timed_phase(timings, timer, "pull"):
            on_progress(0.0, "checking hosted changes")
            manifest = client.get_doc_manifest(match_id)
            changed = plan_pull(manifest, sync_state)
            pulled = [
                (rd, *client.get_doc(match_id, rd.kind, rd.slug, rd.stage_number))
                for rd in changed
            ]
            pulled_total += len(pulled)

        with _timed_phase(timings, timer, "merge"):
            result_counts = _apply_pull(match_root, match_id, sync_state, pulled)
            merged_docs += result_counts["merged"]
            all_conflicts.extend(result_counts["conflicts"])
            all_notes.extend(result_counts["notes"])
            reprocess.update(result_counts["reprocess"])
            save_sync_state(match_root, sync_state)

        try:
            push_report = run_push(
                match_root,
                client=client,
                on_progress=on_progress,
                timer=timer,
                sync_state=sync_state,
            )
            break
        except SyncVersionConflict as exc:
            if attempt == _MAX_ATTEMPTS:
                raise SyncClientError(
                    f"sync could not converge after {_MAX_ATTEMPTS} attempts - a hosted "
                    f"write kept landing mid-sync ({exc})"
                ) from exc
            on_progress(0.0, "hosted changed during sync - retrying")

    report = SyncReport(
        **push_report.model_dump(),
        pulled=pulled_total,
        merged=merged_docs,
        conflicts=all_conflicts,
        notes=all_notes,
        reprocess_videos=len(reprocess),
        attempts=attempt,
    )
    report.timings.update(timings)
    return report


def _apply_pull(
    match_root: Path,
    match_id: str,
    sync_state: SyncState,
    pulled: list[tuple[RemoteDoc, dict, int]],
) -> dict:
    """Merge pulled docs into the local tree and update bases/versions.

    Order per doc: merge in memory -> atomic local write (only when the
    merge changed anything) -> base := remote snapshot -> record remote
    version. A crash after any doc leaves a consistent prefix: bases
    updated for exactly the docs whose merged form is on disk, so the
    next run sees merge results as plain local changes (spec invariant).
    """
    match, shooter_roots = load_match_or_legacy(match_root)
    conflicts: list[dict] = []
    notes: list[str] = []
    reprocess: set[str] = set()
    merged_count = 0

    for rd, remote_doc, version in pulled:
        key = remote_doc_key(rd)
        base = load_base_doc(match_root, key)

        if rd.kind == "match":
            # No whitelisted fields on the match doc: local always wins.
            if base is not None and remote_doc != base:
                notes.append(
                    f"{key}: remote changed the match doc; local wins "
                    "(no mobile surface writes it - investigate)"
                )
        elif rd.kind == "project":
            shooter_root = shooter_roots.get(rd.slug)
            if shooter_root is None:
                notes.append(f"{key}: no local shooter {rd.slug!r}; membership is desktop-owned; ignored")
            else:
                project = MatchProject.load(shooter_root)
                local_doc = project.model_dump(mode="json")
                result = merge_project_doc(
                    base,
                    local_doc,
                    remote_doc,
                    doc_key=key,
                    local_ts=_local_doc_ts(shooter_root / "shooter.json"),
                    remote_ts=rd.updated_at,
                )
                _collect(result, conflicts, notes, reprocess)
                if result.changed_vs_local:
                    merged_project = MatchProject.model_validate(result.doc)
                    merged_project.save(shooter_root)
                    merged_count += 1
        else:  # audit
            shooter_root = shooter_roots.get(rd.slug)
            audit_path = (
                None
                if shooter_root is None
                else shooter_root / "audit" / f"stage{rd.stage_number}.json"
            )
            if audit_path is None:
                notes.append(f"{key}: no local shooter {rd.slug!r}; ignored")
            else:
                local_doc = (
                    json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else {}
                )
                result = merge_audit_doc(
                    base,
                    local_doc,
                    remote_doc,
                    doc_key=key,
                    local_ts=_local_doc_ts(audit_path),
                    remote_ts=rd.updated_at,
                )
                _collect(result, conflicts, notes, reprocess)
                if result.changed_vs_local:
                    audit_path.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write_json(audit_path, result.doc)
                    merged_count += 1

        save_base_doc(match_root, key, remote_doc)
        sync_state.doc_versions[key] = version

    return {
        "merged": merged_count,
        "conflicts": conflicts,
        "notes": notes,
        "reprocess": reprocess,
    }


def _collect(
    result: MergeResult, conflicts: list[dict], notes: list[str], reprocess: set[str]
) -> None:
    conflicts.extend(asdict(c) for c in result.conflicts)
    notes.extend(result.notes)
    reprocess.update(result.reprocess_video_ids)
```

Implementation notes for the engineer:
- For `_local_doc_ts` on project docs, stat the file `MatchProject.load` actually reads (see `match_project.py:1043` - `SHOOTER_FILE` in the shooters/ layout); use the filename constant from `match_model.py`/`match_project.py`, not the string literal shown in the snippet.
- The legacy single-shooter layout is already rejected by preflight (no match_id -> plan error), so `shooter_roots` here is always the `shooters/<slug>/` map.
- `run_push`'s `plan` phase re-hashes docs AFTER the merge applied local writes, so merged changes push naturally and an unchanged doc re-push-0s - no special wiring.
- `processed` must be excluded from the beep unit but IS mutated when remote wins; `_note_non_whitelisted_remote_changes` already strips `processed` for exactly this reason.

`server.py` `_run_sync_match` (:3434): swap the import and call - `from ..sync.run import run_sync, format_sync_message` alongside the existing imports (check the actual import block near the top of server.py; `run_push`/`format_push_message` imports drop if now unused), body becomes `report = run_sync(match_root, client=client, on_progress=..., timer=handle.timer)` and the two tail lines use `report.model_dump()` / `format_sync_message(report)`. The `except SyncClientError` wrapper stays (SyncVersionConflict subclasses it, and exhaustion re-raises plain SyncClientError).

- [ ] **Step 6: Run the full sync test suite**

Run: `pytest tests/test_sync_pull.py tests/test_sync_push.py tests/test_sync_merge.py tests/test_sync_integration.py tests/test_sync_local_endpoints.py -v`
Expected: PASS, including the Task-3 leftovers in `test_sync_push.py`/`test_sync_integration.py` (update their `put_doc` stubs/assertions for the `expected_version` kwarg and version-returning responses; integration tests keep constructing the client in exact production shape - bare-origin `base_url`, bearer header - per the #712 lesson).

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/sync/run.py src/splitsmith/sync/push.py src/splitsmith/ui/server.py \
  tests/test_sync_pull.py tests/test_sync_push.py tests/test_sync_integration.py
git commit -m "feat(sync): pull-merge-push orchestration with bounded conflict retry"
```

---

### Task 7: Staleness hint (status + SPA)

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`SyncStatusResponse` :4338, `get_match_sync_status` :6172)
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (SyncStatusResponse type)
- Modify: `src/splitsmith/ui_static/src/components/match/SyncCard.tsx`
- Test: `tests/test_sync_local_endpoints.py` (extend), `src/splitsmith/ui_static/src/components/match/SyncCard.test.tsx` (extend)

**Interfaces:**
- Produces: `SyncStatusResponse.remote_changes: int | None` - count of remotely-changed docs per `plan_pull`; `None` when sync is unconfigured or the hosted side was unreachable (offline is a normal desktop condition, not an error). SPA shows "Hosted has newer changes - sync now" when `remote_changes > 0`.

- [ ] **Step 1: Write the failing server test**

In `tests/test_sync_local_endpoints.py`, following its existing status-endpoint tests (they monkeypatch prefs and the match root):

```python
def test_sync_status_reports_remote_changes(local_client, monkeypatch, configured_prefs):
    """A manifest with a doc version doc_versions has not seen -> remote_changes 1."""
    manifest = [{"doc_kind": "project", "slug": "anna", "stage_number": None,
                 "version": 5, "updated_at": "2026-08-10T10:00:00+00:00"}]
    monkeypatch.setattr(
        "splitsmith.ui.server._fetch_remote_manifest", lambda prefs, match_id: manifest
    )
    resp = local_client.get("/api/match/sync/status")
    assert resp.status_code == 200
    assert resp.json()["remote_changes"] == 1


def test_sync_status_offline_remote_changes_none(local_client, monkeypatch, configured_prefs):
    def _boom(prefs, match_id):
        raise httpx.TransportError("offline")
    monkeypatch.setattr("splitsmith.ui.server._fetch_remote_manifest", _boom)
    resp = local_client.get("/api/match/sync/status")
    assert resp.status_code == 200
    assert resp.json()["remote_changes"] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_sync_local_endpoints.py -k remote_changes -v`
Expected: FAIL - no `_fetch_remote_manifest`, response lacks `remote_changes`.

- [ ] **Step 3: Implement server side**

`SyncStatusResponse` gains:

```python
    #: Count of hosted docs newer than what this desktop last synced
    #: (manifest version diff). None = unknown: sync unconfigured or the
    #: hosted side unreachable right now - offline is a normal desktop
    #: condition, so the card just omits the hint rather than erroring.
    remote_changes: int | None = None
```

Module-level helper near `_run_sync_match`'s imports:

```python
def _fetch_remote_manifest(prefs, match_id: str) -> list[dict]:
    """One short-timeout manifest GET for the status endpoint's
    remote-staleness hint. Module-level (not inline in the handler) so
    tests monkeypatch it; raises httpx errors to the caller."""
    with httpx.Client(
        base_url=prefs.hosted_base_url,
        headers={"Authorization": f"Bearer {prefs.hosted_token}"},
        timeout=5.0,
    ) as http:
        client = HostedSyncClient(http=http)
        return client.get_doc_manifest(match_id)
```

`get_match_sync_status` (:6172) - after computing `plan`, before the return:

```python
        remote_changes: int | None = None
        if configured:
            match_id = plan.match_id or None
            if match_id:
                try:
                    manifest = await run_in_threadpool(_fetch_remote_manifest, prefs, match_id)
                    remote_changes = len(plan_pull(manifest, sync_state))
                except (httpx.HTTPError, SyncClientError):
                    remote_changes = None
```

and `remote_changes=remote_changes` joins the `SyncStatusResponse(...)` kwargs. Imports: `plan_pull` from `..sync.pull`; `run_in_threadpool` from `fastapi.concurrency` (the handler is async and `_fetch_remote_manifest` blocks; check how server.py already wraps blocking calls in async handlers and copy that idiom - if none exists nearby, `fastapi.concurrency.run_in_threadpool` is the standard one). A hosted-side 404 from `get_doc_manifest` (match never pushed) surfaces as `httpx.HTTPStatusError` (subclass of `httpx.HTTPError`) -> `remote_changes = None`; acceptable - "never pushed" already renders as "Never synced".

- [ ] **Step 4: Run server tests**

Run: `pytest tests/test_sync_local_endpoints.py -v`
Expected: PASS.

- [ ] **Step 5: SPA - failing test, then implement**

`SyncCard.test.tsx` (follow its existing render/mocking pattern for `api.getSyncStatus`):

```tsx
it("shows the hosted-has-newer hint when remote_changes > 0", async () => {
  mockStatus({ configured: true, last_synced_at: ISO, stale: false,
               pending_media: 0, errors: [], remote_changes: 2 });
  renderCard();
  expect(await screen.findByText(/hosted has newer changes/i)).toBeInTheDocument();
});

it("omits the hint when remote_changes is null", async () => {
  mockStatus({ configured: true, last_synced_at: ISO, stale: false,
               pending_media: 0, errors: [], remote_changes: null });
  renderCard();
  expect(await screen.findByText(/synced/i)).toBeInTheDocument();
  expect(screen.queryByText(/hosted has newer changes/i)).not.toBeInTheDocument();
});
```

Run `cd src/splitsmith/ui_static && pnpm test SyncCard` -> new tests FAIL.

`lib/api.ts`: add `remote_changes: number | null;` to the `SyncStatusResponse` type.

`SyncCard.tsx` `SyncStatusLine`: insert between the `errors` branch and the `!status.last_synced_at` branch:

```tsx
  if ((status.remote_changes ?? 0) > 0) {
    return (
      <p className={lineClass}>
        <RefreshCw className="size-3.5 shrink-0 text-led" aria-hidden="true" />
        Hosted has newer changes - sync now
      </p>
    );
  }
```

(Icon + text carry the state, never color alone - matches the card's existing a11y convention. The sync button is already enabled in this state; no button change.)

Run `pnpm test SyncCard && pnpm typecheck` -> PASS.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_sync_local_endpoints.py \
  src/splitsmith/ui_static/src/lib/api.ts \
  src/splitsmith/ui_static/src/components/match/SyncCard.tsx \
  src/splitsmith/ui_static/src/components/match/SyncCard.test.tsx
git commit -m "feat(sync): remote-staleness hint on sync status + SyncCard"
```

---

### Task 8: Docker smoke, gates, staging E2E

**Files:**
- Modify: `tests/test_sync_docker.py` (extend)
- No production code expected; fixes only if the smoke finds bugs.

**Interfaces:** consumes everything above; produces the verified slice.

- [ ] **Step 1: Extend the docker smoke**

`tests/test_sync_docker.py` runs the sync API against live Postgres (`pytest -m docker`). Add one round-trip test following its existing fixture shape: adopt match -> PUT project doc at expected_version 0 -> GET manifest shows version 1 -> PUT again at expected_version 1 -> ok; PUT at stale version 1 again -> 409 `version_conflict`; GET doc returns the latest body. This proves the coalesce-unique-index + RLS + version-guard stack against real Postgres, which aiosqlite can't.

- [ ] **Step 2: Run the full local gate set**

```bash
export PATH="$HOME/.claude-tmp/bin:$PATH"   # docker shim for -m docker
ruff check . && black --check .
pytest
pytest -m docker
cd src/splitsmith/ui_static && pnpm typecheck && pnpm test && cd -
npx eslint src/splitsmith/ui_static/src/components/match/SyncCard.tsx src/splitsmith/ui_static/src/lib/api.ts
```

Expected: all green. (Whole-repo eslint also valid since #516 if preferred: `cd src/splitsmith/ui_static && pnpm lint`.)

- [ ] **Step 3: Grep dash discipline**

Run: `git diff main --unified=0 | grep '^+' | grep -nE '—|--' | grep -v '^+++'`
Expected: no hits in prose/comments (CLI flags like `--check` in docs/commands are fine; judge each hit).

- [ ] **Step 4: Commit + PR**

```bash
git add tests/test_sync_docker.py
git commit -m "test(sync): docker smoke for manifest + version-guarded PUT round trip"
git push -u origin feat/sync-pull-merge
gh pr create --title "feat(sync): bidirectional pull-merge-push desktop sync" --body "..."
```

PR body summarizes the spec decisions (desktop re-derives; three-way merge vs sync_base; version-guarded PUTs; staleness hint) and links `docs/superpowers/specs/2026-08-10-bidirectional-sync-design.md`. Do not auto-merge: staging E2E comes first (merge-when-green is NOT enforced on this repo - merging deploys to staging, which is wanted here, but get the E2E done before tagging any release).

- [ ] **Step 5: Staging E2E (after merge deploys to staging)**

Acceptance per spec - synthetic hosted write, then desktop sync round trip:

1. Point the desktop install at staging (config currently points at PROD - the hosted-sync Settings dialog holds base_url + token; switch to `https://my.staging.splitsmith.app` with a staging desktop token; the memory note says a staging config backup exists as `.bak-staging`). Pick a small already-synced local match, or push one first (`Sync now`).
2. Synthetic remote edit in the staging Neon branch (`br-little-scene-alrqbhpc`), via Neon MCP `run_sql` or psql - bump a coach note inside an audit doc:

```sql
UPDATE state_docs
SET doc = jsonb_set(doc, '{shots,0,coaching_note}', '"e2e-note-from-hosted"'),
    version = version + 1,
    updated_at = now()
WHERE doc_kind = 'audit'
  AND match_id = '<MATCH_ID>'
  AND slug = '<SLUG>'
  AND stage_number = <N>;
```

(The version bump matters - it is what the manifest diff detects. Verify the doc has `shots[0]` first with a SELECT.)

3. Desktop: open the match - the SyncCard shows "Hosted has newer changes - sync now" (Task 7 hint, live-verified).
4. Click Sync now. Expect the job message to include "1 pulled". Verify `shooters/<slug>/audit/stage<N>.json` locally now contains `e2e-note-from-hosted`, `sync_base/audit/<slug>/<N>.json` matches the pushed body, and `sync_state.json` `doc_versions` has the new version.
5. Click Sync now again. Expect re-push-0: "0 pulled, 0 uploaded, ... 0 docs (N unchanged)".
6. Confirm the hosted doc still contains the note (it round-tripped, not clobbered): re-SELECT in Neon.
7. Restore the desktop config to prod afterwards.

Record outcomes in the PR (or a follow-up comment) before calling the slice done.

---

## Self-Review Notes (already applied)

- Spec coverage: hosted GET routes + manifest (T2/T3), strict PUT (T3), event ids (T1), sync_state v2 + sync_base (T4), merge whitelist/conflict matrix incl. beep invalidation + event union + LWW + tripwire (T5), pull-merge-push with crash-replay invariant and bounded retry (T6), staleness hint (T7), docker smoke + staging E2E acceptance (T8). Deletion propagation, gate lifting, needs_attention: explicitly out (spec).
- Type consistency: `put_doc(match_id, item, *, expected_version) -> int` used identically in T3 (definition), T6 (push executor + fakes). `doc_versions`/`doc_hashes` keys are `doc_identity_key` strings everywhere. `SyncReport extends PushReport` (pydantic), `MergeConflict`/`MergeResult` dataclasses only cross module boundaries via `asdict`.
- Known judgment calls the implementer may adjust with a note: exact fixture names in existing test files; where `_fetch_remote_manifest` lives in server.py's layout; `DocItem(body={})` as URL vehicle vs extracting `_doc_path`.
