# Export progress panel and the export limit

Date: 2026-10-25. Status: design, approved for planning once the user has read it.

## Why

A hosted whole-match grid takes 10 to 15 minutes at 1080p basic and longer
dressed or at 4K. During that time the Export page shows one line ("Rendering
stage 3 ... (40%)"), the grid's twelve stage jobs appear only as unrelated
"Grid stage" rows in the jobs drawer, and a stage sits without visible movement
for three to seven minutes. The user cannot tell how long is left, what the
export is made of, or whether it is stuck. And nothing stops a second export
from starting beside the first, which on hosted doubles the workers a user can
hold.

## What the user asked for

- On the Export page, one place that shows a running export: **how long is
  left**, **which parts the export is made of**, and **each part's status and
  progress**.
- It covers **every kind of export**: a single stage, the single-shooter match
  export, trims only, the highlight reel, and the compare grid.
- **Cancel** the whole export from the panel, and **retry** a part that failed.
- **Only one export at a time on hosted**, with the number set by the access
  tier and overridable per user. **Desktop is unlimited.**
- When the limit is reached: whatever is simplest. That is: the Export button
  is disabled with a note naming the running export and a link to its
  progress; the server refuses a second start too; nothing waits in line.

## Goals

1. A user who starts an export and watches the Export page knows, at every
   moment, roughly when it will finish and what it is doing.
2. Each part's progress moves while it works: no part sits still for minutes
   while it is doing work.
3. The panel survives leaving the page, a reload, and opening the page on
   another device: it finds the running export again.
4. A hosted account cannot run more exports at once than its limit allows,
   whatever tab, device or API client starts them.

## Non-goals

- A queue of exports waiting their turn.
- Changing how fast exports run (separate work, issue #1691).
- Estimates for non-export jobs (beep detection, trims made during audit,
  sync, proxies). They keep their drawer rows as they are.
- A tier admin UI. The limit is configuration plus a per-user override an
  admin sets the way tiers are set today.

## Anti-goals

- An estimate that is confidently wrong. A first estimate with no history
  says "about"; an estimate the run has outgrown says so rather than showing
  a negative or frozen "1 min left".
- A limit that a desktop user ever meets.
- A limit check in the SPA alone: the server is the authority.
- Tier names in code. The code checks a limit value, as it checks features
  today (`access.py`: "code checks features, never tier names").

## The panel

Lives on the Export page and replaces today's message line under the Export
button (`pages/Export.tsx`, the `busy && job?.message` block).

- **Header:** what is being exported (kind and target, from `lib/jobLabels`),
  the time left, and the estimated finish time ("about 9 min left, done by
  14:32").
- **One row per part**, in the order they run: name, status (waiting, running,
  done, failed, cancelled), a progress bar, and the part's own time (elapsed
  while running, taken once done).
- **Cancel** for the whole export, behind the existing confirm pattern.
- **Retry** on a failed part.
- **When done:** the panel gives way to the existing result panel
  (`ResultPanel`). A failed or partly failed export keeps its rows so the
  failure is visible.

The parts, per kind:

| Export | Parts |
|---|---|
| Compare grid, hosted | Plan; one row per stage render; the final join and upload |
| Compare grid, desktop | Each stage as rendered or reused, then the join (the existing `GridRenderStep` steps) |
| Single-shooter match export | Its phases (load, render, upload) and, where it renders stage by stage, one row per stage |
| Single stage | Its phases |
| Trims only | One row per stage's trim job |
| Highlight reel | Its phases |

The jobs drawer keeps working as today, except that an export's parts fold
into one row for the export (the grid's twelve "Grid stage" rows become one
"Compare grid" row with its overall progress).

## Progress within a part

Today a grid stage job reports nothing between its start and its end. Each
part that encodes reports progress from the encoder while it runs: ffmpeg's
`-progress` output (`out_time` against the item's known duration) is the
source, reported through the job's existing `progress`/`message` update path,
throttled so the database sees at most one update every few seconds per job.

## Estimates

- **The basis:** finished jobs already record their timings (`timings`: phases,
  `total_ms`, `queue_wait_ms`, and the worker). An estimate for a part is the
  median of recent comparable parts on the same deployment, scaled by the
  part's input: seconds of footage times the number of tiles for a grid
  stage, seconds of footage for a single-shooter render. Comparable means the
  same kind, the same canvas and the same dressing (basic or dressed).
- **With no history:** a built-in table of seconds per footage-second per
  kind, seeded from the staging benchmarks (1080p basic stage: 166 s for
  stage 1 of HFO; dressed: 326 s; 4K basic: 212 s; see #1691). Shown as
  "about".
- **While running:** a part's remaining time comes from its own progress rate
  once it has run long enough to have one, else from its estimate. The
  export's time left is the critical path: for parts that run in parallel
  (grid stages), the longest remaining one plus what follows it (the join);
  for parts in sequence, the sum. Worker start-up (about a minute on Cloud
  Run) counts while a part waits for a worker.
- **When a part outruns its estimate,** it shows "taking longer than usual"
  rather than a frozen or negative number.

## The limit

- **Where it is set:** each access tier gets a number,
  `concurrent_exports`, next to its feature set in the access registry
  (`AccessConfig` in `access.py`); the default is 1 on every tier. A user row
  gets an optional override (`users`, nullable), set by an admin. The
  effective limit is the override when set, else the tier's number.
- **What counts:** an export started from an export route (stage export,
  match export, trims only, highlight reel, compare grid) that has not
  finished, failed or been cancelled. A grid and its stage jobs are one
  export. Trims queued by the audit chain are not exports.
- **The check:** in the request that starts an export, before any job is
  queued; over the limit it answers 409 with the running export's id, kind
  and target, so the page can name it and link to its progress. The check
  and the queueing must not race (two starts in the same second on two
  devices both get through only if the limit allows two).
- **Desktop and local mode:** no limit, no check.
- **The SPA:** the Export button is disabled while the limit is reached, with
  the note "An export is running" and a link that opens that export's panel.

## An export as one thing

The panel, the drawer row, the limit and cancel all need "the export" as a
unit, and today an export is a loose set of job rows (the grid's stage jobs
are linked only through the parent's `stage_jobs` argument; trims only queues
independent jobs). The builder decides how to group them, with these
requirements:

- Every job an export route queues carries the export it belongs to, and
  the export's parts can be listed from it in one query.
- An export's status follows its parts: running while any part runs or
  waits, failed when the parent failed, done when the parent (or, for trims
  only, every part) is done.
- Cancelling the export cancels every part still waiting or running.
- Retrying a failed part queues that part again within the same export; for a
  grid, the join runs again after it, reusing every stored stage (the segment
  cache makes the rest free).

## Calls made while writing this (not the user's; check them)

- **The limit is per account, across all matches.** "One export at a time"
  read as one per user, not one per match.
- **Retry on a kind with one job** (single stage, match export, highlight
  reel) starts that export again with the same settings; only the grid and
  trims only retry a single part.
- **A YouTube upload chained after a match export** shows as the export's
  last part and counts toward the same export, not a second one.

## What is left to the builder

- The data shape of the export group (a column, a table, or the parent's
  id on every part), and its migration.
- The API shape for listing an export with its parts and estimates, and how
  the page finds "my running export" after a reload. Keep it to the batched
  request patterns in `.claude/rules/hosted.md` (no per-row awaits on the
  jobs poll).
- The phase lists for the kinds other than the grid, from what those jobs
  already do.
- The built-in estimate table's numbers beyond the benchmarks above.
- The visual design, inside the SPA's primitives and visual budget
  (`.claude/rules/ui-budget.md`), and the phone layout.

## Constraints

- Code checks a limit value, never a tier name.
- No new dependencies.
- The SPA's wire types are generated: a new or changed route updates
  `ui_static/openapi.json` and `lib/api/types.gen.ts`.
- A user-facing change adds its What's new entry.
- The Export page's existing behaviour for desktop render requests
  (`renderRequests`, the presence line) is unchanged.

## Related

- #1691 (hosted grid per stage), #1675 (the HFO bench epic).
- The dressed grid's 28-minute join (run G5, 2026-10-25) is a separate fix:
  the parts between stages should render on the stage workers too. The panel
  shows whatever parts exist; it does not depend on that fix.
