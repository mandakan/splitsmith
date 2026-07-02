# Aggregate Match Overview -- design

## Problem

The match Overview (the `/` route under `MatchShell`, with no shooter slug in
the URL) is silently scoped to a single shooter and picks the wrong one. In
`MatchShell.tsx` the base project for the slug-less route is loaded from
`r.shooters[0]` -- the alphabetically-first shooter. For a match where Anton
Johansson sorts first and has no footage, the entire Overview renders his
per-shooter empty state:

- Hero shows "AWAITING FOOTAGE" / "ADD FOOTAGE TO GET STARTED" even though
  another shooter (Mathias Axell) has 9 videos and 2 audited stages.
- The subtitle prints `project.competitor_name` -- "ANTON JOHANSSON" -- a
  shooter name the user cannot change from this page (the header pills link
  to *other* pages, they do not re-scope the Overview).
- The stage tiles all show "AWAITING / READY TO RECORD" placeholders.
- The left sidebar stage list is dimmed/disabled ("stages wake up once a
  shooter has videos assigned").

The tell that this is a bug, not intent: the codebase already has one rule for
"which shooter when the URL did not name one" -- `pickDefaultShooterSlug`
(prefers the first shooter with footage). `MatchShell` uses it for the nav
links but ignores it for the actual project load.

The Overview visually promises a match-level dashboard. The fix is to make it
a true aggregate across all shooters in the match, not a per-shooter view.

## Decisions (from brainstorming)

1. The Overview becomes an aggregate match dashboard, not a per-shooter view.
2. Each stage tile shows per-shooter status chips (one chip per shooter,
   colored by that shooter's status for the stage) plus a "K of N audited"
   count.
3. The hero becomes a match-progress summary (no single-shooter "resume"
   button).
4. Tile body is inert; only the per-shooter chips are clickable (each deep-
   links to that shooter's audit for that stage).
5. The existing "Shooters" cards and "Get going" help sections stay for now;
   revisit visually in review.

## Data layer

Per-stage, per-shooter status is required to render the chips and to roll up
match totals. The Overview already fetches the roster via
`listMatchShooters()` (`GET /api/match/shooters`), and the backend's
`_classify_shooter` already loops `legacy.stages` (that loop is how
`stages_audited` is computed, via `stage_audit_status`, the status SSOT). So
the status is essentially free to add to the payload the Overview already
loads.

**Approach: extend `ShooterListEntry`.** Add a compact per-stage status list:

```
class StageStatusEntry(BaseModel):
    stage_number: int
    status: StageStatus

class ShooterListEntry(BaseModel):
    ...
    stage_statuses: list[StageStatusEntry]
```

Fold the per-stage `stage_audit_status(s, audit_dir)` call into the existing
`_classify_shooter` loop (the same computation `audited_count` already runs;
compute the count and the list in one pass so status is evaluated once per
stage). Mirror the TS interface in `api.ts`. No new endpoint and no extra
round trip.

Rejected alternative: client-side fan-out that fetches every shooter's full
project. It ships N heavy `MatchProject` payloads instead of one small matrix,
and duplicates status logic the backend owns.

The canonical match stage list (numbers + names) and match-level metadata
(name, date, scoreboard ids) still come from the base `project`, which is
identical across shooters in a match. Pivot is by `stage_number`.

## Root-cause fix (MatchShell)

Change the slug-less base-project load in `MatchShell.tsx` from
`r.shooters[0].slug` to `pickDefaultShooterSlug(r.shooters)` -- the footage-
bearing shooter, the same rule the nav links already use. This fixes the
sidebar stage list and the match metadata source on the Overview. The base
project is still needed for match-level fields and the canonical stage list.

## Overview (Home.tsx) aggregate model

Pivot `shooters[].stage_statuses` into a per-stage matrix keyed by
`stage_number`, using the base `project.stages` for stage order and names:

```
StageMatrixRow = {
  stage_number: number
  stage_name: string
  cells: { shooter: ShooterListEntry; status: StageStatus; tone: StageStatusTone }[]
  rollupTone: StageStatusTone
  auditedCount: number   // cells whose status === "audited"
}
```

Rollup tone per stage:
- `done` when every cell is terminal (audited or skipped).
- `in_progress` when at least one cell is in_progress / ready / partial.
- `todo` when every cell is todo (no footage anywhere on that stage).

Match totals:
- `totalShooterStages = shooters.length * stageCount`
- `auditedShooterStages = count of audited cells across all rows`
- `auditedPct = round(auditedShooterStages / totalShooterStages * 100)`
- `stagesFullyDone` = rows where every cell is terminal
- `stagesInProgress` = rows with at least one active cell but not fully done
- `stagesUntouched` = rows where every cell is todo
- `hasAnyFootage = shooters.some(s => s.video_count > 0)`

## UI regions

1. **Subtitle** -- drop `project.competitor_name`. Keep the date and the
   "View on scoreboard" link (match-level fields).
2. **Hero** -- replace the single-shooter resume band with a match-progress
   summary: kicker "Match Overview", a line "N of M shooter-stages audited
   (P%)", a progress bar, and "X fully done / Y in progress / Z untouched".
   No Resume button.
3. **Empty state** -- gate on `!hasAnyFootage` instead of a single shooter's
   stages. Reuse the existing `EmptyVariant` unchanged.
4. **Stage grid** -- each tile: stage number + name, a row of per-shooter
   chips (initials, colored by `tone`), and "K of N audited". Tile tone is
   `rollupTone`. Tile body is inert; each chip is a link to
   `href("audit", shooter.slug, String(stage_number))`.

The "Shooters" cards and (empty-state) "Get going" sections are unchanged.

## Scope boundaries

- The persistent left sidebar stays per-shooter; only the shooter it scopes
  to changes (footage-bearing via `pickDefaultShooterSlug`). On audit/coach
  pages per-shooter scoping is correct and is out of scope here.
- Audit / coach / export pages are untouched.
- Legacy single-shooter projects (empty roster from `/api/match/shooters`)
  keep the existing single-card fallback; the aggregate view applies only
  when `shooters.length > 0`.

## Testing

- Backend: fixture test that `_classify_shooter` (or the `/api/match/shooters`
  handler) emits `stage_statuses` with the correct status per stage, matching
  `stage_audit_status`, for a shooter with a mix of audited / ready / todo
  stages.
- Frontend: `ui_static` has no test runner, so verification is
  typecheck + build + scoped eslint. Structure the matrix pivot and rollup as
  pure exported functions (e.g. in a `lib/stageMatrix.ts`) so the logic is
  isolated and reviewable even without an executable test; keep the all-todo
  (empty) and all-audited (complete) edge cases explicit in the code and
  exercise the page manually via the running app before declaring done.
