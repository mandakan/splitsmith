# Route-Suite Timeout Budget (#878) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the route-suite's import budget one home, so file seven inherits it instead of starting at the default and being discovered red under load by whoever trips it.

**Architecture:** `vitest.config.ts` grows two `test.projects` entries -- a `routes` project globbing `src/App.routes.*.test.tsx` that carries the budget, and a default project for the other 82 files at vitest's defaults. The six inline budgets come out. The number is measured first, not inherited from folklore.

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

Baseline already taken on this box, 2026-08-14, idle:

| what | wall | note |
|---|---|---|
| one route file alone | 3.6s | 2.1s of it transform |
| all six together | 4.8s | but **11.4s cumulative transform** -- ~2s per file for the same tree |
| full SPA suite | 33s | 88 files, 517 tests, 822% CPU |

The route files' own tests run 30-600ms each. The cost is the import: `App.tsx` eagerly imports about 30 page modules with no lazy boundary anywhere.

What is missing is the hook's own elapsed time at its worst, under a fully loaded run. That is what this task gets.

**Files:**
- Temporarily modify (all reverted by the end of this task): the six `src/App.routes.*.test.tsx` files

**Interfaces:**
- Produces: `ROUTE_TREE_BUDGET_MS`, one integer, which Task 2 writes into the config. Also produces the worst observed hook time, which goes in the config comment as the number the budget is derived from.

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

Apply this rule, so the number has a stated origin:

```
ROUTE_TREE_BUDGET_MS = max(15_000, roundUpTo5s(4 * worst_observed_ms))
```

Four times the worst observation, rounded up to the next 5s, floored at 15s. The multiple is generous on purpose: the failure this guards against is machine load, which is unbounded, and the cost of a too-high ceiling is only that a genuine hang in these six files takes longer to report.

If the rule lands on 30s, the budget stays 30s -- and is now a derived number rather than a remembered one. Write down both the worst observation and the resulting budget; both go in the config comment.

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
- Modify: `src/splitsmith/ui_static/vitest.config.ts` (the `test` block, currently `environment` + `setupFiles`)
- Modify: `src/App.routes.test.tsx:90-92`, `.account:70-78`, `.share:85-93`, `.pickup:82-90`, `.hosted:89-97` (remove the inline budget and its comment)
- Modify: `src/App.routes.modegate.test.tsx:105-113` (remove the per-test budget and its comment)

**Interfaces:**
- Consumes: `ROUTE_TREE_BUDGET_MS` and the worst observation from Task 1.

- [ ] **Step 1: Rewrite the config's `test` block**

Replace the `test` block in `vitest.config.ts` with:

```ts
  test: {
    // Component tests (MatchExport.test.tsx) need a DOM; plain-logic
    // suites (matchExportModel.test.ts, api.compareGrid.test.ts, ...)
    // run fine under jsdom too, so one environment covers both rather
    // than splitting test files across a node/jsdom pool.
    environment: "jsdom",
    setupFiles: ["./src/testSetup.ts"],
    // Two projects so the route suite's import budget has ONE home
    // (#878). Six files had grown their own copy of a 30s timeout, each
    // added by whoever next hit "Hook timed out" on a loaded box, and
    // nothing stopped file seven from starting at the default and being
    // discovered the same way.
    //
    // The route files are genuinely different from the other 82: each
    // awaits `import("@/App")`, which pulls the whole route tree --
    // about 30 eagerly-imported page modules -- through vite's
    // transform. Measured 2026-08-14: ~2s of transform per file on an
    // idle box, and 11.4s cumulative across the six, because they each
    // pay for the same tree. Under the full 88-file suite that competes
    // with everything else and is what blew the default.
    //
    // Worst observed hook time under a loaded full-suite run:
    // <WORST_MS>ms. Budget is 4x that, rounded up to 5s -- generous
    // because the thing it absorbs is machine load, which has no
    // ceiling, and a too-high budget only costs latency on a report
    // nobody is waiting for.
    //
    // Deliberately NOT global: a genuine hang in the other 82 files
    // should still report at vitest's defaults rather than sitting for
    // half a minute.
    //
    // Both timeouts, not just hookTimeout: five of the six do the import
    // in `beforeAll`, but modegate does it inside its single `it` and
    // needs testTimeout. They are different defaults too (5s vs 10s).
    projects: [
      {
        extends: true,
        test: {
          name: "unit",
          include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
          exclude: ["src/App.routes.*.test.tsx"],
        },
      },
      {
        extends: true,
        test: {
          name: "routes",
          include: ["src/App.routes.*.test.tsx"],
          hookTimeout: ROUTE_TREE_BUDGET_MS,
          testTimeout: ROUTE_TREE_BUDGET_MS,
        },
      },
    ],
  },
```

Define the constant above `export default defineConfig({`:

```ts
// The route suite's import budget. See the comment on `test.projects`
// for how this number was derived; it is a measurement times four, not
// a remembered value.
const ROUTE_TREE_BUDGET_MS = 30_000;
```

Substitute Task 1's actual numbers for `ROUTE_TREE_BUDGET_MS` and `<WORST_MS>`.

- [ ] **Step 2: Verify the split collects everything**

Run: `pnpm test`
Expected: **88 test files, 517 tests, all passing** -- the same totals as before the change. Each file must appear under exactly one project.

A lower file count means the two `include` globs do not cover what the old default did; a higher one means a file is being collected twice. Either way, fix the globs before continuing -- a config that silently stops running tests is strictly worse than six duplicated timeouts.

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

Expected: clean. `vitest.config.ts` is typechecked, so a wrong `projects` shape surfaces here.

- [ ] **Step 7: Commit**

```bash
git add vitest.config.ts src/App.routes.test.tsx src/App.routes.account.test.tsx \
  src/App.routes.share.test.tsx src/App.routes.pickup.test.tsx \
  src/App.routes.hosted.test.tsx src/App.routes.modegate.test.tsx
git commit -m "test(ui): one home for the route suite's import budget"
```

---

### Task 3: Prove file seven inherits it

The six still passing is not evidence -- they passed before. The regression this closes is that a *new* route test file starts at the default and gets discovered red by whoever trips it. That is the only claim worth testing, and it needs a file that has never named a timeout.

**Files:**
- Create then delete: `src/App.routes.inherit.test.tsx` (a probe, not a committed test)
- Temporarily modify then revert: `vitest.config.ts`

**Interfaces:**
- Consumes: Task 2's config.

- [ ] **Step 1: Write the probe file**

Create `src/splitsmith/ui_static/src/App.routes.inherit.test.tsx`:

```tsx
import { beforeAll, describe, expect, it } from "vitest";

describe("budget inheritance probe", () => {
  beforeAll(async () => {
    await import("@/App");
  });

  it("imported the route tree without naming a timeout", () => {
    expect(true).toBe(true);
  });
});
```

- [ ] **Step 2: Confirm it passes**

Run: `pnpm vitest run src/App.routes.inherit`
Expected: 1 file, 1 test, passing.

This alone proves nothing -- it would also pass at the 10s default on an idle box. Step 3 is the actual test.

- [ ] **Step 3: Shrink the budget and watch the probe fail**

In `vitest.config.ts`, temporarily set `const ROUTE_TREE_BUDGET_MS = 1;`.

Run: `pnpm vitest run src/App.routes.inherit`
Expected: FAIL with `Hook timed out in 1ms`.

That failure is the evidence: a file that names no timeout was governed by the config's budget. If it passes, the `routes` project's `include` glob is not matching the new file and the whole change is decorative.

- [ ] **Step 4: Confirm the other project is unaffected**

Still at `ROUTE_TREE_BUDGET_MS = 1`:

Run: `pnpm vitest run src/lib`
Expected: passing. The 1ms budget must not reach the `unit` project -- that is what `exclude` is for, and this is the assertion that it works.

- [ ] **Step 5: Restore the budget and delete the probe**

```bash
git checkout -- vitest.config.ts
rm src/App.routes.inherit.test.tsx
pnpm test
```

Expected: 88 files, 517 tests, passing. `git status --short` shows nothing beyond Task 2's committed work.

The probe is not kept: a permanent file whose only job is to import the route tree would add a seventh ~2s transform to every run to assert something the config already states.

- [ ] **Step 6: Record the drill in the PR body**

Paste the `Hook timed out in 1ms` output from Step 3 and the passing `src/lib` run from Step 4. Without them this PR reads as "deleted six timeouts and added a config option", and a reviewer has no way to tell whether the config reaches new files.

- [ ] **Step 7: Open the PR**

```bash
git push -u origin test/route-suite-timeout-878
gh pr create --fill --title "test(ui): the route-suite beforeAll timeout workaround is copy-pasted across six files (#878)"
gh run watch
```

Expected: the `spa` job green -- `pnpm typecheck`, `pnpm lint`, `pnpm test`, `pnpm build` all pass.

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

- No `src/App.routes.*.test.tsx` file names a timeout.
- `pnpm test` still collects 88 files / 517 tests.
- A new route test file has been observed inheriting the budget, and a non-route file has been observed not inheriting it.
- The budget in the config is a stated multiple of a recorded measurement, and the comment says which.
- The lazy-import follow-up is filed with the measurement attached.
