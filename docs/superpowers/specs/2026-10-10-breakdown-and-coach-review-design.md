# Breakdown and the Coach review page

Status: built (epic #1370; PRs for #1371-#1373 and #1392 for #1374-#1377). Mockup:
https://art.urdr.dev/2ihtfcmah7cc (approved 2026-10-10).

## Why

Placing regions on the old Coach page meant scrolling between the video, the timeline band and the
region card for every edit: the page did two jobs, editing and reviewing, and fitted neither. Coach
is now split by job:

- **Breakdown** edits: regions (movement, reload, activation) and each shot's interval class, in a
  fixed workspace that never scrolls.
- **Coach** reviews: the stage as it was, with figures and notes, in a clean read-only view.

## Decisions (owner, 2026-10-10)

1. **Split by intent.** Editing and reviewing are separate pages, side by side in the nav's
   Analyse group (Coach, then Breakdown).
2. **Breakdown is optional.** It is not an Audit step and nothing counts it: Audit stays the only
   required path to splits and exports, and no pipeline step, readiness check or export gate reads
   regions. Its nav row shows a neutral count of confirmed regions when there are any, never a
   pending badge.
3. **Coach edits metadata only.** Per-shot notes and improvement flags, and a stage note. Regions,
   interval classes and Reclassify live on Breakdown; Coach links there ("Adjust in Breakdown").
4. **The review page is for the owner first.** Sharing it is later (#1379).
5. **The name is Breakdown.**

## Breakdown

`pages/Breakdown.tsx`, data through `lib/useStageWorkspace.ts`, rules in `lib/breakdown.ts`.

- **Layout:** the video and the inspector side by side on top, the shared timeline band pinned at
  the bottom (the NLE arrangement). The page itself never scrolls; the band scrolls its rows under a
  fixed header and ruler (`rowsHeight`).
- **Inspector** (`BreakdownInspector`): the selected region's card or the current shot's interval
  card, above the shot list. Each half folds (to a rail, to its header); the folds are per browser
  (`lib/breakdownPrefs`).
- **Viewer and band split** (`BandSplitter`, `lib/useBandSplit`): a draggable divider sets the
  band's height, kept per browser.
- **Dense mode** below a 900 px tall window (`lib/useShortViewport`): the transport moves into the
  band header and the region card goes compact, so a laptop browser (about 790 px of viewport on a
  900 px screen) still shows video, card and band at once.
- Desktop only (`DesktopGate`), like Coach.

## Coach

`pages/Coach.tsx`, figures from `lib/coachReview.reviewFigures`.

- **Stage strip** (`StageStrip`, `lib/stageStrip`): a read-only line of the stage, shots as ticks
  in their budget hue, movement and reloads as bars, the playhead and the current shot. Tap to
  jump; keyboard accessible. Confirmed regions only, like every output.
- **Figures and time budget:** on the move, exposed reload, the budget by interval class.
- **Stage note** (`StageNoteCard`) and the shot list (`ReviewShotList`) with notes and flags, each
  saved after a pause and on blur (`lib/useNoteAutosave`).

### The stage note

`stage_note` on the stage audit doc, written by `PATCH .../stages/{n}/stage-note` under the audit
lock with the revision check. It is a review route, so a hosted mirror can write it, and sync merges
it three-way like a shot's note. A clear is an explicit `null`; an absent key means the writer
never knew the field, so an older desktop cannot erase a note it never saw (the sync PUT keeps it
and answers `kept_fields`; a one-time re-pull upgrades existing sync state). Stripped on share
reads; audit events record its length and a short hash, never the text. CLAUDE.md, "Stage events",
has the full rule.

## Deep links

"Adjust in Breakdown" and "Review in Coach" keep the stage, the time from the beep (`?t=`) and the
shot (`&shot=`); Breakdown also reads `&region=` (`lib/stageLink`). A stale or malformed link is
ignored.

## Later

- #1379: share links reuse the Coach review page, editing off, comments on; the owner decides what
  a shooter sees of the notes.
