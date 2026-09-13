# UX PR 1: Defects Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the six shipped defects found during the UX review (spec section 7) as one PR, before any redesign work starts.

**Architecture:** Five small SPA fixes (Tailwind class and copy changes inside existing components, each with a vitest render test) plus one server fix (the beep-queue confirm endpoint reuses the chaining logic of the per-video reviewed endpoint). No new components, no new dependencies, no route changes.

**Tech Stack:** React 19 + Tailwind 4 + vitest (`corepack pnpm` in `src/splitsmith/ui_static`); FastAPI + pytest (`uv run pytest` at repo root).

**Spec:** `docs/superpowers/specs/2026-09-13-ux-restructure-and-visual-budget-design.md` (section 7, "Defects to fix first").

## Global Constraints

- Python 3.11+, `uv` only (never `pip`); Black line length 110; Ruff.
- SPA gates: `corepack pnpm typecheck`, `corepack pnpm lint`, `corepack pnpm test`, `corepack pnpm build` in `src/splitsmith/ui_static`. Bare `pnpm` is not on PATH.
- No new dependencies.
- ASCII punctuation in all copy and comments (`--` not em dash).
- `uv run` rewrites `uv.lock`; run `git checkout uv.lock` before staging, never `git add -A`.
- Playwright MCP writes `.playwright-mcp/` into the repo root; delete it before committing.
- Every fix has a test that fails against the pre-change code. Tasks 2-7 each state the expected failure.
- Commit messages: conventional commits, ending with the attribution lines from the session (`Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>` and the `Claude-Session:` line).

---

### Task 1: Branch and commit the spec

**Files:**
- Commit: `docs/superpowers/specs/2026-09-13-ux-restructure-and-visual-budget-design.md`
- Commit: `docs/superpowers/plans/2026-09-13-ux-pr1-defects.md`

- [ ] **Step 1: Create the branch from main**

```bash
cd /home/mathias/work/splitsmith
git checkout main && git pull --ff-only
git checkout -b ux/pr1-defects
```

- [ ] **Step 2: Commit the spec and this plan**

```bash
git add docs/superpowers/specs/2026-09-13-ux-restructure-and-visual-budget-design.md docs/superpowers/plans/2026-09-13-ux-pr1-defects.md
git commit -m "docs: UX restructure spec and PR 1 defects plan"
```

---

### Task 2: Stage stats strip cannot be crushed, and no orphan tile on mobile

**Files:**
- Modify: `src/splitsmith/ui_static/src/components/results/StageStats.tsx:40-70`
- Test: `src/splitsmith/ui_static/src/components/results/StageStats.test.tsx`

**Why:** `ResultsStage.tsx:714` puts `StageStats` first in a `flex flex-col lg:overflow-y-auto` column with a max height. The strip's root has `overflow-hidden` (for the rounded corners), which makes its flex `min-height` resolve to 0, so the column shrinks it to 29 px and the numbers are clipped under the labels. `shrink-0` restores the content height. On mobile, five tiles in two columns leave the fifth alone; giving the stage time the full first row makes the rows even and leads with the stage time.

**Interfaces:**
- Produces: unchanged `StageStats` props. Root element now carries `shrink-0`; the stage-time cell carries `col-span-2 md:col-span-1`; the avg-split cell no longer spans.

- [ ] **Step 1: Write the failing tests**

Append to `StageStats.test.tsx`:

```tsx
  it("refuses to shrink inside a scroll column (the desktop clip, spec 7.1)", () => {
    const { container } = render(
      <StageStats stageTime={32.09} shotCount={30} draw={1.97} fastestSplit={0.249} avgSplit={0.386} />,
    );
    // jsdom has no layout; the class is the contract. ResultsStage mounts
    // this strip first in a `flex flex-col lg:overflow-y-auto` column, and
    // an overflow-hidden flex child with the default shrink collapses to
    // its labels there.
    expect(container.firstElementChild).toHaveClass("shrink-0");
  });

  it("leads with the stage time on a full row so five tiles never leave an orphan", () => {
    render(
      <StageStats stageTime={32.09} shotCount={30} draw={1.97} fastestSplit={0.249} avgSplit={0.386} />,
    );
    const stageTime = screen.getByText("Stage time").parentElement;
    const avgSplit = screen.getByText("Avg split").parentElement;
    expect(stageTime).toHaveClass("col-span-2");
    expect(stageTime).toHaveClass("md:col-span-1");
    expect(avgSplit).not.toHaveClass("col-span-2");
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd src/splitsmith/ui_static && corepack pnpm vitest run src/components/results/StageStats.test.tsx`
Expected: 2 failures -- `expected <div> to have class "shrink-0"` and `expected <div> to have class "col-span-2"`.

- [ ] **Step 3: Apply the class changes**

In `StageStats.tsx` replace the `StageStats` function body:

```tsx
export function StageStats({ stageTime, shotCount, draw, fastestSplit, avgSplit }: StageStatsProps) {
  return (
    <div className="grid shrink-0 grid-cols-2 overflow-hidden rounded-xl border border-rule-strong bg-surface-2 md:grid-cols-5">
      <Cell
        label="Stage time"
        value={stageTime != null ? `${stageTime.toFixed(2)}s` : "-"}
        className="col-span-2 border-b md:col-span-1 md:border-b-0 md:border-r"
      />
      <Cell
        label="Shots"
        value={String(shotCount)}
        className="border-b border-r md:border-b-0"
      />
      <Cell
        label="Draw"
        value={draw != null ? `${draw.toFixed(2)}s` : "-"}
        className="border-b md:border-b-0 md:border-r"
      />
      <Cell
        label="Fastest split"
        value={fastestSplit != null ? `${fastestSplit.toFixed(3)}s` : "-"}
        className="border-r md:border-b-0"
      />
      <Cell
        label="Avg split"
        value={avgSplit != null ? `${avgSplit.toFixed(3)}s` : "-"}
      />
    </div>
  );
}
```

Update the header comment's last sentence from "2-wide grid on mobile, one row of five at md+." to "Stage time on its own row then 2-wide on mobile, one row of five at md+. `shrink-0` because ResultsStage mounts it in a scroll column."

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/splitsmith/ui_static && corepack pnpm vitest run src/components/results/StageStats.test.tsx`
Expected: 4 passed.

- [ ] **Step 5: Verify the layout in a browser (the jsdom test cannot)**

Create a temporary harness (delete both files in step 7):

`src/splitsmith/ui_static/stats-harness.html`:
```html
<!doctype html><html><head><meta charset="utf-8"><title>stats harness</title></head>
<body><div id="root"></div><script type="module" src="/src/__stats_harness__.tsx"></script></body></html>
```

`src/splitsmith/ui_static/src/__stats_harness__.tsx`:
```tsx
import { createRoot } from "react-dom/client";

import { StageStats } from "@/components/results/StageStats";

import "./styles/index.css";

// Same shell as ResultsStage.tsx:714 -- a capped scroll column with a
// tall sibling after the strip.
createRoot(document.getElementById("root")!).render(
  <div style={{ width: 451 }}>
    <div className="flex flex-col gap-4 lg:max-h-[calc(100dvh-86px-2rem)] lg:overflow-y-auto">
      <StageStats stageTime={32.09} shotCount={30} draw={1.97} fastestSplit={0.249} avgSplit={0.386} />
      <div style={{ height: 2000, background: "#14171C" }} />
    </div>
  </div>,
);
```

Run: `cd src/splitsmith/ui_static && corepack pnpm dev` (lands on port 5175 because 5173 is taken by nginx), then with the Playwright MCP at 1440x900 open `http://localhost:5175/stats-harness.html` and evaluate:

```js
() => document.querySelector("div.grid.shrink-0").getBoundingClientRect().height
```

Expected: >= 60 (was 29 before the fix; to see the before, temporarily remove `shrink-0` from the harness's rendered class via devtools or `git stash` the component change and re-evaluate -- record both numbers in the PR).

Then resize to 390x844 and take a screenshot of the strip: stage time on its own full-width row, then two rows of two.

- [ ] **Step 6: Stop the dev server, delete the harness and the Playwright scratch dir**

```bash
rm src/splitsmith/ui_static/stats-harness.html src/splitsmith/ui_static/src/__stats_harness__.tsx
rm -rf .playwright-mcp
```

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/ui_static/src/components/results/StageStats.tsx src/splitsmith/ui_static/src/components/results/StageStats.test.tsx
git commit -m "fix(results): keep the stage stats strip readable in the scroll column

The strip sat first in ResultsStage's capped overflow-y-auto column with
overflow-hidden for its corners, so its flex min-height resolved to 0 and
the column crushed it to the label row: draw, fastest and average split
were invisible at 1440x900 (29 px of a 75 px grid, measured). shrink-0
restores the content height. Stage time now takes the full first row on
mobile so five tiles never leave one alone."
```

---

### Task 3: Shooters page shows a loading state, not "0 active"

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Shooters.tsx:316-330, 373-380, 725-770`
- Test: `src/splitsmith/ui_static/src/pages/Shooters.capabilities.test.tsx`

**Why:** `data` starts as `null` and `active = data?.shooters ?? []`, so until `listMatchShooters` resolves the page renders "0 active" and an empty Active shooters section as if that were the answer. On a cold hosted server that lasted 7+ s during the review. Separately, the camera row renders `-{missing} missing`, which reads as a negative count ("-1 missing").

**Interfaces:**
- Consumes: `api.listMatchShooters()` (unchanged).
- Produces: while `data === null` and no error, the header says "Loading shooters..." and the Active shooters section renders one line "Loading shooters..." (role `status`). The camera chip reads `1 missing`.

- [ ] **Step 1: Write the failing tests**

Append to the `describe("Shooters capability gating", ...)` block in `Shooters.capabilities.test.tsx` (inside it, after the last `it`):

```tsx
  it("renders a loading state, never '0 active', before the shooter list resolves", () => {
    // Never-resolving promise: the page must not present an empty answer
    // while the request is in flight (7 s on a cold hosted server).
    vi.mocked(api.listMatchShooters).mockReturnValue(new Promise(() => {}));
    renderShooters({});
    expect(screen.queryByText(/0 active/)).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent(/loading shooters/i);
  });

  it("describes uncovered stages as 'N missing', not a negative number", async () => {
    vi.mocked(api.listMatchShooters).mockResolvedValue({
      match_root: "/m",
      match_name: "M",
      shooters: [
        {
          ...shooterFixture(),
          stages_total: 12,
          cameras: [
            {
              group_key: "Insta360|GO 3S|head",
              make: "Insta360",
              model: "GO 3S",
              mount: "head",
              role: "primary",
              video_count: 11,
              stage_numbers: [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
            },
          ],
        },
      ],
    });
    renderShooters({});
    expect(await screen.findByText("1 missing")).toBeInTheDocument();
    expect(screen.queryByText("-1 missing")).toBeNull();
  });
```

If `shooterFixture()` in that file lacks fields the `ShooterCameraInfo` type requires, match the shape of `api.listMatchShooters`'s response type in `lib/api.ts` (`ShooterSummary`); do not loosen the type.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd src/splitsmith/ui_static && corepack pnpm vitest run src/pages/Shooters.capabilities.test.tsx`
Expected: first new test fails with `Unable to find role="status"` (and "0 active" is present); second fails with `Unable to find an element with the text: 1 missing`.

- [ ] **Step 3: Add the loading state**

In `Shooters.tsx`, after `const active = data?.shooters ?? [];` (line 316) add:

```tsx
  const loading = data === null && error === null;
```

Replace the header paragraph (around line 327-331):

```tsx
          <p className="max-w-[40rem] text-sm text-muted">
            {loading ? (
              "Loading shooters..."
            ) : (
              <>
                <b className="font-bold text-ink">{active.length} active</b>{" "}
                &middot; manage cameras, role assignments, and per-stage
                coverage.
              </>
            )}
          </p>
```

In the Active shooters section (around line 377), wrap the list:

```tsx
        <div className="flex flex-col gap-3">
          {loading ? (
            <div
              role="status"
              className="rounded-[10px] border border-rule bg-surface px-4 py-6 text-center text-sm text-muted"
            >
              Loading shooters...
            </div>
          ) : (
            active.map((shooter) => (
              <ShooterCard
                key={shooter.slug}
                /* ...existing props unchanged... */
              />
            ))
          )}
        </div>
```

Keep every existing `ShooterCard` prop exactly as it is; only the wrapping conditional is new. Confirm the `error` state variable name by reading the top of the component (`const [error, setError] = useState<string | null>(null)`); if it is named differently, use that name in `loading`.

- [ ] **Step 4: Fix the missing-count copy**

In `CameraRow` (line ~767) change:

```tsx
            -{missing} missing
```
to
```tsx
            {missing} missing
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd src/splitsmith/ui_static && corepack pnpm vitest run src/pages/Shooters.capabilities.test.tsx`
Expected: all pass, including the three pre-existing tests.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/Shooters.tsx src/splitsmith/ui_static/src/pages/Shooters.capabilities.test.tsx
git commit -m "fix(shooters): show a loading state instead of '0 active', and '1 missing' not '-1 missing'

Before the shooter list resolved the page presented an empty answer:
'0 active' and no rows. On a cold hosted server that lasted long enough
to read as a bug. The camera row's uncovered-stage chip rendered a
literal minus sign in front of the count."
```

---

### Task 4: Account page: no empty amber box

**Files:**
- Modify: `src/splitsmith/ui_static/src/components/account/DesktopTokensSection.tsx:169-215`
- Test: `src/splitsmith/ui_static/src/components/account/DesktopTokensSection.test.tsx`

**Why:** The `aria-live` container is rendered unconditionally (correct, so screen readers announce a new token) but carries the amber border and fill unconditionally too, so the page shows an empty warning box. Move the styling to an inner element that exists only when a token was just created. Also `text-amber-600` is a light-theme colour on a dark ground; use the theme's `text-live`.

**Interfaces:**
- Produces: the `[aria-live="polite"]` element stays in the DOM always (existing tests at lines 95 and 143 rely on it) but has no border, background or padding classes; the reveal panel inside it has `data-testid="token-reveal"`.

- [ ] **Step 1: Write the failing test**

Add inside the existing `describe` in `DesktopTokensSection.test.tsx` (use the file's existing render helper and API mocks; read the file's first test to copy the setup that renders the section with an empty token list):

```tsx
  it("renders no bordered banner until a token has been created", async () => {
    renderSection(); // the file's existing helper that mounts with listDesktopTokens -> []
    const live = await screen.findByText(/desktop sync tokens/i);
    const region = live.closest("section, div")!.querySelector("[aria-live='polite']")!;
    expect(region).not.toBeNull();
    expect(region.className).not.toMatch(/border|bg-amber|p-3/);
    expect(screen.queryByTestId("token-reveal")).toBeNull();
  });
```

If the file's helper has a different name, use that name; do not add a second helper.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/splitsmith/ui_static && corepack pnpm vitest run src/components/account/DesktopTokensSection.test.tsx`
Expected: FAIL on `expect(region.className).not.toMatch(/border|bg-amber|p-3/)`.

- [ ] **Step 3: Move the styling inside the conditional**

Replace lines 172-215 (the `aria-live` block) with:

```tsx
        <div aria-live="polite">
          {justCreated ? (
            <div
              data-testid="token-reveal"
              className="space-y-2 rounded-md border border-live/40 bg-live/10 p-3"
            >
              <div className="flex items-start gap-2 text-xs text-live">
                <AlertTriangle
                  className="size-4 shrink-0"
                  aria-hidden="true"
                />
                <span>
                  Copy this token now - you will not see this again.
                  "{justCreated.record.name}" is otherwise identical to
                  every other token in the list below.
                </span>
              </div>
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  readOnly
                  value={justCreated.token}
                  aria-label="New desktop token"
                  className="min-w-0 flex-1 rounded border border-rule bg-bg px-2 py-1 font-mono text-xs"
                  onFocus={(e) => e.currentTarget.select()}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  aria-label={
                    copied
                      ? "Token copied to clipboard"
                      : "Copy token to clipboard"
                  }
                  onClick={() => void handleCopy(justCreated.token)}
                >
                  {copied ? "Copied" : "Copy"}
                </Button>
              </div>
            </div>
          ) : null}
        </div>
```

Keep the two explanatory comments above the block.

- [ ] **Step 4: Run the whole file to verify it passes**

Run: `cd src/splitsmith/ui_static && corepack pnpm vitest run src/components/account/DesktopTokensSection.test.tsx`
Expected: all pass (the two `closest("[aria-live]")` assertions still hold because the container is still there).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/components/account/DesktopTokensSection.tsx src/splitsmith/ui_static/src/components/account/DesktopTokensSection.test.tsx
git commit -m "fix(account): stop rendering an empty warning box above the token form

The aria-live container must always exist so a new token is announced,
but it carried the amber border and fill unconditionally, so the page
showed a bordered empty banner. The styling now lives on the reveal
panel that exists only after a token is created, and uses the theme's
live amber instead of a light-theme text-amber-600."
```

---

### Task 5: Hosted picker stops describing a filesystem

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Pick.tsx:436-450, 627-700, 860-875`
- Test: `src/splitsmith/ui_static/src/pages/Pick.test.tsx`

**Why:** On hosted, every match row shows the container path (`/home/splitsmith/data/users/.../projects/...`), the page offers "Open by path" with a `/Users/you/matches/...` placeholder, and "Import from backup" asks for a destination directory. `POST /api/me/projects/import` extracts under a filesystem `dest_root`, which a hosted user has no way to name. `useDeploymentMode()` is already imported in the page (`const { mode } = useDeploymentMode()` at line 66).

**Interfaces:**
- Consumes: `mode` from `useDeploymentMode()` (`"local" | "hosted"`).
- Produces: when `mode === "hosted"` the path line, the Open-by-path panel, the Import-from-backup panel and the header "Import Backup" button do not render. `resolved` is not consulted: the default `"local"` renders the local variant until features resolve, which matches the rest of the page.

- [ ] **Step 1: Write the failing test**

The existing `vi.mock` in `Pick.test.tsx` already resolves `getServerFeatures` to `{ mode: "hosted" }`. Add a test after the existing ones:

```tsx
  it("hides filesystem affordances on hosted: paths, open-by-path, backup import", async () => {
    vi.mocked(api.getRecentProjectsDetail).mockResolvedValueOnce([
      {
        path: "/home/splitsmith/data/users/01K/projects/stockholm-ipsc-open-2026",
        name: "Stockholm IPSC Open 2026",
        kind: "match",
        match_date: null,
        last_opened_at: "2026-08-01T00:00:00Z",
        shooters: [],
        stages_total: 12,
        stages_audited: 4,
        status: "in_progress",
      } as never,
    ]);
    renderPick();
    await screen.findByText("Stockholm IPSC Open 2026");
    await waitFor(() => expect(api.getServerFeatures).toHaveBeenCalled());
    expect(screen.queryByText(/\/home\/splitsmith\/data/)).toBeNull();
    expect(screen.queryByText(/open by path/i)).toBeNull();
    expect(screen.queryByText(/import from backup/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /import backup/i })).toBeNull();
  });
```

Read `RecentProjectDetail` (or whatever `getRecentProjectsDetail` returns) in `lib/api.ts` and give the fixture the real required fields instead of `as never` if the cast is not needed. Import `waitFor` from `@testing-library/react` if the file does not already.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/splitsmith/ui_static && corepack pnpm vitest run src/pages/Pick.test.tsx`
Expected: FAIL at `expect(screen.queryByText(/\/home\/splitsmith\/data/)).toBeNull()` (the path renders).

- [ ] **Step 3: Gate the four affordances**

In `Pick.tsx`:

1. Header button (line ~438): wrap the `Import Backup` `<Button>` in `{mode !== "hosted" && ( ... )}`.
2. Accordions (line ~627): wrap the whole `<div className="mt-10 grid gap-4 lg:grid-cols-2">...</div>` in `{mode !== "hosted" && ( ... )}`.
3. Path line in the row component (line ~871): the row is rendered by a child component (find the component that receives `project` and renders `{project.path}`; it is in the same file). Add a prop `showPath: boolean` to that component, pass `showPath={mode !== "hosted"}` at both call sites (lines ~570 and ~607), and render the path `<span>` only when `showPath`.

- [ ] **Step 4: Run the file to verify it passes, then typecheck**

Run: `cd src/splitsmith/ui_static && corepack pnpm vitest run src/pages/Pick.test.tsx && corepack pnpm typecheck`
Expected: all pass, no type errors.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/Pick.tsx src/splitsmith/ui_static/src/pages/Pick.test.tsx
git commit -m "fix(pick): hide filesystem affordances on hosted

Container paths under every match, an 'Open by path' panel with a
/Users placeholder, and a backup import asking for a destination
directory all rendered on my.splitsmith.app. The import endpoint
extracts under a filesystem dest_root a hosted user cannot name."
```

---

### Task 6: Export explains a missing source in the user's own terms

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Export.tsx:645-670, 1283-1320`
- Test: `src/splitsmith/ui_static/src/pages/Export.cleanup.test.tsx` (add a `describe` block; the file already mocks `getProject`, `getExportOverview`, `getCleanupPlan`)

**Why:** Three strings tell a hosted user to "mount the source drive" or "reconnect the drive". On hosted a missing source means the original upload is no longer in object storage (storage-aware cleanup can remove it); the fix is to re-upload from Videos.

**Interfaces:**
- Consumes: `deploymentMode` (already `const { mode: deploymentMode } = useDeploymentMode()` at line 143).
- Produces: `stageChipTitle(stage, eligible, sourceMissing, trimsOnly, hosted: boolean)` and `stageSectionHelp(eligibleCount, sourceMissingCount, readyCount, totalCount, trimsOnly, hosted: boolean)`; a `sourceOfflineCopy(hosted: boolean): { title: string; body: (n: number) => string }` helper used by the banner.

- [ ] **Step 1: Write the failing test**

Add to `Export.cleanup.test.tsx`, after the existing `describe`:

```tsx
describe("Export source-missing copy", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  function readyButOffline(): ExportOverview {
    return {
      match_exports: [],
      stages: [
        {
          stage_number: 3,
          stage_name: "B6 Rear",
          skipped: false,
          has_primary: true,
          primary_processed: { beep: true, shot_detect: true, trim: true },
          audit_shot_count: 30,
          total_candidate_count: 119,
          audit_path: "audit.json",
          trimmed_video_path: null,
          lossless_trim_present: false,
          csv_path: null,
          fcpxml_path: null,
          report_path: null,
          overlay_path: null,
          has_exports: false,
          last_export_at: null,
          ready_to_export: true,
          ready_to_trim: true,
          source_reachable: false,
          secondaries: [],
        },
      ],
    };
  }

  it("on hosted, says the upload is gone and points at Videos, never at a drive", async () => {
    vi.mocked(api.getServerFeatures).mockResolvedValue({ lab: false, mode: "hosted" });
    vi.mocked(api.getProject).mockResolvedValue(makeProject());
    vi.mocked(api.getExportOverview).mockResolvedValue(readyButOffline());
    vi.mocked(api.getCleanupPlan).mockResolvedValue(makePlan());
    render(
      <MemoryRouter initialEntries={["/export/anna"]}>
        <Routes>
          <Route path="/export/:slug" element={<Export />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText(/original upload is no longer stored/i)).toBeInTheDocument();
    expect(screen.queryByText(/source drive/i)).toBeNull();
    expect(screen.queryByText(/reconnect the drive/i)).toBeNull();
  });
});
```

Add `getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "local" })` to the file's `vi.mock("@/lib/api", ...)` `api` object so the mock exists (the other tests keep the local default). Note `lib/features.ts` caches the first `getServerFeatures` result module-wide; put this `describe` in its own file if the cache makes the hosted mock ineffective after the local tests ran -- name it `Export.hostedCopy.test.tsx` and copy `makeProject`/`makePlan` there (they are small).

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/splitsmith/ui_static && corepack pnpm vitest run src/pages/Export`
Expected: FAIL with `Unable to find an element with the text: /original upload is no longer stored/i`.

- [ ] **Step 3: Thread `hosted` through the three copy sites**

In `Export.tsx`:

1. Add a helper next to `stageChipTitle`:

```tsx
/** Copy for a stage whose source file is unreachable. Desktop: the drive
 *  is unplugged. Hosted: the raw upload left object storage (cleanup), so
 *  the only fix is re-uploading from Videos. */
function sourceOfflineCopy(hosted: boolean): { title: string; body: (n: number) => string } {
  if (hosted) {
    return {
      title: "Upload missing",
      body: (n) =>
        `${n} otherwise-ready ${n === 1 ? "stage" : "stages"} can't export -- the original upload is no longer stored. Re-upload the stage's video from Videos.`,
    };
  }
  return {
    title: "Source offline",
    body: (n) =>
      `${n} otherwise-ready ${n === 1 ? "stage" : "stages"} can't export -- the original video files aren't reachable. Mount the source drive (or use Relink) and reload the page.`,
  };
}
```

2. Change `stageChipTitle`'s signature to `(stage, eligible, sourceMissing, trimsOnly, hosted: boolean)` and its source line to:

```tsx
  if (sourceMissing)
    return hosted
      ? "Original upload is no longer stored -- re-upload from Videos."
      : "Source video offline -- reconnect the drive and reload.";
```

3. Change `stageSectionHelp`'s signature to add a trailing `hosted: boolean` and its two source-missing returns to:

```tsx
    return hosted
      ? "Ready, but every original upload is no longer stored. Re-upload from Videos."
      : "Ready, but every source video is offline. Mount the source drive and reload.";
```
and
```tsx
    : hosted
      ? "No stage is trimmable. Re-upload the missing videos."
      : "No stage is trimmable. Reconnect the missing sources.";
```

4. At the call sites pass `deploymentMode === "hosted"` as the new last argument (lines ~645 and ~684), and replace the banner's static title and body (lines ~661-668) with `sourceOfflineCopy(deploymentMode === "hosted")`:

```tsx
                  <div className="font-display text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-live">
                    {sourceOfflineCopy(deploymentMode === "hosted").title}
                  </div>
                  <div className="mt-0.5 text-muted">
                    {sourceOfflineCopy(deploymentMode === "hosted").body(sourceMissingNumbers.length)}
                  </div>
```

- [ ] **Step 4: Run the Export tests and typecheck**

Run: `cd src/splitsmith/ui_static && corepack pnpm vitest run src/pages/Export && corepack pnpm typecheck`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/Export.tsx src/splitsmith/ui_static/src/pages/Export.cleanup.test.tsx
# plus src/pages/Export.hostedCopy.test.tsx if it was split out
git commit -m "fix(export): describe a missing source in hosted terms

On my.splitsmith.app a stage whose raw upload has left storage told the
user to mount a drive. It now says the upload is no longer stored and
points at Videos to re-upload."
```

---

### Task 7: Queue confirm chains shot detection

**Files:**
- Modify: `src/splitsmith/ui/server.py:14421-14460` (`confirm_beep_in_queue`) and `:10860-10915` (`set_beep_reviewed`)
- Test: `tests/test_ui_server.py` (next to `test_beep_queue_confirm_secondary`, line ~9614)

**Why:** `PATCH .../videos/{id}/beep-reviewed` submits `shot_detect` when the primary's trim is cached (server.py:10902). `POST /api/match/beep-queue/confirm` only sets `beep_reviewed` and writes the stub audit doc never, so a low-confidence primary confirmed from the Beep review page stays trimmed-without-shots until someone presses Run in Audit's gate. Both endpoints must do the same thing after flipping the flag.

**Interfaces:**
- Produces: a module-level helper inside `create_app`'s closure:
  `async def _after_beep_reviewed(slug: str, stage_number: int, video: StageVideo) -> None` -- writes the stub audit doc if none exists and submits `shot_detect` for a primary with a cached trim and no active job. Called by both endpoints.

- [ ] **Step 1: Write the failing test**

Add after `test_beep_queue_confirm_secondary` in `tests/test_ui_server.py`:

```python
def test_beep_queue_confirm_primary_chains_shot_detect(tmp_path: Path) -> None:
    """Confirming a low-confidence primary through the queue must submit
    shot detection when its trim is already cached -- the same chain the
    per-video beep-reviewed endpoint fires. Before this fix the queue
    only flipped the flag, leaving a trimmed stage with no shots."""
    root = tmp_path / "match"
    project = _build_project_with_primary(
        root,
        "Confirm Primary",
        beep_time=22.5,
        beep_source="auto",
        beep_confidence=0.4,
        beep_reviewed=False,
    )
    primary = project.stages[0].videos[0]
    primary.processed["trim"] = True
    project.save(root / "shooters" / "me")
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)
    state = app.state.splitsmith_state

    release = threading.Event()
    submitted: list[dict] = []

    def _blocked(_handle, **args) -> None:
        submitted.append(args)
        release.wait(timeout=10.0)

    state.jobs.bodies.register("shot_detect", _blocked)
    try:
        resp = client.post(
            "/api/match/beep-queue/confirm",
            json={"slug": "me", "stage_number": 1, "video_id": primary.video_id},
        )
        assert resp.status_code == 200, resp.text
        jobs = client.get("/api/me/jobs").json()
        kinds = [(j["kind"], j["shooter_slug"], j["stage_number"]) for j in jobs]
        assert ("shot_detect", "me", 1) in kinds, kinds
    finally:
        release.set()
    for j in client.get("/api/me/jobs").json():
        if j["kind"] == "shot_detect":
            _wait_for_job(client, j["id"])
```

Check the jobs-list route in this test module: memory says `/api/me/jobs` returns a bare list and `_MatchClient` does not rewrite it. If `_wait_for_job` needs a different shape, mirror `test_shot_detect_does_not_adopt_other_shooters_job`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ui_server.py::test_beep_queue_confirm_primary_chains_shot_detect -n0 -v`
Expected: FAIL at `assert ("shot_detect", "me", 1) in kinds` with `kinds == []`.

- [ ] **Step 3: Extract the post-review chain and call it from both endpoints**

In `server.py`, directly above `set_beep_reviewed` (line ~10860), add:

```python
    async def _after_beep_reviewed(slug: str, stage_number: int, video: StageVideo) -> None:
        """Everything that must follow ``beep_reviewed`` flipping to True.

        Shared by the per-video endpoint and the cross-shooter queue so a
        beep confirmed from either place leaves the same state behind:
        a stub audit document (status surfaces read a document, never
        infer from absence) and, for a primary whose trim is cached, a
        queued ``shot_detect``. No-op for secondaries and for primaries
        whose trim has not run yet -- the trim chain fires detection
        itself once the gate is open.
        """
        existing_doc, audit_version = state.load_audit(slug, stage_number)
        if existing_doc is None:
            state.save_audit(
                slug,
                stage_number,
                {"shots": [], "detection": STUB_AUDIT_DETECTION},
                version=audit_version,
            )
        if (
            video.role == "primary"
            and video.processed.get("trim")
            and await state.jobs.find_active(kind="shot_detect", stage_number=stage_number, shooter_slug=slug)
            is None
        ):
            await state.jobs.submit(
                kind="shot_detect",
                stage_number=stage_number,
                shooter_slug=slug,
                args={"slug": slug, "stage_number": stage_number},
            )
```

In `set_beep_reviewed`, replace the stub-doc block and the shot-detect block (everything between `project.save(...)` and `return JSONResponse(...)`) with:

```python
        if req.reviewed:
            await _after_beep_reviewed(slug, stage_number, video)
```

Keep the explanatory comments above the call by moving them into the helper's docstring (done above); delete them from the endpoint.

In `confirm_beep_in_queue`, make the handler `async def`, and after `proj.save(shooter_root)` add:

```python
        await _after_beep_reviewed(req.slug, req.stage_number, target_video)
```

`state.load_audit` / `state.save_audit` are keyed on the shooter slug, which for the queue is `req.slug` -- the same value `set_beep_reviewed` receives as `slug`. Verify `get_beep_queue()` is safe to call from an async handler (it is sync and does file I/O only; keep the `return get_beep_queue()`).

- [ ] **Step 4: Run the new test and the neighbouring beep tests**

Run: `uv run pytest tests/test_ui_server.py -k "beep_queue or beep_reviewed or set_beep" -n0 -v`
Expected: all pass, including `test_beep_queue_confirm_secondary` (a secondary still only flips the flag) and the existing `beep_reviewed` tests.

- [ ] **Step 5: Format, lint, and restore the lockfile**

```bash
uv run black src/splitsmith/ui/server.py tests/test_ui_server.py
uv run ruff check src/splitsmith/ui/server.py tests/test_ui_server.py
git checkout uv.lock
```

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_ui_server.py
git commit -m "fix(server): queue beep confirm chains shot detection like the per-video endpoint

POST /api/match/beep-queue/confirm only flipped beep_reviewed. A
low-confidence primary confirmed from the Beep review page was left
trimmed with no shots until someone opened Audit and pressed Run. Both
endpoints now share _after_beep_reviewed: stub audit doc, then
shot_detect when the trim is cached."
```

---

### Task 8: Whole-branch verification and PR

**Files:** none new.

- [ ] **Step 1: Run the SPA gates**

```bash
cd src/splitsmith/ui_static
corepack pnpm typecheck && corepack pnpm lint && corepack pnpm test && corepack pnpm build
```
Expected: all green. If `lint` reports fixable issues, fix them by hand (memory: `ruff --fix`-style auto-fixes have broken green CI here before); rerun.

- [ ] **Step 2: Run the Python suite**

```bash
cd /home/mathias/work/splitsmith
uv run pytest -q
git checkout uv.lock
```
Expected: green. A local-only failure under load is the box, not the change (memory `gaspode-load-and-flaky-tests`); rerun that test with `-n0` before concluding.

- [ ] **Step 3: Prove each test fails against the pre-fix code**

For each of Tasks 2-7, stash the implementation file only and rerun the task's test:

```bash
git stash push src/splitsmith/ui_static/src/components/results/StageStats.tsx && (cd src/splitsmith/ui_static && corepack pnpm vitest run src/components/results/StageStats.test.tsx); git stash pop
```
Repeat for `Shooters.tsx`, `DesktopTokensSection.tsx`, `Pick.tsx`, `Export.tsx`, and `src/splitsmith/ui/server.py` (with `uv run pytest tests/test_ui_server.py::test_beep_queue_confirm_primary_chains_shot_detect -n0`). Each run must show the task's stated failure. Record the six results in the PR body.

- [ ] **Step 4: Clean the tree and push**

```bash
rm -rf .playwright-mcp
git status --short   # must list nothing unexpected; never git add -A
git push -u origin ux/pr1-defects
```

- [ ] **Step 5: Open the PR**

```bash
gh pr create --title "fix(ui): six defects found in the UX review (stats clip, shooters loading, account banner, hosted leaks, queue confirm chain)" --body-file - <<'EOF'
Fixes the six shipped defects recorded in
docs/superpowers/specs/2026-09-13-ux-restructure-and-visual-budget-design.md section 7,
ahead of the UX restructure.

- results: stage stats strip crushed to 29 px in the scroll column; draw / fastest / avg split invisible at desktop widths. Measured before/after: <fill in from Task 2 step 5>.
- shooters: loading rendered as "0 active" with an empty list; "-1 missing" copy.
- account: empty amber banner above the token form.
- pick: container paths, Open-by-path and backup import on hosted.
- export: "mount the source drive" copy on hosted.
- server: beep-queue confirm never submitted shot_detect; now shares _after_beep_reviewed with the per-video endpoint.

Each test was run against the pre-fix code and failed as stated (Task 8 step 3): <paste the six one-line results>.

Squash-merge; single-paragraph squash body (release-please parser).

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01Est8bRLwpxCKqPXsopFKQz
EOF
```

---

## Self-review

- Spec coverage: section 7 items 1-6 map to Tasks 2, 3, 4, 5+6, 2 (mobile orphan), 7. Item 4 (hosted-as-desktop) is split across Pick (Task 5) and Export (Task 6). Nothing in section 7 is unassigned.
- Placeholders: the PR body carries two `<fill in>` markers that Task 8 explicitly instructs the executor to replace with measured results; they are inputs, not gaps.
- Type consistency: `stageChipTitle` and `stageSectionHelp` gain a trailing `hosted: boolean` in Task 6 and both call sites pass `deploymentMode === "hosted"`; `_after_beep_reviewed(slug, stage_number, video)` is defined and called with the same argument order in both endpoints in Task 7; `StageStats` props are unchanged in Task 2.
