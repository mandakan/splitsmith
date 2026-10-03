# Hosted Access Tiers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Account-level feature gating for hosted mode (tiers `full` / `sharing` / `disabled`) plus an access-request queue the admin approves from `/admin/access`.

**Architecture:** A pure `splitsmith.access` module owns the `Feature` enum, the `AccessConfig` tier registry and `features_for`. Hosted routes check a feature through one FastAPI dependency; `PostgresJobBackend.submit` checks `hosted_compute` through an injected async predicate as the backstop. Access requests live in a new `access_requests` table behind `db/access_requests.py`; intake rides the existing magic-link begin path plus one public route; admin routes live in a new router module.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async + Alembic, Pydantic v2, pytest (xdist); React + TypeScript SPA, vitest, Playwright for screenshots.

**Spec:** `docs/superpowers/specs/2026-10-03-hosted-access-tiers-design.md`

## Global Constraints

- `uv` only, never `pip`. `uv run` can rewrite `uv.lock`: never commit `uv.lock` from this work.
- Black line length 110, ruff clean, type hints everywhere, `pathlib.Path` for paths.
- No new dependencies (Python or npm).
- Feature names, verbatim: `sync`, `share`, `create_match`, `raw_upload`, `hosted_compute`.
- Default tiers, verbatim: `full` = all features; `sharing` = `{sync, share}`; `disabled` = none. `default_tier` = `full`.
- Refusal body: HTTP 403, `{"detail": {"code": "feature_required", "feature": "<name>"}}`. A user with zero features: HTTP 403, `{"detail": {"code": "account_disabled"}}`.
- Code checks features, never tier names (backend and SPA).
- Local mode never consults access: every feature is on, every new route 404s.
- Intake response copy, verbatim: "If you have access, a sign-in link is on its way. Otherwise your request has been noted."
- The repo is public. Commit messages and comments stay neutral.
- SPA: build on `components/ui` primitives only; one `primary` Button per view; no issue numbers or flavour copy in the UI; ASCII punctuation in copy.
- Tests run with `uv run pytest -n0 <files>` while iterating; the full suite (`uv run pytest`) before the last commit of each task.
- Commit trailer on every commit:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01QpwUtKUhYZRaEbZthbv5HM
  ```

## Review Focus

1. **A job kind or route added later by someone who never read this plan.** Expected: a `sharing` user still cannot reach the fleet. Pinned by the registry-enumerating backstop test (Task 4) and the `_build_tenant` wiring test, both of which must fail when the backstop is deleted.
2. **Existence leak through intake.** Known account, pending, declined, approved-not-yet-signed-in, brand-new and rate-limited emails must get byte-identical responses on both `/api/v1/auth/begin` and `/api/v1/access-requests`. Pinned in Task 6.
3. **A stale tab after a downgrade.** A `full` user downgraded to `sharing` mid-session hits a gated control. Expected: one muted line, not a toast or a blank page. Pinned by `lib/access.test.ts` (Task 8) and the mid-session downgrade test (Task 3).
4. **The desktop token of a downgraded or disabled user.** `sync` stays for `sharing`; `disabled` must stop the desktop's sync at the next request, not at token expiry. Pinned in Task 3 (the tier is read on every request, never cached on the token).
5. **Two admins deciding the same request.** The second decide is a 409, and approve never creates a duplicate user. Pinned in Task 5 (store) and Task 7 (route).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/splitsmith/access.py` (new) | `Feature`, `AccessConfig`, `features_for`, `access_config()` loader, `FeatureRequiredError`. Pure, no DB, no FastAPI. |
| `src/splitsmith/config.py` | `Config.access: AccessConfig` field. |
| `src/splitsmith/db/models.py` | `User.access_tier` column; `AccessRequest` model. |
| `alembic/versions/<rev>_access_tiers_and_requests.py` (new) | Migration. |
| `src/splitsmith/db/access_requests.py` (new) | `AccessRequestStore`, `AccountAccessStore`. |
| `src/splitsmith/db/email.py` | Two new `EmailSender` methods. |
| `src/splitsmith/db/magic_link.py` | `mint_link`, `default_tier`, `on_blocked` hook, tier on `User`. |
| `src/splitsmith/db/desktop_tokens.py` | Tier on `User`. |
| `src/splitsmith/auth.py` | `User.access_tier`, `User.features`. |
| `src/splitsmith/ui/access_gate.py` (new) | `require_feature` dependency, `features_of(request, user)`. |
| `src/splitsmith/db/job_backend.py` | `submit_allowed` predicate. |
| `src/splitsmith/ui/server.py` | Wiring: state fields, gates on existing routes, disabled gate, intake route, exception handler, `/api/me`. |
| `src/splitsmith/ui/admin_access_api.py` (new) | `/api/admin/access-requests*`, `/api/admin/users*`, `/api/admin/access-tiers`. |
| `src/splitsmith/ui_static/src/lib/access.ts` (new) | `can`, `featureRefusal`. |
| `src/splitsmith/ui_static/src/lib/adminAccess.ts` (new) | Admin page derivation. |
| `src/splitsmith/ui_static/src/pages/AdminAccess.tsx` (new) | Admin page. |
| `src/splitsmith/ui_static/src/pages/Login.tsx` | Request-access form. |
| `site/index.html`, `functions/api/waitlist.js`, `scripts/import_waitlist.py` | Waitlist to one list. |

---

### Task 1: Access registry (pure module)

**Files:**
- Create: `src/splitsmith/access.py`
- Modify: `src/splitsmith/config.py` (the `Config` class, ~line 536)
- Test: `tests/test_access.py`

**Interfaces:**
- Produces:
  - `class Feature(StrEnum)`: `sync`, `share`, `create_match`, `raw_upload`, `hosted_compute`
  - `ALL_FEATURES: frozenset[Feature]`
  - `class AccessConfig(BaseModel)`: `tiers: dict[str, frozenset[Feature]]`, `default_tier: str`
  - `features_for(tier: str | None, email: str, config: AccessConfig, admin_emails: frozenset[str]) -> frozenset[Feature]`
  - `access_config() -> AccessConfig` (reads `SPLITSMITH_CONFIG`)
  - `class FeatureRequiredError(Exception)` with attribute `feature: Feature`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_access.py
"""Account features: the tier registry and its resolution (spec 2026-10-03)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from splitsmith.access import ALL_FEATURES, AccessConfig, Feature, access_config, features_for
from splitsmith.config import Config

ADMINS = frozenset({"boss@example.com"})


def test_default_tiers() -> None:
    cfg = AccessConfig()
    assert features_for("full", "a@x.se", cfg, ADMINS) == ALL_FEATURES
    assert features_for("sharing", "a@x.se", cfg, ADMINS) == {Feature.sync, Feature.share}
    assert features_for("disabled", "a@x.se", cfg, ADMINS) == frozenset()
    assert cfg.default_tier == "full"


def test_env_admin_gets_everything_whatever_the_tier() -> None:
    cfg = AccessConfig()
    assert features_for("disabled", "Boss@Example.com", cfg, ADMINS) == ALL_FEATURES


def test_unknown_tier_fails_closed_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert features_for("platinum", "a@x.se", AccessConfig(), ADMINS) == frozenset()
    assert "platinum" in caplog.text


def test_none_tier_fails_closed() -> None:
    assert features_for(None, "a@x.se", AccessConfig(), ADMINS) == frozenset()


def test_yaml_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(
        "access:\n  default_tier: sharing\n  tiers:\n"
        "    full: [sync, share, create_match, raw_upload, hosted_compute]\n"
        "    sharing: [sync, share]\n    club: [sync, share, hosted_compute]\n"
    )
    monkeypatch.setenv("SPLITSMITH_CONFIG", str(path))
    cfg = access_config()
    assert cfg.default_tier == "sharing"
    assert features_for("club", "a@x.se", cfg, ADMINS) == {Feature.sync, Feature.share, Feature.hosted_compute}


def test_bad_feature_name_fails_validation() -> None:
    with pytest.raises(ValidationError):
        AccessConfig.model_validate({"tiers": {"full": ["sync", "teleport"]}, "default_tier": "full"})


def test_default_tier_must_exist() -> None:
    with pytest.raises(ValidationError):
        AccessConfig.model_validate({"tiers": {"full": ["sync"]}, "default_tier": "sharing"})


def test_config_carries_access() -> None:
    assert Config().access == AccessConfig()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest -n0 tests/test_access.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'splitsmith.access'`

- [ ] **Step 3: Implement**

```python
# src/splitsmith/access.py
"""Account-level features for hosted mode (spec 2026-10-03).

A tier is a named set of :class:`Feature`; code checks features, never
tier names, so adding a tier is configuration. Match-level capabilities
(``ui/capabilities.py``) are a separate axis that depends on a match's
origin; a request needs both. Local mode never consults this module.
"""

from __future__ import annotations

import logging
import os
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


class Feature(StrEnum):
    sync = "sync"
    share = "share"
    create_match = "create_match"
    raw_upload = "raw_upload"
    hosted_compute = "hosted_compute"


ALL_FEATURES: frozenset[Feature] = frozenset(Feature)


def _default_tiers() -> dict[str, frozenset[Feature]]:
    return {
        "full": ALL_FEATURES,
        "sharing": frozenset({Feature.sync, Feature.share}),
        "disabled": frozenset(),
    }


class AccessConfig(BaseModel):
    tiers: dict[str, frozenset[Feature]] = Field(default_factory=_default_tiers)
    default_tier: str = "full"

    @model_validator(mode="after")
    def _default_tier_exists(self) -> AccessConfig:
        if self.default_tier not in self.tiers:
            raise ValueError(f"default_tier {self.default_tier!r} is not one of {sorted(self.tiers)}")
        return self


class FeatureRequiredError(Exception):
    """Raised where a hosted account lacks ``feature`` (mapped to a 403)."""

    def __init__(self, feature: Feature) -> None:
        super().__init__(f"feature required: {feature.value}")
        self.feature = feature


def features_for(
    tier: str | None, email: str, config: AccessConfig, admin_emails: frozenset[str]
) -> frozenset[Feature]:
    if email.strip().lower() in admin_emails:
        return ALL_FEATURES
    if tier is None or tier not in config.tiers:
        logger.warning("account tier %r is not in the access registry; no features granted", tier)
        return frozenset()
    return config.tiers[tier]


def access_config() -> AccessConfig:
    """The registry from ``SPLITSMITH_CONFIG`` when set, else the defaults."""
    from .config import Config
    from .runtime import ENV_CONFIG_FILE

    raw = os.environ.get(ENV_CONFIG_FILE, "").strip()
    if not raw:
        return AccessConfig()
    return Config.load(Path(raw).expanduser()).access
```

In `config.py`, add the import near the other local imports and the field on `Config`:

```python
from .access import AccessConfig
...
    footage_sort: FootageSortConfig = Field(default_factory=FootageSortConfig)
    access: AccessConfig = Field(default_factory=AccessConfig)
```

Check for an import cycle: `access.py` must not import `config` at module level (it imports it inside `access_config`).

- [ ] **Step 4: Run tests**

Run: `uv run pytest -n0 tests/test_access.py tests/test_local_mode_no_hosted_imports.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/access.py src/splitsmith/config.py tests/test_access.py
git commit -m "feat(access): account feature registry with tiers"
```

---

### Task 2: Schema: `users.access_tier` and `access_requests`

**Files:**
- Modify: `src/splitsmith/db/models.py` (class `User`, ~line 51; add class `AccessRequest` after it)
- Modify: `src/splitsmith/db/__init__.py` (export `AccessRequest`)
- Create: `alembic/versions/d4a8c1f37e20_access_tiers_and_requests.py` (down_revision `c05ac6f8592c`; confirm with `uv run alembic heads` first and use whatever it prints)
- Test: `tests/test_access_schema.py`

**Interfaces:**
- Produces: `User.access_tier: Mapped[str]` (not null, server default `'full'`); `AccessRequest` ORM model with columns exactly as in the spec table.

- [ ] **Step 1: Failing test**

```python
# tests/test_access_schema.py
from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import insert, select

from splitsmith.db import AccessRequest, Base, User, create_engine, sessionmaker


def _run(coro):  # noqa: ANN001, ANN202
    return asyncio.run(coro)


def test_user_row_inserted_without_tier_gets_full(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")

    async def go() -> str:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Core insert, bypassing the ORM default: the server default must apply.
            await conn.execute(insert(User.__table__).values(id="u1", email="a@x.se", entitlement="free"))
        async with sessionmaker(engine)() as s:
            return (await s.execute(select(User.access_tier).where(User.id == "u1"))).scalar_one()

    assert _run(go()) == "full"


def test_access_request_email_is_unique(tmp_path: Path) -> None:
    import pytest
    from sqlalchemy.exc import IntegrityError

    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")

    async def go() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sessionmaker(engine)() as s:
            s.add(AccessRequest(email="a@x.se", source="form", status="pending"))
            await s.commit()
            s.add(AccessRequest(email="a@x.se", source="login", status="pending"))
            with pytest.raises(IntegrityError):
                await s.commit()

    _run(go())
```

- [ ] **Step 2: Run, expect FAIL** (`ImportError: cannot import name 'AccessRequest'`)

Run: `uv run pytest -n0 tests/test_access_schema.py -q`

- [ ] **Step 3: Implement the models**

In `class User`, after `entitlement_until`:

```python
    # Account access tier (spec 2026-10-03): names a feature set in
    # ``splitsmith.access.AccessConfig``. Separate from ``entitlement``,
    # which stays reserved for billing. The server default keeps a
    # non-ORM insert safe-for-today; the ORM always sets it explicitly.
    access_tier: Mapped[str] = mapped_column(String, nullable=False, server_default="full")
```

New model:

```python
class AccessRequest(Base):
    """A request for a hosted account (spec 2026-10-03). Not under RLS,
    like ``users``: only admin routes and the intake writer touch it."""

    __tablename__ = "access_requests"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_ulid)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String, nullable=True)
    tier_granted: Mapped[str | None] = mapped_column(String, nullable=True)
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

Use whatever `Text`, `func`, `new_ulid` imports `models.py` already has; add the missing ones to its import block.

- [ ] **Step 4: Write the migration**

```python
"""users.access_tier and access_requests (spec 2026-10-03)

Revision ID: d4a8c1f37e20
Revises: c05ac6f8592c
Create Date: 2026-10-03 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4a8c1f37e20"
down_revision: str | Sequence[str] | None = "c05ac6f8592c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Every existing account becomes ``full``: nothing changes for anyone already in."""
    op.add_column("users", sa.Column("access_tier", sa.String(), nullable=False, server_default="full"))
    op.create_table(
        "access_requests",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "last_requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("tier_granted", sa.String(), nullable=True),
        sa.Column("email_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("access_requests")
    op.drop_column("users", "access_tier")
```

- [ ] **Step 5: Run tests, including the migration and schema-diff gates**

Run: `uv run pytest -n0 tests/test_access_schema.py tests/test_db_foundation.py tests/test_schema_diff.py -q`
Expected: PASS. If `test_db_foundation` compares alembic head to the models, a mismatch there means the migration and the model disagree (unique constraint name, server default); fix the migration, not the test.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/db/models.py src/splitsmith/db/__init__.py alembic/versions/d4a8c1f37e20_access_tiers_and_requests.py tests/test_access_schema.py
git commit -m "feat(db): users.access_tier and access_requests"
```

---

### Task 3: Tier on the request user, route gates, the disabled gate

**Files:**
- Modify: `src/splitsmith/auth.py` (`class User`)
- Modify: `src/splitsmith/db/magic_link.py` (`MagicLinkAuth.__init__`, `complete_login`, `authenticate_request`)
- Modify: `src/splitsmith/db/desktop_tokens.py` (`DesktopTokenAuth.authenticate_request`)
- Create: `src/splitsmith/ui/access_gate.py`
- Modify: `src/splitsmith/ui/server.py`: `AppState` (~line 1830), hosted bootstrap (~6856-6890), `_auth_gate` (~8536), `/api/me` GET and PATCH (~11304, ~11347), the routes listed in Step 5, `include_router(sync_router)` (~17594)
- Test: `tests/test_access_gates.py`

**Interfaces:**
- Consumes: `Feature`, `features_for`, `access_config`, `AccessConfig` (Task 1); `User.access_tier` column (Task 2).
- Produces:
  - `auth.User.access_tier: str | None = None`, `auth.User.features: list[str] = []`
  - `AppState.access: AccessConfig`
  - `access_gate.features_of(request: Request, user: User) -> frozenset[Feature]`
  - `access_gate.require_feature(feature: Feature) -> Callable` (a FastAPI dependency)
  - `MagicLinkAuth(..., default_tier: str = "full")`

- [ ] **Step 1: Failing tests**

```python
# tests/test_access_gates.py
"""Hosted route gates per account feature (spec 2026-10-03)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from splitsmith.db import User as UserRow
from splitsmith.db import create_engine, sessionmaker
from tests.hosted_helpers import hosted_app, hosted_env, login  # noqa: F401  (fixtures)


def set_tier(db_url: str, email: str, tier: str) -> None:
    async def go() -> None:
        engine = create_engine(db_url)
        async with sessionmaker(engine)() as s:
            await s.execute(update(UserRow).where(UserRow.email == email).values(access_tier=tier))
            await s.commit()
        await engine.dispose()

    asyncio.run(go())


GATED = [
    # (method, path, json/body kwargs, feature)
    ("post", "/api/match/create-manual", {"json": {"name": "x"}}, "create_match"),
    ("post", "/api/match/create-from-scoreboard", {"json": {}}, "create_match"),
    ("post", "/api/me/projects/import", {"data": {"dest_root": "x"}}, "create_match"),
    ("post", "/api/me/raw/upload", {"files": {"file": ("a.mp4", b"x")}}, "raw_upload"),
    ("post", "/api/me/raw/upload/multipart/create", {"json": {}}, "raw_upload"),
    ("post", "/api/me/raw/upload/multipart/part-url", {"json": {}}, "raw_upload"),
    ("post", "/api/me/raw/upload/multipart/complete", {"json": {}}, "raw_upload"),
    ("post", "/api/me/raw/upload/multipart/abort", {"json": {}}, "raw_upload"),
    ("post", "/api/me/jobs/nope/retry", {}, "hosted_compute"),
    ("get", "/api/sync/fingerprints", {}, "sync"),
]


@pytest.mark.parametrize(("method", "path", "kwargs", "feature"), GATED)
def test_sharing_tier_is_refused_with_the_feature_named(
    hosted_app, hosted_env, method, path, kwargs, feature  # noqa: ANN001
) -> None:
    client, sender = hosted_app
    login(client, sender, "friend@x.se")
    set_tier(hosted_env, "friend@x.se", "sharing" if feature != "sync" else "disabled")
    resp = getattr(client, method)(path, **kwargs)
    if feature == "sync":
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "account_disabled"
        return
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == {"code": "feature_required", "feature": feature}


@pytest.mark.parametrize(("method", "path", "kwargs", "feature"), GATED)
def test_full_tier_passes_the_gate(hosted_app, method, path, kwargs, feature) -> None:  # noqa: ANN001
    client, sender = hosted_app
    login(client, sender, "me@x.se")
    resp = getattr(client, method)(path, **kwargs)
    # Whatever the handler does next (400, 404, 409, 503 for no storage),
    # it is not the access gate.
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    detail = body.get("detail")
    assert not (isinstance(detail, dict) and detail.get("code") in {"feature_required", "account_disabled"})


def test_share_creation_needs_share(hosted_app, hosted_env) -> None:  # noqa: ANN001
    from tests.hosted_helpers import seed_match

    client, sender = hosted_app
    login(client, sender, "friend@x.se")
    seed_match(hosted_env, "friend@x.se", "m1")
    set_tier(hosted_env, "friend@x.se", "disabled")
    resp = client.post("/api/matches/m1/match/shares", json={"scope": "read"})
    assert resp.status_code == 403


def test_me_reports_tier_and_features(hosted_app, hosted_env) -> None:  # noqa: ANN001
    client, sender = hosted_app
    login(client, sender, "friend@x.se")
    set_tier(hosted_env, "friend@x.se", "sharing")
    me = client.get("/api/me").json()
    assert me["access_tier"] == "sharing"
    assert sorted(me["features"]) == ["share", "sync"]


def test_disabled_account_keeps_me_and_logout_only(hosted_app, hosted_env) -> None:  # noqa: ANN001
    client, sender = hosted_app
    login(client, sender, "gone@x.se")
    set_tier(hosted_env, "gone@x.se", "disabled")
    assert client.get("/api/me").status_code == 200
    assert client.get("/api/me/recent-projects").status_code == 403
    assert client.post("/api/v1/auth/logout").status_code == 200


def test_downgrade_takes_effect_on_the_next_request(hosted_app, hosted_env) -> None:  # noqa: ANN001
    client, sender = hosted_app
    login(client, sender, "me@x.se")
    assert "create_match" in client.get("/api/me").json()["features"]
    set_tier(hosted_env, "me@x.se", "sharing")
    assert "create_match" not in client.get("/api/me").json()["features"]


def test_new_account_gets_the_default_tier(hosted_env, monkeypatch, tmp_path) -> None:  # noqa: ANN001
    cfg = tmp_path / "c.yaml"
    cfg.write_text("access:\n  default_tier: sharing\n")
    monkeypatch.setenv("SPLITSMITH_CONFIG", str(cfg))
    from splitsmith.ui.server import create_app
    from tests.hosted_helpers import _CapturingSender

    app = create_app()
    sender = _CapturingSender()
    app.state.splitsmith_state.auth.backends[0]._email = sender
    with TestClient(app, follow_redirects=False) as client:
        login(client, sender, "new@x.se")
        assert client.get("/api/me").json()["access_tier"] == "sharing"


def test_desktop_token_user_carries_the_tier(hosted_app, hosted_env) -> None:  # noqa: ANN001
    """A sync token of a disabled account stops at the next request."""
    client, sender = hosted_app
    login(client, sender, "me@x.se")
    token = client.post("/api/me/desktop-tokens", json={"name": "mac"}).json()["token"]
    client.cookies.clear()
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/sync/fingerprints", headers=headers).status_code == 200
    set_tier(hosted_env, "me@x.se", "disabled")
    assert client.get("/api/sync/fingerprints", headers=headers).status_code == 403
```

Before running, check two details against the code and fix the test, not the gate, if they differ: the desktop-token creation route and response key (grep `desktop-tokens` in `server.py`), and the `/api/me/recent-projects` path (grep `recent-projects`). If the fixtures `hosted_app` / `hosted_env` are not importable that way, follow how `tests/test_mirror_read_only.py` pulls them in.

- [ ] **Step 2: Run, expect FAIL** (no `access_tier` on `/api/me`, no 403s)

Run: `uv run pytest -n0 tests/test_access_gates.py -q`

- [ ] **Step 3: Carry the tier on `User`**

`auth.py`, in `class User` after `is_admin`:

```python
    #: The account's access tier (spec 2026-10-03). ``None`` for the
    #: loopback user; local mode never consults it.
    access_tier: str | None = None
    #: Filled by ``GET /api/me`` only, for the SPA.
    features: list[str] = []
```

`magic_link.py`:
- `__init__` gains `default_tier: str = "full"` stored as `self._default_tier`.
- In `complete_login`, the new-account branch: `UserRow(email=link_row.email, email_verified_at=now, access_tier=self._default_tier)`.
- Both `User(...)` constructions (end of `complete_login` and `authenticate_request`) add `access_tier=user_row.access_tier`.

`desktop_tokens.py`, `DesktopTokenAuth.authenticate_request`: add `access_tier=user_row.access_tier` to the returned `User`. The tier is read from the users row on every request, never stored on the token, which is what makes a downgrade immediate.

- [ ] **Step 4: The gate module**

```python
# src/splitsmith/ui/access_gate.py
"""Account-feature gate for hosted routes (spec 2026-10-03).

Routes declare ``Depends(require_feature(Feature.x))``. Local mode is a
no-op: there are no accounts and every feature is on. The job backend's
``submit_allowed`` predicate is the backstop; this dependency exists so
the common entry points answer with a clean, explainable error.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request

from ..access import ALL_FEATURES, Feature, features_for
from ..auth import User


def features_of(request: Request, user: User) -> frozenset[Feature]:
    from .server import _hosted_mode_active

    if not _hosted_mode_active():
        return ALL_FEATURES
    state = request.app.state.splitsmith_state
    return features_for(user.access_tier, user.email, state.access, state.admin_emails)


def require_feature(feature: Feature) -> Callable[[Request], Awaitable[None]]:
    async def _dependency(request: Request) -> None:
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        if feature not in features_of(request, user):
            raise HTTPException(
                status_code=403, detail={"code": "feature_required", "feature": feature.value}
            )

    return _dependency
```

- [ ] **Step 5: Wire it in `server.py`**

1. `AppState`: add `access: AccessConfig = field(default_factory=AccessConfig)` (match the dataclass/field idiom `AppState` already uses for `admin_emails`). Import `AccessConfig` at the top from `..access`.
2. Hosted bootstrap, right after `state.admin_emails = ...`: `state.access = access_config()`. Pass `default_tier=state.access.default_tier` to `MagicLinkAuth(...)`.
3. Add `dependencies=[Depends(require_feature(Feature.X))]` to the decorators:
   - `create_match`: `@app.post("/api/match/create-manual")`, `@app.post("/api/match/create-from-scoreboard")`, `@app.post("/api/me/projects/import")`
   - `raw_upload`: `@app.post("/api/me/raw/upload")` and the four `/api/me/raw/upload/multipart/*` posts
   - `hosted_compute`: `@app.post("/api/me/jobs/{job_id}/retry", ...)`
   - `share`: `@app.post("/api/match/shares", ...)`, `@app.patch("/api/match/shares/{share_id}/cameras", ...)`, `@app.delete("/api/match/shares/{share_id}", ...)`
4. `app.include_router(sync_router, dependencies=[Depends(require_feature(Feature.sync))])`. Check that `_hosted_gate`'s 404 still fires first in local mode (`require_feature` is a no-op locally, so it does).
5. `_auth_gate`, right after the user is resolved and stashed on `request.state.user`, before the token-scope check:

```python
        if _hosted_mode_active() and not features_of(request, user) and path not in _DISABLED_ALLOWED_PATHS:
            return JSONResponse(status_code=403, content={"detail": {"code": "account_disabled"}})
```

with, at module level next to `_PUBLIC_API_PATHS`:

```python
#: What an account with no features may still reach: who am I, and sign out.
_DISABLED_ALLOWED_PATHS: frozenset[str] = frozenset({"/api/me", "/api/v1/auth/logout"})
```

Use the variable name the gate already uses for the request path.
6. `GET /api/me` and `PATCH /api/me`: extend the `model_copy(update=...)` with `"features": sorted(f.value for f in features_of(request, user))`. Add `request: Request` to both signatures.

- [ ] **Step 6: Run tests**

Run: `uv run pytest -n0 tests/test_access_gates.py tests/test_auth_routes.py tests/test_mirror_read_only.py tests/test_sync_api.py -q`
Expected: PASS

- [ ] **Step 7: Mutation check**

Remove `dependencies=[...]` from `/api/match/create-manual` and confirm `test_sharing_tier_is_refused_with_the_feature_named[...create-manual...]` fails. Remove the `_auth_gate` disabled clause and confirm `test_disabled_account_keeps_me_and_logout_only` fails. Restore both.

- [ ] **Step 8: Full suite, then commit**

Run: `uv run pytest -q`

```bash
git add src/splitsmith/auth.py src/splitsmith/db/magic_link.py src/splitsmith/db/desktop_tokens.py src/splitsmith/ui/access_gate.py src/splitsmith/ui/server.py tests/test_access_gates.py
git commit -m "feat(hosted): gate hosted entry points on account features"
```

---

### Task 4: Job backend backstop

**Files:**
- Modify: `src/splitsmith/db/job_backend.py` (`PostgresJobBackend.__init__` ~line 113, `submit` ~line 196)
- Modify: `src/splitsmith/ui/server.py` (`_build_tenant` ~line 6957; `@app.exception_handler` block ~line 8169)
- Test: `tests/test_access_backstop.py`

**Interfaces:**
- Consumes: `FeatureRequiredError`, `Feature`, `features_for` (Task 1); `User.access_tier` (Task 2).
- Produces: `PostgresJobBackend(..., submit_allowed: Callable[[], Awaitable[bool]] | None = None)`; a 403 `feature_required` for any `FeatureRequiredError` escaping a handler.

- [ ] **Step 1: Failing tests**

```python
# tests/test_access_backstop.py
"""No job reaches the fleet for an account without hosted_compute."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from splitsmith.access import Feature, FeatureRequiredError
from splitsmith.db import ComputeJobRow, PostgresJobBackend
from tests.hosted_helpers import hosted_app, hosted_env, login  # noqa: F401
from tests.test_access_gates import set_tier


def test_every_registered_kind_is_refused(hosted_app, hosted_env) -> None:  # noqa: ANN001
    """Enumerates the live registry: a kind added later is covered with no edit."""
    from splitsmith.db import create_engine, sessionmaker

    client, _ = hosted_app
    bodies = client.app.state.splitsmith_state.job_bodies
    kinds = list(bodies.kinds())
    assert len(kinds) >= 10, kinds
    deferred: list[str] = []

    async def deferrer(**kw):  # noqa: ANN003, ANN202
        deferred.append(kw["kind"])

    async def never() -> bool:
        return False

    async def go() -> int:
        engine = create_engine(hosted_env)
        backend = PostgresJobBackend(
            sessionmaker(engine), user_id="u1", deferrer=deferrer, sweep_on_boot=False,
            bodies=bodies, submit_allowed=never,
        )
        for kind in kinds:
            with pytest.raises(FeatureRequiredError) as exc:
                await backend.submit(kind=kind)
            assert exc.value.feature is Feature.hosted_compute
        async with sessionmaker(engine)() as s:
            n = (await s.execute(select(func.count()).select_from(ComputeJobRow))).scalar_one()
        await engine.dispose()
        return n

    assert asyncio.run(go()) == 0
    assert deferred == []


def test_build_tenant_wires_the_backstop(hosted_app, hosted_env) -> None:  # noqa: ANN001
    """The real seam: a tenant built for a sharing user refuses submit."""
    client, sender = hosted_app
    login(client, sender, "friend@x.se")
    set_tier(hosted_env, "friend@x.se", "sharing")
    state = client.app.state.splitsmith_state
    user_id = client.get("/api/me").json()["id"]
    tenant = state._build_tenant(user_id)
    with pytest.raises(FeatureRequiredError):
        asyncio.run(tenant.jobs.submit(kind="trim"))


def test_full_tenant_is_not_refused_by_the_backstop(hosted_app) -> None:  # noqa: ANN001
    client, sender = hosted_app
    login(client, sender, "me@x.se")
    state = client.app.state.splitsmith_state
    tenant = state._build_tenant(client.get("/api/me").json()["id"])
    try:
        asyncio.run(tenant.jobs.submit(kind="trim"))
    except FeatureRequiredError:  # pragma: no cover - the failure this test exists for
        pytest.fail("a full account was refused by the backstop")
    except Exception:
        pass  # anything past the gate (deferrer, missing args) is not this test's concern
```

`bodies.kinds()`: check what `JobBodyRegistry` exposes for listing kinds. If it has no such method, add `def kinds(self) -> list[str]` returning the registered keys, in this task.

- [ ] **Step 2: Run, expect FAIL** (`TypeError: unexpected keyword argument 'submit_allowed'`)

Run: `uv run pytest -n0 tests/test_access_backstop.py -q`

- [ ] **Step 3: Implement the predicate**

`PostgresJobBackend.__init__` gains `submit_allowed: Callable[[], Awaitable[bool]] | None = None`, stored as `self._submit_allowed`, with a comment: "Account-feature backstop (spec 2026-10-03): when set, ``submit`` refuses unless it answers True. Hosted wiring always sets it; ``None`` is tests and the local registry."

In `submit`, directly after the `_shutting_down` check:

```python
        if self._submit_allowed is not None and not await self._submit_allowed():
            raise FeatureRequiredError(Feature.hosted_compute)
```

`retry` goes through `submit`, so it is covered with no edit.

- [ ] **Step 4: Wire `_build_tenant`**

Inside `_build_tenant(user_id)`, before `return TenantContext(...)`:

```python
        async def _may_submit() -> bool:
            # Read the tier per submit, not per tenant build: a downgrade
            # applies to the next job, and a job body that chains another
            # job (trim -> shot_detect) is checked again at that point.
            async with session_factory() as session:
                row = (
                    await session.execute(
                        select(UserRow.access_tier, UserRow.email).where(UserRow.id == user_id)
                    )
                ).one_or_none()
            if row is None:
                return False
            return Feature.hosted_compute in features_for(
                row.access_tier, row.email, state.access, state.admin_emails
            )
```

and pass `submit_allowed=_may_submit` to `PostgresJobBackend(...)`. `session_factory` is the raw (non-tenant) factory already in scope; `users` is not under RLS. Import `select` and `UserRow` the way the surrounding block imports db names (inside the function body).

- [ ] **Step 5: Exception handler**

Next to the `ShutdownInProgressError` handler:

```python
    @app.exception_handler(FeatureRequiredError)
    async def _feature_required_handler(_request: Request, exc: FeatureRequiredError) -> JSONResponse:
        return JSONResponse(
            status_code=403, content={"detail": {"code": "feature_required", "feature": exc.feature.value}}
        )
```

Add a test to `tests/test_access_backstop.py` that drives a route which submits a job: seed a hosted-origin match for a `full` user, downgrade to `sharing`, then `POST /api/matches/{id}/shooters/{slug}/stages/1/trim`. Expect 403 with the `feature_required` body. Reuse `seed_match` from `tests/hosted_helpers.py`. If the route needs more seeded state before it reaches `submit`, seed it; the assertion must be the backstop's body, not a 404 or 409 from earlier in the handler.

- [ ] **Step 6: Run, mutation check, full suite**

Run: `uv run pytest -n0 tests/test_access_backstop.py -q` (PASS). Then delete the two-line check in `submit`, rerun, and confirm `test_every_registered_kind_is_refused` and `test_build_tenant_wires_the_backstop` both fail. Restore. Then delete `submit_allowed=_may_submit` in `_build_tenant` and confirm `test_build_tenant_wires_the_backstop` fails. Restore. Run `uv run pytest -q`.

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/db/job_backend.py src/splitsmith/ui/server.py tests/test_access_backstop.py
git commit -m "feat(hosted): job submit refuses accounts without hosted compute"
```

---

### Task 5: Access-request and account stores

**Files:**
- Create: `src/splitsmith/db/access_requests.py`
- Modify: `src/splitsmith/db/__init__.py` (export both stores and the errors)
- Test: `tests/test_access_requests_store.py`

**Interfaces:**
- Consumes: `AccessRequest`, `User` models (Task 2).
- Produces:

```python
class AccessRequestView(BaseModel):  # Pydantic, crosses into the API
    id: str; email: str; note: str | None; source: str; status: str
    requested_at: datetime; last_requested_at: datetime
    decided_at: datetime | None; decided_by: str | None
    tier_granted: str | None; email_sent_at: datetime | None

class AccountView(BaseModel):
    id: str; email: str; display_name: str | None; access_tier: str; created_at: datetime

class AlreadyDecidedError(Exception): ...
class NotFoundError(Exception): ...

class AccessRequestStore:
    def __init__(self, session_factory: async_sessionmaker, *, now: Callable[[], datetime] = _utcnow) -> None
    async def record(self, email: str, *, source: str, note: str | None = None) -> bool  # True = newly pending
    async def list(self, status: str | None = None) -> list[AccessRequestView]
    async def approve(self, request_id: str, *, tier: str, admin_email: str) -> AccessRequestView
    async def decline(self, request_id: str, *, admin_email: str) -> AccessRequestView
    async def mark_email_sent(self, request_id: str) -> None
    async def get(self, request_id: str) -> AccessRequestView

class AccountAccessStore:
    def __init__(self, session_factory: async_sessionmaker) -> None
    async def list(self) -> list[AccountView]
    async def set_tier(self, user_id: str, tier: str) -> AccountView  # NotFoundError on unknown id
```

- [ ] **Step 1: Failing tests**

```python
# tests/test_access_requests_store.py
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from splitsmith.db import Base, User, create_engine, sessionmaker
from splitsmith.db.access_requests import (
    AccessRequestStore,
    AccountAccessStore,
    AlreadyDecidedError,
    NotFoundError,
)


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 3, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.t


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[object]:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")

    async def setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(setup())
    yield sessionmaker(engine)


def run(coro):  # noqa: ANN001, ANN202
    return asyncio.run(coro)


def test_record_creates_once_and_dedupes(factory) -> None:  # noqa: ANN001
    clock = Clock()
    store = AccessRequestStore(factory, now=clock)
    assert run(store.record("A@X.se ", source="login")) is True
    clock.t += timedelta(hours=1)
    assert run(store.record("a@x.se", source="form", note="Erik, Bromma")) is False
    [row] = run(store.list())
    assert row.email == "a@x.se"
    assert row.source == "login"  # the first source sticks
    assert row.note == "Erik, Bromma"  # an empty note is filled
    assert row.last_requested_at > row.requested_at


def test_note_is_not_overwritten_and_is_capped(factory) -> None:  # noqa: ANN001
    store = AccessRequestStore(factory, now=Clock())
    run(store.record("a@x.se", source="form", note="first"))
    run(store.record("a@x.se", source="form", note="second"))
    assert run(store.list())[0].note == "first"
    run(store.record("b@x.se", source="form", note="x" * 900))
    assert len(next(r for r in run(store.list()) if r.email == "b@x.se").note) == 500


def test_existing_account_records_nothing(factory) -> None:  # noqa: ANN001
    async def seed() -> None:
        async with factory() as s:
            s.add(User(email="me@x.se", access_tier="full"))
            await s.commit()

    run(seed())
    store = AccessRequestStore(factory, now=Clock())
    assert run(store.record("me@x.se", source="login")) is False
    assert run(store.list()) == []


def test_approve_creates_the_user_with_the_tier(factory) -> None:  # noqa: ANN001
    store = AccessRequestStore(factory, now=Clock())
    run(store.record("a@x.se", source="form"))
    rid = run(store.list())[0].id
    view = run(store.approve(rid, tier="sharing", admin_email="boss@x.se"))
    assert (view.status, view.tier_granted, view.decided_by) == ("approved", "sharing", "boss@x.se")

    async def tier() -> tuple[str, object]:
        async with factory() as s:
            row = (await s.execute(select(User).where(User.email == "a@x.se"))).scalar_one()
            return row.access_tier, row.email_verified_at

    assert run(tier()) == ("sharing", None)


def test_approve_existing_account_sets_tier(factory) -> None:  # noqa: ANN001
    store = AccessRequestStore(factory, now=Clock())
    run(store.record("a@x.se", source="form"))
    rid = run(store.list())[0].id

    async def seed() -> None:
        async with factory() as s:
            s.add(User(email="a@x.se", access_tier="full"))
            await s.commit()

    run(seed())  # they got in through the env allowlist meanwhile
    run(store.approve(rid, tier="sharing", admin_email="boss@x.se"))

    async def users() -> list[tuple[str, str]]:
        async with factory() as s:
            return [(u.email, u.access_tier) for u in (await s.execute(select(User))).scalars()]

    assert run(users()) == [("a@x.se", "sharing")]


def test_double_decide_is_refused(factory) -> None:  # noqa: ANN001
    store = AccessRequestStore(factory, now=Clock())
    run(store.record("a@x.se", source="form"))
    rid = run(store.list())[0].id
    run(store.approve(rid, tier="sharing", admin_email="boss@x.se"))
    with pytest.raises(AlreadyDecidedError):
        run(store.approve(rid, tier="full", admin_email="other@x.se"))
    with pytest.raises(AlreadyDecidedError):
        run(store.decline(rid, admin_email="other@x.se"))


def test_declined_stays_declined_on_repeat_but_can_be_approved(factory) -> None:  # noqa: ANN001
    store = AccessRequestStore(factory, now=Clock())
    run(store.record("a@x.se", source="form"))
    rid = run(store.list())[0].id
    run(store.decline(rid, admin_email="boss@x.se"))
    assert run(store.record("a@x.se", source="login")) is False
    assert run(store.list())[0].status == "declined"
    assert run(store.approve(rid, tier="sharing", admin_email="boss@x.se")).status == "approved"


def test_list_puts_pending_first(factory) -> None:  # noqa: ANN001
    clock = Clock()
    store = AccessRequestStore(factory, now=clock)
    run(store.record("old@x.se", source="form"))
    clock.t += timedelta(minutes=1)
    run(store.record("new@x.se", source="form"))
    run(store.decline(run(store.list())[-1].id, admin_email="boss@x.se"))
    assert [r.status for r in run(store.list())][0] == "pending"
    assert [r.email for r in run(store.list(status="declined"))] == ["old@x.se"]


def test_account_store_sets_tier_and_404s(factory) -> None:  # noqa: ANN001
    async def seed() -> str:
        async with factory() as s:
            u = User(email="a@x.se", access_tier="full")
            s.add(u)
            await s.commit()
            return u.id

    uid = run(seed())
    accounts = AccountAccessStore(factory)
    assert run(accounts.set_tier(uid, "sharing")).access_tier == "sharing"
    with pytest.raises(NotFoundError):
        run(accounts.set_tier("nope", "full"))
```

- [ ] **Step 2: Run, expect FAIL** (module missing)

Run: `uv run pytest -n0 tests/test_access_requests_store.py -q`

- [ ] **Step 3: Implement `db/access_requests.py`**

Rules the implementation must keep, each pinned by a test above:
- Emails normalised with `strip().lower()` at every entry point.
- `record`: if a `User` with the email exists, return False and write nothing. Otherwise select the request; if none, insert `pending` with `requested_at = last_requested_at = now`, wrapped in `session.begin_nested()` with `IntegrityError` caught (two concurrent first requests; the loser falls through to the bump path), and return True. If one exists: bump `last_requested_at`, fill `note` only when the stored note is null, never change `status` or `source`, return False.
- `note` is truncated to 500 characters before storing.
- `approve`: `NotFoundError` on an unknown id; `AlreadyDecidedError` when status is `approved`; allowed from `pending` and `declined`. The status flip uses a conditional `UPDATE ... WHERE id = :id AND status IN ('pending','declined')` and checks `rowcount`, the same race-safe pattern as `complete_login`'s token consumption, so two concurrent approves cannot both win. Then, in the same transaction, select the `User` by email: create it with `access_tier=tier` (email unverified) when missing (a `begin_nested` + `IntegrityError` re-select, as in `complete_login`), else set its `access_tier`.
- `decline`: allowed only from `pending` (conditional UPDATE), else `AlreadyDecidedError`.
- `list(status)`: pending first, then by `last_requested_at` descending.
- `AccountAccessStore.list`: excludes `deleted_at IS NOT NULL` rows, ordered by `created_at`.

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest -n0 tests/test_access_requests_store.py -q` (PASS)

```bash
git add src/splitsmith/db/access_requests.py src/splitsmith/db/__init__.py tests/test_access_requests_store.py
git commit -m "feat(db): access request and account access stores"
```

---

### Task 6: Intake: login, form route, admin alert, mail

**Files:**
- Modify: `src/splitsmith/db/email.py` (Protocol + both senders)
- Modify: `src/splitsmith/db/magic_link.py` (`mint_link`, `on_blocked`)
- Modify: `src/splitsmith/ui/server.py` (hosted bootstrap; new route after `_auth_begin` ~line 7618; `_PUBLIC_API_PATHS` ~line 1293; `AuthBeginRequest` neighbour for the new body model)
- Modify: `tests/hosted_helpers.py` (`_CapturingSender` gains the two methods)
- Test: `tests/test_access_intake.py`

**Interfaces:**
- Consumes: `AccessRequestStore.record` (Task 5).
- Produces:
  - `EmailSender.send_access_granted(*, to: str, link: str) -> None`
  - `EmailSender.send_access_request_alert(*, to: str, email: str, note: str | None, source: str, admin_url: str) -> None`
  - `MagicLinkAuth.mint_link(email: str, *, base_url: str) -> str`
  - `MagicLinkAuth(..., on_blocked: Callable[[str], Awaitable[None]] | None = None)`
  - `AppState.access_requests: AccessRequestStore | None`, `AppState.accounts: AccountAccessStore | None`, `AppState.email_sender`
  - `POST /api/v1/access-requests` with body `{email: str, note: str | None, hp: str | None}`: 202 `{"ok": true, "message": INTAKE_MESSAGE}`
  - `INTAKE_MESSAGE` constant in `server.py`, the verbatim copy from Global Constraints

- [ ] **Step 1: Extend the test sender**

```python
class _CapturingSender:
    def __init__(self) -> None:
        self.links: list[tuple[str, str]] = []
        self.granted: list[tuple[str, str]] = []
        self.alerts: list[dict[str, object]] = []

    async def send_magic_link(self, *, to: str, link: str) -> None:
        self.links.append((to, link))

    async def send_access_granted(self, *, to: str, link: str) -> None:
        self.granted.append((to, link))

    async def send_access_request_alert(self, **kw: object) -> None:
        self.alerts.append(kw)

    def last_token(self) -> str:
        return parse_qs(urlparse(self.links[-1][1]).query)["token"][0]
```

`hosted_app` must swap the sender everywhere the app holds one. After this task that means the magic-link backend's `_email` and `state.email_sender`. Update the fixture to set both.

- [ ] **Step 2: Failing tests**

```python
# tests/test_access_intake.py
"""Access-request intake: no existence leak, no mail to strangers (spec 2026-10-03)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from tests.hosted_helpers import _CapturingSender, login


@pytest.fixture
def closed_app(hosted_env: str, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    monkeypatch.setenv("SPLITSMITH_SIGNUPS_OPEN", "false")
    monkeypatch.setenv("SPLITSMITH_SIGNUP_ALLOWLIST", "me@x.se")
    monkeypatch.setenv("SPLITSMITH_ADMIN_EMAILS", "me@x.se")
    from splitsmith.ui.server import create_app

    app = create_app()
    sender = _CapturingSender()
    state = app.state.splitsmith_state
    state.auth.backends[0]._email = sender
    state.email_sender = sender
    with TestClient(app, follow_redirects=False) as client:
        yield client, sender, state


def requests_list(state) -> list:  # noqa: ANN001
    import asyncio

    return asyncio.run(state.access_requests.list())


def test_unknown_email_at_login_becomes_a_pending_request(closed_app) -> None:  # noqa: ANN001
    client, sender, state = closed_app
    assert client.post("/api/v1/auth/begin", json={"email": "Erik@x.se"}).status_code == 200
    [row] = requests_list(state)
    assert (row.email, row.source, row.status) == ("erik@x.se", "login", "pending")
    assert sender.links == []  # nothing to the requester
    assert [a["email"] for a in sender.alerts] == ["erik@x.se"]  # one alert to the admin


def test_repeat_request_does_not_realert(closed_app) -> None:  # noqa: ANN001
    client, sender, _ = closed_app
    client.post("/api/v1/auth/begin", json={"email": "erik@x.se"})
    client.post("/api/v1/access-requests", json={"email": "erik@x.se", "note": "Bromma"})
    assert len(sender.alerts) == 1


def test_responses_are_identical_for_every_email_state(closed_app) -> None:  # noqa: ANN001
    import asyncio

    client, sender, state = closed_app
    login(client, sender, "me@x.se")  # allowlisted: a real account
    client.cookies.clear()
    client.post("/api/v1/access-requests", json={"email": "pending@x.se"})
    client.post("/api/v1/access-requests", json={"email": "declined@x.se"})
    rid = next(r.id for r in requests_list(state) if r.email == "declined@x.se")
    asyncio.run(state.access_requests.decline(rid, admin_email="me@x.se"))
    emails = ["me@x.se", "pending@x.se", "declined@x.se", "brand-new@x.se"]
    begin = {client.post("/api/v1/auth/begin", json={"email": e}).content for e in emails}
    form = {client.post("/api/v1/access-requests", json={"email": e}).content for e in emails}
    assert len(begin) == 1, begin
    assert len(form) == 1, form


def test_form_route_rate_limit_still_answers_202(closed_app) -> None:  # noqa: ANN001
    client, _, state = closed_app
    bodies = {
        client.post("/api/v1/access-requests", json={"email": f"u{i}@x.se"}).content for i in range(8)
    }
    assert len(bodies) == 1
    assert len(requests_list(state)) == 5  # per-IP limit: 5 an hour


def test_honeypot_records_nothing(closed_app) -> None:  # noqa: ANN001
    client, _, state = closed_app
    resp = client.post("/api/v1/access-requests", json={"email": "bot@x.se", "hp": "filled"})
    assert resp.status_code == 202
    assert requests_list(state) == []


def test_malformed_email_is_400(closed_app) -> None:  # noqa: ANN001
    client, _, _ = closed_app
    assert client.post("/api/v1/access-requests", json={"email": "nope"}).status_code == 400


def test_cors_preflight_only_for_the_marketing_origin(closed_app) -> None:  # noqa: ANN001
    client, _, _ = closed_app
    ok = client.options(
        "/api/v1/access-requests",
        headers={"Origin": "https://splitsmith.app", "Access-Control-Request-Method": "POST"},
    )
    assert ok.headers.get("access-control-allow-origin") == "https://splitsmith.app"
    other = client.options(
        "/api/v1/access-requests",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in other.headers


def test_route_404s_in_local_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPLITSMITH_MODE", raising=False)
    from splitsmith.ui.server import create_app

    with TestClient(create_app()) as client:
        assert client.post("/api/v1/access-requests", json={"email": "a@x.se"}).status_code == 404


def test_alert_failure_never_fails_the_request(closed_app) -> None:  # noqa: ANN001
    client, sender, state = closed_app

    async def boom(**_kw: object) -> None:
        raise RuntimeError("provider down")

    sender.send_access_request_alert = boom
    resp = client.post("/api/v1/access-requests", json={"email": "a@x.se"})
    assert resp.status_code == 202
    assert len(requests_list(state)) == 1
```

The local-mode test needs a local app: copy the setup that an existing local-mode server test uses (grep `create_app(` in `tests/test_auth_routes.py` for a local case) rather than relying on `delenv` alone.

- [ ] **Step 3: Run, expect FAIL**

Run: `uv run pytest -n0 tests/test_access_intake.py -q`

- [ ] **Step 4: Mail**

`email.py`: add both methods to the `EmailSender` Protocol, with docstrings. `send_access_granted`: "You're in: one sign-in link, same validity line as the magic link." `send_access_request_alert`: "To an admin; never to the requester." Then:
- `ConsoleEmailSender`: log `ACCESS_GRANTED <to> <link>` and `ACCESS_REQUEST <to> <email> <source>` at INFO.
- `LettermintEmailSender`: factor the existing POST into `async def _send(self, *, to: str, subject: str, text: str, html: str) -> None` and reuse it in all three methods. Subjects: "You have access to Splitsmith" and "Splitsmith access request: <email>". Bodies are plain like `_magic_link_email_body`. Every interpolated user string (`email`, `note`) goes through `html.escape` in the HTML part.

- [ ] **Step 5: `mint_link` and `on_blocked`**

In `magic_link.py`, move the token-minting half of `begin_login` (create `MagicLinkTokenRow`, build the URL) into:

```python
    async def mint_link(self, email: str, *, base_url: str) -> str:
        """Mint a single-use sign-in link for ``email`` without sending it.
        ``begin_login`` sends it as a sign-in mail; an access approval sends
        it as a "you're in" mail."""
```

`begin_login` then calls `mint_link` and `send_magic_link`, and returns the challenge as before. Its `LoginChallenge` id must still come from the row, so `mint_link` either returns `(link, row_id)` internally through a private helper, or `begin_login` keeps its own path. Pick whichever keeps `begin_login`'s observable behaviour identical; the existing `tests/test_auth_routes.py` and `tests/test_magic_link*.py` must pass unchanged.

The blocked branch becomes:

```python
        if not self._signup_policy.allows_signup(normalized) and not await self._email_has_account(
            normalized
        ):
            if self._on_blocked is not None:
                try:
                    await self._on_blocked(normalized)
                except Exception:
                    logger.exception("recording an access request failed")
            return LoginChallenge(id="blocked", email=normalized, expires_at=now + MAGIC_LINK_TTL)
```

- [ ] **Step 6: Server wiring and the route**

Hosted bootstrap, after `signup_policy = ...`:

```python
    from ..db.access_requests import AccessRequestStore, AccountAccessStore

    state.email_sender = email_sender
    state.access_requests = AccessRequestStore(session_factory)
    state.accounts = AccountAccessStore(session_factory)

    async def _record_and_alert(email: str, *, source: str = "login", note: str | None = None) -> None:
        if not await state.access_requests.record(email, source=source, note=note):
            return
        for admin in sorted(state.admin_emails):
            try:
                await state.email_sender.send_access_request_alert(
                    to=admin, email=email, note=note, source=source,
                    admin_url=f"{state.public_base_url.rstrip('/')}/admin/access",
                )
            except Exception:
                logger.exception("access request alert to an admin failed")

    state.record_access_request = _record_and_alert
```

Pass `on_blocked=lambda email: state.record_access_request(email)` to `MagicLinkAuth(...)`. Read `state.email_sender` inside the closure, not a captured local, so the test fixture's swap applies. Add the matching `AppState` fields with `None` defaults.

Route, next to `_auth_begin`:

```python
INTAKE_MESSAGE = "If you have access, a sign-in link is on its way. Otherwise your request has been noted."
_intake_ip_limiter = CommentRateLimiter(limit=5, window_s=3600.0)
_intake_global_limiter = CommentRateLimiter(limit=50, window_s=3600.0)


def _intake_origins() -> frozenset[str]:
    raw = os.environ.get("SPLITSMITH_ACCESS_REQUEST_ORIGINS", "https://splitsmith.app,https://www.splitsmith.app")
    return frozenset(o.strip() for o in raw.split(",") if o.strip())
```

```python
    def _intake_cors(request: Request, response: Response) -> Response:
        origin = request.headers.get("origin")
        if origin and origin in _intake_origins():
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "content-type"
            response.headers["Vary"] = "Origin"
        return response

    @app.options("/api/v1/access-requests")
    async def _access_request_preflight(request: Request) -> Response:
        if not _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        return _intake_cors(request, Response(status_code=204))

    @app.post("/api/v1/access-requests", status_code=202)
    async def _access_request(payload: AccessRequestBody, request: Request) -> Response:
        """Ask for a hosted account. Always the same 202 whatever the email's
        state, so the route cannot tell anyone which emails have accounts."""
        if not _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        email = payload.email.strip()
        if not email or "@" not in email:
            raise HTTPException(status_code=400, detail="a valid email is required")
        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        if not payload.hp and _intake_ip_limiter.allow(f"ip:{ip}", now=now) and _intake_global_limiter.allow(
            "global", now=now
        ):
            await state.record_access_request(email, source=payload.source, note=payload.note)
        return _intake_cors(request, JSONResponse({"ok": True, "message": INTAKE_MESSAGE}, status_code=202))
```

with

```python
class AccessRequestBody(BaseModel):
    email: str
    note: str | None = None
    hp: str | None = None  # the marketing form's honeypot
    source: Literal["form", "waitlist"] = "form"
```

Add `"/api/v1/access-requests"` to `_PUBLIC_API_PATHS` with a comment ("asking for an account happens before one exists"). Check whether `request.client.host` behind Railway is the client or the proxy: grep `proxy_headers` / `forwarded_allow_ips` in `cli.py`'s `uvicorn.run` call. If it is the proxy, every caller shares one key, and the global limiter is the bound that still holds. Say so in a comment and leave it. Do not trust `X-Forwarded-For` by hand.

Make `/api/v1/auth/begin`'s body identical for the new rows too: it already returns `{"ok": true}` for all; keep it.

- [ ] **Step 7: Run tests, mutation check, full suite**

Run: `uv run pytest -n0 tests/test_access_intake.py tests/test_auth_routes.py -q` (PASS). Mutation check: make the route return a different body when `record` returns False, and confirm `test_responses_are_identical_for_every_email_state` fails. Restore. Then run `uv run pytest -q`.

- [ ] **Step 8: Commit**

```bash
git add src/splitsmith/db/email.py src/splitsmith/db/magic_link.py src/splitsmith/ui/server.py tests/hosted_helpers.py tests/test_access_intake.py
git commit -m "feat(hosted): access requests from sign-in and a request form"
```

---

### Task 7: Admin API

**Files:**
- Create: `src/splitsmith/ui/admin_access_api.py`
- Modify: `src/splitsmith/ui/server.py` (`include_router` block ~line 17594)
- Test: `tests/test_admin_access_routes.py`

**Interfaces:**
- Consumes: `AccessRequestStore`, `AccountAccessStore`, `AlreadyDecidedError`, `NotFoundError` (Task 5); `MagicLinkAuth.mint_link`, `state.email_sender` (Task 6); `state.access` (Task 3).
- Produces the routes from the spec's Admin API table. Response models:
  - `AccessRequestView` (Task 5) for request routes
  - `AdminAccountView(AccountView)` with `is_admin: bool`, for user routes
  - `TiersResponse {tiers: [{name: str, features: list[str]}], default_tier: str}`

- [ ] **Step 1: Failing tests**

```python
# tests/test_admin_access_routes.py
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from tests.hosted_helpers import _CapturingSender, login


@pytest.fixture
def admin_app(hosted_env: str, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    monkeypatch.setenv("SPLITSMITH_ADMIN_EMAILS", "boss@x.se")
    from splitsmith.ui.server import create_app

    app = create_app()
    sender = _CapturingSender()
    state = app.state.splitsmith_state
    state.auth.backends[0]._email = sender
    state.email_sender = sender
    with TestClient(app, follow_redirects=False) as client:
        yield client, sender, state


def seed_request(state, email: str) -> str:  # noqa: ANN001
    asyncio.run(state.access_requests.record(email, source="form"))
    return next(r.id for r in asyncio.run(state.access_requests.list()) if r.email == email)


def test_non_admin_is_403(admin_app) -> None:  # noqa: ANN001
    client, sender, _ = admin_app
    login(client, sender, "friend@x.se")
    assert client.get("/api/admin/access-requests").status_code == 403
    assert client.get("/api/admin/users").status_code == 403


def test_approve_sends_a_working_link_with_the_tier(admin_app) -> None:  # noqa: ANN001
    client, sender, state = admin_app
    rid = seed_request(state, "erik@x.se")
    login(client, sender, "boss@x.se")
    resp = client.post(f"/api/admin/access-requests/{rid}/approve", json={"tier": "sharing"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["email_sent_at"] is not None
    [(to, link)] = sender.granted
    assert to == "erik@x.se"
    client.cookies.clear()
    token = link.split("token=")[1]
    assert client.get("/auth/callback", params={"token": token}).status_code == 303
    me = client.get("/api/me").json()
    assert (me["email"], me["access_tier"]) == ("erik@x.se", "sharing")


def test_second_decide_is_409(admin_app) -> None:  # noqa: ANN001
    client, sender, state = admin_app
    rid = seed_request(state, "erik@x.se")
    login(client, sender, "boss@x.se")
    client.post(f"/api/admin/access-requests/{rid}/decline")
    assert client.post(f"/api/admin/access-requests/{rid}/decline").status_code == 409


def test_unknown_tier_is_422(admin_app) -> None:  # noqa: ANN001
    client, sender, state = admin_app
    rid = seed_request(state, "erik@x.se")
    login(client, sender, "boss@x.se")
    assert client.post(f"/api/admin/access-requests/{rid}/approve", json={"tier": "gold"}).status_code == 422
    uid = client.get("/api/admin/users").json()[0]["id"]
    assert client.patch(f"/api/admin/users/{uid}", json={"access_tier": "gold"}).status_code == 422


def test_mail_failure_keeps_the_approval_and_resend_works(admin_app) -> None:  # noqa: ANN001
    client, sender, state = admin_app
    rid = seed_request(state, "erik@x.se")
    login(client, sender, "boss@x.se")
    original = sender.send_access_granted

    async def boom(**_kw: object) -> None:
        raise RuntimeError("provider down")

    sender.send_access_granted = boom
    resp = client.post(f"/api/admin/access-requests/{rid}/approve", json={"tier": "sharing"})
    assert resp.status_code == 200
    assert (resp.json()["status"], resp.json()["email_sent_at"]) == ("approved", None)
    sender.send_access_granted = original
    again = client.post(f"/api/admin/access-requests/{rid}/resend")
    assert again.status_code == 200 and again.json()["email_sent_at"] is not None
    assert len(sender.granted) == 1


def test_resend_on_a_pending_request_is_409(admin_app) -> None:  # noqa: ANN001
    client, sender, state = admin_app
    rid = seed_request(state, "erik@x.se")
    login(client, sender, "boss@x.se")
    assert client.post(f"/api/admin/access-requests/{rid}/resend").status_code == 409


def test_users_list_and_tier_change(admin_app) -> None:  # noqa: ANN001
    client, sender, _ = admin_app
    login(client, sender, "friend@x.se")
    client.cookies.clear()
    login(client, sender, "boss@x.se")
    users = {u["email"]: u for u in client.get("/api/admin/users").json()}
    assert users["boss@x.se"]["is_admin"] is True
    fid = users["friend@x.se"]["id"]
    assert client.patch(f"/api/admin/users/{fid}", json={"access_tier": "sharing"}).json()["access_tier"] == "sharing"
    assert client.patch("/api/admin/users/nope", json={"access_tier": "full"}).status_code == 404


def test_tiers_endpoint(admin_app) -> None:  # noqa: ANN001
    client, sender, _ = admin_app
    login(client, sender, "boss@x.se")
    body = client.get("/api/admin/access-tiers").json()
    assert body["default_tier"] == "full"
    assert {t["name"] for t in body["tiers"]} == {"full", "sharing", "disabled"}
```

Add a local-mode test asserting all seven routes 404, following the local-app setup from Task 6.

- [ ] **Step 2: Run, expect FAIL**

Run: `uv run pytest -n0 tests/test_admin_access_routes.py -q`

- [ ] **Step 3: Implement the router**

Shape it like `sync_api.py`:
- `router = APIRouter(prefix="/api/admin", dependencies=[Depends(_admin_gate)])`, where `_admin_gate` is 404 outside hosted mode and 403 unless `request.state.user.email.lower() in state.admin_emails`.
- db imports go inside functions, so the slim local install still imports the module (`tests/test_local_mode_no_hosted_imports.py` and `scripts/ci/assert_slim_import_surface.py` must stay green).
- The tier body models validate against the registry: a tier not in `state.access.tiers` raises `HTTPException(422)`.
- Errors map: `AlreadyDecidedError` -> 409, `NotFoundError` -> 404.
- Approve and resend share one helper:

```python
async def _send_granted(state, view: AccessRequestView) -> AccessRequestView:  # noqa: ANN001
    try:
        link = await state.auth.backends[0].mint_link(view.email, base_url=state.public_base_url)
        await state.email_sender.send_access_granted(to=view.email, link=link)
    except Exception:
        logger.exception("access granted mail failed")
        return await state.access_requests.get(view.id)
    await state.access_requests.mark_email_sent(view.id)
    return await state.access_requests.get(view.id)
```

- Resend is 409 unless status is `approved`.

Register it in `server.py` beside `sync_router`, with the same lazy-import comment idiom.

- [ ] **Step 4: Run, full suite, commit**

Run: `uv run pytest -n0 tests/test_admin_access_routes.py tests/test_local_mode_no_hosted_imports.py -q`, then `uv run python scripts/ci/assert_slim_import_surface.py` if it runs standalone (check its header for how CI invokes it), then `uv run pytest -q`.

```bash
git add src/splitsmith/ui/admin_access_api.py src/splitsmith/ui/server.py tests/test_admin_access_routes.py
git commit -m "feat(hosted): admin API for access requests and account tiers"
```

---

### Task 8: SPA: features on `Me`, gating, refusal line, request form

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (`AuthUser` ~line 1867; `authBegin` neighbour ~line 3449; admin calls next to `adminListWorkers` ~line 4603)
- Create: `src/splitsmith/ui_static/src/lib/access.ts`, `src/splitsmith/ui_static/src/lib/access.test.ts`
- Modify: `src/splitsmith/ui_static/src/pages/Pick.tsx` (New match / Import, ~line 255 and ~line 333; empty state)
- Modify: `src/splitsmith/ui_static/src/pages/Ingest.tsx` (the `HostedUploadModal` entry point, ~line 925)
- Modify: `src/splitsmith/ui_static/src/pages/Login.tsx`
- Test: the vitest files above, plus `Login.test.tsx` and `Pick.test.tsx` where they exist (extend; create if absent)

**Interfaces:**
- Consumes: `/api/me` `access_tier` + `features`; `POST /api/v1/access-requests`.
- Produces:

```ts
// lib/access.ts
export type Feature = "sync" | "share" | "create_match" | "raw_upload" | "hosted_compute";
export function can(user: AuthUser | null | undefined, feature: Feature): boolean;
/** One muted line for a refusal, or null when ``err`` is not one. */
export function featureRefusal(err: unknown): string | null;
```

- [ ] **Step 1: Failing vitest**

```ts
// lib/access.test.ts
import { describe, expect, it } from "vitest";

import { ApiError, type AuthUser } from "@/lib/api";
import { can, featureRefusal } from "@/lib/access";

const user = (features: string[], id = "u1"): AuthUser => ({
  id,
  email: "a@x.se",
  display_name: null,
  is_admin: false,
  access_tier: "sharing",
  features,
});

describe("can", () => {
  it("reads the feature list, never the tier name", () => {
    const u = { ...user(["sync", "share"]), access_tier: "full" };
    expect(can(u, "create_match")).toBe(false);
    expect(can(u, "share")).toBe(true);
  });
  it("local mode (loopback user) can do everything", () => {
    expect(can(user([], "local"), "create_match")).toBe(true);
  });
  it("no user, no feature", () => {
    expect(can(null, "sync")).toBe(false);
  });
});

describe("featureRefusal", () => {
  it("names what is not included", () => {
    const err = new ApiError(403, "forbidden", { detail: { code: "feature_required", feature: "raw_upload" } });
    expect(featureRefusal(err)).toBe("Uploading footage here is not included in this account's access.");
  });
  it("disabled account", () => {
    const err = new ApiError(403, "forbidden", { detail: { code: "account_disabled" } });
    expect(featureRefusal(err)).toBe("This account is disabled.");
  });
  it("anything else is not a refusal", () => {
    expect(featureRefusal(new ApiError(403, "forbidden", { detail: "read_only_mirror" }))).toBeNull();
    expect(featureRefusal(new Error("x"))).toBeNull();
  });
});
```

`ApiError` is currently not exported (`class ApiError` at api.ts ~2447). Export it. Check first how `request()` fills `body` on an error response; the test's `body` shape must match what the real `request()` stores. Adjust the test fixtures to that shape if it differs.

- [ ] **Step 2: Run, expect FAIL**

Run (from `src/splitsmith/ui_static`): `corepack pnpm vitest run src/lib/access.test.ts`

- [ ] **Step 3: Implement**

```ts
// lib/access.ts
/**
 * Account features (spec 2026-10-03). Pages ask ``can(user, feature)``;
 * nothing in the SPA compares tier names. Local mode has no accounts, so
 * the loopback user can do everything.
 */
import { ApiError, type AuthUser } from "@/lib/api";

export type Feature = "sync" | "share" | "create_match" | "raw_upload" | "hosted_compute";

const LOOPBACK_ID = "local";

export function can(user: AuthUser | null | undefined, feature: Feature): boolean {
  if (!user) return false;
  if (user.id === LOOPBACK_ID) return true;
  return user.features.includes(feature);
}

const WHAT: Record<Feature, string> = {
  sync: "Syncing from the desktop app",
  share: "Sharing",
  create_match: "Creating matches here",
  raw_upload: "Uploading footage here",
  hosted_compute: "Running detection and renders here",
};

export function featureRefusal(err: unknown): string | null {
  if (!(err instanceof ApiError) || err.status !== 403) return null;
  const detail = (err.body as { detail?: unknown } | null)?.detail;
  if (!detail || typeof detail !== "object") return null;
  const { code, feature } = detail as { code?: string; feature?: string };
  if (code === "account_disabled") return "This account is disabled.";
  if (code === "feature_required" && feature && feature in WHAT) {
    return `${WHAT[feature as Feature]} is not included in this account's access.`;
  }
  return null;
}
```

`api.ts`:
- `AuthUser` gains `access_tier: string | null;` and `features: string[];`.
- Add `requestAccess: (email: string, note: string | null) => request<{ ok: boolean; message: string }>("/api/v1/access-requests", { method: "POST", body: JSON.stringify({ email, note }) })`, following the `authBegin` call's exact options shape.
- Fix any existing `AuthUser` literal in tests that now fails typecheck by adding the two fields.

- [ ] **Step 4: Gate the controls**

- `Pick.tsx`: read the user from `useAuth()`. Render "New match" and the Import control only when `can(user, "create_match")`. When the match list is empty and the user cannot create, the empty state is one line, "Matches arrive here from the desktop app.", with a link "Get the desktop app" to `https://splitsmith.app/#download` (check `site/index.html` for the actual install anchor id and use it). If `continueMatch` drove `variant="primary"` on New match, make sure the view still has exactly one primary.
- `Ingest.tsx`: render the hosted upload entry point only when `can(user, "raw_upload")`.
- Where these pages already catch errors from create, import or upload calls, put `featureRefusal(err)` first and show its string as the muted line the page already uses for errors (`text-sm text-muted` or the page's existing error line component). Never a toast.

- [ ] **Step 5: Request-access form on Login**

In `Login.tsx`, below the sign-in form, a text button "Request access" toggles a second small form: email (prefilled from the sign-in field), an optional textarea "Who are you? (name, club)" with `maxLength={500}`, and a submit. On success, both forms are replaced by the server's `message` string, the same text whatever happened. On a network failure, show "Could not send the request. Check your connection and retry." Keep the file's existing structure; the eslint-disable header stays, since this file is grandfathered.

Vitest for Login (extend or create `pages/Login.test.tsx`, mocking `api.requestAccess`):
- submitting calls `requestAccess` with the trimmed email and note
- the success text is the server's `message`
- a rejected call shows the network-failure line

- [ ] **Step 6: Run SPA checks**

From `src/splitsmith/ui_static`: `corepack pnpm vitest run`, `corepack pnpm tsc --noEmit` (or the repo's typecheck script; see `package.json`), `corepack pnpm lint`. All green.

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/ui_static/src
git commit -m "feat(spa): account features gate match creation and uploads; request access from sign-in"
```

---

### Task 9: SPA: `/admin/access`

**Files:**
- Create: `src/splitsmith/ui_static/src/lib/adminAccess.ts`, `lib/adminAccess.test.ts`, `pages/AdminAccess.tsx`, `pages/AdminAccess.test.tsx`
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (admin calls and types)
- Modify: `src/splitsmith/ui_static/src/App.tsx` (route beside `admin/workers`, ~line 275)
- Modify: `src/splitsmith/ui_static/src/components/AccountChip.tsx` (~line 76, link beside Workers)

**Interfaces:**
- Consumes: the Task 7 routes.
- Produces:

```ts
// api.ts
export interface AccessRequest {
  id: string; email: string; note: string | null; source: string;
  status: "pending" | "approved" | "declined";
  requested_at: string; last_requested_at: string;
  decided_at: string | null; decided_by: string | null;
  tier_granted: string | null; email_sent_at: string | null;
}
export interface AdminAccount {
  id: string; email: string; display_name: string | null;
  access_tier: string; created_at: string; is_admin: boolean;
}
export interface AccessTiers { tiers: { name: string; features: string[] }[]; default_tier: string }
// api.adminAccessRequests(), adminApproveAccessRequest(id, tier), adminDeclineAccessRequest(id),
// adminResendAccessRequest(id), adminUsers(), adminSetUserTier(id, tier), adminAccessTiers()

// lib/adminAccess.ts
export interface RequestRow {
  id: string; email: string; note: string | null; sourceLabel: string; age: string;
  pending: boolean; mailFailed: boolean;
}
export function requestRows(rows: AccessRequest[], now: Date): { pending: RequestRow[]; decided: RequestRow[] };
export function approveTierDefault(tiers: AccessTiers): string;  // "sharing" when it exists, else default_tier
```

- [ ] **Step 1: Failing vitest for the derivation**

```ts
// lib/adminAccess.test.ts
import { describe, expect, it } from "vitest";

import type { AccessRequest } from "@/lib/api";
import { approveTierDefault, requestRows } from "@/lib/adminAccess";

const base: AccessRequest = {
  id: "r1", email: "a@x.se", note: null, source: "login", status: "pending",
  requested_at: "2026-10-01T12:00:00Z", last_requested_at: "2026-10-03T10:00:00Z",
  decided_at: null, decided_by: null, tier_granted: null, email_sent_at: null,
};
const now = new Date("2026-10-03T12:00:00Z");

describe("requestRows", () => {
  it("splits pending from decided and words the source", () => {
    const { pending, decided } = requestRows(
      [base, { ...base, id: "r2", source: "waitlist", status: "declined" }],
      now,
    );
    expect(pending.map((r) => r.id)).toEqual(["r1"]);
    expect(pending[0].sourceLabel).toBe("Sign-in");
    expect(decided[0].sourceLabel).toBe("Waitlist");
  });
  it("age counts from the last request", () => {
    expect(requestRows([base], now).pending[0].age).toBe("2 h");
  });
  it("flags an approval whose mail never went out", () => {
    const row = { ...base, status: "approved" as const, email_sent_at: null };
    expect(requestRows([row], now).decided[0].mailFailed).toBe(true);
    expect(requestRows([{ ...row, email_sent_at: "2026-10-03T11:00:00Z" }], now).decided[0].mailFailed).toBe(false);
  });
});

describe("approveTierDefault", () => {
  it("prefers sharing", () => {
    expect(approveTierDefault({ tiers: [{ name: "full", features: [] }, { name: "sharing", features: [] }], default_tier: "full" })).toBe("sharing");
  });
  it("falls back to the registry default", () => {
    expect(approveTierDefault({ tiers: [{ name: "full", features: [] }], default_tier: "full" })).toBe("full");
  });
});
```

Source labels: `login` "Sign-in", `form` "Form", `waitlist` "Waitlist", `import` "Waitlist import". Age: under 1 h "N min", under 48 h "N h", else "N d". Use `numeral` for the number where it is rendered.

- [ ] **Step 2: Run, expect FAIL; implement `lib/adminAccess.ts`; run, PASS**

Run: `corepack pnpm vitest run src/lib/adminAccess.test.ts`

- [ ] **Step 3: The page**

`pages/AdminAccess.tsx`, modelled on `pages/AdminWorkers.tsx` for the admin check, loading and error states, but on the primitives only:
- `PageHeader` title "Access".
- Requests section (`Label` "Requests"): a `Table` of pending rows with email, note (muted, truncated to one line with `title` holding the full note), source, age, a `Segmented` tier picker (options from `adminAccessTiers`, initial `approveTierDefault`), an Approve `Button` (`default` variant), and a Decline `Button` (`destructive`, which is an outline). No `primary` button in the table: the view's one primary is none, which is fine.
- Decided requests in a folded group (the same folding idiom the Export page uses, see its `openGroups` test helper), showing status, tier, decided by, and when `mailFailed` a `Chip` "Email not sent" plus a Resend button.
- Users section (`Label` "Users"): `Table` of email, display name, tier, created; a row `Menu` with "Set tier" entries per tier. An admin's row shows a `Chip` "Admin" and a muted note "tier has no effect".
- After any action, reload both lists. A 409 shows the muted line "Someone else already decided this request." and reloads.
- Empty pending list: one muted line, "No pending requests."

`App.tsx`: `<Route path="admin/access" element={<AdminAccess />} />` beside `admin/workers`. `AccountChip.tsx`: an "Access" link beside the Workers link inside the same `user.is_admin` block.

- [ ] **Step 4: Page vitest**

`pages/AdminAccess.test.tsx`, mocking the api module the way `AdminWorkers.test.tsx` does:
- a non-admin sees no tables
- approving calls `adminApproveAccessRequest(id, "sharing")` and reloads
- a 409 shows the "Someone else" line
- `mailFailed` rows show "Email not sent" and Resend calls `adminResendAccessRequest`

- [ ] **Step 5: Lint, typecheck, vitest, commit**

From `src/splitsmith/ui_static`: `corepack pnpm vitest run && corepack pnpm lint && corepack pnpm tsc --noEmit`

```bash
git add src/splitsmith/ui_static/src
git commit -m "feat(spa): admin page for access requests and account tiers"
```

---

### Task 10: One list: the marketing waitlist

**Files:**
- Modify: `site/index.html` (form handler ~line 450)
- Delete: `functions/api/waitlist.js`. Keep the `WAITLIST` KV binding in `wrangler.toml` until the import has run; Step 4 removes it.
- Modify: `package.json` (`waitlist:list` / `waitlist:get` scripts stay until the import has run)
- Create: `scripts/import_waitlist.py`
- Test: `tests/test_import_waitlist.py`

**Interfaces:**
- Consumes: `AccessRequestStore` (Task 5). The import needs `requested_at` kept, so add `async def import_entry(self, email: str, *, requested_at: datetime, source: str = "import") -> bool` to the store in this task. It inserts a `pending` row with both timestamps set to `requested_at` when neither a request nor an account exists, and returns whether it inserted.
- Produces: `scripts/import_waitlist.py <dump.json>` where the dump is a JSON list of `{"email": str, "ts": int | str}` objects (the KV value's `ts` plus the key's email). Run against `SPLITSMITH_DATABASE_URL`.

- [ ] **Step 1: Failing test**

```python
# tests/test_import_waitlist.py
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from sqlalchemy import select

from splitsmith.db import AccessRequest, Base, User, create_engine, sessionmaker


def test_import_keeps_timestamps_and_skips_known(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}"
    engine = create_engine(url)

    async def setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sessionmaker(engine)() as s:
            s.add(User(email="me@x.se", access_tier="full"))
            await s.commit()

    asyncio.run(setup())
    dump = tmp_path / "dump.json"
    dump.write_text(json.dumps([
        {"email": "Erik@x.se", "ts": 1759000000000},
        {"email": "me@x.se", "ts": 1759000000000},
        {"email": "erik@x.se", "ts": 1759100000000},
    ]))
    out = subprocess.run(
        [sys.executable, "scripts/import_waitlist.py", str(dump)],
        env={"SPLITSMITH_DATABASE_URL": url, "PATH": ""}, capture_output=True, text=True, check=True,
    )
    assert "imported 1, skipped 2" in out.stdout

    async def rows() -> list[tuple[str, str, int]]:
        async with sessionmaker(engine)() as s:
            return [
                (r.email, r.source, int(r.requested_at.timestamp()))
                for r in (await s.execute(select(AccessRequest))).scalars()
            ]

    assert asyncio.run(rows()) == [("erik@x.se", "import", 1759000000)]
```

Before writing the script, check what `ts` is in `functions/api/waitlist.js` (milliseconds from `Date.now()`, or an ISO string) and accept both.

- [ ] **Step 2: Run, FAIL; implement the script and `import_entry`; run, PASS**

The script: argparse with one positional path; reads `SPLITSMITH_DATABASE_URL`; normalises emails; imports the earliest `ts` per email; prints `imported N, skipped M`. The module docstring gives the exact dump command, so the operator can produce the file:

```
npx wrangler kv key list --binding WAITLIST --remote > keys.json
# then, per key, `wrangler kv key get` -- or the package.json waitlist:list script's output
```

Read the existing `waitlist:list` / `waitlist:get` scripts in `package.json`. If they already produce email+ts JSON, document that one command instead.

- [ ] **Step 3: Point the site form at the app**

In `site/index.html`, the handler posts to `https://my.splitsmith.app/api/v1/access-requests` with `JSON.stringify({ email, hp, source: "waitlist" })` and `mode: "cors"`. On `res.ok` (202), show the server's `message` and set the button text to "Requested". Drop the `data.already` branch: the app never says. Keep the existing 429 and invalid-email branches, mapped to the app's 400. Delete `functions/api/waitlist.js`.

- [ ] **Step 4: Rollout note in the PR body (not code)**

The KV binding and the `waitlist:*` scripts are removed in a follow-up commit after the import has been run against production. Say so in the PR description with the exact commands.

- [ ] **Step 5: Commit**

```bash
git add site/index.html functions/api/waitlist.js scripts/import_waitlist.py src/splitsmith/db/access_requests.py tests/test_import_waitlist.py
git commit -m "feat(site): the waitlist form files an access request"
```

---

### Task 11: Docs, visual check, whole-branch review

**Files:**
- Modify: `CLAUDE.md` (new section "Hosted access tiers (spec 2026-10-03)")
- Modify: `docs/saas-readiness/11-environment-strategy.md` (the env table: `SPLITSMITH_ACCESS_REQUEST_ORIGINS`; the `access.default_tier` config value per environment)
- Modify: `docs/superpowers/specs/2026-10-03-hosted-access-tiers-design.md` (Status line)

- [ ] **Step 1: CLAUDE.md section**

At most 25 lines, in the file's existing style. It covers:
- **Features vs. capabilities:** account features versus match capabilities.
- **Code checks features:** never tier names.
- **The backstop:** `submit_allowed` in `PostgresJobBackend`, wired in `_build_tenant`, and that a new job kind needs no edit to be covered.
- **Gating a new route:** a new hosted entry point that creates matches, uploads or submits declares `require_feature`.
- **Intake:** the response must stay identical across email states, and nothing is ever mailed to a requester.
- **Admin routes:** they live in `ui/admin_access_api.py` with lazy db imports.
- **SPA:** reads `lib/access.can`.

- [ ] **Step 2: Visual check**

Use the demo harness from CLAUDE.md. The admin page and the hosted gating need hosted mode, so drive the screens with Playwright against a Vite dev server with mocked API routes. Follow the "Frontend e2e verification" memory: corepack pnpm, the mock harness under `~/.claude-tmp`. Screenshot:
- the Login page with the request form open, and after submit
- Matches as a `sharing` user with no matches (the empty-state line, no New match)
- `/admin/access` with two pending rows, one decided row with "Email not sent", and the Users table

Publish the screenshots as an Artifact (gaspode is headless) and show them before calling the UI done.

- [ ] **Step 3: Whole-branch review**

Dispatch a reviewer over the whole branch with these specific claims to verify, the implementation report marked as unverified:
1. No hosted route that creates a match, uploads raw media or submits a job is reachable by a `sharing` account. Grep every `jobs.submit` and every `upsert(` on `matches_store` and check each path.
2. The intake responses are byte-identical across email states, including the rate-limited and honeypot paths, on both routes.
3. Each new test fails against the pre-change code. Run the mutation drills from Tasks 3, 4 and 6 again on the final branch.
4. The local slim install still imports and builds the app.
5. A downgrade applies on the next request, both for a cookie and for a desktop token.

- [ ] **Step 4: Update the spec status, commit**

```bash
git add CLAUDE.md docs/saas-readiness/11-environment-strategy.md docs/superpowers/specs/2026-10-03-hosted-access-tiers-design.md
git commit -m "docs: hosted access tiers"
```
