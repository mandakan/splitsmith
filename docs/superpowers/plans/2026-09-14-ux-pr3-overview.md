# UX PR 3: Overview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the match Overview as the stage pipeline table: one row per stage showing its place in the loop, its provisional or audited splits, and exactly one next action, with the header's primary button pointing at the loop's next step.

**Architecture:** The server's `_build_triage_response` already walks every shooter's audit docs per stage; `TriageCell` grows seven fields computed from data it holds. The SPA derives rows, actions and stats in a pure module (`lib/overview.ts`) and renders them with the PR 2 primitives (`PageHeader`, `StatStrip`, `DataTable`, `Chip`, `Button`). Beep review's Confirm and Triage's Accept become row actions; the pages stay until PR 5 / PR 8.

**Tech Stack:** FastAPI + pytest (server), React 19 + Tailwind 4 + vitest (SPA).

**Spec:** `docs/superpowers/specs/2026-09-13-ux-restructure-and-visual-budget-design.md` section 4.2. Mockup, approved 2026-09-14 (expand rows + chips filter; sync as a one-line row; stage cards and the shooters strip go): https://claude.ai/code/artifact/e559e89a-040f-4ad7-81eb-7798ed67460d

## Global Constraints

- Same as PR 2: `corepack pnpm` gates, no prettier, no new deps, ASCII copy, `git checkout uv.lock` after `uv run`, never `git add -A`, delete `.playwright-mcp/` before committing, attribution lines on commits.
- Visual budget: only primitives from `components/ui`; this page deletes its `/* eslint-disable no-restricted-syntax ... */` line and must lint clean.
- Splits rank at least equal with scoring (memory `splits-are-the-product`): draw and avg split columns sit before time.
- Red marks the current position and the one primary action; stage state is amber / green / hollow.
- Local demo match for verification (PR 2 plan, Global Constraints): `seed_demo_match.py`, `splitsmith ui --project ... --port 5174`, start with `nohup ... &` in its own command, kill by pid, re-read `match.json` for the id after re-seeding.

---

### Task 1: Branch and the `TriageCell` fields

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`TriageCell` at ~4567, `_build_triage_response` at ~13480)
- Test: `tests/test_triage_api.py`

**Interfaces:**
- Produces on `TriageCell`: `video_count: int`, `beep_time: float | None` (primary's, source-absolute), `beep_reviewed: bool`, `shot_count: int` (kept shots), `draw: float | None`, `avg_split: float | None`, `time_seconds: float`. `draw` / `avg_split` come from `share_card.stage_figures(engine_shots)`; both `None` when there is no audit doc.

- [ ] **Step 1: Branch**

```bash
cd /home/mathias/work/splitsmith && git checkout main && git pull --ff-only && git checkout -b ux/pr3-overview
```

- [ ] **Step 2: Failing test**

Append to `tests/test_triage_api.py`:

```python
def test_triage_cells_carry_pipeline_fields(client: _MatchClient, seeded_stage: dict) -> None:
    """The Overview's stage table reads one triage GET per load (UX PR 3):
    every cell carries its footage, beep, shot and split figures. Stage 1
    has an audit doc (two shots: 0.4 s draw, 1.2 s split); stage 2 has
    none and reports null figures rather than zeros."""
    resp = client.get("/api/match/triage")
    assert resp.status_code == 200, resp.text
    cells = {c["stage_number"]: c for c in resp.json()["cells"]}
    s1, s2 = cells[1], cells[2]
    assert s1["video_count"] == 1
    assert s1["beep_time"] == 5.0
    assert s1["beep_reviewed"] is False
    assert s1["shot_count"] == 2
    assert s1["draw"] == pytest.approx(0.4)
    assert s1["avg_split"] == pytest.approx(1.2)
    assert s1["time_seconds"] == 10.0
    assert s2["shot_count"] == 0
    assert s2["draw"] is None
    assert s2["avg_split"] is None
```

Run: `uv run pytest tests/test_triage_api.py::test_triage_cells_carry_pipeline_fields -n0 -q`
Expected: FAIL with `KeyError: 'video_count'`.

- [ ] **Step 3: Implement**

In `TriageCell` add, after `needs_attention`:

```python
    # UX PR 3: the Overview's pipeline table reads these instead of a
    # second per-stage walk. All derived from data this builder already
    # holds; the split figures use the same rule as the share card.
    video_count: int = 0
    beep_time: float | None = None
    beep_reviewed: bool = False
    shot_count: int = 0
    draw: float | None = None
    avg_split: float | None = None
    time_seconds: float = 0.0
```

In `_build_triage_response`, inside the stage loop, after `anomalies` is computed:

```python
                figures = stage_figures(engine_shots) if doc is not None else None
```
(hoist `engine_shots` out of the `if doc is not None:` block: initialise `engine_shots: list[Shot] = []` before it). Then extend the `TriageCell(...)` call:

```python
                        video_count=len([v for v in stg.videos if v.role != "ignored"]),
                        beep_time=prim.beep_time if prim is not None else None,
                        beep_reviewed=bool(prim.beep_reviewed) if prim is not None else False,
                        shot_count=len(engine_shots),
                        draw=figures.draw if figures is not None else None,
                        avg_split=figures.avg_split if figures is not None else None,
                        time_seconds=float(stg.time_seconds),
```

Import `stage_figures` from `..share_card` next to the other `share_card` imports in `server.py` (grep `from ..share_card`), and `Shot` from `..config` if not already imported.

- [ ] **Step 4: Run, format, commit**

```bash
uv run pytest tests/test_triage_api.py -n0 -q && uv run black --check src/splitsmith/ui/server.py tests/test_triage_api.py && uv run ruff check src/splitsmith/ui/server.py tests/test_triage_api.py; git checkout uv.lock
git add src/splitsmith/ui/server.py tests/test_triage_api.py
git commit -m "feat(api): triage cells carry footage, beep, shot and split figures for the Overview"
```

---

### Task 2: `lib/overview.ts` -- rows, actions, stats

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (`TriageCell` type gains the seven fields)
- Create: `src/lib/overview.ts`, `src/lib/overview.test.ts`

**Interfaces:**
```ts
export type RowAction =
  | { kind: "add_footage" }
  | { kind: "confirm_beep" }
  | { kind: "running" }
  | { kind: "audit"; accept: boolean }   // accept: true when shot_count > 0 and status is not audited
  | { kind: "splits" }
  | { kind: "none" };                     // skipped

export interface OverviewCell {
  slug: string; shooterName: string; status: StageStatus;
  videoCount: number; beepTime: number | null; beepReviewed: boolean; beepConfidence: number | null;
  shotCount: number; flagCount: number; draw: number | null; avgSplit: number | null; timeSeconds: number;
  audited: boolean; provisional: boolean;   // audited = countsAsDone(status); provisional = has figures && !audited
  running: boolean;                          // a job for this slug+stage is pending/running
  action: RowAction;
}
export interface OverviewRow {
  stageNumber: number; stageName: string;
  cells: OverviewCell[];              // one per shooter, in shooter order
  lead: OverviewCell | null;          // the audio-source (or only) shooter's cell; figures shown on the parent row
  auditedCount: number; worst: RowAction; // worst = the earliest-in-loop action among cells
}
export function buildOverviewRows(args: { triage: TriageResponse; shooters: ShooterListEntry[]; leadSlug: string | null; jobs: Job[] }): OverviewRow[]
export function rowAction(cell: Omit<OverviewCell, "action">, threshold: number): RowAction
export function nextAction(rows: OverviewRow[]): { row: OverviewRow; cell: OverviewCell } | null
   // first cell (stage order, shooter order) whose action is confirm_beep or audit
export function overviewStats(rows: OverviewRow[]): { audited: number; total: number; needsFootage: number; avgDraw: number | null; avgSplit: number | null; scoredTime: number | null }
   // averages over audited cells only; scoredTime = sum of lead time_seconds over stages with time > 0
export function formatClock(seconds: number): string   // 316.9 -> "5:16.9"
```

Action rule, in order: `status === "skipped"` -> none; `videoCount === 0` -> add_footage; `!beepReviewed` (or `beepTime == null`) -> confirm_beep; `running` -> running; `countsAsDone(status)` -> splits; otherwise audit with `accept = shotCount > 0`. `flagCount = anomalies.length`. `running` = some job with matching `shooter_slug` and `stage_number` whose status is pending/running and kind in (`detect_beep`, `trim`, `shot_detect`).

- [ ] **Step 1: Failing tests**

`overview.test.ts` with real numbers from the Stockholm match: a triage fixture of four cells (stage 1 no video; stage 3 audited with draw 1.97 / avg 0.386 / 30 shots; stage 6 detected with 33 shots and 4 anomalies, `beep_reviewed: true`; stage 10 with `beep_reviewed: false` and confidence 0.42), one shooter, no jobs. Assert:
- `rows.map(r => r.lead!.action.kind)` is `["add_footage", "splits", "audit", "confirm_beep"]` and stage 6's action has `accept: true`.
- `nextAction(rows)` points at stage 6 (the first audit/confirm in stage order).
- `overviewStats(rows)` gives `audited 1, total 4, needsFootage 1, avgDraw 1.97, avgSplit 0.386, scoredTime` = sum of the four `time_seconds`.
- With a running `shot_detect` job for stage 6, its action is `running` and `nextAction` moves to stage 10.
- `formatClock(316.9) === "5:16.9"` and `formatClock(48.63) === "48.63"` (under a minute: seconds with two decimals).
- Multi-shooter: two shooters, stage 6 audited for `s1` and detected for `s2`, `leadSlug: "s1"`: the parent's `lead` is s1's cell, `auditedCount` is 1, `worst.kind` is `"audit"`.

- [ ] **Step 2: Implement** `lib/overview.ts` per the interfaces; add the seven fields to `TriageCell` in `api.ts` (all required in the type; the server always sends them).

- [ ] **Step 3: Run, commit**

```bash
corepack pnpm vitest run src/lib/overview.test.ts && corepack pnpm typecheck
git add src/splitsmith/ui_static/src/lib/overview.ts src/splitsmith/ui_static/src/lib/overview.test.ts src/splitsmith/ui_static/src/lib/api.ts
git commit -m "feat(ui): overview row, action and stat derivation"
```

---

### Task 3: `SyncCard` becomes a one-line row

**Files:**
- Modify: `src/components/match/SyncCard.tsx` (render only; state and handlers unchanged), `src/components/match/SyncCard.test.tsx` (label expectations only where the visible text changes)

**Interfaces:**
- Same props. Renders `<section aria-label="Hosted sync">` as one row: a 22 px icon cell, "Hosted sync", a muted status sentence ("not set up yet" / "synced 2 min ago" / "synced 2 min ago, 3 files changed" / "1 video pending upload"), then right-aligned `Settings` (ghost) and the primary sync action as `default` (never `primary`: the page's primary is the next-step button). Errors render as a second line in `text-led-text` inside the same row. Height 40 px; no glow, no card padding.

- [ ] **Step 1: Read the current render** (`SyncCard.tsx:180-260`) and list every visible string; keep them verbatim unless the mockup changes them (the mockup only shortens "Not set up yet - pushes this match to your splitsmith.app account." to "not set up yet" with the explanation in the Settings dialog, which already carries it).

- [ ] **Step 2: Re-render** as the row; run `corepack pnpm vitest run src/components/match/SyncCard.test.tsx`, fix only the expectations that pinned the long sentence.

- [ ] **Step 3: Commit** `feat(ui): hosted sync as a one-line row`.

---

### Task 4: `OverviewTable` and `OverviewCards`

**Files:**
- Create: `src/components/overview/OverviewTable.tsx`, `src/components/overview/OverviewTable.test.tsx`, `src/components/overview/OverviewCards.tsx` (mobile)

**Interfaces:**
```ts
interface OverviewTableProps {
  rows: OverviewRow[]; multi: boolean; filterSlug: string | null; currentStage: number | null;
  threshold: number; editDenied: boolean;
  hrefs: { audit: (slug: string, stage: number) => string; splits: (slug: string, stage: number) => string; footage: (slug: string) => string; beep: (slug: string, stage: number) => string };
  onAccept: (slug: string, stage: number) => void;
}
```
- Columns: `#`, Stage, Footage, Beep, Shots, Audit, Draw, Avg split, Time, action. Cell content per the mockup: Footage = `{n} cam(s)` or `<Chip tone="warn">none</Chip>`; Beep = time to 2 dp, plus `<Chip tone="warn">confirm</Chip>` when not reviewed, plus `<Chip tone="warn">{pct}%</Chip>` when reviewed but confidence < threshold; Shots = count plus `<Chip tone="warn">{n} flag(s)</Chip>`; Audit = `<Chip tone="ok" tick="fire">audited</Chip>` | dimmed "detected" | dimmed "detecting..." | dimmed "--"; Draw / Avg split / Time = numerals, `dim` unless audited.
- Action cell by `RowAction`: add_footage -> `<Button size="sm" asChild><Link to=footage>Add footage</Link></Button>`; confirm_beep -> Confirm beep (link to `beep`); running -> dimmed "running"; audit -> `Audit` (link; `variant="primary"` only when this cell is the `nextAction`) plus ghost `Accept` when `accept` and `!editDenied`; splits -> ghost `Splits` link; none -> empty.
- Multi (`multi && filterSlug == null`): parent row shows `videoCount` etc. as `"{k} / {n}"` counts across cells and the lead's figures; an expand toggle (`aria-expanded`) reveals one child `<Tr>` per cell with the shooter's initials avatar and name in the Stage column. `filterSlug` set: single-shooter rendering of that cell.
- `currentStage` row gets `<Tr current>`.
- `OverviewCards`: same `OverviewRow[]`, one card per stage as in the mockup's phone frame (title line with audit chip, meta line, figures line, action row).

- [ ] **Step 1: Failing tests** (`OverviewTable.test.tsx`, rows built from the same fixture as Task 2 via `buildOverviewRows`): renders one `tr` per stage; stage 1 shows "Add footage"; stage 6 shows a primary `Audit` and a ghost `Accept` when `nextAction` is stage 6; `Accept` is absent when `editDenied`; clicking `Accept` calls `onAccept("s1", 6)`; stage 3's draw cell is not dimmed and stage 6's is; multi mode with two shooters shows "1 / 2" in the Audit column and expanding row 6 shows both shooter names.

- [ ] **Step 2: Implement** both components with `Table/Th/Td/Tr`, `Chip`, `Button`, `Label`; no arbitrary sizes (the file is new, so the lint rule applies from the first line).

- [ ] **Step 3: Run, typecheck, lint, commit** `feat(ui): Overview pipeline table and mobile cards`.

---

### Task 5: `Home.tsx` rebuilt

**Files:**
- Modify: `src/pages/Home.tsx` (rewrite: ~1095 lines become ~250), `src/pages/Home.capabilities.test.tsx`; Create: `src/pages/Home.overview.test.tsx`
- Delete from `Home.tsx`: `ActiveVariant`, `EmptyVariant`, `HeroStat`, `SectionHead`, `ShooterCard`, `AddShooterCard`, `EmptyStageTile`, `AggregateStageTile`, `HelpCard`, `toneForStatus`, `initials`, `pad2`, `expectedShotsFromStage`; the `Kicker`, `Avatar`, `StageCompareLink` imports; the grandfather comment.

**Structure of the new page:**
1. Data: `ctx.project`, `ctx.shooters` (from the outlet; drop the page's own `listMatchShooters` fetch unless the outlet's list is null on first paint -- read `MatchShell` to confirm it is populated before children render), `api.getTriage()` on mount and after every accept (`acceptStage` returns the fresh `TriageResponse`; set it directly), `ctx.jobs` for `running`. Refetch triage when `ctx.jobs` settles (same pattern MatchShell uses: track active job ids, refetch when one leaves the set).
2. `leadSlug`: the audio-source shooter when the match has one (read `project.compare_camera`? no -- the manifest's audio source is a compare concept; for the Overview use the URL/default shooter: `pickDefaultShooterSlug(shooters)` from `lib/defaultShooter.ts`).
3. Render:
   - `<PageHeader title={project.name} sub={date · shooter or "N shooters" · division · <a>View on scoreboard</a>} actions={[Edit stages (hidden when editDenied), Share (hosted only, share_manage), primary next action]}>` with the shooter chips as `children` when `shooters.length > 1` (a row of `Chip`s: "All" + one per shooter, click sets `filterSlug`).
   - `{deploymentMode === "local" ? <SyncCard .../> : null}`
   - `<StatStrip>` with Audited `n / N`, Needs footage, Avg draw, Avg split, Scored time (`formatClock`). Dashes when null.
   - No footage anywhere (`stats.needsFootage === stats.total`): the empty block from the mockup (one sentence, one primary "Add footage" linking to `ingest/<slug>` or `shooters`), no table.
   - Otherwise `isMobile ? <OverviewCards/> : <OverviewTable/>`.
   - `EditStagesDrawer` unchanged.
4. The primary next-action button: label `Audit 06 B5 All` / `Confirm beep 10 B3` (ordinal padded, name), links to the same href the row uses. Absent when `nextAction` is null (everything audited): then the primary is `Splits` (link to `/results`).
5. `currentStage` for the `<Tr current>`: the `nextAction` row.

- [ ] **Step 1: Update `Home.capabilities.test.tsx`**: mock `api.getTriage` (resolve a two-cell fixture) and `api.acceptStage`; rename the button expectation to `"Edit stages"`; replace the "add a squadmate" expectations with: edit denied -> no `Accept` button and no `Edit stages`; edit allowed -> both present.

- [ ] **Step 2: Write `Home.overview.test.tsx`** (failing): header primary reads `Audit 06 B5 All` for the four-stage fixture; the stats strip shows `1 / 4`; clicking Accept on stage 6 calls `api.acceptStage("s1", 6)` and the table re-renders from the returned response; with every cell audited the primary reads `Splits`; with zero videos anywhere the empty block renders and no table.

- [ ] **Step 3: Rewrite `Home.tsx`**; run `corepack pnpm vitest run src/pages/Home` until green; `corepack pnpm exec eslint src/pages/Home.tsx` must report 0 errors with the grandfather comment removed.

- [ ] **Step 4: Commit** `feat(ui): Overview is the stage pipeline table`.

---

### Task 6: Verification and PR

- [ ] **Step 1: Gates** -- `corepack pnpm typecheck && lint && test && build`; `uv run pytest tests/test_triage_api.py tests/test_ui_server.py -q` (the triage builder is exercised by both); `git checkout uv.lock`.
- [ ] **Step 2: Screenshots** against the demo match at 1440 and 390: Overview (single shooter), Overview after clicking Accept on stage 7 (row flips to audited, primary moves to stage 8), the empty state (re-seed a match with no videos: run the seeder with an env var `DEMO_EMPTY=1` -- add that switch to the seeder), and a multi-shooter match (add a second shooter to the demo via `POST /api/match/shooters` with name "Anna" then re-open; the second shooter has no footage, so rows show "1 / 2"). Delete `.playwright-mcp/`.
- [ ] **Step 3: Republish** the Overview artifact with a "Built" section (same recipe as PR 2).
- [ ] **Step 4: Push and open the PR** (`ux/pr3-overview` -> `main`), body: what the table shows, the `TriageCell` extension, the Accept wiring, what was deleted from `Home.tsx`, gates, artifact link. Squash-merge note.

---

## Self-review

- Spec 4.2 coverage: header + primary next action (Task 5), five stats (Task 2/5), table columns and one action per row (Task 4), provisional figures dimmed (Task 4), multi-shooter expansion (Task 4), removal of cards / duplicated kicker / "shooter-stages" (Task 5), sync row (Task 3). Beep review's Confirm links to the existing beep-review page until PR 5 gives Audit its step 1; Triage's Accept uses the existing endpoint.
- Placeholders: none; every string and column is named in the mockup or the task.
- Type consistency: `RowAction` kinds are used identically in Tasks 2, 4 and 5; `buildOverviewRows`'s `jobs` argument is `ctx.jobs ?? []`; `acceptStage` returns `TriageResponse`, which Task 5 sets directly.
