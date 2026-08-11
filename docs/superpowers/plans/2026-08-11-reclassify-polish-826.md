# Interval Reclassify Polish (#826) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the code follow-ups from the slice 5 final review (issue #826): patch/undo edge hardening, sheet busy feedback + ARIA polish, and direct test coverage for the ResultsStage write flow.

**Architecture:** All SPA-only, three files of production code touched (coachPatch.ts, MobileConfirmSheet.tsx + ReclassifySheet.tsx, ResultsStage.tsx) plus their tests. No backend, no sync, no route changes.

**Tech Stack:** React + TypeScript + Tailwind + vitest/@testing-library (pnpm only).

## Global Constraints

- New copy/comments use single ASCII dash "-", never em dash, never "--".
- WCAG 2.2 AA; 44 px targets stay; status never color-alone. User-facing copy is sentence-case constants (slice-4 lesson: CSS does the uppercasing).
- No new dependencies. Scoped test runs per task; full SPA gate at the end.
- Worktree `~/.claude-tmp/wt-sync-spec`, branch `fix/826-reclassify-polish` off origin/main. SPA root `src/splitsmith/ui_static`.
- Out of scope (stays open on #826): real-phone visual pass. Docker smoke re-run already done post-merge.

## Key existing facts (verified at 4fbe3eb)

- `apiErrorText(err, fallback)` exists in `@/lib/api` (tested in `src/lib/apiErrors.test.ts:43`) - returns the ApiError detail or the fallback for non-API errors.
- `src/pages/ResultsStage.test.tsx` exists with a `vi.mock("@/lib/api", ...)` harness; `getStageCoach` is mocked (`:22`), `patchStageShotCoach` is NOT yet in the mock.
- `ReclassifySheet` guards `apply()` on `busy` already; `MobileConfirmSheet` has no disabled/busy support.
- `applyShotPatch` in ResultsStage: `setSheetShot(null)` unconditionally on success; error branch is `e instanceof ApiError ? e.detail : String(e)`; snackbar `onAction` is not double-tap guarded.
- SplitsList seek-button accessible name for shot 1 in its test: "01 1.00 0.500" (tier chip absent with null baselines).

---

### Task 1: coachPatch trim symmetry + SplitsList test regex

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/coachPatch.ts`
- Modify: `src/splitsmith/ui_static/src/lib/coachPatch.test.ts`
- Modify: `src/splitsmith/ui_static/src/components/results/SplitsList.test.tsx`

**Interfaces:** `buildCoachPatch` signature unchanged; only the note comparison normalizes both sides.

- [ ] **Step 1: Failing test** - add to the `buildCoachPatch` describe block in `coachPatch.test.ts`:

```ts
  it("a whitespace-padded stored note does not ride a class-only patch", () => {
    expect(
      buildCoachPatch(shot({ coaching_note: " wide entry " }), {
        intervalClass: "movement",
        note: " wide entry ",
      }),
    ).toEqual({ interval_class: "movement", interval_class_source: "manual" });
  });
```

- [ ] **Step 2: Run** `pnpm vitest run src/lib/coachPatch.test.ts` - the new test FAILS (patch carries `coaching_note: "wide entry"`).

- [ ] **Step 3: Fix** in `coachPatch.ts` - the comparison line becomes:

```ts
  const prevNote = (prev.coaching_note ?? "").trim();
```

(the sent value stays the trimmed draft; a note change to a differently-padded but identical-when-trimmed value is now a no-op).

- [ ] **Step 4:** In `SplitsList.test.tsx`, replace the row-seek query `getByRole("button", { name: /01/ })` with the exact accessible name `getByRole("button", { name: "01 1.00 0.500" })`.

- [ ] **Step 5: Run** `pnpm vitest run src/lib/coachPatch.test.ts src/components/results/SplitsList.test.tsx` - all PASS. Commit:

```bash
git add src/splitsmith/ui_static/src/lib/coachPatch.ts src/splitsmith/ui_static/src/lib/coachPatch.test.ts src/splitsmith/ui_static/src/components/results/SplitsList.test.tsx
git commit -m "fix(ui): trim both sides of coach note comparison; exact seek-row test name"
```

---

### Task 2: Sheet busy feedback + roving-tabindex radiogroup

**Files:**
- Modify: `src/splitsmith/ui_static/src/components/MobileConfirmSheet.tsx`
- Modify: `src/splitsmith/ui_static/src/components/results/ReclassifySheet.tsx`
- Modify: `src/splitsmith/ui_static/src/components/results/ReclassifySheet.test.tsx`

**Interfaces:**
- MobileConfirmSheet gains optional `confirmDisabled?: boolean` (default false). Existing callers (Triage, MobileBeepReview) unchanged.
- ReclassifySheet props unchanged; behavior: while `busy`, confirm shows "Applying..." and is disabled; radios follow the APG roving-tabindex pattern.

- [ ] **Step 1: Failing tests** - add to `ReclassifySheet.test.tsx`:

```tsx
  it("busy disables Apply and labels it Applying...", () => {
    render(<ReclassifySheet shot={shot()} busy={true} onApply={() => {}} onCancel={() => {}} />);
    const btn = screen.getByRole("button", { name: "Applying..." });
    expect(btn).toBeDisabled();
  });

  it("radios rove: only the selected chip is tabbable and arrows move selection", () => {
    render(<ReclassifySheet shot={shot()} busy={false} onApply={() => {}} onCancel={() => {}} />);
    const fire = screen.getByRole("radio", { name: "Fire" });
    expect(fire).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("radio", { name: "Draw" })).toHaveAttribute("tabindex", "-1");
    fire.focus();
    fireEvent.keyDown(fire, { key: "ArrowRight" });
    const transition = screen.getByRole("radio", { name: "Transition" });
    expect(transition).toHaveAttribute("aria-checked", "true");
    expect(transition).toHaveAttribute("tabindex", "0");
    expect(transition).toHaveFocus();
  });
```

- [ ] **Step 2: Run** `pnpm vitest run src/components/results/ReclassifySheet.test.tsx` - both FAIL.

- [ ] **Step 3: MobileConfirmSheet** - add the prop and wire it to the confirm button:

```tsx
  confirmDisabled = false,
```
in the destructure, `confirmDisabled?: boolean;` in the props type, and on the confirm button:

```tsx
            <button
              type="button"
              onClick={onConfirm}
              disabled={confirmDisabled}
              className="btn-led-fill min-h-11 flex-1 rounded-md disabled:cursor-default disabled:opacity-60"
            >
```

- [ ] **Step 4: ReclassifySheet** - pass `confirmLabel={busy ? "Applying..." : "Apply"}` and `confirmDisabled={busy}`. Implement roving tabindex on the chip group: the tabbable chip is the selected one (or the first chip when nothing is selected); ArrowRight/ArrowDown select the next class (wrapping), ArrowLeft/ArrowUp the previous, and selection moves focus:

```tsx
  const move = (from: CoachIntervalClass, delta: number) => {
    const i = CLASSES.indexOf(from);
    const next = CLASSES[(i + delta + CLASSES.length) % CLASSES.length];
    setSelected(next);
    // Roving tabindex: focus follows selection (APG radio group pattern).
    requestAnimationFrame(() => {
      document.getElementById(`reclass-chip-${next}`)?.focus();
    });
  };
```

Each chip button gains `id={`reclass-chip-${c}`}`, `tabIndex={(selected ? c === selected : c === CLASSES[0]) ? 0 : -1}`, and:

```tsx
  onKeyDown={(e) => {
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      e.preventDefault();
      move(c, 1);
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      e.preventDefault();
      move(c, -1);
    }
  }}
```

- [ ] **Step 5: Run** `pnpm vitest run src/components/results/ReclassifySheet.test.tsx` - all PASS (pre-existing tests must stay green; the tab-stop change must not break the "offers all six classes" test). Then `pnpm typecheck`. Commit:

```bash
git add src/splitsmith/ui_static/src/components/MobileConfirmSheet.tsx src/splitsmith/ui_static/src/components/results/ReclassifySheet.tsx src/splitsmith/ui_static/src/components/results/ReclassifySheet.test.tsx
git commit -m "feat(ui): sheet busy feedback + roving tabindex for class chips"
```

---

### Task 3: ResultsStage flow hardening + direct unit tests

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/ResultsStage.tsx`
- Modify: `src/splitsmith/ui_static/src/pages/ResultsStage.test.tsx`

**Interfaces:** no prop/API changes; behavior changes are (a) undo snack dismisses on first tap, (b) success only closes the sheet still showing the patched shot, (c) non-API errors show a friendly constant.

- [ ] **Step 1: Failing tests** - in `ResultsStage.test.tsx`, add `patchStageShotCoach: vi.fn()` to the existing api mock, then add tests (reuse the harness's existing render/`makeCoach` helpers; adapt names to the file's conventions):

```tsx
  it("apply flow: chip -> sheet -> Apply patches and shows the undo snack", async () => {
    vi.mocked(api.patchStageShotCoach).mockResolvedValue(makeCoach(shots));
    // render, tap "Reclassify shot 1 (...)", pick "Movement", tap Apply
    // assert api.patchStageShotCoach called with (slug, stage, 1,
    //   { interval_class: "movement", interval_class_source: "manual" })
    // assert snack text "Shot 1 - Movement" with an Undo button
  });

  it("undo dismisses the snack on first tap and re-patches the inverse", async () => {
    // after the apply flow above, tap Undo
    // assert the snack with the Undo button is gone IMMEDIATELY (before the
    //   patch resolves - double-tap guard), and patchStageShotCoach was
    //   called a second time with { clear_class: true }
    // assert the "Change undone" snack appears after resolution
  });

  it("a non-API patch failure shows the friendly fallback, not String(e)", async () => {
    vi.mocked(api.patchStageShotCoach).mockRejectedValue(new TypeError("Failed to fetch"));
    // apply flow; assert role="alert" contains PATCH_FAILED_FALLBACK text
    // and does NOT contain "TypeError"
  });
```

Write the real interactions with @testing-library (`fireEvent`/`await screen.findByText`); the comments above are the assertions to encode, not placeholders to leave.

- [ ] **Step 2: Run** `pnpm vitest run src/pages/ResultsStage.test.tsx` - new tests FAIL.

- [ ] **Step 3: Implement** in `ResultsStage.tsx`:

1. Import `apiErrorText` from `@/lib/api` (drop the manual `ApiError` branch if no other use remains). Add near the other constants:

```tsx
// Sentence case - display CSS owns any uppercasing.
const PATCH_FAILED_FALLBACK = "Could not save the change - check the connection and retry.";
```

2. Error branch becomes:

```tsx
      } catch (e) {
        setSnack({ message: apiErrorText(e, PATCH_FAILED_FALLBACK), tone: "error" });
      }
```

3. Stale-close guard - success path closes the sheet only if it still shows the patched shot:

```tsx
        setSheetShot((cur) => (cur && cur.shot_number === shot.shot_number ? null : cur));
```

4. Undo double-tap guard - the snack dismisses itself on first tap, before the network round-trip:

```tsx
            onAction: () => {
              setSnack(null);
              void applyShotPatch(shot, undoPatch, false);
            },
```

- [ ] **Step 4: Run** `pnpm vitest run src/pages/ResultsStage.test.tsx` - all PASS. Then `pnpm typecheck`. Commit:

```bash
git add src/splitsmith/ui_static/src/pages/ResultsStage.tsx src/splitsmith/ui_static/src/pages/ResultsStage.test.tsx
git commit -m "fix(ui): undo double-tap guard, stale-close guard, friendly patch error"
```

---

### Task 4: Gate + PR

- [ ] **Step 1:** `cd src/splitsmith/ui_static && pnpm typecheck && pnpm test && pnpm exec eslint src/lib/coachPatch.ts src/components/MobileConfirmSheet.tsx src/components/results/ src/pages/ResultsStage.tsx` - clean (warnings only if pre-existing on main).
- [ ] **Step 2:** ASCII sweep of added lines (no em dash, no `--` in prose).
- [ ] **Step 3:** Push `fix/826-reclassify-polish`, open PR titled "fix: interval reclassify polish (#826)" with a body listing the items closed and noting the real-phone visual pass stays open on #826; merge when CI is green; comment on #826 with what shipped (leave the issue open only if the phone pass should remain tracked, else close it and note the pass moves to the next release checklist - decision: KEEP #826 OPEN, comment progress).

## Self-review notes

- All eight #826 code items are covered: trim symmetry (T1), regex (T1), busy feedback (T2), roving tabindex (T2), undo busy-guard (T3), stale-close (T3), friendly error (T3), applyShotPatch test (T3).
- MobileConfirmSheet's new prop is optional-with-default: Triage/MobileBeepReview call sites compile unchanged.
- The roving-tabindex test asserts focus movement, not just attributes - catches the requestAnimationFrame wiring.
