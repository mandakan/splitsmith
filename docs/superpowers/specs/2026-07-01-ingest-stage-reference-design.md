# In-app stage reference for video ingest

Date: 2026-07-01
Status: design approved, pending spec review
Issue: (none yet -- personal-tool UX gap reported during local app use)

## Problem

When assigning imported videos to stages on the Ingest page, the stage
picker shows only `Stage NN -- {stage_name}`. That is not enough to tell
which video belongs to which stage, so the user leaves the app and reads
the stage list on SSI Scoreboard by hand. The redesign (commit `b3531b5`)
did not cause the picker to lose data -- the richer per-stage metadata was
never surfaced in this picker in any version. The metadata the user relies
on (round count, paper/steel target layout, and shooting order) already
exists in the project but is thrown away by the frontend.

## What the user relies on

Confirmed by the user, in priority order:

1. Round count and target layout (paper vs steel) to recognize a stage.
2. Shooting order -- videos were captured in squad-rotation order, so
   chronological order lines up with the stage sequence.

The user does NOT rely on stage time/duration or the written procedure for
this task. The user explicitly chose "surface the data, never guess" over
any auto-suggest that could mislead.

## Goal

Bring the SSI stage list into the Ingest page so the user never has to
leave the app to assign videos. Surface round/target metadata and put
videos in capture-time order. No guessing, no auto-assignment.

## Scope

Frontend only (`src/splitsmith/ui_static/src/`). No backend change, no
import-path change, no new runtime or dev dependencies.

The round/target data is already on the wire: the project API returns
`ui/project.py::StageEntry`, which carries `stage_rounds`
(`expected` / `paper_targets` / `steel_targets`, from `config.py`). The
frontend `StageEntry` type in `lib/api.ts` simply does not declare it, so
it is dropped before render.

## Design

### 1. Type plumbing

Add to `StageEntry` in `src/splitsmith/ui_static/src/lib/api.ts` (near
line 178):

```ts
export interface StageRounds {
  expected: number | null;
  paper_targets: number | null;
  steel_targets: number | null;
}

// on StageEntry:
stage_rounds: StageRounds | null;
```

No payload change -- this only stops the UI from discarding a field the
server already sends. Verify one live `/api/match/{slug}` response actually
contains `stage_rounds` before relying on it.

### 2. Stage reference panel

New component `StageReference` rendered on the Ingest page, pinned (sticky)
directly under the page header, above the video lists.

Behavior:

- Collapsible. State persisted in `localStorage` (existing pattern in the
  codebase, e.g. `MatchShell`/`AppShell`). Default open when at least one
  video is unassigned; default collapsed once everything is assigned.
- When expanded: a dense, read-only, monospace grid with one row per stage.
  Max height ~40vh with internal vertical scroll so a 20-stage match never
  dominates the viewport.
- When collapsed: a single coverage readout line, e.g.
  `STAGE COVERAGE  8 / 12 have footage  -  4 remaining`.

Columns (instrument-panel styling, `tabular-nums` for all counts):

```
STAGE  NAME              ROUNDS  TARGETS   VIDEOS  STATUS
03     El Presidente     12rd    6P 2S       1     [ready]
04     Accelerator       24rd    12P 4S      0     [todo]
05     Standards         18rd    9P          --    [todo]
```

- `ROUNDS` = `stage_rounds.expected` rendered as `{n}rd`; `--` when null.
- `TARGETS` = `{paper}P {steel}S`, omitting a side whose count is null;
  `--` when both null.
- `VIDEOS` = count of assigned videos (`stage.videos.length`).
- `STATUS` = existing `StatusPill` (`components/ui/StatusPill.tsx`),
  reusing whatever `StageStatus -> tone` mapping `StageBlock` already uses.
  Do not introduce a second mapping.

Empty-data state: if NO stage in the match has any `stage_rounds`, show a
subtle inline hint that round/target data is unavailable and that
re-syncing from the scoreboard can backfill it (the backend already has
`MatchProject.merge_stage_rounds`). The backfill action itself is out of
scope for this change.

Optional, low-risk nicety: clicking a stage row scrolls that stage's
`StageBlock` into view. Read-only, no assignment side effect. Include only
if it fits cleanly.

### 3. Capture-time ordering

Sort the **unassigned** video list by `video.match_timestamp` ascending.
Videos without a timestamp sink to the bottom via a stable sort, preserving
their existing relative order. The per-row capture timestamp is already
rendered (`Ingest.tsx` `recordedAt`, ~line 963/1027); no change there.

This is the "surface, no guess" ordering: videos appear in shooting order
next to the reference panel so the user maps them top-down themselves.

### 4. Picker unchanged

The `<select>` at `Ingest.tsx` ~line 1038 stays as-is. The stage name is
the identifier and the column is narrow; padding a round-count hint in
would truncate the name. The reference panel is the single source of
metadata; the picker stays a clean control.

## Accessibility (WCAG 2.2 AA)

- Status conveyed by `StatusPill`, which already pairs color with a text
  label and a shape cue -- color is never the sole carrier.
- Collapse toggle: real `<button>` with `aria-expanded`, visible focus ring
  (existing `focus:` token pattern), keyboard operable.
- `rem`-based sizing, `tabular-nums` for numeric alignment.
- Reduced motion: the collapse transition respects the global
  `prefers-reduced-motion` block; no new unguarded animation.
- Panel exposed with appropriate table/list semantics for screen readers.

## Out of scope (YAGNI)

- No auto-suggest / squad-rotation computation (user chose "no guess").
- `course_display` (Short/Med/Long), `procedure`, `firearm_condition`,
  `max_points` stay dropped on scoreboard import -- the user does not rely
  on them and surfacing them needs a backend/import change.
- No scoreboard re-sync/backfill button (only the empty-state hint).
- No change to the assigned-per-stage `StageBlock` rows.

## Verification

`ui_static` has no JS unit-test framework (scripts: `dev`, `build`,
`typecheck`, `lint`). Adding one is a new dependency and out of scope
unless the user asks. Verification for this change:

- `pnpm typecheck` (`tsc -b --noEmit`) clean.
- `pnpm lint` (eslint) clean.
- Browser verification via Playwright MCP against a real match created
  from SSI Scoreboard: panel renders round/target counts, `--` for nulls,
  correct assigned counts and status pills; unassigned list is in
  capture-time order; collapse/expand persists across reload; empty-data
  hint shows when no stage has round data.

## Files touched

- `src/splitsmith/ui_static/src/lib/api.ts` -- `StageEntry` type +
  `StageRounds`.
- `src/splitsmith/ui_static/src/pages/Ingest.tsx` -- render
  `StageReference`, sort unassigned list.
- New: `src/splitsmith/ui_static/src/components/ingest/StageReference.tsx`
  (final path to match existing component-folder convention).
