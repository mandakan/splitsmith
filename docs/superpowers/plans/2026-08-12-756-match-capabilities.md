# PR B: Match Capability Model + SPA Sweep (#756) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mirror guard's five hand-listed exception regexes with one route-to-required-capability table that drives both the 403 decision and a `capabilities: [...]` payload field, then gate every match page's write affordances on the capability instead of `origin`.

**Architecture:** New `splitsmith/ui/capabilities.py` owns the capability names (`edit`, `review`, `share_manage`), the per-origin and per-share-scope capability sets, and `required_capability(method, rest)`. `_match_id_alias` consults it for the 403 and pins the computed set in a ContextVar; the three origin-bearing payloads (project, shooter list, beep queue) serialize it. The SPA gains a `MatchCapability` type + `capabilityDenied()` helper and every match-scoped page gates on `edit`. Builds on PR A (`current_share_scope` ContextVar). Spec: `docs/superpowers/specs/2026-08-12-share-write-foundation-design.md`.

**Tech Stack:** FastAPI middleware, pytest; React + TypeScript, vitest, pnpm (ui_static is pnpm-only - never introduce npm/package-lock.json).

## Global Constraints

- New text (comments, docstrings, copy) uses ASCII punctuation only and single `-` dashes - never em dashes, never `--`.
- Python commands via `uv run`; SPA commands via `pnpm` from `src/splitsmith/ui_static/`.
- No new dependencies.
- Guard behavior parity: every verdict `tests/test_mirror_read_only.py` pins today must survive the refactor unchanged.
- `origin` remains legitimate for provenance (picker flag) and behavior (#821 proxy-poll arming, mirror media surface) - only writability tests on it are replaced.
- Gates before PR: `uv run ruff check .`, `uv run black --check .`, full `uv run pytest`; SPA: `pnpm typecheck`, `pnpm test`, scoped `pnpm eslint` over touched files.
- ~21 env-dependent local pytest failures are known-green in CI; verify against main before attributing.

---

### Task 0: Branch (stacked on PR A)

- [ ] This PR consumes PR A's `current_share_scope`. Branch off PR A's head:

```bash
git switch feat/779-share-readonly-defense
git switch -c feat/756-match-capabilities
```

Open the PR (Task 7) only after PR A has merged, with base `main`. If PR A is still open when this plan finishes, open PR B with base `feat/779-share-readonly-defense` and retarget to `main` after A merges (the stacked-PR pattern used for the auth swap).

---

### Task 1: Capability module + unit tests

**Files:**
- Create: `src/splitsmith/ui/capabilities.py`
- Test: `tests/test_capabilities.py`

**Interfaces:**
- Produces: `EDIT = "edit"`, `REVIEW = "review"`, `SHARE_MANAGE = "share_manage"`; `capabilities_for_origin(origin: str | None) -> frozenset[str]`; `share_scope_capabilities(scope: str | None) -> frozenset[str]`; `required_capability(method: str, rest: str) -> str | None`. Tasks 2-3 consume all of these.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_capabilities.py`:

```python
"""#756: the capability table is the single encoding of who may write
what. These tests pin (a) the per-origin and per-scope sets and (b) the
route classification, including exact parity with the five exception
regexes the old mirror guard hand-listed."""

from __future__ import annotations

import pytest

from splitsmith.ui.capabilities import (
    EDIT,
    REVIEW,
    SHARE_MANAGE,
    capabilities_for_origin,
    required_capability,
    share_scope_capabilities,
)


def test_origin_capability_sets() -> None:
    assert capabilities_for_origin("hosted") == {EDIT, REVIEW, SHARE_MANAGE}
    assert capabilities_for_origin("desktop") == {REVIEW, SHARE_MANAGE}
    assert capabilities_for_origin("local") == {EDIT, REVIEW}
    # None means "no aliased match bound" (legacy bare-path local traffic)
    # and gets the local set - same fallback get_project uses for origin.
    assert capabilities_for_origin(None) == {EDIT, REVIEW}


def test_share_scope_capability_sets() -> None:
    assert share_scope_capabilities("read") == frozenset()
    # Unknown or absent scopes fail closed - a typo'd scope grants nothing.
    assert share_scope_capabilities("coach") == frozenset()
    assert share_scope_capabilities(None) == frozenset()


@pytest.mark.parametrize(
    ("method", "rest", "expected"),
    [
        # Safe methods never need a capability.
        ("GET", "shooters/anna/project", None),
        ("HEAD", "match/shooters", None),
        ("OPTIONS", "match/stage/1/compare", None),
        # Share management - any method, base and sub-paths.
        ("POST", "match/shares", SHARE_MANAGE),
        ("DELETE", "match/shares/01ABC", SHARE_MANAGE),
        # The review set - exact parity with the old exception regexes.
        ("POST", "match/beep-queue/confirm", REVIEW),
        ("POST", "shooters/anna/stages/3/videos/v1/beep", REVIEW),
        ("POST", "shooters/anna/stages/3/audit/accept", REVIEW),
        ("POST", "shooters/anna/stages/3/attention", REVIEW),
        ("PATCH", "shooters/anna/stages/3/shots/2/coach", REVIEW),
        ("POST", "shooters/anna/stages/3/coach/reclassify", REVIEW),
        # Method mismatches fall through to EDIT - the old guard was
        # method-gated per regex and the table must stay that strict.
        ("DELETE", "shooters/anna/stages/3/videos/v1/beep", EDIT),
        ("POST", "shooters/anna/stages/3/shots/2/coach", EDIT),
        ("PATCH", "shooters/anna/stages/3/coach/reclassify", EDIT),
        # Beep re-detect was never mirror-writable (only .../beep is).
        ("POST", "shooters/anna/stages/3/videos/v1/beep/detect", EDIT),
        # Unlisted writes require EDIT - new routes fail over-restricted,
        # never silently writable.
        ("POST", "match/shooters", EDIT),
        ("PUT", "match/stages", EDIT),
        ("DELETE", "match/shooters/anna", EDIT),
        ("POST", "shooters/anna/stages/3/export", EDIT),
    ],
)
def test_required_capability(method: str, rest: str, expected: str | None) -> None:
    assert required_capability(method, rest) == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_capabilities.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'splitsmith.ui.capabilities'`

- [ ] **Step 3: Write the module**

Create `src/splitsmith/ui/capabilities.py`:

```python
"""Match capability model (#756) + share-scope mapping (#779).

One encoding of who may write what. The alias middleware consults
``required_capability`` for its 403 and serializes the computed set on
match payloads, so enforcement and what the SPA renders can never
disagree - the same single-source rationale that put
READ_ONLY_MIRROR_MESSAGE in one constant.

Capabilities:

- ``edit``: the full mutation surface (trims, stages, shooters, ingest,
  exports). The default requirement for any write route not classified
  below - new write routes fail over-restricted, never silently
  writable.
- ``review``: the phone-triage writes mirrors accept (slices 3-5).
- ``share_manage``: the match/shares management routes.
"""

from __future__ import annotations

import re

EDIT = "edit"
REVIEW = "review"
SHARE_MANAGE = "share_manage"


def capabilities_for_origin(origin: str | None) -> frozenset[str]:
    """Capability set of an authenticated (non-share) request, derived
    from where the match's canonical data lives. Today origin fully
    determines writability; when the #631 transfer endgame lands, this
    function keys off sync state instead and no caller changes."""
    if origin == "desktop":
        # A mirror desktop still owns: review actions sync back, editing
        # stays on desktop. Share management is the point of exposing
        # the mirror hosted-side.
        return frozenset({REVIEW, SHARE_MANAGE})
    if origin == "hosted":
        return frozenset({EDIT, REVIEW, SHARE_MANAGE})
    # "local" and None (legacy bare-path local traffic): one operator,
    # full control, no share surface to manage.
    return frozenset({EDIT, REVIEW})


# Share-token scopes -> capability sets. 'read' is the only scope shipped
# (#779); a write-capable scope (e.g. 'coach') is one new entry here.
_SHARE_SCOPE_CAPABILITIES: dict[str, frozenset[str]] = {
    "read": frozenset(),
}


def share_scope_capabilities(scope: str | None) -> frozenset[str]:
    """Capability set a share token's scope grants. Unknown scopes get
    nothing - fail closed."""
    return _SHARE_SCOPE_CAPABILITIES.get(scope or "", frozenset())


# The review-writable route shapes, verbatim from the retired per-slice
# mirror regexes (server.py) - method-gated exactly as the old guard was.
_REVIEW_ROUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"^match/beep-queue/confirm$")),
    ("POST", re.compile(r"^shooters/[^/]+/stages/\d+/videos/[^/]+/beep$")),
    ("POST", re.compile(r"^shooters/[^/]+/stages/\d+/(audit/accept|attention)$")),
    ("PATCH", re.compile(r"^shooters/[^/]+/stages/\d+/shots/\d+/coach$")),
    ("POST", re.compile(r"^shooters/[^/]+/stages/\d+/coach/reclassify$")),
)


def required_capability(method: str, rest: str) -> str | None:
    """Capability a request needs, or None for safe methods.

    ``rest`` is the alias-relative path (what follows
    ``/api/matches/{id}/``), the same string the old guard matched.
    """
    if method in ("GET", "HEAD", "OPTIONS"):
        return None
    if rest == "match/shares" or rest.startswith("match/shares/"):
        return SHARE_MANAGE
    for allowed_method, pattern in _REVIEW_ROUTES:
        if method == allowed_method and pattern.match(rest) is not None:
            return REVIEW
    return EDIT
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_capabilities.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/capabilities.py tests/test_capabilities.py
git commit -m "feat: match capability model - one table for guard and payload (#756)"
```

---

### Task 2: Guard refactor - `_match_id_alias` consumes the table

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`_match_id_alias` ~line 6455-6558, mirror regexes ~line 6429-6446, ContextVars ~line 1030-1056)
- Test: `tests/test_mirror_read_only.py` (parity - existing tests unchanged), plus one new forward-compat test there

**Interfaces:**
- Consumes: Task 1's module; `current_share_request` (existing), `current_share_scope` (PR A).
- Produces: `current_match_capabilities: ContextVar[frozenset[str] | None]` in server.py; the guard 403s from the table. Task 3 serializes the ContextVar.

- [ ] **Step 1: Write the failing forward-compat test**

Append to `tests/test_mirror_read_only.py`:

```python
def test_guard_follows_capability_set_not_origin(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#756 forward-compat: the 403 must be derived from the capability
    set, not from origin directly - flipping the set for a desktop
    mirror (what the #631 transfer endgame will do) must open the guard
    with no other change."""
    import splitsmith.ui.server as server_module

    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed_mirror(client, "mirror-fc", "Forward Compat")

    monkeypatch.setattr(
        server_module,
        "capabilities_for_origin",
        lambda origin: frozenset({"edit", "review", "share_manage"}),
    )
    resp = client.post(_alias_url("mirror-fc", "match/shooters"), json={"name": "Anna"})
    # The middleware no longer blocks it; whatever the handler says, it
    # must not be the mirror 403.
    assert resp.status_code != 403, resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mirror_read_only.py -k follows_capability -v`
Expected: FAIL - `capabilities_for_origin` is not yet an attribute of the server module (AttributeError from monkeypatch), or the request still 403s.

- [ ] **Step 3: Refactor the guard**

In `src/splitsmith/ui/server.py`:

1. Import near the other splitsmith imports:

```python
from splitsmith.ui.capabilities import (
    capabilities_for_origin,
    required_capability,
    share_scope_capabilities,
)
```

2. Next to `current_match_origin` (~line 1049), add:

```python
# The bound match's capability set (#756), computed by the alias
# middleware from the same origin fact (or, on share requests, from the
# token's scope) that decides the 403 - handlers serialize it so the SPA
# gates affordances on the same truth the guard enforces. None outside
# an aliased request.
current_match_capabilities: ContextVar[frozenset[str] | None] = ContextVar(
    "splitsmith_current_match_capabilities", default=None
)
```

3. Delete the four `_mirror_*_re` regex definitions and their comments (~lines 6429-6446) - they move verbatim into `capabilities.py` (Task 1).

4. In `_match_id_alias`, replace the `if (owner_row.origin == "desktop" ... )` guard block with:

```python
            # Capability gate (#756, formerly the read-only mirror gate,
            # #631 Task 6). One table decides both this 403 and the
            # ``capabilities`` payload field - see capabilities.py. On a
            # share request the set comes from the token's scope instead
            # of the origin (#779); share traffic is GET-only at the
            # share alias, so the gate is payload-only there today.
            if current_share_request.get():
                match_capabilities = share_scope_capabilities(current_share_scope.get())
            else:
                match_capabilities = capabilities_for_origin(owner_row.origin)
            needed = required_capability(request.method, rest)
            if needed is not None and needed not in match_capabilities:
                return JSONResponse(status_code=403, content={"detail": "read_only_mirror"})
```

5. In the local-mode `else` branch, after `match_origin = "local"`, add:

```python
            match_capabilities = capabilities_for_origin("local")
```

(No guard in the local branch - local mode has no restriction today and this refactor is behavior-preserving.)

6. Thread the ContextVar through the same set/reset dance as the others:

```python
        origin_token = current_match_origin.set(match_origin)
        capabilities_token = current_match_capabilities.set(match_capabilities)
        try:
            return await call_next(request)
        finally:
            current_match_capabilities.reset(capabilities_token)
            current_match_origin.reset(origin_token)
            current_match_id.reset(id_token)
            current_match_root.reset(root_token)
```

- [ ] **Step 4: Run the parity net + new test**

Run: `uv run pytest tests/test_mirror_read_only.py tests/test_share_routes.py -v`
Expected: every pre-existing test PASSES unchanged (that is the parity claim) plus the new forward-compat test. If any existing mirror test fails, the table diverges from the old regexes - fix `capabilities.py`, not the test.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_mirror_read_only.py
git commit -m "refactor: mirror guard driven by the capability table (#756)"
```

---

### Task 3: Serialize `capabilities` on the three origin-bearing payloads

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`get_project` ~line 6979-6988; the `/api/match/shooters` handler; the beep-queue handler)
- Test: `tests/test_mirror_read_only.py`, `tests/test_share_routes.py`

**Interfaces:**
- Consumes: `current_match_capabilities` (Task 2).
- Produces: `capabilities: list[str]` (sorted) on the project payload, the shooter-list payload, and the beep-queue payload; empty list on share-served responses. Tasks 4-6 consume these client-side.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mirror_read_only.py`:

```python
def test_payload_capabilities_match_guard(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """#756: the serialized set is the same one the guard enforces - a
    mirror advertises review+share_manage, never edit."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed_mirror(client, "mirror-caps", "Caps Match")

    shooters = client.get(_alias_url("mirror-caps", "match/shooters"))
    assert shooters.status_code == 200, shooters.text
    assert shooters.json()["capabilities"] == ["review", "share_manage"]

    queue = client.get(_alias_url("mirror-caps", "match/beep-queue"))
    assert queue.status_code == 200, queue.text
    assert queue.json()["capabilities"] == ["review", "share_manage"]
```

(If the beep-queue GET path differs from `match/beep-queue`, use the path the existing beep-queue tests in the repo hit - `grep -rn "beep-queue" tests/` shows it.)

Append to `tests/test_share_routes.py`:

```python
def test_share_payload_capabilities_empty(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """#779/#756: a read-scoped share response advertises no
    capabilities - the anonymous surface renders zero write CTAs from
    data, not from route-shape assumptions."""
    token = _setup_shared_match(hosted_env, hosted_app)
    client, _ = hosted_app
    resp = client.get(_share_url(token, "match/shooters"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["capabilities"] == []

    project = client.get(_share_url(token, f"shooters/{SLUG}/project"))
    assert project.status_code == 200, project.text
    assert project.json()["capabilities"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mirror_read_only.py -k payload_capabilities -v` and `uv run pytest tests/test_share_routes.py -k capabilities_empty -v`
Expected: FAIL with KeyError `'capabilities'`.

- [ ] **Step 3: Serialize the field**

In `get_project` (server.py ~6979), directly under the `payload["origin"] = ...` line:

```python
        payload["capabilities"] = sorted(
            current_match_capabilities.get() or capabilities_for_origin(None)
        )
```

Find the shooter-list and beep-queue handlers (`grep -n '"origin"' src/splitsmith/ui/server.py` - they set `origin` from `current_match_origin` the same way `get_project` does) and add the identical line to each response payload.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_mirror_read_only.py tests/test_share_routes.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_mirror_read_only.py tests/test_share_routes.py
git commit -m "feat: serialize match capabilities on project, shooter-list, beep-queue payloads (#756)"
```

---

### Task 4: SPA capability type + MatchShell context + banner

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (~lines 276-280, 1462, 1625-1631, 1770-1779)
- Modify: `src/splitsmith/ui_static/src/components/match/MatchShell.tsx` (~lines 111-121, 245-248, 333-337, 607-615, 696-705)
- Modify: `src/splitsmith/ui_static/src/components/share/ShareShell.tsx` (~line 141)
- Test: `src/splitsmith/ui_static/src/components/match/MatchShell.test.tsx`, new `src/splitsmith/ui_static/src/lib/capabilities.test.ts`

**Interfaces:**
- Consumes: server payload field (Task 3).
- Produces: `export type MatchCapability = "edit" | "review" | "share_manage"`; `export function capabilityDenied(caps, cap): boolean` in `api.ts`; `capabilities: MatchCapability[] | null` on `MatchShellOutletContext`. Tasks 5-6 consume these.

- [ ] **Step 1: Write the failing tests**

Create `src/splitsmith/ui_static/src/lib/capabilities.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { capabilityDenied } from "./api";

describe("capabilityDenied", () => {
  it("denies only when the set is known and lacks the capability", () => {
    expect(capabilityDenied(["review", "share_manage"], "edit")).toBe(true);
    expect(capabilityDenied(["edit", "review"], "edit")).toBe(false);
    expect(capabilityDenied([], "edit")).toBe(true);
  });

  it("never denies while the set is unknown (null/undefined)", () => {
    // Pages keep their optimistic first render until the shell's fetch
    // resolves - unknown must not flash-hide controls on editable
    // matches.
    expect(capabilityDenied(null, "edit")).toBe(false);
    expect(capabilityDenied(undefined, "edit")).toBe(false);
  });
});
```

Update `MatchShell.test.tsx`: the `setUpApiWithOrigin` helper (~line 255-283) mocks `listMatchShooters` - add a `capabilities` field to its mocked response, parameterized alongside origin: `["review", "share_manage"]` when the origin argument is `"desktop"`, `["edit", "review", "share_manage"]` otherwise. Then add one test to the mirror-banner describe block:

```tsx
  it("keeps the banner off when a desktop-origin match has edit capability", async () => {
    // #756 forward-compat: the banner keys off the capability set, not
    // provenance - a fully-transferred mirror must not claim read-only.
    setUpApiWithOrigin("desktop", {
      capabilities: ["edit", "review", "share_manage"],
    });
    renderShell();
    await screen.findByText(/shooters/i);
    expect(screen.queryByText(/synced from a desktop install/i)).toBeNull();
  });
```

(Adapt `setUpApiWithOrigin`'s signature to take the optional capabilities override, defaulting to the origin-derived set above; `renderShell` and the query text mirror the existing banner tests at ~line 386-411.)

- [ ] **Step 2: Run tests to verify they fail**

Run (from `src/splitsmith/ui_static/`): `pnpm test -- capabilities MatchShell`
Expected: FAIL - `capabilityDenied` is not exported; the new banner test fails because the banner still keys off origin.

- [ ] **Step 3: Implement types, helper, context, banner**

In `api.ts`, under the `MatchOrigin` type (~1462):

```ts
/** Server-derived per-match capability set (#756), computed next to the
 *  403 guard so payload and enforcement can never disagree. Gate write
 *  affordances on these, never on `origin` - origin is provenance
 *  (picker flag) and media-surface behavior (#821), not writability. */
export type MatchCapability = "edit" | "review" | "share_manage";

/** True when the capability set is KNOWN and lacks `cap`. Null or
 *  undefined means "not loaded yet" and denies nothing - pages keep
 *  their current optimistic render until the shell's first fetch
 *  resolves, exactly as the origin-based gates behaved. */
export function capabilityDenied(
  caps: MatchCapability[] | null | undefined,
  cap: MatchCapability,
): boolean {
  return Array.isArray(caps) && !caps.includes(cap);
}
```

Payload types: add `capabilities: MatchCapability[];` to `ShooterListResponse` (~1625) and `BeepQueueResponse` (~1770), and `capabilities?: MatchCapability[];` to `MatchProject` (~276, optional for the same reason `origin` is - mutating routes echo the doc without it).

In `MatchShell.tsx`:

```tsx
  const [capabilities, setCapabilities] = useState<MatchCapability[] | null>(null);
```

set it next to `setOrigin(r.origin)`:

```tsx
        setCapabilities(r.capabilities);
```

add `capabilities: MatchCapability[] | null;` to `MatchShellOutletContext` (with a doc comment pointing pages at `capabilityDenied(capabilities, "edit")` instead of origin) and thread it through the context object (~696-705). Banner condition (~607):

```tsx
      {capabilityDenied(capabilities, "edit") ? (
```

(the JSX body and `READ_ONLY_MIRROR_MESSAGE` stay as they are).

In `ShareShell.tsx` (~141), add `capabilities: []` to the context object it builds - the share surface advertises no capabilities today; when write-scoped tokens land, this reads the share payload's field instead.

- [ ] **Step 4: Run typecheck + tests**

Run (from `src/splitsmith/ui_static/`): `pnpm typecheck && pnpm test`
Expected: typecheck surfaces every mock/fixture that now lacks `capabilities` on `ShooterListResponse`/`BeepQueueResponse` - add the field to each (use `["edit", "review"]` for local-origin fixtures, `["review", "share_manage"]` for desktop ones). Then all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/api.ts src/splitsmith/ui_static/src/lib/capabilities.test.ts src/splitsmith/ui_static/src/components/match/MatchShell.tsx src/splitsmith/ui_static/src/components/match/MatchShell.test.tsx src/splitsmith/ui_static/src/components/share/ShareShell.tsx
git commit -m "feat(ui): MatchCapability type, capabilityDenied helper, capability-keyed banner (#756)"
```

Then `git add` any test fixtures updated for the new required fields in the same commit before running it, or as an immediate `fix(ui)` follow-up commit.

---

### Task 5: Sweep Home + Ingest

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Home.tsx` (~lines 82-90, 305-316, 336, 344, 349, 536-538, 682-696, 723-740)
- Modify: `src/splitsmith/ui_static/src/pages/Ingest.tsx` (write affordances at ~240, 252, 268, 312, 362)
- Test: new `src/splitsmith/ui_static/src/pages/Home.capabilities.test.tsx`

**Interfaces:**
- Consumes: `capabilityDenied`, `MatchShellOutletContext.capabilities` (Task 4), `MatchProject.capabilities`.
- Produces: no new interfaces - page-local gating only.

- [ ] **Step 1: Write the failing Home test**

Create `src/splitsmith/ui_static/src/pages/Home.capabilities.test.tsx`. Scaffolding: mock `@/lib/api` by spreading the actual module and overriding the fetches Home's shell context needs (copy the `vi.mock` + `MemoryRouter`/`Route` pattern from `Ingest.proxyPoll.test.tsx:22-80`); render `<Home />` as a child route of a parent element that provides the outlet context:

```tsx
function OutletCtx({ ctx }: { ctx: MatchShellOutletContext }) {
  return <Outlet context={ctx} />;
}

function renderHome(ctx: Partial<MatchShellOutletContext>) {
  const base: MatchShellOutletContext = {
    project: projectFixture,
    health: null,
    shooters: [shooterFixture],
    refresh: () => {},
    origin: "desktop",
    capabilities: ["review", "share_manage"],
    ...ctx,
  };
  return render(
    <ConfirmProvider>
      <MemoryRouter initialEntries={["/match/m1"]}>
        <Routes>
          <Route path="/match/:matchId" element={<OutletCtx ctx={base} />}>
            <Route index element={<Home />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ConfirmProvider>,
  );
}
```

(Fill `projectFixture`/`shooterFixture` from the fixtures `MatchShell.test.tsx` already uses; include every field the context type requires - if `MatchShellOutletContext` has fields beyond the six above, mirror them from an existing test's context.)

Tests:

```tsx
it("hides edit entry points when the capability set lacks edit", () => {
  renderHome({ capabilities: ["review", "share_manage"] });
  expect(screen.queryByRole("button", { name: /edit stages/i })).toBeNull();
  expect(screen.queryByRole("button", { name: /add a squadmate/i })).toBeNull();
});

it("shows edit entry points on a desktop-origin match WITH edit (forward compat)", () => {
  // The #631 transfer endgame: origin stays "desktop" forever, but a
  // completed transfer grants edit - the page must not test origin.
  renderHome({ origin: "desktop", capabilities: ["edit", "review", "share_manage"] });
  expect(screen.getByRole("button", { name: /edit stages/i })).toBeInTheDocument();
});
```

(Adjust the queried names to the actual accessible names - the "Edit Stages" button text and the `aria-label="Add a squadmate"` tile are in the excerpts at Home.tsx:305-316 and 682-696; which variant renders depends on the fixture's shooters, so target whichever variant the fixture produces and query its hidden affordances.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm test -- Home.capabilities`
Expected: the forward-compat test FAILS (Home still derives from `ctx.origin === "desktop"`, so it hides the buttons despite edit being granted).

- [ ] **Step 3: Convert Home**

In `Home.tsx` (~82-90), replace:

```tsx
  const readOnlyMirror = ctx?.origin === "desktop";
```

with:

```tsx
  // #756: gate on the server-derived capability, not origin - a mirror
  // that completes its transfer becomes editable with no change here.
  // Origin stays provenance-only.
  const editDenied = capabilityDenied(ctx?.capabilities, "edit");
```

and rename every `readOnlyMirror` usage in the file (`~305, 336, 344, 349, 536, 682, 723, 731`) to `editDenied`, including the prop passed into the page variants. Update the intent comment to say the gate is the `edit` capability.

- [ ] **Step 4: Convert Ingest**

In `Ingest.tsx`, derive once near the top of the component (Ingest fetches its own `project: MatchProject`):

```tsx
  // #756: mirrors (and any future non-editable match) get a read-only
  // Ingest - the page's whole surface is edit-class writes. Disable
  // with the banner's reason rather than hide: an Ingest page with no
  // controls at all would read as broken (the issue's per-surface rule).
  const editDenied = capabilityDenied(project?.capabilities, "edit");
```

Then gate the five write affordances (scan folder ~240, scan dropped files ~252, move-to-shooter ~268, reassign ~312, remove ~362): disable each control when `editDenied` and surface `READ_ONLY_MIRROR_MESSAGE` once near the top of the control area (a single muted note, not per-control). Leave the `project.origin === "desktop"` check inside `anyProxyPending` (~187) untouched - that is #821 media behavior, not writability.

- [ ] **Step 5: Run tests + typecheck**

Run: `pnpm typecheck && pnpm test -- Home Ingest`
Expected: all PASS, including the untouched `Ingest.proxyPoll.test.tsx`.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/Home.tsx src/splitsmith/ui_static/src/pages/Home.capabilities.test.tsx src/splitsmith/ui_static/src/pages/Ingest.tsx
git commit -m "feat(ui): Home and Ingest gate edit affordances on the capability set (#756)"
```

---

### Task 6: Sweep BeepReview, Export, MatchExport, Compare

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/useBeepQueue.ts` (~224-245)
- Modify: `src/splitsmith/ui_static/src/pages/BeepReview.tsx` and `src/splitsmith/ui_static/src/pages/MobileBeepReview.tsx` (re-detect affordance)
- Modify: `src/splitsmith/ui_static/src/pages/Export.tsx` (~327, 460, 516), `src/splitsmith/ui_static/src/pages/MatchExport.tsx` (~164), `src/splitsmith/ui_static/src/pages/Compare.tsx` (~539, 592)
- Test: `src/splitsmith/ui_static/src/lib/useBeepQueue.test.tsx`

**Interfaces:**
- Consumes: `capabilityDenied`, `BeepQueueResponse.capabilities`, `MatchShellOutletContext.capabilities`.
- Produces: `useBeepQueue` return gains `editDenied: boolean`.

- [ ] **Step 1: Write the failing hook test**

In `useBeepQueue.test.tsx`, extend the existing "reports isMirror" test (~73-78) - its mocked queue response already carries `origin: "desktop"`; give it `capabilities: ["review", "share_manage"]` (Task 4's typecheck sweep already added a value; set it to this) and assert:

```tsx
    expect(result.current.editDenied).toBe(true);
```

Add a sibling case with `capabilities: ["edit", "review"]` asserting `editDenied === false`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm test -- useBeepQueue`
Expected: FAIL - `editDenied` is undefined on the hook return.

- [ ] **Step 3: Implement**

In `useBeepQueue.ts` return object (next to `isMirror`):

```ts
    // #756: re-detect fires a detection job against source media - an
    // edit-class write the mirror guard 403s. Confirm/override are the
    // review writes and stay live regardless.
    editDenied: capabilityDenied(data?.capabilities, "edit"),
```

In `BeepReview.tsx` and `MobileBeepReview.tsx`: disable the re-detect control when `editDenied`, with `READ_ONLY_MIRROR_MESSAGE` as its tooltip/aria reason (disable, not hide - a missing re-detect next to a live confirm would read as a bug). Confirm and manual override stay ungated (review-class, mirrors accept them).

In `Export.tsx`: from the outlet context, `const editDenied = capabilityDenied(ctx?.capabilities, "edit")`; disable the set-compare-camera control (~327) and the two generate buttons (~460, ~516) when set, each with `READ_ONLY_MIRROR_MESSAGE` as the disabled reason (Export pages are the spec's canonical disable-with-reason surface - a bare page with no export button looks broken).

In `MatchExport.tsx` (~164): same treatment for the compare-grid generate button.

In `Compare.tsx` (~539, 592): the rebuild-trim-caches button is currently mounted under `!shareView` - additionally hide it when `capabilityDenied(ctx?.capabilities, "edit")` (hide, not disable: it is one action among several on a page whose value is reading).

Review.tsx / PromoteReview.tsx are deliberately NOT swept: their `saveFixtureAudit` (PUT `/api/fixture/audit?path=`) is a dev fixture tool outside the match alias - the guard never 403s it, so capability-gating it would be a category error. Leave as-is.

- [ ] **Step 4: Run the SPA suite**

Run: `pnpm typecheck && pnpm test`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/useBeepQueue.ts src/splitsmith/ui_static/src/lib/useBeepQueue.test.tsx src/splitsmith/ui_static/src/pages/BeepReview.tsx src/splitsmith/ui_static/src/pages/MobileBeepReview.tsx src/splitsmith/ui_static/src/pages/Export.tsx src/splitsmith/ui_static/src/pages/MatchExport.tsx src/splitsmith/ui_static/src/pages/Compare.tsx
git commit -m "feat(ui): capability-gate beep re-detect, exports, and trim rebuild (#756)"
```

---

### Task 7: Gates, visual verification, PR

**Files:**
- No source changes (gate task).

- [ ] **Step 1: Full gates**

Run:
```bash
uv run ruff check .
uv run black --check .
uv run pytest
cd src/splitsmith/ui_static && pnpm typecheck && pnpm test
pnpm eslint src/lib/api.ts src/lib/useBeepQueue.ts src/lib/capabilities.test.ts \
  src/components/match/MatchShell.tsx src/components/share/ShareShell.tsx \
  src/pages/Home.tsx src/pages/Home.capabilities.test.tsx src/pages/Ingest.tsx \
  src/pages/BeepReview.tsx src/pages/MobileBeepReview.tsx \
  src/pages/Export.tsx src/pages/MatchExport.tsx src/pages/Compare.tsx
cd -
```
Expected: all clean (pytest modulo the ~21 known env-dependent failures - verify against main). Also grep the diff for dash discipline: `git diff main | grep '^+' | grep -E '—|--' ` should show no prose em dashes or double dashes in added comments/copy.

- [ ] **Step 2: Visual verification (mirror walk)**

Per the issue's verification section, walk a synced mirror's pages. Playwright MCP navigate hangs on the SPA (live SSE) - use the bounded headless screenshot recipe with `domcontentloaded` (route is `/match/:matchId` singular), screenshotting Home, Ingest, BeepReview, Export at minimum, confirming: banner present, edit controls hidden/disabled with the banner's wording, review controls (beep confirm) still live.

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin feat/756-match-capabilities
gh pr create --title "feat: match writability as a server-derived capability (#756)" --body "$(cat <<'EOF'
Closes #756.

The mirror guard's five hand-listed exception regexes were the de-facto
capability system. This PR makes it explicit and single-sourced:

- capabilities.py: edit / review / share_manage, per-origin and
  per-share-scope sets, and required_capability(method, rest) with
  unlisted writes defaulting to edit (fail over-restricted)
- _match_id_alias 403s from that table (parity pinned by the existing
  mirror suite plus a forward-compat test that flips the set for a
  desktop mirror and watches the guard open)
- project / shooter-list / beep-queue payloads serialize the same set;
  share responses advertise the token scope's set (empty today)
- SPA: MatchCapability + capabilityDenied(); Home, Ingest, BeepReview
  (re-detect), Export, MatchExport, Compare gate on edit instead of
  origin; hide where the action is one of several, disable-with-reason
  where absence would read as broken; origin stays provenance/media
  behavior only

Design: docs/superpowers/specs/2026-08-12-share-write-foundation-design.md
Depends on PR A (#779 scope plumbing) for current_share_scope.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_013p2JUqQX6BRGjUfqFoPVYi
EOF
)"
```

Expected: PR opens. Merge only after PR A lands (this branch builds on it), CI is green, and review is done.
