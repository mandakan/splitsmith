---
paths:
  - "src/splitsmith/ui_static/**"
---

# SPA: the visual budget and where things go

Spec `docs/superpowers/specs/2026-09-13-ux-restructure-and-visual-budget-design.md`.
Every page is on the primitives; any new screen follows the budget. ESLint
(`no-restricted-syntax` in `ui_static/eslint.config.js`) enforces the mechanical
half.

## Primitives (`components/ui`), nothing else

- `PageHeader` is the only way to get a page or stage title.
- `Label` is the only tracked-caps style: 1-3 words, never a sentence.
- `Stat` / `StatStrip`, `Table` / `Th` / `Td` / `Tr`, `PipelineDots`,
  `ProgressStrip`, `Field` rows on every settings surface, `Segmented` for a
  closed choice, `Sheet` (side panel) and `Menu` (popover; portals to
  `document.body` so a row menu is never clipped by a `Table`, and flips upward
  when there is no room below).
- `Chip`: neutral pill with a coloured tick, never a coloured fill.
- `Button`: `primary` is Antonio led-fill and there is **one per view**;
  `default` neutral; `destructive` is an outline, never a red fill.
- The `numeral` utility for every count, time and split.
- Sizes come from the theme scale (`text-sm` 12 px, `text-md` 14 px,
  `text-base` 15 px). Arbitrary `text-[...]` and `tracking-[...]` are lint
  errors outside `components/ui`.
- `Kicker`, `DisplayHeading`, `Readout` and `TickStrip` are deprecated: add no
  consumers.

## Rules the lint cannot check

- Red marks the brand, the one primary action, the current position and focus,
  nothing else. Stage state is amber / green / hollow; errors are
  `--color-destructive` as outline + text.
- Glow only on live state.
- Leading zeros only on stage and shot ordinals (`Stage 03`), never on counts
  (`4 / 12`).
- One card level per view; lists are hairline rows, not stacked cards.
- No flavour copy, no issue numbers in the UI.
- Splits rank at least equal with scorecard figures in any summary.
- Every page's data derivation lives in a pure `lib/*.ts` module with tests;
  the page maps results to primitives.

## Nav

Rows live in `components/match/navItems.tsx`, grouped by phase (Prepare /
Review / Analyse / Deliver). A new row needs a group. A queue about state
belongs on the Overview rows or the progress strip, never a row of its own.
Triage and Jobs have no pages (`/triage`, `/jobs` redirect to Overview).

## Page seams (build on these, not around them)

- **Overview** (`pages/Home.tsx`, `components/overview/*`, `lib/overview.ts`):
  a new per-stage state or action is a `rowAction` case. Rows carry accept /
  audit and the flag count; the progress strip and its drawer are Jobs.
- **Splits and the stage page** (`pages/Results.tsx`, `pages/ResultsStage.tsx`,
  `lib/splitsTable.ts`, `components/results/*`,
  `components/share/ShareShell.tsx`). Per-stage split figures for any
  share-surface consumer come from `stages[].figures` on the project payload,
  never from triage (owner-only). Other cameras: see `pip.md`.
- **Audit**: see `audit-ui.md`.
- **Footage** (`pages/Ingest.tsx`, `components/footage/*`, `lib/footage.ts`):
  locally, Add footage is the footage sort (`pages/FootageSort.tsx`,
  `ui/footage_sort_api.py`) whenever the match has scorecards; the
  per-shooter import is the fallback. The coverage matrix derives from
  `buildFootageRows`. A per-video control goes on `ClipSheet`, a per-stage
  action on `CoverageMatrix`'s row menu, shooter management on `ShootersPanel`
  / `AddShooterSheet`.
- **Coach** (`pages/Coach.tsx`, `components/coach/*`, `lib/timeBudget.ts`): the
  budget sums each shot's split into its stored interval class; segments equal
  the stage time to 1 ms (pinned by fixture). Budget hues are the chip ticks
  (`BUDGET_TICK`); an outlier is an interval over twice its type's match median.
  See `breakdown-ui.md` for what is Coach's and what is Breakdown's.
- **Compare** (`pages/Compare.tsx`, `pages/compare/*`): the nav row appears
  only on multi-shooter matches (`matchNavItems({ multiShooter })`). Compare
  keeps its grid (no PiP).
- **Matches** (`pages/Pick.tsx`, `lib/matches.ts`): the Continue card names
  `RecentProjectDetail.next_step`, which both recent-project enrichers in
  `ui/server.py` derive through `_next_step_from_statuses`. Deleting a match is
  on the row menu (every row, including "Folder not found"); hosted and desktop
  deletes are separate actions, worded by `lib/matchDelete.ts`.
- **Export** (`pages/Export.tsx`, `components/export/*`, `lib/exportPlan.ts`):
  the blocker ladder in `stageBlock` is the one place a stage's "why not" and
  its fix are worded, which keeps hosted copy free of drives. The bundle gate is
  `ready_to_export_bare` (reviewed beep + stage time), not `ready_to_export`: a
  bare stage renders with its chapter and cards, losing only shot-dependent
  extras (the "No splits" chip, the rail's "Splits" line, `bareHint`); its
  summary hold is the scoring-only card. The compare grid is the page's third
  mode (`pages/matchExportModel.ts` owns the payload). Presets and the Look
  gallery: root CLAUDE.md "Export presets".
- **Account** (`pages/Account.tsx`, `components/account/*`): `Field` rows.

## Grandfathered (restyle onto the primitives whenever you touch them)

`components/results/ResultsPlayer.tsx`, `ReclassifySheet.tsx`,
`ShareDialog.tsx`, `components/comments/CommentPanel.tsx`,
`components/BeepSection.tsx`, `components/Waveform.tsx`,
`components/MarkerLayer.tsx`, `components/VideoPanel.tsx`,
`components/audit/MultiCamColumn.tsx`, `CamGridModal.tsx`, `CamSyncPill.tsx`,
`AnomalyPins.tsx`, `components/FolderPicker.tsx`, `HostedUploadModal.tsx`,
`RelinkDialog.tsx`, `UploadDock.tsx`, `StageTimeSection.tsx`,
`components/scoreboard/ConnectMatchDialog.tsx`.
