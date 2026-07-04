# Public Share Links MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An owner of a hosted match can mint unguessable, revocable links; anyone with the link gets the read-only Results surface (overview + stage playback with video) with no account.

**Architecture:** A `share_tokens` table + per-user store; a `_share_alias` HTTP middleware that validates the token, pins the owner's tenant, and rewrites the path to `/api/matches/{match_id}/...` so the existing `_match_id_alias` middleware and route handlers do all the work behind a strict GET whitelist. The SPA reuses the existing Results pages under a new `/share/:token` route subtree via a share-aware `scopeRequestPath` and `useMatchHref`.

**Tech Stack:** FastAPI middleware closures in `create_app`, SQLAlchemy 2.x async + Alembic, React Router v6 + existing api.ts plumbing, pnpm-only ui_static.

Spec: `docs/superpowers/specs/2026-07-04-public-share-links-design.md`.

## Global Constraints

- Python 3.11+, type hints everywhere, Black line length 110, Ruff.
- New prose/comments use single ASCII dash "-", never em dash, never "--".
- `uv` for Python, `pnpm` for ui_static (never npm/pip).
- No new dependencies.
- Uniform `404 {"detail": "not found"}` for unknown/revoked/expired tokens and non-whitelisted share paths - never reveal which case hit.
- `match_id` for share requests comes ONLY from the token row, never from client input.
- Commit after every task; enumerate paths in `git add` explicitly (no globs).

## Reference map (read before coding a task)

- Auth gate middleware: `src/splitsmith/ui/server.py:4822` (`_auth_gate`), public paths frozenset at `server.py:658`.
- Match alias middleware: `server.py:4711` (`_match_id_alias`) - the pattern `_share_alias` copies.
- Starlette ordering: later-registered `@app.middleware("http")` runs OUTER. Current order: `_auth_gate` (outer, registered ~4822) -> `_match_id_alias` (inner, ~4711). `_share_alias` must be registered BETWEEN them in code (after `_match_id_alias`, before `hosted = _hosted_mode_active()` at ~4820) so it runs inside `_auth_gate` and outside `_match_id_alias`.
- Hosted wiring (session_factory, `state.public_base_url`, `_build_tenant`): `server.py:4201-4291`.
- `TenantContext`: `server.py:715`; `current_tenant` ContextVar: `server.py:746`; `AppState.build_tenant`: `server.py:936`.
- Store pattern to copy: `src/splitsmith/db/matches.py` (`PostgresMatchStore`), tests in `tests/test_matches_store.py`.
- Hosted test-app fixture pattern: `tests/test_auth_routes.py` (`hosted_env` + `hosted_app` + `_CapturingSender` login dance).
- Alembic head to chain from: `20aa31aeca11`.
- SPA scoping: `src/splitsmith/ui_static/src/lib/api.ts:1554-1574` (`currentMatchIdFromLocation`, `MATCH_SCOPED_PREFIXES`, `scopeRequestPath`).
- Link builder: `src/splitsmith/ui_static/src/lib/matchHref.ts` (`useMatchHref`).
- Auth guard: `src/splitsmith/ui_static/src/lib/auth.tsx` (`AuthGate`, redirects anon -> /login in hosted mode).
- Shell contract: `src/splitsmith/ui_static/src/components/match/MatchShell.tsx:52` (`MatchShellOutletContext = {project, health, shooters, refresh}`) and its data fetches at `MatchShell.tsx:211-289` (`api.getProject`, `api.listMatchShooters`, `pickDefaultShooterSlug`).
- Routes: `src/splitsmith/ui_static/src/App.tsx` (`match/:matchId` subtree; `results` + `results/:slug/:stage` under `MatchShell`).

---

### Task 1: `share_tokens` model + migration

**Files:**
- Modify: `src/splitsmith/db/models.py` (append after `ComputeJobRow`)
- Modify: `src/splitsmith/db/__init__.py` (export `ShareTokenRow` alongside the other rows - check existing `__init__` exports first and mirror them)
- Create: `alembic/versions/<generated>_create_share_tokens_table.py`
- Test: covered by Task 2's store tests (SQLite `create_all` exercises the model); migration exercised by the docker smoke in Task 9.

**Interfaces:**
- Produces: `ShareTokenRow` with columns `id: str` (ULID PK), `user_id: str` (FK users.id, CASCADE, indexed), `match_id: str`, `token: str` (unique), `created_at`, `revoked_at: datetime | None`, `expires_at: datetime | None`.

- [ ] **Step 1: Add the model** to `models.py`:

```python
class ShareTokenRow(Base):
    """One row per share link (public read-only match access, #349).

    The raw token IS the capability: 256 bits from ``secrets.token_urlsafe``,
    stored raw (not hashed) so the owner's share dialog can re-display the
    link. This is a deliberate departure from ``sessions`` /
    ``magic_link_tokens``: a leaked share token yields read-only access to
    one match and dies on revocation, not an account takeover.

    Not under RLS, following the ``sessions`` precedent: anonymous
    resolution runs before any ``app.user_id`` GUC exists. The unique-token
    lookup bounds the anonymous path; owner-management queries filter by
    ``user_id`` explicitly (see ``ShareTokenStore``).

    Revoke sets ``revoked_at`` instead of deleting - the share dialog keeps
    showing revoked links as an audit trail. ``expires_at`` is honored by
    the resolver but always NULL from the MVP UI.
    """

    __tablename__ = "share_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_ulid)
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    match_id: Mapped[str] = mapped_column(String, nullable=False)
    token: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<ShareTokenRow user_id={self.user_id!r} match_id={self.match_id!r} "
            f"revoked={self.revoked_at is not None}>"
        )
```

- [ ] **Step 2: Generate the migration.** Look at the newest file in `alembic/versions/` for house style. Run:

```bash
uv run alembic revision -m "create share_tokens table"
```

Fill it in by hand (autogenerate needs a live DB): `down_revision = "20aa31aeca11"`. `upgrade()` = `op.create_table` mirroring the model (String cols, `sa.ForeignKey("users.id", ondelete="CASCADE")`, unique constraint on `token`, index on `user_id`, `server_default=sa.func.now()` on `created_at`). NO RLS statements - add a comment in the migration saying share_tokens follows the sessions precedent (resolved pre-GUC). `downgrade()` = `op.drop_table("share_tokens")`.

- [ ] **Step 3: Sanity check** - model imports and table creates:

```bash
uv run python -c "
import asyncio
from splitsmith.db import Base, create_engine
from splitsmith.db.models import ShareTokenRow
e = create_engine('sqlite+aiosqlite:///:memory:')
async def go():
    async with e.begin() as c:
        await c.run_sync(Base.metadata.create_all)
asyncio.run(go())
print('ok')
"
```

Expected: `ok`.

- [ ] **Step 4: Commit** (`git add` the three exact files):

```bash
git commit -m "feat(db): share_tokens table for public share links"
```

---

### Task 2: `ShareTokenStore` + anonymous `resolve`

**Files:**
- Create: `src/splitsmith/db/share_tokens.py`
- Test: `tests/test_share_tokens_store.py`

**Interfaces:**
- Consumes: `ShareTokenRow` from Task 1.
- Produces:
  - `@dataclass ShareToken: id: str; match_id: str; token: str; created_at: datetime; revoked_at: datetime | None`
  - `class ShareTokenStore(session_factory: async_sessionmaker, *, user_id: str)` with:
    - `async create(match_id: str) -> ShareToken` (mints `secrets.token_urlsafe(32)`)
    - `async list_for_match(match_id: str) -> list[ShareToken]` (newest first, revoked included)
    - `async revoke(share_id: str) -> bool` (False when not found / not owned; idempotent True if already revoked)
  - `@dataclass ResolvedShare: owner_user_id: str; match_id: str`
  - `async def resolve_share_token(session_factory: async_sessionmaker, token: str) -> ResolvedShare | None` - module-level, returns None for missing OR revoked OR expired (`expires_at <= now(UTC)`); the three are indistinguishable.

Copy the docstring conventions, `user_id` fail-loud constructor guard, and per-method `user_id` filter from `PostgresMatchStore` (`src/splitsmith/db/matches.py`). Every store method's WHERE includes `ShareTokenRow.user_id == self._user_id`.

- [ ] **Step 1: Write failing tests** in `tests/test_share_tokens_store.py`. Mirror the fixture style of `tests/test_matches_store.py` (in-memory aiosqlite engine + `create_all`). Cover:

```python
# - create() returns a ShareToken with a 43-char urlsafe token; row lands in the table
# - list_for_match() returns newest first and includes revoked rows
# - revoke() sets revoked_at and returns True; unknown id returns False
# - revoke() on another user's share id returns False (isolation)
# - list_for_match() never returns another user's rows (isolation)
# - resolve_share_token(): live token -> ResolvedShare(owner_user_id, match_id)
# - resolve_share_token(): unknown token -> None
# - resolve_share_token(): revoked token -> None
# - resolve_share_token(): expires_at in the past -> None (seed expires_at directly on the row)
```

Write each as a real test function with asserts (about 9 tests). Use two store instances (`user_id="user-a"` / `"user-b"`) against one engine for the isolation cases.

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_share_tokens_store.py -q
```

Expected: FAIL with `ModuleNotFoundError: splitsmith.db.share_tokens`.

- [ ] **Step 3: Implement** `src/splitsmith/db/share_tokens.py`:

```python
"""Per-user share-token store + anonymous resolver (public share links, #349).

``ShareTokenStore`` is the owner-side management surface, constructed
per-request with the resolved user id like ``PostgresMatchStore``.
``resolve_share_token`` is the anonymous path: it takes the raw
(non-tenant) session factory because share_tokens is not under RLS -
the unique-token lookup is the isolation boundary, resolved before any
``app.user_id`` GUC exists (sessions precedent).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import ShareTokenRow


@dataclass(frozen=True)
class ShareToken:
    id: str
    match_id: str
    token: str
    created_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True)
class ResolvedShare:
    owner_user_id: str
    match_id: str


def _to_share_token(row: ShareTokenRow) -> ShareToken:
    return ShareToken(
        id=row.id,
        match_id=row.match_id,
        token=row.token,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
    )


class ShareTokenStore:
    """Owner-scoped view of ``share_tokens``.

    Multi-tenant invariant: every statement filters on
    ``ShareTokenRow.user_id == self._user_id``. Isolation tests in
    ``test_share_tokens_store.py`` guard it - add one per new method.
    """

    def __init__(self, session_factory: async_sessionmaker, *, user_id: str) -> None:
        if not isinstance(user_id, str) or not user_id:
            raise ValueError(
                "ShareTokenStore requires a non-empty user_id; "
                f"got {user_id!r}. The auth layer must resolve a real "
                "user before constructing the per-request store."
            )
        self._session_factory = session_factory
        self._user_id = user_id

    async def create(self, match_id: str) -> ShareToken:
        row = ShareTokenRow(
            user_id=self._user_id,
            match_id=match_id,
            token=secrets.token_urlsafe(32),
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_share_token(row)

    async def list_for_match(self, match_id: str) -> list[ShareToken]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(ShareTokenRow)
                    .where(
                        ShareTokenRow.user_id == self._user_id,
                        ShareTokenRow.match_id == match_id,
                    )
                    .order_by(ShareTokenRow.created_at.desc(), ShareTokenRow.id.desc())
                )
            ).scalars()
            return [_to_share_token(r) for r in rows]

    async def revoke(self, share_id: str) -> bool:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(ShareTokenRow).where(
                        ShareTokenRow.user_id == self._user_id,
                        ShareTokenRow.id == share_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            if row.revoked_at is None:
                row.revoked_at = datetime.now(UTC)
                await session.commit()
            return True


async def resolve_share_token(
    session_factory: async_sessionmaker, token: str
) -> ResolvedShare | None:
    """Resolve a raw share token to its owner + match, or None.

    None covers missing, revoked, and expired alike - callers must not be
    able to distinguish them (uniform 404 at the HTTP layer).
    """
    if not token:
        return None
    async with session_factory() as session:
        row = (
            await session.execute(select(ShareTokenRow).where(ShareTokenRow.token == token))
        ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    if row.expires_at is not None:
        expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
        if expires <= datetime.now(UTC):
            return None
    return ResolvedShare(owner_user_id=row.user_id, match_id=row.match_id)
```

Note the naive-datetime guard on `expires_at`: SQLite returns naive datetimes; compare in UTC. Check how `magic_link.py` handles the same problem and match it.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_share_tokens_store.py -q
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/db/share_tokens.py tests/test_share_tokens_store.py
git commit -m "feat(db): ShareTokenStore + anonymous resolve_share_token"
```

---

### Task 3: Tenant wiring for share tokens

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`TenantContext` ~715, `AppState` properties ~780-950, `_apply_hosted_mode_wiring` ~4230-4291)

**Interfaces:**
- Consumes: `ShareTokenStore`, `resolve_share_token` from Task 2.
- Produces (used by Tasks 4-5):
  - `TenantContext.share_tokens: ShareTokenStore | None` (new field, default None for local mode)
  - `AppState.share_tokens` property -> `current_tenant.get().share_tokens` when a tenant is pinned, else `None` (mirror the `storage` property at `server.py:920-930`)
  - `AppState.resolve_share_token: Callable[[str], Awaitable[ResolvedShare | None]] | None` - plain attribute, set only by hosted wiring, built over the RAW `session_factory` (share_tokens is not RLS-scoped): `state.resolve_share_token = lambda tok: resolve_share_token(session_factory, tok)` (as a proper closure, not a lambda, matching house style).

- [ ] **Step 1: Wire it.** Add the `TenantContext` field, the `AppState` property + attribute (default `None`), extend `_build_tenant` with `share_tokens=ShareTokenStore(tenant_factory, user_id=user_id)` (tenant factory is fine - the store filters by user_id explicitly and the GUC is inert on a non-RLS table), and set `state.resolve_share_token` in `_apply_hosted_mode_wiring` next to `state.auth = MagicLinkAuth(...)` (`server.py:4241`). Import from `..db.share_tokens` following the existing hosted-import conventions - check `tests/test_local_mode_no_hosted_imports.py` to see whether db imports must stay lazy/guarded and match whatever `PostgresMatchStore` does.

- [ ] **Step 2: Verify nothing breaks**

```bash
uv run pytest tests/test_hosted_mode_boot.py tests/test_local_mode_no_hosted_imports.py -q
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add src/splitsmith/ui/server.py
git commit -m "feat(server): share-token store on TenantContext + anonymous resolver wiring"
```

---

### Task 4: Owner management routes

**Files:**
- Modify: `src/splitsmith/ui/server.py` (new routes inside `create_app`, near the other hosted-only routes - find where the auth routes are conditionally registered and follow that pattern)
- Test: `tests/test_share_routes.py` (new)

**Interfaces:**
- Consumes: `AppState.share_tokens`, `state.public_base_url`, `current_match_id` ContextVar.
- Produces routes (client-visible via the match alias as `/api/matches/{id}/match/shares...`):
  - `GET /api/match/shares` -> `{"shares": [{"id", "url", "created_at", "revoked_at"}]}`
  - `POST /api/match/shares` -> one share object, 201
  - `DELETE /api/match/shares/{share_id}` -> 204, or 404 when unknown/not owned
- `url = f"{state.public_base_url}/share/{token}"`. The raw token appears ONLY inside `url`.

Register the three routes only when `hosted` is true (same conditional the login routes use). Handlers read `mid = current_match_id.get()`; `None` -> raise `_no_project_error()` (bare-path call without the match prefix). `state.share_tokens` is non-None by construction under the auth gate in hosted mode; raise 500 if None (assertion-style guard).

Pydantic response models module-level like `AuthBeginRequest` (`server.py:644`): `ShareInfo(id: str, url: str, created_at: datetime, revoked_at: datetime | None)`, `ShareListResponse(shares: list[ShareInfo])`.

- [ ] **Step 1: Write failing tests** in `tests/test_share_routes.py`. Copy the `hosted_env` / `hosted_app` fixtures + `_CapturingSender` login dance from `tests/test_auth_routes.py` into a shared helper (either import from that module or lift into `tests/hosted_helpers.py` and refactor both - prefer the helper module). Add a `login(client, sender, email) -> None` helper (begin + callback) and a `seed_match(db_url, user_email, match_id)` helper that inserts the user's `MatchRow` directly with the engine (resolve `user_id` by selecting the `users` row by email after login; `storage_prefix=f"matches/{match_id}"`). Tests:

```python
# - management routes 401 anonymous (no cookie): GET/POST/DELETE
# - POST /api/matches/{mid}/match/shares -> 201, url startswith "http://localhost:5174/share/",
#   revoked_at is None
# - GET .../match/shares lists it (and lists revoked ones after DELETE, with revoked_at set)
# - DELETE .../match/shares/{id} -> 204; second DELETE -> 204 (idempotent revoke returns True)
# - DELETE with an unknown share_id -> 404
# - POST against a match_id the caller does not own -> 404 (alias middleware ownership gate;
#   just use a made-up match_id with no MatchRow)
# - user B (second login) cannot list or revoke user A's shares (seed both users)
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_share_routes.py -q
```

Expected: FAIL with 404s (routes don't exist yet).

- [ ] **Step 3: Implement the three handlers.** Sketch:

```python
if hosted:

    @app.get("/api/match/shares")
    async def list_match_shares() -> ShareListResponse:
        mid = current_match_id.get()
        if mid is None:
            raise _no_project_error()
        store = state.share_tokens
        if store is None:
            raise HTTPException(status_code=500, detail="share store unavailable")
        shares = await store.list_for_match(mid)
        return ShareListResponse(
            shares=[
                ShareInfo(
                    id=s.id,
                    url=f"{state.public_base_url}/share/{s.token}",
                    created_at=s.created_at,
                    revoked_at=s.revoked_at,
                )
                for s in shares
            ]
        )
```

POST mirrors it with `await store.create(mid)` and `status_code=201`. DELETE takes `share_id: str`, returns `Response(status_code=204)` on `True`, raises `HTTPException(404, "not found")` on `False`.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_share_routes.py tests/test_auth_routes.py -q
```

Expected: PASS (auth routes still green after any fixture refactor).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_share_routes.py tests/hosted_helpers.py tests/test_auth_routes.py
git commit -m "feat(api): share-link management routes (create/list/revoke)"
```

---

### Task 5: `_share_alias` middleware (anonymous read path)

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`_PUBLIC_API_PATHS` area ~658 for a module-level regex; `_auth_gate` ~4823; new middleware registered between `_match_id_alias` and the `hosted =` line ~4820)
- Test: extend `tests/test_share_routes.py`

**Interfaces:**
- Consumes: `state.resolve_share_token` (Task 3), `state.build_tenant` (`server.py:936`), `current_tenant` ContextVar, whitelist regex.
- Produces: anonymous GETs on
  - `/api/share/{token}/match/shooters`
  - `/api/share/{token}/shooters/{slug}/project`
  - `/api/share/{token}/shooters/{slug}/stages/{n}/coach`
  - `/api/share/{token}/shooters/{slug}/videos/stream?...`

- [ ] **Step 1: Write failing tests** (extend `tests/test_share_routes.py`; anonymous client = fresh `TestClient` or `client.cookies.clear()` after grabbing the share URL):

```python
# - GET /api/share/{garbage}/match/shooters -> 404 {"detail": "not found"}
# - revoked token -> 404 on every whitelisted path
# - expired token (set expires_at in the past directly via the engine) -> 404
# - valid token, non-whitelisted rest -> 404:
#     /api/share/{t}/match  (no such whitelisted rest)
#     /api/share/{t}/shooters/anna/videos  (prefix of a whitelisted path)
#     /api/share/{t}/me
#     /api/share/{t}/shooters/anna/project/extra
# - valid token, whitelisted path, non-GET (POST to .../match/shooters) -> 404
# - happy path: GET /api/share/{t}/match/shooters -> 200 with the seeded shooter roster
# - happy path: GET /api/share/{t}/shooters/{slug}/project -> 200
# - no session cookie was needed for any of the above (assert client has no cookies)
# - whitelist lock: parametrized assert that _SHARE_PATH_RE matches exactly the four shapes
#   and rejects a dozen adversarial variants ("shooters//project", "match/shooters/",
#   "shooters/a/stages/x/coach", "SHOOTERS/a/project", "shooters/a/stages/1/coach/distributions")
```

Happy-path seeding: after login + `seed_match`, write the match + project docs the handlers read. Use the real models so shapes stay honest - `Match` (`src/splitsmith/match_model.py:198`; set `match_id`, `name`, `shooters=["anna"]`, one `MatchStageDefinition`) and the per-shooter project doc exactly as `state.shooter_project` loads it (follow `server.py:1107-1130` -> `ProjectStateStore.load_project` in `src/splitsmith/db/project_state.py` to confirm the doc_kind and the model it validates against; build the doc with that model's constructor and `.model_dump(mode="json")`). Insert via `ProjectStateStore(session_factory, user_id=...)` save methods, not raw rows. Skip a stream_video happy-path test - it needs real media on disk; the coach/stream handlers ride the same alias + tenant path proven by the project test, and stream 404s on missing media are exercised implicitly by the whitelist tests.

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_share_routes.py -q
```

Expected: new tests FAIL (404 tests may accidentally pass - the interesting failures are the happy paths and the auth-gate 401 on /api/share/*).

- [ ] **Step 3: Implement.** Module level, next to `_PUBLIC_API_PATHS`:

```python
# GET-only path shapes reachable through /api/share/{token}/ - the entire
# anonymous surface. Anything else under the prefix is a uniform 404. The
# share middleware impersonates the owner's tenant for the request, so this
# whitelist is the containment boundary: extend it only with read-only,
# match-scoped routes, and never let the client supply the match id.
_SHARE_PATH_RE = re.compile(
    r"^(?:match/shooters"
    r"|shooters/[^/]+/project"
    r"|shooters/[^/]+/stages/\d+/coach"
    r"|shooters/[^/]+/videos/stream)$"
)
```

In `_auth_gate`, after the `_PUBLIC_API_PATHS` check add:

```python
        if path.startswith("/api/share/"):
            # Anonymous share reads: authorization is the token itself,
            # resolved by the _share_alias middleware inside this gate.
            return await call_next(request)
```

New middleware, registered AFTER `_match_id_alias`'s definition and BEFORE `hosted = _hosted_mode_active()` (so it runs between the gate and the alias), and only when hosted wiring is active - compute `hosted` earlier or guard inside the middleware with `state.resolve_share_token is None -> 404`:

```python
    @app.middleware("http")
    async def _share_alias(request, call_next):
        path = request.url.path
        prefix = "/api/share/"
        if not path.startswith(prefix):
            return await call_next(request)
        not_found = JSONResponse(status_code=404, content={"detail": "not found"})
        resolver = state.resolve_share_token
        if resolver is None:
            # Local mode: no share surface at all.
            return not_found
        token, sep, rest = path[len(prefix) :].partition("/")
        if not sep or not token or request.method != "GET" or not _SHARE_PATH_RE.fullmatch(rest):
            return not_found
        resolved = await resolver(token)
        if resolved is None:
            return not_found
        # Rewrite onto the match-alias prefix with the match id from the
        # token row - never from the URL - and pin the owner's tenant so
        # the downstream ownership check + stores resolve as the owner.
        rewritten = f"/api/matches/{resolved.match_id}/{rest}"
        request.scope["path"] = rewritten
        request.scope["raw_path"] = rewritten.encode("utf-8")
        tenant_token = current_tenant.set(state.build_tenant(resolved.owner_user_id))
        try:
            return await call_next(request)
        finally:
            current_tenant.reset(tenant_token)
```

Ordering check: `_share_alias` must be defined in code AFTER `_match_id_alias` (line ~4711) and BEFORE `_auth_gate` (line ~4822). Starlette makes later-registered middleware run outer, so execution is `_auth_gate` -> `_share_alias` -> `_match_id_alias`. Verify with a quick failing-order symptom: if `_share_alias` runs outside the gate the happy-path test still passes but `/api/share/...` with a garbage token would 401 instead of 404; the tests catch both orderings.

Local-mode test: `create_app()` without hosted env -> `client.get("/api/share/x/match/shooters")` -> 404 (add to `tests/test_share_routes.py` using a plain local app like `tests/test_ui_server.py` builds).

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_share_routes.py tests/test_auth_routes.py tests/test_ui_server.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_share_routes.py
git commit -m "feat(api): anonymous share read path via token-scoped alias middleware"
```

---

### Task 6: SPA plumbing - share-aware request scoping, hrefs, auth gate

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (~1554-1574)
- Modify: `src/splitsmith/ui_static/src/lib/matchHref.ts`
- Modify: `src/splitsmith/ui_static/src/lib/auth.tsx` (AuthGate)

**Interfaces:**
- Produces: `currentShareTokenFromLocation(): string | null` (exported from api.ts); share-mode `scopeRequestPath`; share-mode `useMatchHref`; AuthGate passthrough for `/share/`.

- [ ] **Step 1: api.ts.** Next to `currentMatchIdFromLocation` (`api.ts:1554`):

```typescript
/** Share token parsed from the current URL.
 *
 * Public share routes live under ``/share/:token/...``. When the viewer
 * is on one of those URLs, match-scoped API traffic routes through
 * ``/api/share/{token}/...`` - the anonymous, token-authorized read
 * path - instead of the session-authorized match prefix. */
export function currentShareTokenFromLocation(): string | null {
  if (typeof window === "undefined") return null;
  const m = window.location.pathname.match(/^\/share\/([^/]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}
```

Extend `scopeRequestPath` (share wins; a URL can't be both `/match/...` and `/share/...`):

```typescript
function scopeRequestPath(path: string): string {
  if (!MATCH_SCOPED_PREFIXES.some((p) => path.startsWith(p))) return path;
  const shareToken = currentShareTokenFromLocation();
  if (shareToken) {
    return `/api/share/${encodeURIComponent(shareToken)}${path.substring(4)}`;
  }
  const matchId = currentMatchIdFromLocation();
  if (!matchId) return path;
  return `/api/matches/${encodeURIComponent(matchId)}${path.substring(4)}`;
}
```

- [ ] **Step 2: matchHref.ts.** `useMatchHref` gains share awareness (Results/ResultsStage call `href("results", ...)`; in share mode those must land on `/share/{token}/results/...`):

```typescript
export function useMatchHref(): MatchHrefBuilder {
  const { matchId, token } = useParams<{ matchId?: string; token?: string }>();
  const { pathname } = useLocation();
  const shareToken = pathname.startsWith("/share/") ? token : undefined;
  return useCallback(
    (...segments: string[]) => {
      const tail = segments
        .filter((s) => s != null && s !== "")
        .map((s) => encodeURIComponent(s))
        .join("/");
      if (shareToken) {
        return `/share/${encodeURIComponent(shareToken)}/${tail}`;
      }
      if (matchId) {
        return `/match/${encodeURIComponent(matchId)}/${tail}`;
      }
      return `/${tail}`;
    },
    [matchId, shareToken],
  );
}
```

(Import `useLocation` from react-router-dom. The share route param MUST be named `:token` in Task 7 for this to resolve.)

- [ ] **Step 3: auth.tsx.** In `AuthGate`, before the loading branch, add a share bypass so anonymous share viewers never see the login redirect or the Standby spinner:

```typescript
  // Public share views are token-authorized server-side; the session
  // gate has no say there. Bypass before the loading branch so a share
  // link renders without waiting on /api/me.
  if (location.pathname.startsWith("/share/")) return <>{children}</>;
```

- [ ] **Step 4: Verify**

```bash
cd src/splitsmith/ui_static && pnpm typecheck && pnpm exec eslint src/lib/api.ts src/lib/matchHref.ts src/lib/auth.tsx
```

Expected: clean. (Check package.json for the exact typecheck script name; use `pnpm exec tsc --noEmit` if there is no script.)

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/api.ts src/splitsmith/ui_static/src/lib/matchHref.ts src/splitsmith/ui_static/src/lib/auth.tsx
git commit -m "feat(ui): share-aware API scoping, hrefs, and auth-gate bypass"
```

---

### Task 7: SPA share surface - ShareShell + routes

**Files:**
- Create: `src/splitsmith/ui_static/src/components/share/ShareShell.tsx`
- Modify: `src/splitsmith/ui_static/src/App.tsx`

**Interfaces:**
- Consumes: `MatchShellOutletContext` type (`MatchShell.tsx:52`), `api.listMatchShooters`, `api.getProject`, `pickDefaultShooterSlug` (`@/lib/defaultShooter`), `isUnauthorized`/`ApiError` from api.ts.
- Produces: routes `/share/:token/results` (Results), `/share/:token/results/:slug/:stage` (ResultsStage), `/share/:token` (index redirect to `results`); a dead-link full-page state.

- [ ] **Step 1: ShareShell.** A lean shell (no sidebar, no jobs rail, no localStorage). Structure:

```tsx
/**
 * ShareShell - the public, token-authorized wrapper around the read-only
 * Results surface (#349). Mounts under /share/:token and provides the same
 * outlet context MatchShell gives Results/ResultsStage, fetched through the
 * anonymous /api/share/{token}/ path (see scopeRequestPath). No auth, no
 * mutations, no persistence - if a fetch 404s the link is gone (revoked,
 * expired, or never existed; the server keeps those indistinguishable).
 */
import { useCallback, useEffect, useState } from "react";
import { Outlet } from "react-router-dom";

import {
  api,
  type MatchProject,
  type ShooterListEntry,
} from "@/lib/api";
import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { pickDefaultShooterSlug } from "@/lib/defaultShooter";

export function ShareShell() {
  const [shooters, setShooters] = useState<ShooterListEntry[]>([]);
  const [project, setProject] = useState<MatchProject | null>(null);
  const [dead, setDead] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  useEffect(() => {
    let alive = true;
    api
      .listMatchShooters()
      .then((r) => {
        if (!alive) return;
        setShooters(r.shooters);
        const slug = pickDefaultShooterSlug(r.shooters);
        if (slug) {
          api
            .getProject(slug)
            .then((p) => {
              if (alive) setProject(p);
            })
            .catch(() => {
              if (alive) setProject(null);
            });
        }
      })
      .catch(() => {
        if (alive) setDead(true);
      });
    return () => {
      alive = false;
    };
  }, [refreshKey]);

  if (dead) return <ShareUnavailable />;

  const context: MatchShellOutletContext = {
    project,
    health: null,
    shooters,
    refresh,
  };
  return (
    <div className="min-h-dvh bg-bg">
      <Outlet context={context} />
    </div>
  );
}
```

`ShareUnavailable` in the same file: a full-page state matching the instrument-panel aesthetic (bg-bg, mono uppercase kicker) saying "This link is no longer available" with a sub-line "Ask whoever shared it for a fresh link." No login CTA. Before writing markup, look at how `DesktopGate` / the AuthGate "Standby..." screen are styled and reuse those exact utility-class patterns; check with the frontend-design skill conventions already embedded in the codebase rather than inventing new tokens.

Verify against `pages/Results.tsx` + `pages/ResultsStage.tsx` while implementing: they must render with `health: null` and without MatchShell chrome (they use only `project`, `shooters` per the recon - if a runtime null-deref appears, fix by feeding the minimal value, never by editing the Results pages' data contract). ResultsStage also needs the stage list header MatchShell normally provides? It does not - it renders its own header from the coach payload.

- [ ] **Step 2: Routes in App.tsx**, sibling of the `match/:matchId` subtree (inside `AuthGate`, which now bypasses `/share/`):

```tsx
          {/* Public share surface (#349): token-authorized, read-only,
              mobile-friendly. Mirrors the match results subtree shape so
              useMatchHref("results", ...) round-trips inside the share. */}
          <Route path="share/:token" element={<ShareShell />}>
            <Route index element={<Navigate to="results" replace />} />
            <Route path="results" element={<Results />} />
            <Route path="results/:slug/:stage" element={<ResultsStage />} />
          </Route>
```

No `ShooterScopedRoute` wrapper (check what it does first - if it only guards slug presence/redirect against the match subtree, the plain route is correct here; if it provides context ResultsStage needs, include it).

- [ ] **Step 3: Verify + smoke**

```bash
cd src/splitsmith/ui_static && pnpm typecheck && pnpm build && pnpm exec eslint src/components/share/ShareShell.tsx src/App.tsx
```

Expected: clean build.

- [ ] **Step 4: Commit**

```bash
git add src/splitsmith/ui_static/src/components/share/ShareShell.tsx src/splitsmith/ui_static/src/App.tsx
git commit -m "feat(ui): public /share/:token results surface"
```

---

### Task 8: Share management dialog on Results

**Files:**
- Create: `src/splitsmith/ui_static/src/components/results/ShareDialog.tsx`
- Modify: `src/splitsmith/ui_static/src/pages/Results.tsx` (add the Share button + dialog mount; owner view only)
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (three methods + types)

**Interfaces:**
- Consumes: management routes from Task 4; `useDeploymentMode()` (`@/lib/features`); overlay conventions - z tokens, body Portal, `useDialogFocus` (see an existing dialog, e.g. `ConfirmDialog.tsx`, and `docs`/memory convention: never inline fixed overlays).
- Produces api.ts additions:

```typescript
export interface ShareInfo {
  id: string;
  url: string;
  created_at: string;
  revoked_at: string | null;
}
export interface ShareListResponse {
  shares: ShareInfo[];
}
// inside api:
  listShares: () => request<ShareListResponse>("/api/match/shares"),
  createShare: () =>
    request<ShareInfo>("/api/match/shares", { method: "POST" }),
  revokeShare: (shareId: string) =>
    request<void>(`/api/match/shares/${encodeURIComponent(shareId)}`, {
      method: "DELETE",
    }),
```

(These ride `scopeRequestPath` automatically -> `/api/matches/{id}/match/shares`. IMPORTANT: on a `/share/:token` URL they would rewrite onto the share prefix and 404 - the dialog only mounts on the owner's match route, which guarantees the match rewrite. Note this in a comment on the api methods.)

- [ ] **Step 1: ShareDialog.** Follow the structure of an existing portal dialog (`ConfirmDialog.tsx` or the newest dialog in the codebase - copy its Portal + `useDialogFocus` + z-token skeleton exactly). Content: title "Share results"; explanatory line ("Anyone with a link sees the read-only results - splits, stats, and video. Revoke a link to cut off access."); "Create link" button -> `api.createShare()` then refresh list; list of shares newest first - live ones show the URL in a mono input with a Copy button (`navigator.clipboard.writeText`, flip the button label to "Copied" for 2s) and a Revoke button (wrap in the existing confirm pattern if `ConfirmProvider` is cheap to use, otherwise a two-click "Revoke?" arm state); revoked ones stay listed, muted (`text-subtle`), labeled with their `revoked_at` date, no copy button. Loading + error states per house style. Accessibility: WCAG 2.2 AA - the copy feedback must not be color-only (text change covers it), focus stays trapped in the dialog, Escape closes (via `useDialogFocus` stack).

- [ ] **Step 2: Results.tsx.** Add a "Share" button in the page header area (match the existing button styles on the page). Mount condition:

```tsx
  const deploymentMode = useDeploymentMode();
  const shareToken = useParams<{ token?: string }>().token; // undefined outside /share
  const canShare = deploymentMode === "hosted" && !shareToken;
```

Render the button + dialog only when `canShare`. (On `/share/:token` the same Results component renders for anonymous viewers - the button must not exist there. `useDeploymentMode` returns "local" while loading, so the button pops in after the features fetch; that matches how other hosted-only chrome behaves - verify with one existing usage.)

- [ ] **Step 3: Verify**

```bash
cd src/splitsmith/ui_static && pnpm typecheck && pnpm build && pnpm exec eslint src/components/results/ShareDialog.tsx src/pages/Results.tsx src/lib/api.ts
```

Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add src/splitsmith/ui_static/src/components/results/ShareDialog.tsx src/splitsmith/ui_static/src/pages/Results.tsx src/splitsmith/ui_static/src/lib/api.ts
git commit -m "feat(ui): share-link management dialog on Results"
```

---

### Task 9: Gates, smoke, PR

**Files:** none new (fixes only if gates fail).

- [ ] **Step 1: Python gates**

```bash
uv run ruff check . && uv run black --check . && uv run pytest -q
```

Expected: all green. Fix anything red (no "pre-existing" excuses - all debt here is ours).

- [ ] **Step 2: SPA gates**

```bash
cd src/splitsmith/ui_static && pnpm typecheck && pnpm build
```

- [ ] **Step 3: Docker smoke** (migration touches the DB; CI skips this):

```bash
export PATH="$HOME/.claude-tmp/bin:$PATH"   # docker symlink workaround
uv run pytest -m docker -q
```

Expected: PASS (proves the Alembic migration applies against live Postgres).

- [ ] **Step 4: New-copy dash sweep**

```bash
git diff origin/main | grep '^+' | grep -nE '(--|—)' | grep -v '^+++' | grep -v 'CLAUDE\|frontmatter'
```

Review hits: allowed only inside shell flags/CLI examples; fix prose.

- [ ] **Step 5: Push + PR**

```bash
git push -u origin feat/public-share-links
gh pr create --title "feat: public share links for match results (MVP)" --body "..."
```

PR body: summary (owner mints revocable unguessable links; anonymous read-only Results with video), design pointer to the spec file, security notes (whitelist containment, uniform 404, token-row match pinning, raw-token rationale), test coverage list, refs `#349` + `#449`, and the standard generated-with footer.
