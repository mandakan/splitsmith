# Aggregate Match Overview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the match Overview (`/` route) a true aggregate dashboard across all shooters, instead of a per-shooter view silently scoped to the wrong (alphabetically-first, often footage-less) shooter.

**Architecture:** The backend already loops each shooter's stages to compute `stages_audited`; expose the per-stage `StageStatus` it already has via a new `stage_statuses` field on the `/api/match/shooters` payload the Overview already fetches. The frontend pivots that into a per-stage x per-shooter matrix (pure functions in a new `lib/stageMatrix.ts`) and renders a match-progress hero + per-shooter status chips per stage tile. A one-line `MatchShell` fix aligns the base project load with the existing footage-bearing default-shooter rule.

**Tech Stack:** Python 3.11 + FastAPI + Pydantic (backend), React + TypeScript + Vite + Tailwind (SPA under `src/splitsmith/ui_static`), pytest.

## Global Constraints

- Python 3.11+, type hints everywhere; Pydantic models for data crossing module boundaries; Black line length 100; Ruff clean.
- Run CI gates locally before declaring done: `ruff check`, `black --check`, `pytest` for backend changes.
- SPA is **pnpm-only** (never introduce npm/package-lock.json). Verify SPA via `pnpm -C src/splitsmith/ui_static typecheck` and `pnpm -C src/splitsmith/ui_static build`.
- `ui_static` has **no test runner**; whole-repo `eslint .` is red from pre-existing files, so lint only the files you touch (`pnpm -C src/splitsmith/ui_static exec eslint <path>`).
- Backend `StageStatus` string values (must match the TS union exactly): `todo`, `partial`, `ready`, `in_progress`, `audited`, `skipped`.
- Match cap is small (<= ~4 shooters); no pagination/perf work needed.

---

### Task 1: Backend -- expose per-stage status on the shooters payload

**Files:**
- Modify: `src/splitsmith/ui/server.py` (add `StageStatusEntry` model near `ShooterListEntry` ~line 2637; add field to `ShooterListEntry`; populate in `_classify_shooter` ~line 8975)
- Test: `tests/test_ui_server.py` (new test after `test_list_match_shooters_returns_active_and_others`, ~line 6429)

**Interfaces:**
- Consumes: `MatchProject.stage_statuses(root) -> dict[int, StageStatus]` (already exists in `src/splitsmith/ui/project.py:980`), `StageStatus` enum (`project.py:397`).
- Produces: `ShooterListEntry.stage_statuses: list[StageStatusEntry]` where `StageStatusEntry = {stage_number: int, status: str}` on the `GET /api/match/shooters` response.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ui_server.py` (mirror the setup style of `test_list_match_shooters_returns_active_and_others`, but give one shooter an audited stage). Note: a stage is `audited` only when its `audit/stage{N}.json` contains a `save` event; with a primary video + `time_seconds > 0` but no audit JSON the status is `ready`; with no primary it is `todo`.

```python
def test_list_match_shooters_includes_per_stage_statuses(
    tmp_path: Path, _user_config_home: Path
) -> None:
    """The shooters payload carries a per-stage status list so the match
    Overview can render an aggregate grid without fetching each project."""
    import json

    from splitsmith import match_model

    target = tmp_path / "mm"
    match = match_model.Match.init(target, name="Multi-shooter")
    match.stages = [
        match_model.MatchStageDefinition(stage_number=1, stage_name="One"),
        match_model.MatchStageDefinition(stage_number=2, stage_name="Two"),
    ]
    match.save(target)

    # Shooter "ma": stage 1 has a primary video + time but no audit JSON
    # (-> ready); stage 2 has nothing (-> todo).
    sroot = match_model.Match.shooter_root(target, "ma")
    match.add_shooter(target, match_model.Shooter(slug="ma", name="Mathias"))
    legacy = MatchProject.init(sroot, name="Multi-shooter")
    legacy.competitor_name = "Mathias"
    legacy.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=12.0,
            videos=[StageVideo(path="s1.mp4", role="primary")],
        ),
        StageEntry(stage_number=2, stage_name="Two", time_seconds=0.0),
    ]
    legacy.save(sroot)

    app = create_app()
    client = _MatchClient(app)
    resp = client.post(
        "/api/me/recent-projects/bind",
        json={"path": str(target.resolve())},
    )
    assert resp.status_code == 200
    match_id = app.state.splitsmith_state.matches.known_ids()[0]

    listing = client.get(f"/api/matches/{match_id}/match/shooters")
    assert listing.status_code == 200, listing.text
    ma = next(s for s in listing.json()["shooters"] if s["slug"] == "ma")
    by_stage = {e["stage_number"]: e["status"] for e in ma["stage_statuses"]}
    assert by_stage == {1: "ready", 2: "todo"}
```

Confirm `StageVideo` is already imported in `tests/test_ui_server.py`; if not, add it to the existing `from splitsmith.ui.project import ...` line.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ui_server.py::test_list_match_shooters_includes_per_stage_statuses -v`
Expected: FAIL with `KeyError: 'stage_statuses'` (field does not exist yet).

- [ ] **Step 3: Add the Pydantic model + field**

In `src/splitsmith/ui/server.py`, immediately before `class ShooterListEntry(BaseModel):` (~line 2637):

```python
class StageStatusEntry(BaseModel):
    """One stage's lifecycle status for a single shooter, surfaced on the
    shooters listing so the match Overview can render an aggregate grid
    without fetching every shooter's full project."""

    stage_number: int
    status: StageStatus
```

Then add the field to `ShooterListEntry` (after `stages_missing_trim`):

```python
    # Per-stage status for this shooter, one entry per stage in the
    # shooter's own project (same source as ``stages_audited``). The match
    # Overview pivots these across shooters into a per-stage grid.
    stage_statuses: list[StageStatusEntry] = Field(default_factory=list)
```

Confirm `StageStatus` is imported in `server.py` (it is used elsewhere); if the symbol is not already imported, add it to the existing `from splitsmith.ui.project import ...` block. Confirm `Field` is imported from `pydantic` (it is used elsewhere in this file).

- [ ] **Step 4: Populate it in `_classify_shooter`**

In `src/splitsmith/ui/server.py`, inside `_classify_shooter`, reuse the already-loaded `legacy` project. Just before the `return ShooterListEntry(...)` (~line 8975):

```python
        stage_status_map = legacy.stage_statuses(shooter_root)
```

Then add to the `ShooterListEntry(...)` constructor call:

```python
            stage_statuses=[
                StageStatusEntry(stage_number=n, status=st)
                for n, st in sorted(stage_status_map.items())
            ],
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_ui_server.py::test_list_match_shooters_includes_per_stage_statuses -v`
Expected: PASS.

- [ ] **Step 6: Run the surrounding suite + lint/format**

Run: `uv run pytest tests/test_ui_server.py -k "match_shooters" -q && uv run ruff check src/splitsmith/ui/server.py && uv run black --check src/splitsmith/ui/server.py`
Expected: all pass, no lint/format diffs.

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_ui_server.py
git commit -m "feat(overview): expose per-stage status on shooters payload"
```

---

### Task 2: Frontend -- mirror the `stage_statuses` type

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (add `StageStatusEntry` interface; add field to `ShooterListEntry` ~line 1279-1292)

**Interfaces:**
- Consumes: backend field from Task 1.
- Produces: `ShooterListEntry.stage_statuses: StageStatusEntry[]` for `lib/stageMatrix.ts` (Task 4) and `Home.tsx` (Tasks 5-6).

- [ ] **Step 1: Add the interface + field**

In `src/splitsmith/ui_static/src/lib/api.ts`, immediately before `export interface ShooterListEntry {` (~line 1279):

```typescript
/** One stage's lifecycle status for a single shooter, mirrored from the
 *  backend ``StageStatusEntry``. The match Overview pivots these across
 *  shooters into an aggregate per-stage grid. */
export interface StageStatusEntry {
  stage_number: number;
  status: StageStatus;
}
```

Add the field inside `ShooterListEntry` (after `stages_missing_trim: number;`):

```typescript
  /** Per-stage status for this shooter (one entry per stage in the
   *  shooter's own project). Drives the aggregate Overview grid. */
  stage_statuses: StageStatusEntry[];
```

- [ ] **Step 2: Typecheck**

Run: `pnpm -C src/splitsmith/ui_static typecheck`
Expected: PASS (no type errors).

- [ ] **Step 3: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/api.ts
git commit -m "feat(overview): mirror per-stage status type in api client"
```

---

### Task 3: Frontend -- fix the default base-project shooter in MatchShell

**Files:**
- Modify: `src/splitsmith/ui_static/src/components/match/MatchShell.tsx:219-221`

**Interfaces:**
- Consumes: `pickDefaultShooterSlug` (already imported at `MatchShell.tsx:39`).
- Produces: base `project` scoped to the footage-bearing default shooter on the slug-less Overview route.

- [ ] **Step 1: Change the fallback shooter**

In `src/splitsmith/ui_static/src/components/match/MatchShell.tsx`, in the `listMatchShooters().then(...)` block, replace the alphabetically-first fallback:

Find (~lines 217-228):

```typescript
        // No URL slug -> fall back to the alphabetically-first shooter
        // so the sidebar has stage status to show.
        if (!slug && r.shooters.length > 0) {
          api
            .getProject(r.shooters[0].slug)
            .then((p) => {
              if (alive) setProject(p);
            })
            .catch(() => {
              if (alive) setProject(null);
            });
        }
```

Replace with:

```typescript
        // No URL slug -> fall back to the footage-bearing default shooter
        // (same rule the nav links use) so the sidebar + Overview base
        // project show a shooter that actually has work, not the
        // alphabetically-first footage-less one.
        const fallbackSlug = pickDefaultShooterSlug(r.shooters);
        if (!slug && fallbackSlug) {
          api
            .getProject(fallbackSlug)
            .then((p) => {
              if (alive) setProject(p);
            })
            .catch(() => {
              if (alive) setProject(null);
            });
        }
```

- [ ] **Step 2: Typecheck + scoped lint**

Run: `pnpm -C src/splitsmith/ui_static typecheck && pnpm -C src/splitsmith/ui_static exec eslint src/components/match/MatchShell.tsx`
Expected: PASS, no new errors.

- [ ] **Step 3: Commit**

```bash
git add src/splitsmith/ui_static/src/components/match/MatchShell.tsx
git commit -m "fix(overview): base project defaults to footage-bearing shooter"
```

---

### Task 4: Frontend -- stage-matrix pure functions

**Files:**
- Create: `src/splitsmith/ui_static/src/lib/stageMatrix.ts`

**Interfaces:**
- Consumes: `ShooterListEntry` (`stage_statuses`, `slug`, `name`, `video_count`) and `StageEntry` (`stage_number`, `stage_name`) from `@/lib/api`; `statusTone`, `isTerminal`, `isNextUpCandidate` from `@/lib/stageStatus`.
- Produces:
  - `type StageMatrixCell = { shooter: ShooterListEntry; status: StageStatus; tone: StageStatusTone }`
  - `type StageMatrixRow = { stageNumber: number; stageName: string; cells: StageMatrixCell[]; rollupTone: StageStatusTone; auditedCount: number }`
  - `type MatchTotals = { totalShooterStages: number; auditedShooterStages: number; auditedPct: number; stagesFullyDone: number; stagesInProgress: number; stagesUntouched: number; hasAnyFootage: boolean }`
  - `buildStageMatrix(stages: StageEntry[], shooters: ShooterListEntry[]): StageMatrixRow[]`
  - `matchTotals(rows: StageMatrixRow[], shooters: ShooterListEntry[]): MatchTotals`

- [ ] **Step 1: Write the module**

Create `src/splitsmith/ui_static/src/lib/stageMatrix.ts`:

```typescript
/**
 * Aggregate per-stage x per-shooter model for the match Overview.
 *
 * The backend gives each shooter a ``stage_statuses`` list; this module
 * pivots those into one row per match stage (cells across shooters) plus a
 * per-row rollup tone and match-level totals. Pure functions -- no I/O, no
 * React -- so the logic stays reviewable even though ``ui_static`` has no
 * test runner. Keep the all-todo (empty) and all-audited (complete) edge
 * cases explicit below.
 */

import type { ShooterListEntry, StageEntry, StageStatus } from "@/lib/api";
import {
  isTerminal,
  isNextUpCandidate,
  statusTone,
  type StageStatusTone,
} from "@/lib/stageStatus";

export type StageMatrixCell = {
  shooter: ShooterListEntry;
  status: StageStatus;
  tone: StageStatusTone;
};

export type StageMatrixRow = {
  stageNumber: number;
  stageName: string;
  cells: StageMatrixCell[];
  rollupTone: StageStatusTone;
  auditedCount: number;
};

export type MatchTotals = {
  totalShooterStages: number;
  auditedShooterStages: number;
  auditedPct: number;
  stagesFullyDone: number;
  stagesInProgress: number;
  stagesUntouched: number;
  hasAnyFootage: boolean;
};

/** Roll a row of per-shooter statuses up to one tone for the tile:
 *  - "done"        when every cell is terminal (audited or skipped)
 *  - "in_progress" when at least one cell is active (not yet terminal but
 *                  has footage-driven progress -- ready/partial/in_progress)
 *  - "todo"        when every cell is todo (no footage anywhere)
 *  An empty roster (no cells) rolls up to "todo". */
function rollupTone(cells: StageMatrixCell[]): StageStatusTone {
  if (cells.length === 0) return "todo";
  if (cells.every((c) => isTerminal(c.status))) return "done";
  if (cells.some((c) => c.status !== "todo" && isNextUpCandidate(c.status))) {
    return "in_progress";
  }
  return "todo";
}

export function buildStageMatrix(
  stages: StageEntry[],
  shooters: ShooterListEntry[],
): StageMatrixRow[] {
  return stages.map((stage) => {
    const cells: StageMatrixCell[] = shooters.map((shooter) => {
      const entry = shooter.stage_statuses.find(
        (e) => e.stage_number === stage.stage_number,
      );
      // A shooter with no entry for this stage is treated as "todo"
      // (nothing recorded yet), which keeps the grid rectangular even if a
      // shooter's project predates a stage being added to the match.
      const status: StageStatus = entry?.status ?? "todo";
      return { shooter, status, tone: statusTone(status) };
    });
    return {
      stageNumber: stage.stage_number,
      stageName: stage.stage_name,
      cells,
      rollupTone: rollupTone(cells),
      auditedCount: cells.filter((c) => c.status === "audited").length,
    };
  });
}

export function matchTotals(
  rows: StageMatrixRow[],
  shooters: ShooterListEntry[],
): MatchTotals {
  const totalShooterStages = rows.length * shooters.length;
  const auditedShooterStages = rows.reduce((n, r) => n + r.auditedCount, 0);
  const stagesFullyDone = rows.filter(
    (r) => r.cells.length > 0 && r.cells.every((c) => isTerminal(c.status)),
  ).length;
  const stagesUntouched = rows.filter(
    (r) => r.cells.length > 0 && r.cells.every((c) => c.status === "todo"),
  ).length;
  const stagesInProgress = rows.length - stagesFullyDone - stagesUntouched;
  return {
    totalShooterStages,
    auditedShooterStages,
    auditedPct:
      totalShooterStages > 0
        ? Math.round((auditedShooterStages / totalShooterStages) * 100)
        : 0,
    stagesFullyDone,
    stagesInProgress,
    stagesUntouched,
    hasAnyFootage: shooters.some((s) => s.video_count > 0),
  };
}
```

- [ ] **Step 2: Typecheck + scoped lint**

Run: `pnpm -C src/splitsmith/ui_static typecheck && pnpm -C src/splitsmith/ui_static exec eslint src/lib/stageMatrix.ts`
Expected: PASS, no errors.

- [ ] **Step 3: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/stageMatrix.ts
git commit -m "feat(overview): stage-matrix pivot + match-totals helpers"
```

---

### Task 5: Frontend -- Home aggregate wiring (subtitle, empty gate, hero)

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Home.tsx` (build matrix + totals ~line 105-163; subtitle ~line 194; empty gate ~line 246; `ActiveVariant` hero ~line 296-375)

**Interfaces:**
- Consumes: `buildStageMatrix`, `matchTotals`, `StageMatrixRow`, `MatchTotals` from `@/lib/stageMatrix` (Task 4); `ShooterListEntry.stage_statuses` (Task 2).
- Produces: `ActiveVariant` receiving `rows: StageMatrixRow[]` and `totals: MatchTotals` (consumed by Task 6's grid).

- [ ] **Step 1: Import and compute the matrix + totals in `Home`**

In `src/splitsmith/ui_static/src/pages/Home.tsx`, add the import near the other `@/lib` imports:

```typescript
import {
  buildStageMatrix,
  matchTotals,
  type MatchTotals,
  type StageMatrixRow,
} from "@/lib/stageMatrix";
```

In the `Home` component body, after the `shooters` state/effect and near the existing `stageViews` memo (~line 105), add:

```typescript
  const stageRows = useMemo<StageMatrixRow[]>(
    () => (project ? buildStageMatrix(project.stages, shooters) : []),
    [project, shooters],
  );
  const totals = useMemo<MatchTotals>(
    () => matchTotals(stageRows, shooters),
    [stageRows, shooters],
  );
```

- [ ] **Step 2: Re-gate the empty state on match-wide footage**

Replace the `isEmpty` definition (~line 160-163):

```typescript
  const isEmpty =
    !project ||
    stageViews.length === 0 ||
    stageViews.every((v) => v.status === "todo");
```

with:

```typescript
  // Aggregate gate: the Overview is "empty" only when NO shooter in the
  // match has footage. A single footage-less shooter no longer blanks the
  // whole page (the pre-aggregate bug). Legacy single-shooter projects
  // (empty roster) fall back to the per-project stage check.
  const isEmpty =
    !project ||
    stageViews.length === 0 ||
    (shooters.length > 0
      ? !totals.hasAnyFootage
      : stageViews.every((v) => v.status === "todo"));
```

- [ ] **Step 3: Drop the shooter name from the subtitle**

Remove the `competitor_name` span (~line 194):

```typescript
          {project.competitor_name && <span>{project.competitor_name}</span>}
```

Delete that line entirely. (The date and scoreboard link around it stay.)

- [ ] **Step 4: Pass rows + totals into `ActiveVariant`**

Update the `ActiveVariant` call site (~line 254-262) to pass the new props and drop the now-unused single-shooter ones:

```typescript
          <ActiveVariant
            project={project}
            rows={stageRows}
            totals={totals}
            shooters={shooters}
            navSlug={navSlug}
          />
```

- [ ] **Step 5: Rewrite `ActiveVariant` signature + hero**

Replace the `ActiveVariant` function signature and its `{ ... }` prop type (~line 273-289) with:

```typescript
function ActiveVariant({
  project,
  rows,
  totals,
  shooters,
  navSlug,
}: {
  project: MatchProject;
  rows: StageMatrixRow[];
  totals: MatchTotals;
  shooters: ShooterListEntry[];
  navSlug: string | null;
}) {
  const navigate = useNavigate();
  const href = useMatchHref();
```

Replace the entire "Resume hero" `<section>...</section>` block (~line 296-375) with a match-progress summary:

```typescript
      {/* Match-progress summary */}
      <section
        className="relative mb-6 overflow-hidden rounded-2xl border border-rule-strong p-7 shadow-[inset_0_1px_0_rgba(255,255,255,0.04),0_24px_48px_-24px_rgba(0,0,0,0.6)]"
        style={{
          backgroundImage:
            "radial-gradient(900px 220px at 20% 30%, rgba(255,45,45,0.10), transparent 65%), linear-gradient(135deg, var(--color-surface) 0%, var(--color-surface-2) 100%)",
        }}
      >
        <span
          aria-hidden
          className="absolute inset-y-0 left-0 w-[3px] bg-led shadow-[0_0_16px_var(--color-led-glow)]"
        />
        <div className="relative z-10">
          <div className="mb-2.5 inline-flex items-center gap-2.5 font-mono text-[0.625rem] font-bold uppercase tracking-[0.2em] text-led">
            <span
              aria-hidden
              className="inline-block size-1.5 rounded-full bg-led shadow-[0_0_8px_var(--color-led-glow)]"
            />
            Match Overview
          </div>
          <h2 className="mb-3 font-display text-4xl font-bold uppercase leading-none tracking-tight text-ink">
            {totals.auditedShooterStages} of {totals.totalShooterStages}{" "}
            <span className="text-led">shooter-stages</span> audited
          </h2>
          <div
            className="mb-4 h-2 w-full max-w-xl overflow-hidden rounded-full bg-surface-3"
            role="progressbar"
            aria-valuenow={totals.auditedPct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Match audited percentage"
          >
            <span
              className="block h-full rounded-full bg-led shadow-[0_0_12px_var(--color-led-glow)]"
              style={{ width: `${totals.auditedPct}%` }}
            />
          </div>
          <div className="inline-flex overflow-hidden rounded-[10px] border border-rule bg-surface-3">
            <HeroStat
              label="Match audited"
              value={`${totals.auditedPct}%`}
              tone="led"
            />
            <HeroStat
              label="Fully done"
              value={pad2(totals.stagesFullyDone)}
              tone={totals.stagesFullyDone > 0 ? "led" : undefined}
            />
            <HeroStat
              label="In progress"
              value={pad2(totals.stagesInProgress)}
              tone={totals.stagesInProgress > 0 ? "live" : undefined}
            />
            <HeroStat label="Untouched" value={pad2(totals.stagesUntouched)} />
          </div>
        </div>
      </section>
```

Note: this removes all use of `nextUp`, `totalsByTone`, `auditedPct`, and `stageHref` inside `ActiveVariant`. The `stageHref` const and the "Stages" `SectionHead` count that referenced `totalsByTone` are handled in Task 6. For this task, if the typecheck flags `stageHref`/`totalsByTone` as still-referenced further down in the Shooters/Stages sections, leave those references until Task 6 — but to keep this task independently green, temporarily keep the existing "Shooters" and "Stages" sections (lines ~377-466) intact. They still typecheck because `shooters` is still passed. The only removed props are `nextUp`, `totalsByTone`, `auditedPct`; grep `ActiveVariant` for those three names and confirm none remain except inside the Shooters/Stages sections rewritten in Task 6.

To keep Task 5 self-contained and green, replace the two now-broken references in the existing Shooters/Stages sections:
- The "Stages" `SectionHead` `count` (~line 434-456) references `totalsByTone`. Replace its `count` prop with a matrix-derived one:

```typescript
        count={
          <>
            <b className="font-bold text-ink-2">{pad2(totals.stagesFullyDone)}</b>{" "}
            fully done <span className="text-whisper">&middot;</span>{" "}
            <b className="font-bold text-ink-2">{pad2(totals.stagesInProgress)}</b>{" "}
            in progress <span className="text-whisper">&middot;</span>{" "}
            <b className="font-bold text-ink-2">{pad2(totals.stagesUntouched)}</b>{" "}
            untouched
          </>
        }
```

- The stage-grid `.map` (~line 458-466) still references `stageViews`/`stageHref`. Leave it rendering the old `StageTile` for now by mapping over `rows` with a placeholder until Task 6; to keep it compiling, temporarily render nothing:

```typescript
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-[repeat(auto-fill,minmax(240px,1fr))]">
        {/* Per-shooter chip tiles land in Task 6 */}
      </div>
```

- [ ] **Step 6: Typecheck + scoped lint**

Run: `pnpm -C src/splitsmith/ui_static typecheck && pnpm -C src/splitsmith/ui_static exec eslint src/pages/Home.tsx`
Expected: PASS. If typecheck reports an unused `stageViews`/`toneForStatus`/`nextUp` symbol, it is because Task 6 still needs them — leave `stageViews` defined (the EmptyVariant still uses it) and remove only genuinely unused locals flagged by the compiler.

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/Home.tsx
git commit -m "feat(overview): match-progress hero + match-wide empty gate"
```

---

### Task 6: Frontend -- per-shooter stage-tile grid

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Home.tsx` (stage grid in `ActiveVariant` ~line 458; add an `AggregateStageTile` component near the other tile components ~line 645+)

**Interfaces:**
- Consumes: `StageMatrixRow` / `StageMatrixCell` from `@/lib/stageMatrix`; `useMatchHref` `href("audit", slug, stageNo)`; `toneForStatus`/tile styling already in `Home.tsx`.
- Produces: final aggregate stage grid (no further consumers).

- [ ] **Step 1: Add the `AggregateStageTile` component**

In `src/splitsmith/ui_static/src/pages/Home.tsx`, add near the other subcomponents (after `ActiveVariant`, alongside `StageTile`). Use the existing tile tone mapping `toneForStatus`/`tonePresentation` already in the file for the tile border/label; the code below uses a local tone->class map for the per-shooter chips. Confirm the tile-tone helper name by grepping `toneFor` in the file and reuse it for the tile shell rather than duplicating.

```typescript
function AggregateStageTile({
  row,
  hrefFor,
}: {
  row: StageMatrixRow;
  hrefFor: (slug: string, stage: number) => string;
}) {
  // Per-shooter chip color by status tone. Mirrors the tile tones used
  // elsewhere; kept local so the chip palette is obvious at a glance.
  const chipTone: Record<string, string> = {
    done: "border-led-deep bg-led/15 text-led",
    in_progress: "border-live/50 bg-live/10 text-live",
    ready: "border-rule-strong bg-surface-3 text-ink-2",
    partial: "border-rule-strong bg-surface-3 text-ink-2",
    todo: "border-rule bg-surface-2 text-whisper",
    skipped: "border-rule bg-surface-2 text-muted",
  };
  return (
    <div className="rounded-xl border border-rule-strong bg-surface-2 p-4">
      <div className="mb-3 flex items-center gap-2.5">
        <span className="font-mono text-xs font-bold text-muted">
          {pad2(row.stageNumber)}
        </span>
        <span className="truncate font-display text-sm font-semibold uppercase tracking-[0.04em] text-ink">
          {row.stageName}
        </span>
      </div>
      <div className="mb-2.5 flex flex-wrap gap-1.5">
        {row.cells.map((cell) => (
          <Link
            key={cell.shooter.slug}
            to={hrefFor(cell.shooter.slug, row.stageNumber)}
            title={`${cell.shooter.name} -- ${statusLabel(cell.status)}`}
            className={`inline-flex size-7 items-center justify-center rounded-full border font-mono text-[0.625rem] font-bold uppercase transition-transform hover:-translate-y-0.5 ${chipTone[cell.tone] ?? chipTone.todo}`}
          >
            {initials(cell.shooter.name)}
          </Link>
        ))}
      </div>
      <div className="font-mono text-[0.625rem] uppercase tracking-[0.08em] text-muted">
        {row.auditedCount} of {row.cells.length} audited
      </div>
    </div>
  );
}

/** Two-letter initials for a shooter chip ("Mathias Axell" -> "MA"). */
function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}
```

Confirm `Link` is imported from `react-router-dom` in `Home.tsx`; if not, add it. Confirm `statusLabel` is imported from `@/lib/stageStatus`; if not, add it to the existing import.

- [ ] **Step 2: Render the grid from `rows`**

Replace the placeholder grid from Task 5 Step 5 (the empty `<div className="grid ...">{/* Task 6 */}</div>`) with:

```typescript
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-[repeat(auto-fill,minmax(240px,1fr))]">
        {rows.map((row) => (
          <AggregateStageTile
            key={row.stageNumber}
            row={row}
            hrefFor={(slug, stage) => href("audit", slug, String(stage))}
          />
        ))}
      </div>
```

- [ ] **Step 3: Typecheck + build + scoped lint**

Run: `pnpm -C src/splitsmith/ui_static typecheck && pnpm -C src/splitsmith/ui_static exec eslint src/pages/Home.tsx && pnpm -C src/splitsmith/ui_static build`
Expected: all PASS. Fix any now-unused imports/locals the compiler flags (e.g. an orphaned `StageTile`/`toneForStatus` if nothing else references it -- remove only if truly unused).

- [ ] **Step 4: Manual verification in the running app**

Start the app (see the project's `run` skill / dev server) and open the ESS BLACK HANDGUN 2026 match Overview. Confirm:
- No "AWAITING FOOTAGE" / "ADD FOOTAGE TO GET STARTED" while Mathias has footage.
- Subtitle shows the date + scoreboard link, no shooter name.
- Hero shows "N of M shooter-stages audited" with a filled progress bar.
- Each stage tile shows one chip per shooter; Mathias's chips on audited stages are lit; clicking a chip opens that shooter's audit for that stage.
- The left sidebar stage list is no longer dimmed/"awaiting".

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/Home.tsx
git commit -m "feat(overview): per-shooter status chips on stage tiles"
```

---

## Self-review notes

- Spec coverage: data layer (Tasks 1-2), MatchShell root-cause fix (Task 3), matrix/rollup/totals (Task 4), subtitle + hero + empty gate (Task 5), per-shooter chip grid (Task 6), backend fixture test (Task 1), scope boundaries respected (sidebar stays per-shooter, only the default shooter changes; legacy single-shooter fallback preserved in the `isEmpty` gate).
- The legacy single-shooter path (empty roster) keeps the old `stageViews.every(todo)` gate and the existing single-card fallbacks in `EmptyVariant`/`ActiveVariant` Shooters section, which are untouched.
- Types are consistent across tasks: `stage_statuses` / `StageStatusEntry` (Tasks 1-2), `StageMatrixRow`/`MatchTotals`/`buildStageMatrix`/`matchTotals` (Task 4) consumed verbatim in Tasks 5-6.
