# Mobile Interval Reclassify (Slice 5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Interval chips in the mobile/responsive ResultsStage become tappable, opening a bottom sheet with interval-class options plus the optional coaching note; writes go through the existing per-shot coach PATCH endpoint, with undo via a new snackbar. Hosted mirrors accept these writes via new mirror-gate exemptions.

**Architecture:** Backend change is one middleware exemption (two compiled regexes + method-aware checks in `_match_id_alias`); coach endpoints, sync merge (COACH_FIELDS LWW), and share-token blocking already exist and need no changes. Frontend restructures the SplitsList row (chip becomes a sibling button of the seek button - nested buttons are invalid HTML), adds a `ReclassifySheet` built on `MobileConfirmSheet`, a new `Snackbar` component (first interactive toast in the codebase), and pure patch/undo builders in `src/lib/`.

**Tech Stack:** FastAPI + pytest (backend); React + TypeScript + Tailwind + vitest/@testing-library (SPA, pnpm only).

## Deviations from the parent spec (deliberate, discovered during exploration)

- The spec says chips live in "SplitsList and ShotRuler". **ShotRuler does not render on ResultsStage** - it is Coach-page-only (desktop-gated). ResultsStage's ruler-like element is `ShotTicker`, an `aria-hidden pointer-events-none` HUD overlay; making it interactive is out of scope. Slice 5 makes **SplitsList chips** the sole tap target.
- The spec names `coach/reclassify` (bulk POST) as a write path. The mobile UI only needs the per-shot PATCH; the bulk POST still gets a mirror-gate exemption (it is safe, job-free, and named by the spec) but no UI calls it in this slice.
- "Pin a stale auto class as manual by re-selecting it" is deferred: applying the same class as currently shown is a no-op (sheet just closes).

## Global Constraints

- New copy/comments use single ASCII dash "-", never em dash, never "--". Grep added lines before committing.
- WCAG 2.2 AA: 44 px min touch targets (`min-h-11`), status never carried by color alone, `motion-safe:` gating for animation, focus-visible rings.
- Overlay architecture: body `Portal`, shared z-token scale (`z-modal` 70, `z-toast` 80), `useDialogFocus` for dialogs. Never inline fixed overlays.
- Share mounts stay read-only: the server whitelist (`_SHARE_PATH_RE` + GET-only gate) is the backstop and must NOT be touched; the client hides write affordances under `/share/` paths.
- SPA is pnpm-only. Never introduce npm/package-lock.json.
- Scoped test runs per task; full gates (ruff + black + pytest, pnpm typecheck + test + scoped eslint) once at the end-of-branch gate.
- No new dependencies.
- Worktree: `~/.claude-tmp/wt-sync-spec`, branch `feat/mobile-interval-reclassify` off `origin/main`. SPA root: `src/splitsmith/ui_static`.

## Key existing facts (verified 2026-08-11 at b5df7a0)

- Per-shot coach endpoint: `PATCH /api/shooters/{slug}/stages/{n}/shots/{shot}/coach` (`server.py:10866`), body `CoachShotPatchRequest` (`server.py:4966`): `interval_class`, `interval_class_source`, `clear_class`, `improvement_flag`, `coaching_note`, `clear_note`. Returns full `CoachStageResponse`. Self-heals classification after every patch (`server.py:10909`) - the #778 invariant needs no new code. Appends a `coach_patch` audit event.
- Bulk endpoint: `POST .../coach/reclassify` (`server.py:10841`) - only rewrites auto-source shots, preserves manual.
- Neither coach handler submits jobs - no `current_match_origin` guard needed (unlike beep's `_maybe_chain_trim`).
- Mirror gate: `_match_id_alias` middleware, exemption tuple at `server.py:6477-6488`; existing regexes `_mirror_beep_write_re` (`:6411`), `_mirror_triage_write_re` (`:6416`). All existing endpoint exemptions are POST-only; the coach patch is a PATCH, so the new exemption must be method-aware per shape.
- Sync: `COACH_FIELDS` (`coach.py:42`) already merge as an atomic per-shot LWW unit (`sync/merge.py:160-218`) with tests (`tests/test_sync_merge.py:146,157`). No sync changes needed.
- Share tokens: `_SHARE_PATH_RE` (`server.py:977-996`) + GET-only gate (`server.py:6560`) already reject both coach writes; pinned by `tests/test_share_routes.py:826-831`. No changes needed.
- Client fns exist: `api.patchStageShotCoach(slug, stageNumber, shotNumber, patch)` (`api.ts:3330`), `CoachShotPatch` (`api.ts:1177`), `CoachIntervalClass` (`api.ts:1070`), `CoachShot` (`api.ts:1079`).
- `scopeRequestPath` (`api.ts:1934`) silently rewrites `/api/shooters/...` onto `/api/share/{token}/...` under a share URL - a chip tap on a share mount would 404 server-side, which is why the client must not render the affordance there.
- `INTERVAL_LABEL` / `INTERVAL_TONE` maps: `src/lib/splits.ts:156,199`. Labels: first_shot=Draw, split=Fire, transition=Transition, movement=Movement, reload=Reload, activation=Activation.
- `isShareView(pathname)` currently lives in `src/pages/Compare.tsx:64-70`.
- `MobileConfirmSheet` (`src/components/MobileConfirmSheet.tsx`): `{open, title, body: ReactNode, confirmLabel, onConfirm, onCancel}`; Triage already passes a textarea as `body`.
- Toast precedent (status-only, no action): `DropGuard.tsx:60-74`, `SaveToast` in `Audit.tsx:2286-2317` (assertive on error). `--z-index-toast: 80` = `z-toast`, defined `styles/index.css:275`. No snackbar-with-action exists.
- SplitsList row is a single `<button>` (`SplitsList.tsx:65`); the interval chip is a `<span>` inside it (`:105-114`).
- ResultsStage holds coach state as `const [coach, setCoach] = useState<CoachStageResponse | null>(null)` (`ResultsStage.tsx:63`); `shots = coach?.shots ?? []`. Write handlers should `setCoach(response)` - no refetch (Coach.tsx:1072-1085 precedent). Baselines are fetched once on mount and go stale after a reclassify; same accepted limitation as desktop Coach.
- Mirror-gate test template: `tests/test_mirror_read_only.py` - `_seed_mirror` (`:41`), triage exemption tests (`:319-405`).

---

### Task 1: Mirror-gate exemption for coach writes (backend)

**Files:**
- Modify: `src/splitsmith/ui/server.py` (regexes near `:6416`, gate tuple near `:6477-6488`)
- Test: `tests/test_mirror_read_only.py`

**Interfaces:**
- Produces: on a desktop-origin mirror, `PATCH shooters/{slug}/stages/{n}/shots/{m}/coach` and `POST shooters/{slug}/stages/{n}/coach/reclassify` pass the gate (no other method/shape widened). Frontend tasks rely on the PATCH passing.

- [ ] **Step 1: Write the failing tests** - append to `tests/test_mirror_read_only.py` after the triage section:

```python
# Coach writes pass the read-only gate on mirrors (Slice 5: mobile
# interval reclassify); everything else coach-shaped stays blocked.


def test_mirror_allows_coach_shot_patch(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The gate no longer 403s the per-shot coach PATCH on a mirror.

    Only the middleware is under test: with no shooter seeded the handler
    itself 404s, which proves the request got past the 403."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRCOACHGATE0000000001"
    _seed_mirror(client, match_id, "gate-coach-patch")
    resp = client.patch(
        f"/api/matches/{match_id}/shooters/alice/stages/1/shots/3/coach",
        json={"interval_class": "movement", "interval_class_source": "manual"},
    )
    assert resp.status_code != 403, resp.text


def test_mirror_allows_coach_reclassify(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The gate no longer 403s the bulk coach reclassify POST on a mirror."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRCOACHGATE0000000002"
    _seed_mirror(client, match_id, "gate-coach-reclassify")
    resp = client.post(f"/api/matches/{match_id}/shooters/alice/stages/1/coach/reclassify")
    assert resp.status_code != 403, resp.text


@pytest.mark.parametrize(
    ("match_id", "method", "rest"),
    [
        ("01JMIRRCOACHGATEBOUND0001", "POST", "shooters/alice/stages/1/shots/3/coach"),
        ("01JMIRRCOACHGATEBOUND0002", "PATCH", "shooters/alice/stages/1/coach/reclassify"),
        ("01JMIRRCOACHGATEBOUND0003", "PATCH", "shooters/alice/stages/1/shots/3/coach/extra"),
        ("01JMIRRCOACHGATEBOUND0004", "PATCH", "shooters/alice/stages/1/coach"),
        ("01JMIRRCOACHGATEBOUND0005", "POST", "shooters/alice/stages/1/coach/reclassify/"),
        ("01JMIRRCOACHGATEBOUND0006", "DELETE", "shooters/alice/stages/1/shots/3/coach"),
    ],
    ids=[
        "shot-patch-wrong-method",
        "reclassify-wrong-method",
        "shot-patch-extra-segment",
        "coach-root-patch",
        "reclassify-trailing-slash",
        "shot-patch-delete",
    ],
)
def test_mirror_coach_exemption_boundary_pins(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    match_id: str,
    method: str,
    rest: str,
) -> None:
    """Pin the edges of the coach exemptions.

    Each shape is exempt only for its own method (PATCH for the per-shot
    patch, POST for reclassify); the regexes are anchored with ``$``. Any
    variant slipping through would silently widen the read-only mirror's
    write surface."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed_mirror(client, match_id, "gate-coach-boundary")
    resp = client.request(method, f"/api/matches/{match_id}/{rest}", json={})
    assert resp.status_code == 403, resp.text
    assert resp.json() == {"detail": "read_only_mirror"}
```

- [ ] **Step 2: Run to verify the new allow-tests fail** (boundary pins already pass - the gate blocks everything today):

Run: `uv run pytest tests/test_mirror_read_only.py -k coach -v`
Expected: `test_mirror_allows_coach_shot_patch` and `test_mirror_allows_coach_reclassify` FAIL (403), all boundary pins PASS.

- [ ] **Step 3: Implement the exemption** - in `server.py`, directly under `_mirror_triage_write_re` (`:6416`), add:

```python
    # Slice 5 (mobile interval reclassify): the two coach writes a mirror
    # accepts - the per-shot coach PATCH and the bulk reclassify POST.
    # Both are pure state-doc writes (no job chaining), and COACH_FIELDS
    # already merge per-shot LWW on desktop pull. Note the per-shot patch
    # is a PATCH, so its exemption is method-gated separately below.
    _mirror_coach_patch_re = re.compile(r"^shooters/[^/]+/stages/\d+/shots/\d+/coach$")
    _mirror_coach_reclassify_re = re.compile(r"^shooters/[^/]+/stages/\d+/coach/reclassify$")
```

and extend the gate tuple (after the `_mirror_triage_write_re` line):

```python
                    or (request.method == "PATCH" and _mirror_coach_patch_re.match(rest) is not None)
                    or (request.method == "POST" and _mirror_coach_reclassify_re.match(rest) is not None)
```

- [ ] **Step 4: Run the whole file**

Run: `uv run pytest tests/test_mirror_read_only.py -v`
Expected: ALL PASS (including the pre-existing beep/triage pins - proves no widening).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check src/splitsmith/ui/server.py tests/test_mirror_read_only.py
uv run black --check src/splitsmith/ui/server.py tests/test_mirror_read_only.py
git add src/splitsmith/ui/server.py tests/test_mirror_read_only.py
git commit -m "feat(coach): exempt coach writes from the mirror read-only gate"
```

---

### Task 2: Pure helpers - `isShareView` moves to lib, patch/undo builders

**Files:**
- Create: `src/splitsmith/ui_static/src/lib/shareView.ts`
- Create: `src/splitsmith/ui_static/src/lib/coachPatch.ts`
- Create: `src/splitsmith/ui_static/src/lib/coachPatch.test.ts`
- Modify: `src/splitsmith/ui_static/src/pages/Compare.tsx` (delete local `isShareView`, import from lib; keep re-export if any test imports it from Compare - check with `grep -rn "isShareView" src/`)

**Interfaces:**
- Produces: `isShareView(pathname: string): boolean`; `buildCoachPatch(prev: CoachShot, draft: ReclassifyDraft): CoachShotPatch | null`; `buildUndoPatch(prev: CoachShot, applied: CoachShotPatch): CoachShotPatch`; `interface ReclassifyDraft { intervalClass: CoachIntervalClass | null; note: string }`. Tasks 4 and 5 consume all of these.

- [ ] **Step 1: Write failing tests** - `src/lib/coachPatch.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import type { CoachShot } from "@/lib/api";
import { buildCoachPatch, buildUndoPatch } from "@/lib/coachPatch";

function shot(overrides: Partial<CoachShot> = {}): CoachShot {
  return {
    shot_number: 3,
    ms_after_beep: 1500,
    time_from_beep: 1.5,
    time_absolute: 3.5,
    split: 0.42,
    interval_class: "split",
    interval_class_source: "auto",
    improvement_flag: false,
    coaching_note: null,
    stale: false,
    reload_hint: false,
    ...overrides,
  };
}

describe("buildCoachPatch", () => {
  it("class change becomes a manual override", () => {
    expect(buildCoachPatch(shot(), { intervalClass: "movement", note: "" })).toEqual({
      interval_class: "movement",
      interval_class_source: "manual",
    });
  });

  it("same class and same note is a no-op (null)", () => {
    expect(buildCoachPatch(shot(), { intervalClass: "split", note: "" })).toBeNull();
    expect(buildCoachPatch(shot({ coaching_note: "wide" }), { intervalClass: "split", note: "wide" })).toBeNull();
  });

  it("note-only change patches the note, trimmed", () => {
    expect(buildCoachPatch(shot(), { intervalClass: "split", note: "  push harder  " })).toEqual({
      coaching_note: "push harder",
    });
  });

  it("emptying an existing note clears it", () => {
    expect(buildCoachPatch(shot({ coaching_note: "old" }), { intervalClass: "split", note: " " })).toEqual({
      clear_note: true,
    });
  });

  it("null draft class leaves classification untouched", () => {
    expect(buildCoachPatch(shot({ interval_class: null, interval_class_source: null }), { intervalClass: null, note: "n" })).toEqual({
      coaching_note: "n",
    });
  });
});

describe("buildUndoPatch", () => {
  it("restores a prior manual class verbatim", () => {
    const prev = shot({ interval_class: "reload", interval_class_source: "manual" });
    expect(buildUndoPatch(prev, { interval_class: "split", interval_class_source: "manual" })).toEqual({
      interval_class: "reload",
      interval_class_source: "manual",
    });
  });

  it("reverts a prior auto class by clearing (server re-derives)", () => {
    expect(buildUndoPatch(shot(), { interval_class: "movement", interval_class_source: "manual" })).toEqual({
      clear_class: true,
    });
  });

  it("restores a prior note, clears a previously-absent note", () => {
    expect(buildUndoPatch(shot({ coaching_note: "old" }), { clear_note: true })).toEqual({
      coaching_note: "old",
    });
    expect(buildUndoPatch(shot(), { coaching_note: "new" })).toEqual({ clear_note: true });
  });

  it("only inverts touched fields", () => {
    const prev = shot({ coaching_note: "keep" });
    expect(buildUndoPatch(prev, { interval_class: "movement", interval_class_source: "manual" })).toEqual({
      clear_class: true,
    });
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/lib/coachPatch.test.ts`
Expected: FAIL - module `@/lib/coachPatch` not found.

- [ ] **Step 3: Implement** - `src/lib/coachPatch.ts`:

```ts
/**
 * Pure builders for the mobile interval-reclassify write path.
 *
 * buildCoachPatch turns the sheet's draft into the minimal
 * CoachShotPatch (null when nothing changed - the caller just closes
 * the sheet). buildUndoPatch inverts exactly the fields a patch
 * touched: a prior manual class is restored verbatim; a prior auto (or
 * absent) class is reverted with clear_class so the server re-derives
 * the rule verdict instead of us faking an "auto" write client-side.
 */
import type { CoachIntervalClass, CoachShot, CoachShotPatch } from "@/lib/api";

export interface ReclassifyDraft {
  /** Selected class; null means the sheet never had a selection. */
  intervalClass: CoachIntervalClass | null;
  /** Note textarea contents, untrimmed. */
  note: string;
}

export function buildCoachPatch(prev: CoachShot, draft: ReclassifyDraft): CoachShotPatch | null {
  const patch: CoachShotPatch = {};
  if (draft.intervalClass != null && draft.intervalClass !== prev.interval_class) {
    patch.interval_class = draft.intervalClass;
    patch.interval_class_source = "manual";
  }
  const note = draft.note.trim();
  const prevNote = prev.coaching_note ?? "";
  if (note !== prevNote) {
    if (note === "") patch.clear_note = true;
    else patch.coaching_note = note;
  }
  return Object.keys(patch).length > 0 ? patch : null;
}

export function buildUndoPatch(prev: CoachShot, applied: CoachShotPatch): CoachShotPatch {
  const undo: CoachShotPatch = {};
  if (applied.interval_class !== undefined || applied.clear_class) {
    if (prev.interval_class != null && prev.interval_class_source === "manual") {
      undo.interval_class = prev.interval_class;
      undo.interval_class_source = "manual";
    } else {
      undo.clear_class = true;
    }
  }
  if (applied.coaching_note !== undefined || applied.clear_note) {
    if (prev.coaching_note != null && prev.coaching_note !== "") {
      undo.coaching_note = prev.coaching_note;
    } else {
      undo.clear_note = true;
    }
  }
  return undo;
}
```

and `src/lib/shareView.ts`:

```ts
/**
 * Share-mount detection for components that render on both the operator
 * routes and the anonymous /share/:token routes. The server whitelist
 * is the security backstop; this only decides whether to render write
 * affordances at all (a write from a share mount would be silently
 * rewritten onto the share prefix by scopeRequestPath and 404).
 */
export function isShareView(pathname: string): boolean {
  return /^\/share\//.test(pathname);
}
```

Then in `Compare.tsx`: delete the local definition (lines 64-70) and its doc comment, add `import { isShareView } from "@/lib/shareView";`. If `grep -rn '"@/pages/Compare"' src/ | grep -i isshare` shows importers, update them to the lib path instead of re-exporting (clean, no fallbacks).

- [ ] **Step 4: Run tests + typecheck**

Run: `pnpm vitest run src/lib/coachPatch.test.ts && pnpm typecheck`
Expected: PASS, no type errors (proves the Compare refactor is complete).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/coachPatch.ts src/splitsmith/ui_static/src/lib/coachPatch.test.ts src/splitsmith/ui_static/src/lib/shareView.ts src/splitsmith/ui_static/src/pages/Compare.tsx
git commit -m "feat(ui): coach patch/undo builders + isShareView moved to lib"
```

---

### Task 3: Snackbar component (first interactive toast)

**Files:**
- Create: `src/splitsmith/ui_static/src/components/Snackbar.tsx`
- Create: `src/splitsmith/ui_static/src/components/Snackbar.test.tsx`

**Interfaces:**
- Produces: `interface SnackState { message: string; tone: "status" | "error"; actionLabel?: string; onAction?: () => void }` and `function Snackbar({ snack, onDismiss }: { snack: SnackState | null; onDismiss: () => void })`. Task 5 consumes both.

- [ ] **Step 1: Write failing tests** - `Snackbar.test.tsx`:

```tsx
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Snackbar } from "@/components/Snackbar";

describe("Snackbar", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("renders nothing visible when snack is null but keeps the live region", () => {
    render(<Snackbar snack={null} onDismiss={() => {}} />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("shows the message and fires the action", () => {
    const onAction = vi.fn();
    const onDismiss = vi.fn();
    render(
      <Snackbar
        snack={{ message: "Shot 03 - Movement", tone: "status", actionLabel: "Undo", onAction }}
        onDismiss={onDismiss}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Shot 03 - Movement");
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(onAction).toHaveBeenCalledTimes(1);
  });

  it("auto-dismisses after the timeout", () => {
    const onDismiss = vi.fn();
    render(<Snackbar snack={{ message: "saved", tone: "status" }} onDismiss={onDismiss} />);
    act(() => vi.advanceTimersByTime(6000));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("errors use an assertive alert region and do not auto-dismiss", () => {
    const onDismiss = vi.fn();
    render(<Snackbar snack={{ message: "patch failed", tone: "error" }} onDismiss={onDismiss} />);
    expect(screen.getByRole("alert")).toHaveTextContent("patch failed");
    act(() => vi.advanceTimersByTime(20000));
    expect(onDismiss).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run src/components/Snackbar.test.tsx`
Expected: FAIL - module not found.

- [ ] **Step 3: Implement** - `Snackbar.tsx`:

```tsx
/**
 * Snackbar - the codebase's first interactive toast. Follows the
 * SaveToast/DropGuard shell (body Portal, z-toast token, live region
 * rendered unconditionally, pointer-events-none wrapper with an
 * interactive inner pill) and adds an optional action button (Undo).
 *
 * Status snacks are polite and auto-dismiss after 6 s; error snacks are
 * assertive, never auto-dismiss, and get an explicit Dismiss button
 * (WCAG - a timed disappearance must not be the only path for content
 * the user needs to act on). Undo is a convenience, not the only path:
 * the sheet can always re-apply the previous class, so the 6 s limit is
 * acceptable.
 */
import { useEffect } from "react";

import { Portal } from "@/components/ui/Portal";
import { cn } from "@/lib/utils";

const SNACK_MS = 6000;

export interface SnackState {
  message: string;
  tone: "status" | "error";
  actionLabel?: string;
  onAction?: () => void;
}

export function Snackbar({
  snack,
  onDismiss,
}: {
  snack: SnackState | null;
  onDismiss: () => void;
}) {
  const isError = snack?.tone === "error";

  useEffect(() => {
    if (!snack || snack.tone === "error") return;
    const id = window.setTimeout(onDismiss, SNACK_MS);
    return () => window.clearTimeout(id);
  }, [snack, onDismiss]);

  return (
    <Portal>
      <div
        role={isError ? "alert" : "status"}
        aria-live={isError ? "assertive" : "polite"}
        className="pointer-events-none fixed inset-x-4 bottom-4 z-toast flex justify-center sm:inset-x-auto sm:right-4"
      >
        {snack ? (
          <div
            className={cn(
              "pointer-events-auto flex min-h-11 items-center gap-3 rounded-md border bg-surface px-4 py-2 text-sm shadow-md",
              isError ? "border-led text-led" : "border-rule-strong text-ink",
            )}
          >
            <span>{snack.message}</span>
            {snack.actionLabel && snack.onAction ? (
              <button
                type="button"
                onClick={snack.onAction}
                className="min-h-11 shrink-0 rounded px-2 font-display text-sm font-bold uppercase tracking-[0.06em] text-led focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
              >
                {snack.actionLabel}
              </button>
            ) : null}
            {isError ? (
              <button
                type="button"
                onClick={onDismiss}
                className="min-h-11 shrink-0 rounded px-2 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
              >
                Dismiss
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </Portal>
  );
}
```

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run src/components/Snackbar.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/components/Snackbar.tsx src/splitsmith/ui_static/src/components/Snackbar.test.tsx
git commit -m "feat(ui): Snackbar component with action button"
```

---

### Task 4: ReclassifySheet component

**Files:**
- Create: `src/splitsmith/ui_static/src/components/results/ReclassifySheet.tsx`
- Create: `src/splitsmith/ui_static/src/components/results/ReclassifySheet.test.tsx`

**Interfaces:**
- Consumes: `MobileConfirmSheet`, `buildCoachPatch`/`ReclassifyDraft` (Task 2), `INTERVAL_LABEL`/`INTERVAL_TONE` from `@/lib/splits`, `CoachShot`/`CoachShotPatch`/`CoachIntervalClass` from `@/lib/api`.
- Produces: `function ReclassifySheet({ shot, busy, onApply, onCancel }: { shot: CoachShot | null; busy: boolean; onApply: (shot: CoachShot, patch: CoachShotPatch) => void; onCancel: () => void })`. Task 5 mounts it keyed by shot number.

- [ ] **Step 1: Write failing tests** - `ReclassifySheet.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ReclassifySheet } from "@/components/results/ReclassifySheet";
import type { CoachShot } from "@/lib/api";

function shot(overrides: Partial<CoachShot> = {}): CoachShot {
  return {
    shot_number: 5,
    ms_after_beep: 2100,
    time_from_beep: 2.1,
    time_absolute: 4.1,
    split: 0.61,
    interval_class: "split",
    interval_class_source: "auto",
    improvement_flag: false,
    coaching_note: null,
    stale: false,
    reload_hint: false,
    ...overrides,
  };
}

describe("ReclassifySheet", () => {
  it("renders nothing when shot is null", () => {
    render(<ReclassifySheet shot={null} busy={false} onApply={() => {}} onCancel={() => {}} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("offers all six classes with the current one pre-selected", () => {
    render(<ReclassifySheet shot={shot()} busy={false} onApply={() => {}} onCancel={() => {}} />);
    expect(screen.getAllByRole("radio")).toHaveLength(6);
    expect(screen.getByRole("radio", { name: "Fire" })).toBeChecked();
  });

  it("applying a new class emits a manual-override patch", () => {
    const onApply = vi.fn();
    render(<ReclassifySheet shot={shot()} busy={false} onApply={onApply} onCancel={() => {}} />);
    fireEvent.click(screen.getByRole("radio", { name: "Movement" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ shot_number: 5 }), {
      interval_class: "movement",
      interval_class_source: "manual",
    });
  });

  it("applying with nothing changed just cancels", () => {
    const onApply = vi.fn();
    const onCancel = vi.fn();
    render(<ReclassifySheet shot={shot()} busy={false} onApply={onApply} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).not.toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("a note edit rides the patch", () => {
    const onApply = vi.fn();
    render(<ReclassifySheet shot={shot()} busy={false} onApply={onApply} onCancel={() => {}} />);
    fireEvent.change(screen.getByLabelText(/note/i), { target: { value: "slow entry" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith(expect.anything(), { coaching_note: "slow entry" });
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run src/components/results/ReclassifySheet.test.tsx`
Expected: FAIL - module not found.

- [ ] **Step 3: Implement** - `ReclassifySheet.tsx`:

```tsx
/**
 * ReclassifySheet - the mobile interval-reclassify bottom sheet
 * (slice 5 of the mobile operator surfaces program). Wraps
 * MobileConfirmSheet with a radio chip-group of the six interval
 * classes plus the optional coaching note. Owns only draft state; the
 * caller owns the write (and should remount this keyed by shot number
 * so drafts never leak between shots).
 *
 * Selection is not color-only: the picked chip gets a ring, bold text
 * and aria-checked. Reload/activation are manual-only classes the auto
 * rule never assigns - offering them here is the point of the surface.
 */
import { useState } from "react";

import { MobileConfirmSheet } from "@/components/MobileConfirmSheet";
import type { CoachIntervalClass, CoachShot, CoachShotPatch } from "@/lib/api";
import { buildCoachPatch } from "@/lib/coachPatch";
import { INTERVAL_LABEL, INTERVAL_TONE } from "@/lib/splits";
import { cn } from "@/lib/utils";

const CLASSES = Object.keys(INTERVAL_LABEL) as CoachIntervalClass[];

export function ReclassifySheet({
  shot,
  busy,
  onApply,
  onCancel,
}: {
  shot: CoachShot | null;
  busy: boolean;
  onApply: (shot: CoachShot, patch: CoachShotPatch) => void;
  onCancel: () => void;
}) {
  const [selected, setSelected] = useState<CoachIntervalClass | null>(
    shot?.interval_class ?? null,
  );
  const [note, setNote] = useState(shot?.coaching_note ?? "");

  if (!shot) return null;

  const apply = () => {
    if (busy) return;
    const patch = buildCoachPatch(shot, { intervalClass: selected, note });
    if (!patch) {
      onCancel();
      return;
    }
    onApply(shot, patch);
  };

  return (
    <MobileConfirmSheet
      open
      title={`Shot ${shot.shot_number} - ${shot.split.toFixed(3)}s`}
      confirmLabel="Apply"
      onConfirm={apply}
      onCancel={onCancel}
      body={
        <span className="block">
          <span role="radiogroup" aria-label="Interval class" className="mb-4 flex flex-wrap gap-2">
            {CLASSES.map((c) => {
              const picked = selected === c;
              return (
                <button
                  key={c}
                  type="button"
                  role="radio"
                  aria-checked={picked}
                  onClick={() => setSelected(c)}
                  className={cn(
                    "min-h-11 rounded border px-3 font-mono text-xs uppercase focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led",
                    INTERVAL_TONE[c],
                    picked ? "font-bold ring-2 ring-led" : "opacity-70",
                  )}
                >
                  {INTERVAL_LABEL[c]}
                </button>
              );
            })}
          </span>
          <label className="block">
            <span className="mb-1 block text-xs uppercase tracking-[0.06em] text-muted">
              Coaching note (optional)
            </span>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
              className="w-full rounded border border-rule bg-surface-2 p-2 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
            />
          </label>
        </span>
      }
    />
  );
}
```

Note: `MobileConfirmSheet` renders `body` inside a `<p>`; Triage already ships a textarea there, so this follows the established precedent (span wrappers keep the nesting as valid as the precedent's).

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run src/components/results/ReclassifySheet.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/components/results/ReclassifySheet.tsx src/splitsmith/ui_static/src/components/results/ReclassifySheet.test.tsx
git commit -m "feat(ui): ReclassifySheet bottom sheet for interval classes"
```

---

### Task 5: SplitsList tappable chips + ResultsStage wiring

The row restructure and the page wiring ship together: the restructure alone renders a chip button no handler consumes, and the wiring alone has nothing to tap. Still two commits (component, then page) inside one reviewable task.

**Files:**
- Modify: `src/splitsmith/ui_static/src/components/results/SplitsList.tsx`
- Create: `src/splitsmith/ui_static/src/components/results/SplitsList.test.tsx`
- Modify: `src/splitsmith/ui_static/src/pages/ResultsStage.tsx`

**Interfaces:**
- Consumes: `isShareView` (Task 2), `buildUndoPatch` (Task 2), `Snackbar`/`SnackState` (Task 3), `ReclassifySheet` (Task 4), `api.patchStageShotCoach`.
- Produces: `SplitsListProps` gains `onReclassify?: (shot: CoachShot) => void` - chip is a button iff provided; ResultsStage passes it only when `!isShareView(location.pathname)`.

- [ ] **Step 1: Write failing SplitsList tests** - `SplitsList.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SplitsList } from "@/components/results/SplitsList";
import type { CoachShot } from "@/lib/api";

function shot(n: number, overrides: Partial<CoachShot> = {}): CoachShot {
  return {
    shot_number: n,
    ms_after_beep: n * 1000,
    time_from_beep: n,
    time_absolute: n + 2,
    split: 0.5,
    interval_class: "split",
    interval_class_source: "auto",
    improvement_flag: false,
    coaching_note: null,
    stale: false,
    reload_hint: false,
    ...overrides,
  };
}

describe("SplitsList", () => {
  it("without onReclassify the chip stays a non-interactive span (share contract)", () => {
    render(
      <SplitsList
        shots={[shot(1)]}
        activeShotNumber={null}
        onSeek={() => {}}
        isPlaying={false}
        baselines={null}
      />,
    );
    expect(screen.queryByRole("button", { name: /reclassify/i })).not.toBeInTheDocument();
    expect(screen.getByText("Fire")).toBeInTheDocument();
  });

  it("with onReclassify the chip is a button and does not trigger seek", () => {
    const onSeek = vi.fn();
    const onReclassify = vi.fn();
    render(
      <SplitsList
        shots={[shot(1)]}
        activeShotNumber={null}
        onSeek={onSeek}
        isPlaying={false}
        baselines={null}
        onReclassify={onReclassify}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Reclassify shot 1 (Fire)" }));
    expect(onReclassify).toHaveBeenCalledWith(expect.objectContaining({ shot_number: 1 }));
    expect(onSeek).not.toHaveBeenCalled();
  });

  it("an unclassified shot gets a Classify affordance only when interactive", () => {
    const unclassified = shot(2, { interval_class: null, interval_class_source: null });
    const { rerender } = render(
      <SplitsList
        shots={[unclassified]}
        activeShotNumber={null}
        onSeek={() => {}}
        isPlaying={false}
        baselines={null}
        onReclassify={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "Reclassify shot 2 (unclassified)" })).toBeInTheDocument();
    rerender(
      <SplitsList
        shots={[unclassified]}
        activeShotNumber={null}
        onSeek={() => {}}
        isPlaying={false}
        baselines={null}
      />,
    );
    expect(screen.queryByRole("button", { name: /reclassify/i })).not.toBeInTheDocument();
  });

  it("row click still seeks", () => {
    const onSeek = vi.fn();
    render(
      <SplitsList
        shots={[shot(1)]}
        activeShotNumber={null}
        onSeek={onSeek}
        isPlaying={false}
        baselines={null}
        onReclassify={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /01/ }));
    expect(onSeek).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run src/components/results/SplitsList.test.tsx`
Expected: the interactive-chip tests FAIL (`onReclassify` prop does not exist; chip is a span).

- [ ] **Step 3: Restructure SplitsList**

The row `<button>` becomes a `<div>` wrapper (keeps `data-shot-number`, active styling, LED bar, scroll-margin) containing two siblings: the seek `<button>` (flex-1, holds number/time/split/tier and the note line) and a right-aligned cluster with the chip (button when `onReclassify` is set, span otherwise) and the improvement flag. Nested buttons are invalid HTML, which is why the chip cannot stay inside the seek button. Replace the `shots.map` body (`SplitsList.tsx:61-127`) with:

```tsx
{shots.map((shot) => {
  const tier = gapTier(shot.split, shot.interval_class, baselines);
  const active = activeShotNumber === shot.shot_number;
  const chipTone = shot.interval_class
    ? INTERVAL_TONE[shot.interval_class]
    : "text-muted border-rule bg-surface-2";
  const chipLabel = shot.interval_class ? INTERVAL_LABEL[shot.interval_class] : "Classify";
  const chip = (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded border px-1.5 py-0.5 font-mono text-[0.625rem] uppercase",
        chipTone,
      )}
    >
      {chipLabel}
    </span>
  );
  return (
    <div
      key={shot.shot_number}
      data-shot-number={shot.shot_number}
      className={cn(
        "relative flex min-h-11 items-center transition-colors hover:bg-surface-2",
        "max-lg:scroll-mt-[calc(var(--shell-header-h,0px)+var(--results-player-h,0px)+8px)]",
        active && "bg-surface-2",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "absolute inset-y-0 left-0 w-[3px] bg-led shadow-[0_0_12px_var(--color-led-glow)]",
          active ? "opacity-100" : "opacity-0",
        )}
      />
      <button
        type="button"
        onClick={() => onSeek(shot)}
        className="min-h-11 flex-1 px-4 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led focus-visible:ring-inset"
      >
        <span className="flex items-center gap-3">
          <span className="w-8 shrink-0 font-mono text-xs font-bold tabular-nums text-muted">
            {pad2(shot.shot_number)}
          </span>
          <span className="w-14 shrink-0 text-right font-mono text-sm tabular-nums text-ink-2">
            {shot.time_from_beep.toFixed(2)}
          </span>
          <span className="w-16 shrink-0 text-right font-mono text-sm font-bold tabular-nums text-ink">
            {shot.split.toFixed(3)}
          </span>
          {tier ? (
            <span className="inline-flex shrink-0 items-center gap-1 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
              <span aria-hidden className="size-2 rounded-full" style={{ backgroundColor: tier.color }} />
              {tier.label}
            </span>
          ) : null}
        </span>
        {shot.coaching_note ? (
          <span className="mt-1 block pl-11 text-xs text-muted">{shot.coaching_note}</span>
        ) : null}
      </button>
      <span className="flex shrink-0 items-center gap-2 pr-4">
        {onReclassify ? (
          <button
            type="button"
            aria-label={`Reclassify shot ${shot.shot_number} (${
              shot.interval_class ? INTERVAL_LABEL[shot.interval_class] : "unclassified"
            })`}
            onClick={() => onReclassify(shot)}
            className="flex min-h-11 min-w-11 items-center justify-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
          >
            {chip}
          </button>
        ) : shot.interval_class ? (
          chip
        ) : null}
        {shot.improvement_flag ? (
          <Flag
            role="img"
            aria-label="Flagged for improvement"
            className="size-3.5 shrink-0 text-led"
          />
        ) : null}
      </span>
    </div>
  );
})}
```

Add to `SplitsListProps`:

```ts
  /** Operator-only: makes the interval chip a tap target that opens the
   *  reclassify sheet. Omitted on share mounts, where the chip stays a
   *  read-only span (and unclassified shots get no affordance at all). */
  onReclassify?: (shot: CoachShot) => void;
```

and destructure `onReclassify` in the component signature. Update the file's doc comment (lines 1-10): the surface is read-only on share mounts; on operator mounts the chip is the slice-5 reclassify entry point.

- [ ] **Step 4: Run SplitsList tests**

Run: `pnpm vitest run src/components/results/SplitsList.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit the component**

```bash
git add src/splitsmith/ui_static/src/components/results/SplitsList.tsx src/splitsmith/ui_static/src/components/results/SplitsList.test.tsx
git commit -m "feat(ui): tappable interval chips in SplitsList"
```

- [ ] **Step 6: Wire ResultsStage**

In `ResultsStage.tsx` (`ResultsStageInner`), add imports:

```tsx
import { useLocation } from "react-router-dom";  // merge into the existing react-router import
import { Snackbar, type SnackState } from "@/components/Snackbar";
import { ReclassifySheet } from "@/components/results/ReclassifySheet";
import { buildUndoPatch } from "@/lib/coachPatch";
import { isShareView } from "@/lib/shareView";
import { INTERVAL_LABEL } from "@/lib/splits";  // merge into the existing splits import
import type { CoachShot, CoachShotPatch } from "@/lib/api";  // merge into the existing api type import
```

Add state + handlers after the existing state block (`:63-76`):

```tsx
  const location = useLocation();
  const canReclassify = !isShareView(location.pathname);
  const [sheetShot, setSheetShot] = useState<CoachShot | null>(null);
  const [patchBusy, setPatchBusy] = useState(false);
  const [snack, setSnack] = useState<SnackState | null>(null);

  // Non-optimistic write, per the desktop Coach precedent: PATCH returns
  // the full CoachStageResponse, which replaces coach state wholesale -
  // no refetch, no local mirror to desync. Undo re-patches the inverse
  // (buildUndoPatch) of exactly the fields the apply touched.
  const applyShotPatch = useCallback(
    async (shot: CoachShot, patch: CoachShotPatch, undoable: boolean) => {
      setPatchBusy(true);
      try {
        const updated = await api.patchStageShotCoach(slug, stage, shot.shot_number, patch);
        setCoach(updated);
        setSheetShot(null);
        if (undoable) {
          const undoPatch = buildUndoPatch(shot, patch);
          setSnack({
            message: patch.interval_class
              ? `Shot ${shot.shot_number} - ${INTERVAL_LABEL[patch.interval_class]}`
              : `Shot ${shot.shot_number} note saved`,
            tone: "status",
            actionLabel: "Undo",
            onAction: () => void applyShotPatch(shot, undoPatch, false),
          });
        } else {
          setSnack({ message: "Change undone", tone: "status" });
        }
      } catch (e) {
        setSnack({ message: e instanceof ApiError ? e.detail : String(e), tone: "error" });
      } finally {
        setPatchBusy(false);
      }
    },
    [slug, stage],
  );
```

Pass the handler to SplitsList (`:402-408`):

```tsx
<SplitsList
  shots={shots}
  activeShotNumber={activeShotNumber}
  onSeek={seekToShot}
  isPlaying={isPlaying}
  baselines={baselines}
  onReclassify={canReclassify ? setSheetShot : undefined}
/>
```

Mount the sheet and snackbar just before the closing tag of the page root (sibling of the main layout, since both portal to body anyway):

```tsx
<ReclassifySheet
  key={sheetShot?.shot_number ?? "closed"}
  shot={sheetShot}
  busy={patchBusy}
  onApply={(shot, patch) => void applyShotPatch(shot, patch, true)}
  onCancel={() => setSheetShot(null)}
/>
<Snackbar snack={snack} onDismiss={() => setSnack(null)} />
```

Update the file's top doc comment (lines 1-11): the surface is read-only **on share mounts**; operator mounts (desktop and mobile) carry the slice-5 interval-reclassify write path, deliberately breaking the old blanket read-only contract (mobile operator surfaces program). Note the server share whitelist as the backstop.

- [ ] **Step 7: Typecheck + full SPA test run for the touched area**

Run: `pnpm typecheck && pnpm vitest run src/components/results/ src/components/Snackbar.test.tsx src/lib/coachPatch.test.ts`
Expected: PASS, no type errors.

- [ ] **Step 8: Commit the wiring**

```bash
git add src/splitsmith/ui_static/src/pages/ResultsStage.tsx
git commit -m "feat(ui): interval reclassify sheet + undo snackbar on ResultsStage"
```

---

### Task 6: End-of-branch gates + visual verification

**Files:** none new (fixes only if gates fail).

- [ ] **Step 1: Python gates**

Run: `uv run ruff check . && uv run black --check . && uv run pytest -q`
Expected: clean. Known env-dependent local failures (~21, see memory) must be verified against main before being accepted as pre-existing - never new ones.

- [ ] **Step 2: SPA gates**

Run: `cd src/splitsmith/ui_static && pnpm typecheck && pnpm test && pnpm exec eslint src/components/results/ src/components/Snackbar.tsx src/lib/coachPatch.ts src/lib/shareView.ts src/pages/ResultsStage.tsx src/pages/Compare.tsx`
Expected: clean.

- [ ] **Step 3: ASCII sweep of added lines**

Run: `git diff origin/main | grep '^+' | grep -P '[\x{2013}\x{2014}\x{2018}\x{2019}\x{201C}\x{201D}\x{2026}]' ; git diff origin/main | grep '^+' | grep -- '--' | grep -v '^+++'`
Expected: no em/en dashes or curly quotes; any `--` hits are code/flags only, not prose.

- [ ] **Step 4: Visual verification at phone width**

Launch the local app against a real match (X9 storage), then use the bounded headless screenshot recipe (Playwright MCP navigate hangs on the SPA's live SSE - use a bounded script with `domcontentloaded`, route is `/match/:matchId` singular). Capture:
1. ResultsStage at 390 px width - chips render with 44 px hit areas.
2. Sheet open - six class chips + note field.
3. Snackbar visible after an apply.
Verify dark-theme tones, focus rings, and that the note line and flag icon still lay out correctly on long notes.

- [ ] **Step 5: Docker smoke (mirror-gate touch point)**

Run: `PATH=~/.claude-tmp/bin:$PATH uv run pytest -m docker -n0 -q`
Expected: passes (or skips identical to main). The gate change is middleware-only, but the mirror tests ride hosted fixtures - cheap insurance per the db-change policy.

- [ ] **Step 6: Push branch + PR**

```bash
git push -u origin feat/mobile-interval-reclassify
gh pr create --title "feat: mobile interval reclassify (slice 5)" --body "..."
```

PR body: summary, the two spec deviations (ShotRuler absent from ResultsStage; same-class pin deferred), deferrals, and the staging E2E checklist (below) as a post-merge step.

**Staging E2E (post-merge, mirrors slices 3/4):** on ess-black mirror via phone (or curl with the staging login recipe: `scripts/staging_login_link.py` -> `curl -c cookies auth/callback` -> cookie requests): tap a chip, reclassify a split to movement with a note, verify snackbar + undo; then desktop `splitsmith sync`: pull merges the coach fields (0 conflicts), re-push-0; verify the interval class + note visible on desktop Coach page.

---

## Self-review notes

- Spec coverage: tappable chips (Task 5), bottom sheet with classes + note (Task 4), writes via existing endpoints (Task 1 gate + Task 5 wiring), undo via snackbar (Tasks 3+5), share mount stays read-only (Task 2 `isShareView` + Task 5 gating + untouched server whitelist). ShotRuler deviation documented up top.
- The PATCH-vs-POST method gating is the one place slice 3/4 patterns would mislead - called out in Task 1 with boundary pins for the wrong-method cases.
- No sync work: COACH_FIELDS merge + tests already exist (verified against b5df7a0). The `_strip_audit` tripwire stops firing for coach fields since they are whitelisted merge units already.
- `improvement_flag` is deliberately not in the sheet (spec: class options + note only).
