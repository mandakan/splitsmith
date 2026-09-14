# UX PR 4: Splits, stage page, share surface

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Results page as "Splits" (five stats, one row per stage, splits before scoring), put the stage page on the built primitives, and give the anonymous share surface the same two pages under one bar.

**Architecture:** One server field (`stages[].figures` on the project payload) feeds a pure `lib/splitsTable.ts` that derives rows, collapse, stats and scoreboard totals; `pages/Results.tsx` maps that to `PageHeader` / `StatStrip` / `Table`. `pages/ResultsStage.tsx` keeps its player, camera, play-all, reclassify and comment logic and swaps only its header and list rendering. `ShareShell` renders a share bar shaped like `GlobalBar`.

**Tech Stack:** FastAPI + pytest (server field); React 19, Tailwind 4, vitest (SPA). `corepack pnpm typecheck | lint | test | build` in `src/splitsmith/ui_static`; `uv run pytest -n0 <file>` for one server test; `git checkout uv.lock` after any `uv run`.

**Spec:** `docs/superpowers/specs/2026-09-13-ux-restructure-and-visual-budget-design.md` s4.5, s5, s6. Mockup: https://claude.ai/code/artifact/b4487da7-3acf-4420-9815-c6d70236e2b2

## Decisions taken on the mockup (2026-09-14)

1. Splits header primary is **Play all**; Share is neutral (hosted owner) and the row play glyph opens a stage.
2. Multi-shooter: shooter chips filter; "All" shows the lead shooter's row per stage with a disclosure to one sub-row per shooter (same rule as Overview).
3. Scoreboard totals are a dimmed footer row in the scorecard's columns.
4. Share chrome is one bar: brand + match crumb + "Analyse your own matches" pill; footer line "Made with Splitsmith".

## Global Constraints

- Visual budget (spec s5, CLAUDE.md "UI: the visual budget"): build with `components/ui` primitives only; no `text-[...]`, `tracking-[...]`, `font-display` in pages/components (lint enforces); one `Button variant="primary"` per view; red only for brand, primary, current position, focus; splits rank at least equal with scorecard figures; no flavour copy.
- Every rebuilt file deletes its `/* eslint-disable no-restricted-syntax ... */` line. Files rebuilt here: `pages/Results.tsx`, `pages/ResultsStage.tsx`, `components/results/SplitsList.tsx`, `components/results/Scorecard.tsx`, `components/share/ShareShell.tsx`. Files that stay grandfathered (out of scope, untouched): `ResultsPlayer.tsx`, `CamPicker.tsx`, `ReclassifySheet.tsx`, `ShareDialog.tsx`, `components/comments/CommentPanel.tsx`.
- Share surface: no allowlist change. Share viewers see no workflow state ("no video", never "not audited"), no Share / refresh / reclassify affordances.
- Route stays `/results`; nav label is already "Splits" (PR 2).
- Existing behaviour tests must stay green: `pages/ResultsStage*.test.tsx` (play-all, cameras, trim-stale, reclassify, comments), `App.routes.share.test.tsx`, `lib/useActiveShare.test.tsx`, `components/match/StageCompareLink.test.tsx`. `pages/Results.test.tsx`, `components/results/SplitsList.test.tsx` and `components/share/ShareShell.test.tsx` are rewritten for the new rendering; their hosted-link cases keep their assertions.

---

### Task 1: `stages[].figures` on the project payload

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`get_project`, near line 7835)
- Test: `tests/test_triage_api.py` (same `client` fixture as `test_triage_cells_carry_pipeline_fields`)

**Interfaces:**
- Produces: each `stages[]` dict on `GET /api/shooters/{slug}/project` carries `figures: {draw: float|None, avg_split: float|None, fastest_split: float|None, shot_count: int, split_count: int} | None` (`None` when no audit doc exists for the stage). Served unchanged on `/api/share/{token}/shooters/{slug}/project` (already allowlisted).

- [ ] **Step 1: Write the failing test**

```python
def test_project_stages_carry_figures(client: _MatchClient) -> None:
    """The Splits page (UX PR 4) reads draw / avg split / fastest split /
    shot count per stage from the project payload, on both the owner and
    the share surface. Stage 1: 0.4 s draw, splits 0.3 and 0.25 (the
    fastest); stage 2 has no doc and reports null, not zeros."""
    doc = {
        "stage_number": 1,
        "shots": [
            {"shot_number": 1, "ms_after_beep": 400},
            {"shot_number": 2, "ms_after_beep": 700},
            {"shot_number": 3, "ms_after_beep": 950},
        ],
        "audit_events": [],
    }
    assert client.put("/api/shooters/alice/stages/1/audit", json=doc).status_code == 200
    resp = client.get("/api/shooters/alice/project")
    assert resp.status_code == 200, resp.text
    stages = {s["stage_number"]: s for s in resp.json()["stages"]}
    f1 = stages[1]["figures"]
    assert f1["draw"] == pytest.approx(0.4)
    assert f1["avg_split"] == pytest.approx(0.275)
    assert f1["fastest_split"] == pytest.approx(0.25)
    assert f1["shot_count"] == 3
    assert f1["split_count"] == 2
    assert stages[2]["figures"] is None
```

- [ ] **Step 2: Run it, expect KeyError on `"figures"`**

Run: `uv run pytest -n0 tests/test_triage_api.py::test_project_stages_carry_figures -v`

- [ ] **Step 3: Implement in `get_project`**

Inside the `for stage_dict in payload.get("stages", [])` loop, after the status assignment, add (reusing `audit_docs` loaded once before the loop -- `audit_docs = state.load_audit_docs(slug)` is already computed for `statuses`, so bind it to a name):

```python
            doc = (audit_docs or {}).get(int(n))
            if doc is None and audit_docs is None:
                doc, _ = state.load_audit(slug, int(n))
            stage_dict["figures"] = _stage_figures_payload(doc, project, int(n))
```

and a module-level helper next to `_build_triage_response`'s imports (`stage_figures` and `Shot` are already imported there; add `from ..coach import statistic_splits` in sorted position):

```python
def _stage_figures_payload(doc: dict | None, project: Project, stage_number: int) -> dict | None:
    """Per-stage splits summary for the project payload (UX PR 4). The
    split rule is ``coach.statistic_splits``; ``share_card.stage_figures``
    shapes draw + average, this adds the fastest split and the counts.
    ``None`` (not zeros) when the stage has no audit doc."""
    if doc is None:
        return None
    stage = next((s for s in project.stages if s.stage_number == stage_number), None)
    prim = stage.primary() if stage is not None else None
    beep = prim.beep_time if prim is not None and prim.beep_time is not None else 0.0
    shots = audit_shots_to_engine_shots(doc, beep_time_in_source=beep)
    fig = stage_figures(shots)
    splits = statistic_splits(shots) if shots else []
    return {
        "draw": fig.draw,
        "avg_split": fig.avg_split,
        "fastest_split": min(splits) if splits else None,
        "shot_count": len(shots),
        "split_count": fig.split_count,
    }
```

(`Project` is the legacy project model already named in this module; match the name `get_project`'s `project` variable has. Check `state.load_audit` returns `(doc, version)` -- it does.)

- [ ] **Step 4: Run the test and the two neighbours**

Run: `uv run pytest -n0 tests/test_triage_api.py -k "figures or project" -v` then `uv run pytest tests/test_ui_server.py -q`; `git checkout uv.lock`.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_triage_api.py
git commit -m "feat(api): per-stage split figures on the project payload"
```

---

### Task 2: `lib/splitsTable.ts`

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (`StageEntry` gains `figures?: StageFigures | null`; new `StageFigures` interface)
- Create: `src/splitsmith/ui_static/src/lib/splitsTable.ts`
- Test: `src/splitsmith/ui_static/src/lib/splitsTable.test.ts`

**Interfaces (produces):**

```ts
export interface StageFigures { draw: number | null; avg_split: number | null; fastest_split: number | null; shot_count: number; split_count: number }

export interface SplitsCell {
  slug: string; shooterName: string;
  status: StageStatus; audited: boolean; skipped: boolean;
  draw: number | null; avgSplit: number | null; fastestSplit: number | null; shotCount: number;
  timeSeconds: number; scorecard: StageScorecard | null;
}
export type SplitsRow =
  | { kind: "stage"; stageNumber: number; stageName: string; cells: SplitsCell[]; lead: SplitsCell | null; auditedCount: number }
  | { kind: "collapsed"; from: number; to: number; firstName: string; lastName: string; reason: "no_footage" | "not_audited" | "no_video"; count: number };

export function buildSplitsRows(args: { projects: Record<string, MatchProject | null>; shooters: ShooterListEntry[]; leadSlug: string | null; filterSlug: string | null; share: boolean }): SplitsRow[]
export function splitsStats(rows: SplitsRow[]): { avgDraw: number | null; avgSplit: number | null; fastestSplit: number | null; shots: number; scoredTime: number | null; audited: number; total: number }
export function scoreboardTotals(rows: SplitsRow[]): { time: number; hitFactor: number | null; alphas: number; charlies: number; deltas: number; misses: number } | null
export function formatHits(sc: StageScorecard): string   // "12A 6C 1D" (M, NS, P appended only when > 0; "DQ" appended when dq)
export function firstPlayable(rows: SplitsRow[]): { slug: string; stageNumber: number } | null
export function scorecardSyncedAt(project: MatchProject | null): string | null   // max scorecard_updated_at
```

Rules the module owns (each is a test):

- A stage row's `cells` are one per shooter in shooter-list order, built from `projects[slug].stages[n]` (`figures` -> draw/avg/fastest/shots; `status`; `time_seconds`; `scorecard`). A shooter whose project is missing (fetch failed / in flight) gets no cell.
- `lead` is the `filterSlug` cell when set, else the `leadSlug` cell, else the first cell.
- `audited = countsAsDone(status)`; `skipped = status === "skipped"`.
- Collapse: consecutive stage rows whose lead cell is not audited collapse into one `collapsed` row when they share the same reason. Reasons: owner surface `no_footage` (lead has zero non-ignored videos) or `not_audited` (anything else, skipped included); share surface always `no_video`. Owner `not_audited` rows do **not** collapse (each carries its own Audit link) -- only `no_footage` and `no_video` runs do. On "All" in multi-shooter, a stage row stays a stage row whenever any shooter's cell is audited.
- `splitsStats` averages `draw` and `avgSplit` over lead cells that are audited; `fastestSplit` is the min over those; `shots` sums their `shotCount`; `scoredTime` sums `timeSeconds > 0` over all lead cells (scoreboard figure, audit-independent; same rule as `overviewStats`), null when none; `audited` / `total` count lead cells.
- `scoreboardTotals` sums over lead cells with a scorecard (time, points -> hitFactor = points / time, A/C/D/M); null when no lead cell has a scorecard.
- `firstPlayable` is the first stage row (stage order) whose lead is audited.
- `formatClock` is imported from `lib/overview.ts`, not re-implemented.

- [ ] **Step 1: Add the api types** (`StageFigures`, `figures?: StageFigures | null` on `StageEntry` with a one-line doc pointing at UX PR 4).

- [ ] **Step 2: Write `splitsTable.test.ts`** with a `makeProject(slug, stages: Array<{n, name, status, figures?, time?, scorecard?, videos?}>)` helper (copy the `makeProject` shape from `pages/Results.test.tsx`; it needs the new `figures` field) and one `it` per rule above, plus:
  - "collapsed run reports from/to and the two names" (`{from: 9, to: 12, firstName: "B3-1", lastName: "Stage B2 Left", count: 4}`),
  - "share surface collapses not-audited and no-footage runs together as no_video",
  - "owner surface keeps not-audited stages as one row each",
  - "formatHits omits zero M/NS/P and appends DQ".

- [ ] **Step 3: Run, expect module-not-found**: `corepack pnpm vitest run src/lib/splitsTable.test.ts`

- [ ] **Step 4: Implement `lib/splitsTable.ts`** per the interface, with a module doc comment in the style of `lib/overview.ts`.

- [ ] **Step 5: Run to green, typecheck**: `corepack pnpm vitest run src/lib/splitsTable.test.ts && corepack pnpm typecheck`

- [ ] **Step 6: Commit** `feat(ui): splits table derivation`

---

### Task 3: `SplitsTable` and `SplitsCards`

**Files:**
- Create: `src/splitsmith/ui_static/src/components/results/SplitsTable.tsx`
- Create: `src/splitsmith/ui_static/src/components/results/SplitsCards.tsx`
- Test: `src/splitsmith/ui_static/src/components/results/SplitsTable.test.tsx`

**Interfaces:**

```ts
export interface SplitsHrefs { stage: (slug: string, n: number) => string; audit: (slug: string, n: number) => string }
export interface SplitsTableProps {
  rows: SplitsRow[]; multi: boolean; filterSlug: string | null; share: boolean;
  totals: ReturnType<typeof scoreboardTotals>; hrefs: SplitsHrefs;
}
```

Rendering (desktop, `Table` / `Th` / `Td` / `Tr` primitives):

- Header: `#`, `Stage`, `Draw`, `Avg split`, `Fastest`, `Shots`, `Time`, `HF`, `Hits`, and an unlabelled play column. `HF` and `Hits` headers carry `className="text-subtle"`.
- Stage row (lead audited): ordinal `Td kind="ordinal"` (`pad2`), name `Td kind="name"`; four `Td kind="num"` for draw (2 dp), avg split (3 dp), fastest (3 dp), shots; time `Td kind="num"` (`formatClock`); HF `Td kind="num" dim` (2 dp or dash); hits `Td dim` (`formatHits`); play cell: a `Link` to `hrefs.stage(lead.slug, n)` with `aria-label="Play stage {n}"`, rendered as a 26 px round outline with a filled triangle (`Play` from lucide at `size-3`), muted at rest, ink on hover.
- Stage row (lead not audited, owner): ordinal, dim name, one `Td colSpan={5} dim` reading `not audited` followed by ` · ` and a `Link` "Audit" to `hrefs.audit(lead.slug, n)` (text-ink-2); HF and hits still rendered dim from the scorecard when present; empty play cell.
- Collapsed row: ordinal `pad2(from)–pad2(to)` (en dash), dim name `"{firstName} to {lastName}"` (or just the name when `count === 1`), `Td colSpan={5} dim` with `no footage` / `no video`, dashes for HF and hits.
- Multi-shooter, `filterSlug == null`: the name cell gets a `Button variant="ghost" size="sm"` disclosure with `aria-expanded`, label `▾ {auditedCount}` / `▴ {auditedCount}` (aria-label "Show shooters" / "Hide shooters" -- same wording as OverviewTable), and a `StageCompareLink` when `auditedCount >= 2`. Expanded child rows: `Tr className="bg-surface-2"`, empty ordinal cell, shooter name in `Td` (text-ink-2, not bold), the same figure cells, a play link per audited cell; a non-audited child reads `not audited` (owner) / `no video` (share) across the five figure columns.
- Footer (`totals != null`): a final `Tr` whose cells are `text-muted font-mono text-sm`, label `Scoreboard` in the name cell, empty figure cells, time `formatClock(totals.time)`, HF 2 dp, hits `"{A}A {C}C {D}D"` plus `" {M}M"` when misses > 0. The row gets `className="border-t border-rule-strong"`.
- `SplitsCards` (phone, `useIsMobile`): one `<section aria-label="Stage {n} {name}">` per row, `rounded-[10px] border border-rule bg-surface px-3.5 py-3 text-md`: line 1 ordinal + name + play link (or dim `no footage` / `not audited` / `no video` on the right); line 2 `numeral text-sm text-muted flex flex-wrap gap-x-2.5` with `draw <b>1.84</b>`, `avg <b>0.52</b>`, `fast <b>0.31</b>`, `<b>28</b> shots`, `<b>48.63</b>` (b = `font-medium text-ink`); line 3 `text-sm text-subtle`: `HF 2.12 · 10A 16C 5D` when a scorecard exists. Collapsed rows are one dim line. Multi-shooter shows the lead / filtered cell only (the table is the per-shooter surface). No footer on mobile.

- [ ] **Step 1: Write `SplitsTable.test.tsx`**: build rows with `buildSplitsRows` from two projects (one audited stage with figures + scorecard, one not audited with scorecard, a 3-stage no-footage run) and assert: audited row shows `1.84`, `0.520`, `0.310`, `28`, `48.63`, dim `2.12`, `10A 16C 5D`, a link named `Play stage 2`; not-audited owner row shows `not audited` and an `Audit` link and still shows the dim HF; collapsed row shows `03–05` and `no footage` and no play link; share render shows `no video` and no Audit link; multi + no filter renders the disclosure and, after click, the child rows; footer shows `Scoreboard` and the hits string.

- [ ] **Step 2: Run, expect failure**; **Step 3: Implement both components**; **Step 4: Run to green, lint, typecheck**.

- [ ] **Step 5: Commit** `feat(ui): SplitsTable and SplitsCards`

---

### Task 4: `pages/Results.tsx` becomes Splits

**Files:**
- Rewrite: `src/splitsmith/ui_static/src/pages/Results.tsx` (drop the eslint-disable line; drop the `Kicker` import)
- Rewrite: `src/splitsmith/ui_static/src/pages/Results.test.tsx`
- Modify: `src/splitsmith/ui_static/src/lib/stageMatrix.ts` only if `matchTotals` has no other consumer after this task (`grep -rn "stageMatrix" src`); Home no longer uses it after PR 3 -- delete dead exports, keep `buildStageMatrix` if Coach/Compare import it.

Page structure (keep every data effect the current file has: hosted-twin probe, refresh-from-scoreboard, per-shooter project fetch -- extend the fetch to single-shooter too so `projects[slug]` is always the source; the outlet `project` seeds `projects[defaultSlug]` synchronously so the first paint has rows):

```tsx
<div className="w-full max-w-[1100px] mx-auto px-4 py-4 md:px-7 md:py-5">
  <PageHeader
    title="Splits"
    sub={<>{shooterLine} · {audited} of {total} stages audited · {syncedAt ? <>Scorecard synced {formatDateTime(syncedAt)} {canRefresh ? <RefreshGlyph/> : null}</> : null}</>}
    actions={<>{shareButton}{playAll}</>}>
    {shooters.length > 1 ? <ShooterChips .../> : null}
  </PageHeader>
  {error alerts}
  <StatStrip lead className="mb-4">  Avg draw / Avg split / Fastest split / Shots / Scored time  </StatStrip>
  {isMobile ? <SplitsCards/> : <SplitsTable/>}
  {canShare && showShare ? <ShareDialog onClose/> : null}
</div>
```

- Share surface sub-line: `{shooterLine} · {formatDate(match_date)} · {n} stages on video` (no audit wording, no sync line).
- `RefreshGlyph`: `<button type="button" aria-label="Refresh from scoreboard" disabled={refreshing}>` rendering `RefreshCw` (`Loader2 animate-spin` while refreshing) at `size-3.5`, `text-ink-2 hover:text-ink`, inline after the sync text. Same `refreshFromScoreboard` body as today.
- `shareButton`: hosted owner -> `<Button onClick={() => setShowShare(true)} aria-label="Manage share links for these results">Share</Button>`; local with hosted twin -> `<Button asChild><a href={hostedResultsHref} target="_blank" rel="noopener noreferrer" aria-label="Share on splitsmith.app - opens the hosted results page">Share on splitsmith.app</a></Button>` plus the `Unsynced changes - sync first to share them` hint when stale (keep this copy: `Results.test.tsx` asserts it). Share surface: nothing.
- `playAll`: `firstPlayable(rows)` -> `<Button variant="primary" asChild><Link to={`${href("results", slug, String(n))}?play=all`}>Play all</Link></Button>`; hidden when nothing is playable (no primary on an empty page).
- Shooter chips: copy the `role="group" aria-label="Filter by shooter"` block from `pages/Home.tsx` verbatim (same `Chip` tones, `aria-pressed`).
- `hrefs`: `stage: (slug, n) => href("results", slug, String(n))`, `audit: (slug, n) => href("audit", slug, String(n))`.
- Stats: `fmt(v, 2)` for draw, `fmt(v, 3)` for avg and fastest, `String(shots)`, `formatClock(scoredTime)`; tone `dim` when null (same pattern as Home).

New `Results.test.tsx` (keep the mock harness; `makeProject` gains `figures` on stage 1 and a scorecard):
- "renders one row per stage with splits before scoring" -- header `Splits`, cells in order, `Play stage 1` link.
- "collapses stages without footage on the owner surface and keeps not-audited rows" .
- "share surface reads no video, no Share, no refresh, no Audit link".
- "the header primary is Play all pointing at the first audited stage with ?play=all".
- "multi-shooter chips filter the table to one shooter" (click `Bjorn`, assert his figures / `not audited`).
- Keep verbatim: the five hosted-link cases ("links a synced match to its hosted results page", "warns when local changes have not been pushed yet", "omits the link before the first push", "never probes the local-only sync endpoints in hosted mode", "never probes them on the anonymous share surface").

- [ ] **Step 1: Rewrite the tests** (expect failures on the rendering cases).
- [ ] **Step 2: Rewrite the page.**
- [ ] **Step 3: `corepack pnpm vitest run src/pages/Results.test.tsx src/lib/stageMatrix.test.ts` (if present) then `corepack pnpm lint` (the file must pass without the disable line) and `typecheck`.**
- [ ] **Step 4: Commit** `feat(ui): Splits page on the primitives`

---

### Task 5: Shot table and scorecard footer

**Files:**
- Rewrite: `src/splitsmith/ui_static/src/components/results/SplitsList.tsx` (same export name `SplitsList`, same props, so `ResultsStage` and its tests keep working)
- Rewrite: `src/splitsmith/ui_static/src/components/results/Scorecard.tsx` -> keep the `Scorecard` export, render the footer shape; delete `matchTotals` from it (Task 4 moved totals to `lib/splitsTable.ts`)
- Update: `components/results/SplitsList.test.tsx` (chip role/name assertions), `StageStats.test.tsx` unchanged

`SplitsList` rendering (a `<section>` with a `Table`-like shell; not the `Table` primitive because rows are buttons, not `<tr>`):
- Header row: `Label`-styled cells `#`, `T`, `Split`, `Interval` (last right-aligned), `grid-cols-[34px_56px_70px_1fr] gap-2.5 px-3.5 py-2 border-b border-rule-strong`.
- Row: same grid, `min-h-11`, `border-b border-rule last:border-b-0`, `data-shot-number`, active row `bg-surface-2 shadow-[inset_2px_0_0_var(--color-led)]` -- no glow (glow is for live state only). The seek `<button>` spans `#`, `T`, `Split`: ordinal `font-mono text-sm text-muted` (`pad2`, the one place a leading zero belongs), `T` `numeral text-md text-ink-2 text-right`, split `numeral text-md font-medium text-right` coloured by tier: `text-done` quick, `text-live` long, `text-ink` typical or unjudged. Drop the separate tier dot+label -- the numeral colour is the tier, the legend under the ruler names it.
- Interval cell: `Chip tick={TICK_FOR_CLASS[interval_class]}` with `INTERVAL_LABEL`; map `first_shot -> "draw"`, `movement -> "movement"`, `transition -> "transition"`, `split -> "fire"`, `reload -> "reload"`, `activation -> "activation"` (check `CoachIntervalClass` in `lib/api.ts` for the full union and map every member; unknown -> `"muted"`). Owner (`onReclassify`): the chip sits inside `<button aria-label="Reclassify shot N (label)">` as today; unclassified reads `Classify` with tick `muted`. Share: plain chip, none for unclassified. `improvement_flag`: `Flag` icon `size-3.5 text-live` with `role="img" aria-label="Flagged for improvement"` (amber, not red -- red is not for flags). `coaching_note`: second line `col-start-2 col-span-3 text-sm text-muted`.
- Keep the auto-scroll effect and the `max-lg:scroll-mt-[...]` class exactly as they are (the `calc(var(...))` arbitrary value is allowed by the lint; verify with `corepack pnpm lint`).

`Scorecard` footer: `<div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-rule-strong px-3.5 py-2.5 font-mono text-sm text-muted">` with `Hit factor <b>2.4595</b>`, `Stage <b>34.24%</b>`, `Points <b>79</b>`, `A 12 · C 6 · D 1 · M 0 · NS 0` (`Proc n` only when > 0), `DQ` as `Chip tone="warn"` when set, and a trailing `ml-auto font-sans text-sm` `From scoreboard, {formatTimestamp(updatedAt)}` when `updatedAt` is passed (new optional prop `updatedAt?: string | null`; `ResultsStage` passes `scorecardUpdatedAt` and drops its own caption). `b` = `font-medium text-ink-2`.

- [ ] **Step 1: Update `SplitsList.test.tsx`** expectations (the reclassify button name, the `Classify` affordance, seek still fires) and add "the split numeral carries the tier colour class" (`text-done` on a quick gap given baselines with >= 5 samples).
- [ ] **Step 2: Rewrite both components; run `SplitsList.test.tsx`, `pages/ResultsStage.test.tsx` (the reclassify flow depends on the chip button names).**
- [ ] **Step 3: Commit** `feat(ui): shot table with tiered splits, scorecard as footer`

---

### Task 6: `pages/ResultsStage.tsx` header and layout

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/ResultsStage.tsx` (drop the eslint-disable line and the `Kicker` import)

Keep every hook, effect and handler. Change only rendering:

- `header` -> `PageHeader`:
  - `ordinal={pad2(stage)}`, `title={coach.stage_name ?? ""}` (when the stage has no name, title is `Stage`), `back={{ label: "All stages", to: href("results") }}`.
  - `sub`: the shooter name, or the native `<select aria-label="Shooter">` for multi-shooter (keep the option-disabling logic; style `bg-transparent text-md text-muted hover:text-ink appearance-none pr-4` with the `ChevronDown` as today); after it, when `trimStale`, `<Chip tone="warn" role="status">Awaiting desktop re-process</Chip>` (the trim-stale tests look for this text).
  - `actions`, in order: hosted-owner `Share` (`Button`, opens the same `ShareDialog` as Results; import it here), `Compare shooters` (`Button asChild` `Link` to `href("compare", String(stage))`, `className="hidden md:inline-flex"`, only when `shooters.length > 1`), `Play all` (`Button` with `aria-pressed={playAll}`, `aria-label` and `title` unchanged; when on, `variant="primary"`, else `variant="default"` -- that is the page's one primary and it is live state), prev / next as `Button size="icon" variant="default" aria-label="Previous stage" / "Next stage"` (`Link` when available, disabled button otherwise). Check `button.tsx` for the icon size name; add `size="icon"` there if missing (`size-9`).
- Error state: `Button` for Retry, error text `text-sm text-led-text` in a `role="alert"` paragraph (no tinted box).
- Not-audited state (`!coach`): `PageHeader ordinal title="Stage"` + `<p className="text-md text-muted">Stage not audited yet.</p>` + `Button asChild Link` "Back to results" (keep the text: `ResultsStage.test.tsx` "links back" cases read it -- verify and keep whichever text those tests use).
- Legend under the player (desktop and mobile): `<div className="flex flex-wrap items-center gap-4 px-1 py-2 text-sm text-muted">` with three `<span>` each holding an `<i class="inline-block size-2 rounded-full bg-done|bg-ink-2|bg-live mr-1.5"/>` and `Quick` / `Typical` / `Long`, and a trailing `ml-auto` `Against your match baseline, per interval class`. Render it only when `baselines != null`.
- Right column order unchanged: `StageStats`, `SplitsList`, `Scorecard` (now with `updatedAt`), `CommentPanel`. Column wrapper classes unchanged.
- The `navButton` class string and the two `inline-flex min-h-11 ... font-display` link/button class strings are deleted.

- [ ] **Step 1: Make the changes; run all four `ResultsStage*.test.tsx` files and fix any assertion that keyed on removed copy (list each change in the commit body).**
- [ ] **Step 2: `corepack pnpm lint && corepack pnpm typecheck`.**
- [ ] **Step 3: Commit** `feat(ui): stage page header and legend on the primitives`

---

### Task 7: Share bar

**Files:**
- Modify: `src/splitsmith/ui_static/src/components/share/ShareShell.tsx` (drop the eslint-disable line)
- Update: `src/splitsmith/ui_static/src/components/share/ShareShell.test.tsx`

`ShareFrame` keeps its flex/scroll structure (the `md:h-dvh` lock is load-bearing for Compare). Replace the header and footer contents:

- Header `<nav aria-label="Global">`, `flex items-center gap-4 px-4 py-3 md:px-7 border-b border-rule bg-surface` plus the same hairline the shell draws under `GlobalBar` (copy the class from `RootLayout.tsx`): `Brand variant="compact"`, the wordmark `Splitsmith` in `font-display text-base font-bold uppercase tracking-tight text-ink` -- this is the one allowed `font-display` use; it lives in `components/ui/Brand.tsx` already if there is a wordmark variant: use that instead and do not write `font-display` in ShareShell -- then a crumb `<span className="text-md text-ink font-medium truncate">{project?.name}</span> / <span className="text-md text-ink-2">Splits</span>` (hidden below md), spacer, and `<a href={MARKETING_URL} target="_blank" rel="noopener" className="rounded-full border border-rule-strong px-3 py-1 text-md text-ink-2 hover:text-ink">Analyse your own matches ↗</a>`.
- Footer: `flex justify-between px-4 py-3 md:px-7 text-sm text-muted border-t border-rule`: `<a href={MARKETING_URL}>Made with Splitsmith</a>` and `<a href={MARKETING_URL}>splitsmith.app</a>`.
- `ShareUnavailable` / `ShareLoadError`: `Label` for "Share link", `<h1 className="text-lg font-semibold text-ink">` (no Antonio -- PageHeader is for pages, this is a notice), `text-md text-muted` body, `Button` for Try again.
- `ShareFrame` needs the project name: pass `project?.name ?? null` from `ShareShell` (dead / error frames pass null and render no crumb).

- [ ] **Step 1: Update `ShareShell.test.tsx`**: "renders the share bar with the match name and the marketing link, and the footer line" (assert `Analyse your own matches` link `href` and `Made with Splitsmith`), keep "keeps the header on the dead-link page".
- [ ] **Step 2: Implement; run the test, `App.routes.share.test.tsx`, lint, typecheck.**
- [ ] **Step 3: Commit** `feat(ui): share surface chrome`

---

### Task 8: Rendered checkpoint, screenshots, PR

- [ ] **Step 1: Full SPA gate**: `corepack pnpm typecheck && corepack pnpm lint && corepack pnpm test && corepack pnpm build`.
- [ ] **Step 2: Server gate**: `uv run pytest -q tests/test_triage_api.py tests/test_ui_server.py tests/test_share*.py`; `git checkout uv.lock`.
- [ ] **Step 3: Seed and run**: `uv run python scripts/seed_demo_match.py ~/.claude-tmp/demo-match` then `nohup uv run splitsmith ui --project ~/.claude-tmp/demo-match --skip-system-check --no-browser --port 5174 > ~/.claude-tmp/ui.log 2>&1 &`; wait for `/api/health`; read the match id from `match.json`.
- [ ] **Step 4: Screenshots** (Playwright MCP, 1440x900 and 390x844): `/match/<id>/results`, `/match/<id>/results/s_demo0001/3`, both at phone width. The share surface cannot run locally (hosted-only); cover it with `ShareShell.test.tsx` and show the bar in a vitest DOM snapshot pasted into the PR, or run the mock share harness in `~/.claude-tmp` (see memory `frontend-e2e-verification`) if it still works.
- [ ] **Step 5: Check the screenshots against the mockup** and against the budget: one primary per view, no leading zero on counts, HF/hits dimmed, split numerals coloured, chips neutral with a tick, no glow on the active row. Fix and re-shoot.
- [ ] **Step 6: Republish the mockup artifact** with a "Built" section carrying the screenshots (base64 JPEG), same URL.
- [ ] **Step 7: Clean up** `.playwright-mcp/` in the repo root, stop the server by pid.
- [ ] **Step 8: Push, open the PR** (`feat(ui): Splits page, stage page and share surface on the primitives (UX PR 4)`) with the artifact link and the decisions list; merge on green (standing instruction).
