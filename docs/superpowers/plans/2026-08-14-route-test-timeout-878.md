# Route-Suite Timeout Budget (#878) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the route-suite's import budget one home, so file seven inherits it instead of starting at the default and being discovered red under load by whoever trips it.

**Architecture:** `vite.config.ts` gains one `TEST_BUDGET_MS` constant driving suite-wide `hookTimeout` and `testTimeout`. The six inline budgets come out. The number is measured first, not inherited from folklore -- and the measurement is what collapsed this from a two-project split to a single constant (see "Second scope change" below).

**Tech Stack:** vitest 4.1, vite 6, jsdom, React Testing Library, pnpm.

## Global Constraints

- Work happens in `src/splitsmith/ui_static/`. All `pnpm` commands run from there.
- `pnpm`, never `npm`. The SPA CI job runs `pnpm typecheck`, `pnpm lint`, `pnpm test`, `pnpm build` -- all four must stay green.
- **No new dependencies.**
- No production source change. `App.tsx` is not touched by this plan.
- Branch: `test/route-suite-timeout-878`, cut from `main` **after #877 merges**. Not stacked.
- Conventional-commit subjects. Squash bodies stay short -- a many-commit body breaks release-please's parser.

## Correction to the issue body before you start

#878 says all six files apply the workaround "in `beforeAll`". Five do. `App.routes.modegate.test.tsx` does the import **inside the test** and carries a per-test `{ timeout: 30_000 }` at line 112 instead:

```tsx
  it(
    "holds the route tree on standby until /api/server/features resolves",
    { timeout: 30_000 },
    async () => {
      window.history.pushState({}, "", "/pick");
      const { App } = await import("@/App");
```

So a `hookTimeout` alone covers five of six and leaves modegate exactly where it is. The project needs **both** `hookTimeout` and `testTimeout`. These are also two different vitest defaults -- `testTimeout` is 5s, `hookTimeout` is 10s -- which is why modegate's own comment says 5s and the issue says 10s. Both are right about their own file.

---

### Task 1: Measure what the budget should be

30s is an observation from a loaded box, not a measurement. Nobody has looked at where the time goes or at what the hook actually costs at its worst. Setting the number without measuring would move the folklore into a config file rather than retire it.

Baseline taken on this box, 2026-08-14, idle:

| what | wall | note |
|---|---|---|
| one route file alone | 3.6s | 2.1s of it transform |
| all six together | 4.8s | but **11.4s cumulative transform** -- ~2s per file for the same tree |
| full SPA suite | 33s | 88 files, 517 tests, 822% CPU |

The route files' own tests run 30-600ms each. The cost is the import: `App.tsx` eagerly imports about 30 page modules with no lazy boundary anywhere.

What is missing is the hook's own elapsed time at its worst, under a fully loaded run. That is what this task gets.

## Scope change, 2026-08-14 (after #876 and #877 merged)

Re-measuring on a fresh branch cut from `main` invalidated two of this
plan's assumptions. Both change the fix.

**The suite is bigger.** 104 files / 604 tests, not 88 / 517 -- Lab work
(#890, #891) and the mobile audit UI landed in between. Every count in
Task 2 and Task 3 must be re-derived, not copied from this document.

**The problem is no longer confined to the route glob.**
`src/pages/MobileAudit.test.tsx` fails 2 tests under full-suite load and
passes 13/13 in isolation, reproducibly (3 of 3 runs), with
`Test timed out in 5000ms` -- vitest's default `testTimeout`. It is not a
route file, so the `App.routes.*` project this plan proposed would not
have covered it, and the next victim would be rediscovered the same way.
That progression is precisely what #878's issue body complains about.

**So the design changes:** raise the *global* `testTimeout` and
`hookTimeout` to a measured value, and keep the larger route-tree budget
scoped to the six files via `projects`. This overrides the original
"deliberately NOT global" reasoning below. That reasoning was not wrong
-- a genuine hang elsewhere now takes the global budget to report
instead of 5s -- but a suite that fails spuriously is the worse of the
two, and the global number is derived from measurement rather than
picked.

## Second scope change, 2026-08-14: the measurement killed the projects split

Task 1 ran and inverted the premise the whole design rested on:

| | worst observed, 3 loaded full runs | derived budget |
|---|---|---|
| route-tree hook (`App.routes.pickup`) | **4,428 ms** | 20,000 ms |
| ordinary test (`MobileAudit`, 409-on-save) | **6,573 ms** | 30,000 ms |

**The route files are not the expensive ones.** The worst ordinary test
is ~50% slower than the worst route hook. A `routes` project carrying a
"bigger" budget would therefore *lower* those six files from the global
30s to 20s -- the exact opposite of what it exists for.

So the projects split goes. **One global budget, 30,000 ms, and nothing
else.** That is simpler, it covers every file including the ones nobody
has thought about yet, and it means file seven inherits it by existing
rather than by matching a glob.

What this retires, and it is the more interesting half: "the route files
are special because they import the whole route tree" was itself
folklore. The tree costs ~2s of transform per file, which is real, but
it never was the suite's worst case. Nobody had measured either number.

Everything below that describes `test.projects`, a `routes` project, a
`unit` project, or two constants is superseded: there is one constant,
`TEST_BUDGET_MS`, set at the root of the `test` block.

**Verification is CI-green, not local-green.** The `MobileAudit`
failure reproduces only on this box; `main`'s `spa` job is green.
Per the human partner's direction, local-only failures are not chased as
separate defects and do not gate this work. The global raise is still
justified on CI's own evidence: #878's issue body records three files
timing out in CI, and `App.routes.modegate.test.tsx`'s comment notes
CI's budget was raised once already for exactly this. MobileAudit is
expected to stop failing locally as a side effect; that is a bonus, not
the acceptance criterion, and no MobileAudit-specific work is in scope.

**Files:**
- Temporarily modify (all reverted by the end of this task): the six `src/App.routes.*.test.tsx` files

**Interfaces:**
- Produces: two measurements and the budgets derived from them. Task 2 consumes only the global one (see the second scope change).

- [ ] **Step 1: Instrument the import**

In each of the five files with a `beforeAll` (`App.routes.test.tsx:90`, `.account:76`, `.share:91`, `.pickup:88`, `.hosted:95`), wrap the import:

```tsx
  beforeAll(async () => {
    const t0 = performance.now();
    await import("@/App");
    console.log(`[measure] ${import.meta.url} ${Math.round(performance.now() - t0)}ms`);
  }, 30_000);
```

In `App.routes.modegate.test.tsx`, wrap the in-test import the same way:

```tsx
      const t0 = performance.now();
      const { App } = await import("@/App");
      console.log(`[measure] ${import.meta.url} ${Math.round(performance.now() - t0)}ms`);
```

- [ ] **Step 2: Measure under a full-suite run**

The full suite is the load that matters -- these files time out because they compete with the other 82, not on their own.

```bash
pnpm test 2>&1 | grep '\[measure\]'
```

Run it three times and keep the worst single number across all runs. One sample is a sample, not a maximum.

Expected: values in the low seconds on an idle box. On a loaded box the same command is what produced the original 10s timeouts, so if this box is busy, so much the better -- record what you see.

- [ ] **Step 3: Derive the budget**

Two numbers now, not one, per the scope change above.

**The route budget**, from the worst observed *hook* time:

```
ROUTE_TREE_BUDGET_MS = max(15_000, roundUpTo5s(4 * worst_hook_ms))
```

**The global budget**, from the worst observed *ordinary test* time across the rest of the suite -- measure it the same way, and include `src/pages/MobileAudit.test.tsx`, which is the file currently blowing the 5s default:

```
GLOBAL_BUDGET_MS = max(15_000, roundUpTo5s(4 * worst_ordinary_test_ms))
```

Four times the worst observation in both cases, rounded up to the next 5s, floored at 15s. The multiple is generous on purpose: the failure this guards against is machine load, which is unbounded, and the cost of a too-high ceiling is only that a genuine hang takes longer to report.

If a rule lands on 30s, that budget stays 30s -- and is now derived rather than remembered. Write down both worst observations and both resulting budgets; all four numbers go in the config comment.

- [ ] **Step 4: Revert the instrumentation**

```bash
git checkout -- src/App.routes.test.tsx src/App.routes.account.test.tsx \
  src/App.routes.share.test.tsx src/App.routes.pickup.test.tsx \
  src/App.routes.hosted.test.tsx src/App.routes.modegate.test.tsx
git status --short
```

Expected: clean. Nothing from this task is committed -- it produced two numbers, which Task 2 consumes.

---

### Task 2: One home for the budget

**Files:**
- Modify: `src/splitsmith/ui_static/vite.config.ts` (the `test` block, currently `environment` + `setupFiles`)
- Modify: `src/App.routes.test.tsx:90-92`, `.account:70-78`, `.share:85-93`, `.pickup:82-90`, `.hosted:89-97` (remove the inline budget and its comment)
- Modify: `src/App.routes.modegate.test.tsx:105-113` (remove the per-test budget and its comment)

**Interfaces:**
- Consumes: Task 1's measurements. Per the second scope change, only ONE budget is written: `TEST_BUDGET_MS = 30_000`, the global one. The 20,000 ms route figure Task 1 also derived is deliberately NOT used -- it is lower than the global budget, so scoping it would reduce those files' allowance.

- [ ] **Step 1: Rewrite the config's `test` block**

One constant, one pair of timeouts, no projects. Replace the `test` block in `vite.config.ts` with:

```ts
  test: {
    // Component tests (MatchExport.test.tsx) need a DOM; plain-logic
    // suites (matchExportModel.test.ts, api.compareGrid.test.ts, ...)
    // run fine under jsdom too, so one environment covers both rather
    // than splitting test files across a node/jsdom pool.
    environment: "jsdom",
    setupFiles: ["./src/testSetup.ts"],
    // One budget for the whole suite (#878). Six files had grown their
    // own copy of a 30s timeout, each added by whoever next hit a red
    // run on a loaded box, and nothing stopped file seven from starting
    // at the default and being discovered the same way.
    //
    // The number is measured, not remembered. Worst observations across
    // three loaded full-suite runs, 2026-08-14:
    //
    //   ordinary test  6573 ms  MobileAudit.test.tsx, 409-on-save
    //   route-tree hook 4428 ms  App.routes.pickup.test.tsx
    //
    // Budget is 4x the worst, rounded up to 5s: 30s. The multiple is
    // generous because what it absorbs is machine load, which has no
    // ceiling, and a too-high budget only costs latency on a report
    // nobody is waiting for.
    //
    // Deliberately NOT scoped to the route files, which is where this
    // change started. Those six await `import("@/App")` and pull ~30
    // eagerly-imported page modules through vite's transform (~2s each,
    // 11.4s cumulative), so they looked like the expensive ones -- but
    // measuring said the worst ordinary test is ~50% slower than the
    // worst route hook. A per-glob project would have *lowered* their
    // budget while leaving MobileAudit.test.tsx failing at the 5s
    // default. "The route files are special" was folklore too.
    //
    // Both timeouts, not just hookTimeout: five of the six route files
    // import in `beforeAll`, modegate imports inside its single `it`,
    // and MobileAudit's failures are plain tests. The defaults differ
    // (testTimeout 5s, hookTimeout 10s), so both need saying.
    hookTimeout: TEST_BUDGET_MS,
    testTimeout: TEST_BUDGET_MS,
  },
```

Define the constant above `export default defineConfig({`:

```ts
// Suite-wide timeout budget: 4x the worst test observed under load.
// See the comment on `test` for the measurements it comes from.
const TEST_BUDGET_MS = 30_000;
```

- [ ] **Step 2: Verify nothing stopped being collected**

Record the pre-change totals on this branch first (`pnpm vitest run`) -- the counts elsewhere in this document are stale. At the time of writing: **104 files / 604 tests**, with 2 failing under load in `MobileAudit.test.tsx`.

Run: `pnpm test`
Expected: the same file and test totals you recorded, **and the two `MobileAudit` failures gone** -- they were failing at the 5s default, and 30s is six times that. If they still fail, the budget is not reaching ordinary tests and Step 1 is wrong; investigate before continuing rather than moving on.

A changed file count means something stopped being collected. That is strictly worse than six duplicated timeouts -- fix it before continuing.

- [ ] **Step 3: Verify filtering still works**

Run: `pnpm vitest run src/App.routes`
Expected: 6 files, 17 tests, passing. This is the command anyone debugging these files reaches for.

- [ ] **Step 4: Remove the six inline budgets**

In the five `beforeAll` files, drop the timeout argument and the `#867 final review M10` comment block above it (the config now carries that reasoning). Each becomes:

```tsx
  beforeAll(async () => {
    await import("@/App");
  });
```

In `App.routes.modegate.test.tsx`, remove the `{ timeout: 30_000 }` argument and the four-line comment above it, so the `it` returns to its two-argument form:

```tsx
  it("holds the route tree on standby until /api/server/features resolves", async () => {
    window.history.pushState({}, "", "/pick");
    const { App } = await import("@/App");
```

Mind the indentation: removing the options argument un-nests the callback body by two spaces.

- [ ] **Step 5: Verify the six still pass on the config's budget alone**

Run: `pnpm vitest run src/App.routes`
Expected: 6 files, 17 tests, passing -- now with no file naming a timeout.

- [ ] **Step 6: Typecheck and lint**

```bash
pnpm typecheck
pnpm lint
```

Expected: clean. `vite.config.ts` is typechecked, so a malformed `test` block surfaces here.

- [ ] **Step 7: Commit**

```bash
git add vite.config.ts src/App.routes.test.tsx src/App.routes.account.test.tsx \
  src/App.routes.share.test.tsx src/App.routes.pickup.test.tsx \
  src/App.routes.hosted.test.tsx src/App.routes.modegate.test.tsx
git commit -m "test(ui): one home for the route suite's import budget"
```

---

### Task 3: Prove file seven inherits it

The six still passing is not evidence -- they passed before. The regression this closes is that a *new* test file starts at vitest's default and gets discovered red by whoever trips it. That is the only claim worth testing, and it needs a file that has never named a timeout.

With the projects split gone, the check is simpler than the original plan's: there is one budget, so one probe proves it, and it should be a **non-route** file -- that is the case the original design would have missed.

**Files:**
- Create then delete: `src/inherit.probe.test.ts` (a probe, not a committed test)
- Temporarily modify then revert: `vite.config.ts`

**Interfaces:**
- Consumes: Task 2's config.

- [ ] **Step 1: Write the probe file**

Deliberately not named `App.routes.*` -- a non-route file is what proves the budget is genuinely global. Create `src/splitsmith/ui_static/src/inherit.probe.test.ts`:

```ts
import { describe, expect, it } from "vitest";

describe("budget inheritance probe", () => {
  it("runs under the suite-wide budget without naming a timeout", async () => {
    await new Promise((r) => setTimeout(r, 6_000));
    expect(true).toBe(true);
  });
});
```

The 6s sleep is above vitest's 5s default and below the 30s budget, so this file passes only if the config's budget is reaching it.

- [ ] **Step 2: Confirm it passes**

Run: `pnpm vitest run src/inherit.probe`
Expected: 1 file, 1 test, passing, taking just over 6s.

**This is the load-bearing assertion**, not a formality: against vitest's untouched 5s default this same file fails with `Test timed out in 5000ms`. It passing is the proof the budget applies to a file that names nothing and is not a route test.

- [ ] **Step 3: Shrink the budget and watch it fail**

In `vite.config.ts`, temporarily set `const TEST_BUDGET_MS = 1;`.

Run: `pnpm vitest run src/inherit.probe`
Expected: FAIL with `Test timed out in 1ms`.

Two assertions in one: the config reaches the file, and it reaches it *as the timeout* rather than by coincidence.

- [ ] **Step 4: Confirm the route files are governed by the same constant**

Still at `TEST_BUDGET_MS = 1`:

Run: `pnpm vitest run src/App.routes`
Expected: FAIL -- hooks timing out in 1ms.

This is what confirms the six route files now take their budget from the config rather than from the inline values Task 2 deleted. If they pass here, an inline timeout survived somewhere.

- [ ] **Step 5: Restore the budget and delete the probe**

```bash
git checkout -- vite.config.ts
rm src/inherit.probe.test.ts
pnpm test
```

Expected: the totals recorded in Task 2 Step 2, all passing. `git status --short` shows nothing beyond Task 2's committed work.

The probe is not kept: a permanent test whose only job is to sleep for 6 seconds would add 6 seconds to every run to assert something the config already states.

- [ ] **Step 6: Record the drill in the PR body**

Paste the `Test timed out in 1ms` output from Step 3, the route-file failure from Step 4, and the passing 6s probe from Step 2. Without them this PR reads as "deleted six timeouts and added a config option", and a reviewer has no way to tell whether the config reaches anything.

- [ ] **Step 7: Open the PR**

```bash
git push -u origin test/route-suite-timeout-878
gh pr create --fill --title "test(ui): the route-suite beforeAll timeout workaround is copy-pasted across six files (#878)"
gh run watch
```

Expected: the `spa` job green -- `pnpm typecheck`, `pnpm lint`, `pnpm test`, `pnpm build` all pass. That job is the acceptance criterion.

---

### Task 4: File the follow-up this measurement earned

Task 1 measured a cost nobody had quantified. That number is the evidence for a change this plan deliberately does not make, and it should not be lost with the branch.

**Files:** none.

- [ ] **Step 1: Open the issue**

```bash
gh issue create --title "perf(ui): App.tsx eagerly imports ~30 page modules, so every route test pays for the whole tree" --body '...'
```

The body should carry:

- The measurement: ~2s of vite transform per route test file, 11.4s cumulative across the six for the same tree, because `App.tsx` imports every page eagerly with no `React.lazy` anywhere. Full suite context: 88 files, 517 tests, 33s.
- What lazy boundaries would buy: the transform cost drops for all six route test files at once, and the production bundle gains route-level code splitting.
- What they would cost, which is why #878 did not do it: it changes production rendering, needs Suspense fallbacks, and turns the route assertions async (`findBy` rather than `getBy`). That is a product change, and #878 was test-infra hygiene -- doing it there would have shipped a rendering change under a `test(ui):` subject.
- A link to #878 and to this plan.

- [ ] **Step 2: Reference it from the closing comment on #878**

So the next person reading #878's "reduce the cost" suggestion finds where that thread went instead of re-deriving it.

---

## Done when

- No `src/App.routes.*.test.tsx` file names a vitest hook/test timeout, and neither does any other test file -- this does not cover RTL's own `waitFor({ timeout })` calls, which stay: `App.routes.pickup.test.tsx:94`'s `{ timeout: FEATURES_DELAY_MS - 50 }` asserts a redirect lands before a real 300 ms `setTimeout`, a wall-clock assertion the global budget structurally cannot replace.
- `vite.config.ts` carries exactly one budget constant, and its comment states the two measurements it came from.
- `pnpm test` collects the same file and test totals recorded at the start of Task 2 (104 / 604 at the time of writing -- re-derive, do not copy).
- A **non-route** probe file that names no timeout has been observed passing a 6s test, and failing when the constant is set to 1ms. That pair is what proves the budget is global and is genuinely the timeout.
- The six route files have been observed failing at a 1ms budget, proving they now take it from the config rather than from a surviving inline value.
- The `spa` job is green in CI. That is the acceptance criterion; local-only failures are noted, not chased.
- The lazy-import follow-up is filed with the measurement attached.
