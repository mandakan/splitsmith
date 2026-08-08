# Browser-assisted desktop auth (#719) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the paste-once desktop sync token with a browser-assisted
device-code flow, scope the resulting credential to the sync surface only, and
show the linked hosted account as a signed-in identity in the local UI.

**Architecture:** A new hosted `device_authorizations` table drives an OAuth
device-flow: the desktop install asks for a code, the operator approves it in a
browser on whatever machine they have to hand, and the first successful poll
mints a `desktop_tokens` row with `scope='sync'`. A single gate in `_auth_gate`
turns that scope into a hard 403 on everything outside `/api/sync/*`. The local
side stores the returned token plus a small account record in `config.yaml` and
surfaces it as `HostedAccountChip` in `GlobalBar`.

**Tech Stack:** FastAPI, SQLAlchemy async + alembic, Pydantic, httpx,
pytest (+ `httpx.MockTransport`), React 19 + react-router + vitest, Tailwind.

**Source of truth:** `docs/superpowers/specs/2026-08-07-desktop-device-auth-design.md`.
Read `docs/superpowers/plans/2026-08-08-desktop-device-auth-kickoff.md` before
starting -- it records what moved under the spec.

## Global Constraints

- Python 3.11+, type hints everywhere. `pathlib.Path` for paths, f-strings,
  Black at line length 110, Ruff clean.
- `uv` for dependency management. **No new dependencies** -- everything here
  uses `secrets`, `httpx`, SQLAlchemy, and Pydantic, all already present.
- Pydantic models for anything crossing a module boundary. No bare dicts.
- ASCII punctuation only in code comments, docstrings and UI copy: `--` not an
  em dash, `...` not an ellipsis character, straight quotes.
- `pnpm` is not on PATH. Every frontend command is `corepack pnpm ...`. A
  "command not found" reads as success if you only grep for failures.
- `src/lib/features.ts` caches `getServerFeatures()` in a module-level promise
  with no invalidation. The first deployment mode resolved in a test *file*
  wins for that whole file. Need both modes? Use two files.
- New hosted modules must stay out of the local-slim import chain --
  `tests/test_local_mode_no_hosted_imports.py` fails if `splitsmith.db` or
  `sqlalchemy` reach module scope of a local-mode entrypoint. Import hosted
  things lazily inside functions, exactly as `ui/sync_api.py` does.
- Test suite runs under xdist (`-n auto`). Use `-n0` for a focused run. New
  tests must not share mutable state outside `tmp_path`.
- **The mutation drill is mandatory for the scope gate (Task 3).** Delete the
  gate, run the tests, confirm they go red, restore it. A scope test that
  passes without the gate present is worth nothing.
- Squash bodies break release-please: pass an explicit `--body` to
  `gh pr merge`.

## Deviation from the spec, recorded

The spec's `device_authorizations` column list has no `last_polled_at`, but its
poll route enumerates `slow_down` as a response. A per-`device_code` interval
throttle cannot be enforced without persisting the last poll time, so Task 1
adds `last_polled_at` (nullable). Everything else in the table matches the spec
verbatim.

## File Structure

**Created:**
- `src/splitsmith/db/device_auth.py` -- `DeviceAuthStore`: mint, look up by user
  code, approve, deny, and the mint-at-poll-time consume. Raw (non-tenant)
  session factory, same rationale as `DesktopTokenAuth`.
- `src/splitsmith/ui/device_auth_api.py` -- the six `/api/device/*` routes.
  Hosted-gated the way `sync_api.py` is.
- `alembic/versions/<rev>_add_device_authorizations_and_token_scope.py`
- `src/splitsmith/ui_static/src/components/account/HostedAccountChip.tsx`
- `src/splitsmith/ui_static/src/components/account/DeviceLoginDialog.tsx`
- `src/splitsmith/ui_static/src/pages/DesktopApprove.tsx`
- `src/splitsmith/ui_static/src/lib/deviceApproveStash.ts`
- Tests: `tests/test_device_auth_store.py`, `tests/test_device_auth_routes.py`,
  `tests/test_token_scope_gate.py`, `tests/test_device_auth_docker.py`,
  `tests/test_device_local_endpoints.py`, and four vitest files named in
  their tasks.

**Modified:**
- `src/splitsmith/db/models.py` -- `DeviceAuthorizationRow`; `scope` on
  `DesktopTokenRow`.
- `src/splitsmith/db/desktop_tokens.py` -- `create(..., scope=)`, and
  `DesktopTokenAuth` populating `User.token_scope`.
- `src/splitsmith/auth.py` -- `User.token_scope`; `CompositeAuth` docstring.
- `src/splitsmith/ui/server.py` -- `_PUBLIC_API_PATHS`, the scope gate,
  `AppState.device_auth` + `AppState.device_flow`, the three local device
  routes, the account block on `GET /api/settings/hosted-sync`, router include.
- `src/splitsmith/sync/client.py` -- three device methods + an unauthenticated
  constructor path.
- `src/splitsmith/user_config.py` -- `HostedAccountRef`, `GlobalPrefs.hosted_account`.
- SPA: `lib/api.ts`, `components/layout/GlobalBar.tsx`,
  `components/match/MatchShell.tsx`, `components/layout/globalChrome.test.tsx`,
  `components/match/SyncSettingsDialog.tsx`, `App.tsx`.

---

### Task 1: Schema -- `device_authorizations` + `desktop_tokens.scope`

**Files:**
- Modify: `src/splitsmith/db/models.py` (add `DeviceAuthorizationRow`; add
  `scope` to `DesktopTokenRow` around line 626)
- Modify: `src/splitsmith/db/__init__.py` (export the new row)
- Create: `alembic/versions/<rev>_add_device_authorizations_and_token_scope.py`
- Modify: `src/splitsmith/db/desktop_tokens.py` (`create` takes `scope`)
- Test: `tests/test_device_auth_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `DeviceAuthorizationRow` with columns `id`, `device_code_hash`,
  `user_code`, `device_name`, `scope`, `status`, `user_id`, `created_at`,
  `expires_at`, `last_polled_at`. `DesktopTokenRow.scope: str`.
  `DesktopTokenStore.create(name: str, *, scope: str = "sync") -> tuple[DesktopTokenRecord, str]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_device_auth_schema.py`:

```python
"""Schema-level tests for the device-flow tables (#719).

Covers the two additive schema changes the device flow needs: the new
``device_authorizations`` table and ``desktop_tokens.scope``. The scope
default is the load-bearing part -- an existing pasted token must keep
working, which means it must NOT read as ``'sync'``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from splitsmith.db import Base, DesktopTokenRow, DeviceAuthorizationRow, User, create_engine, sessionmaker


def _factory(tmp_path: Path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'schema.sqlite'}")

    async def _create() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    return sessionmaker(engine)


def test_device_authorization_row_round_trips(tmp_path: Path) -> None:
    sf = _factory(tmp_path)
    expires = datetime.now(UTC) + timedelta(minutes=10)

    async def _run() -> DeviceAuthorizationRow:
        async with sf() as s:
            s.add(
                DeviceAuthorizationRow(
                    device_code_hash="hash-a",
                    user_code="ABCD-2345",
                    device_name="mac studio",
                    scope="sync",
                    expires_at=expires,
                )
            )
            await s.commit()
        async with sf() as s:
            return (
                await s.execute(
                    select(DeviceAuthorizationRow).where(
                        DeviceAuthorizationRow.user_code == "ABCD-2345"
                    )
                )
            ).scalar_one()

    row = asyncio.run(_run())
    assert row.id
    assert row.status == "pending"
    assert row.user_id is None
    assert row.last_polled_at is None


def test_desktop_token_scope_defaults_to_full_for_legacy_rows(tmp_path: Path) -> None:
    """A row inserted without an explicit scope is a legacy pasted token.

    The gate tests ``token_scope == "sync"``, so 'full' is what keeps an
    install in the field working after this ships.
    """
    sf = _factory(tmp_path)

    async def _run() -> str:
        async with sf() as s:
            user = User(email="owner@example.com")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            uid = user.id
        async with sf() as s:
            s.add(DesktopTokenRow(user_id=uid, name="legacy", token_hash="hash-b"))
            await s.commit()
        async with sf() as s:
            row = (
                await s.execute(select(DesktopTokenRow).where(DesktopTokenRow.token_hash == "hash-b"))
            ).scalar_one()
            return row.scope

    assert asyncio.run(_run()) == "full"


def test_store_create_mints_sync_scope_by_default(tmp_path: Path) -> None:
    """Every token minted from here on is sync-scoped -- device flow and
    the account page's manual button alike."""
    from splitsmith.db.desktop_tokens import DesktopTokenStore

    sf = _factory(tmp_path)

    async def _run() -> str:
        async with sf() as s:
            user = User(email="owner@example.com")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            uid = user.id
        store = DesktopTokenStore(sf, user_id=uid)
        record, _raw = await store.create("mac studio")
        async with sf() as s:
            row = (
                await s.execute(select(DesktopTokenRow).where(DesktopTokenRow.id == record.id))
            ).scalar_one()
            return row.scope

    assert asyncio.run(_run()) == "sync"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_device_auth_schema.py -n0 -v`
Expected: FAIL -- `ImportError: cannot import name 'DeviceAuthorizationRow'`.

- [ ] **Step 3: Add the model**

In `src/splitsmith/db/models.py`, add `scope` to `DesktopTokenRow` immediately
after `token_hash` (currently line 626):

```python
    # Device-flow scoping (#719). ``'full'`` is the legacy pasted token
    # that resolves to an unrestricted User; ``'sync'`` is the scoped
    # credential the device flow (and, from #719 on, the account page's
    # manual button) mints. The server-side default is 'full' so rows
    # that predate this column -- and only those -- read as legacy.
    scope: Mapped[str] = mapped_column(String, nullable=False, server_default="full", default="full")
```

Then append a new row class next to `DesktopTokenRow`:

```python
class DeviceAuthorizationRow(Base):
    """One in-flight browser-assisted device authorization (#719).

    The desktop install POSTs to ``/api/device/authorize`` and gets back a
    ``device_code`` (32 bytes, stored only as a SHA-256 hash -- the real
    secret) plus a ``user_code`` (8 characters, low entropy on purpose:
    only usable by a caller who already holds a session and who then has
    to approve, and it dies in 10 minutes).

    Not under RLS, same rationale as ``DesktopTokenRow`` and
    ``ShareTokenRow``: the polling request authenticates from the device
    code alone, before any ``app.user_id`` GUC exists. An RLS'd table
    would make the resolution query return zero rows and break the flow
    outright.

    ``status`` walks pending -> approved|denied -> consumed. Approving
    records the approver and nothing else; the token is minted by the
    first poll that wins the conditional approved -> consumed update, so
    no plaintext credential is ever stored at rest and two concurrent
    polls cannot mint two tokens.

    ``last_polled_at`` backs the per-device_code interval throttle that
    produces the ``slow_down`` poll verdict.
    """

    __tablename__ = "device_authorizations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_ulid)
    device_code_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    user_code: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    device_name: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[str] = mapped_column(String, nullable=False, server_default="sync", default="sync")
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending", default="pending")
    user_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<DeviceAuthorizationRow id={self.id!r} user_code={self.user_code!r} "
            f"status={self.status!r} device_name={self.device_name!r}>"
        )
```

Export it from `src/splitsmith/db/__init__.py` alongside `DesktopTokenRow`
(match the existing `__all__` / import style in that file exactly).

- [ ] **Step 4: Widen `DesktopTokenStore.create`**

In `src/splitsmith/db/desktop_tokens.py`:

```python
    async def create(self, name: str, *, scope: str = "sync") -> tuple[DesktopTokenRecord, str]:
        """Mint a new token. Returns (record, raw_token) - the raw value
        is only ever available here; only its hash is persisted.

        ``scope`` defaults to ``"sync"`` (#719): every token minted after
        that issue lands is scoped to ``/api/sync/*``, whether it came
        from the device flow or the account page's manual button. The
        only ``'full'`` tokens that will ever exist are the ones already
        issued before the column landed.
        """
        plain, hashed = _mint()
        row = DesktopTokenRow(user_id=self._user_id, name=name, token_hash=hashed, scope=scope)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_device_auth_schema.py -n0 -v`
Expected: 3 passed.

- [ ] **Step 6: Write the migration**

Run `uv run alembic revision -m "add device_authorizations and desktop_tokens.scope"`
to get a real revision id, then replace the generated body. `down_revision`
must be `"73e6636ba194"` (the current head -- verify with
`uv run alembic heads` before writing).

```python
"""add device_authorizations table and desktop_tokens.scope

Browser-assisted desktop auth (#719).

1. ``desktop_tokens.scope`` -- ``server_default='full'`` so every row that
   predates this migration backfills to the legacy, unrestricted value.
   That is what keeps a desktop install in the field working: the scope
   gate in ``_auth_gate`` tests ``== "sync"``, which a 'full' row fails.
   Every token minted after this ships is 'sync' (see
   ``DesktopTokenStore.create``). Plain metadata on an already-tenant-
   scoped table; the ``tenant_isolation`` policy keys on ``user_id`` only
   and is unchanged by adding a column, so no RLS DDL here.

2. ``device_authorizations`` -- in-flight device-code authorizations.
   Like ``desktop_tokens`` / ``share_tokens`` / ``sessions``, this table
   is deliberately NOT under Row-Level Security: the poll request
   authenticates from the device code alone, before any ``app.user_id``
   GUC exists, so the lookup runs pre-tenant on the raw session factory.
   An RLS'd table would return zero rows and break authentication.

Revision ID: <rev>
Revises: 73e6636ba194
Create Date: <generated>

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "<rev>"
down_revision: str | Sequence[str] | None = "73e6636ba194"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "desktop_tokens",
        sa.Column("scope", sa.String(), nullable=False, server_default="full"),
    )

    op.create_table(
        "device_authorizations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("device_code_hash", sa.String(), nullable=False),
        sa.Column("user_code", sa.String(), nullable=False),
        sa.Column("device_name", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False, server_default="sync"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_code_hash", name="uq_device_authorizations_device_code_hash"),
        sa.UniqueConstraint("user_code", name="uq_device_authorizations_user_code"),
    )
    op.create_index(
        op.f("ix_device_authorizations_user_code"),
        "device_authorizations",
        ["user_code"],
        unique=False,
    )
    op.create_index(
        op.f("ix_device_authorizations_user_id"),
        "device_authorizations",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_device_authorizations_user_id"), table_name="device_authorizations")
    op.drop_index(op.f("ix_device_authorizations_user_code"), table_name="device_authorizations")
    op.drop_table("device_authorizations")
    op.drop_column("desktop_tokens", "scope")
```

- [ ] **Step 7: Verify the migration applies and is the only head**

Run:
```bash
uv run alembic heads
uv run pytest tests/test_db_foundation.py -n0 -q
```
Expected: exactly one head (the new revision); `test_db_foundation.py` green.

- [ ] **Step 8: Commit**

```bash
git add src/splitsmith/db/models.py src/splitsmith/db/__init__.py \
        src/splitsmith/db/desktop_tokens.py alembic/versions/ \
        tests/test_device_auth_schema.py
git commit -m "feat(db): device_authorizations table and desktop token scope (#719)"
```

---

### Task 2: `DeviceAuthStore` -- the device-flow state machine

**Files:**
- Create: `src/splitsmith/db/device_auth.py`
- Test: `tests/test_device_auth_store.py`

**Interfaces:**
- Consumes: `DeviceAuthorizationRow`, `DesktopTokenRow` (Task 1); `_mint` /
  `_hash` from `splitsmith.db.workers`.
- Produces:
  - `format_user_code(raw: str) -> str` and `normalize_user_code(raw: str) -> str`
  - `DeviceAuthRequest(BaseModel)`: `device_code: str`, `user_code: str`,
    `expires_in: int`, `interval: int`
  - `DevicePending(BaseModel)`: `user_code: str`, `device_name: str`,
    `scope: str`, `created_at: datetime`, `expires_at: datetime`
  - `DeviceAccount(BaseModel)`: `id: str`, `email: str`, `display_name: str | None`
  - `DevicePollResult(BaseModel)`: `status: str` (one of `pending`,
    `slow_down`, `denied`, `expired`, `approved`), `token: str | None = None`,
    `account: DeviceAccount | None = None`, `device_name: str | None = None`
  - `class DeviceAuthStore`:
    - `__init__(session_factory: async_sessionmaker, *, ttl_seconds: int = 600, interval_seconds: int = 5)`
    - `async authorize(device_name: str, *, scope: str = "sync") -> DeviceAuthRequest`
    - `async pending(user_code: str) -> DevicePending | None`
    - `async decide(user_code: str, *, user_id: str, approved: bool) -> bool`
    - `async poll(device_code: str) -> DevicePollResult`
    - `async revoke_token(token: str) -> bool`

- [ ] **Step 1: Write the failing test**

Create `tests/test_device_auth_store.py`:

```python
"""``DeviceAuthStore`` -- the device-flow state machine (#719).

Driven directly against a SQLite state store, no HTTP. The route layer
(tests/test_device_auth_routes.py) exercises the same store wired into
FastAPI; this file pins the behaviour the routes only pass through:

  - authorize mints a hashed device code and a formatted user code
  - poll before approval reports pending, and throttles to slow_down
  - approve -> poll mints exactly one desktop_tokens row, scope 'sync'
  - a SECOND poll after that reports expired, and mints nothing more
  - deny -> poll reports denied
  - an expired authorization reports expired even when approved
  - an unknown device code reports expired (no probing for live codes)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from splitsmith.db import Base, DesktopTokenRow, DeviceAuthorizationRow, User, create_engine, sessionmaker
from splitsmith.db.device_auth import DeviceAuthStore, normalize_user_code


def _factory(tmp_path: Path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'device.sqlite'}")

    async def _create() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    return sessionmaker(engine)


async def _seed_user(sf, email: str = "owner@example.com") -> str:
    async with sf() as s:
        user = User(email=email)
        s.add(user)
        await s.commit()
        await s.refresh(user)
        return user.id


def test_normalize_user_code_is_forgiving_about_case_and_hyphens() -> None:
    assert normalize_user_code("abcd2345") == "ABCD-2345"
    assert normalize_user_code("ABCD-2345") == "ABCD-2345"
    assert normalize_user_code(" abcd 2345 ") == "ABCD-2345"


def test_authorize_stores_only_the_hash(tmp_path: Path) -> None:
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf)

    async def _run() -> tuple[str, str]:
        req = await store.authorize("mac studio")
        async with sf() as s:
            row = (
                await s.execute(
                    select(DeviceAuthorizationRow).where(
                        DeviceAuthorizationRow.user_code == req.user_code
                    )
                )
            ).scalar_one()
            return req.device_code, row.device_code_hash

    plain, hashed = asyncio.run(_run())
    assert plain
    assert plain != hashed
    assert hashed != ""


def test_poll_before_approval_is_pending_then_slow_down(tmp_path: Path) -> None:
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf, interval_seconds=60)

    async def _run() -> tuple[str, str]:
        req = await store.authorize("mac studio")
        first = await store.poll(req.device_code)
        second = await store.poll(req.device_code)
        return first.status, second.status

    assert asyncio.run(_run()) == ("pending", "slow_down")


def test_approve_then_poll_mints_exactly_one_sync_token(tmp_path: Path) -> None:
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf, interval_seconds=0)

    async def _run() -> tuple[str, str, int, str, str]:
        uid = await _seed_user(sf)
        req = await store.authorize("mac studio")
        decided = await store.decide(req.user_code, user_id=uid, approved=True)
        assert decided is True
        first = await store.poll(req.device_code)
        second = await store.poll(req.device_code)
        async with sf() as s:
            rows = list((await s.execute(select(DesktopTokenRow))).scalars())
        return first.status, second.status, len(rows), rows[0].scope, rows[0].name

    status, second_status, count, scope, name = asyncio.run(_run())
    assert status == "approved"
    assert second_status == "expired"
    assert count == 1
    assert scope == "sync"
    assert name == "mac studio"


def test_approved_poll_returns_the_token_and_account(tmp_path: Path) -> None:
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf, interval_seconds=0)

    async def _run():
        uid = await _seed_user(sf, "shooter@example.com")
        req = await store.authorize("mac studio")
        await store.decide(req.user_code, user_id=uid, approved=True)
        return await store.poll(req.device_code)

    result = asyncio.run(_run())
    assert result.token
    assert result.account is not None
    assert result.account.email == "shooter@example.com"
    assert result.device_name == "mac studio"


def test_denied_poll_reports_denied(tmp_path: Path) -> None:
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf, interval_seconds=0)

    async def _run() -> str:
        uid = await _seed_user(sf)
        req = await store.authorize("mac studio")
        await store.decide(req.user_code, user_id=uid, approved=False)
        return (await store.poll(req.device_code)).status

    assert asyncio.run(_run()) == "denied"


def test_expired_authorization_reports_expired_even_when_approved(tmp_path: Path) -> None:
    """An approval that sat past the window must not mint a token."""
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf, interval_seconds=0)

    async def _run() -> tuple[str, int]:
        uid = await _seed_user(sf)
        req = await store.authorize("mac studio")
        await store.decide(req.user_code, user_id=uid, approved=True)
        async with sf() as s:
            row = (
                await s.execute(
                    select(DeviceAuthorizationRow).where(
                        DeviceAuthorizationRow.user_code == req.user_code
                    )
                )
            ).scalar_one()
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await s.commit()
        result = await store.poll(req.device_code)
        async with sf() as s:
            rows = list((await s.execute(select(DesktopTokenRow))).scalars())
        return result.status, len(rows)

    assert asyncio.run(_run()) == ("expired", 0)


def test_unknown_device_code_reports_expired(tmp_path: Path) -> None:
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf)
    assert asyncio.run(store.poll("no-such-device-code")).status == "expired"


def test_pending_returns_none_for_unknown_or_expired_user_code(tmp_path: Path) -> None:
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf, ttl_seconds=0)

    async def _run() -> tuple[object, object]:
        unknown = await store.pending("ZZZZ-9999")
        req = await store.authorize("mac studio")
        stale = await store.pending(req.user_code)
        return unknown, stale

    unknown, stale = asyncio.run(_run())
    assert unknown is None
    assert stale is None


def test_revoke_token_revokes_the_matching_row(tmp_path: Path) -> None:
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf, interval_seconds=0)

    async def _run() -> tuple[bool, bool]:
        uid = await _seed_user(sf)
        req = await store.authorize("mac studio")
        await store.decide(req.user_code, user_id=uid, approved=True)
        result = await store.poll(req.device_code)
        assert result.token is not None
        revoked = await store.revoke_token(result.token)
        async with sf() as s:
            row = (await s.execute(select(DesktopTokenRow))).scalar_one()
            return revoked, row.revoked_at is not None

    assert asyncio.run(_run()) == (True, True)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_device_auth_store.py -n0 -v`
Expected: FAIL -- `ModuleNotFoundError: splitsmith.db.device_auth`.

- [ ] **Step 3: Write the store**

Create `src/splitsmith/db/device_auth.py`:

```python
"""Browser-assisted device authorization (#719).

``DeviceAuthStore`` owns the whole device-code state machine: mint an
authorization, show it to the approving browser, record the decision, and
mint the scoped ``desktop_tokens`` row on the first poll that collects an
approval.

It takes the RAW (non-tenant) session factory, same rationale as
``DesktopTokenAuth`` and ``WorkersStore``: the polling request
authenticates from the device code alone, before any ``app.user_id`` GUC
exists, so every query here runs pre-tenant. The two session-authenticated
methods (``pending`` / ``decide``) take the approver's ``user_id`` as an
explicit argument rather than relying on a tenant being pinned, so the one
factory serves both halves of the flow.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import DesktopTokenRow, DeviceAuthorizationRow
from .models import User as UserRow
from .workers import _hash, _mint

#: The user-code alphabet: A-Z and 2-9 with I, L, O, U, 0 and 1 removed -
#: the characters people mistype when reading a code off one screen and
#: typing it into another. 30 symbols, 8 characters, ~39 bits. Low on
#: purpose: the code is only usable by a caller who already holds a
#: session and who then has to approve, and it dies in ``ttl_seconds``.
#: The real secret is the device code.
_USER_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
_USER_CODE_LENGTH = 8

#: How many times ``authorize`` retries on a user-code collision before
#: giving up. Collisions are vanishingly unlikely at this alphabet size
#: with a 10-minute window; the retry exists so a collision is a slow
#: request rather than a 500.
_USER_CODE_ATTEMPTS = 5


def format_user_code(raw: str) -> str:
    """Render 8 raw characters as ``XXXX-XXXX``."""
    return f"{raw[:4]}-{raw[4:]}"


def normalize_user_code(raw: str) -> str:
    """Canonicalize whatever the user typed into the stored form.

    Uppercases, drops everything that is not in the alphabet (spaces, the
    hyphen the user may or may not have typed), and re-inserts the hyphen.
    Anything that does not reduce to exactly 8 alphabet characters is
    returned as-is so the lookup simply misses -- callers treat a miss and
    a malformed code identically (404), so there is nothing to gain from
    distinguishing them here.
    """
    cleaned = "".join(c for c in raw.upper() if c in _USER_CODE_ALPHABET)
    if len(cleaned) != _USER_CODE_LENGTH:
        return raw.strip().upper()
    return format_user_code(cleaned)


class DeviceAuthRequest(BaseModel):
    """What ``authorize`` hands back to the desktop install.

    ``device_code`` appears here and only here - the row keeps its hash.
    """

    device_code: str
    user_code: str
    expires_in: int
    interval: int


class DevicePending(BaseModel):
    """What the approval screen shows about a live authorization."""

    user_code: str
    device_name: str
    scope: str
    created_at: datetime
    expires_at: datetime


class DeviceAccount(BaseModel):
    """The account a poll resolved to. No secrets, no admin flag."""

    id: str
    email: str
    display_name: str | None = None


class DevicePollResult(BaseModel):
    """One poll verdict.

    ``status`` is one of ``pending``, ``slow_down``, ``denied``,
    ``expired`` or ``approved``. Only ``approved`` carries ``token`` /
    ``account`` / ``device_name``, and only once - a second poll on the
    same device code reports ``expired``.
    """

    status: str
    token: str | None = None
    account: DeviceAccount | None = None
    device_name: str | None = None


def _aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; treat them as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class DeviceAuthStore:
    """The device-flow state machine over ``device_authorizations``."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        ttl_seconds: int = 600,
        interval_seconds: int = 5,
    ) -> None:
        self._session_factory = session_factory
        self._ttl_seconds = ttl_seconds
        self._interval_seconds = interval_seconds

    async def authorize(self, device_name: str, *, scope: str = "sync") -> DeviceAuthRequest:
        """Create a pending authorization and return its two codes."""
        plain, hashed = _mint()
        expires_at = datetime.now(UTC) + timedelta(seconds=self._ttl_seconds)
        for _ in range(_USER_CODE_ATTEMPTS):
            user_code = format_user_code(
                "".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(_USER_CODE_LENGTH))
            )
            row = DeviceAuthorizationRow(
                device_code_hash=hashed,
                user_code=user_code,
                device_name=device_name,
                scope=scope,
                status="pending",
                expires_at=expires_at,
            )
            try:
                async with self._session_factory() as session:
                    session.add(row)
                    await session.commit()
            except IntegrityError:
                continue
            return DeviceAuthRequest(
                device_code=plain,
                user_code=user_code,
                expires_in=self._ttl_seconds,
                interval=self._interval_seconds,
            )
        raise RuntimeError("could not allocate a unique device user code")

    async def pending(self, user_code: str) -> DevicePending | None:
        """The approval screen's view, or ``None`` if there is nothing to
        approve (unknown code, already decided, or expired)."""
        normalized = normalize_user_code(user_code)
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(DeviceAuthorizationRow).where(
                        DeviceAuthorizationRow.user_code == normalized
                    )
                )
            ).scalar_one_or_none()
            if row is None or row.status != "pending":
                return None
            if _aware(row.expires_at) <= datetime.now(UTC):
                return None
            return DevicePending(
                user_code=row.user_code,
                device_name=row.device_name,
                scope=row.scope,
                created_at=_aware(row.created_at),
                expires_at=_aware(row.expires_at),
            )

    async def decide(self, user_code: str, *, user_id: str, approved: bool) -> bool:
        """Record the browser's decision. ``False`` when there was nothing
        live to decide on, which the route turns into a 404."""
        normalized = normalize_user_code(user_code)
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(DeviceAuthorizationRow).where(
                        DeviceAuthorizationRow.user_code == normalized
                    )
                )
            ).scalar_one_or_none()
            if row is None or row.status != "pending" or _aware(row.expires_at) <= now:
                return False
            row.status = "approved" if approved else "denied"
            row.user_id = user_id
            await session.commit()
            return True

    async def poll(self, device_code: str) -> DevicePollResult:
        """One poll from the desktop install.

        An unknown device code reports ``expired``, identically to a real
        one that ran out - a caller must not be able to probe for which
        codes exist.
        """
        hashed = _hash(device_code)
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(DeviceAuthorizationRow).where(
                        DeviceAuthorizationRow.device_code_hash == hashed
                    )
                )
            ).scalar_one_or_none()
            if row is None or row.status == "consumed":
                return DevicePollResult(status="expired")
            if _aware(row.expires_at) <= now:
                return DevicePollResult(status="expired")
            if row.status == "denied":
                return DevicePollResult(status="denied")
            if row.status == "pending":
                last = row.last_polled_at
                too_soon = (
                    last is not None
                    and (now - _aware(last)).total_seconds() < self._interval_seconds
                )
                if too_soon:
                    return DevicePollResult(status="slow_down")
                row.last_polled_at = now
                await session.commit()
                return DevicePollResult(status="pending")

            # status == "approved": the conditional update is the whole
            # concurrency story. Two simultaneous polls both read
            # "approved"; only one of them touches a row here, and only
            # that one mints a token. The loser falls through to expired.
            result = await session.execute(
                update(DeviceAuthorizationRow)
                .where(
                    DeviceAuthorizationRow.id == row.id,
                    DeviceAuthorizationRow.status == "approved",
                )
                .values(status="consumed")
            )
            if result.rowcount != 1:
                await session.rollback()
                return DevicePollResult(status="expired")
            user_row = (
                await session.execute(select(UserRow).where(UserRow.id == row.user_id))
            ).scalar_one_or_none()
            if user_row is None or user_row.deleted_at is not None:
                await session.commit()  # keep it consumed; the account is gone
                return DevicePollResult(status="expired")
            plain, token_hash = _mint()
            session.add(
                DesktopTokenRow(
                    user_id=user_row.id,
                    name=row.device_name,
                    token_hash=token_hash,
                    scope=row.scope,
                )
            )
            await session.commit()
            return DevicePollResult(
                status="approved",
                token=plain,
                account=DeviceAccount(
                    id=user_row.id,
                    email=user_row.email,
                    display_name=user_row.display_name,
                ),
                device_name=row.device_name,
            )

    async def revoke_token(self, token: str) -> bool:
        """Revoke the ``desktop_tokens`` row a raw token resolves to.

        Backs ``DELETE /api/device/session`` - the one route a sync-scoped
        token may reach outside ``/api/sync/*``, so the local install can
        sign itself out without holding a session cookie. Returns ``False``
        for an unknown or already-revoked token; the route reports success
        either way (the credential is dead in both cases).
        """
        hashed = _hash(token)
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(DesktopTokenRow).where(DesktopTokenRow.token_hash == hashed)
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            if row.revoked_at is None:
                row.revoked_at = datetime.now(UTC)
                await session.commit()
            return True
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_device_auth_store.py -n0 -v`
Expected: 10 passed.

- [ ] **Step 5: Prove the concurrency claim is real, not decorative**

Change the conditional `update(...)` in `poll` to an unconditional
`row.status = "consumed"` and re-run
`test_approve_then_poll_mints_exactly_one_sync_token`. It still passes --
the calls are sequential. Restore the conditional update, then add this
test, which drives two polls concurrently:

```python
def test_two_concurrent_polls_mint_one_token(tmp_path: Path) -> None:
    """The conditional approved -> consumed update is the only thing
    stopping two in-flight polls from each minting a credential."""
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf, interval_seconds=0)

    async def _run() -> tuple[list[str], int]:
        uid = await _seed_user(sf)
        req = await store.authorize("mac studio")
        await store.decide(req.user_code, user_id=uid, approved=True)
        results = await asyncio.gather(
            store.poll(req.device_code),
            store.poll(req.device_code),
            return_exceptions=True,
        )
        statuses = [r.status for r in results if not isinstance(r, BaseException)]
        async with sf() as s:
            rows = list((await s.execute(select(DesktopTokenRow))).scalars())
        return sorted(statuses), len(rows)

    statuses, count = asyncio.run(_run())
    assert count == 1
    assert "approved" in statuses
```

Run: `uv run pytest tests/test_device_auth_store.py -n0 -v`
Expected: 11 passed.

Note for the implementer: SQLite serializes writes, so this test proves the
statement shape rather than true parallel execution. The Postgres proof is
Task 5's docker test. If the SQLite version turns out to be unable to fail
even with the guard removed, say so in the PR body rather than keeping a
test that cannot fail -- and rely on Task 5 for the real proof.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/db/device_auth.py tests/test_device_auth_store.py
git commit -m "feat(db): DeviceAuthStore device-code state machine (#719)"
```

---

### Task 3: `User.token_scope` and the one scope gate

**Files:**
- Modify: `src/splitsmith/auth.py` (`User`, `CompositeAuth` docstring)
- Modify: `src/splitsmith/db/desktop_tokens.py` (`DesktopTokenAuth`)
- Modify: `src/splitsmith/ui/server.py` (`_PUBLIC_API_PATHS` ~line 898;
  `_auth_gate` ~line 6265, right after `request.state.user = user`)
- Test: `tests/test_token_scope_gate.py`

**Interfaces:**
- Consumes: `DesktopTokenRow.scope` (Task 1).
- Produces: `User.token_scope: str | None` -- `None` from `MagicLinkAuth` and
  `LoopbackAuth` (unrestricted), the row's `scope` from `DesktopTokenAuth`.
  `_PUBLIC_API_PATHS` gains `/api/device/authorize` and `/api/device/token`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_token_scope_gate.py`:

```python
"""The scope gate: a sync-scoped desktop token reaches /api/sync/* and
nothing else (#719).

This is the security-critical seam of the whole change. It is one
``if`` in ``_auth_gate``, and these tests are the only thing standing
behind it, so run the mutation drill before trusting them: delete the
gate, watch every test in this file that asserts a 403 go red, restore.

Tokens are seeded directly into the DB with an explicit scope rather
than driven through the device flow, so the gate is tested independently
of the flow that produces its input.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select

from splitsmith.db import DesktopTokenRow, User, create_engine, sessionmaker
from splitsmith.db.workers import _mint
from tests.hosted_helpers import _CapturingSender, login


def _seed_token(db_url: str, email: str, *, scope: str) -> str:
    """Insert a desktop token with an explicit scope; return the raw value."""
    engine = create_engine(db_url)
    sf = sessionmaker(engine)
    plain, hashed = _mint()

    async def _insert() -> None:
        async with sf() as s:
            user = (await s.execute(select(User).where(User.email == email))).scalar_one()
            s.add(
                DesktopTokenRow(
                    user_id=user.id,
                    name="mac studio",
                    token_hash=hashed,
                    scope=scope,
                )
            )
            await s.commit()

    asyncio.run(_insert())
    return plain


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_sync_token_reaches_the_sync_surface(
    hosted_app: tuple[TestClient, _CapturingSender], hosted_env: str
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    token = _seed_token(hosted_env, "owner@example.com", scope="sync")

    resp = client.post(
        "/api/sync/matches",
        json={"match_id": "m-scope", "name": "Scope Test"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text


def test_sync_token_is_403_on_the_match_surface(
    hosted_app: tuple[TestClient, _CapturingSender], hosted_env: str
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    token = _seed_token(hosted_env, "owner@example.com", scope="sync")

    resp = client.get("/api/me/matches", headers=_auth(token))
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "token scope"


def test_sync_token_is_403_on_desktop_token_management(
    hosted_app: tuple[TestClient, _CapturingSender], hosted_env: str
) -> None:
    """A sync token must not be able to mint itself a wider one."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    token = _seed_token(hosted_env, "owner@example.com", scope="sync")

    resp = client.post("/api/me/desktop-tokens", json={"name": "wider"}, headers=_auth(token))
    assert resp.status_code == 403, resp.text


def test_sync_token_is_403_on_api_me(
    hosted_app: tuple[TestClient, _CapturingSender], hosted_env: str
) -> None:
    """Recorded consequence: the local install learns its account from the
    device-flow poll response, not from a live /api/me lookup."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    token = _seed_token(hosted_env, "owner@example.com", scope="sync")

    assert client.get("/api/me", headers=_auth(token)).status_code == 403


def test_sync_token_may_delete_its_own_session(
    hosted_app: tuple[TestClient, _CapturingSender], hosted_env: str
) -> None:
    """The single exception - it is what lets the local UI sign out
    without holding a cookie."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    token = _seed_token(hosted_env, "owner@example.com", scope="sync")

    assert client.delete("/api/device/session", headers=_auth(token)).status_code == 200


def test_full_token_is_unaffected(
    hosted_app: tuple[TestClient, _CapturingSender], hosted_env: str
) -> None:
    """An install in the field holding a pasted token must not break."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    token = _seed_token(hosted_env, "owner@example.com", scope="full")

    assert client.get("/api/me", headers=_auth(token)).status_code == 200


def test_session_cookie_is_unaffected(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """MagicLinkAuth leaves token_scope None -- unrestricted."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    assert client.get("/api/me").status_code == 200


def test_sync_token_cannot_reach_a_sync_lookalike_prefix(
    hosted_app: tuple[TestClient, _CapturingSender], hosted_env: str
) -> None:
    """``startswith("/api/sync/")`` with the trailing slash: a route named
    ``/api/syncthing`` must not slip through the gate."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    token = _seed_token(hosted_env, "owner@example.com", scope="sync")

    # No such route exists; the point is that the gate answers first.
    resp = client.get("/api/syncthing/whatever", headers=_auth(token))
    assert resp.status_code == 403, resp.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_token_scope_gate.py -n0 -v`
Expected: the 403-asserting tests FAIL with 200/404 (no gate yet), and
`test_sync_token_may_delete_its_own_session` FAILs with 404 (Task 4 adds
the route -- that one goes green at the end of Task 4, not here; note it in
the commit and do not chase it).

- [ ] **Step 3: Add `token_scope` to `User` and populate it**

`src/splitsmith/auth.py`:

```python
class User(BaseModel):
    """Identity of the caller behind a request.

    ``id`` is the stable foreign key used everywhere user identity is
    embedded (project ownership, ACL rows, sync sentinels). In local
    mode it is the literal string ``"local"``; in hosted mode it is
    the database ULID.

    ``token_scope`` is the credential's reach, not the user's (#719).
    ``None`` means unrestricted - a session cookie or the loopback
    user. ``"sync"`` is a device-flow desktop token, which the gate in
    ``_auth_gate`` confines to ``/api/sync/*`` plus its own sign-out
    route. ``"full"`` is a legacy pasted desktop token, unrestricted by
    design so installs in the field keep working.
    """

    id: str
    email: str
    display_name: str | None = None
    is_admin: bool = False
    token_scope: str | None = None
```

And update `CompositeAuth`'s docstring, which currently claims downstream
code never distinguishes which backend answered -- that stops being true:

```python
class CompositeAuth:
    """Tries each backend in order; the first non-``None`` result wins.

    Hosted mode authenticates two ways: a magic-link session cookie (the
    browser) or a desktop bearer token (the sync push from the desktop
    app). Both resolve to a normal :class:`User`, so ``current_tenant``
    and RLS treat them identically.

    One thing downstream DOES distinguish (#719): ``User.token_scope``.
    ``DesktopTokenAuth`` sets it from the token row; the cookie and
    loopback backends leave it ``None``. ``_auth_gate`` reads it to
    confine a sync-scoped token to the sync surface. Tenancy is still
    backend-agnostic; only reach is not.
    """
```

`src/splitsmith/db/desktop_tokens.py`, last line of `authenticate_request`:

```python
            return User(
                id=user_row.id,
                email=user_row.email,
                display_name=user_row.display_name,
                token_scope=row.scope,
            )
```

- [ ] **Step 4: Add the gate and the public paths**

`src/splitsmith/ui/server.py`, in `_PUBLIC_API_PATHS` (~line 898), after the
`/api/workers/channel` entry:

```python
        # Device-flow bring-up (#719): the desktop install has no cookie
        # jar and, by definition, no bearer yet. Same rationale already
        # recorded above for /api/workers/register - the credential in
        # the request IS the authorization, checked in the handlers, and
        # an unknown device code is indistinguishable from an expired
        # one so there is nothing to probe for.
        "/api/device/authorize",
        "/api/device/token",
```

In `_auth_gate`, immediately after `request.state.user = user`:

```python
        # Scope gate (#719). A device-flow desktop token is issued for the
        # sync surface and reaches nothing else - not /api/me, not the
        # match surface, not the token-management routes it could use to
        # mint itself something wider. The single exception is its own
        # sign-out, which is what lets the local UI unlink without holding
        # a session cookie. token_scope is None for session cookies and
        # the loopback user, and "full" for legacy pasted tokens, so both
        # fall straight through.
        if (
            user.token_scope == "sync"
            and not path.startswith("/api/sync/")
            and path != "/api/device/session"
        ):
            return JSONResponse(status_code=403, content={"detail": "token scope"})
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_token_scope_gate.py -n0 -v`
Expected: 7 passed, 1 failed
(`test_sync_token_may_delete_its_own_session`, waiting on Task 4).

- [ ] **Step 6: Run the mutation drill**

This is not optional and not a formality.

```bash
# 1. Comment out the four-line `if` block in _auth_gate.
uv run pytest tests/test_token_scope_gate.py -n0 -v
#    Expected: the five 403-asserting tests FAIL.
# 2. Restore the block.
uv run pytest tests/test_token_scope_gate.py -n0 -v
#    Expected: back to 7 passed, 1 failed.
```

Record the deleted-gate failure count in the PR body. If any 403 test still
passed with the gate removed, that test is worthless -- fix it before moving on.

- [ ] **Step 7: Check nothing else regressed**

Run:
```bash
uv run pytest tests/test_desktop_token_routes.py tests/test_desktop_tokens.py \
              tests/test_sync_api.py tests/test_auth.py tests/test_auth_routes.py -n0 -q
```
Expected: all green. Existing tests mint tokens through `DesktopTokenStore`,
which now defaults to `scope="sync"` -- if any of them authenticates a minted
token against a non-sync route, it will now 403. That is the gate working;
update the test to mint with `scope="full"` where the intent was "a normal
credential", and leave it alone where the intent was the sync surface.

- [ ] **Step 8: Commit**

```bash
git add src/splitsmith/auth.py src/splitsmith/db/desktop_tokens.py \
        src/splitsmith/ui/server.py tests/test_token_scope_gate.py
git commit -m "feat(auth): scope device tokens to the sync surface (#719)"
```

---

### Task 4: Hosted `/api/device/*` routes

**Files:**
- Create: `src/splitsmith/ui/device_auth_api.py`
- Modify: `src/splitsmith/ui/server.py` (`AppState.device_auth` field near
  line 1159; `state.device_auth = DeviceAuthStore(session_factory)` next to
  `state.workers_store = WorkersStore(session_factory)` at line 5308; router
  include next to the `sync_router` include at line 14178)
- Test: `tests/test_device_auth_routes.py`

**Interfaces:**
- Consumes: `DeviceAuthStore` and its four models (Task 2); the scope gate and
  `_PUBLIC_API_PATHS` entries (Task 3).
- Produces the HTTP contract the local side (Task 6) and the SPA (Tasks 7-8)
  call:
  - `POST /api/device/authorize` `{device_name}` ->
    `{device_code, user_code, verification_uri, verification_uri_complete, expires_in, interval}`
  - `POST /api/device/token` `{device_code}` ->
    `{status, token?, account?, device_name?}`
  - `GET /api/device/pending/{user_code}` -> `{user_code, device_name, scope, created_at, expires_at}`
  - `POST /api/device/pending/{user_code}/approve` -> `{approved: true}`
  - `POST /api/device/pending/{user_code}/deny` -> `{approved: false}`
  - `DELETE /api/device/session` -> `{revoked: bool}`

- [ ] **Step 1: Write the failing test**

Create `tests/test_device_auth_routes.py`:

```python
"""HTTP surface for the browser-assisted device flow (#719).

Task 2 covers the state machine in isolation; this file exercises it
wired into real routes, including the two auth boundaries the routes
themselves own: the public poll pair (no cookie, no bearer) and the
session-cookie approval pair.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.hosted_helpers import _CapturingSender, login


def _authorize(client: TestClient, name: str = "mac studio") -> dict:
    resp = client.post("/api/device/authorize", json={"device_name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_authorize_is_public_and_returns_both_urls(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, _ = hosted_app
    body = _authorize(client)

    assert body["device_code"]
    assert len(body["user_code"]) == 9 and body["user_code"][4] == "-"
    assert body["verification_uri"].endswith("/desktop/approve")
    assert body["verification_uri_complete"].endswith(f"/desktop/approve?code={body['user_code']}")
    assert body["expires_in"] == 600
    assert body["interval"] == 5


def test_poll_before_approval_is_pending(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, _ = hosted_app
    body = _authorize(client)
    resp = client.post("/api/device/token", json={"device_code": body["device_code"]})
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    assert resp.json()["token"] is None


def test_unknown_device_code_reports_expired_not_404(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """Uniform verdict: a caller must not be able to probe for live codes."""
    client, _ = hosted_app
    resp = client.post("/api/device/token", json={"device_code": "nope"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "expired"


def test_pending_screen_requires_a_session(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, _ = hosted_app
    body = _authorize(client)
    assert client.get(f"/api/device/pending/{body['user_code']}").status_code == 401


def test_approve_then_poll_returns_the_token_and_account(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    body = _authorize(client)
    login(client, sender, "owner@example.com")

    pending = client.get(f"/api/device/pending/{body['user_code']}")
    assert pending.status_code == 200, pending.text
    assert pending.json()["device_name"] == "mac studio"
    assert pending.json()["scope"] == "sync"

    approve = client.post(f"/api/device/pending/{body['user_code']}/approve")
    assert approve.status_code == 200, approve.text

    poll = client.post("/api/device/token", json={"device_code": body["device_code"]})
    assert poll.status_code == 200, poll.text
    payload = poll.json()
    assert payload["status"] == "approved"
    assert payload["token"]
    assert payload["account"]["email"] == "owner@example.com"
    assert payload["device_name"] == "mac studio"


def test_second_poll_after_collection_reports_expired(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    body = _authorize(client)
    login(client, sender, "owner@example.com")
    client.post(f"/api/device/pending/{body['user_code']}/approve")
    client.post("/api/device/token", json={"device_code": body["device_code"]})

    again = client.post("/api/device/token", json={"device_code": body["device_code"]})
    assert again.json()["status"] == "expired"
    assert again.json()["token"] is None


def test_deny_then_poll_reports_denied(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    body = _authorize(client)
    login(client, sender, "owner@example.com")
    assert client.post(f"/api/device/pending/{body['user_code']}/deny").status_code == 200

    poll = client.post("/api/device/token", json={"device_code": body["device_code"]})
    assert poll.json()["status"] == "denied"


def test_pending_screen_404s_for_an_already_decided_code(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    body = _authorize(client)
    login(client, sender, "owner@example.com")
    client.post(f"/api/device/pending/{body['user_code']}/approve")

    assert client.get(f"/api/device/pending/{body['user_code']}").status_code == 404


def test_user_code_lookup_is_case_and_hyphen_insensitive(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The operator retypes the code off another screen; be forgiving."""
    client, sender = hosted_app
    body = _authorize(client)
    login(client, sender, "owner@example.com")

    typed = body["user_code"].replace("-", "").lower()
    assert client.get(f"/api/device/pending/{typed}").status_code == 200


def test_device_session_delete_revokes_the_calling_token(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    body = _authorize(client)
    login(client, sender, "owner@example.com")
    client.post(f"/api/device/pending/{body['user_code']}/approve")
    token = client.post("/api/device/token", json={"device_code": body["device_code"]}).json()["token"]

    headers = {"Authorization": f"Bearer {token}"}
    assert client.delete("/api/device/session", headers=headers).status_code == 200
    # The credential is dead: the same bearer now fails auth outright.
    assert client.post(
        "/api/sync/matches", json={"match_id": "m1", "name": "x"}, headers=headers
    ).status_code == 401


def test_device_routes_404_in_local_mode(tmp_path) -> None:
    """Same hosted-gate idiom as sync_api: a local install has no accounts
    to authorize against, so the whole surface is simply absent."""
    from splitsmith import match_model
    from splitsmith.ui.server import create_app

    root = tmp_path / "match"
    match_model.Match.init(root, name="Local")
    client = TestClient(create_app(project_root=root, project_name="Local"))
    assert client.post("/api/device/authorize", json={"device_name": "x"}).status_code == 404
    assert client.post("/api/device/token", json={"device_code": "x"}).status_code == 404
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_device_auth_routes.py -n0 -v`
Expected: all fail with 404 (no routes).

- [ ] **Step 3: Write the router**

Create `src/splitsmith/ui/device_auth_api.py`:

```python
"""Hosted-only ``/api/device/*`` routes: the browser-assisted device flow
(#719, design doc 2026-08-07).

Six routes across three auth boundaries:

- ``authorize`` / ``token`` are **public** (both in ``_PUBLIC_API_PATHS``).
  The desktop install has no cookie jar and no bearer yet; the device code
  in the request is the authorization, and an unknown one is answered
  identically to an expired one so nothing can be probed for.
- The three ``pending`` routes need a **session cookie**: they are the
  approval screen and its two buttons, driven by a signed-in browser.
- ``DELETE /session`` needs the **sync bearer** and is the one route the
  scope gate lets a sync-scoped token reach outside ``/api/sync/*`` - it
  is how the local UI unlinks without holding a cookie.

Local mode has no accounts to authorize against, so every route 404s
there, same guard idiom as ``sync_api.py`` and the desktop-token
management routes. Imports of the db layer stay inside the functions for
the same reason they do there: the local-slim wheel imports this module
and must not pull sqlalchemy in with it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

if TYPE_CHECKING:
    from ..db.device_auth import DeviceAuthStore

router = APIRouter(prefix="/api/device")

#: Where the SPA renders the approval screen. Kept here rather than
#: inlined so the two URL builders below cannot drift apart.
_APPROVE_PATH = "/desktop/approve"


class DeviceAuthorizeRequest(BaseModel):
    """Body for ``POST /api/device/authorize``."""

    device_name: str


class DeviceAuthorizeResponse(BaseModel):
    """Response for ``POST /api/device/authorize``.

    ``device_code`` appears here and only here; the row keeps its hash.
    ``verification_uri_complete`` is the prefilled approval screen the
    desktop UI opens in the operator's own browser - which is what makes
    the remote-host topology work, since the SPA runs where the operator
    is even when the server does not.
    """

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


class DeviceTokenRequest(BaseModel):
    """Body for ``POST /api/device/token``."""

    device_code: str


class DeviceAccountInfo(BaseModel):
    """The linked account, as the desktop install will cache it."""

    id: str
    email: str
    display_name: str | None = None


class DeviceTokenResponse(BaseModel):
    """One poll verdict. Only ``approved`` carries a credential, once."""

    status: str
    token: str | None = None
    account: DeviceAccountInfo | None = None
    device_name: str | None = None


class DevicePendingResponse(BaseModel):
    """What the approval screen shows."""

    user_code: str
    device_name: str
    scope: str
    created_at: str
    expires_at: str


class DeviceDecisionResponse(BaseModel):
    """Result of approve / deny."""

    approved: bool


class DeviceSessionResponse(BaseModel):
    """Result of ``DELETE /api/device/session``."""

    revoked: bool


def _hosted_gate() -> None:
    """Raise 404 outside hosted mode. Lazy import, same as sync_api."""
    from .server import _hosted_mode_active

    if not _hosted_mode_active():
        raise HTTPException(status_code=404, detail="not found")


def _store(request: Request) -> DeviceAuthStore:
    store = request.app.state.splitsmith_state.device_auth
    if store is None:
        raise HTTPException(status_code=500, detail="device auth store unavailable")
    return store


def _current_user(request: Request) -> Any:
    """The user ``_auth_gate`` already resolved. The three session routes
    sit behind that gate, so an anonymous caller 401s before arriving."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


def _public_base(request: Request) -> str:
    """The origin to build verification URLs against.

    ``SPLITSMITH_PUBLIC_URL`` is what the magic-link mailer already uses,
    so the approve link and the sign-in link the operator may need first
    always point at the same host.
    """
    import os

    from .server import SPLITSMITH_PUBLIC_URL_ENV

    configured = os.environ.get(SPLITSMITH_PUBLIC_URL_ENV, "").strip()
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")


@router.post("/authorize", response_model=DeviceAuthorizeResponse)
async def authorize_device(
    body: DeviceAuthorizeRequest, request: Request
) -> DeviceAuthorizeResponse:
    """Start a device authorization. Public - there is no credential yet."""
    _hosted_gate()
    req = await _store(request).authorize(body.device_name.strip() or "desktop")
    base = _public_base(request)
    return DeviceAuthorizeResponse(
        device_code=req.device_code,
        user_code=req.user_code,
        verification_uri=f"{base}{_APPROVE_PATH}",
        verification_uri_complete=f"{base}{_APPROVE_PATH}?code={req.user_code}",
        expires_in=req.expires_in,
        interval=req.interval,
    )


@router.post("/token", response_model=DeviceTokenResponse)
async def poll_device_token(body: DeviceTokenRequest, request: Request) -> DeviceTokenResponse:
    """Poll for the outcome. Public - the device code is the credential.

    Always 200: the verdict is in the body. An unknown device code, a
    consumed one and an expired one all report ``expired``, so a caller
    cannot use the status code to learn which codes exist.
    """
    _hosted_gate()
    result = await _store(request).poll(body.device_code)
    account = (
        DeviceAccountInfo(
            id=result.account.id,
            email=result.account.email,
            display_name=result.account.display_name,
        )
        if result.account is not None
        else None
    )
    return DeviceTokenResponse(
        status=result.status,
        token=result.token,
        account=account,
        device_name=result.device_name,
    )


@router.get("/pending/{user_code}", response_model=DevicePendingResponse)
async def get_pending_device(user_code: str, request: Request) -> DevicePendingResponse:
    """Data for the approval screen. Session cookie required."""
    _hosted_gate()
    _current_user(request)
    pending = await _store(request).pending(user_code)
    if pending is None:
        raise HTTPException(status_code=404, detail="not found")
    return DevicePendingResponse(
        user_code=pending.user_code,
        device_name=pending.device_name,
        scope=pending.scope,
        created_at=pending.created_at.isoformat(),
        expires_at=pending.expires_at.isoformat(),
    )


@router.post("/pending/{user_code}/approve", response_model=DeviceDecisionResponse)
async def approve_pending_device(user_code: str, request: Request) -> DeviceDecisionResponse:
    """Approve. Records status + the approving user; mints nothing."""
    _hosted_gate()
    user = _current_user(request)
    ok = await _store(request).decide(user_code, user_id=user.id, approved=True)
    if not ok:
        raise HTTPException(status_code=404, detail="not found")
    return DeviceDecisionResponse(approved=True)


@router.post("/pending/{user_code}/deny", response_model=DeviceDecisionResponse)
async def deny_pending_device(user_code: str, request: Request) -> DeviceDecisionResponse:
    """Deny. The polling install gets a distinct terminal verdict."""
    _hosted_gate()
    user = _current_user(request)
    ok = await _store(request).decide(user_code, user_id=user.id, approved=False)
    if not ok:
        raise HTTPException(status_code=404, detail="not found")
    return DeviceDecisionResponse(approved=False)


@router.delete("/session", response_model=DeviceSessionResponse)
async def delete_device_session(request: Request) -> DeviceSessionResponse:
    """Revoke the calling token's own row.

    The one route the scope gate lets a sync-scoped token reach outside
    ``/api/sync/*``. Reads the bearer straight off the header rather than
    from the resolved user: the row to revoke is the credential that was
    presented, not every credential that user holds.
    """
    _hosted_gate()
    _current_user(request)
    scheme, _, bearer = request.headers.get("authorization", "").partition(" ")
    token = bearer.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=400, detail="a bearer token is required")
    revoked = await _store(request).revoke_token(token)
    return DeviceSessionResponse(revoked=revoked)
```

- [ ] **Step 4: Wire it into the app**

In `src/splitsmith/ui/server.py`:

1. Add to the `AppState` dataclass next to `workers_store` (~line 1159):

```python
    # Device-flow authorizations (#719). Raw (non-tenant) session factory,
    # same as workers_store: the poll authenticates from the device code
    # alone, before any tenant is pinned. None in local mode.
    device_auth: DeviceAuthStore | None = None
```

Add `DeviceAuthStore` to the `TYPE_CHECKING` import block that already
imports `DesktopTokenStore` (~line 98).

2. Next to `state.workers_store = WorkersStore(session_factory)` (line 5308):

```python
    state.device_auth = DeviceAuthStore(session_factory)
```

with `from ..db.device_auth import DeviceAuthStore` added to the same lazy
import block that already pulls `DesktopTokenAuth` / `DesktopTokenStore`
(line 5227).

3. Next to the `sync_router` include (line 14178):

```python
    from .device_auth_api import router as device_router

    app.include_router(device_router)
```

- [ ] **Step 5: Run the tests**

Run:
```bash
uv run pytest tests/test_device_auth_routes.py tests/test_token_scope_gate.py -n0 -v
```
Expected: all pass, including `test_sync_token_may_delete_its_own_session`,
which was the one failure left over from Task 3.

- [ ] **Step 6: Confirm the local-slim import contract still holds**

Run: `uv run pytest tests/test_local_mode_no_hosted_imports.py -n0 -v`
Expected: PASS. If it fails, a db import escaped to module scope in
`device_auth_api.py` -- move it inside the function.

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/ui/device_auth_api.py src/splitsmith/ui/server.py \
        tests/test_device_auth_routes.py
git commit -m "feat(api): browser-assisted device authorization routes (#719)"
```

---

### Task 5: Postgres proof under docker

**Files:**
- Create: `tests/test_device_auth_docker.py`
- Test: itself

**Interfaces:**
- Consumes: `DeviceAuthStore` (Task 2), the migration (Task 1).
- Produces: nothing consumed by later tasks.

Read `tests/test_sync_docker.py` first and reuse its `hosted_stack` fixture and
`_psql` helpers verbatim -- this file brings no new infrastructure.

- [ ] **Step 1: Write the test**

Create `tests/test_device_auth_docker.py`. Three things SQLite cannot prove:

```python
"""Docker-compose proof for the device flow (#719).

Reuses ``test_hosted_docker_smoke.py``'s ``hosted_stack`` fixture (docker
compose up/down) plus its ``_psql`` helpers - the same idiom every other
``@pytest.mark.docker`` test in this repo uses.

Three things the in-process SQLite suite cannot prove:

1. The migration applies cleanly on live Postgres: ``device_authorizations``
   exists with its two unique constraints, and every pre-existing
   ``desktop_tokens`` row backfilled to ``scope='full'`` - the property
   that keeps installs in the field working.
2. ``device_authorizations`` carries NO RLS policy, deliberately (the poll
   resolves pre-tenant, exactly like ``desktop_tokens``). Asserted against
   ``pg_policies`` under the non-superuser ``splitsmith_app`` role the
   production API actually runs as.
3. The mint-at-poll-time conditional update holds under genuine
   concurrency. SQLite serializes writes, so the in-process version of
   this test proves only the statement shape; this one runs two polls in
   parallel against real Postgres and asserts exactly one token row.

Run with ``PATH=~/.claude-tmp/bin:$PATH uv run pytest -m docker \\
tests/test_device_auth_docker.py -n0 -v`` (docker CLI lives outside the
default non-interactive PATH on this host, and -n0 is required because
the compose fixtures use fixed container names).
"""
```

Then the body, mirroring `test_sync_docker.py`'s structure exactly -- same
`_psql` import, same `pytestmark`, same host-side app-role DSN:

```python
from __future__ import annotations

import asyncio
import uuid

import pytest

from .test_hosted_docker_smoke import _psql

# ``hosted_stack`` is re-exported for global fixture discovery via
# conftest.py (same idiom as test_sync_docker.py - importing it directly
# would trigger ruff F811 against the fixture parameter below).

pytestmark = pytest.mark.docker

# The role the container's own SPLITSMITH_DATABASE_URL uses. Connecting as
# it from the host exercises the same non-superuser path the API runs
# under, not the ``splitsmith`` superuser ``_psql`` seeds with.
HOST_APP_DB_URL = "postgresql+asyncpg://splitsmith_app:splitsmith_app@localhost:5432/splitsmith"


def test_migration_creates_device_authorizations(hosted_stack: None) -> None:
    columns = set(
        _psql(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'device_authorizations'"
        ).split()
    )
    assert columns == {
        "id",
        "device_code_hash",
        "user_code",
        "device_name",
        "scope",
        "status",
        "user_id",
        "created_at",
        "expires_at",
        "last_polled_at",
    }


def test_legacy_desktop_tokens_backfill_to_full(hosted_stack: None) -> None:
    """The property that keeps an install in the field working.

    A row inserted without an explicit scope - which is exactly what every
    pre-#719 row is - must read as 'full', because the gate tests
    ``== "sync"``.
    """
    uid = f"user-legacy-{uuid.uuid4().hex[:8]}"
    _psql(
        f"INSERT INTO users (id, email, entitlement) VALUES ('{uid}', '{uid}@hosted.local', 'free')"
    )
    _psql(
        "INSERT INTO desktop_tokens (id, user_id, name, token_hash) VALUES "
        f"('tok-{uid}', '{uid}', 'legacy paste', 'hash-{uid}')"
    )
    assert _psql(f"SELECT scope FROM desktop_tokens WHERE id = 'tok-{uid}'").strip() == "full"


def test_device_authorizations_has_no_rls_policy(hosted_stack: None) -> None:
    """Deliberately absent, not forgotten.

    The poll resolves from the device code alone, before any
    ``app.user_id`` GUC exists - exactly like ``desktop_tokens``. An RLS
    policy here would make that lookup return zero rows and break
    authentication outright. This asserts the absence so a future
    "enable RLS everywhere" sweep has to argue with a test.
    """
    assert _psql(
        "SELECT policyname FROM pg_policies WHERE tablename = 'device_authorizations'"
    ).strip() == ""


def test_concurrent_polls_mint_one_token_on_postgres(hosted_stack: None) -> None:
    """The real proof of mint-at-poll-time.

    SQLite serializes writes, so the in-process version of this test
    (tests/test_device_auth_store.py) proves the statement shape and
    nothing more. This one runs both polls against live Postgres under
    the app role.
    """
    from splitsmith.db import create_engine, sessionmaker
    from splitsmith.db.device_auth import DeviceAuthStore

    uid = f"user-device-{uuid.uuid4().hex[:8]}"
    _psql(
        f"INSERT INTO users (id, email, entitlement) VALUES ('{uid}', '{uid}@hosted.local', 'free')"
    )
    sf = sessionmaker(create_engine(HOST_APP_DB_URL))
    store = DeviceAuthStore(sf, interval_seconds=0)

    async def _run() -> list[str]:
        req = await store.authorize(f"device-{uid}")
        assert await store.decide(req.user_code, user_id=uid, approved=True) is True
        results = await asyncio.gather(
            store.poll(req.device_code),
            store.poll(req.device_code),
            return_exceptions=True,
        )
        return [r.status for r in results if not isinstance(r, BaseException)]

    statuses = asyncio.run(_run())
    minted = _psql(
        f"SELECT count(*) FROM desktop_tokens WHERE user_id = '{uid}'"
    ).strip()
    assert minted == "1", f"expected exactly one token, got {minted} (statuses: {statuses})"
    assert statuses.count("approved") == 1
    assert _psql(f"SELECT scope FROM desktop_tokens WHERE user_id = '{uid}'").strip() == "sync"
```

Check `_psql`'s exact return shape against `test_hosted_docker_smoke.py`
before relying on `.split()` / `.strip()` -- it wraps `psql -tAc`, so rows
come back newline-separated and untrimmed, but confirm rather than assume.

- [ ] **Step 2: Run it**

Run:
```bash
PATH=~/.claude-tmp/bin:$PATH uv run pytest -m docker tests/test_device_auth_docker.py -n0 -v
```
Expected: 4 passed. `-n0` is mandatory -- the compose fixtures use fixed
container names and concurrent xdist workers collide on them.

- [ ] **Step 3: Prove the concurrency test can fail**

Temporarily change `DeviceAuthStore.poll`'s conditional update to an
unconditional `row.status = "consumed"` and re-run
`test_concurrent_polls_mint_one_token_on_postgres`. It must go red (two
token rows). Restore. If it stays green even unconditionally, the two polls
are not actually overlapping -- fix the test (or say so in the PR body)
rather than shipping a check that cannot fail.

- [ ] **Step 4: Commit**

```bash
git add tests/test_device_auth_docker.py
git commit -m "test: postgres proof for device authorizations (#719)"
```

---

### Task 6: Local side -- prefs, client, and the three endpoints

**Files:**
- Modify: `src/splitsmith/user_config.py` (`HostedAccountRef`, `GlobalPrefs`)
- Modify: `src/splitsmith/sync/client.py` (three device methods + factory)
- Modify: `src/splitsmith/ui/server.py` (`AppState.device_flow`;
  `HostedSyncSettings`; the three routes next to
  `get_hosted_sync_settings` at line 12883)
- Test: `tests/test_device_local_endpoints.py`

**Interfaces:**
- Consumes: the hosted HTTP contract from Task 4.
- Produces, for the SPA (Tasks 7-8):
  - `GET /api/settings/hosted-sync` -> `{base_url, token_set, account}` where
    `account` is `null` or `{id, email, display_name, device_name, linked_at}`
  - `POST /api/settings/hosted-sync/device/start` -> `{user_code,
    verification_uri, verification_uri_complete, expires_in, interval}`
  - `GET /api/settings/hosted-sync/device/status` -> `{status, account,
    device_name}` where `status` is `idle | pending | slow_down | approved |
    denied | expired`
  - `DELETE /api/settings/hosted-sync/session` -> `{cleared: true,
    hosted_revoked: bool}`
  - `HostedSyncClient.device_authorize(device_name) -> dict`,
    `.device_poll(device_code) -> dict`, `.device_revoke_session() -> None`
  - `splitsmith.ui.server._build_device_client(base_url, token=None) -> HostedSyncClient`
    (the seam tests monkeypatch)

- [ ] **Step 1: Write the failing test**

Create `tests/test_device_local_endpoints.py`:

```python
"""Local-side device flow: start, poll, and unlink (#719).

The hosted server is an ``httpx.MockTransport`` double, wired in by
monkeypatching ``server._build_device_client`` - the one seam the three
routes build their client through. No network, no hosted app.

What matters here and is easy to get wrong:
  - the token is written to config.yaml on approval and NEVER echoed back
  - the account block appears in GET /api/settings/hosted-sync
  - polling is throttled server-side, so a fast-refreshing SPA cannot
    trip the hosted side's slow_down
  - sign-out clears local prefs even when the hosted revoke call fails
"""

from __future__ import annotations

from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from splitsmith import match_model, user_config
from splitsmith.sync.client import HostedSyncClient
from splitsmith.ui import server as server_mod
from splitsmith.ui.project import MatchProject
from splitsmith.ui.server import create_app

START = "/api/settings/hosted-sync/device/start"
STATUS = "/api/settings/hosted-sync/device/status"
SESSION = "/api/settings/hosted-sync/session"


def _local_app(tmp_path: Path) -> TestClient:
    root = tmp_path / "match"
    match = match_model.Match.init(root, name="Device Test")
    match.add_shooter(root, match_model.Shooter(slug="me", name="Me"))
    MatchProject.init(match_model.Match.shooter_root(root, "me"), name="Device Test")
    return TestClient(create_app(project_root=root, project_name="Device Test"))


class _FakeHosted:
    """Scripted hosted side. ``verdicts`` is popped one per poll."""

    def __init__(self, verdicts: list[dict], *, revoke_status: int = 200) -> None:
        self.verdicts = verdicts
        self.revoke_status = revoke_status
        self.polls = 0
        self.revokes = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/device/authorize":
            return httpx.Response(
                200,
                json={
                    "device_code": "dev-code",
                    "user_code": "ABCD-2345",
                    "verification_uri": "https://hosted.example/desktop/approve",
                    "verification_uri_complete": (
                        "https://hosted.example/desktop/approve?code=ABCD-2345"
                    ),
                    "expires_in": 600,
                    "interval": 5,
                },
            )
        if path == "/api/device/token":
            self.polls += 1
            verdict = self.verdicts.pop(0) if self.verdicts else {"status": "expired"}
            return httpx.Response(200, json=verdict)
        if path == "/api/device/session":
            self.revokes += 1
            return httpx.Response(self.revoke_status, json={"revoked": True})
        raise AssertionError(f"unexpected hosted call: {path}")


def _install_fake(monkeypatch, fake: _FakeHosted) -> None:
    def _build(base_url: str, *, token: str | None = None) -> HostedSyncClient:
        return HostedSyncClient(
            http=httpx.Client(
                base_url=base_url,
                transport=httpx.MockTransport(fake.handle),
                headers={"Authorization": f"Bearer {token}"} if token else {},
            )
        )

    monkeypatch.setattr(server_mod, "_build_device_client", _build)


_APPROVED = {
    "status": "approved",
    "token": "sync-token-value",
    "account": {"id": "u1", "email": "shooter@example.com", "display_name": None},
    "device_name": "gaspode",
}


def test_start_requires_a_configured_base_url(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    resp = client.post(START)
    assert resp.status_code == 409
    assert resp.json()["detail"] == "hosted_base_url_not_set"


def test_start_returns_the_user_code_but_never_the_device_code(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    _install_fake(monkeypatch, _FakeHosted([]))

    resp = client.post(START)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_code"] == "ABCD-2345"
    assert body["verification_uri_complete"].endswith("code=ABCD-2345")
    assert "device_code" not in resp.text


def test_status_is_idle_before_a_flow_starts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    assert client.get(STATUS).json()["status"] == "idle"


def test_approval_writes_token_and_account_without_echoing_the_token(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    _install_fake(monkeypatch, _FakeHosted([dict(_APPROVED)]))

    client.post(START)
    status = client.get(STATUS)
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "approved"
    assert status.json()["account"]["email"] == "shooter@example.com"
    assert "sync-token-value" not in status.text

    prefs = user_config.load_global_prefs()
    assert prefs.hosted_token == "sync-token-value"
    assert prefs.hosted_account is not None
    assert prefs.hosted_account.email == "shooter@example.com"
    assert prefs.hosted_account.device_name == "gaspode"

    settings = client.get("/api/settings/hosted-sync")
    assert settings.json()["token_set"] is True
    assert settings.json()["account"]["email"] == "shooter@example.com"
    assert "sync-token-value" not in settings.text


def test_status_throttles_to_the_hosted_interval(tmp_path: Path, monkeypatch) -> None:
    """The SPA polls faster than the hosted interval on purpose; the local
    side absorbs that instead of tripping slow_down upstream."""
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    fake = _FakeHosted([{"status": "pending"}, {"status": "pending"}, {"status": "pending"}])
    _install_fake(monkeypatch, fake)

    client.post(START)
    for _ in range(3):
        assert client.get(STATUS).json()["status"] == "pending"
    assert fake.polls == 1


def test_denied_and_expired_are_distinct_terminal_states(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    _install_fake(monkeypatch, _FakeHosted([{"status": "denied"}]))

    client.post(START)
    assert client.get(STATUS).json()["status"] == "denied"
    # Terminal: the pending state is cleared, so the next read is idle.
    assert client.get(STATUS).json()["status"] == "idle"
    assert user_config.load_global_prefs().hosted_token is None


def test_sign_out_clears_prefs_and_revokes_hosted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    fake = _FakeHosted([dict(_APPROVED)])
    _install_fake(monkeypatch, fake)
    client.post(START)
    client.get(STATUS)

    resp = client.delete(SESSION)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"cleared": True, "hosted_revoked": True}
    assert fake.revokes == 1
    prefs = user_config.load_global_prefs()
    assert prefs.hosted_token is None
    assert prefs.hosted_account is None
    # base_url survives: it is how the operator points at staging.
    assert prefs.hosted_base_url == "https://hosted.example"


def test_sign_out_clears_prefs_even_when_the_hosted_revoke_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """Leaving a dead token in config.yaml because the network was down
    is the worse failure. The flag is what lets the UI say so."""
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    fake = _FakeHosted([dict(_APPROVED)], revoke_status=500)
    _install_fake(monkeypatch, fake)
    client.post(START)
    client.get(STATUS)

    resp = client.delete(SESSION)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"cleared": True, "hosted_revoked": False}
    prefs = user_config.load_global_prefs()
    assert prefs.hosted_token is None
    assert prefs.hosted_account is None


def test_device_routes_404_in_hosted_mode(
    hosted_app: tuple[TestClient, object],
) -> None:
    """Local-only, the inverse of the /api/device/* guard."""
    client, _ = hosted_app
    assert client.post(START).status_code == 404
    assert client.get(STATUS).status_code == 404
    assert client.delete(SESSION).status_code == 404
```

`user_config.ENV_HOME` is `"SPLITSMITH_HOME"` -- pointing it at `tmp_path`
is what keeps these tests from writing the developer's real `config.yaml`.
Every test in this file must set it; a missing `monkeypatch.setenv` there
is not a failing test, it is a test that quietly edits `~/.config`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_device_local_endpoints.py -n0 -v`
Expected: all fail (404 / no `_build_device_client`).

- [ ] **Step 3: Add `HostedAccountRef` to prefs**

`src/splitsmith/user_config.py`, above `GlobalPrefs`:

```python
class HostedAccountRef(BaseModel):
    """The hosted account this desktop install is linked to (#719).

    Cached from the device-flow poll response rather than read live: the
    sync-scoped token the flow mints cannot reach ``/api/me``, and
    widening the scope for a cosmetic field is the wrong trade. A hosted-
    side email change therefore will not propagate until the install
    re-links, which is accepted.
    """

    id: str
    email: str
    display_name: str | None = None
    device_name: str
    linked_at: datetime
```

and on `GlobalPrefs`, after `hosted_token`:

```python
    # One nested model rather than five flat fields, per this model's own
    # "add sparingly" instruction (#719).
    hosted_account: HostedAccountRef | None = None
```

Import `datetime` at the top of the module if it is not already imported.

- [ ] **Step 4: Add the device methods to `HostedSyncClient`**

`src/splitsmith/sync/client.py`, after `ensure_match`:

```python
    def device_authorize(self, device_name: str) -> dict:
        """Start a device authorization on the hosted side (#719).

        Public route: the client this runs on carries no bearer, by
        definition - there is no credential yet. Full path, because
        ``base_url`` is the bare hosted origin (#712) and this client
        owns every prefix it uses.
        """
        resp = self._http.post("/api/device/authorize", json={"device_name": device_name})
        self._raise_for_status(resp)
        return resp.json()

    def device_poll(self, device_code: str) -> dict:
        """Poll for the outcome. Always 200; the verdict is in the body."""
        resp = self._http.post("/api/device/token", json={"device_code": device_code})
        self._raise_for_status(resp)
        return resp.json()

    def device_revoke_session(self) -> None:
        """Revoke this install's own token. Needs the bearer."""
        resp = self._http.delete("/api/device/session")
        self._raise_for_status(resp)
```

- [ ] **Step 5: Add the local routes**

In `src/splitsmith/ui/server.py`:

1. Extend `HostedSyncSettings` and add the pending-state model. Near the
   existing `HostedSyncSettings` (~line 4189):

```python
class HostedAccountInfo(BaseModel):
    """The linked hosted account, as the SPA renders it (#719).

    Cached in ``config.yaml`` from the device-flow poll, never fetched
    live - a sync-scoped token cannot read ``/api/me``.
    """

    id: str
    email: str
    display_name: str | None = None
    device_name: str
    linked_at: datetime


class HostedSyncSettings(BaseModel):
    """Response body for GET/PUT /api/settings/hosted-sync (#631, Task 9).

    ``token_set`` is a boolean, never the raw token - the desktop client
    keeps its own copy from the moment it typed it in; the server has no
    business echoing a bearer credential back over localhost HTTP. Same
    rule applies to ``account``: it carries identity, never a credential.
    """

    base_url: str | None = None
    token_set: bool = False
    account: HostedAccountInfo | None = None


class DeviceStartResponse(BaseModel):
    """Response for POST /api/settings/hosted-sync/device/start (#719).

    Deliberately has no ``device_code`` field: the secret stays on this
    process. The SPA only needs the code to show and the URL to open.
    """

    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


class DeviceStatusResponse(BaseModel):
    """Response for GET /api/settings/hosted-sync/device/status (#719).

    ``status`` is ``idle`` (no flow in progress), ``pending``, ``approved``,
    ``denied`` or ``expired``. ``denied`` and ``expired`` are distinct on
    purpose - "you declined this on splitsmith.app" and "the code ran out"
    are different problems and get different copy.
    """

    status: str
    account: HostedAccountInfo | None = None
    device_name: str | None = None
```

2. Add the in-memory pending slot to `AppState`, next to `device_auth`:

```python
    # In-flight device-flow state for the LOCAL install (#719): the
    # device code, the hosted-declared interval, when it expires, and
    # when we last forwarded a poll. Deliberately in memory and not on
    # disk - polling is lazy (the SPA's own poll drives it), so closing
    # the tab leaves no orphaned poller, and restarting the local server
    # mid-flow just means starting over inside a 10-minute window.
    device_flow: dict[str, object] | None = None
```

3. The client factory, module level near `_hosted_mode_active`:

```python
def _build_device_client(base_url: str, *, token: str | None = None) -> HostedSyncClient:
    """Build a ``HostedSyncClient`` for the device-flow calls (#719).

    ``token=None`` gives the unauthenticated client the two public device
    routes need - there is no bearer to send before the flow completes.
    The one seam tests monkeypatch to script a hosted side.
    """
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return HostedSyncClient(http=httpx.Client(base_url=base_url, headers=headers, timeout=30.0))
```

4. The helper and the three routes. Define `_account_info` **above**
   `get_hosted_sync_settings` (line 12883) so the two existing handlers can
   call it too; the three new routes go immediately after
   `put_hosted_sync_settings` (line 12902):

```python
    def _account_info(ref: user_config.HostedAccountRef | None) -> HostedAccountInfo | None:
        if ref is None:
            return None
        return HostedAccountInfo(
            id=ref.id,
            email=ref.email,
            display_name=ref.display_name,
            device_name=ref.device_name,
            linked_at=ref.linked_at,
        )

    @app.post("/api/settings/hosted-sync/device/start", response_model=DeviceStartResponse)
    async def start_device_login() -> DeviceStartResponse:
        """Begin a browser-assisted link. Local-only.

        Names the device after the host so the operator can tell two
        installs apart on the hosted account page.
        """
        if _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        prefs = user_config.load_global_prefs()
        if not prefs.hosted_base_url:
            raise HTTPException(status_code=409, detail="hosted_base_url_not_set")
        client = _build_device_client(prefs.hosted_base_url)
        try:
            started = await run_in_threadpool(client.device_authorize, socket.gethostname())
        except (SyncClientError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=502, detail=f"could not reach the hosted server: {exc}") from exc
        state.device_flow = {
            "device_code": started["device_code"],
            "interval": int(started.get("interval", 5)),
            "expires_at": time.monotonic() + int(started.get("expires_in", 600)),
            "last_polled_at": None,
            "last_status": "pending",
            "base_url": prefs.hosted_base_url,
        }
        return DeviceStartResponse(
            user_code=started["user_code"],
            verification_uri=started["verification_uri"],
            verification_uri_complete=started["verification_uri_complete"],
            expires_in=int(started.get("expires_in", 600)),
            interval=int(started.get("interval", 5)),
        )

    @app.get("/api/settings/hosted-sync/device/status", response_model=DeviceStatusResponse)
    async def get_device_status() -> DeviceStatusResponse:
        """Forward one poll upstream, at most once per hosted interval.

        The SPA polls faster than the hosted side wants; this throttle is
        what stops a fast-refreshing tab from tripping ``slow_down``. In
        between forwards it replays the cached verdict.
        """
        if _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        flow = state.device_flow
        if flow is None:
            return DeviceStatusResponse(status="idle")
        now = time.monotonic()
        if now >= float(flow["expires_at"]):
            state.device_flow = None
            return DeviceStatusResponse(status="expired")
        last = flow["last_polled_at"]
        if last is not None and now - float(last) < float(flow["interval"]):
            return DeviceStatusResponse(status=str(flow["last_status"]))

        client = _build_device_client(str(flow["base_url"]))
        try:
            verdict = await run_in_threadpool(client.device_poll, str(flow["device_code"]))
        except (SyncClientError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=502, detail=f"could not reach the hosted server: {exc}") from exc
        flow["last_polled_at"] = now
        status = str(verdict.get("status", "pending"))
        # slow_down means we polled upstream too fast despite the throttle
        # (clock skew, a restarted hosted process). Nothing has changed for
        # the operator, so report it as pending rather than inventing a
        # sixth UI state.
        if status == "slow_down":
            status = "pending"
        flow["last_status"] = status

        if status == "approved":
            account = verdict.get("account") or {}
            prefs = user_config.load_global_prefs()
            prefs.hosted_token = verdict.get("token")
            prefs.hosted_account = user_config.HostedAccountRef(
                id=str(account.get("id", "")),
                email=str(account.get("email", "")),
                display_name=account.get("display_name"),
                device_name=str(verdict.get("device_name") or socket.gethostname()),
                linked_at=datetime.now(UTC),
            )
            user_config.save_global_prefs(prefs)
            state.device_flow = None
            return DeviceStatusResponse(
                status="approved",
                account=_account_info(prefs.hosted_account),
                device_name=prefs.hosted_account.device_name,
            )
        if status in ("denied", "expired"):
            state.device_flow = None
        return DeviceStatusResponse(status=status)

    @app.delete("/api/settings/hosted-sync/session", response_model=DeviceUnlinkResponse)
    async def unlink_hosted_account() -> DeviceUnlinkResponse:
        """Unlink this install: revoke upstream, then clear local prefs.

        The local copy is cleared even when the hosted call fails
        (offline, or the token was already revoked from the account
        page). Leaving a dead token in config.yaml because the network
        was down is the worse failure; ``hosted_revoked=False`` is what
        lets the UI say the local copy is gone and point at the account
        page for certainty. ``hosted_base_url`` survives - it is how the
        operator points an install at staging.
        """
        if _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        prefs = user_config.load_global_prefs()
        revoked = False
        if prefs.hosted_base_url and prefs.hosted_token:
            client = _build_device_client(prefs.hosted_base_url, token=prefs.hosted_token)
            try:
                await run_in_threadpool(client.device_revoke_session)
                revoked = True
            except (SyncClientError, httpx.HTTPError):
                revoked = False
        prefs.hosted_token = None
        prefs.hosted_account = None
        user_config.save_global_prefs(prefs)
        state.device_flow = None
        return DeviceUnlinkResponse(cleared=True, hosted_revoked=revoked)
```

Add `DeviceUnlinkResponse` next to the other two models:

```python
class DeviceUnlinkResponse(BaseModel):
    """Response for DELETE /api/settings/hosted-sync/session (#719).

    ``cleared`` is always True - the local prefs are gone either way.
    ``hosted_revoked`` is False when the upstream revoke could not be
    confirmed, which the UI turns into "signed out here; check your
    account page to be sure".
    """

    cleared: bool
    hosted_revoked: bool
```

Also update `get_hosted_sync_settings` / `put_hosted_sync_settings` to pass
`account=_account_info(prefs.hosted_account)` in both returns. Check the
imports the block above needs (`socket`, `time`, `datetime`/`UTC`,
`run_in_threadpool` from `starlette.concurrency`) and add whichever are
missing at module top.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_device_local_endpoints.py tests/test_sync_local_endpoints.py -n0 -v`
Expected: all pass. `test_sync_local_endpoints.py`'s
`test_settings_round_trip_masks_token` compares the settings body to an
exact dict -- update it to include `"account": None` rather than loosening
the assertion.

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/user_config.py src/splitsmith/sync/client.py \
        src/splitsmith/ui/server.py tests/test_device_local_endpoints.py \
        tests/test_sync_local_endpoints.py
git commit -m "feat(sync): local device-login endpoints and linked-account prefs (#719)"
```

---

### Task 7: The local UI -- `HostedAccountChip` and `DeviceLoginDialog`

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (methods + types)
- Create: `src/splitsmith/ui_static/src/components/account/HostedAccountChip.tsx`
- Create: `src/splitsmith/ui_static/src/components/account/DeviceLoginDialog.tsx`
- Modify: `src/splitsmith/ui_static/src/components/layout/GlobalBar.tsx`
- Modify: `src/splitsmith/ui_static/src/components/match/MatchShell.tsx`
- Modify: `src/splitsmith/ui_static/src/components/layout/globalChrome.test.tsx`
- Test: `src/splitsmith/ui_static/src/components/account/DeviceLoginDialog.test.tsx`,
  `src/splitsmith/ui_static/src/components/account/HostedAccountChip.test.tsx`

**Interfaces:**
- Consumes: the four local endpoints from Task 6.
- Produces: `<HostedAccountChip />` (no props) and
  `<DeviceLoginDialog onClose={() => void} onLinked={(account) => void} />`.

- [ ] **Step 1: Add the api methods and types**

In `src/splitsmith/ui_static/src/lib/api.ts`, extend `HostedSyncSettings` and
add the new types next to it:

```ts
/** The hosted account this install is linked to (#719). Cached from the
 *  device-flow poll on the server side, never a live lookup -- the
 *  sync-scoped token cannot read /api/me. */
export interface HostedAccountInfo {
  id: string;
  email: string;
  display_name: string | null;
  device_name: string;
  linked_at: string;
}

/** Response from POST /api/settings/hosted-sync/device/start (#719).
 *  Carries no device_code: the secret stays on the local server. */
export interface DeviceStartResponse {
  user_code: string;
  verification_uri: string;
  verification_uri_complete: string;
  expires_in: number;
  interval: number;
}

/** Response from GET /api/settings/hosted-sync/device/status (#719).
 *  ``denied`` and ``expired`` are distinct terminal states on purpose. */
export interface DeviceStatusResponse {
  status: "idle" | "pending" | "approved" | "denied" | "expired";
  account: HostedAccountInfo | null;
  device_name: string | null;
}

/** Response from DELETE /api/settings/hosted-sync/session (#719).
 *  ``hosted_revoked: false`` means the local copy is gone but the hosted
 *  side could not be reached to confirm. */
export interface DeviceUnlinkResponse {
  cleared: boolean;
  hosted_revoked: boolean;
}
```

and add `account: HostedAccountInfo | null;` to `HostedSyncSettings`, plus
the three calls in the `api` object next to `putSyncSettings`:

```ts
  /** Begin a browser-assisted link to the hosted account (#719). 409
   *  ``hosted_base_url_not_set`` when no hosted target is configured. */
  startDeviceLogin: () =>
    request<DeviceStartResponse>("/api/settings/hosted-sync/device/start", {
      method: "POST",
    }),

  /** Poll the in-flight device login. Safe to call on a short interval --
   *  the local server throttles the upstream forward to the hosted
   *  interval, so this never trips ``slow_down``. */
  getDeviceStatus: () =>
    request<DeviceStatusResponse>("/api/settings/hosted-sync/device/status"),

  /** Unlink: revoke upstream, then clear the local token and account. */
  unlinkHostedAccount: () =>
    request<DeviceUnlinkResponse>("/api/settings/hosted-sync/session", {
      method: "DELETE",
    }),
```

- [ ] **Step 2: Write the failing dialog test**

Create `src/splitsmith/ui_static/src/components/account/DeviceLoginDialog.test.tsx`:

```tsx
/**
 * DeviceLoginDialog state machine (#719).
 *
 * The three transitions that carry real user consequence: approval
 * closes the dialog with the linked account, and the two terminal
 * failures render distinct copy -- "you declined this" and "the code
 * ran out" are different problems and must not share a message.
 *
 * Own file (not folded into HostedAccountChip.test.tsx) because
 * src/lib/features.ts caches the deployment mode per module registry.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DeviceLoginDialog } from "@/components/account/DeviceLoginDialog";

const startDeviceLogin = vi.fn();
const getDeviceStatus = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      startDeviceLogin: (...a: unknown[]) => startDeviceLogin(...a),
      getDeviceStatus: (...a: unknown[]) => getDeviceStatus(...a),
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "local" }),
    },
  };
});

const STARTED = {
  user_code: "ABCD-2345",
  verification_uri: "https://hosted.example/desktop/approve",
  verification_uri_complete: "https://hosted.example/desktop/approve?code=ABCD-2345",
  expires_in: 600,
  interval: 1,
};

const ACCOUNT = {
  id: "u1",
  email: "shooter@example.com",
  display_name: null,
  device_name: "gaspode",
  linked_at: "2026-08-08T10:00:00Z",
};

function renderDialog(onLinked = vi.fn()) {
  return {
    onLinked,
    ...render(<DeviceLoginDialog onClose={vi.fn()} onLinked={onLinked} />),
  };
}

describe("DeviceLoginDialog", () => {
  it("shows the user code once the flow starts", async () => {
    startDeviceLogin.mockResolvedValue(STARTED);
    getDeviceStatus.mockResolvedValue({ status: "pending", account: null, device_name: null });
    renderDialog();
    expect(await screen.findByText("ABCD-2345")).toBeInTheDocument();
  });

  it("reports the linked account when the poll approves", async () => {
    startDeviceLogin.mockResolvedValue(STARTED);
    getDeviceStatus.mockResolvedValue({
      status: "approved",
      account: ACCOUNT,
      device_name: "gaspode",
    });
    const { onLinked } = renderDialog();
    await waitFor(() => expect(onLinked).toHaveBeenCalledWith(ACCOUNT));
  });

  it("renders declined copy on denial", async () => {
    startDeviceLogin.mockResolvedValue(STARTED);
    getDeviceStatus.mockResolvedValue({ status: "denied", account: null, device_name: null });
    renderDialog();
    expect(await screen.findByText(/declined/i)).toBeInTheDocument();
    expect(screen.queryByText(/ran out/i)).not.toBeInTheDocument();
  });

  it("renders expiry copy on expiry, distinct from denial", async () => {
    startDeviceLogin.mockResolvedValue(STARTED);
    getDeviceStatus.mockResolvedValue({ status: "expired", account: null, device_name: null });
    renderDialog();
    expect(await screen.findByText(/ran out/i)).toBeInTheDocument();
    expect(screen.queryByText(/declined/i)).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd src/splitsmith/ui_static && corepack pnpm exec vitest run src/components/account/DeviceLoginDialog.test.tsx`
Expected: FAIL -- cannot resolve `DeviceLoginDialog`. Confirm the command
actually ran; a missing `corepack` prefix produces "command not found",
which is not a test result.

- [ ] **Step 4: Write `DeviceLoginDialog`**

Create `src/splitsmith/ui_static/src/components/account/DeviceLoginDialog.tsx`.
Follow `SyncSettingsDialog.tsx`'s overlay skeleton exactly -- `Portal` +
`z-modal` + `useDialogFocus(true, panelRef, onClose)` + `Card`.

Behaviour:
- On mount, call `api.startDeviceLogin()`. A 409 `hosted_base_url_not_set`
  renders "Set the hosted server URL in sync settings first." and nothing else.
- Show `user_code` large and monospaced (`font-mono text-3xl tracking-[0.2em]`),
  with the hyphen as returned.
- A primary button "Open splitsmith.app to approve" that calls
  `window.open(started.verification_uri_complete, "_blank", "noopener")`.
  This is what makes the remote-host topology work: the SPA runs in the
  operator's browser even when the server is on another box.
- Poll `api.getDeviceStatus()` on a `setInterval` of
  `Math.max(started.interval, 1) * 1000`, cleared on unmount and on any
  terminal status. Guard with an `alive` flag the way `AuthProvider.refresh`
  does, so a resolved promise cannot set state after unmount.
- `approved` -> call `onLinked(status.account)` then `onClose()`.
- `denied` -> "You declined this on splitsmith.app." plus a "Try again"
  button that restarts the flow.
- `expired` -> "The code ran out. Start again." plus the same button.
- Copy uses ASCII punctuation only.

- [ ] **Step 5: Run the dialog test**

Run: `cd src/splitsmith/ui_static && corepack pnpm exec vitest run src/components/account/DeviceLoginDialog.test.tsx`
Expected: 4 passed.

- [ ] **Step 6: Write the failing chip test**

Create `src/splitsmith/ui_static/src/components/account/HostedAccountChip.test.tsx`:

```tsx
/**
 * HostedAccountChip (#719) -- the local install's linked-account chip.
 *
 * Local mode throughout this file; the hosted-mode self-gate lives in
 * HostedAccountChip.hosted.test.tsx because src/lib/features.ts caches
 * the deployment mode in a module-level promise with no invalidation,
 * so the first mode resolved in a file wins for the whole file.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HostedAccountChip } from "@/components/account/HostedAccountChip";

const getSyncSettings = vi.fn();
const unlinkHostedAccount = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getSyncSettings: (...a: unknown[]) => getSyncSettings(...a),
      unlinkHostedAccount: (...a: unknown[]) => unlinkHostedAccount(...a),
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "local" }),
    },
  };
});

const ACCOUNT = {
  id: "u1",
  email: "shooter@example.com",
  display_name: null,
  device_name: "gaspode",
  linked_at: "2026-08-08T10:00:00Z",
};

describe("HostedAccountChip (local mode)", () => {
  beforeEach(() => {
    getSyncSettings.mockReset();
    unlinkHostedAccount.mockReset();
  });

  it("offers sign-in when nothing is linked", async () => {
    getSyncSettings.mockResolvedValue({
      base_url: "https://hosted.example",
      token_set: false,
      account: null,
    });
    render(<HostedAccountChip />);
    expect(
      await screen.findByRole("button", { name: /sign in to splitsmith\.app/i }),
    ).toBeInTheDocument();
  });

  it("shows the linked email and device once linked", async () => {
    getSyncSettings.mockResolvedValue({
      base_url: "https://hosted.example",
      token_set: true,
      account: ACCOUNT,
    });
    render(<HostedAccountChip />);
    expect(await screen.findByText("shooter@example.com")).toBeInTheDocument();
    expect(screen.getByText(/gaspode/)).toBeInTheDocument();
  });

  it("signs out and returns to the signed-out label", async () => {
    getSyncSettings.mockResolvedValue({
      base_url: "https://hosted.example",
      token_set: true,
      account: ACCOUNT,
    });
    unlinkHostedAccount.mockResolvedValue({ cleared: true, hosted_revoked: true });
    render(<HostedAccountChip />);
    await userEvent.click(await screen.findByRole("button", { name: /sign out/i }));
    await waitFor(() => expect(unlinkHostedAccount).toHaveBeenCalled());
    expect(
      await screen.findByRole("button", { name: /sign in to splitsmith\.app/i }),
    ).toBeInTheDocument();
  });

  it("says so when the hosted revoke could not be confirmed", async () => {
    // The local copy is gone either way; the operator needs to be told to
    // check the account page. Asserted on rendered text, not on a prop --
    // on #617 a note reached the cell and got ellipsized away while the
    // assertion still passed.
    getSyncSettings.mockResolvedValue({
      base_url: "https://hosted.example",
      token_set: true,
      account: ACCOUNT,
    });
    unlinkHostedAccount.mockResolvedValue({ cleared: true, hosted_revoked: false });
    render(<HostedAccountChip />);
    await userEvent.click(await screen.findByRole("button", { name: /sign out/i }));
    expect(await screen.findByText(/account page/i)).toBeInTheDocument();
  });
});
```

And the hosted-mode self-gate, in its own file
`src/splitsmith/ui_static/src/components/account/HostedAccountChip.hosted.test.tsx`:

```tsx
/**
 * HostedAccountChip is local-only (#719) -- the mirror of AccountChip,
 * which is hosted-only. They must never render together: one shows the
 * session you are logged in as, the other a stored credential.
 *
 * Separate file, not a describe block: the features.ts mode cache is per
 * module registry (see GlobalBar.hosted.test.tsx for the same split).
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HostedAccountChip } from "@/components/account/HostedAccountChip";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getSyncSettings: vi.fn().mockResolvedValue({
        base_url: "https://hosted.example",
        token_set: true,
        account: {
          id: "u1",
          email: "shooter@example.com",
          display_name: null,
          device_name: "gaspode",
          linked_at: "2026-08-08T10:00:00Z",
        },
      }),
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "hosted" }),
    },
  };
});

describe("HostedAccountChip (hosted mode)", () => {
  it("renders nothing", async () => {
    const { container } = render(<HostedAccountChip />);
    // Wait for the mode to resolve, then assert the chip stayed absent --
    // asserting immediately would pass even if the gate did not exist,
    // because the initial render is empty regardless.
    await vi.waitFor(() =>
      expect(screen.queryByText("shooter@example.com")).not.toBeInTheDocument(),
    );
    expect(container).toBeEmptyDOMElement();
  });
});
```

The comment in that last test matters: mutate `HostedAccountChip` to drop
its `mode !== "local"` guard and confirm the test goes red. If it stays
green, the assertion is firing before the mode resolves and proves nothing
-- the exact failure mode caught three times during #550.

- [ ] **Step 7: Write `HostedAccountChip`**

Create `src/splitsmith/ui_static/src/components/account/HostedAccountChip.tsx`:

```tsx
/**
 * HostedAccountChip - the hosted account this LOCAL install is linked to
 * (#719).
 *
 * Deliberately separate from AccountChip, which it resembles and does not
 * mean the same thing: AccountChip shows the session you are logged in
 * *as* (hosted only); this shows the hosted account this desktop install
 * is *linked to* (local only). Collapsing them would conflate a session
 * with a stored credential. They self-gate on opposite deployment modes,
 * so the two never render together.
 *
 * No "last sync" time here on purpose. Sync state is per-match and lives
 * on SyncCard; the only account-level equivalent is the hosted token
 * row's last_used_at, which a sync-scoped token cannot read back.
 */
```

Structure: `useDeploymentMode()` -- return `null` unless `"local"`. Fetch
`api.getSyncSettings()` on mount; hold `account` in state. Reuse
`AccountChip`'s outer chip classes so the two look like one system. Signed
out: a `Button variant="ghost" size="sm"` reading "Sign in to
splitsmith.app" that opens `DeviceLoginDialog`. Signed in: the email
(`display_name ?? email`, `title={email}`) plus a menu carrying the device
name and a "Sign out" `IconButton` with `LogOut`. On a `hosted_revoked:
false` unlink, render a short line pointing at the account page.

- [ ] **Step 8: Mount it and extend the mount guard**

`GlobalBar.tsx` -- import and render alongside `AccountChip`:

```tsx
      <ModeSwitch size="sm" />
      <HostedAccountChip />
      <AccountChip />
```

`MatchShell.tsx` -- add `<HostedAccountChip />` next to the existing
`<AccountChip />` in the mobile nav drawer. That drawer is the only account
surface on a phone inside a match, because `RootLayout` renders no global
bar there (`useShellOwnsMobileAccount`).

`globalChrome.test.tsx` -- extend, do not work around. The existing regex
`/<AccountChip\b/` does not match `<HostedAccountChip`, so add a second
assertion rather than editing the first:

```tsx
  it("renders HostedAccountChip from exactly the same two call sites", () => {
    const sites = readdirSync("src", { recursive: true, encoding: "utf8" })
      .filter((f) => f.endsWith(".tsx") && !f.endsWith(".test.tsx"))
      .map((f) => `src/${f}`)
      .filter((f) => /<HostedAccountChip\b/.test(readFileSync(f, "utf8")));
    // Same two sites as AccountChip and for the same reason: the global
    // bar on desktop (and on /pick on a phone), plus MatchShell's mobile
    // nav drawer, which is the only account surface inside a match on a
    // phone. The two chips self-gate on opposite deployment modes.
    expect(sites.sort()).toEqual([
      "src/components/layout/GlobalBar.tsx",
      "src/components/match/MatchShell.tsx",
    ]);
  });
```

- [ ] **Step 9: Run the frontend suite**

Run:
```bash
cd src/splitsmith/ui_static
corepack pnpm exec vitest run
corepack pnpm exec tsc -b --noEmit
corepack pnpm exec eslint .
```
Expected: all vitest files pass (24 baseline files + the 3 added here);
`tsc` clean; eslint 0 errors (41 pre-existing warnings is the baseline --
do not add to it).

- [ ] **Step 10: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/api.ts \
        src/splitsmith/ui_static/src/components/account/ \
        src/splitsmith/ui_static/src/components/layout/ \
        src/splitsmith/ui_static/src/components/match/MatchShell.tsx
git commit -m "feat(ui): hosted account chip and device login dialog (#719)"
```

---

### Task 8: The approval screen, the login bounce, and demoting the paste

**Files:**
- Create: `src/splitsmith/ui_static/src/pages/DesktopApprove.tsx`
- Create: `src/splitsmith/ui_static/src/lib/deviceApproveStash.ts`
- Modify: `src/splitsmith/ui_static/src/App.tsx` (route + the `AuthGate` stash)
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (three approval calls)
- Modify: `src/splitsmith/ui_static/src/components/match/SyncSettingsDialog.tsx`
- Test: `src/splitsmith/ui_static/src/pages/DesktopApprove.test.tsx`,
  `src/splitsmith/ui_static/src/lib/deviceApproveStash.test.ts`

**Interfaces:**
- Consumes: `GET/POST /api/device/pending/*` from Task 4.
- Produces: route `/desktop/approve`, and
  `stashApproveCode(code: string): void` / `takeApproveCode(): string | null`.

- [ ] **Step 1: Add the api calls**

```ts
/** The device authorization awaiting approval (#719). */
export interface DevicePendingInfo {
  user_code: string;
  device_name: string;
  scope: string;
  created_at: string;
  expires_at: string;
}
```

```ts
  /** Load the approval screen's data. 404 for an unknown, already-decided
   *  or expired code -- the screen renders one message for all three. */
  getDevicePending: (userCode: string) =>
    request<DevicePendingInfo>(`/api/device/pending/${encodeURIComponent(userCode)}`),

  /** Approve the device. Mints nothing -- the desktop install's next poll
   *  collects the credential. */
  approveDevice: (userCode: string) =>
    request<{ approved: boolean }>(
      `/api/device/pending/${encodeURIComponent(userCode)}/approve`,
      { method: "POST" },
    ),

  /** Deny the device. */
  denyDevice: (userCode: string) =>
    request<{ approved: boolean }>(
      `/api/device/pending/${encodeURIComponent(userCode)}/deny`,
      { method: "POST" },
    ),
```

- [ ] **Step 2: Write the failing stash test**

Create `src/splitsmith/ui_static/src/lib/deviceApproveStash.test.ts`:

```ts
/**
 * Surviving the login redirect (#719).
 *
 * An operator who follows verification_uri_complete without a live
 * session gets bounced to /login; the magic link returns them to "/",
 * by which point the code in the URL is long gone. The stash is what
 * carries it across. sessionStorage, not localStorage: it is scoped to
 * the tab that started the flow and dies with it.
 */
import { beforeEach, describe, expect, it } from "vitest";

import { stashApproveCode, takeApproveCode } from "@/lib/deviceApproveStash";

describe("deviceApproveStash", () => {
  beforeEach(() => sessionStorage.clear());

  it("round-trips a code", () => {
    stashApproveCode("ABCD-2345");
    expect(takeApproveCode()).toBe("ABCD-2345");
  });

  it("is single-use, so a later reload does not re-bounce", () => {
    stashApproveCode("ABCD-2345");
    takeApproveCode();
    expect(takeApproveCode()).toBeNull();
  });

  it("returns null when nothing was stashed", () => {
    expect(takeApproveCode()).toBeNull();
  });

  it("ignores a stashed value that is not a plausible user code", () => {
    sessionStorage.setItem("splitsmith.deviceApproveCode", "../../etc/passwd");
    expect(takeApproveCode()).toBeNull();
  });
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd src/splitsmith/ui_static && corepack pnpm exec vitest run src/lib/deviceApproveStash.test.ts`
Expected: FAIL -- module not found.

- [ ] **Step 4: Write the stash**

```ts
/**
 * Carry a device user code across the magic-link login redirect (#719).
 *
 * The device flow's verification_uri_complete points at
 * /desktop/approve?code=XXXX-XXXX. With no session, AuthGate bounces to
 * /login; the magic link lands back on "/" with no query string, so the
 * code has to be parked somewhere. sessionStorage, single-use.
 *
 * If the magic link opens in a DIFFERENT browser the stash is gone --
 * that is the conventional device-flow fallback, and /desktop/approve
 * renders an input for the eight characters instead. Taking that path is
 * what lets magic_link.py stay free of a `next` parameter.
 */

const KEY = "splitsmith.deviceApproveCode";

/** The stored form: 8 alphabet characters, hyphenated. Validated on read
 *  so a hand-edited sessionStorage value cannot steer the redirect. */
const USER_CODE_RE = /^[ABCDEFGHJKMNPQRSTVWXYZ23456789]{4}-[ABCDEFGHJKMNPQRSTVWXYZ23456789]{4}$/;

export function stashApproveCode(code: string): void {
  if (!USER_CODE_RE.test(code)) return;
  sessionStorage.setItem(KEY, code);
}

export function takeApproveCode(): string | null {
  const value = sessionStorage.getItem(KEY);
  sessionStorage.removeItem(KEY);
  return value !== null && USER_CODE_RE.test(value) ? value : null;
}
```

- [ ] **Step 5: Wire the route and the bounce**

In `App.tsx`, add the two imports first --
`import { DesktopApprove } from "@/pages/DesktopApprove";` and
`import { stashApproveCode, takeApproveCode } from "@/lib/deviceApproveStash";`
-- then add the route inside the `RootLayout` block, next to
`admin/workers`:

```tsx
          {/* Device-flow approval screen (#719). Under RootLayout so it
              carries the account chip -- the operator needs to see which
              account they are approving for. */}
          <Route path="desktop/approve" element={<DesktopApprove />} />
```

and in `AuthGate`, replace the anonymous redirect with a version that
stashes first, and add the pickup on the way back:

```tsx
  if (status === "anon" && location.pathname !== "/login") {
    // Device-flow codes have to survive the login round trip (#719): the
    // magic link returns to "/" with no query string, so park the code
    // before we lose it.
    if (location.pathname === "/desktop/approve") {
      const code = new URLSearchParams(location.search).get("code");
      if (code) stashApproveCode(code);
    }
    return <Navigate to="/login" replace />;
  }
  if (status === "authed" && location.pathname === "/") {
    const code = takeApproveCode();
    if (code) return <Navigate to={`/desktop/approve?code=${code}`} replace />;
  }
  return <>{children}</>;
```

- [ ] **Step 6: Write the failing approval-screen test**

Create `src/splitsmith/ui_static/src/pages/DesktopApprove.test.tsx`:

```tsx
/**
 * The approval screen (#719).
 *
 * Hosted-mode surface: this is where the operator's browser turns a
 * pending device authorization into an approval. Covers the prefilled
 * path, both decisions, the different-browser fallback (manual code
 * entry), and the uniform not-found message.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import { DesktopApprove } from "@/pages/DesktopApprove";

const getDevicePending = vi.fn();
const approveDevice = vi.fn();
const denyDevice = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getDevicePending: (...a: unknown[]) => getDevicePending(...a),
      approveDevice: (...a: unknown[]) => approveDevice(...a),
      denyDevice: (...a: unknown[]) => denyDevice(...a),
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "hosted" }),
    },
  };
});

const PENDING = {
  user_code: "ABCD-2345",
  device_name: "gaspode",
  scope: "sync",
  created_at: "2026-08-08T10:00:00Z",
  expires_at: "2026-08-08T10:10:00Z",
};

function renderAt(search: string) {
  return render(
    <MemoryRouter initialEntries={[`/desktop/approve${search}`]}>
      <Routes>
        <Route path="/desktop/approve" element={<DesktopApprove />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("DesktopApprove", () => {
  beforeEach(() => {
    getDevicePending.mockReset();
    approveDevice.mockReset();
    denyDevice.mockReset();
  });

  it("shows the pending device and both decisions", async () => {
    getDevicePending.mockResolvedValue(PENDING);
    renderAt("?code=ABCD-2345");
    expect(await screen.findByText(/gaspode/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /approve/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /deny/i })).toBeInTheDocument();
  });

  it("approves and confirms", async () => {
    getDevicePending.mockResolvedValue(PENDING);
    approveDevice.mockResolvedValue({ approved: true });
    renderAt("?code=ABCD-2345");
    await userEvent.click(await screen.findByRole("button", { name: /approve/i }));
    expect(approveDevice).toHaveBeenCalledWith("ABCD-2345");
    expect(await screen.findByText(/approved/i)).toBeInTheDocument();
  });

  it("denies with distinct copy", async () => {
    getDevicePending.mockResolvedValue(PENDING);
    denyDevice.mockResolvedValue({ approved: false });
    renderAt("?code=ABCD-2345");
    await userEvent.click(await screen.findByRole("button", { name: /deny/i }));
    expect(denyDevice).toHaveBeenCalledWith("ABCD-2345");
    expect(await screen.findByText(/declined/i)).toBeInTheDocument();
  });

  it("falls back to manual entry with no code in the URL", async () => {
    // The magic link opened in a different browser, so the sessionStorage
    // stash is gone. This is the conventional device-flow fallback and the
    // reason magic_link.py needs no `next` parameter.
    getDevicePending.mockResolvedValue(PENDING);
    renderAt("");
    const input = await screen.findByLabelText(/code/i);
    expect(getDevicePending).not.toHaveBeenCalled();
    await userEvent.type(input, "abcd2345");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));
    // The server normalizes case and hyphens, so assert the lookup ran --
    // not its exact spelling.
    expect(getDevicePending).toHaveBeenCalled();
    expect(await screen.findByText(/gaspode/)).toBeInTheDocument();
  });

  it("renders one message for unknown, decided and expired alike", async () => {
    getDevicePending.mockRejectedValue(new ApiError(404, "not found"));
    renderAt("?code=ZZZZ-9999");
    expect(await screen.findByText(/no longer waiting/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
  });
});
```

Check `ApiError`'s constructor signature in `lib/api.ts` before using it --
match whatever it actually takes rather than the `(status, detail)` guessed
here.

- [ ] **Step 7: Write `DesktopApprove`**

Create `src/splitsmith/ui_static/src/pages/DesktopApprove.tsx`:

```tsx
/**
 * Device-flow approval screen (#719).
 *
 * Reached from the desktop install's verification_uri_complete. With a
 * live session and a ?code, it is one click. Without a session, AuthGate
 * stashes the code and bounces through /login, then returns here (see
 * lib/deviceApproveStash). If the magic link opened in a different
 * browser the stash is gone and this renders an input for the eight
 * characters instead -- the conventional device-flow fallback.
 *
 * Approving mints nothing. It records the decision and the approving
 * account; the desktop install's next poll is what collects the
 * credential. That is what keeps a plaintext token from ever sitting at
 * rest, even for the seconds between approval and collection.
 */
```

Uses `useSearchParams`, `api.getDevicePending`, and the two decision calls.
States: loading, the prefilled approval card (device name, requested scope
rendered as "sync only -- it can push matches and nothing else", the code),
the manual-entry form, approved, denied, and not-found. All copy ASCII.

- [ ] **Step 8: Demote the paste path in `SyncSettingsDialog`**

Keep the base URL field exactly as it is. Move the token field inside a
`<details>` disclosure:

```tsx
            <details className="rounded border border-rule bg-surface-2/40 p-3">
              <summary className="cursor-pointer text-xs uppercase tracking-[0.08em] text-muted">
                Advanced: paste a token instead
              </summary>
              <p className="mt-2 text-xs text-muted">
                Sign in from the account chip instead -- it links this
                install through your browser. Pasting a token is for a
                machine with no browser at all.
              </p>
              {/* the existing token input, unchanged */}
            </details>
```

Update the dialog's docstring: the token field is now the escape hatch, and
the primary path is `HostedAccountChip` -> `DeviceLoginDialog`. Leave the
save contract alone (`null` keeps, `""` clears) -- nothing about it changed.

- [ ] **Step 9: Run everything**

```bash
cd src/splitsmith/ui_static
corepack pnpm exec vitest run
corepack pnpm exec tsc -b --noEmit
corepack pnpm exec eslint .
cd -
uv run pytest -n auto -q
```
Expected: vitest all green; tsc clean; eslint 0 errors; the Python suite
green (~2600 tests, roughly 220s under xdist).

Then run the docker test separately, which needs `-n0`:

```bash
PATH=~/.claude-tmp/bin:$PATH uv run pytest -m docker tests/test_device_auth_docker.py -n0 -v
```

- [ ] **Step 10: Commit**

```bash
git add src/splitsmith/ui_static/src
git commit -m "feat(ui): device approval screen and demote the paste path (#719)"
```

---

## Before opening the PR

1. **Run the app, do not just read it.** `splitsmith ui` against a match,
   `splitsmith serve` for hosted (or staging). Click through: sign in from the
   chip, approve in the browser, watch the dialog close, confirm the chip shows
   the email, run a real sync, then sign out. A green suite over this change is
   evidence it broke nothing known -- not evidence the flow works.
2. **Re-run the mutation drill on the scope gate** and put the numbers in the
   PR body: N tests red with the gate deleted, 0 with it restored.
3. **Check the paste path still works** -- seed a `scope='full'` token by hand,
   paste it into the Advanced disclosure, sync. This is the "must not move"
   item and nothing in the automated suite drives it end to end.
4. **`/pick` keeps its mobile account menu.** Resize to a phone viewport on
   `/pick` and confirm the global bar (and both chips) are still there. This
   was nearly regressed during #550.
5. **Merge with an explicit body:**
   `gh pr merge --squash --body "$(cat <<'EOF' ... EOF)"` -- a concatenated
   multi-commit body breaks release-please's parser and the change silently
   vanishes from the changelog while CI stays green.
