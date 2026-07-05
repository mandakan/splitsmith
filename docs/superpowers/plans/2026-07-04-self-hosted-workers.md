# Self-Hosted Workers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gitea-runner-style registration of home Docker workers, an admin Workers UI, and a priority dispatcher that prefers home workers over the Railway worker.

**Architecture:** Home workers stay direct-DB Procrastinate consumers (same as the Railway worker). Registration is a one-time credential bootstrap. Wakes reach home agents over an outbound SSE channel; the existing Railway launcher becomes one leg of a priority dispatcher with grace-window escalation. Spec: `docs/superpowers/specs/2026-07-04-self-hosted-workers-design.md` - read it first.

**Tech Stack:** FastAPI SSE (StreamingResponse), SQLAlchemy async + Alembic, httpx (agent SSE client), Typer CLI, React SPA (existing hand-rolled api.ts client).

## Global Constraints

- Python 3.11+, type hints, Pydantic at module boundaries, pathlib, Black 110, Ruff.
- No new dependencies (httpx, typer, sqlalchemy, fastapi all already present).
- New copy/comments use "-", never em dash, never "--".
- No legacy/parallel paths: `build_worker_launcher` is REPLACED by the dispatcher, not kept alongside.
- `workers` is an operator-level table: NO `user_id`, NOT under RLS (share_tokens/sessions precedent). The multi-tenant table checklist does not apply; say so in the migration docstring.
- Status in the UI is a word plus color, never color alone (WCAG).
- All commands run from repo root `/Users/mathias/work/splitsmith/.claude/worktrees/mobile-results-viewer`. Python via `uv run`, SPA via `pnpm` in `src/splitsmith/ui_static` (pnpm ONLY, never npm).
- Commit after each task with the trailer lines used in this repo (Co-Authored-By + Claude-Session).

---

### Task 1: `WorkerRow` model + migration

**Files:**
- Modify: `src/splitsmith/db/models.py` (add `WorkerRow` after `ShareTokenRow`, which ends near line 530)
- Create: `alembic/versions/<autogen>_create_workers_table.py`
- Test: `tests/test_workers_store.py` (model columns exercised via Task 2's store tests; this task only needs the migration round-trip)

**Interfaces:**
- Produces: `splitsmith.db.models.WorkerRow` with columns exactly as below; later tasks import it.

- [ ] **Step 1: Add the model**

Follow `ShareTokenRow`'s style (docstring explains the no-RLS decision). Import `Boolean`, `Integer`, `JSON` from sqlalchemy if not already imported in models.py.

```python
class WorkerRow(Base):
    """One compute-worker target (self-hosted box or the Railway service).

    Operator infrastructure, not tenant data: no user_id column and not
    under RLS - the multi-tenant table checklist does not apply. Tokens
    are stored as sha256 hex digests (sessions precedent, NOT the raw
    share_tokens one): a worker token bootstraps infra credentials, so a
    DB leak must not yield usable tokens.

    kind is "self_hosted" (registered via the admin UI) or "railway"
    (a single row seeded at serve boot when the Railway launcher env
    vars are present, so the Railway worker gets the same enabled and
    priority knobs).
    """

    __tablename__ = "workers"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_ulid)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False, default="self_hosted")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    registration_token_hash: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_token_hash: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_wake_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    info: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False
    )
```

Match the `created_at` server-default idiom actually used by `ShareTokenRow` (read it; if it uses `sa.text("(CURRENT_TIMESTAMP)")` style in the model, mirror that).

- [ ] **Step 2: Generate the migration**

Run: `uv run alembic revision --autogenerate -m "create workers table"`
Then edit the generated file: add a module docstring modeled on `alembic/versions/4ab814cb20f5_create_share_tokens_table.py` (state the no-RLS/no-user_id decision and the token-hash rationale). Verify upgrade creates the table with unique constraints on `name`, `registration_token_hash`, `worker_token_hash` and downgrade drops it.

- [ ] **Step 3: Migration round-trip**

Run: `uv run pytest tests/test_db_foundation.py -x -q` (the migration-sanity suite) plus `uv run alembic upgrade head` against a scratch SQLite url if that suite doesn't already cover head-migration. Expected: PASS.

- [ ] **Step 4: Commit** `feat(db): workers table for self-hosted worker registry`

---

### Task 2: `WorkersStore` (registry + token exchange)

**Files:**
- Create: `src/splitsmith/db/workers.py`
- Test: `tests/test_workers_store.py` (mirror the async-SQLite harness of `tests/test_share_tokens_store.py`)

**Interfaces:**
- Produces:
  - `WorkerRecord` frozen dataclass: `id, name, kind, enabled, priority, registered (bool), last_seen_at, last_wake_at, info (dict | None), created_at, token_expires_at`
  - `WorkersStore(session_factory)` with methods:
    - `async create_self_hosted(name: str, *, priority: int = 10, ttl_hours: float = 24.0) -> tuple[WorkerRecord, str]` - returns record + PLAINTEXT registration token
    - `async list() -> list[WorkerRecord]` (ordered by priority asc, name asc)
    - `async get(worker_id: str) -> WorkerRecord | None`
    - `async update(worker_id: str, *, enabled: bool | None = None, priority: int | None = None, name: str | None = None) -> WorkerRecord | None`
    - `async delete(worker_id: str) -> bool` - refuses `kind="railway"` (returns False)
    - `async register(token: str, info: dict) -> tuple[WorkerRecord, str] | None` - single-use exchange; returns record + PLAINTEXT worker token; None for unknown/used/expired (uniform)
    - `async authenticate(worker_token: str) -> WorkerRecord | None` - hash lookup; None for unknown or unregistered
    - `async touch_seen(worker_id: str) -> None`
    - `async touch_wake(worker_ids: list[str]) -> None`
    - `async ensure_railway_row() -> None` - idempotent upsert of the single `kind="railway"` row (name `"railway"`, priority 100, enabled True on create only - never reset an operator's toggle)
    - `async list_enabled() -> list[WorkerRecord]` - enabled AND (registered or railway), ordered by priority asc
- Tokens: `secrets.token_urlsafe(32)`; storage is `hashlib.sha256(token.encode()).hexdigest()`.

- [ ] **Step 1: Write failing tests** covering: create returns plaintext token and pending record; register succeeds once, second call returns None; expired token returns None (freeze time by passing `ttl_hours=0`); authenticate round-trips the worker token and rejects garbage; update flips enabled/priority; delete refuses railway; `ensure_railway_row` is idempotent and preserves a flipped `enabled=False`; `list_enabled` excludes disabled and unregistered self-hosted rows but includes railway.
- [ ] **Step 2: Run** `uv run pytest tests/test_workers_store.py -x -q` - expect import errors/failures.
- [ ] **Step 3: Implement** `src/splitsmith/db/workers.py` (module docstring explains operator-scope + hashing, referencing `share_tokens.py` as the contrasting precedent). Reuse the `_aware()` naive-datetime coercion trick from `share_tokens.py` for expiry comparisons on SQLite.
- [ ] **Step 4: Run tests** - PASS.
- [ ] **Step 5: Commit** `feat(db): WorkersStore with single-use registration exchange`

---

### Task 3: Wake channel registry

**Files:**
- Create: `src/splitsmith/worker_channel.py`
- Test: `tests/test_worker_channel.py`

**Interfaces:**
- Produces `WakeChannelRegistry`:
  - `connect(worker_id: str) -> asyncio.Queue[str]` - one live channel per worker; a second connect pushes `"replaced"` onto the old queue and supersedes it
  - `disconnect(worker_id: str, queue: asyncio.Queue[str]) -> None` - no-op if a newer queue took over
  - `push(worker_id: str, event: str) -> bool` - False when not connected
  - `connected_ids() -> frozenset[str]`
- Events used by later tasks: `"wake"`, `"disabled"`, `"enabled"`, `"replaced"`.

- [ ] **Step 1: Failing tests**: push to unconnected id returns False; connect + push("wake") is readable from the queue; reconnect supersedes (old queue receives "replaced", push lands on the new queue); disconnect with the stale queue does not remove the new one; connected_ids reflects state.
- [ ] **Step 2: Run** `uv run pytest tests/test_worker_channel.py -x -q` - FAIL.
- [ ] **Step 3: Implement** (plain dict, no locks needed - single event loop; module docstring must state the per-replica seam: in-process registry, wakes best-effort, safety nets cover).
- [ ] **Step 4: Run** - PASS.
- [ ] **Step 5: Commit** `feat: in-process wake-channel registry`

---

### Task 4: Dispatcher replaces the single launcher

**Files:**
- Modify: `src/splitsmith/worker_trigger.py`
- Test: `tests/test_worker_trigger.py` (extend; delete tests that pin the removed `build_worker_launcher` behavior - obsolete-test deletion is correct here)

**Interfaces:**
- Consumes: `WorkersStore.list_enabled/touch_wake` (Task 2), `WakeChannelRegistry.push/connected_ids` (Task 3), existing `RailwayLauncherConfig`, `load_railway_config`, `make_worker_active_checker`, `make_boot_retrigger` (unchanged), `wrap_deferrer` (unchanged - dispatcher satisfies the same `WorkerLauncher` protocol).
- Produces:
  - `class WorkerDispatcher` implementing `schedule()` / `async trigger() -> bool` with ctor `(store: WorkersStore, registry: WakeChannelRegistry, *, railway: RailwayLauncherConfig | None, worker_active: WorkerActiveCheck | None, pending_jobs: Callable[[], Awaitable[int]], cooldown_seconds: float = 30.0, grace_seconds: float = 90.0, transport: httpx.AsyncBaseTransport | None = None)`
  - `build_worker_dispatcher(store, registry, *, worker_active, pending_jobs, transport=None) -> WorkerDispatcher | None` - returns None iff `SPLITSMITH_WORKER_LAUNCHER=none`; otherwise a dispatcher whose railway leg is `load_railway_config()` (may be None). `build_worker_launcher` is DELETED.
  - `make_pending_jobs_counter(session_factory) -> Callable[[], Awaitable[int]]` - extract the `count(*) FROM procrastinate_jobs WHERE status='todo'` query already inside `make_boot_retrigger` and reuse it in both places.

Behavior of `trigger()` (keep the never-raise + cooldown + heartbeat-gate contract documented on `RailwayWorkerLauncher.trigger`, lines 143-171):
1. Cooldown (30s) and lock, stamp-before-check, exactly as today.
2. Heartbeat gate via `worker_active` - live drain means do nothing.
3. `await self._wake_from(min_priority_exclusive=None)`.

`_wake_from(min_priority_exclusive)`: load `store.list_enabled()`, drop rows with priority <= min_priority_exclusive (when not None), group by priority ascending into tiers, walk tiers:
- self-hosted target available iff `registry.push(id, "wake")` succeeds (push IS the availability check)
- railway target available iff `self._railway is not None`; wake = the existing `_redeploy` GraphQL call (move `_redeploy` + mutation onto the dispatcher; `RailwayWorkerLauncher` class is deleted)
- first tier with at least one successful wake: `store.touch_wake(ids)`; if any woken target was self-hosted, arm the grace task; return True. A tier where every push failed falls through to the next tier.

Grace task (held in `self._tasks` like `schedule()` does): `await asyncio.sleep(grace_seconds)`; if `await pending_jobs() > 0` and not `await worker_active()`: `await self._wake_from(min_priority_exclusive=<woken tier priority>)`. Escalation bypasses the cooldown (it is part of the same logical trigger); it must also never raise.

- [ ] **Step 1: Failing tests** (use the existing test file's harness: httpx `MockTransport` for the Railway GraphQL call, plus an async-SQLite `WorkersStore` and a real registry):
  - home worker connected + enabled -> push received, railway transport NOT called
  - home worker registered but not connected -> railway called immediately (tier fall-through)
  - railway row disabled -> railway never called even with config present
  - no railway config + no connected homes -> trigger returns False, nothing woken
  - grace escalation: grace_seconds=0.01, pending_jobs returns 1, worker_active False -> railway called after the wake; with worker_active True -> railway NOT called
  - cooldown still suppresses a second trigger; heartbeat gate still short-circuits
  - `make_pending_jobs_counter` counts todo rows (reuse the trick the existing boot-retrigger test uses)
- [ ] **Step 2: Run** `uv run pytest tests/test_worker_trigger.py -x -q` - FAIL.
- [ ] **Step 3: Implement.** Update the module docstring (it currently says "the only implementation today is Railway"). Keep `make_boot_retrigger` signature unchanged but have it call `make_pending_jobs_counter` internally.
- [ ] **Step 4: Run full trigger suite** - PASS.
- [ ] **Step 5: Commit** `feat: priority dispatcher with grace-window escalation replaces single Railway launcher`

---

### Task 5: Admin gate + `is_admin` on /api/me

**Files:**
- Modify: `src/splitsmith/auth.py` (add `is_admin: bool = False` to `User`, line 27)
- Modify: `src/splitsmith/ui/server.py`:
  - env const `SPLITSMITH_ADMIN_EMAILS_ENV = "SPLITSMITH_ADMIN_EMAILS"` next to the other env consts (~line 4132)
  - `AppState.admin_emails: frozenset[str] = frozenset()` near `public_base_url` (~line 900)
  - parse in `_apply_hosted_mode_wiring` (~line 4277): lowercase, comma-separated, strip empties
  - `require_admin` dependency next to `get_current_user` (~line 4612): `user = Depends(get_current_user)`; raise `HTTPException(403, "admin access required")` unless `user.email.lower() in state.admin_emails`; returns the user with `is_admin=True` set
  - `get_me` (line 7441): return `user.model_copy(update={"is_admin": user.email.lower() in state.admin_emails})`
- Test: `tests/test_auth_routes.py` (extend)

**Interfaces:**
- Produces: `require_admin` FastAPI dependency (closure inside `create_app`, like `get_current_user`); `User.is_admin`.

- [ ] **Step 1: Failing tests**: /api/me carries `is_admin: false` by default; with `SPLITSMITH_ADMIN_EMAILS` matching the signed-in hosted user (use `tests/hosted_helpers.py` harness), /api/me shows true; comparison is case-insensitive.
- [ ] **Step 2: Run** - FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest tests/test_auth_routes.py -x -q` - PASS.
- [ ] **Step 5: Commit** `feat: SPLITSMITH_ADMIN_EMAILS gate + is_admin on /api/me`

---

### Task 6: Worker-facing endpoints (register + SSE channel) and wiring

**Files:**
- Modify: `src/splitsmith/ui/server.py`
- Test: `tests/test_worker_routes.py` (new; base on `tests/test_share_routes.py` harness)

**Interfaces:**
- Consumes: `WorkersStore` (Task 2), `WakeChannelRegistry` (Task 3), dispatcher builder (Task 4), admin state (Task 5).
- Produces (wiring, in `_apply_hosted_mode_wiring`):
  - `state.workers_store = WorkersStore(session_factory)` (raw factory - not tenant-scoped)
  - non-worker branch (line 4322): `state.wake_channels = WakeChannelRegistry()`; build the dispatcher via `build_worker_dispatcher(state.workers_store, state.wake_channels, worker_active=make_worker_active_checker(session_factory), pending_jobs=make_pending_jobs_counter(session_factory))`; when not None wrap the deferrer and build boot_retrigger with it (replacing the old launcher block)
  - `state.worker_credentials: dict` built from env: `{"database_url": url, "public_url": state.public_base_url, "s3": {...} | None}` with the five `SPLITSMITH_S3_*` values when the bucket is set
  - Railway-row seeding: extend `_boot_retrigger_lifespan` (line 4485) into a hosted boot lifespan that first runs `await state.workers_store.ensure_railway_row()` when `load_railway_config()` is not None, then the retrigger. It must no longer return None just because retrigger is None - return the lifespan when either piece exists.
- Produces (endpoints, defined with the other routes; add both paths to `_PUBLIC_API_PATHS`, line 679, with a comment why - token IS the auth):
  - `POST /api/workers/register`, body `{token: str, info: dict = {}}`. Local mode (`state.workers_store is None`) and every failure -> uniform `404 {"detail": "not found"}`. Success -> `{"worker_id", "worker_token", "credentials": state.worker_credentials}`.
  - `GET /api/workers/channel`, `Authorization: Bearer <worker_token>`. Unknown/missing token or local mode -> uniform 404. Success -> `StreamingResponse(media_type="text/event-stream")`:
    - on connect: `await store.touch_seen(id)`; if `state.boot_retrigger` is set, `asyncio.create_task(state.boot_retrigger())` (throttled internally - this closes the "jobs queued while agent was offline" gap); if the row is disabled, send `event: disabled` first
    - loop: `asyncio.wait_for(queue.get(), timeout=20)` -> `f"event: {name}\ndata: {{}}\n\n"`; on timeout emit keepalive comment `": ka\n\n"`
    - `finally`: `registry.disconnect(id, queue)` + `store.touch_seen(id)` (best-effort, swallow errors - client is gone)
- Contract for Task 8: SSE event names are exactly `wake`, `disabled`, `enabled`, `replaced`.

- [ ] **Step 1: Failing tests**: register with unknown token -> 404 with the exact uniform body; happy path returns worker_token + credentials echoing env; second register with same token -> 404; channel with bad bearer -> 404; channel with good bearer streams a `wake` event after `state.wake_channels.push(id, "wake")` (read the stream with the TestClient in a thread or httpx ASGI transport - copy the SSE-read helper approach if one exists, else read the first two chunks with `iter_lines`); disabled row gets `event: disabled` as first event. Also: local mode (no wiring) -> both endpoints 404.
- [ ] **Step 2: Run** `uv run pytest tests/test_worker_routes.py -x -q` - FAIL. **Step 3: Implement.** **Step 4: Run** new file + `tests/test_worker_trigger.py` + `tests/test_share_routes.py` (gate/middleware neighbors) - PASS.
- [ ] **Step 5: Commit** `feat: worker registration + SSE wake channel endpoints`

---

### Task 7: Admin workers API

**Files:**
- Modify: `src/splitsmith/ui/server.py` (routes near the /api/me cluster)
- Test: `tests/test_admin_workers_routes.py` (new)

**Interfaces:**
- Consumes: `require_admin` (Task 5), `WorkersStore` (Task 2), `state.wake_channels` (Task 6).
- Produces endpoints (all `Depends(require_admin)`; 404 in local mode where `state.workers_store is None`):
  - `GET /api/admin/workers` -> `{"workers": [WorkerView...]}` where `WorkerView = {id, name, kind, enabled, priority, status, registered, last_seen_at, last_wake_at, info}` and `status` is derived: `"disabled"` if not enabled; else `"online"` if id in `wake_channels.connected_ids()` (railway: `"online"` iff `load_railway_config()` present); else `"pending"` if self-hosted and not registered; else `"offline"`.
  - `POST /api/admin/workers` body `{name: str, priority: int = 10}` -> 409 on duplicate name; else `{worker: WorkerView, registration_token: str, expires_at, docker_command: str}` where docker_command is the copy-paste line using `state.public_base_url` and image `ghcr.io/<owner>/splitsmith:latest` - read the actual image ref from the deploy workflow (`.github/workflows/`) and use that.
  - `PATCH /api/admin/workers/{worker_id}` body `{enabled?, priority?, name?}` -> updated WorkerView; when `enabled` flips, push `"disabled"`/`"enabled"` to a connected channel.
  - `DELETE /api/admin/workers/{worker_id}` -> push `"disabled"` to a connected channel, then delete; 400 when the store refuses (railway row); 404 unknown id.
- Pydantic request/response models beside the endpoints (repo rule: Pydantic across boundaries).

- [ ] **Step 1: Failing tests**: non-admin user -> 403; admin CRUD happy paths; duplicate name 409; delete railway -> 400; PATCH enabled=false pushes `disabled` on the registry; status derivation (pending vs offline vs online via a registry connect).
- [ ] **Step 2: Run** - FAIL. **Step 3: Implement.** **Step 4: Run** - PASS.
- [ ] **Step 5: Commit** `feat: admin workers CRUD API`

---

### Task 8: Agent runtime + CLI command

**Files:**
- Create: `src/splitsmith/agent.py`
- Modify: `src/splitsmith/cli.py` (new command after `worker`, line 674)
- Test: `tests/test_agent.py`

**Interfaces:**
- Consumes: register endpoint + SSE contract (Task 6), `splitsmith.queue.run_worker` (existing).
- Produces:
  - `AgentState` Pydantic model: `server_url, worker_id, worker_token, credentials (dict)`; persisted as `agent.json` (mode 0600) in the state dir
  - `async run_agent(server_url: str, *, registration_token: str | None, state_dir: Path, concurrency: int = 1, transport: httpx.AsyncBaseTransport | None = None) -> None`
  - `register(server_url, token, state_dir, transport) -> AgentState` (sync bootstrap, httpx POST, writes agent.json)
  - `apply_credentials(state: AgentState) -> None`: sets `SPLITSMITH_MODE=hosted`, `SPLITSMITH_DATABASE_URL`, `SPLITSMITH_PUBLIC_URL` (= server_url), `SPLITSMITH_EMAIL_BACKEND` setdefault `console`, and the five `SPLITSMITH_S3_*` vars when the bundle has s3
  - CLI: `splitsmith agent --server-url URL [--token TOKEN] [--state-dir PATH] [--concurrency N]`; `--state-dir` defaults to env `SPLITSMITH_AGENT_STATE_DIR` else `/data`; missing agent.json AND missing --token -> exit 2 with a clear message

Runtime shape (two coroutines + `asyncio.Event`):
- reader: `client.stream("GET", f"{server}/api/workers/channel", headers=..., timeout=httpx.Timeout(10, read=90))`; parse SSE lines (`event: <name>`); `wake`/`enabled` -> `wake_event.set()` (unless disabled flag); `disabled` -> set disabled flag; `replaced` -> log + reconnect; HTTP 404 -> log "token revoked or worker deleted" and exit code 3; connection errors -> exponential backoff 1s..60s, reset on a connect that lives >60s
- drainer: `await wake_event.wait()`; clear; `await run_worker(db_url, concurrency=concurrency, wait=False)`; loop (a wake landing mid-drain re-sets the event, triggering one more drain - never lost, never concurrent)

- [ ] **Step 1: Failing tests** (httpx `MockTransport` throughout; monkeypatch `run_worker` with a recording stub):
  - `register` writes agent.json with 0600 and round-trips `AgentState`
  - `apply_credentials` sets the env vars (use `monkeypatch.setenv` isolation)
  - SSE parsing: feed a canned stream `": ka\n\n" + "event: wake\ndata: {}\n\n"` -> drain stub called once
  - disabled event suppresses a following wake; enabled re-allows and triggers a catch-up drain
  - a wake during an in-flight drain causes exactly one follow-up drain
  - 404 on channel -> SystemExit(3)
- [ ] **Step 2: Run** `uv run pytest tests/test_agent.py -x -q` - FAIL. **Step 3: Implement** agent.py + the CLI command (docstring in the style of `worker`'s, explaining the drain-on-wake model and that Neon sleeps between drains). **Step 4: Run** - PASS.
- [ ] **Step 5: Commit** `feat: splitsmith agent - self-hosted worker runtime`

---

### Task 9: SPA - admin Workers page

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (add `is_admin: boolean` to `AuthUser` ~line 1149; add admin worker types + `adminListWorkers/adminCreateWorker/adminUpdateWorker/adminDeleteWorker` next to the share api methods ~line 3307)
- Modify: `src/splitsmith/ui_static/src/App.tsx` (route `admin/workers` under the `<Route element={<AppShell />}>` block, lines 222-229)
- Modify: `src/splitsmith/ui_static/src/components/AppShell.tsx` (nav link "Workers" visible iff `useAuth().user?.is_admin`)
- Create: `src/splitsmith/ui_static/src/pages/AdminWorkers.tsx`
- Create: `src/splitsmith/ui_static/src/components/admin/RegisterWorkerDialog.tsx`

**Interfaces:**
- Consumes: Task 7 endpoint shapes verbatim; existing `request<T>()` wrapper, `StatusPill`, `Card`, `Portal`, `useDialogFocus` (copy the ShareDialog skeleton, `src/components/results/ShareDialog.tsx`).
- Produces: `/admin/workers` page.

Page behavior:
- Load list on mount; row layout follows the Pick.tsx section/article pattern; columns: name, kind, StatusPill (`online` -> tone "exported", `offline` -> "awaiting", `disabled` -> "archived", `pending` -> "in-progress"; label = the word), priority (inline number input, PATCH on blur/Enter), last seen (relative), enabled toggle (PATCH), delete button with confirm (native `confirm()` is NOT used elsewhere - reuse whatever confirm pattern the share dialog/delete flows use).
- "Register worker" button -> RegisterWorkerDialog: name + priority fields, POST, then swaps to the token screen: token shown once in a mono block, the `docker_command` from the response with a Copy button (reuse ShareDialog's copy-timer pattern), and the warning copy "This token is shown once. The agent keeps database credentials after registration - deleting the worker does not revoke them."
- Non-admin visiting /admin/workers: render nothing but a short "Admin access required" note (the API 403s anyway).
- Error handling: catch `ApiError`, show `.detail` inline (ShareDialog pattern).

- [ ] **Step 1: Implement** (no SPA test runner exists - verification is Step 2).
- [ ] **Step 2: Verify**: `cd src/splitsmith/ui_static && pnpm typecheck && pnpm build && pnpm exec eslint src/pages/AdminWorkers.tsx src/components/admin/RegisterWorkerDialog.tsx src/components/AppShell.tsx src/lib/api.ts src/App.tsx` - all clean (scoped eslint; whole-repo lint has known pre-existing failures).
- [ ] **Step 3: Commit** `feat(ui): admin Workers page with runner-style registration dialog`

---

### Task 10: Docs + full local gates

**Files:**
- Modify: `SPEC.md` (short "Self-hosted workers" subsection near the hosted/worker-fleet material: registration flow, wake channel, dispatch tiers, env vars `SPLITSMITH_ADMIN_EMAILS` / `SPLITSMITH_AGENT_STATE_DIR`, the credential-revocation limitation)
- Modify: `CLAUDE.md` only if the worker-fleet paragraph it already contains now misstates reality (it describes detection, likely untouched).

- [ ] **Step 1: Write docs.**
- [ ] **Step 2: Full gates**: `uv run ruff check . && uv run black --check . && uv run pytest -x -q` - all green.
- [ ] **Step 3: Docker smoke** (DB change rule): ensure docker on PATH per the known workaround (symlink in `~/.claude-tmp/bin`), then `uv run pytest -m docker -q` - green.
- [ ] **Step 4: Grep added lines for em dashes / "--" in new copy**: `git diff origin/main | grep "^+" | grep -n "—\|--"` and fix hits in prose (code flags like `--one-shot` are fine).
- [ ] **Step 5: Commit** `docs: self-hosted workers spec section`

---

### Task 11: PR, merge, staging config

- [ ] **Step 1:** Push branch, open PR to main titled `feat: self-hosted workers - registration, wake channel, priority dispatch`; body summarizes the spec decisions + deferred items; standard PR trailer.
- [ ] **Step 2:** CI green; merge (user pre-authorized merge-to-staging in this session).
- [ ] **Step 3:** Set `SPLITSMITH_ADMIN_EMAILS=m@thias.se` on the staging serve service via the Railway tooling (staging environment only for now).
- [ ] **Step 4:** After the main->staging deploy finishes, verify on staging: /api/me shows is_admin for the operator account; /admin/workers renders; create a worker, confirm the token dialog; hit the register endpoint with a garbage token -> 404.
- [ ] **Step 5:** Hand the user the docker run command for their home box + the test checklist (register, enqueue a detect, watch the home agent drain it, disable it, confirm Railway fallback).
