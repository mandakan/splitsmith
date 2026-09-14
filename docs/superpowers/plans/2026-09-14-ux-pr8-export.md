# UX PR 8: Matches, Export, Account, and retiring the queue routes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The last PR of the restructure. Matches (`/pick`) opens on a Continue card that names the next action and a hairline table; Export keeps its mode / stages / options / summary shape on the primitives, every blocked stage says why in one line with the fix, the compare grid becomes its third mode; Account is a form on the primitives; the Beep review, Triage and Jobs rows leave the nav and their routes redirect for one release.

**Architecture:** One small server addition (`next_step` on `RecentProjectDetail`, computed in both enrichers from the per-stage status walk that already produces `stages_audited`). Two pure modules with tests: `lib/matches.ts` (filter, counts, the Continue pick and its label / href) and `lib/exportPlan.ts` (per-stage export rows with the blocker line and fix target per mode and deployment, eligibility, the duration estimate). A new `Field` primitive for form rows. The pages keep their data cores (bind / delete / import on Pick; the overview load, job submit and poll, cleanup dialog on Export; PATCH /api/me and the token section on Account) and map to primitives.

**Tech Stack:** React 19, Tailwind 4, vitest; FastAPI + pytest for the one server field. Demo: `scripts/seed_demo_match.py --media` (4 audited stages of 12, one stage without footage, one unassigned clip).

**Spec:** s4.1, s4.8, s4.9, s7 step 8 ("Remove the Beep review, Triage and Jobs routes from the nav (routes may redirect for one release)"). Mockup: https://claude.ai/code/artifact/60a61afb-0e33-48f6-9d42-65d36aa87437

## Decisions taken on the mockup (2026-09-14)

1. The Continue card's next action comes from the server: `RecentProjectDetail.next_step`, in both enrichers.
2. The compare-grid export folds into Export as its third output mode; `pages/MatchExport.tsx` is deleted and the slug-less `/export` route redirects to the lead shooter's export.
3. Delete match lives in the Export summary footer beside Reclaim space; the Matches row loses its trash icon and Cmd+Backspace.

## Global Constraints

- Budget as in PRs 3-7. One primary per view: Matches = the Continue card's button (New match becomes the primary only when no match can continue); Export = Export bundle / Export trims / Render grid; Account = Save. Red: the brand, the primary, the current row.
- Rebuilt files drop their grandfather line: `pages/Pick.tsx`, `pages/Export.tsx`, `pages/Account.tsx`, `components/account/DesktopTokensSection.tsx`, `components/export/ExportHistory.tsx`. Deleted: `pages/Triage.tsx` (+ test), `pages/Jobs.tsx` (+ test), `pages/MatchExport.tsx` (+ test; its cases move to `Export.compareGrid.test.tsx`), `components/export/primitives.tsx`. `pages/matchExportModel.ts` stays (pure, tested) and is imported by Export.
- Tests that stay green (copy-bound ones are updated to the new copy, listed per task): `Pick.devMode.test.tsx`, `Export.cleanup.test.tsx`, `Export.hostedCopy.test.tsx`, `Account.test.tsx`, `components/account/*.test.tsx`, `components/export/ExportHistory.test.tsx`, `App.routes.*.test.tsx`, `components/match/*`.
- Hosted never shows a desktop-only blocker: "mount the drive" / "Relink" copy lives only behind `deploymentMode === "local"` in `lib/exportPlan.ts`, pinned by test.
- `_SHARE_PATH_RE` is untouched.

---

### Task 1: server `next_step`

`src/splitsmith/ui/server.py`:

```py
class NextStep(BaseModel):
    kind: Literal["footage", "audit", "export"]
    shooter_slug: str | None = None
    stage_number: int | None = None
    stage_name: str | None = None

class RecentProjectDetail(BaseModel):
    ...
    # The picker's Continue card (UX PR 8): the first stage, in match order
    # and shooter order, that is neither audited nor skipped, or the phase
    # the match is in when no stage qualifies. Derived from the same
    # per-stage walk as ``stages_audited``; None for unresolved kinds.
    next_step: NextStep | None = None
```

Helper, module level, used by both enrichers:

```py
def _next_step_from_statuses(
    per_shooter: list[tuple[str, dict[int, StageStatus]]],  # match shooter order
    stage_names: dict[int, str],
    video_total: int,
) -> NextStep | None:
    if not per_shooter:
        return None
    if video_total == 0:
        return NextStep(kind="footage")
    for slug, statuses in per_shooter:
        for n in sorted(statuses):
            if statuses[n] not in (StageStatus.audited, StageStatus.skipped):
                return NextStep(kind="audit", shooter_slug=slug, stage_number=n, stage_name=stage_names.get(n, f"Stage {n}"))
    return NextStep(kind="export", shooter_slug=per_shooter[0][0])
```

Local enricher: inside the shooter loop replace `audited_total += legacy.audited_count(shooter_root)` with `statuses = legacy.stage_statuses(shooter_root); audited_total += sum(1 for s in statuses.values() if s == StageStatus.audited); per_shooter.append((slug, statuses))`; after the loop `detail.next_step = _next_step_from_statuses(per_shooter, {s.stage_number: s.stage_name for s in match.stages}, video_total)`. Hosted enricher: same with `proj.stage_statuses(project_root, audit_docs=audit_by_slug.get(slug, {}))` and `match_doc["stages"]`. `audited_count` is unchanged for its other callers.

- [ ] Test `tests/test_ui_server.py::test_recent_projects_detail_names_the_next_step` next to `test_recent_projects_detail_in_progress_once_footage_attached`: two stages, one shooter, stage 1 audited (write `audit/stage1.json` with a `save` event; see `stage_audit_status`), stage 2 with a primary video and time -> `next_step == {"kind": "audit", "shooter_slug": "ma", "stage_number": 2, "stage_name": "Two"}`; the no-footage fixture -> `{"kind": "footage"}`.
- [ ] Test in `tests/test_hosted_raw_upload.py::test_recent_projects_detail_reads_from_store_after_path_wiped`: assert `entry["next_step"]["kind"] == "footage"` (no upload yet).
- [ ] `uv run pytest tests/test_ui_server.py -k recent_projects -n0`; `git checkout uv.lock`; commit `feat(api): next_step on the recent-projects detail`.

### Task 2: `lib/api.ts`, `lib/matches.ts`, `lib/exportPlan.ts`, `components/ui/Field.tsx`

`lib/api.ts`: `export interface NextStep { kind: "footage" | "audit" | "export"; shooter_slug: string | null; stage_number: number | null; stage_name: string | null }` and `next_step: NextStep | null` on `RecentProjectDetail` (update the test fixtures that build one: `Pick.test.tsx`, `Pick.devMode.test.tsx`).

`lib/matches.ts`:

```ts
export type StatusFilter = "all" | "awaiting_footage" | "in_progress" | "exported" | "archived";
export function matchCounts(recents: RecentProjectDetail[]): Record<StatusFilter, number>   // missing kinds excluded from every count
export function filterMatches(recents, query: string, status: StatusFilter): RecentProjectDetail[]  // name + club (+ path only when `localFs`)
export function pickContinue(recents): RecentProjectDetail | null   // most recent last_modified_at ?? last_opened_at among kind === "match", status !== "archived", next_step != null
export function continueLabel(step: NextStep): string   // footage -> "Add footage"; audit -> `Audit stage ${pad2(n)} ${name}`; export -> "Export"
export function continueHref(match: RecentProjectDetail): string  // `/match/${id}/ingest` | `/match/${id}/audit/${slug}/${n}` | `/match/${id}/export/${slug}`
export function progressStates(m): PipelineState[]   // stages_audited done, the next one live when in_progress, rest todo
export function touchedAt(m): Date
```

`lib/exportPlan.ts`:

```ts
export type ExportMode = "single" | "trims" | "compare";
export type StageBlock = { reason: string; fix?: { label: string; to: "audit" | "footage" | "relink" | "scores" } };
export interface ExportStageRow { stage: StageExportStatus; time: number | null; shots: number | null; eligible: boolean; block: StageBlock | null }
export function exportRows(stages: StageExportStatus[], times: Map<number, number>, mode: ExportMode, hosted: boolean): ExportStageRow[]
```
Blocker ladder (first match wins), all one line, no issue numbers: skipped -> `{reason: "Skipped"}`; `source_reachable === false` -> hosted `{reason: "Upload missing -- the original upload is no longer stored", fix: {label: "Re-upload", to: "footage"}}` / local `{reason: "Source offline -- the video file is not reachable", fix: {label: "Relink", to: "relink"}}`; `!has_primary` -> `{reason: "No footage", fix: {label: "Footage", to: "footage"}}`; time missing (0) -> `{reason: "No stage time", fix: {label: "Import scores", to: "scores"}}`; mode trims and `!ready_to_trim` -> `{reason: "No confirmed beep", fix: {label: "Audit", to: "audit"}}`; mode single and `!ready_to_export` -> `{reason: "Not audited yet", fix: {label: "Audit", to: "audit"}}`; compare mode: eligible when `!skipped` (the server validates the rest). `eligible = block === null`.
```ts
export function estimateDuration(selected: number[], times, opts: { mode; head; tail; transitionKind; transitionSeconds; titleKind; titleSeconds }): number   // the existing formula from Export.tsx
export function summaryLines(...)  // the rail's rows as [label, value, dim?] so the rail maps them
```
- [ ] Tests: `lib/matches.test.ts` (counts skip missing, filter by club, continue picks the most recently touched with a step, label / href per kind, progress states); `lib/exportPlan.test.ts` (ladder order with one fixture per rung; hosted copy never contains "drive" or "Relink"; trims mode passes an unaudited stage with a beep; duration estimate matches the old numbers for a 2-stage cross-dissolve + slate case).

`components/ui/Field.tsx`:
```tsx
export function Field({ label, hint, help, error, htmlFor, children }: { label: string; hint?: ReactNode; help?: ReactNode; error?: string | null; htmlFor?: string; children: ReactNode })
```
`grid grid-cols-[150px_minmax(0,1fr)] gap-4 border-b border-rule px-3.5 py-3 last:border-b-0`; the label column is `Label` (as a `<label htmlFor>` when given) with the hint under it in `text-sm text-subtle`; the control column renders children, then `help` in `text-sm text-muted`, then `error` as `role="alert"` in `text-sm text-destructive`. Also `export const inputClass = "w-full rounded-md border border-rule-strong bg-surface-2 px-2.5 py-1.5 text-md text-ink outline-none placeholder:text-subtle focus:border-led disabled:opacity-50"` for bare inputs and selects. Export from the barrel.
- [ ] Test: label associates with the control, error announced.
- [ ] Commit `feat(ui): matches and export plan derivations, Field primitive`.

### Task 3: `pages/Pick.tsx`

Keep: fetches, `open`, `deleteProject` (kept for the Export page? no -- delete moves to Export in Task 4; Pick keeps no delete), `openExplicitPath`, `runImport`, `postBindTarget`, the `/` focus and arrow / Enter keys (drop Cmd+Backspace). The context row portal stays (its copy: "No match open" + `numeral` count + version + identity name; `Pick.test.tsx` `/standby/i` -> `/no match open/i`).

Render: `PageHeader title="Matches" sub={`${counts.all} matches · ${counts.in_progress} in progress`} actions={[Import backup (local only), Merge legacy (existing condition), New match (Button, `variant={continue ? "default" : "primary"}`, Kbd)]}`; the Continue card (`rounded-[10px] border border-rule-strong bg-surface` with a led left rule: `Label` "Continue", name in `font-display` via a `text-xl` heading? -- no: use `PageHeader`'s sibling style through a `DisplayName` span `font-display text-xl font-bold uppercase`... arbitrary sizes are banned, `text-xl` is on the scale, fine; `Next: <b>{continueLabel}</b> · {shooter}`, `PipelineDots` + `n / N stages audited` + `touched {relative}`; `Button variant="primary"` with the label's verb, `onClick` binds then navigates to `continueHref`); tools row (search input with `Kbd` "/", filter `Chip`s as buttons with `aria-pressed`, counts in `numeral`; Awaiting footage only when > 0); the table (`Table`/`Th`/`Td`/`Tr`: Match (name, `Chip` "Manual" when manual), Date (`formatDate` or em dash), Shooters (`AvatarStack size="sm"`), Stages (`numeral`, right), Progress (`PipelineDots` + `Chip tone="ok"` "Exported" / `Chip` "Awaiting footage" / muted "Archived" / `text-destructive` "Folder not found"), Touched, Open (`Button size="sm"`, "Restore" for archived, disabled when missing); the selected row carries `data-current` styling (led inset) and row click opens); empty state; then, local only, the two panels (`grid gap-px` list: Open by path input + Button; Import backup file + destination + Button + overwrite checkbox); footer keys legend + version. Archived rows sit in the same table, dimmed.

- [ ] Update `Pick.test.tsx` (standby copy, identity pill by name, hosted hides paths / open by path / import); add `Pick.continue.test.tsx`: renders the Continue card for the in-progress match with `next_step` audit -> "Audit stage 05 B5 Rear", clicking binds (`api.bindProject`) and navigates to `/match/<id>/audit/<slug>/5`; no card when every match is exported-and-archived; New match is the primary then.
- [ ] `Pick.devMode.test.tsx` green. Commit `feat(ui): Matches with the Continue card and the match table`.

### Task 4: `pages/Export.tsx` with the compare grid as mode three

Keep verbatim: the overview / project / runs load and `reload`, `selection` handling, `selectMode`, camera selectors, `hostedDownloads`, `submitTrims`, `submitExport`, `reveal`, `CleanupDialog` wiring, `ResultPanel` logic (restyled). Fold in from `MatchExport.tsx`: `shooters` load (`api.listMatchShooters`, only when mode is compare or on mount when `shooters.length >= 2` -- load once on mount, it is one call), `audioFrom`, `canvas`, `submit` -> `submitCompare` (uses `buildCompareGridPayload`, `summarizeGridResult`), the partial-result summary. Compare mode is offered only when the match has 2+ shooters (`ctx.shooters` from the outlet or the fetched list); eligibility in compare mode follows `exportRows(..., "compare")`.

Delete match: `Button variant="destructive" size="sm"` in the summary footer beside Reclaim space; uses the same `useConfirm` dialog and checkboxes `Pick.tsx` had (moved here), calls `api.deleteProject(matchRoot, ...)` (the match root from `api.listMatchShooters().match_root` / `ctx.matchRoot` -- check `MatchShellOutletContext`), then `navigate("/pick", { replace: true })`.

Render: `PageHeader title="Export" sub={`${shooterName} · ${eligible} of ${total} stages ready`} actions={Reveal folder (local, when exports_dir)}`; error line; `lg:grid-cols-[minmax(0,1fr)_320px]`:
- Output section (`rounded-[10px] border border-rule bg-surface`, header row `Label` + segmented control Timeline / Trims only / Compare grid (`aria-pressed` buttons; Compare grid `disabled` with `title` on single-shooter matches) + the mode's one-line sub). Trims mode adds the "Camera for the grid" `Field` (existing select); compare mode adds Reference (segmented shooters, beep tick on the active) and Canvas (segmented from `CANVAS_CHOICES`).
- Stages section: `Table` with checkbox (`aria-label="Select stage N"`), ordinal (`pad2`), name, Time (`numeral`), Shots, Status: `Chip tone="ok"` "Ready" or the blocker line (`text-sm text-muted`, "Source offline" as `Chip tone="warn"` + reason) with the fix as a `Link` (audit -> `href("audit", slug, n)`, footage -> `href("ingest", slug)`, scores -> `href("ingest", slug)` (the scores import lives on Footage / Overview), relink -> a `button` that calls the existing relink? -- Export has no relink; link to Footage with `?relink=n` is over-scope: make relink a `Link` to Footage). Sub line "n of N exportable · m selected". Rows without footage beyond the first three collapse to one "k more stages without footage" row (toggle expands).
- Options section (single mode only): `Field`-like rows with `Label` key, control, hint: Padding (segmented Tight / Normal / Full / Custom + two `NumInput`s when custom), Transition (segmented + duration), Title card (segmented + hold), Overlay (segmented + codec select when on), Format (select), Bundle name (input) + "exports/". Destination line with the path in mono.
- `ExportHistory` restyled (Label header, hairline rows, `numeral` duration, `text-live` anomalies).
- Summary rail (sticky): `Label` "Bundle" / "Trims" / "Grid" + `~ m:ss`; rows from `summaryLines`; the files list; the primary (`Button variant="primary"` full width: Export bundle / Export trims / Render grid; busy label from job message); queued note / result panel / compare summary under it; footer: Reclaim space (`Button variant="ghost" size="sm"`) and Delete match (`destructive`).
- [ ] `Export.hostedCopy.test.tsx`: assert the row says "Upload missing" and "Re-upload", and never "drive" / "Relink". `Export.cleanup.test.tsx` green. New `Export.compareGrid.test.tsx` ports the eight `MatchExport.test.tsx` cases onto the Export page in compare mode (mock `listMatchShooters` with two shooters, `getExportOverview`; the stage controls are checkboxes now, `getByRole("checkbox", {name: /Stage One/})`). New `Export.deleteMatch.test.tsx`: the confirm + `api.deleteProject` + navigate.
- [ ] `App.tsx`: `export` route -> `<DefaultShooterRedirect base="export" />`; remove the `MatchExport` import; delete `pages/MatchExport.tsx` + test, `components/export/primitives.tsx` (move `pad2` uses to `lib/utils`' or local; `SelectField` moves to `components/export/SelectField.tsx` restyled on `inputClass`).
- [ ] Commit `feat(ui): Export on the primitives with the stage table, compare grid as mode three, delete match`.

### Task 5: `pages/Account.tsx` and `DesktopTokensSection`

`PageHeader title="Account" sub={user.email} actions={Sign out if the shell has a sign-out (check `useAuth`; else none)}`; section Profile: `Field label="Display name" htmlFor="account-display-name" hint={`${n} / 60`} help={the existing sentence} error={error}` with the input (`inputClass`, `maxLength`), the sr-only live region kept, `Button variant="primary" size="sm"` Save + "Saved" in `text-done`. Section Desktop sync tokens: header `Label` + the one-line description; `Field label="New token"` with name input + `Button size="sm"` Create token; the reveal as a `border-live/50 ring-[3px] ring-live/10` row (`data-testid="token-reveal"` kept) with the input and Copy; the token list as `Table`: name, created, last used ("never used"), action (`Button variant="destructive" size="sm"` Revoke -> Confirm revoke / Cancel), revoked rows `line-through text-subtle` with "revoked" in the last-used cell. Keep every aria-label and the fetch / create / revoke logic verbatim.
- [ ] `Account.test.tsx`, `components/account/DesktopTokensSection.test.tsx` green (adjust queries only where the markup moved from cards to rows). Commit `feat(ui): Account on the form primitives`.

### Task 6: routes and nav

- `components/match/navItems.tsx`: remove the `triage` row and the `triageFlaggedCount` arg; `navItems.test.ts` keys become `["overview","videos","audit","results","coach","compare","export"]`, the triage describe block is deleted.
- `MatchShell.tsx` / `MatchSidebar.tsx`: drop `triageFlaggedCount` state, the `getTriageSummary` fetch and the prop; `pageTitle` for `/triage` and `/jobs` removed (the routes redirect before the title matters).
- `App.tsx`: `triage` and `jobs` routes -> `<Navigate to=".." replace />` won't carry the match prefix; use a small `RedirectToOverview` (`useParams` matchId -> `/match/${matchId}/`) next to `BeepReviewRoute`; delete `pages/Triage.tsx` (+ test), `pages/Jobs.tsx` (+ test). `lib/api.ts` keeps `getTriage` / `getTriageSummary` (Overview uses the former).
- `pages/MobileAudit.tsx`: "Go to jobs" -> `Link to={href("")}` "Overview".
- [ ] `App.routes.*.test.tsx`, `MatchShell.*.test.tsx` green; typecheck, lint, test, build. Commit `feat(ui): Triage and Jobs leave the nav; their routes redirect to Overview`.

### Task 7: checkpoint, docs, PR

- [ ] Seed the demo (`--media`), start the server on 5174, screenshot `/pick`, `/match/<id>/export/<slug>` (timeline, trims, and the stage table blockers), `/account` (hosted only -- skip on the local demo; the render test covers it), the sidebar; move shots to the scratchpad; republish the artifact with a Built section.
- [ ] `CLAUDE.md`: the restructure section becomes "eight merged as of 2026-09-14"; the no-go file list is replaced by "every page is on the primitives; new surfaces follow the budget"; keep the harness paragraph. `docs/superpowers/specs/...` untouched.
- [ ] PR `feat(ui): Matches, Export, Account on the primitives; Triage and Jobs routes retired (UX PR 8)`; merge on green.
