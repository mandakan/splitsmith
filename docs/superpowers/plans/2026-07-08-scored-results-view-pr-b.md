# PR B -- Scored results view Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show splitsmith's splits alongside the SSI Scoreboard scorecard in the shareable results view -- splits featured, scorecard as context -- plus match totals and an explicit refresh, reading the scorecard persisted by PR A.

**Architecture:** No new persistence -- PR A already stores `StageEntry.scorecard`, and splits already arrive via the Coach endpoint (`CoachShot.split`). This PR is read/format + UI: a shared scorecard display component, scorecard on the results overview and per-stage view, match totals, and a "Refresh from scoreboard" button wired to the existing `refreshScoreboardTimes` API. Works unchanged for anonymous share viewers because `scopeRequestPath` rewrites `/api/...` to `/api/share/{token}/...`.

**Tech Stack:** SPA only -- React 19, react-router-dom 7, Tailwind 4, lucide-react, no react-query (manual `useEffect` + `api.*` + `useState`), pnpm-only, no test runner (verify via typecheck + build + scoped eslint).

## Global Constraints

- **DEPENDS ON PR A** being merged: `StageEntry.scorecard` (backend + TS type), `LinkProposal`, and the persisted scores must exist. Do not start until PR A is on the branch base.
- ASCII punctuation only in all new code/comments/copy: `--` not em dash, `...` not ellipsis, straight quotes.
- Accessibility is first-class: color is never the sole carrier of state (pair color with text/shape); rem sizing; focus rings; target WCAG 2.2 AA; respect `prefers-reduced-motion`.
- Design aesthetic = instrument panel: dark surfaces, LED red accent, Antonio/Geist/JetBrains Mono. Reuse existing tokens; verify `var(--token)` names against `styles/index.css` before use (bare unknown `var()` silently falls back).
- Results/ResultsStage are the MOBILE viewer and are NOT wrapped in `DesktopGate`. Keep them mobile-first.
- Split *values* come from the backend `CoachShot.split`; the SPA only classifies/formats via `lib/splits.ts`. Do not recompute splits from raw audit times.
- Verify via `pnpm typecheck` + `pnpm build` + eslint scoped to changed files (whole-repo `eslint .` is red from pre-existing files).
- Model tiers per task are advisory for subagent-driven execution.

## Key existing shapes (from the SPA map)

- `Results.tsx` reads `{project, shooters, refresh}` from `useOutletContext<MatchShellOutletContext>()`; overview uses only `stage.time_seconds` via `buildStageMatrix(project.stages, shooters)` (`lib/stageMatrix.ts`). Multi-shooter fetches each shooter's project (`api.getProject(slug)`).
- `ResultsStage.tsx` reads `api.getStageCoach(slug, stage) -> CoachStageResponse`; already derives `stageTime`, `splits`, `fastestSplit`, `avgSplit` and renders `<StageStats>` + `<SplitsList>` (`components/results/SplitsList.tsx`). It does NOT currently have the scorecard (that is on the project's `StageEntry`, not the Coach response).
- `StageEntry` (TS, extended by PR A): `{ ..., time_seconds, scorecard: StageScorecard | null }`.
- `api.refreshScoreboardTimes(slug) => request<MatchProject & {stage_times_merged:number}>(...)` already exists.
- Inline error banner pattern: `error: string|null` -> `border-led/40 bg-led/10 text-led`. Retry pattern: bump an `attempt` counter used as a `useEffect` dep.

---

### Task 1: Shared Scorecard display component [model: sonnet]

**Files:**
- Create: `src/splitsmith/ui_static/src/components/results/Scorecard.tsx`

**Interfaces:**
- Produces: `export function Scorecard({ scorecard, className }: { scorecard: StageScorecard | null; className?: string })` -- renders hit factor, stage %, points, and the hit breakdown (A/C/D, M, NS, procedurals, DQ flag) in a compact instrument-panel grid. Renders nothing (returns `null`) when `scorecard` is null. Also export `export function matchTotals(stages: {scorecard: StageScorecard | null; time_seconds: number}[]): {points:number; time:number; hitFactor:number|null; alphas:number; charlies:number; deltas:number; misses:number}` -- a pure helper summing available scorecards (ignoring null ones), for the overview totals.
- Consumes: `StageScorecard` type from `lib/api.ts` (PR A).

- [ ] **Step 1: Implement the component + pure helper**

Layout: labels use the muted token, numbers use `font-mono tabular-nums`. Each hit type shows a text label next to its count (color is not the sole carrier). Guard every field with `?? "--"`. `matchTotals` sums only non-null scorecards; `hitFactor` = totalPoints/totalTime when time>0 else null.

- [ ] **Step 2: Verify**

Run (from `src/splitsmith/ui_static/`): `pnpm typecheck && pnpm exec eslint src/components/results/Scorecard.tsx`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add src/splitsmith/ui_static/src/components/results/Scorecard.tsx
git commit -m "feat(ui): shared scorecard display component + match-totals helper"
```

---

### Task 2: Scorecard on the per-stage results view [model: sonnet]

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/ResultsStage.tsx` (data load `:51-68`, derived stats `:101-109`)

**Interfaces:**
- Consumes: `api.getProject(slug)` (or the outlet `project` if the stage view already has it) to read `StageEntry.scorecard` for this `stage`; the existing `coach` splits stay the featured content.
- Produces: the per-stage view renders `<Scorecard scorecard={stageEntry?.scorecard ?? null} />` beneath the splits/stats, with a `scorecard_updated_at` timestamp line. Splits remain visually primary.

- [ ] **Step 1: Load the stage's scorecard**

ResultsStage currently only fetches the Coach response. Add a second source for `StageEntry.scorecard`: either read `project` from `useOutletContext` and `project.stages.find(s => s.stage_number === stage)`, or `api.getProject(slug)` in the existing `useEffect` (guarded by `alive`). Prefer the outlet `project` if present to avoid an extra fetch; fall back to `getProject` when the stage view is reached without the shell context.

- [ ] **Step 2: Render scorecard under the splits**

Place `<Scorecard>` after `<SplitsList>`, with a small "from scoreboard, updated <ts>" caption using `scorecard_updated_at`. If `scorecard` is null, render nothing (graceful degradation to splits-only).

- [ ] **Step 3: Verify**

Run: `pnpm typecheck && pnpm build && pnpm exec eslint src/pages/ResultsStage.tsx`
Expected: clean; 0 eslint errors on changed files.

- [ ] **Step 4: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/ResultsStage.tsx
git commit -m "feat(ui): show scoreboard scorecard beneath splits on the stage view"
```

---

### Task 3: Scorecard columns + match totals on the overview [model: sonnet]

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Results.tsx` (mobile card list `:240-321`, desktop matrix `:324-412`)
- Possibly modify: `src/splitsmith/ui_static/src/lib/stageMatrix.ts` if per-stage scorecard needs threading through the matrix builder

**Interfaces:**
- Consumes: `project.stages[].scorecard` (single-shooter) and the per-shooter projects already fetched for multi-shooter (`api.getProject(slug)`, `:124-141`) -- extend the pivot to carry `scorecard` alongside `time_seconds`; `matchTotals` from Task 1.
- Produces: each stage row/cell shows time plus hit factor (and stage % where space allows); a match-total row/summary shows total time, total points, overall hit factor, and aggregate A/C/D from `matchTotals`. Unscored stages show time-only (current behavior) -- no empty scorecard chrome.

- [ ] **Step 1: Thread scorecard through the stage matrix**

Extend the multi-shooter pivot (`slug -> stage_number -> {time_seconds, scorecard}`) and, if used, `buildStageMatrix` so each cell can access its `scorecard`. Keep `time_seconds` as the primary cell value.

- [ ] **Step 2: Render HF/points per stage + a totals summary**

Mobile cards: add a compact HF line under the time. Desktop matrix: add a hit-factor sub-value per cell and a totals row using `matchTotals(stages)`. Degrade to time-only when `scorecard` is null across the board.

- [ ] **Step 3: Verify**

Run: `pnpm typecheck && pnpm build && pnpm exec eslint src/pages/Results.tsx src/lib/stageMatrix.ts`
Expected: clean; 0 eslint errors on changed files.

- [ ] **Step 4: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/Results.tsx src/splitsmith/ui_static/src/lib/stageMatrix.ts
git commit -m "feat(ui): scorecard metrics and match totals on the results overview"
```

---

### Task 4: Refresh-from-scoreboard button [model: haiku]

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Results.tsx` (share/actions area near the share button `:194-206`)

**Interfaces:**
- Consumes: `api.refreshScoreboardTimes(slug)` (exists) for each linked shooter, then `refresh()` from outlet context; owner-only.
- Produces: a "Refresh from scoreboard" button shown only when `deploymentMode !== "share"` (owner) AND the match is scoreboard-linked (`project.scoreboard_match_id`). Clicking refreshes each linked shooter's stage times/scorecard, then calls `refresh()`. Disabled + spinner while running; errors via the inline banner. Share viewers never see it (they cannot fetch upstream).

- [ ] **Step 1: Add the button + handler**

```tsx
const [refreshing, setRefreshing] = useState(false);
async function refreshFromScoreboard() {
  setRefreshing(true); setError(null);
  try {
    for (const s of shooters) {
      if (s.selected_competitor_id != null) await api.refreshScoreboardTimes(s.slug);
    }
    refresh();
  } catch (e) {
    setError(e instanceof ApiError ? e.detail : String(e));
  } finally {
    setRefreshing(false);
  }
}
```

Gate rendering on owner + `project?.scoreboard_match_id`.

- [ ] **Step 2: Verify**

Run: `pnpm typecheck && pnpm build && pnpm exec eslint src/pages/Results.tsx`
Expected: clean; 0 eslint errors on changed files.

- [ ] **Step 3: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/Results.tsx
git commit -m "feat(ui): owner-only refresh-from-scoreboard on the results view"
```

---

### Task 5: Visual verification pass [model: sonnet]

**Files:** none (verification task)

- [ ] **Step 1: Build and screenshot** the results overview and a stage view for a scored, linked match (per memory `reference_ui_verification`: bounded headless screenshot with `domcontentloaded`; route is `/match/:matchId`). Confirm: splits remain visually primary; scorecard renders; unscored stage degrades to time-only; totals compute; refresh button owner-only.

- [ ] **Step 2: Share-view check** -- load the `/share/:token` results path and confirm scores + splits render for an anonymous viewer (no scoreboard identity) from persisted state, and the refresh button is absent.

- [ ] **Step 3: Accessibility spot-check** -- hit-type counts are labeled with text (not color-only); focus rings present; numbers use `tabular-nums`.

- [ ] **Step 4:** If all good, this PR is ready. Note any visual gaps for a follow-up rather than silently shipping.

---

## Self-Review

- **Spec coverage (Phase B):** splits featured + scorecard as context per stage (Task 2, covered); scorecard + match totals on overview (Tasks 1, 3, covered); refresh button + `scorecard_updated_at` shown, no polling (Tasks 2, 4, covered); graceful degradation to splits-only / time-only (Tasks 2, 3, covered); share viewers see persisted scores without fetching (Task 5 share check, relies on PR A persistence, covered).
- **Placeholder scan:** no TBDs; every task has concrete files, the reused symbols are named, and verification commands are exact. Layout specifics (grid arrangement) are intentionally left to the implementer within the stated aesthetic constraints -- these are UI polish, not missing logic.
- **Type consistency:** `StageScorecard` / `scorecard` names match PR A's TS types (Task 5 of PR A); `matchTotals` signature is consumed identically in Tasks 1 and 3; `refreshScoreboardTimes` matches the existing API function.
- **Dependency note:** every task depends on PR A's persisted `StageEntry.scorecard`; Task 5's share check also validates PR A's hosted-store round-trip end to end.
```
