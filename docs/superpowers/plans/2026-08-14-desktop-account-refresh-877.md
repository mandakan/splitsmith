# Desktop Account Refresh (#877) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A display name set on the web at `/account` reaches the desktop app's account chip, instead of the chip showing the user's raw email address forever.

**Architecture:** A new `GET /api/sync/whoami` on the hosted side returns identity only, reachable by the sync-scoped desktop token that `_auth_gate` confines to `/api/sync/*`. The desktop's existing `GET /api/settings/hosted-sync` -- the call the chip already makes on mount -- refreshes its `config.yaml` snapshot through that route, best-effort, behind an in-process TTL, writing only when a field changed.

**Tech Stack:** FastAPI, Pydantic, httpx, pytest, React (read-only -- no SPA change is needed; the chip already renders `display_name ?? email`).

## Global Constraints

- Python 3.11+, type hints everywhere. `pathlib.Path` for paths, never strings.
- Black formatting, line length 110. Ruff over `src tests scripts`.
- `uv` for dependency management, never `pip`. **No new dependencies.**
- Pydantic models for everything crossing a module boundary. No dicts of unknown shape.
- Imports: stdlib, third-party, local, separated by blank lines.
- **The #719 scope boundary does not move.** A sync-scoped token must keep getting 403 on `/api/me`. This plan adds a route to the surface that scope already reaches; it never widens the scope.
- The desktop's account snapshot carries identity, never a credential -- the rule `HostedSyncSettings` already states for `token_set`.
- Branch: `feat/desktop-account-refresh-877`, cut from `main` **after #876 merges**. Not stacked.
- Conventional-commit subjects. Squash bodies stay short -- a many-commit body breaks release-please's parser.

---

### Task 1: `GET /api/sync/whoami` on the hosted side

The desktop holds a sync-scoped token. `_auth_gate` confines that scope to `/api/sync/*` plus `/api/device/session`, so `/api/me` returns 403 by design (#719) -- a credential that exists to push match data should not read or name an account. The fix is a route on the surface the scope already reaches, returning only what the chip renders.

**Files:**
- Modify: `src/splitsmith/ui/sync_api.py` (response model near the other models around line 74; route near `create_or_adopt_match` at line 329)
- Modify: `src/splitsmith/sync/client.py` (new method next to `device_revoke_session`, line 113)
- Test: `tests/test_sync_api.py` (new tests at the end)
- Test: `tests/test_token_scope_gate.py` (the paired 200/403 assertion)

**Interfaces:**
- Produces: `GET /api/sync/whoami` -> `{"id": str, "email": str, "display_name": str | None}`. 404 outside hosted mode, 401 unauthenticated.
- Produces: `HostedSyncClient.whoami() -> dict` -- Task 2 calls this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sync_api.py`:

```python
# whoami (#877): the desktop's only way to refresh its cached account


def test_whoami_returns_identity_under_a_bearer(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    client.patch("/api/me", json={"display_name": "Mathias A"})
    headers = _bearer_for(client)

    resp = client.get("/api/sync/whoami", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == "owner@example.com"
    assert body["display_name"] == "Mathias A"
    assert body["id"]


def test_whoami_carries_no_credential(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    # Identity only. The desktop caches this body into config.yaml; a
    # token or session id leaking into it would be a credential written
    # to a plaintext file the SPA can read back.
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    headers = _bearer_for(client)

    body = client.get("/api/sync/whoami", headers=headers).json()

    assert set(body) == {"id", "email", "display_name"}


def test_whoami_null_display_name_for_an_unnamed_account(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "unnamed@example.com")
    headers = _bearer_for(client)

    assert client.get("/api/sync/whoami", headers=headers).json()["display_name"] is None


def test_whoami_reachable_under_a_session_cookie(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    # Not bearer-only: the route is ordinary hosted surface, and a
    # cookie-scoped caller is unrestricted.
    client, sender = hosted_app
    login(client, sender, "owner@example.com")

    assert client.get("/api/sync/whoami").status_code == 200


def test_whoami_404s_in_local_mode() -> None:
    from splitsmith.ui.server import create_app

    app = create_app()
    with TestClient(app) as client:
        assert client.get("/api/sync/whoami").status_code == 404
```

Append to `tests/test_token_scope_gate.py`:

```python
def test_sync_token_reads_whoami_but_still_not_me(
    hosted_app: tuple[TestClient, _CapturingSender], hosted_env: str
) -> None:
    """#877's route and the #719 boundary it must not breach, in one test.

    The desktop needs to refresh the account label it caches at link
    time, and cannot read /api/me to do it. The fix was a route on the
    surface the sync scope already reaches -- NOT a wider scope. If a
    future change makes the second assertion pass, the containment #719
    established is gone and the first assertion is no longer evidence of
    anything.
    """
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    token = _seed_token(hosted_env, "owner@example.com", scope="sync")
    client.cookies.clear()

    assert client.get("/api/sync/whoami", headers=_auth(token)).status_code == 200
    assert client.get("/api/me", headers=_auth(token)).status_code == 403
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sync_api.py -k whoami tests/test_token_scope_gate.py -k whoami -n0 -q`
Expected: FAIL -- 404 where 200 is asserted, because the route does not exist.

Note the 404-vs-403 detail: an unknown path under `/api/sync/` still passes the scope gate and reaches the router, which has no such route. So the failure is a 404, not a 403.

- [ ] **Step 3: Add the response model**

In `src/splitsmith/ui/sync_api.py`, next to the other response models (after `SyncMatchCreateResponse`, around line 88):

```python
class SyncWhoAmIResponse(BaseModel):
    """Body for ``GET /api/sync/whoami`` (#877).

    Identity only, never a credential -- the same rule
    ``HostedSyncSettings`` states for the desktop's own settings body.

    Exists because the desktop caches ``email`` / ``display_name`` into
    ``config.yaml`` at device-link time and had no way to refresh them:
    its token is sync-scoped, and ``_auth_gate`` gives that scope a 403
    on ``/api/me`` deliberately (#719). Widening the scope for a label
    would have undone that containment; this route is the narrow half.
    """

    id: str
    email: str
    display_name: str | None = None
```

- [ ] **Step 4: Add the route**

In the same file, above `create_or_adopt_match` (line 329):

```python
@router.get("/whoami", response_model=SyncWhoAmIResponse)
async def whoami(user: Any = Depends(_current_user)) -> SyncWhoAmIResponse:
    """The account this credential belongs to, as the desktop chip renders it.

    Read-only and free of side effects: the desktop calls it on a chip
    mount, so it must stay cheap enough to sit in front of a UI paint.
    """
    _hosted_gate()
    return SyncWhoAmIResponse(
        id=str(user.id),
        email=str(user.email),
        display_name=user.display_name,
    )
```

`user` is `splitsmith.auth.User`, which already carries `display_name: str | None` -- no store lookup is needed, and adding one would put a query in front of every chip mount.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sync_api.py -k whoami tests/test_token_scope_gate.py -k whoami -n0 -q`
Expected: PASS, 6 passed.

- [ ] **Step 6: Add the client method**

In `src/splitsmith/sync/client.py`, after `device_revoke_session` (line 116):

```python
    def whoami(self) -> dict:
        """The linked account's identity (#877). Needs the bearer.

        Used by the desktop to refresh the account snapshot it cached at
        link time. ``/api/me`` is unreachable with this client's
        sync-scoped token, which is why this route exists.
        """
        resp = self._http.get("/api/sync/whoami")
        self._raise_for_status(resp)
        return resp.json()
```

- [ ] **Step 7: Run the scope gate's mutation drill**

`tests/test_token_scope_gate.py`'s own docstring prescribes it, and this task added a test to that file. Delete the scope `if` in `_auth_gate` (`server.py`, around line 7438), run the file, confirm every 403 assertion -- including the new one -- goes red, restore.

Run: `uv run pytest tests/test_token_scope_gate.py -n0 -q`

- [ ] **Step 8: Lint, format, commit**

```bash
uv run ruff check src tests scripts
uv run black --check src tests scripts
git add src/splitsmith/ui/sync_api.py src/splitsmith/sync/client.py tests/test_sync_api.py tests/test_token_scope_gate.py
git commit -m "feat(sync): a sync-scoped identity route the desktop can read"
```

---

### Task 2: Refresh the desktop's cached snapshot

`HostedAccountRef` is written into `config.yaml` once, when the device flow completes, and nothing refreshes it. Link the app while `display_name` is `NULL`, set a name on the web, and the chip renders the email indefinitely -- repairable only by unlinking and re-linking, which nobody would guess.

`HostedAccountChip` already calls `GET /api/settings/hosted-sync` on mount and on `HOSTED_ACCOUNT_CHANGED_EVENT`. That is the call that refreshes.

**Files:**
- Modify: `src/splitsmith/ui/server.py` -- `AppState` field (near `device_flow`, line 1664); `_build_device_client` (line 5933); module constants; `get_hosted_sync_settings` (line 14565); the snapshot comment in `get_device_status` (around line 14751); `HostedAccountInfo`'s docstring (line 4843)
- Modify: `src/splitsmith/user_config.py` -- `HostedAccountRef` docstring (line 109)
- Modify: `src/splitsmith/ui/device_auth_api.py` -- the staleness comment (line 183)
- Test: `tests/test_device_local_endpoints.py`

**Interfaces:**
- Consumes: `HostedSyncClient.whoami()` from Task 1.
- Produces: no new public surface. `GET /api/settings/hosted-sync`'s response shape is unchanged -- only its freshness changes.

- [ ] **Step 1: Write the failing tests**

`tests/test_device_local_endpoints.py` already monkeypatches `server_mod._build_device_client` with an `httpx.MockTransport` double (its `_FakeHosted` class, line 45, and the `_build` seam at line 90). Extend that double to answer whoami, then add the tests.

In `_FakeHosted.__init__`, add:

```python
        self.whoami_calls = 0
        self.whoami_payload: dict | None = None
        self.whoami_status = 200
```

In `_FakeHosted.handle`, add a branch alongside the existing paths:

```python
        if path == "/api/sync/whoami":
            self.whoami_calls += 1
            if self.whoami_status != 200:
                return httpx.Response(self.whoami_status, json={"detail": "nope"})
            return httpx.Response(200, json=self.whoami_payload or {})
```

Then append these tests:

```python
# The cached account snapshot refreshes itself (#877)


def _link_account(tmp_path: Path, monkeypatch, hosted: _FakeHosted) -> TestClient:
    """A local app with config.yaml already holding a linked account.

    Written directly rather than driven through the device flow: this
    file already covers the flow, and these tests are about what happens
    to the snapshot afterwards.
    """
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path / "home"))
    prefs = user_config.load_global_prefs()
    prefs.hosted_base_url = "https://hosted.example"
    prefs.hosted_token = "desktop-token"
    prefs.hosted_account = user_config.HostedAccountRef(
        id="u1",
        email="owner@example.com",
        display_name=None,
        device_name="mac studio",
        linked_at=datetime.now(UTC),
    )
    user_config.save_global_prefs(prefs)
    client = _local_app(tmp_path)
    monkeypatch.setattr(
        server_mod,
        "_build_device_client",
        lambda base_url, **kw: HostedSyncClient(
            http=httpx.Client(base_url=base_url, transport=httpx.MockTransport(hosted.handle))
        ),
    )
    return client


def test_settings_picks_up_a_display_name_set_on_the_web(tmp_path: Path, monkeypatch) -> None:
    hosted = _FakeHosted([])
    hosted.whoami_payload = {"id": "u1", "email": "owner@example.com", "display_name": "Mathias A"}
    client = _link_account(tmp_path, monkeypatch, hosted)

    body = client.get("/api/settings/hosted-sync").json()

    assert body["account"]["display_name"] == "Mathias A"
    assert hosted.whoami_calls == 1
    # and it survives a restart, i.e. it reached config.yaml
    assert user_config.load_global_prefs().hosted_account.display_name == "Mathias A"


def test_settings_picks_up_an_email_change_too(tmp_path: Path, monkeypatch) -> None:
    hosted = _FakeHosted([])
    hosted.whoami_payload = {"id": "u1", "email": "new@example.com", "display_name": None}
    client = _link_account(tmp_path, monkeypatch, hosted)

    assert client.get("/api/settings/hosted-sync").json()["account"]["email"] == "new@example.com"


def test_an_upstream_failure_returns_the_cached_account(tmp_path: Path, monkeypatch) -> None:
    # #738: a transient failure must not make a linked operator look
    # unlinked. The chip reads a missing account as "sign in".
    hosted = _FakeHosted([])
    hosted.whoami_status = 503
    client = _link_account(tmp_path, monkeypatch, hosted)

    resp = client.get("/api/settings/hosted-sync")

    assert resp.status_code == 200
    assert resp.json()["account"]["email"] == "owner@example.com"


def test_a_401_does_not_unlink_the_device(tmp_path: Path, monkeypatch) -> None:
    # Deliberate: auto-unlinking on an upstream status code would mean a
    # hosted outage signs every desktop install out. Revocation still
    # surfaces on the next sync.
    hosted = _FakeHosted([])
    hosted.whoami_status = 401
    client = _link_account(tmp_path, monkeypatch, hosted)

    assert client.get("/api/settings/hosted-sync").json()["account"] is not None
    assert user_config.load_global_prefs().hosted_token == "desktop-token"


def test_a_second_call_inside_the_ttl_does_not_hit_upstream(tmp_path: Path, monkeypatch) -> None:
    # GlobalBar and the mobile drawer each render a chip with independent
    # state, and both refetch on route changes. Without the TTL a desktop
    # session issues a steady trickle of upstream calls for a label.
    hosted = _FakeHosted([])
    hosted.whoami_payload = {"id": "u1", "email": "owner@example.com", "display_name": "Mathias A"}
    client = _link_account(tmp_path, monkeypatch, hosted)

    client.get("/api/settings/hosted-sync")
    client.get("/api/settings/hosted-sync")

    assert hosted.whoami_calls == 1


def test_a_failed_refresh_also_consumes_the_ttl(tmp_path: Path, monkeypatch) -> None:
    # Otherwise every chip mount retries against a dead host and blocks
    # for the timeout each time.
    hosted = _FakeHosted([])
    hosted.whoami_status = 503
    client = _link_account(tmp_path, monkeypatch, hosted)

    client.get("/api/settings/hosted-sync")
    client.get("/api/settings/hosted-sync")

    assert hosted.whoami_calls == 1


def test_an_unchanged_response_does_not_rewrite_config(tmp_path: Path, monkeypatch) -> None:
    hosted = _FakeHosted([])
    hosted.whoami_payload = {"id": "u1", "email": "owner@example.com", "display_name": None}
    client = _link_account(tmp_path, monkeypatch, hosted)
    config_path = user_config.user_config_dir() / user_config.CONFIG_FILENAME
    before = config_path.stat().st_mtime_ns

    client.get("/api/settings/hosted-sync")

    assert config_path.stat().st_mtime_ns == before


def test_an_unlinked_install_makes_no_upstream_call(tmp_path: Path, monkeypatch) -> None:
    hosted = _FakeHosted([])
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path / "home"))
    client = _local_app(tmp_path)
    monkeypatch.setattr(
        server_mod,
        "_build_device_client",
        lambda base_url, **kw: HostedSyncClient(
            http=httpx.Client(base_url=base_url, transport=httpx.MockTransport(hosted.handle))
        ),
    )

    assert client.get("/api/settings/hosted-sync").json()["account"] is None
    assert hosted.whoami_calls == 0
```

Add the imports these need to the file's import block: `from datetime import UTC, datetime`.

`user_config` exposes no public path accessor -- `_config_path()` is private -- so the mtime test composes the path from the two public names `save_global_prefs` writes through: `user_config.user_config_dir() / user_config.CONFIG_FILENAME`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_device_local_endpoints.py -n0 -q`
Expected: the eight new tests FAIL -- `whoami_calls == 0` where 1 is asserted, and `display_name` still `None`. The file's pre-existing tests stay green.

- [ ] **Step 3: Add the TTL field to `AppState`**

In `src/splitsmith/ui/server.py`, next to `device_flow` (line 1664):

```python
    # Monotonic timestamp of the last upstream account refresh (#877), or
    # None when none has run in this process. Held here rather than in
    # config.yaml because it is per-process liveness, not user
    # preference: a restart should refresh, and two installs sharing a
    # home directory should not share a cooldown.
    hosted_account_refreshed_at: float | None = None
```

- [ ] **Step 4: Add the constants**

Near the other module-level tunables in `server.py`:

```python
#: How long the cached hosted-account snapshot is trusted before the next
#: ``GET /api/settings/hosted-sync`` refreshes it upstream (#877). The
#: chip calls that route on every mount, and GlobalBar plus the mobile
#: drawer each render one, so an untimed refresh would be a steady
#: trickle of upstream calls for a label.
HOSTED_ACCOUNT_REFRESH_TTL_S = 300.0

#: Connect/read budget for that refresh. Much shorter than the 30s the
#: device-flow calls use: this one sits in front of a UI paint, and its
#: failure mode is "keep showing the cached name", which costs nothing.
HOSTED_ACCOUNT_REFRESH_TIMEOUT_S = 5.0
```

- [ ] **Step 5: Let `_build_device_client` take a timeout**

Replace `_build_device_client` (line 5933):

```python
def _build_device_client(
    base_url: str, *, token: str | None = None, timeout: float = 30.0
) -> HostedSyncClient:
    """Build a ``HostedSyncClient`` for the device-flow calls (#719).

    ``token=None`` gives the unauthenticated client the two public device
    routes need - there is no bearer to send before the flow completes.
    The one seam tests monkeypatch to script a hosted side.

    ``timeout`` defaults to the device flow's 30s. The account refresh
    (#877) passes something much shorter: it runs in front of a UI paint
    and can afford to give up.
    """
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return HostedSyncClient(http=httpx.Client(base_url=base_url, headers=headers, timeout=timeout))
```

- [ ] **Step 6: Add the refresh, and call it**

In `server.py`, above `get_hosted_sync_settings` (line 14565):

```python
    async def _refresh_hosted_account(prefs: user_config.GlobalPrefs) -> user_config.GlobalPrefs:
        """Best-effort upstream refresh of the cached account snapshot (#877).

        The snapshot is written once, at device-link time, when
        ``display_name`` is typically still NULL. Without this the chip
        renders the operator's email address forever -- the one thing
        the design says it will never publish -- and the only repair is
        to unlink and re-link.

        Best-effort in the strong sense: every failure path returns the
        cached value with no error surfaced. The chip reads a missing
        account as "not signed in" (#738), so a network blip must not be
        able to produce that.

        The TTL is consumed before the call, not after, so a dead host
        costs one timeout per window rather than one per chip mount.
        """
        ref = prefs.hosted_account
        if ref is None or not prefs.hosted_base_url or not prefs.hosted_token:
            return prefs

        now = time.monotonic()
        last = state.hosted_account_refreshed_at
        if last is not None and now - last < HOSTED_ACCOUNT_REFRESH_TTL_S:
            return prefs
        state.hosted_account_refreshed_at = now

        client = _build_device_client(
            prefs.hosted_base_url,
            token=prefs.hosted_token,
            timeout=HOSTED_ACCOUNT_REFRESH_TIMEOUT_S,
        )
        try:
            payload = await run_in_threadpool(client.whoami)
        except (SyncClientError, httpx.HTTPError, ValueError):
            # Includes 401. A revoked token is NOT grounds to unlink
            # here: an upstream outage would then sign every install
            # out. Revocation surfaces on the next sync, where the
            # operator is already watching an outcome.
            return prefs
        finally:
            client.close()

        email = str(payload.get("email") or ref.email)
        raw_name = payload.get("display_name")
        display_name = str(raw_name) if raw_name is not None else None
        if email == ref.email and display_name == ref.display_name:
            return prefs

        prefs.hosted_account = ref.model_copy(
            update={"email": email, "display_name": display_name}
        )
        user_config.save_global_prefs(prefs)
        return prefs
```

Then in `get_hosted_sync_settings`, one line:

```python
    @app.get("/api/settings/hosted-sync", response_model=HostedSyncSettings)
    async def get_hosted_sync_settings() -> HostedSyncSettings:
        if _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        prefs = user_config.load_global_prefs()
        prefs = await _refresh_hosted_account(prefs)
        return HostedSyncSettings(
            base_url=prefs.hosted_base_url,
            token_set=bool(prefs.hosted_token),
            account=_account_info(prefs.hosted_account),
        )
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_device_local_endpoints.py -n0 -q`
Expected: PASS, all tests in the file including the eight new ones.

- [ ] **Step 8: Update the four comments that are now false**

Each records the limitation this task closes. Update, do not delete -- the reason the route exists belongs next to the code that depends on it.

`src/splitsmith/user_config.py`, `HostedAccountRef` docstring:

```python
class HostedAccountRef(BaseModel):
    """The hosted account this desktop install is linked to (#719).

    Seeded from the device-flow poll and refreshed from
    ``GET /api/sync/whoami`` whenever the SPA reads
    ``GET /api/settings/hosted-sync`` and the cached copy is older than
    ``HOSTED_ACCOUNT_REFRESH_TTL_S`` (#877). The refresh goes through the
    sync surface rather than ``/api/me``, which the sync-scoped token
    this install holds cannot reach by design.

    A hosted-side name or email change therefore propagates within one
    TTL window while the app is running, and the stored copy is what the
    chip renders when the host is unreachable.
    """
```

`src/splitsmith/ui/server.py`, `HostedAccountInfo` docstring (line 4844):

```python
class HostedAccountInfo(BaseModel):
    """The linked hosted account, as the SPA renders it (#719).

    Cached in ``config.yaml`` from the device-flow poll, then refreshed
    best-effort from ``GET /api/sync/whoami`` on read (#877) -- a
    sync-scoped token still cannot reach ``/api/me``, which is why the
    refresh has its own route on the sync surface.
    """
```

`src/splitsmith/ui/server.py`, the snapshot comment inside `get_device_status` (around line 14751):

```python
                    # The initial snapshot. display_name is typically
                    # NULL at this moment -- an account sets its name on
                    # the web afterwards -- so this is a starting point,
                    # not the last word. GET /api/settings/hosted-sync
                    # refreshes it from /api/sync/whoami (#877).
                    display_name=account.get("display_name"),
```

`src/splitsmith/ui/device_auth_api.py`, the staleness comment inside `poll_device_token` (around line 181). It currently ends "staleness is a property of that cache, not of this read", which was true when the cache had no way to refresh. Replace with:

```python
            # Live read, not a cache - this is where the account's current
            # display_name crosses from DB state onto the wire. The desktop
            # client caches this response (see server.py's
            # get_device_status, ``prefs.hosted_account = ...``), and since
            # #877 that cache refreshes itself from /api/sync/whoami rather
            # than holding whatever this poll happened to return. Which
            # matters here: at this moment display_name is usually still
            # NULL, because an account sets its name after linking.
            display_name=result.account.display_name,
```

- [ ] **Step 9: Run the full Python suite**

Run: `uv run pytest -q`
Expected: green. `_build_device_client`'s signature changed, so anything passing it positionally would break -- this confirms nothing did.

- [ ] **Step 10: Lint, format, commit**

```bash
uv run ruff check src tests scripts
uv run black --check src tests scripts
git add src/splitsmith/ui/server.py src/splitsmith/user_config.py src/splitsmith/ui/device_auth_api.py tests/test_device_local_endpoints.py
git commit -m "feat(desktop): refresh the linked account's name instead of caching it forever"
```

---

### Task 3: Two-server end-to-end verification

The unit tests script the hosted side with a mock transport, so nothing has yet proven the two real halves agree on the route, the bearer, or the payload shape. Staging cannot answer it either -- staging deploys from `main` and runs the released build, so a branch-only route simply does not exist there. That was learned the hard way on #719, where `/api/device/*` 404'd on staging.

**Files:** none. This task produces evidence, not a diff.

**Interfaces:**
- Consumes: Tasks 1 and 2.

- [ ] **Step 1: Bring up a hosted server on a fresh database**

```bash
mkdir -p ~/.claude-tmp/877
DB=~/.claude-tmp/877/hosted.sqlite
SPLITSMITH_DATABASE_URL="sqlite+aiosqlite:///$DB" uv run alembic upgrade head
SPLITSMITH_DATABASE_URL="sqlite+aiosqlite:///$DB" \
SPLITSMITH_PUBLIC_URL=http://localhost:5190 \
SPLITSMITH_EMAIL_BACKEND=console \
  uv run splitsmith serve --host 127.0.0.1 --port 5190 --skip-migrations --skip-system-check
```

Run it in the background and keep the log -- the magic-link URL is printed there.

- [ ] **Step 2: Sign in and set a display name**

Request a magic link for a scratch address, then curl the `http://localhost:5190/auth/callback?token=...` URL the console transport printed, with `-c ~/.claude-tmp/877/cookies.txt`.

**Use `localhost` throughout, never `127.0.0.1`.** The session cookie is scoped to `localhost`; mixing the two silently drops the session and every call reads as anonymous.

Then:

```bash
curl -s -b ~/.claude-tmp/877/cookies.txt -X PATCH http://localhost:5190/api/me \
  -H 'content-type: application/json' -d '{"display_name":"Mathias A"}'
```

- [ ] **Step 3: Link a desktop install and read the chip's data before the name lands**

Start a desktop install with an isolated config so this never touches the real `~/.splitsmith`:

```bash
SPLITSMITH_HOME=~/.claude-tmp/877/desktop \
  uv run splitsmith ui --project <a match folder> --port 5191 --no-browser
```

Point it at `http://localhost:5190`, run the device flow, and approve it in the hosted UI. To reproduce the actual bug, link **before** setting the display name if you can order it that way; otherwise clear `display_name` on the hosted side with a second `PATCH /api/me` sending `null`, and confirm `GET http://localhost:5191/api/settings/hosted-sync` reports `display_name: null`.

- [ ] **Step 4: Set the name on the web, then watch the desktop pick it up**

```bash
curl -s -b ~/.claude-tmp/877/cookies.txt -X PATCH http://localhost:5190/api/me \
  -H 'content-type: application/json' -d '{"display_name":"Mathias A"}'
curl -s http://localhost:5191/api/settings/hosted-sync | python3 -m json.tool
```

Expected: `account.display_name` is `"Mathias A"`.

If it still reports `null`, the TTL from an earlier read is holding. Restart the desktop process and re-read -- `hosted_account_refreshed_at` is per-process by design, so a restart always refreshes.

- [ ] **Step 5: Confirm it reached disk, and that the chip renders it**

```bash
grep -A 5 hosted_account ~/.claude-tmp/877/desktop/config.yaml
```

Expected: `display_name: Mathias A`.

Then load `http://localhost:5191` in a browser and look at the chip. It must read `Mathias A`, not the email address.

**Look at the rendered chip, not just the JSON.** On #617 a fix reached the table cell and rich ellipsized it away -- the assertion passed while the user saw nothing. Chip width is exactly the kind of thing that swallows a longer string: `HostedAccountChip` truncates at `max-w-[16rem]`.

If the Playwright MCP browser is used for this: its context is persistent, and a route mock left there intercepts `/api/*` for every origin. It has already nearly produced a false bug report on this surface. Fetch from inside the page and check for fixture values before trusting what you see; `browser_close` then re-navigate clears it.

- [ ] **Step 6: Kill both servers and record the result**

Paste the before/after `GET /api/settings/hosted-sync` bodies and a screenshot of the chip into the PR body. That is what makes this fix reviewable -- the diff alone cannot show that the two halves agree.

- [ ] **Step 7: Open the PR**

```bash
git push -u origin feat/desktop-account-refresh-877
gh pr create --fill --title "feat(desktop): the account chip keeps a stale display name until re-link (#877)"
gh run watch
```

---

## Done when

- A display name set at `/account` reaches the desktop chip within one TTL window, demonstrated on two real servers with the rendered chip looked at.
- A sync-scoped token gets 200 on `/api/sync/whoami` and 403 on `/api/me`, asserted together.
- An unreachable hosted host leaves the chip showing the cached account, not a sign-in button.
- No new dependency, and no change to what the sync scope may reach beyond the one route added.
