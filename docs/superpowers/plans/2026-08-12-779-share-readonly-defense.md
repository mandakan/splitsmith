# PR A: Scope-Keyed Share Read-Only Defense (#779) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a write during a read-scoped share request fail loudly at the database (SET TRANSACTION READ ONLY), with a store-level guard and a byte-identity test net as complements - keyed off a new `share_tokens.scope` column so write-scoped coach tokens later just add a scope mapping.

**Architecture:** A new `scope` column (`'read'` default) flows from `resolve_share_token` into a db-layer ContextVar set by `_share_alias`. The existing `after_begin` listener in `engine.py` (where the tenant GUC is set, per-transaction, NullPool-safe) issues `SET TRANSACTION READ ONLY` when the scope grants no writes. `ProjectStateStore` and `PostgresMatchStore` refuse mutations at their choke points under the same condition. Spec: `docs/superpowers/specs/2026-08-12-share-write-foundation-design.md`.

**Tech Stack:** FastAPI middleware, SQLAlchemy async + Alembic, pytest (sqlite unit tests + `-m docker` Postgres tests).

## Global Constraints

- New text (comments, docstrings, copy) uses ASCII punctuation only and single `-` dashes - never em dashes, never `--`.
- All shell commands run through `uv run` (non-interactive shell has no venv on PATH).
- No new dependencies.
- Gates before PR: `uv run ruff check .`, `uv run black --check .`, scoped pytest per task, full `uv run pytest` plus `uv run pytest -m docker -n0` at the end (DB change).
- ~21 env-dependent local pytest failures are known-green in CI; compare any failure against main before treating it as caused by this work.

---

### Task 0: Branch

- [ ] From `feat/share-write-foundation` (the branch carrying the spec + plans), create this PR's own branch so PR B can stack on it later without the two PRs sharing a head:

```bash
git switch -c feat/779-share-readonly-defense
```

---

### Task 1: `share_tokens.scope` column + resolver plumbing

**Files:**
- Modify: `src/splitsmith/db/models.py` (ShareTokenRow, ~line 516-556)
- Modify: `src/splitsmith/db/share_tokens.py` (ResolvedShare, resolve_share_token)
- Create: `alembic/versions/a1c9e3b7d5f0_add_scope_to_share_tokens.py`
- Test: `tests/test_share_tokens_store.py`

**Interfaces:**
- Produces: `ShareTokenRow.scope: Mapped[str]` (default `"read"`); `ResolvedShare` gains `scope: str`; `resolve_share_token` returns it. Task 2 consumes `resolved.scope`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_share_tokens_store.py`, following the file's existing fixture pattern for constructing the store and resolver (copy the session-factory setup from the test directly above the insertion point):

```python
def test_created_token_resolves_with_read_scope(store_env) -> None:
    """#779: every token minted by the MVP UI is read-scoped; the resolver
    must surface the scope so the share middleware can key enforcement
    off it."""
    # Use the same store construction + asyncio.run driving idiom as the
    # neighboring tests in this file.
    async def _run(sf, user_id):
        store = ShareTokenStore(sf, user_id=user_id)
        created = await store.create("match-1")
        resolved = await resolve_share_token(sf, created.token)
        assert resolved is not None
        assert resolved.scope == "read"

    _drive(_run)  # adapt to this file's actual fixture/driver helper
```

Note for the implementer: this file already has tests that create tokens and resolve them - mirror their exact setup (fixture names, how `sf`/`user_id` are obtained). The assertion `resolved.scope == "read"` is the new content.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_share_tokens_store.py -k read_scope -v`
Expected: FAIL with `AttributeError: 'ResolvedShare' object has no attribute 'scope'`

- [ ] **Step 3: Add the column, dataclass field, and resolver field**

In `src/splitsmith/db/models.py`, inside `ShareTokenRow` after the `token` column:

```python
    # #779: named scope keying what a share request may do. "read" (the
    # only value shipped today) maps to zero write capabilities - the
    # share middleware and engine enforce a READ ONLY transaction for it.
    # A later write-capable scope (e.g. "coach") is one new mapping, not
    # a schema change.
    scope: Mapped[str] = mapped_column(String, nullable=False, server_default="read")
```

In `src/splitsmith/db/share_tokens.py`, extend `ResolvedShare` and the resolver return:

```python
@dataclass(frozen=True)
class ResolvedShare:
    owner_user_id: str
    match_id: str
    scope: str
```

```python
    return ResolvedShare(owner_user_id=row.user_id, match_id=row.match_id, scope=row.scope)
```

Create `alembic/versions/a1c9e3b7d5f0_add_scope_to_share_tokens.py` (head is `f3a9c7e5d1b2`; confirm with `uv run alembic heads` and adjust `down_revision` if a migration landed since):

```python
"""add scope to share_tokens

#779: share tokens gain a named scope keying what a request they
authorize may do. 'read' is the only value shipped; the server maps
scope -> capability set and enforces READ ONLY transactions for
scopes without writes. server_default backfills every existing token
as read-scoped, which is exactly what they all are today.
share_tokens is not under RLS (models.py docstring), so no RLS DDL.

Revision ID: a1c9e3b7d5f0
Revises: f3a9c7e5d1b2
Create Date: 2026-08-12 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c9e3b7d5f0"
down_revision: str | Sequence[str] | None = "f3a9c7e5d1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "share_tokens",
        sa.Column("scope", sa.String(), nullable=False, server_default="read"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("share_tokens", "scope")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_share_tokens_store.py -v`
Expected: all PASS (new test plus the file's existing tests - the `server_default` keeps old construction paths working).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/db/models.py src/splitsmith/db/share_tokens.py alembic/versions/a1c9e3b7d5f0_add_scope_to_share_tokens.py tests/test_share_tokens_store.py
git commit -m "feat: add scope column to share_tokens, resolver surfaces it (#779)"
```

---

### Task 2: `share_guard` module + middleware scope wiring

**Files:**
- Create: `src/splitsmith/db/share_guard.py`
- Modify: `src/splitsmith/ui/server.py` (`_share_alias`, ~line 6580-6627)
- Test: `tests/test_share_routes.py`

**Interfaces:**
- Consumes: `ResolvedShare.scope` (Task 1).
- Produces: `current_share_scope: ContextVar[str | None]`, `share_request_is_read_only() -> bool`, `class ShareReadOnlyError(RuntimeError)` - all in `splitsmith.db.share_guard`. Tasks 3 and 4 consume these.

- [ ] **Step 1: Create the guard module**

Create `src/splitsmith/db/share_guard.py`:

```python
"""Share-request scope context + read-only enforcement helpers (#779).

The share alias middleware records the resolved token's scope here; the
engine's after_begin listener and the state stores consult it. Lives in
the db layer so the engine and stores can import it without reaching
into the UI server module.
"""

from __future__ import annotations

from contextvars import ContextVar

# Scope of the share token authorizing the current request, or None
# outside a share request. "read" is the only scope shipped today; a
# write-capable scope added later (e.g. "coach") simply stops matching
# the read-only check below and the capability table decides what it
# may write.
current_share_scope: ContextVar[str | None] = ContextVar(
    "splitsmith_current_share_scope", default=None
)


class ShareReadOnlyError(RuntimeError):
    """A mutation was attempted while serving a read-scoped share request.

    Always a bug: a share-whitelisted route grew a write side effect.
    Surfaces as a 500 by design - loud beats silent anonymous writes.
    """


def share_request_is_read_only() -> bool:
    """True when the current request is a share request whose scope
    grants no writes.

    Keyed off the scope rather than mere share-ness so that write-scoped
    tokens later skip both the READ ONLY transaction and the store
    guard without touching this module's callers.
    """
    return current_share_scope.get() == "read"
```

- [ ] **Step 2: Write the failing wiring test**

Append to `tests/test_share_routes.py`:

```python
def test_share_request_carries_read_scope(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#779: _share_alias must pin the resolved token's scope for the
    duration of the request, and reset it afterwards. Probed from inside
    the request via the store loader the project route calls."""
    from splitsmith.db.project_state import ProjectStateStore
    from splitsmith.db.share_guard import current_share_scope

    token = _setup_shared_match(hosted_env, hosted_app)
    client, sender = hosted_app

    seen: list[str | None] = []
    orig = ProjectStateStore.load_project

    async def probe(self, match_id: str, slug: str):
        seen.append(current_share_scope.get())
        return await orig(self, match_id, slug)

    monkeypatch.setattr(ProjectStateStore, "load_project", probe)

    resp = client.get(_share_url(token, f"shooters/{SLUG}/project"))
    assert resp.status_code == 200, resp.text
    assert seen == ["read"]

    # Owner path: same route, no share scope.
    seen.clear()
    login(client, sender, "owner@example.com")
    owner = client.get(f"/api/matches/{MID}/shooters/{SLUG}/project")
    assert owner.status_code == 200, owner.text
    assert seen == [None]
    client.cookies.clear()
```

Note: if the share project route turns out not to call `ProjectStateStore.load_project` (the probe list stays empty), move the probe to the loader it does call - the point is observing the ContextVar from inside a share-served handler, not the specific seam.

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_share_routes.py -k carries_read_scope -v`
Expected: FAIL at `assert seen == ["read"]` (ContextVar never set, so `seen == [None]`).

- [ ] **Step 4: Wire the middleware**

In `src/splitsmith/ui/server.py`, import at the top of the file with the other db imports:

```python
from splitsmith.db.share_guard import current_share_scope
```

In `_share_alias`, set the scope alongside the existing tenant/share tokens and reset it in `finally` (LIFO order preserved):

```python
        tenant_token = current_tenant.set(state.build_tenant(resolved.owner_user_id))
        share_token = current_share_request.set(True)
        scope_token = current_share_scope.set(resolved.scope)
```

```python
        finally:
            current_share_scope.reset(scope_token)
            current_share_request.reset(share_token)
            current_tenant.reset(tenant_token)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_share_routes.py -v`
Expected: all PASS (new wiring test plus every existing share test unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/db/share_guard.py src/splitsmith/ui/server.py tests/test_share_routes.py
git commit -m "feat: share middleware pins token scope in a db-layer ContextVar (#779)"
```

---

### Task 3: READ ONLY transaction for read-scoped share requests

**Files:**
- Modify: `src/splitsmith/db/engine.py` (`_tenant_guc_after_begin`, ~line 62-95)
- Test: `tests/test_share_readonly_docker.py` (created here, executed in Task 6's docker gate)

**Interfaces:**
- Consumes: `share_request_is_read_only()` (Task 2); `hosted_stack` fixture + `HOST_APP_DB_URL` pattern from `tests/test_sync_docker.py`.
- Produces: every tenant-factory transaction begun while `current_share_scope == "read"` is `READ ONLY` on Postgres.

- [ ] **Step 1: Extend the after_begin listener**

In `src/splitsmith/db/engine.py`, add the import:

```python
from .share_guard import share_request_is_read_only
```

and extend the inner `_after_begin` in `_tenant_guc_after_begin`:

```python
    def _after_begin(
        session: Session,
        transaction: SessionTransaction,
        connection: Connection,
    ) -> None:
        if connection.dialect.name != "postgresql":
            return
        # #779: a read-scoped share request must not write anything, no
        # matter which code path tries - including code that never heard
        # of the share ContextVars. SET TRANSACTION must precede the
        # transaction's first query, so it goes before the GUC SELECT.
        # Same per-transaction reasoning as the GUC itself: NullPool
        # hands each transaction a fresh connection.
        if share_request_is_read_only():
            connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(
            text("SELECT set_config('app.user_id', :uid, true)"),
            {"uid": user_id},
        )

    return _after_begin
```

Also extend the `_tenant_guc_after_begin` docstring with one paragraph:

```
    #779 addition: when the current request is a read-scoped share
    request (see splitsmith.db.share_guard), the listener also issues
    SET TRANSACTION READ ONLY so any accidental write fails at Postgres
    with SQLSTATE 25006 instead of succeeding as the impersonated owner.
    Only tenant-factory sessions get the listener; raw-factory sessions
    (auth, share-token resolution) carry no tenant GUC, so RLS already
    fails their owner-state writes closed.
```

- [ ] **Step 2: Run the existing unit suites to confirm no regression**

Run: `uv run pytest tests/test_share_routes.py tests/test_share_tokens_store.py -v`
Expected: all PASS (sqlite short-circuits before the new branch; hosted-app share reads on Postgres are SELECT-only and unaffected).

- [ ] **Step 3: Write the docker enforcement test**

Create `tests/test_share_readonly_docker.py`:

```python
"""#779: prove the READ ONLY share transaction at a real Postgres.

Runs under `pytest -m docker` against the compose stack, connecting as
the same non-superuser role the API runs under. Unit suites cover the
wiring on sqlite (where the listener is a no-op by design); this file
is the proof the database actually refuses the write.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from splitsmith.db.engine import create_engine, sessionmaker, tenant_session_factory
from splitsmith.db.share_guard import current_share_scope

from .test_sync_docker import HOST_APP_DB_URL

# hosted_stack is provided via conftest.py's re-export (same idiom as
# the other docker suites).

pytestmark = pytest.mark.docker


def _tenant_factory():
    engine = create_engine(HOST_APP_DB_URL, pool_disabled=True)
    base = sessionmaker(engine)
    return tenant_session_factory(base, "u-779-readonly-test")


async def _run_statement(sql: str) -> None:
    factory = _tenant_factory()
    async with factory() as session:
        await session.execute(text(sql))
        await session.commit()


def test_read_scoped_share_transaction_rejects_writes(hosted_stack: None) -> None:
    """A data-modification statement inside a read-scoped share request
    fails with SQLSTATE 25006 (read_only_sql_transaction), even one that
    would touch zero rows - enforcement is per-transaction, not
    per-row."""
    token = current_share_scope.set("read")
    try:
        with pytest.raises(DBAPIError) as excinfo:
            asyncio.run(_run_statement("DELETE FROM matches WHERE false"))
    finally:
        current_share_scope.reset(token)
    assert "read-only" in str(excinfo.value).lower()


def test_read_scoped_share_transaction_still_reads(hosted_stack: None) -> None:
    """The same read-scoped transaction serves SELECTs - the share
    surface itself must keep working, and set_config is legal inside a
    READ ONLY transaction."""
    token = current_share_scope.set("read")
    try:
        asyncio.run(_run_statement("SELECT count(*) FROM matches"))
    finally:
        current_share_scope.reset(token)


def test_unscoped_transaction_still_writes(hosted_stack: None) -> None:
    """Control: without a share scope the identical statement succeeds -
    the defense keys off the scope, not off using the tenant factory."""
    asyncio.run(_run_statement("DELETE FROM matches WHERE false"))
```

- [ ] **Step 4: Verify the docker file at least collects**

Run: `uv run pytest tests/test_share_readonly_docker.py --collect-only -q`
Expected: 3 tests collected (execution happens in Task 6's docker gate; the compose build is too slow for per-task cycles).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/db/engine.py tests/test_share_readonly_docker.py
git commit -m "feat: read-scoped share requests run READ ONLY transactions (#779)"
```

---

### Task 4: Store-level mutation guard

**Files:**
- Modify: `src/splitsmith/db/project_state.py` (`ProjectStateStore`)
- Modify: `src/splitsmith/db/matches.py` (`PostgresMatchStore`)
- Test: `tests/test_project_state_store.py`, `tests/test_matches_store.py`

**Interfaces:**
- Consumes: `share_request_is_read_only()`, `ShareReadOnlyError` (Task 2).
- Produces: every public mutation entry point of both stores raises `ShareReadOnlyError` under a read-scoped share request.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_project_state_store.py` (mirror the file's existing store-construction fixture; the neighboring isolation tests show the exact `sf`/`user_id` setup and asyncio driving idiom to copy):

```python
def test_mutations_refused_during_read_scoped_share_request(store_env) -> None:
    """#779: the store is the choke point every state_docs write flows
    through - under a read-scoped share request each mutation entry
    point must raise instead of writing as the impersonated owner."""
    from splitsmith.db.share_guard import ShareReadOnlyError, current_share_scope

    async def _run(sf, user_id):
        store = ProjectStateStore(sf, user_id=user_id)
        # Seed one doc outside the share scope so delete paths have a target.
        await store.save_match("m1", {"name": "seed"}, expected_version=0)
        token = current_share_scope.set("read")
        try:
            with pytest.raises(ShareReadOnlyError):
                await store.save_match("m1", {"name": "x"}, expected_version=1)
            with pytest.raises(ShareReadOnlyError):
                await store.save_project("m1", "anna", {"slug": "anna"}, expected_version=0)
            with pytest.raises(ShareReadOnlyError):
                await store.save_audit("m1", "anna", 1, {"stage_number": 1}, expected_version=0)
            with pytest.raises(ShareReadOnlyError):
                await store.delete_shooter("m1", "anna")
            with pytest.raises(ShareReadOnlyError):
                await store.delete_audit("m1", "anna", 1)
            with pytest.raises(ShareReadOnlyError):
                await store.delete_match("m1")
            # Reads stay open - the share surface depends on them.
            doc, version = await store.load_match("m1")
            assert version == 1 and doc == {"name": "seed"}
        finally:
            current_share_scope.reset(token)
        # Outside the scope the store mutates normally again.
        assert await store.delete_match("m1") == 1

    _drive(_run)  # adapt to this file's actual driver helper
```

Append the same-shaped test to `tests/test_matches_store.py` for `PostgresMatchStore`: copy an existing `upsert` call from that file verbatim, wrap it in `current_share_scope.set("read")` / `reset`, and assert `ShareReadOnlyError`; repeat for each public mutation method the file exercises (the store's test file enumerates them - one refusal assertion per mutation method, plus one read that succeeds under the scope).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_project_state_store.py tests/test_matches_store.py -k share -v`
Expected: FAIL - `DID NOT RAISE ShareReadOnlyError` (mutations currently succeed).

- [ ] **Step 3: Add the guard to both stores**

In `src/splitsmith/db/project_state.py`, module level:

```python
from .share_guard import ShareReadOnlyError, share_request_is_read_only


def _refuse_share_write() -> None:
    """#779 store-level complement to the READ ONLY share transaction:
    refuse mutations at the choke point when serving a read-scoped share
    request. Postgres enforces the same rule at the database; this check
    also covers the sqlite test engine and fails with a clearer error."""
    if share_request_is_read_only():
        raise ShareReadOnlyError(
            "refusing to mutate owner state during a read-scoped share request"
        )
```

Add `_refuse_share_write()` as the first statement of the private `_save` (all `save_*` wrappers route through it) and of `delete_shooter`, `delete_audit`, `delete_match`.

In `src/splitsmith/db/matches.py`, import the same two names, add an identical module-level `_refuse_share_write()` helper (or import it from `project_state` if ruff is happy with the dependency direction; prefer the local copy - two one-liners beat a cross-store import), and add the call as the first statement of every public method that executes an INSERT/UPDATE/DELETE (`upsert` at minimum; the file's own test enumerates the rest).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_project_state_store.py tests/test_matches_store.py -v`
Expected: all PASS - new refusal tests green, every existing test untouched (the ContextVar defaults to None outside share requests).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/db/project_state.py src/splitsmith/db/matches.py tests/test_project_state_store.py tests/test_matches_store.py
git commit -m "feat: state stores refuse mutations under read-scoped share requests (#779)"
```

---

### Task 5: Byte-identity test net over the share whitelist

**Files:**
- Test: `tests/test_share_routes.py`

**Interfaces:**
- Consumes: `_setup_shared_match`, `_seed_stage_audit`, `_share_url`, `MID`, `SLUG` (all existing in the file).
- Produces: a parametrized regression net future share-whitelist additions must pass.

- [ ] **Step 1: Write the parametrized net**

Append to `tests/test_share_routes.py`:

```python
def _dump_state_docs(db_url: str, user_email: str) -> list[tuple]:
    """Serialize every state_docs row for the user as sorted tuples of
    all mapped columns - byte-identity means nothing changed, version
    and timestamps included."""
    import json

    from sqlalchemy import inspect as sa_inspect

    from splitsmith.db.models import StateDocRow

    engine = create_engine(db_url)
    sf = sessionmaker(engine)
    cols = sorted(c.key for c in sa_inspect(StateDocRow).mapper.column_attrs)

    async def _dump() -> list[tuple]:
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == user_email))).scalar_one()
            rows = (
                (await s.execute(_select(StateDocRow).where(StateDocRow.user_id == row.id)))
                .scalars()
                .all()
            )
        return sorted(
            tuple(json.dumps(getattr(r, k), default=str, sort_keys=True) for k in cols)
            for r in rows
        )

    return asyncio.run(_dump())


# One concrete instantiation per _SHARE_PATH_RE alternative (server.py).
# When the whitelist grows an entry, this list must grow one too - the
# assertion below is the promise every share route is write-free.
_SHARE_WHITELIST_INSTANCES = [
    "match/shooters",
    f"shooters/{SLUG}/project",
    f"shooters/{SLUG}/stages/1/coach",
    f"shooters/{SLUG}/coach/distributions",
    f"shooters/{SLUG}/videos/stream",
    "match/stage/1/compare",
    f"match/shooters/{SLUG}/videos/stream",
    "og.png",
    f"og/{SLUG}/1.png",
    "og-meta",
    f"og-meta/{SLUG}/1",
]


@pytest.mark.parametrize("rest", _SHARE_WHITELIST_INSTANCES)
def test_share_whitelist_routes_leave_state_docs_untouched(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    rest: str,
) -> None:
    """#779 test net: walk every whitelisted share shape against a
    seeded match carrying a legacy (unclassified) audit doc - the shape
    known to tempt read paths into healing writes (#775) - and assert
    the owner's state_docs rows are byte-identical before and after."""
    token = _setup_shared_match(hosted_env, hosted_app)
    legacy_doc = {
        "stage_number": 1,
        "shots": [
            {"shot_number": 1, "ms_after_beep": 1500},
            {"shot_number": 2, "ms_after_beep": 1800},
        ],
    }
    _seed_stage_audit(hosted_env, "owner@example.com", MID, SLUG, legacy_doc)

    client, _ = hosted_app
    before = _dump_state_docs(hosted_env, "owner@example.com")
    resp = client.get(_share_url(token, rest))
    # Routes without seeded media legitimately 404/422; the invariant
    # under test is the absence of writes, not the status code.
    assert resp.status_code < 500, f"{rest}: {resp.status_code} {resp.text[:200]}"
    after = _dump_state_docs(hosted_env, "owner@example.com")
    assert after == before, f"share GET {rest!r} mutated state_docs"
```

Adjust the imports at the top of the test file if `create_engine` / `sessionmaker` / `_select` / `User` are not already imported there (the `_seed_stage_audit` helper shows the file's import aliases).

- [ ] **Step 2: Run the net**

Run: `uv run pytest tests/test_share_routes.py -k untouched -v`
Expected: 11 PASS. If any instantiation fails, that is a real #779 finding - fix the route, not the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_share_routes.py
git commit -m "test: byte-identity net over every share-whitelisted route (#779)"
```

---

### Task 6: Gates, docker suite, PR

**Files:**
- No source changes (gate task).

- [ ] **Step 1: Lint + format + full unit suite**

Run:
```bash
uv run ruff check .
uv run black --check .
uv run pytest
```
Expected: ruff/black clean; pytest green modulo the ~21 known env-dependent failures (verify any failure exists on main before attributing it).

- [ ] **Step 2: Docker suite (DB change gate)**

Run: `uv run pytest -m docker -n0`
Expected: PASS including the three new `test_share_readonly_docker.py` tests. Ensure `~/.claude-tmp/bin` is on PATH first (docker is not on the non-interactive PATH; the symlink workaround is documented in memory).

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin feat/779-share-readonly-defense
gh pr create --title "feat: scope-keyed read-only defense for share requests (#779)" --body "$(cat <<'EOF'
Closes #779.

Share requests impersonate the token owner's tenant, so RLS never
rejects a write issued while serving one - the GET-only whitelist was
the single load-bearing defense. This PR adds the layers the issue
called for, keyed off a new share_tokens.scope column ('read' default)
so write-scoped coach tokens later add a scope mapping instead of
unwinding a blanket rule:

- share_tokens.scope + migration; resolver surfaces it; _share_alias
  pins it in a db-layer ContextVar
- read-scoped share transactions run SET TRANSACTION READ ONLY via the
  existing after_begin listener (per-transaction, NullPool-safe);
  accidental writes fail loudly with SQLSTATE 25006
- ProjectStateStore + PostgresMatchStore refuse mutations at their
  choke points under the same condition
- parametrized byte-identity net over all 11 share-whitelisted routes;
  docker-marked tests prove enforcement at a real Postgres

Design: docs/superpowers/specs/2026-08-12-share-write-foundation-design.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_013p2JUqQX6BRGjUfqFoPVYi
EOF
)"
```

Expected: PR opens against main. Do not merge in the same command as any check - merge only after CI is green and review is done.
