# Desktop-to-Hosted Sync MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push a locally-worked match (state docs + per-stage trims) to my.splitsmith.app as a read-only mirror so the existing share mechanic serves it, per `docs/superpowers/specs/2026-08-07-desktop-hosted-sync-mvp-design.md` (issue #631).

**Architecture:** Hosted side gains desktop bearer tokens (worker-token pattern + `user_id`), an idempotent `/api/sync/` router (match create-or-adopt, doc upserts, media presign), and a read-only gate for `origin='desktop'` matches in the `_match_id_alias` middleware. Local side gains a `splitsmith.sync` package (digest cache, doc sanitization, push plan, httpx client), a `sync_match` job, and a sync card in the SPA. No share code changes.

**Tech Stack:** FastAPI, SQLAlchemy async + Alembic, httpx (already a dependency), React SPA (`ui_static`, pnpm). **Zero new dependencies.**

## Global Constraints

- No backwards compatibility, no fallbacks, no legacy shims - no live users; write the clean design directly (user directive 2026-08-07 + standing memory).
- New prose/comments use single ASCII dash "-", never em dash, never "--".
- Python 3.11+, type hints everywhere, Pydantic across module boundaries, `pathlib.Path`, f-strings, black (line length 110), ruff. Imports: stdlib / third-party / local, blank-line separated.
- `uv` only, never pip. Do NOT touch `pyproject.toml` - this feature adds no dependencies.
- Sync logic lives in `src/splitsmith/sync/`, not in CLI or server handlers (architecture rule 1/2).
- Tests: pytest, parallel-safe (no shared mutable state outside `tmp_path`). Before every commit: run the task's scoped tests; before the PR: `ruff check . && black --check . && pytest` (full CI gate) plus `pnpm typecheck && pnpm test` in `ui_static` for SPA tasks.
- DB changes require the docker smoke (`pytest -m docker`) locally before merge (Task 12). `docker` needs `~/.claude-tmp/bin` on PATH (memory: docker-path-workaround).
- SPA: pnpm only; overlays use z tokens + body Portal + `useDialogFocus` (PR #519 convention); WCAG 2.2 AA - color never the sole state carrier.
- Branch: continue on `spec/desktop-hosted-sync-mvp` (holds the spec). PR title `feat(sync): desktop-to-hosted match push MVP (#631)`.

**Key facts pinned from code (do not re-derive):**

- Local match layout: `<match-root>/match.json` (`Match`, `match_model.py:198`), `shooters/<slug>/` with `shooter.json`, `project.json` (`MatchProject`), `audit/stage<N>.json`, `trimmed/stage<N>_cam_<video_id>_trimmed.mp4` + `.params.json` sidecar.
- `Match.match_id` is deterministic and frozen (`generate_match_id`, `match_model.py:89`); hosted `matches` is unique on `(user_id, match_id)` - it IS the sync key.
- Hosted read surface (incl. every share-whitelisted route) reads only the `project` + `audit` state docs via `state.shooter_project(slug)` - `shooter.json` is local-disk redundancy and is NOT synced.
- Trim R2 key: `users/<uid>/` prefix (from `_tenant_s3_storage`, `server.py:5281`) + `matches/<match_id>/shooters/<slug>/trimmed/<basename>` (scope set in `AppState.shooter_project`, `server.py:1476-1495`; key builder `_storage_trim_key`, `audio.py:685`).
- Auth gate: `_auth_gate` middleware `server.py:5969`; hosted wiring `_apply_hosted_mode_wiring` sets `state.auth = MagicLinkAuth(...)` at `server.py:5085` with the RAW `session_factory` in scope.
- Read-only choke point: `_match_id_alias` middleware `server.py:5800-5884`, ownership check `await owner_store.get(match_id)` at line 5841.
- Store APIs: `PostgresMatchStore.upsert(match_id, name, storage_prefix)` (`db/matches.py:63`); `ProjectStateStore.save_match/save_project/save_audit(..., expected_version=)` + `load_*` returning `(doc, version)` (`db/project_state.py`).
- Multipart precedent: endpoints `server.py:6501-6568`, models `server.py:3980-4021`, `S3Storage.create_multipart_upload/presign_upload_part/complete_multipart_upload/abort_multipart_upload` (`storage.py:539-597`), `_require_storage()` 503s when storage is None.
- Worker token hashing: `_hash`/`_mint` in `db/workers.py:57-61` (`sha256 hexdigest` / `secrets.token_urlsafe(32)`).
- Test fixtures: `tests/hosted_helpers.py` - `hosted_env`, `hosted_app`, `login(client, sender, email)`, `seed_match(db_url, email, match_id)`. Multipart storage double pattern: `tests/test_hosted_raw_upload.py`.
- Jobs: bodies registered in `register_job_bodies` (`server.py:2006`, registrations at `server.py:3353-3360`); `await state.jobs.submit(kind=..., args=...)`; `JobHandle.update(progress=, message=)`; SPA labels in `Jobs.tsx:52-72` (`KIND_LABEL`/`KIND_ICON`).
- Local prefs: `GlobalPrefs` + `load_global_prefs()`/`save_global_prefs()` (`user_config.py:109-120, 454-476`), YAML at `~/.splitsmith/config.yaml`.
- SPA: mode flag `useDeploymentMode()` (`lib/features.ts`); match overview `pages/Home.tsx`; shell `components/match/MatchShell.tsx`; account chip `components/AccountChip.tsx`; API module `lib/api.ts`; hosted route is `/match/:matchId` (singular).

---

### Task 1: DB schema - `desktop_tokens` table, `matches.origin`, store plumbing

**Files:**
- Modify: `src/splitsmith/db/models.py` (add `DesktopTokenRow`; add `origin` to `MatchRow` near line 274)
- Modify: `src/splitsmith/db/matches.py` (`MatchRecord` + `upsert`/`get` grow `origin`)
- Modify: `src/splitsmith/db/__init__.py` (export `DesktopTokenRow`)
- Create: `alembic/versions/<rev>_add_desktop_tokens_and_match_origin.py`
- Test: `tests/test_db_matches_origin.py`

**Interfaces:**
- Produces: `DesktopTokenRow` (table `desktop_tokens`: `id` str ULID PK, `user_id` FK `users.id` CASCADE indexed non-null, `name` str non-null, `token_hash` str unique non-null, `created_at`, `last_used_at` nullable, `revoked_at` nullable).
- Produces: `MatchRow.origin: Mapped[str]` non-null, server_default `"hosted"`.
- Produces: `PostgresMatchStore.upsert(match_id: str, name: str, storage_prefix: str, *, origin: str = "hosted") -> MatchRecord`; `MatchRecord.origin: str`. Upsert on an existing row updates `name`/`updated_at` but NEVER changes `origin`.

- [ ] **Step 1: Write the failing test** - `tests/test_db_matches_origin.py`. Copy the sqlite engine setup from `tests/hosted_helpers.py:36-44` (create_engine on `tmp_path` sqlite, `Base.metadata.create_all`). Seed a `User` row, build `PostgresMatchStore` the way existing store tests do (grep `PostgresMatchStore(` in `tests/` and mirror the constructor call):

```python
async def test_upsert_sets_origin_and_never_flips_it(store) -> None:
    rec = await store.upsert("bromma-abc123", "Bromma", "matches/bromma-abc123", origin="desktop")
    assert rec.origin == "desktop"
    rec2 = await store.upsert("bromma-abc123", "Bromma renamed", "matches/bromma-abc123")
    assert rec2.origin == "desktop"  # default arg must not overwrite
    assert rec2.name == "Bromma renamed"

async def test_origin_defaults_to_hosted(store) -> None:
    rec = await store.upsert("m2", "Native", "matches/m2")
    assert rec.origin == "hosted"
```

- [ ] **Step 2: Run it, verify it fails** - `pytest tests/test_db_matches_origin.py -n0 -v` - expect `TypeError: upsert() got an unexpected keyword argument 'origin'` (or AttributeError on `MatchRecord.origin`).
- [ ] **Step 3: Implement.** In `models.py`: add `origin: Mapped[str] = mapped_column(String, nullable=False, server_default="hosted")` to `MatchRow`; add `DesktopTokenRow` copying the PK/`created_at` column style from `WorkerRow` (`models.py:545`) with the columns in Interfaces above and a docstring noting: account-scoped credential, resolved pre-tenant via the raw session factory (same rationale as `share_tokens` resolution), one row per issued token, revocation is a timestamp not a delete. In `matches.py`: thread `origin` through `MatchRecord` and `upsert` (set only on INSERT; the update branch must not touch it), and include it in `get`/`list` results. Export from `db/__init__.py`.
- [ ] **Step 4: Write the migration.** New revision, `down_revision` = current head (run `uv run alembic heads` to get it; was `f6acac06499c` at planning time). Follow the two precedents exactly:
  - column add: `op.add_column("matches", sa.Column("origin", sa.String(), nullable=False, server_default="hosted"))` (RLS policy keys on `user_id` only - no RLS DDL needed for this, see docstring of `f6acac06499c_add_shooter_slug_to_compute_jobs.py`);
  - table create + RLS: mirror `4ab814cb20f5` (the `share_tokens` migration) - `op.create_table("desktop_tokens", ...)` then the same `ALTER TABLE ... ENABLE/FORCE ROW LEVEL SECURITY` + `CREATE POLICY tenant_isolation` block with the `user_id = current_setting('app.user_id', true)` body, guarded to Postgres-only the same way that file guards it. `downgrade` drops policy + table + column.
- [ ] **Step 5: Run test + full db tests** - `pytest tests/test_db_matches_origin.py tests/test_db* -n0 -v` - expect PASS (sqlite `create_all` picks the new schema up automatically).
- [ ] **Step 6: Commit** - `git add -- src/splitsmith/db/models.py src/splitsmith/db/matches.py src/splitsmith/db/__init__.py alembic/versions/ tests/test_db_matches_origin.py && git commit -m "feat(sync): desktop_tokens table + matches.origin column (#631)"` (enumerate paths, never glob - memory rule).

### Task 2: DesktopTokenStore + bearer auth backend + gate wiring

**Files:**
- Create: `src/splitsmith/db/desktop_tokens.py`
- Modify: `src/splitsmith/auth.py` (add `CompositeAuth`)
- Modify: `src/splitsmith/ui/server.py:5085` (compose auth in `_apply_hosted_mode_wiring`)
- Test: `tests/test_desktop_tokens.py`

**Interfaces:**
- Produces (`db/desktop_tokens.py`):
  - `class DesktopTokenRecord(BaseModel)`: `id: str`, `name: str`, `created_at: datetime`, `last_used_at: datetime | None`, `revoked_at: datetime | None` (never the hash).
  - `class DesktopTokenStore:` `__init__(self, session_factory, *, user_id: str)` (fail loud on empty user_id, mirroring `ProjectStateStore`); `async create(self, name: str) -> tuple[DesktopTokenRecord, str]` (returns raw token once); `async list(self) -> list[DesktopTokenRecord]`; `async revoke(self, token_id: str) -> bool`.
  - `class DesktopTokenAuth:` `__init__(self, session_factory)`; `async authenticate_request(self, request) -> User | None` - parses `Authorization: Bearer <token>` (same partition parse as `server.py:6124-6127`), sha256-hashes, resolves an unrevoked `desktop_tokens` row via the RAW session factory, loads the `users` row, stamps `last_used_at`, returns `auth.User(id=..., email=..., display_name=...)`. Malformed/unknown/revoked -> `None`.
  - Reuse `_hash`/`_mint` by importing them from `db/workers.py` if they are module-level there; otherwise copy the two one-liners (sha256 hexdigest / `secrets.token_urlsafe(32)`).
- Produces (`auth.py`): `class CompositeAuth:` `__init__(self, *backends: AuthBackend)`; `authenticate_request` returns the first non-None result. Satisfies the `AuthBackend` Protocol.
- Wiring: `server.py:5085` becomes `state.auth = CompositeAuth(MagicLinkAuth(session_factory, email_sender, signup_policy=signup_policy), DesktopTokenAuth(session_factory))`. NOTE: `hosted_helpers.py:75` pokes `app.state.splitsmith_state.auth._email` - update that fixture to reach the magic-link backend inside the composite (give `CompositeAuth` a stable attribute, e.g. `self.backends`, and change the fixture to `auth.backends[0]._email`). Fix the fixture, do not add a compatibility property.

- [ ] **Step 1: Write failing store tests** (`tests/test_desktop_tokens.py`, sqlite engine per Task 1 pattern):

```python
async def test_create_returns_raw_token_and_hashes_at_rest(store, session_factory) -> None:
    rec, raw = await store.create("mac studio")
    assert raw and raw not in (rec.id, rec.name)
    row = await _fetch_row(session_factory, rec.id)
    assert row.token_hash == hashlib.sha256(raw.encode()).hexdigest()

async def test_revoked_token_stops_authenticating(store, auth, request_with_bearer) -> None:
    rec, raw = await store.create("t")
    assert (await auth.authenticate_request(request_with_bearer(raw))) is not None
    await store.revoke(rec.id)
    assert (await auth.authenticate_request(request_with_bearer(raw))) is None

async def test_garbage_bearer_is_none_not_error(auth, request_with_bearer) -> None:
    assert (await auth.authenticate_request(request_with_bearer("nonsense"))) is None
```

Build `request_with_bearer` with `fastapi.Request` from a minimal ASGI scope (`{"type": "http", "headers": [(b"authorization", f"Bearer {raw}".encode())]}`).
- [ ] **Step 2: Run, verify fail** - `pytest tests/test_desktop_tokens.py -n0 -v` - ModuleNotFoundError.
- [ ] **Step 3: Implement** `db/desktop_tokens.py`, `CompositeAuth`, and the wiring + `hosted_helpers.py` fixture fix.
- [ ] **Step 4: End-to-end gate test** in the same file, using `hosted_app`/`login`: login as `m@thias.se`, insert a token row directly (or via Task 3's endpoint once it exists - here insert directly), then `client.get("/api/me/matches"-equivalent listing route, headers={"Authorization": f"Bearer {raw}"})` with NO session cookie (`client.cookies.clear()`) and assert 200; assert 401 with a bogus bearer. (Grep server.py for the match listing route the SPA picker uses and use that path.)
- [ ] **Step 5: Run all auth tests** - `pytest tests/test_desktop_tokens.py tests/test_auth.py tests/test_auth_routes.py tests/test_share_routes.py -v` - expect PASS (share + magic-link must be unbroken by the composite).
- [ ] **Step 6: Commit** - `git commit -m "feat(sync): desktop bearer tokens resolve to a normal tenant (#631)"`.

### Task 3: Token management endpoints `/api/me/desktop-tokens`

**Files:**
- Modify: `src/splitsmith/ui/server.py` (three routes next to the existing `/api/me/` group; request/response models next to the multipart models ~line 4021)
- Test: `tests/test_desktop_token_routes.py`

**Interfaces:**
- `GET /api/me/desktop-tokens` -> `{"tokens": [DesktopTokenRecord...]}` (hosted only; 404 in local mode like other hosted-only routes - grep `_hosted_mode_active()` for the guard idiom).
- `POST /api/me/desktop-tokens` body `{"name": str}` -> `{"token": <raw>, "record": DesktopTokenRecord}` - raw appears in this response only.
- `DELETE /api/me/desktop-tokens/{token_id}` -> `{"revoked": bool}`.
- Store constructed per-request: `DesktopTokenStore(state.session_factory_raw_or_equivalent, user_id=request.state.user.id)` - grep how per-user stores are built in the `/api/me/` handlers and mirror exactly (there is an established pattern; copy it, using the tenant-scoped factory so RLS applies).

- [ ] **Step 1: Failing route tests** using `hosted_app` + `login`: create -> raw returned once and list shows the record without any hash/raw; revoke -> `revoked: true` and the bearer stops working (reuse Task 2's gate assertion); a second user (login with another email) cannot list or revoke the first user's tokens (assert list is empty and revoke returns 404/false).
- [ ] **Step 2: Run, verify fail** (405/404s).
- [ ] **Step 3: Implement the three handlers** (thin: parse, call store, serialize).
- [ ] **Step 4: Run** - `pytest tests/test_desktop_token_routes.py -v` - PASS.
- [ ] **Step 5: Commit** - `git commit -m "feat(sync): desktop token management endpoints (#631)"`.

### Task 4: Sync router - match create-or-adopt + doc upserts

**Files:**
- Create: `src/splitsmith/ui/sync_api.py` (APIRouter, prefix `/api/sync`; handlers pull `state = request.app.state.splitsmith_state`)
- Modify: `src/splitsmith/ui/server.py` (`app.include_router(sync_router)` in `create_app`, after middlewares are registered)
- Test: `tests/test_sync_api.py`

**Interfaces:**
- Consumes: `PostgresMatchStore.upsert(..., origin=)` (Task 1), `ProjectStateStore.save_match/save_project/save_audit` + `load_*`.
- Produces routes (all hosted-only - 404 via the standard local-mode guard; all reached through `_auth_gate`, so `request.state.user` and `current_tenant` are set; desktop bearer or session both work):
  - `POST /api/sync/matches` body `SyncMatchCreate{match_id: str, name: str}` -> 200 `{"match_id": ..., "origin": "desktop"}`. If a row exists with `origin == "hosted"` -> 409 `{"detail": "match_exists_hosted"}`. Otherwise `matches_store.upsert(match_id, name, f"matches/{match_id}", origin="desktop")`.
  - `PUT /api/sync/matches/{match_id}/docs/match` body = raw match doc -> validate with `match_model.Match.model_validate` (422 on failure), then load-current-version + `save_match(..., expected_version=version)`; on `StateConflictError` retry once, then 409. -> `{"version": int}`.
  - `PUT /api/sync/matches/{match_id}/docs/project/{slug}` - same shape, validated with `MatchProject.model_validate`.
  - `PUT /api/sync/matches/{match_id}/docs/audit/{slug}/{stage_number}` - body is a schemaless dict (audit docs have no model), stored as-is.
  - Every route first resolves the match via `matches_store.get(match_id)`: 404 if absent, 409 `{"detail": "not_a_mirror"}` if `origin != "desktop"` (a sync can never touch a native hosted match).
- Mirror-upsert helper (module-level in `sync_api.py`, reused by all three doc routes):

```python
async def _mirror_save(load: Callable[[], Awaitable[tuple[dict | None, int]]],
                       save: Callable[[int], Awaitable[int]]) -> int:
    """Unconditional last-write-wins upsert over the optimistic-lock store."""
    _, version = await load()
    try:
        return await save(version)
    except StateConflictError:
        _, version = await load()
        return await save(version)
```

- [ ] **Step 1: Failing tests** (`hosted_app` + `login` + bearer from Task 3's POST): create match twice (idempotent, same 200); `seed_match` a native row then POST sync create for it -> 409; PUT a valid match doc (build with `match_model.Match(name="X")`, `model_dump(mode="json")`) -> version 1, PUT again -> version 2; PUT garbage doc -> 422; PUT docs for an unknown match -> 404; second user cannot touch first user's mirror (404 via tenancy).
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** `sync_api.py` + router inclusion.
- [ ] **Step 4: Run** - `pytest tests/test_sync_api.py -v` - PASS.
- [ ] **Step 5: Commit** - `git commit -m "feat(sync): /api/sync match adopt + state-doc mirror upserts (#631)"`.

### Task 5: Sync router - media presign endpoints

**Files:**
- Modify: `src/splitsmith/ui/sync_api.py`
- Test: `tests/test_sync_media_api.py`

**Interfaces:**
- Consumes: storage acquisition exactly as `create_multipart_upload` (`server.py:6501`) does (`_require_storage()` idiom - 503 when unconfigured); `S3Storage` multipart methods.
- Produces (request models in `sync_api.py`, named `SyncMedia*` to avoid clashing with the raw-upload `Multipart*` models):
  - `POST /api/sync/matches/{match_id}/media/create` body `{key: str}` -> `{"upload_id": str, "key": str, "part_size": int}` (`part_size` = the `_RAW_UPLOAD_PART_SIZE` constant).
  - `POST /api/sync/matches/{match_id}/media/part-url` body `{key, upload_id, part_number}` -> `{"url": str}`.
  - `POST /api/sync/matches/{match_id}/media/complete` body `{key, upload_id, parts: [{part_number, etag}]}` -> `{"size": int}`.
  - `POST /api/sync/matches/{match_id}/media/abort` body `{key, upload_id}` -> `{}`.
- Key containment (the security boundary - test it hard): every route validates

```python
_SYNC_MEDIA_KEY_RE = re.compile(
    r"^matches/(?P<match_id>[A-Za-z0-9._-]+)/shooters/[A-Za-z0-9_-]+/trimmed/[A-Za-z0-9._-]+\.(?:mp4|json)$"
)
```

  and requires `m["match_id"] == match_id` path param, plus the Task 4 mirror check (`origin == "desktop"`). Anything else -> 422. The tenant `users/<uid>/` prefix is applied by the storage layer, never by the client.

- [ ] **Step 1: Failing tests.** Reuse/extend the storage double from `tests/test_hosted_raw_upload.py` (read it first; if its double is local to that file, lift it into `tests/hosted_helpers.py` and update both callers - no duplication). Cases: happy path create -> part-url -> complete round-trips and the double holds the object under the exact key; `key="../../users/other/x.mp4"` -> 422; key whose embedded match_id differs from the path param -> 422; key for a native (`origin='hosted'`) match -> 409; `.wav` extension -> 422.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** - PASS.
- [ ] **Step 5: Commit** - `git commit -m "feat(sync): presigned multipart media push for mirrors (#631)"`.

### Task 6: Read-only mirror gate + origin exposure

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`_match_id_alias` middleware, after the ownership check at line 5841; match-list response model; `ShooterListResponse` gains `origin`)
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` types only if the response type is declared there (keep in lockstep; SPA behavior lands in Task 10)
- Test: `tests/test_mirror_read_only.py`

**Interfaces:**
- Gate: in `_match_id_alias`, the ownership check already fetches the row - change it to keep the record, then:

```python
row = await owner_store.get(match_id)
if row is None:
    return JSONResponse(status_code=404, content={"detail": "not found"})
if (
    row.origin == "desktop"
    and request.method not in ("GET", "HEAD", "OPTIONS")
    and not rest.startswith("match/shares")
):
    return JSONResponse(status_code=403, content={"detail": "read_only_mirror"})
```

  `match/shares` stays writable - creating/revoking share links for a mirror is the whole point.
- Match deletion must keep working for mirrors: find the delete-match route (grep `matches_store.delete` / `delete_match` in server.py). If it is alias-routed (a `rest` path), add its exact `rest` to the exemption tuple; if it is a non-alias route it is untouched - either way, cover it with a test.
- Origin exposure: add `origin: str` ("hosted" | "desktop"; local mode serves "local") to the match-list response the SPA picker consumes AND to `ShooterListResponse` (`server.py:3645`) so the match surface knows it is on a mirror. `/api/sync/*` routes are NOT alias-routed, so the gate never blocks sync itself.

- [ ] **Step 1: Failing tests**: seed a mirror (sync create via Task 4 route + one project doc), then: `POST .../match/shooters` (add shooter) -> 403; `GET .../match/shooters` -> 200 and payload carries `origin: "desktop"`; `POST .../match/shares` -> 200/201 (share creation allowed); same POSTs against a native match -> unchanged behavior; delete-match on the mirror -> succeeds.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run new tests + the share suite** - `pytest tests/test_mirror_read_only.py tests/test_share_routes.py -v` - PASS.
- [ ] **Step 5: Commit** - `git commit -m "feat(sync): desktop mirrors are read-only on hosted (#631)"`.

### Task 7: Local sync engine - digest cache, doc sanitization, push plan

**Files:**
- Create: `src/splitsmith/sync/__init__.py` (empty), `src/splitsmith/sync/state.py`, `src/splitsmith/sync/docs.py`, `src/splitsmith/sync/plan.py`
- Test: `tests/test_sync_plan.py`

**Interfaces (all pure - no network, no global state):**
- `state.py`:

```python
class SyncedItem(BaseModel):
    sha256: str
    size: int
    mtime_ns: int

class SyncState(BaseModel):
    schema_version: int = 1
    last_synced_at: datetime | None = None
    items: dict[str, SyncedItem] = Field(default_factory=dict)  # remote key -> digest

SYNC_STATE_FILE = "sync_state.json"
def load_sync_state(match_root: Path) -> SyncState: ...   # missing/corrupt file -> fresh SyncState()
def save_sync_state(match_root: Path, state: SyncState) -> None:  # atomic_write_json (import from ui.project)
```

- `docs.py`:

```python
STRIPPED_PROJECT_FIELDS = ("raw_dir", "audio_dir", "trimmed_dir", "exports_dir",
                           "probes_dir", "thumbs_dir", "last_scanned_dir")
def sanitize_project_doc(doc: dict) -> dict: ...  # returns a copy with those keys removed
def absolute_path_videos(project: MatchProject) -> list[tuple[int, str]]:
    """(stage_number, path) for every StageVideo whose path is absolute - unsyncable."""
```

  Absolute `StageVideo.path` breaks hosted streaming (`stream_video` requires a relative path for the presign branch) and `video_id` is a hash of the path, so rewriting is forbidden - push VALIDATION fails with a message listing offending stages (conservative choice per CLAUDE.md).
- `plan.py`:

```python
class DocItem(BaseModel):
    kind: Literal["match", "project", "audit"]
    slug: str | None = None
    stage_number: int | None = None
    body: dict

class MediaItem(BaseModel):
    local_path: Path
    remote_key: str      # matches/<match_id>/shooters/<slug>/trimmed/<basename>
    size: int
    mtime_ns: int        # sha256 computed lazily at upload time by push.py

class PushPlan(BaseModel):
    match_id: str
    match_name: str
    docs: list[DocItem]
    media: list[MediaItem]     # only items whose size/mtime differ from sync_state
    media_skipped: int
    errors: list[str]          # non-empty -> push must not run

def build_push_plan(match_root: Path, *, sync_state: SyncState) -> PushPlan: ...
```

  Loads `Match` + shooter roots via `load_match_or_legacy(match_root)`; per shooter loads `MatchProject.load`, collects `absolute_path_videos` into `errors`, emits the match `DocItem`, sanitized project `DocItem`s, one audit `DocItem` per existing `audit/stage<N>.json` (parse stage number from filename), and one `MediaItem` per `trimmed/stage*_cam_*_trimmed.mp4` and its `.params.json` sidecar when present. Skip decision: `sync_state.items.get(key)` matches current `size` + `mtime_ns` -> skipped (rsync-style; sha256 verified only on push). Docs are always pushed - they are small and the upsert is idempotent.

- [ ] **Step 1: Failing tests** building a real match dir under `tmp_path` with `match_model.Match.init`, `match.add_shooter`, `MatchProject.init`, a fake trim file (`(shooter_root / "trimmed").mkdir(); (... / "stage1_cam_abc123_trimmed.mp4").write_bytes(b"x" * 1024)`) + params sidecar + an `audit/stage1.json`. Assert: plan emits 1 match doc + 1 project doc + 1 audit doc + 2 media items with the exact remote keys; project doc body lacks every `STRIPPED_PROJECT_FIELDS` key; second plan with the first plan's digests recorded -> `media == []`, `media_skipped == 2`; touching the mp4 (change mtime + size) puts it back in `media`; a project with an absolute `StageVideo.path` -> `errors` names stage + path; corrupt `sync_state.json` -> fresh state, full plan.
- [ ] **Step 2: Run, verify fail** - `pytest tests/test_sync_plan.py -n0 -v`.
- [ ] **Step 3: Implement the three modules.**
- [ ] **Step 4: Run** - PASS.
- [ ] **Step 5: Commit** - `git commit -m "feat(sync): local push planning - digests, sanitization, trim enumeration (#631)"`.

### Task 8: HostedSyncClient + run_push

**Files:**
- Create: `src/splitsmith/sync/client.py`, `src/splitsmith/sync/push.py`
- Test: `tests/test_sync_push.py`

**Interfaces:**
- `client.py`:

```python
class SyncClientError(RuntimeError):
    """Raised with a user-facing message (401 -> token revoked, 409 -> hosted match exists, etc.)."""

class HostedSyncClient:
    def __init__(self, *, http: httpx.Client, media_http: httpx.Client | None = None) -> None: ...
```

  `http` is pre-configured with `base_url` + `Authorization: Bearer <token>` header (constructed by the caller; tests inject a `TestClient` / MockTransport). `media_http` does the presigned part PUTs (defaults to `httpx.Client()`; tests inject `httpx.MockTransport`). Methods: `ensure_match(match_id, name)`, `put_doc(match_id, item: DocItem) -> int`, `upload_media(match_id, item: MediaItem, *, progress=None) -> str` (create -> loop file chunks of `part_size` -> PUT each to the part url capturing the `ETag` response header -> complete; returns the sha256 computed while streaming the file once). Map 401 -> `SyncClientError("hosted rejected the token - generate a new one on your account page")`, 409 on ensure_match -> `SyncClientError("a hosted match with this id already exists and is not a mirror")`.
- `push.py`:

```python
class PushReport(BaseModel):
    uploaded: int
    skipped: int
    docs: int

def run_push(match_root: Path, *, client: HostedSyncClient,
             on_progress: Callable[[float, str], None] = lambda p, m: None) -> PushReport:
```

  Sequence: `load_sync_state` -> `build_push_plan` -> raise `SyncClientError("\n".join(plan.errors))` if any -> `ensure_match` -> media items one by one (progress by cumulative bytes; after EACH success record `SyncedItem(sha256, size, mtime_ns)` and `save_sync_state` - crash-safe incrementality) -> all docs -> stamp `last_synced_at`, final save. Media before docs is the consistency invariant from the spec - keep the order.

- [ ] **Step 1: Failing tests** with `httpx.MockTransport` doubles for BOTH clients: happy path pushes 2 media + 3 docs in order (assert media requests all precede doc requests), records digests, second run uploads 0; a part PUT failing mid-push leaves already-uploaded items recorded (rerun re-uploads only the failed-and-after items); validation errors abort before any network call; 401 surfaces the token message.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** - `pytest tests/test_sync_push.py tests/test_sync_plan.py -v` - PASS.
- [ ] **Step 5: Commit** - `git commit -m "feat(sync): hosted client + incremental push execution (#631)"`.

### Task 9: Local wiring - prefs, settings endpoints, sync job + trigger/status routes

**Files:**
- Modify: `src/splitsmith/user_config.py` (`GlobalPrefs` gains `hosted_base_url: str | None = None`, `hosted_token: str | None = None`)
- Modify: `src/splitsmith/ui/server.py` (job body registration in `register_job_bodies`; four routes)
- Test: `tests/test_sync_local_endpoints.py`

**Interfaces (all four routes are LOCAL-only - guard with the inverse of the hosted guard, 404 in hosted mode):**
- `GET /api/settings/hosted-sync` -> `{"base_url": str | None, "token_set": bool}` (never the raw token).
- `PUT /api/settings/hosted-sync` body `{"base_url": str, "token": str | None}` -> same shape; `token: null` keeps the stored one, empty string clears it. Persist via `save_global_prefs`.
- `POST /api/match/sync` -> `await state.jobs.submit(kind="sync_match")` -> the `Job` wire model (alias-routed, so the match context contextvars ride into the job per `JobRegistry.submit`). 409 `{"detail": "sync_not_configured"}` when prefs lack url/token.
- `GET /api/match/sync/status` -> `{"configured": bool, "last_synced_at": datetime | None, "stale": bool, "pending_media": int, "errors": [str]}` - built from `load_sync_state` + `build_push_plan` (size/mtime only - no hashing - so it is fast).
- Job body `_run_sync_match(handle)` registered as `"sync_match"` next to `server.py:3353-3360`: resolve match root from the replayed context (mirror how `_run_match_export` gets it), build `httpx.Client(base_url=prefs.hosted_base_url, headers={"Authorization": f"Bearer {prefs.hosted_token}"}, timeout=30.0)` (the `SsiHttpClient` idiom, `scoreboard/http.py:94-115`), run `run_push(..., on_progress=lambda p, m: handle.update(progress=p, message=m))`, `handle.set_result(report.model_dump())`. `SyncClientError` -> re-raise so the job fails with its message verbatim.

- [ ] **Step 1: Failing tests** (local-mode `TestClient` - the default app fixture used by `tests/test_ui_server.py`): settings round-trip masks the token; sync trigger without config -> 409; with config -> job enqueued (assert kind); status on a never-synced match -> `configured: true, stale: true`; hosted-mode app -> all four routes 404.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** - `pytest tests/test_sync_local_endpoints.py tests/test_user_config.py -v` - PASS.
- [ ] **Step 5: Commit** - `git commit -m "feat(sync): local sync job, settings + trigger endpoints (#631)"`.

### Task 10: Hosted SPA - desktop tokens dialog + mirror read-only surface

**Files:**
- Create: `src/splitsmith/ui_static/src/components/account/DesktopTokensDialog.tsx`
- Modify: `src/splitsmith/ui_static/src/components/AccountChip.tsx` (menu entry opening the dialog)
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (listDesktopTokens / createDesktopToken / revokeDesktopToken; `origin` on the match + shooter-list types)
- Modify: `src/splitsmith/ui_static/src/components/match/MatchShell.tsx` (mirror banner + affordance gating)
- Test: `src/splitsmith/ui_static/src/components/account/DesktopTokensDialog.test.tsx`

**Interfaces:**
- Consumes: Task 3 endpoints; `origin` field from Task 6.
- Dialog: list (name, created, last used), create form (name -> shows raw token ONCE in a copy-to-clipboard field with an explicit "you will not see this again" line), revoke with confirm. Overlay per the PR #519 convention (z tokens, body Portal, `useDialogFocus`). Announce state changes accessibly (`aria-live` on the token reveal); never color-only status.
- Mirror surface: when the loaded match has `origin === "desktop"`, `MatchShell` shows a persistent banner ("Synced from a desktop install - read-only here") and mutating controls are not rendered. MVP bar: server already 403s every mutation (Task 6), so the SPA work is the banner plus hiding the obvious write CTAs on the overview (add-shooter, stage editor entry points); a 403 `read_only_mirror` response anywhere else must surface the banner message, not a generic error toast (extend the api.ts error mapping).

- [ ] **Step 1: Failing vitest** for the dialog (mock api.ts module): renders list; create reveals token once; revoke calls through. Plus a MatchShell test: `origin: "desktop"` renders the banner, `origin: "hosted"` does not.
- [ ] **Step 2: Run** - `pnpm test` in `src/splitsmith/ui_static` - FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Verify** - `pnpm typecheck && pnpm test && pnpm exec eslint src/components/account src/components/match/MatchShell.tsx` - clean.
- [ ] **Step 5: Commit** - `git commit -m "feat(sync): hosted token management UI + mirror read-only surface (#631)"`.

### Task 11: Local SPA - sync card, settings dialog, job label

**Files:**
- Create: `src/splitsmith/ui_static/src/components/match/SyncCard.tsx`, `src/splitsmith/ui_static/src/components/match/SyncSettingsDialog.tsx`
- Modify: `src/splitsmith/ui_static/src/pages/Home.tsx` (mount SyncCard), `src/splitsmith/ui_static/src/components/Jobs.tsx` (`KIND_LABEL["sync_match"] = "Sync to hosted"`, `KIND_ICON["sync_match"] = CloudUpload` from lucide), `src/splitsmith/ui_static/src/lib/api.ts` (getSyncStatus / startSync / getSyncSettings / putSyncSettings)
- Test: `src/splitsmith/ui_static/src/components/match/SyncCard.test.tsx`

**Interfaces:**
- Consumes: Task 9 endpoints; `useDeploymentMode()` - the card renders in `"local"` mode only.
- SyncCard states (text + icon, never color alone): not configured (-> opens SyncSettingsDialog), never synced, synced at T (relative time), stale ("N files changed since last sync"), validation errors (list them - absolute-path videos), syncing (live via the jobs polling the panel already does; disable the button while a `sync_match` job for this match is pending/running). After a successful sync: "Open on splitsmith.app" link -> `${base_url}/match/${matchId}` (route is singular `/match/:matchId`).
- SyncSettingsDialog: base URL + token paste (password-type input), saves via PUT, shows `token_set` as a masked placeholder. Same overlay conventions as Task 10.

- [ ] **Step 1: Failing vitest** for SyncCard: hosted mode renders nothing; unconfigured shows setup CTA; stale status shows count; errors listed; button fires startSync.
- [ ] **Step 2: Run** - FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Verify** - `pnpm typecheck && pnpm test && pnpm exec eslint src/components/match src/pages/Home.tsx` - clean. Visual check per memory (bounded headless screenshot, domcontentloaded) of Home with the card.
- [ ] **Step 5: Commit** - `git commit -m "feat(sync): local sync card + settings + job surface (#631)"`.

### Task 12: Verification pass - docker RLS, integration round-trip, docs

**Files:**
- Create: `tests/test_sync_docker.py` (`@pytest.mark.docker`), `tests/test_sync_integration.py` (`@pytest.mark.integration`)
- Modify: `docs/saas-readiness/07-sync-and-migration.md` (banner pointing at the spec; mark tus/`/api/v1`/tarball import as superseded)
- Test: this task IS tests.

**Interfaces:** consumes everything above; changes no production code (fix-forward if it finds bugs, in this task's commits).

- [ ] **Step 1: Docker RLS test** (mirror an existing `-m docker` test's session/GUC setup): two users, each creates a desktop token and a mirror match; assert user A's tenant-scoped store cannot read B's token rows or mirror docs under live Postgres RLS; assert the raw-factory resolver still resolves both users' bearers (the pre-tenant path).
- [ ] **Step 2: Run** - `PATH=~/.claude-tmp/bin:$PATH pytest -m docker tests/test_sync_docker.py -v` - PASS (verify it does not silently skip - memory: docker-path-workaround).
- [ ] **Step 3: Integration round-trip test**: build a local match under `tmp_path` (Task 7's builder; media via `tests/synthetic_media.py`, never gitignored samples); spin up `hosted_app` with the multipart storage double; run `run_push` with `HostedSyncClient(http=<TestClient with bearer>, media_http=<MockTransport writing into the double>)`; then as the hosted user create a share token via the existing `POST .../match/shares` route; then anonymously (cookies cleared) `GET /api/share/<token>/match/shooters` -> 200 with the shooter, and `GET /api/share/<token>/shooters/<slug>/videos/stream?path=...&kind=trim` -> 307 whose Location contains the pushed trim key. Second `run_push` uploads 0. This is the spec's acceptance test.
- [ ] **Step 4: Run** - `pytest tests/test_sync_integration.py -v` - PASS. Then the honesty check from the review-practice doc: pick two load-bearing assertions (the 307 Location; the 403 in Task 6), revert their fix commits locally (`git stash` the gate), watch the tests fail, restore.
- [ ] **Step 5: Doc updates** - banner on doc 07; check off the desktop->hosted half in issue #631 description via a comment when the PR opens.
- [ ] **Step 6: Full gate** - `ruff check . && black --check . && pytest` and `cd src/splitsmith/ui_static && pnpm typecheck && pnpm test`. All green.
- [ ] **Step 7: Commit + PR** - `git commit -m "test(sync): docker RLS + share round-trip integration coverage (#631)"`; open PR `feat(sync): desktop-to-hosted match push MVP (#631)` with body listing the spec deviations recorded in the spec file.

## Task dependency order

1 -> 2 -> 3 -> 4 -> 5 -> 6 (hosted chain); 7 -> 8 -> 9 (local chain, independent of 3-6 until 8's tests); 10 needs 3+6; 11 needs 9; 12 needs all. Parallelizable pairs for subagent dispatch: (4,7), (5,8 up to mocks), (10,11).
