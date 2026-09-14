# UX PR 6: Footage

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One Footage page inside `MatchShell`: header with counts, an always-present drop zone, the coverage matrix (stage x shooter), the clip sheet, and side panels for unassigned videos, shooters and cameras. The ingest shell, the three-region workspace and the Shooters page fold into it.

**Architecture:** `pages/Ingest.tsx` keeps its data core (project + shooter fetches, scan / upload, move, assign, remove, relink, window drag) and gets a new render tree; the matrix and counts derive from `lib/footage.ts` (pure, tested) over `buildClipModel` per shooter. `ClipDetail` becomes `ClipSheet` (same writes, in a side sheet). The Shooters page's add flows (scoreboard roster, manual, connect) move into `AddShooterSheet`; its per-shooter controls into the Shooters panel. Routes: `ingest/:slug` moves under `MatchShell`; `/shooters` redirects to Footage; the Shooters nav row goes.

**Tech Stack:** React 19, Tailwind 4, vitest. `corepack pnpm typecheck | lint | test | build` in `src/splitsmith/ui_static`. Demo: `uv run python scripts/seed_demo_match.py ~/.claude-tmp/demo-match --media`.

**Spec:** s4.3, s5, s6. Mockup: https://claude.ai/code/artifact/e499759a-7be0-4bec-90cc-7216e1aa0c7a

## Decisions taken on the mockup (2026-09-14)

1. Clip detail is a sheet from the right (matrix stays in view); the three-region workspace goes.
2. Every stage is a row, covered or not; no collapsing.
3. The Shooters page folds completely: nav row goes, `/shooters` redirects to Footage, add / remove / rebuild-trims live in the Shooters panel and an Add shooter sheet.

## Global Constraints

- Visual budget as in PRs 3-5. One `Button variant="primary"` per view: **Add footage**. Red: primary, the primary-camera tick on a file chip, the current row (the row whose clip is open in the sheet). Beep state on a row: green tick confirmed, amber `unconfirmed` / `detecting`, dash for none.
- Files rebuilt here drop their grandfather line: `pages/Ingest.tsx`, `pages/ingest/ClipDetail.tsx` (renamed `components/footage/ClipSheet.tsx`), `components/ingest/RoleToggles.tsx`, `components/ingest/CoverageSelect.tsx`, `components/ingest/ShooterPickerPopover.tsx`, `components/ingest/IngestMoveBanner.tsx`. Deleted: `pages/ingest/ReviewLayout.tsx` (+ its test), `pages/ingest/ClipList.tsx`, `components/ingest/StageReferenceDrawer.tsx`, `components/ingest/CameraCard.tsx` (its mount-save logic moves into `CameraRow`), `pages/Shooters.tsx` (+ its test, rewritten as `AddShooterSheet.test.tsx` and `ShootersPanel.test.tsx` cases). Untouched and still grandfathered: `components/FolderPicker.tsx`, `components/HostedUploadModal.tsx`, `components/RelinkDialog.tsx`, `components/UploadDock.tsx`, `components/scoreboard/ConnectMatchDialog.tsx`, `components/StageTimeSection.tsx`.
- Audio source is a Compare-manifest property, not a shooter attribute: the mockup's `audio` chip on the shooter row is dropped.
- Existing tests that must stay green (update copy-bound assertions, keep the behaviour): `Ingest.addFootage.test.tsx`, `Ingest.emptyState.test.tsx` (the "empty state" is now the page with an empty matrix; the drop / picker / read-only cases keep their meaning), `Ingest.proxyPoll.test.tsx`, `App.routes.*`, `components/match/*`.

---

### Task 1: `lib/footage.ts`

**Files:** Create `src/lib/footage.ts` + `src/lib/footage.test.ts`.

```ts
export interface FootageCell { slug: string; shooterName: string; videos: StageVideo[]; primary: StageVideo | null; beep: { time: number | null; reviewed: boolean; detecting: boolean } }
export interface FootageRow { stageNumber: number; stageName: string; cells: FootageCell[]; covered: boolean /* every shooter has a primary */ }
export interface UnassignedItem { slug: string; shooterName: string; video: StageVideo; recordedAt: string | null; durationS: number | null }
export function buildFootageRows(args: { projects: Record<string, MatchProject | null>; shooters: ShooterListEntry[]; jobs: Job[] }): FootageRow[]
export function unassignedVideos(args: { projects; shooters }): UnassignedItem[]
export function footageStats(rows: FootageRow[], unassigned: UnassignedItem[]): { shooters: number; videos: number; covered: number; total: number; unassigned: number }
export function beepState(cell: FootageCell): { label: string; tone: "ok" | "warn" | "none" }   // "5.32" ok | "4.90 · unconfirmed" warn | "detecting…" warn | "—" none
```

Rules: stage rows come from the union of stages across the shooters' projects (placeholders skipped), sorted; a cell's videos are the stage's non-ignored... no: **all** videos including ignored (the chip shows the role); `primary` is the `role === "primary"` one; `beep` reads the primary (`detecting` when a `detect_beep` job for that shooter+stage is active); `covered` when every shooter with a project has a primary. Unassigned = each project's `unassigned_videos` with `recorded_at` / `duration` fields if the type has them (check `StageVideo`; otherwise null). `videos` in stats counts every video across projects, assigned and not.

- [ ] Tests: one per function, including the multi-shooter `covered` rule and the detecting state.
- [ ] Commit `feat(ui): footage matrix derivation`.

---

### Task 2: `CoverageMatrix`, `FootageCards`, side panels

**Files (create, with render tests in one `Footage.components.test.tsx`):**
- `components/footage/FileChip.tsx` -- `Chip`-shaped mono chip: role tick (`bg-led` primary, `bg-muted` secondary, hollow ring for ignored), the file's basename shortened to `VID_…066.MP4` (keep the first 4 and the last 7 chars of the stem + extension when longer than 18), `title` = full name, a `<button>` that calls `onOpen(video)`; `current` adds `border-ink-2 text-ink`.
- `components/footage/CoverageMatrix.tsx` -- `Table`; header `#`, `Stage`, one `Th` per shooter, `Th align="right"` `Beep` (single shooter only; multi-shooter puts the beep state inside each cell under the chips as `text-sm`), an unlabelled menu column. Cell with videos: the chips (wrap); without: `Td dim` `no footage · ` + `Assign…` button (`text-ink-2`, opens the sheet in assign mode for that stage + shooter -- see Task 3). Beep column: `numeral` time + green `✓`, or amber label, with a `Confirm` `Button size="sm"` linking to `href("audit", slug, String(n))` when unconfirmed. Row menu: `Button size="icon" variant="ghost" aria-label="Stage N actions"` opening a small menu (reuse the `Menu` pattern from `TransportLine`; lift it into `components/ui/Menu.tsx` so both use it): `Assign a video…`, `Detect beep` (primary present), `Open in Audit`. `Tr current` for the row whose clip is open.
- `components/footage/FootageCards.tsx` -- phone: one card per stage: ordinal, name, beep state on line 1; chips on line 2; `no footage` dim when empty.
- `components/footage/UnassignedPanel.tsx` -- `<section aria-label="Unassigned videos">` panel (`rounded-[10px] border border-rule bg-surface`): `Label` header with the count in `tone="live"` when > 0; per file a row: shortened name (button, opens the sheet), `recorded · duration` muted mono; a second line with a native `<select aria-label="Assign to stage">` (stages as `NN · name`) that calls `onAssign(video, stageNumber)` and an `Ignore` ghost button (`onIgnore(video)` = assign with role ignored to the stage the model suggests, or remove if none -- match what ClipDetail's ignore does today; read it). Empty: one muted line `Every video is placed`.
- `components/footage/ShootersPanel.tsx` -- `<section aria-label="Shooters">`: `Label` header + `Add` ghost button (`onAdd`); per shooter a row: initials disc (`size-5 rounded-full bg-surface-3 font-mono text-xs`), name (`font-medium text-ink`, a `Link` to `href("ingest", slug)` so the drop zone targets them; current one `text-ink` + led inset), `n videos` muted, and a menu (`aria-label="{name} actions"`): `Open Audit`, `Rebuild trims (n)` when `stages_missing_trim > 0`, `Remove…` (destructive, `useConfirm`). Single shooter: the muted line `Add a squadmate to compare runs side by side.` under the row. `editDenied` disables Add / Remove / Rebuild.
- `components/footage/CamerasPanel.tsx` -- `<section aria-label="Cameras">`: per `CameraGroup` a row: letter, `make model` muted mono, a native `<select aria-label="Mount for camera A">` over `CAMERA_MOUNTS` with the same save the old `CameraCard` did (`api.setCameraMount`-equivalent: read `CameraCard.tsx` for the exact call and the calibration hint; keep the hint as `title`), count. Hidden when no cameras.

- [ ] Tests: matrix renders chips / no-footage / beep states / Confirm link; chip click calls `onOpen`; unassigned select calls `onAssign`; shooters menu calls `onRemove`; cameras select calls the mount save (mock `api`).
- [ ] Commit `feat(ui): coverage matrix, footage cards and side panels`.

---

### Task 3: `ClipSheet` and `AddShooterSheet`

**Files:** Create `components/footage/ClipSheet.tsx` (from `pages/ingest/ClipDetail.tsx`, then delete the old file), `components/footage/AddShooterSheet.tsx` (from `pages/Shooters.tsx`'s add section), `components/ui/Sheet.tsx` (a right-side panel: `fixed inset-y-0 right-0 z-chrome w-full max-w-[440px] bg-surface border-l border-rule-strong shadow-lg`, focus trap not required, Escape closes, a backdrop `bg-bg/60` that closes; below md it is a bottom sheet `inset-x-0 bottom-0 max-h-[85vh] rounded-t-[10px]`). Tests for both sheets.

`ClipSheet` props: `{ slug; clip: ClipItem | null; mode: "edit" | "assign"; assignStage?: number; allStages; shooters; rawVideos; mediaOnDesktop; busy; onMove; onRemove; onMoveShooter; onError; onReload; onClose }`. Renders the `Sheet` with: header (`font-mono text-md` file name, role `Chip` with tick, close `Button size="icon" variant="ghost"`); body: the `<video>` (same `api.videoStreamUrl(slug, path, "proxy")` + proxy / desktop placeholders as today), a `numeral text-sm text-muted` meta line (shooter, recorded, duration, camera make model · mount), then label/control rows (`grid grid-cols-[96px_1fr] items-center gap-3 text-md`): `Stage` native select (`NN · name` plus the stage's time and rounds in the option text when known -- the reference drawer's job), `Role` = `RoleToggles` (restyled: neutral segmented `Button size="sm"` group with `aria-pressed`), `Beep` = beep chip + `Detect beep` / `Re-detect` `Button size="sm"` (the existing `detectBeep`), `Coverage` = `CoverageSelect` (restyled chips) only when the raw manifest says this file spans stages (same condition as today), `Shooter` native select (moves the file; today's `ShooterPickerPopover` becomes this select -- delete the popover). Footer: `Remove video` (`Button variant="destructive"`, confirm), spacer, `Open in Audit` (`Button asChild Link`). In `assign` mode with no clip: the body is the unassigned list to pick from (`Pick a video for stage NN`), each a button that assigns and switches to edit mode.

`AddShooterSheet` props: `{ open; onClose; project; shooters; editDenied; onAdded() }`. Body: when scoreboard-linked, the roster filter input + list (`pickCompetitor` from Shooters.tsx, claimed ids excluded) with a `Add by name instead` link; else the manual name form (`api.addMatchShooter`) and, when not linked, the `ConnectMatchButton` / `ConnectMatchDialog` pair. Keep the exact API calls and the `capabilities` gating from `Shooters.capabilities.test.tsx` (port those cases).

- [ ] Commit `feat(ui): clip sheet and add-shooter sheet`.

---

### Task 4: `pages/Ingest.tsx` render, routes, nav

**Files:** Modify `pages/Ingest.tsx` (render only; keep the data core), `App.tsx`, `components/match/navItems.tsx` (+ test), `components/match/MatchShell.tsx` (`viewLabelForPath` `/shooters` -> `Footage`), `components/match/DefaultShooterRedirect.tsx` (no-shooter fallback -> `ingest` instead of `shooters`? read it: it already falls back to `shooters`; change that fallback to the match overview `""`), `navItems` slug-less targets `${base}/shooters?pick=x` -> `${base}/ingest` (the redirect picks the default shooter); delete `pages/Shooters.tsx`, `pages/ingest/ReviewLayout*`, `ClipList.tsx`, `StageReferenceDrawer.tsx`, `CameraCard.tsx`, `ShooterPickerPopover.tsx`, `TipCards` / `EmptyState` / `AddFootageCard` / `RecentSources` in Ingest.

- Route: `ingest/:slug` moves under `<Route element={<MatchShell />}>` with `ShooterScopedRoute`; no `DesktopGate` (the page has a phone render). `shooters` route -> `<Navigate to=".." />`-style redirect to `ingest` (a tiny `ShootersRedirect` reusing `DefaultShooterRedirect base="ingest"`).
- Page reads `useOutletContext<MatchShellOutletContext>()` for `shooters`, `capabilities`, `jobs`, `refresh`; fetches every shooter's project (`Promise.all(api.getProject)`) into `projects`, re-fetching on `refresh` and after every write (the existing `handleSaved` path); the `:slug` project is the write target and the drop zone's target.
- Render: `<div className="px-4 py-4 md:px-7 md:py-5">` `PageHeader title="Footage" sub={counts line}` with `actions`: `Find moved videos` (local, `RelinkDialog`), `Add shooter` (opens `AddShooterSheet`), `Add footage` (primary; hosted -> `HostedUploadModal`, local -> `FolderPicker`; `editDenied` -> the read-only note instead). `children`: shooter chips when > 1 (same block as Overview / Splits; the chip is a `Link` to `href("ingest", slug)` so it changes the target). Then the drop zone row (`border border-dashed border-rule-strong rounded-[10px] px-4 py-3 flex items-center gap-3 text-md`): upload glyph, `Drop video files here, or browse.`, muted `Files are matched to stages by recording time` (+ ` and added to {name}` on multi), right: local -> the storage chip toggle (`Chip` `Link in place` / `Copy files`, `aria-pressed`) and `Browse` button; hosted -> `Browse`; hosted + `editDenied` -> the read-only copy (keeps `Ingest.emptyState` cases). The window-level drop handling stays exactly as it is (`useWindowFileDrag`, the takeover overlay restyled: `rounded-[10px] border border-dashed border-led bg-surface`, no glow). Then the `lg:grid-cols-[minmax(0,1fr)_320px]` grid: `CoverageMatrix` (or `FootageCards` on mobile) | the three panels. `IngestMoveBanner` (restyled, drop its grandfather line) and the `moveBlocked` note render above the matrix as today. `ClipSheet` and `AddShooterSheet` at the end.
- Empty match (no videos anywhere): the same page; the matrix shows every stage as `no footage`; nothing else changes (spec: no separate empty state).
- [ ] Update the four Ingest tests + `App.routes` tests; delete `Shooters.capabilities.test.tsx` after porting its capability cases into `AddShooterSheet.test.tsx` / `ShootersPanel` cases; run everything; commit `feat(ui): Footage page inside the match shell; Shooters folds in`.

---

### Task 5: Checkpoint, screenshots, PR

- [ ] Gates: `corepack pnpm typecheck && lint && test && build`.
- [ ] Seed with `--media` (the seeder's shooter has no unassigned videos: add one to the seeder -- `project.unassigned_videos = [StageVideo(path=Path("raw/demo-source.mp4"), role="primary")]` is wrong (same path); instead register a second copy `raw/demo-extra.mp4` (copy the rendered clip) as an unassigned video). Screenshot 1440: Footage single shooter with the sheet closed and open (click a chip), and 390. Compare with the mockup; fix; add the Built section to the artifact; PR `feat(ui): Footage page with the coverage matrix and the clip sheet (UX PR 6)`; merge on green; update CLAUDE.md's no-go list.
