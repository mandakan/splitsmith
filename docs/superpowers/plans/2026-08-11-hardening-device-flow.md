# Hardening Wave PR 1: Device Flow, Auth Gate, Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close issues #734, #736, #737, #735, #738, #739 and #725 in one PR: deterministic deployment-mode resolution, live account-chip refresh, no orphaned hosted credentials, bounded device_authorizations growth, the five #738 test/behavior gaps, and removal of the retired `health.bound` signal.

**Architecture:** SPA changes live in `src/splitsmith/ui_static/src` (React 18 + vitest + testing-library); backend changes in `src/splitsmith/ui/server.py` and `src/splitsmith/db/device_auth.py` (FastAPI + SQLAlchemy async + pytest). No schema migration needed. The AuthGate gains a mode-resolution gate; the retired bound signal is deleted from the SPA (server keeps returning it for API compat); cross-component chip refresh follows the existing `splitsmith:no-project` CustomEvent precedent.

**Tech Stack:** TypeScript/React/vitest, Python/FastAPI/pytest, httpx.MockTransport fakes.

## Global Constraints

- Branch: `fix/hardening-device-flow` off `origin/main`. Work in a worktree (superpowers:using-git-worktrees).
- New prose/comments use single ASCII dash `-`, never `--`, never em dash. ASCII punctuation only.
- SPA commits that should appear in the changelog use `fix(ui):` / `refactor(ui):` prefixes (bare `ui:` is dropped by release-please).
- Per-task test runs are scoped (named test files only). Full gates run once in the final task: `ruff check . && black --check . && pytest` plus `pnpm typecheck && pnpm test` and scoped eslint in `src/splitsmith/ui_static`.
- SPA test caveat: `src/lib/features.ts` caches deployment mode in a module-level promise with no invalidation - the first mode resolved in a test FILE wins for the whole file. Tests needing a different mode or a delayed resolve go in their own file.
- Do not remove `bound` from the server's `HealthResponse` or from the SPA `ServerHealth` type - `_register_response` (picker/create flow) still returns `bound: true` meaningfully. Only SPA consumers of `/api/health`'s always-false value are deleted.
- The user does not edit files in this repo; any lint/test debt encountered was introduced by prior Claude sessions - fix or surface it, never dismiss as pre-existing. ~21 env-dependent local pytest failures are green in CI; verify suspicious failures against main before treating them as caused by this branch.

---

### Task 1: AuthGate holds the route tree until deployment mode resolves (#734)

**Files:**
- Modify: `src/splitsmith/ui_static/src/App.tsx` (AuthGate, lines ~108-196)
- Test: `src/splitsmith/ui_static/src/App.routes.modegate.test.tsx` (new file - the module-level features cache means the delayed-resolve mock needs its own file)

**Interfaces:**
- Consumes: `useDeploymentMode(): { mode: "local" | "hosted"; resolved: boolean }` from `src/lib/features.ts` (already exists).
- Produces: AuthGate renders a `<Standby />` placeholder until `resolved === true`; the ordinary route tree never mounts with the provisional `"local"` default. Later tasks (2, 3) may assume any component inside the route tree sees a final mode.

- [ ] **Step 1: Write the failing test**

Create `src/App.routes.modegate.test.tsx`. Mirror the mock idiom of `src/App.routes.pickup.test.tsx` (full-App render, real `window.history`, delayed `getServerFeatures`):

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const FEATURES_DELAY_MS = 200;

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getMe: vi.fn().mockResolvedValue({
        id: "u1",
        email: "m@thias.se",
        display_name: null,
        is_admin: false,
      }),
      getServerFeatures: vi.fn().mockImplementation(
        () =>
          new Promise((resolve) => {
            setTimeout(
              () => resolve({ lab: false, mode: "local" }),
              FEATURES_DELAY_MS,
            );
          }),
      ),
      getHealth: vi.fn().mockResolvedValue({
        status: "ok",
        bound: false,
        project_name: null,
        project_root: null,
        match_id: null,
        kind: null,
        default_shooter_slug: null,
        schema_version: null,
      }),
      getScoreboardIdentity: vi.fn().mockResolvedValue(null),
      getRecentProjectsDetail: vi.fn().mockResolvedValue([]),
    },
  };
});

import { api } from "@/lib/api";

describe("AuthGate mode-resolution gate (#734)", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("holds the route tree on standby until /api/server/features resolves", async () => {
    window.history.pushState({}, "", "/pick");
    const { App } = await import("@/App");
    render(<App />);

    // While the mode is unresolved the tree must not mount: Pick's own
    // data fetch is the observable proxy for "the tree mounted".
    expect(await screen.findByRole("status", { name: /loading/i })).toBeInTheDocument();
    expect(api.getRecentProjectsDetail).not.toHaveBeenCalled();

    await waitFor(
      () => expect(api.getRecentProjectsDetail).toHaveBeenCalled(),
      { timeout: FEATURES_DELAY_MS + 1000 },
    );
    expect(screen.queryByRole("status", { name: /loading/i })).not.toBeInTheDocument();
  });
});
```

Adjust the `getMe` / auth-context mock shape to whatever `App.routes.pickup.test.tsx` actually mocks (copy its module-mock block verbatim as the base) - the assertion pair (`getRecentProjectsDetail` not called while pending, called after) is the contract.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/App.routes.modegate.test.tsx`
Expected: FAIL - `getRecentProjectsDetail` has already been called while features are pending (tree mounts on the "local" default).

- [ ] **Step 3: Implement the gate**

In `src/App.tsx`:

1. Extract the standby JSX (currently inline in the `status === "loading"` branch) into a component above `AuthGate`:

```tsx
function Standby() {
  return (
    <div
      className="grid min-h-dvh place-items-center bg-bg"
      role="status"
      aria-label="Loading"
    >
      <span className="font-mono text-xs uppercase tracking-[0.16em] text-subtle">
        Standby...
      </span>
    </div>
  );
}
```

2. In `AuthGate`, destructure `resolved`:

```tsx
const { mode, resolved } = useDeploymentMode();
```

3. Replace the `status === "loading"` branch body with `return <Standby />;`.

4. Insert the gate after the `pendingPickupCode` Navigate and before the `mode === "local"` early return:

```tsx
  // Hold the route tree until the deployment mode has genuinely resolved
  // (#734). Mounting on the provisional "local" default lets mode-gated
  // surfaces fire local-only requests against a hosted server and
  // reintroduces the mount-then-navigate races described above for every
  // future mode-dependent feature. The share and pickup branches stay
  // above this line: neither depends on mode, and the pickup must win
  // the first commit regardless of how slow /api/server/features is.
  if (!resolved) return <Standby />;
```

5. Update the long `pendingPickupCode` comment block: the paragraph starting "Deliberately NOT gated on deployment mode" stays true (the pickup check remains above the gate) but add one sentence noting the gate below now also stops the ordinary tree from mounting pre-resolve, closing the same class for other features.

- [ ] **Step 4: Run the new test and the adjacent route tests**

Run: `pnpm vitest run src/App.routes.modegate.test.tsx src/App.routes.pickup.test.tsx src/App.routes.test.tsx src/App.routes.hosted.test.tsx src/App.routes.share.test.tsx`
Expected: all PASS. (`App.routes.pickup.test.tsx` must stay green: the pickup Navigate sits above the gate.)

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/App.tsx src/splitsmith/ui_static/src/App.routes.modegate.test.tsx
git commit -m "fix(ui): hold route tree until deployment mode resolves (#734)"
```

---

### Task 2: Delete the retired bound signal from the SPA (#725, #739)

**Files:**
- Modify: `src/splitsmith/ui_static/src/App.tsx` (delete `LegacyMatchRedirect`, lines ~73-106, and its catch-all usage ~line 349)
- Modify: `src/splitsmith/ui_static/src/components/AppShell.tsx` (delete health fetch + bound redirect + `ProjectHeader` bound branch)
- Modify: `src/splitsmith/ui_static/src/App.routes.test.tsx` (the `/_design` test's `bound: true` override becomes dead)
- Test: existing suites pin the behavior; one new assertion in `App.routes.test.tsx`

**Interfaces:**
- Consumes: nothing new.
- Produces: catch-all route is `<Route path="*" element={<Navigate to="/pick" replace />} />` (synchronous - no async navigation left to race, which retires #739). `AppShell` renders unconditionally for `/promote-review` and `/_design` (fixes #725). `ServerHealth.bound` stays in `src/lib/api.ts` (the register/bind flow still uses it).

Background for the engineer: `GET /api/health` returns `bound: false` unconditionally (`src/splitsmith/ui/server.py:6750` - "Tier 1 step 4 of doc 10 retired the bound-state concept"). Every SPA branch on `getHealth().bound` is therefore dead: `AppShell`'s redirect makes `/_design` and `/promote-review` unreachable on a real server (#725), and `LegacyMatchRedirect`'s `h.bound && h.match_id` branch never fires, leaving only an async detour to `/pick` that races effect-driven navigation (#739). Delete both; do not preserve them behind flags (pre-production, no-fallbacks policy).

- [ ] **Step 1: Write the failing test**

In `src/App.routes.test.tsx`, find the test `"renders AppShell chrome on an AppShell surface (/_design)"` (~line 109). Delete the `vi.mocked(api.getHealth).mockResolvedValueOnce({ ... bound: true ... })` override and its long explanatory comment (lines ~110-127), so the test now runs against the shared module-level mock (`bound: false`). Keep both existing assertions (global nav + "Design system" link).

- [ ] **Step 2: Run it to verify it fails**

Run: `pnpm vitest run src/App.routes.test.tsx -t "AppShell surface"`
Expected: FAIL - with `bound: false` and no override, AppShell redirects to /pick and the "Design system" link is absent. This failure IS issue #725 reproduced in a test.

- [ ] **Step 3: Implement**

In `AppShell.tsx`:
1. Delete the `health` state, its `useEffect`, the bind-state comment block (lines ~77-100), and the redirect `if (!bindExempt && health && !health.bound) { return <Navigate to="/pick" replace />; }` (lines 102-104).
2. `contextRow`: replace `<ProjectHeader health={health} />` with `<ProjectHeader />`.
3. `ProjectHeader`: remove the `health` prop and the `health.bound` branch entirely. The bound branch's body was the only reachable one, so the component collapses to:

```tsx
function ProjectHeader() {
  return <div className="text-sm text-muted">splitsmith</div>;
}
```

Also delete the now-unused `switchProject` function, `switching` state, `useNavigate` call inside `ProjectHeader`, and the imports that become unused (`Navigate`, `Repeat`, `api`, `ServerHealth` - check with `pnpm typecheck`/eslint after the edit; `api` may still be used elsewhere in the file).

In `App.tsx`:
1. Delete `LegacyMatchRedirect` (the whole function, ~lines 78-106) and the doc comment above it (~73-77).
2. Replace the catch-all:

```tsx
          {/* Catch-all: /api/health retired the bound-state concept, so a
              bare or unknown path has exactly one destination - the
              picker. Synchronous on purpose: an async redirect here is
              what used to race effect-driven navigation (#739). */}
          <Route path="*" element={<Navigate to="/pick" replace />} />
```

3. Update the `pendingPickupCode` comment in AuthGate: it names "LegacyMatchRedirect's async getHealth()-driven redirect" as the racing tree - reword to reference the (now synchronous) catch-all and note the historical race is gone but the pickup ordering is kept for the reasons that remain (StrictMode stash semantics, mode-independence).

- [ ] **Step 4: Run the SPA suites for the touched surfaces**

Run: `pnpm vitest run src/App.routes.test.tsx src/App.routes.pickup.test.tsx src/App.routes.hosted.test.tsx src/App.routes.share.test.tsx src/App.routes.modegate.test.tsx && pnpm typecheck`
Expected: PASS. If any test asserted `LegacyMatchRedirect`-specific behavior (bound:true bookmark redirect to /match/<id>), delete that test - it pins retired server behavior (delete-obsolete-tests policy). `src/pages/Ingest.addFootage.test.tsx` / `Ingest.emptyState.test.tsx` mock `bound: true` inside a ServerHealth object but do not route through AppShell - leave them alone.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/App.tsx src/splitsmith/ui_static/src/components/AppShell.tsx src/splitsmith/ui_static/src/App.routes.test.tsx
git commit -m "fix(ui): drop retired health.bound checks; /_design and /promote-review reachable (#725, #739)"
```

---

### Task 3: Account chip refreshes on settings mutations (#736)

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (event constant + dispatch in `putSyncSettings` / `unlinkHostedAccount`, ~lines 3867-3899)
- Modify: `src/splitsmith/ui_static/src/components/account/HostedAccountChip.tsx` (listener effect + dispatch on link)
- Test: `src/splitsmith/ui_static/src/components/account/HostedAccountChip.test.tsx`

**Interfaces:**
- Consumes: the existing `splitsmith:no-project` CustomEvent precedent (`api.ts` ~line 1990).
- Produces: `export const HOSTED_ACCOUNT_CHANGED_EVENT = "splitsmith:hosted-account-changed"` and `export function notifyHostedAccountChanged(): void` in `api.ts`. Both `HostedAccountChip` instances (GlobalBar + MatchShell drawer) refetch on the event, so they can never disagree after a save/unlink/link.

- [ ] **Step 1: Write the failing test**

In `HostedAccountChip.test.tsx` (follow the file's existing spread-actual `vi.mock("@/lib/api", ...)` idiom - `getSyncSettings` is already a re-assignable mock there):

```tsx
import { HOSTED_ACCOUNT_CHANGED_EVENT } from "@/lib/api";

it("refetches when the hosted account changes elsewhere (#736)", async () => {
  getSyncSettings.mockResolvedValueOnce({
    base_url: "https://hosted.example",
    token_set: false,
    account: null,
  });
  render(<HostedAccountChip />);
  expect(
    await screen.findByRole("button", { name: /sign in to splitsmith\.app/i }),
  ).toBeInTheDocument();

  getSyncSettings.mockResolvedValueOnce({
    base_url: "https://hosted.example",
    token_set: true,
    account: {
      id: "u1",
      email: "shooter@example.com",
      display_name: null,
      device_name: "gaspode",
    },
  });
  act(() => {
    window.dispatchEvent(new CustomEvent(HOSTED_ACCOUNT_CHANGED_EVENT));
  });
  expect(await screen.findByText(/shooter@example\.com/)).toBeInTheDocument();
});
```

Match the `account` object shape to the file's existing linked-account fixtures.

- [ ] **Step 2: Run it to verify it fails**

Run: `pnpm vitest run src/components/account/HostedAccountChip.test.tsx`
Expected: FAIL - `HOSTED_ACCOUNT_CHANGED_EVENT` is not exported yet (compile error), then after a stub export, the chip does not listen so the email never appears.

- [ ] **Step 3: Implement**

In `api.ts`, next to the sync-settings methods:

```ts
/** Fired on window after any mutation that changes the linked hosted
 *  account (settings save, unlink, device link). HostedAccountChip
 *  renders twice (GlobalBar and the mobile nav drawer) with independent
 *  state; the event is what keeps the copies in agreement (#736). */
export const HOSTED_ACCOUNT_CHANGED_EVENT = "splitsmith:hosted-account-changed";

export function notifyHostedAccountChanged(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(HOSTED_ACCOUNT_CHANGED_EVENT));
  }
}
```

Rewrite the two mutating methods to dispatch after success (keep their doc comments):

```ts
  putSyncSettings: async (baseUrl: string, token: string | null) => {
    const settings = await request<HostedSyncSettings>("/api/settings/hosted-sync", {
      method: "PUT",
      json: { base_url: baseUrl, token },
    });
    notifyHostedAccountChanged();
    return settings;
  },

  unlinkHostedAccount: async () => {
    const resp = await request<DeviceUnlinkResponse>("/api/settings/hosted-sync/session", {
      method: "DELETE",
    });
    notifyHostedAccountChanged();
    return resp;
  },
```

In `HostedAccountChip.tsx`:
1. Import `HOSTED_ACCOUNT_CHANGED_EVENT` and `notifyHostedAccountChanged` from `@/lib/api`.
2. Add a listener effect after the existing load effect:

```tsx
  useEffect(() => {
    if (!resolved || mode !== "local") return;
    const onChanged = () => void load();
    window.addEventListener(HOSTED_ACCOUNT_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(HOSTED_ACCOUNT_CHANGED_EVENT, onChanged);
  }, [resolved, mode, load]);
```

3. In the `DeviceLoginDialog` `onLinked` handler, add `notifyHostedAccountChanged();` after `setLoginOpen(false)` so the sibling chip picks up a fresh link too.

- [ ] **Step 4: Run the chip suites**

Run: `pnpm vitest run src/components/account/HostedAccountChip.test.tsx src/components/account/HostedAccountChip.hosted.test.tsx src/components/match/SyncCard.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/api.ts src/splitsmith/ui_static/src/components/account/HostedAccountChip.tsx src/splitsmith/ui_static/src/components/account/HostedAccountChip.test.tsx
git commit -m "fix(ui): account chip refetches after sync-settings mutations (#736)"
```

---

### Task 4: Revoke and clear the token on a base-URL repoint; tri-state hosted_revoked (#737)

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`put_hosted_sync_settings` ~13678-13712, `unlink_hosted_account` ~13849-13879, `DeviceUnlinkResponse` model ~4442-4450)
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (`DeviceUnlinkResponse` ~line 4324)
- Modify: `src/splitsmith/ui_static/src/components/account/HostedAccountChip.tsx` (line 84)
- Test: `tests/test_device_local_endpoints.py`

**Interfaces:**
- Consumes: `_build_device_client(base_url, token=...)` seam, `client.device_revoke_session()`, `_FakeHosted`/`_install_fake` test doubles (all exist).
- Produces: a base-URL change best-effort revokes the old token against the OLD host, then clears `prefs.hosted_token`. `DeviceUnlinkResponse.hosted_revoked: bool | None` - `True` revoked, `False` revoke attempted and failed, `None` nothing to revoke.

- [ ] **Step 1: Write the failing tests**

In `tests/test_device_local_endpoints.py`. First, extend `_install_fake` to record the base URLs clients are built against (backward compatible - existing callers unchanged):

```python
def _install_fake(monkeypatch, fake: _FakeHosted) -> None:
    def _build(base_url: str, *, token: str | None = None) -> HostedSyncClient:
        fake.built_against.append(base_url)
        return HostedSyncClient(
            http=httpx.Client(
                base_url=base_url,
                transport=httpx.MockTransport(fake.handle),
                headers={"Authorization": f"Bearer {token}"} if token else {},
            )
        )

    monkeypatch.setattr(server_mod, "_build_device_client", _build)
```

and add `self.built_against: list[str] = []` to `_FakeHosted.__init__`. Then the new tests:

```python
def test_repointing_the_base_url_revokes_and_clears_the_token(tmp_path: Path, monkeypatch) -> None:
    """A token minted by one host must not survive a repoint (#737): the
    revoke has to run against the OLD host, and the local copy goes."""
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    fake = _FakeHosted([dict(_APPROVED)])
    _install_fake(monkeypatch, fake)
    client.post(START)
    assert client.get(STATUS).json()["status"] == "approved"

    resp = client.put(
        "/api/settings/hosted-sync",
        json={"base_url": "https://staging.hosted.example", "token": None},
    )
    assert resp.status_code == 200, resp.text
    assert fake.revokes == 1
    # The revoke client was built against the OLD host, not the new one.
    assert fake.built_against[-1] == "https://hosted.example"
    assert resp.json()["token_set"] is False
    prefs = user_config.load_global_prefs()
    assert prefs.hosted_token is None
    assert prefs.hosted_account is None


def test_repoint_revoke_failure_still_clears_the_token(tmp_path: Path, monkeypatch) -> None:
    """Old host unreachable: the local copy still goes. A dead token in
    config.yaml is the worse failure, same rule as sign-out."""
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    fake = _FakeHosted([dict(_APPROVED)], revoke_status=500)
    _install_fake(monkeypatch, fake)
    client.post(START)
    assert client.get(STATUS).json()["status"] == "approved"

    resp = client.put(
        "/api/settings/hosted-sync",
        json={"base_url": "https://staging.hosted.example", "token": None},
    )
    assert resp.status_code == 200, resp.text
    assert user_config.load_global_prefs().hosted_token is None


def test_sign_out_with_nothing_linked_reports_no_revoke_needed(tmp_path: Path, monkeypatch) -> None:
    """hosted_revoked is tri-state (#737): null means there was nothing to
    revoke, so the UI must not warn about a revoke that never ran."""
    monkeypatch.setenv(user_config.ENV_HOME, str(tmp_path / "cfg"))
    client = _local_app(tmp_path)
    client.put("/api/settings/hosted-sync", json={"base_url": "https://hosted.example"})
    fake = _FakeHosted([])
    _install_fake(monkeypatch, fake)

    resp = client.delete(SESSION)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"cleared": True, "hosted_revoked": None}
    assert fake.revokes == 0
```

Also UPDATE the existing `test_repointing_the_base_url_clears_the_linked_account`: its final assertion `assert user_config.load_global_prefs().hosted_token == "sync-token-value"` (and the comment above it) now pins the OLD behavior - change it to `assert user_config.load_global_prefs().hosted_token is None` and reword the comment: the token is revoked-and-cleared on repoint; only same-host resubmits keep it. `test_resaving_the_same_base_url_keeps_the_linked_account` stays untouched - it pins the same-host contract.

- [ ] **Step 2: Run to verify the new tests fail**

Run: `pytest tests/test_device_local_endpoints.py -x -q`
Expected: the three new tests FAIL (revokes == 0, token still set, hosted_revoked False); the updated repoint test FAILS on the flipped assertion.

- [ ] **Step 3: Implement**

In `server.py`, `put_hosted_sync_settings`: capture old values before mutating, and insert the revoke-and-clear block between the `base_url` assignment and the existing `if req.token is not None:` block:

```python
        prefs = user_config.load_global_prefs()
        old_base_url = prefs.hosted_base_url
        old_token = prefs.hosted_token
        base_url_changed = old_base_url != req.base_url
        prefs.hosted_base_url = req.base_url
        # A token minted by one host is dead weight - and a live
        # credential - once this install points elsewhere (#737).
        # Best-effort revoke against the OLD host, then drop the local
        # copy. The ``token: null`` keeps-the-token contract is about
        # same-host resubmits and is untouched by this.
        if base_url_changed and old_token:
            if old_base_url:
                client = _build_device_client(old_base_url, token=old_token)
                try:
                    await run_in_threadpool(client.device_revoke_session)
                except (SyncClientError, httpx.HTTPError):
                    # Old host unreachable. The existing warning copy
                    # already points the operator at that host's account
                    # page; nothing more to do here.
                    pass
                finally:
                    client.close()
            prefs.hosted_token = None
```

(the rest of the handler - the `req.token` block, the account clear, the device_flow drop, the response - is unchanged).

`DeviceUnlinkResponse` model: change `hosted_revoked: bool` to `hosted_revoked: bool | None` and extend its docstring: `None` when nothing was linked, `False` when the revoke was attempted and failed.

`unlink_hosted_account`: change `revoked = False` to `revoked: bool | None = None` (the `try` still sets `True`, the `except` sets `False`).

SPA `api.ts`:

```ts
/** Response from DELETE /api/settings/hosted-sync/session (#719, #737).
 *  ``hosted_revoked`` is tri-state: true - the hosted side confirmed the
 *  revoke; false - a revoke was attempted and failed (warn); null -
 *  there was nothing to revoke (do not warn). */
export interface DeviceUnlinkResponse {
  cleared: boolean;
  hosted_revoked: boolean | null;
}
```

`HostedAccountChip.tsx` line 84: `if (!resp.hosted_revoked)` becomes `if (resp.hosted_revoked === false)`.

- [ ] **Step 4: Run the suites**

Run: `pytest tests/test_device_local_endpoints.py -q && cd src/splitsmith/ui_static && pnpm typecheck && pnpm vitest run src/components/account/HostedAccountChip.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_device_local_endpoints.py src/splitsmith/ui_static/src/lib/api.ts src/splitsmith/ui_static/src/components/account/HostedAccountChip.tsx
git commit -m "fix: revoke and clear desktop token on base-URL repoint; tri-state hosted_revoked (#737)"
```

---

### Task 5: Sweep expired device_authorizations on authorize (#735)

**Files:**
- Modify: `src/splitsmith/db/device_auth.py` (`authorize`, lines ~150-193; imports; module constants)
- Test: `tests/test_device_auth_store.py`

**Interfaces:**
- Consumes: `DeviceAuthorizationRow` model, `async_sessionmaker` factory (exist).
- Produces: every `authorize()` call deletes rows whose `expires_at` is more than `_PURGE_AFTER` (1 day) in the past, regardless of status. No new scheduler: the unauthenticated `POST /api/device/authorize` insert is the only growth source, so sweeping on insert bounds the table by construction (there is no periodic-job infrastructure in this repo, and the Procrastinate worker scales to zero, so a periodic task would not reliably fire).

- [ ] **Step 1: Write the failing tests**

In `tests/test_device_auth_store.py`, following the file's `_factory(tmp_path)` async idiom:

```python
async def test_authorize_sweeps_rows_a_day_past_expiry(tmp_path) -> None:
    """#735: the unauthenticated authorize endpoint is the only growth
    source, so sweeping on insert is what bounds the table."""
    factory = _factory(tmp_path)
    store = DeviceAuthStore(factory)
    stale = await store.authorize("stale-device")
    async with factory() as session:
        await session.execute(
            update(DeviceAuthorizationRow).values(
                expires_at=datetime.now(UTC) - timedelta(days=2)
            )
        )
        await session.commit()

    await store.authorize("fresh-device")

    async with factory() as session:
        rows = (await session.execute(select(DeviceAuthorizationRow))).scalars().all()
    assert [r.device_name for r in rows] == ["fresh-device"]
    assert stale.user_code not in {r.user_code for r in rows}


async def test_authorize_keeps_recently_expired_rows(tmp_path) -> None:
    """Rows inside the one-day grace stay: an expired-but-recent code
    still answers polls with a proper 'expired' verdict."""
    factory = _factory(tmp_path)
    store = DeviceAuthStore(factory)
    await store.authorize("recent-device")
    async with factory() as session:
        await session.execute(
            update(DeviceAuthorizationRow).values(
                expires_at=datetime.now(UTC) - timedelta(hours=1)
            )
        )
        await session.commit()

    await store.authorize("fresh-device")

    async with factory() as session:
        rows = (await session.execute(select(DeviceAuthorizationRow))).scalars().all()
    assert sorted(r.device_name for r in rows) == ["fresh-device", "recent-device"]
```

Add the imports the file lacks (`update` alongside its existing `select` import from sqlalchemy, `datetime`/`UTC`/`timedelta`). Mirror the file's existing async test decoration (pytest-asyncio marker or anyio - copy whatever its current tests use).

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_device_auth_store.py -q`
Expected: the two new tests FAIL (stale row survives).

- [ ] **Step 3: Implement**

In `device_auth.py`:
1. Extend the sqlalchemy import: `from sqlalchemy import delete, select, update`.
2. Module constant near `_USER_CODE_ATTEMPTS`:

```python
#: How long past ``expires_at`` a row may linger before an authorize call
#: sweeps it (#735). Inside the grace window an expired code still
#: answers polls with a real "expired" verdict; past it the row is pure
#: growth - consumed rows too, since the credential they minted lives in
#: ``desktop_tokens``, not here.
_PURGE_AFTER = timedelta(days=1)
```

3. In `authorize`, compute `now` explicitly and sweep inside the same session that inserts (first loop iteration and retries alike are fine - the sweep is idempotent):

```python
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=self._ttl_seconds)
        for _ in range(_USER_CODE_ATTEMPTS):
            ...
            try:
                async with self._session_factory() as session:
                    # #735: this is an unauthenticated public write and
                    # nothing else ever deletes rows. Sweeping stale rows
                    # on every insert bounds the table by construction.
                    await session.execute(
                        delete(DeviceAuthorizationRow).where(
                            DeviceAuthorizationRow.expires_at < now - _PURGE_AFTER
                        )
                    )
                    session.add(row)
                    await session.commit()
```

- [ ] **Step 4: Run the store, schema and route suites**

Run: `pytest tests/test_device_auth_store.py tests/test_device_auth_schema.py tests/test_device_auth_routes.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/db/device_auth.py tests/test_device_auth_store.py
git commit -m "fix: sweep expired device_authorizations on every authorize (#735)"
```

Note for the reviewer (put in the PR body, not code): the docker-marked device-auth suite (`pytest -m docker tests/test_device_auth_docker.py`) should be run locally before merge - this touches a query against live Postgres (db-change smoke policy).

---

### Task 6: Backend test gaps - countdown, bearer-required, slow_down (#738 items 1-3)

**Files:**
- Modify: `tests/test_device_local_endpoints.py` (line ~270)
- Test: `tests/test_device_auth_routes.py` (two new tests)

**Interfaces:** consumes existing fixtures only: `hosted_app` and `login()` from `tests/hosted_helpers.py`.

- [ ] **Step 1: Tighten the unfalsifiable countdown assertion**

In `test_second_start_while_one_is_pending_resumes_it`, change

```python
    assert 0 < body["expires_in"] <= 600
```

to

```python
    # Strict: a resumed start counts down the REMAINDER. A hardcoded 600
    # would pass <=; the strict bound is what makes this falsifiable.
    assert 0 < body["expires_in"] < 600
```

(Safe: the resume path computes `int(expires_at - monotonic_now)` and truncation makes it at most 599 whenever any time at all has passed between the two POSTs.)

- [ ] **Step 2: Add the two hosted-route tests**

In `tests/test_device_auth_routes.py`, mirroring the file's existing `hosted_app`/`login` usage and the request-body field names of its existing `/api/device/token` tests:

```python
def test_delete_session_with_cookie_but_no_bearer_is_400(hosted_app) -> None:
    """The 400 branch is the one auth-adjacent decision this route makes
    itself (#738): a browser session must not be able to unlink an
    arbitrary device by omitting the bearer."""
    client = hosted_app
    login(client)
    resp = client.delete("/api/device/session")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "a bearer token is required"


def test_token_poll_too_fast_surfaces_slow_down(hosted_app) -> None:
    """The hosted side of the slow_down path (#738): two polls inside the
    5s interval - the second must say slow_down, which the local server
    then remaps to pending (covered in test_device_local_endpoints)."""
    client = hosted_app
    start = client.post(
        "/api/device/authorize", json={"device_name": "gaspode"}
    ).json()
    first = client.post("/api/device/token", json={"device_code": start["device_code"]})
    assert first.status_code == 200
    assert first.json()["status"] == "pending"
    second = client.post("/api/device/token", json={"device_code": start["device_code"]})
    assert second.status_code == 200
    assert second.json()["status"] == "slow_down"
```

If the existing token tests in the file use different body keys or a helper for the authorize call, copy their exact shape.

- [ ] **Step 3: Run and verify they pass (and that item 1 is falsifiable)**

Run: `pytest tests/test_device_auth_routes.py tests/test_device_local_endpoints.py -q`
Expected: PASS. Spot-check falsifiability: temporarily hardcode `expires_in=600` in the resume branch of `start_device_login` (`server.py` ~13748), rerun `pytest tests/test_device_local_endpoints.py -k resumes -q`, confirm it now FAILS, then revert the hack.

- [ ] **Step 4: Commit**

```bash
git add tests/test_device_local_endpoints.py tests/test_device_auth_routes.py
git commit -m "test: pin device-flow countdown, bearer-required 400, hosted slow_down (#738)"
```

---

### Task 7: DesktopApprove distinguishes transport failure from 404 (#738 item 4)

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/DesktopApprove.tsx`
- Test: `src/splitsmith/ui_static/src/pages/DesktopApprove.test.tsx`

**Interfaces:**
- Consumes: `ApiError` (exported from `@/lib/api`; the test file already constructs `new ApiError(404, "not found")`).
- Produces: `Phase` union gains `"error"`; only `ApiError` with `status === 404` maps to `"not-found"`. Unknown/decided/expired stay deliberately indistinguishable (all are server 404s).

- [ ] **Step 1: Write the failing test**

In `DesktopApprove.test.tsx`, next to the existing `"renders one message for unknown, decided and expired alike"` test:

```tsx
it("distinguishes a transport failure from a decided/expired code", async () => {
  getDevicePending.mockRejectedValueOnce(new TypeError("failed to fetch"));
  renderAt("?code=ABCD-2345");
  expect(await screen.findByText(/could not check that code/i)).toBeInTheDocument();
  expect(screen.queryByText(/no longer waiting/i)).not.toBeInTheDocument();

  // Retry with the same code once the server is back.
  getDevicePending.mockResolvedValueOnce({
    user_code: "ABCD-2345",
    device_name: "gaspode",
    scope: "sync",
    created_at: "2026-08-11T10:00:00Z",
    expires_at: "2026-08-11T10:10:00Z",
  });
  await userEvent.click(screen.getByRole("button", { name: /try again/i }));
  expect(await screen.findByText(/gaspode/)).toBeInTheDocument();
});
```

Copy the pending-info fixture shape from the file's existing pending-phase tests.

- [ ] **Step 2: Run it to verify it fails**

Run: `pnpm vitest run src/pages/DesktopApprove.test.tsx`
Expected: FAIL - the TypeError collapses into the "no longer waiting" copy.

- [ ] **Step 3: Implement**

In `DesktopApprove.tsx`:
1. `import { api, ApiError } from "@/lib/api";` (extend the existing import).
2. `type Phase = "loading" | "manual" | "pending" | "approved" | "denied" | "not-found" | "error";`
3. Add state: `const [lastTriedCode, setLastTriedCode] = useState("");`
4. Failure classifier next to `Phase`:

```tsx
// A bare 404 is the server's deliberate unknown/decided/expired verdict
// and keeps the one indistinguishable message. Anything else (network
// down, 500, timeout) is NOT a verdict and must not read as one (#738).
function failurePhase(e: unknown): Phase {
  return e instanceof ApiError && e.status === 404 ? "not-found" : "error";
}
```

5. URL-code effect: before the `api.getDevicePending(urlCode)` call add `setLastTriedCode(urlCode);` and change the catch to:

```tsx
    .catch((e) => {
      if (!alive) return;
      setPhase(failurePhase(e));
    });
```

6. `lookupManualCode`: after `const value = raw.trim();` passes the empty check, add `setLastTriedCode(value);` and change its `catch` to `catch (e) { setPhase(failurePhase(e)); }`.
7. Render the error card alongside the not-found card:

```tsx
      {phase === "error" ? (
        <Card>
          <CardContent className="space-y-3 pt-6 text-sm">
            <p role="alert" className="text-ink-2">
              Could not check that code - the server did not answer. This
              is a connection problem, not a verdict on the code.
            </p>
            <div className="flex gap-2">
              <Button
                type="button"
                size="sm"
                onClick={() => void lookupManualCode(lastTriedCode)}
              >
                Try again
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={backToManualEntry}
              >
                Enter a different code
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}
```

8. Update the file's top doc comment: the "every lookup failure (whatever the cause)" sentence is no longer true - only 404s render the no-longer-waiting copy.

- [ ] **Step 4: Run the page suite**

Run: `pnpm vitest run src/pages/DesktopApprove.test.tsx && pnpm typecheck`
Expected: PASS, including the pre-existing 404-indistinguishability test.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/DesktopApprove.tsx src/splitsmith/ui_static/src/pages/DesktopApprove.test.tsx
git commit -m "fix(ui): approve screen separates transport failures from decided codes (#738)"
```

---

### Task 8: HostedAccountChip surfaces load failures (#738 item 5)

**Files:**
- Modify: `src/splitsmith/ui_static/src/components/account/HostedAccountChip.tsx`
- Test: `src/splitsmith/ui_static/src/components/account/HostedAccountChip.test.tsx`

**Interfaces:**
- Consumes: nothing new.
- Produces: on `getSyncSettings()` rejection the chip renders a muted retry button ("Account status unavailable") instead of the misleading sign-in button.

- [ ] **Step 1: Write the failing test**

```tsx
it("surfaces a settings load failure instead of the sign-in button (#738)", async () => {
  getSyncSettings.mockRejectedValueOnce(new Error("boom"));
  render(<HostedAccountChip />);
  const retry = await screen.findByRole("button", {
    name: /account status unavailable/i,
  });
  expect(
    screen.queryByRole("button", { name: /sign in/i }),
  ).not.toBeInTheDocument();

  getSyncSettings.mockResolvedValueOnce({
    base_url: "https://hosted.example",
    token_set: true,
    account: {
      id: "u1",
      email: "shooter@example.com",
      display_name: null,
      device_name: "gaspode",
    },
  });
  await userEvent.click(retry);
  expect(await screen.findByText(/shooter@example\.com/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pnpm vitest run src/components/account/HostedAccountChip.test.tsx`
Expected: FAIL - the failure currently renders the sign-in button.

- [ ] **Step 3: Implement**

In `HostedAccountChip.tsx`:
1. Add state: `const [loadFailed, setLoadFailed] = useState(false);`
2. Rewrite `load` (this also retires the silent-swallow that hid #734's 404):

```tsx
  const load = useCallback(async () => {
    try {
      const settings = await api.getSyncSettings();
      setAccount(settings.account);
      setLoadFailed(false);
    } catch {
      // A transient failure must not masquerade as "not signed in"
      // (#738): a genuinely linked operator seeing the sign-in button
      // is worse than an explicit unavailable state.
      setLoadFailed(true);
    } finally {
      setLoaded(true);
    }
  }, []);
```

3. In the pill, add a `loadFailed` branch ahead of the `account` ternary:

```tsx
        {loadFailed ? (
          <button
            type="button"
            onClick={() => void load()}
            title="Could not load the linked-account status - click to retry"
            className="text-[0.8125rem] text-muted transition-colors hover:text-ink"
          >
            Account status unavailable - retry
          </button>
        ) : account ? (
          ...existing account JSX...
        ) : (
          ...existing sign-in Button...
        )}
```

- [ ] **Step 4: Run the chip suites**

Run: `pnpm vitest run src/components/account/HostedAccountChip.test.tsx src/components/account/HostedAccountChip.hosted.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/components/account/HostedAccountChip.tsx src/splitsmith/ui_static/src/components/account/HostedAccountChip.test.tsx
git commit -m "fix(ui): account chip shows an explicit unavailable state on load failure (#738)"
```

---

### Task 9: Full gates, PR

- [ ] **Step 1: Full backend gate**

Run from repo root: `ruff check . && black --check . && pytest -q`
Expected: ruff/black clean; pytest green modulo the known ~21 env-dependent local failures - compare any failure against a fresh `origin/main` run before attributing it; fix anything this branch introduced.

- [ ] **Step 2: Full SPA gate**

Run: `cd src/splitsmith/ui_static && pnpm typecheck && pnpm test && pnpm eslint src`
Expected: clean.

- [ ] **Step 3: ASCII sweep of added lines**

Run: `git diff origin/main | grep '^+' | grep -nP '[\x{2010}-\x{2015}\x{2018}-\x{201F}\x{2026}\x{00A0}\x{200B}]' ; git diff origin/main | grep '^+' | grep -n ' -- '`
Expected: no output (no em dashes, curly quotes, double-dash in new text).

- [ ] **Step 4: Open the PR**

```bash
git push -u origin fix/hardening-device-flow
gh pr create --title "fix: device-flow and shell hardening wave (#734 #735 #736 #737 #738 #739 #725)" --body "$(cat <<'EOF'
Hardening wave PR 1 of 2 (post-v0.25.0 plan).

- #734: AuthGate holds the route tree until /api/server/features resolves; no surface ever mounts on the provisional "local" default.
- #725 + #739: retired health.bound checks deleted from the SPA; /_design and /promote-review reachable; catch-all is a synchronous Navigate, removing the async-redirect race class.
- #736: hosted-account chip refetches via a splitsmith:hosted-account-changed CustomEvent after settings save / unlink / device link; both render sites stay in agreement.
- #737: repointing the base URL best-effort revokes the old token against the OLD host, then clears it; hosted_revoked is tri-state (true/false/null).
- #735: authorize() sweeps device_authorizations rows a day past expiry - insert-time sweep bounds the table without new scheduler infrastructure.
- #738: strict resume-countdown assertion, bearer-required 400 test, hosted slow_down test, approve screen separates transport failures from 404 verdicts, chip surfaces settings-load failures.

Deliberately NOT in this PR: rate-limiting GET /api/device/pending (noted in #735 as non-urgent; code space 30^8 over 10 minutes).

Before merge: run `pytest -m docker tests/test_device_auth_docker.py` locally (db-touching change policy).

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_013p2JUqQX6BRGjUfqFoPVYi
EOF
)"
```

Do not enable auto-merge without watching checks: main has no required checks, so `--auto` merges immediately; use `gh run watch` to actually wait for green.
