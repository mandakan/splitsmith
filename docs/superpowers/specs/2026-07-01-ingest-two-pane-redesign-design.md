# Ingest page two-pane master-detail redesign

Date: 2026-07-01
Status: design approved, pending spec review
Issue: (none yet -- personal-tool UX gap reported during local app use)

## Problem

The "Add Footage" ingest page (`/match/:matchId/ingest/:slug`) is hard to
work with. Four concrete complaints, all rooted in the current layout:

1. The stage reference table (`StageReference.tsx`) is `sticky top-0` with
   its own inner `max-h-[40vh]` scroll region, so it pins itself to the top
   and eats roughly half the viewport. Pinning it there is a no-go.
2. The per-clip player is rendered *below* the grid row
   (`Ingest.tsx:1328`), while the stage `<select>` lives *in* the row. To
   preview a clip you scroll down to the play button, then scroll back up to
   reach the dropdown. The two controls are always separated by the full
   video height.
3. Spacebar does not play/pause the ingest player, even though the rest of
   the app supports it. The ingest `<video>` uses raw native controls and
   never wired in the shared `useSpacePlayPause` hook.
4. With several rows each able to expand their own inline player, it is
   ambiguous which stage dropdown belongs to which open video.

## Confirmed workflow

The user works this page **one clip at a time**: open a clip, scrub to read
the range sign, assign its stage, move to the next. Only one player is
active at a time. This is the signal that drives the whole redesign -- one
player, one picker, ever.

## Goal

Restructure the ingest review screen into a two-pane master-detail layout
with a collapsible stage-reference drawer, so the four pain points above
disappear. Frontend only. No backend, API, or import-path change.

## Scope

Frontend only (`src/splitsmith/ui_static/src/`). No backend change, no API
change, no new runtime or dev dependencies. Selection is pure client state;
drawer collapse persists in `localStorage`.

## Layout

Three regions inside `ReviewState`, replacing today's single stacked card:

```
breadcrumb / ADD FOOTAGE
[ toolbar: "N videos - M cameras" - beep-review CTA - camera mounts - Add more - Confirm ]
+----------------+-----------------------------+-----------+
| CLIP LIST      |  DETAIL (persistent)        | STAGE REF |
| (work queue)   |  player + assignment bar    |  drawer   |
+----------------+-----------------------------+----[handle]
```

- The center detail pane flexes. The stage-reference drawer is a fixed
  ~280px right column that collapses to a ~24px handle tab (the hide-handle
  the user asked for). Collapsing gives the player full width.
- The drawer **pushes**, it never overlays. The point of the drawer is
  reading stage names while scrubbing the player, so it must sit beside the
  player, not cover it.
- Page-level chrome -- drop summary ("N videos detected - M cameras"),
  beep-review CTA, camera-mount editing (from #511), Add-more, Confirm --
  moves into a thin toolbar under the breadcrumb, out of the vertical flow.

## Left -- clip list (work queue)

- Section `TO ASSIGN (n)` first, then per-stage groups (collapsible).
  Assigning a clip drops it out of the queue into its stage group. Top line
  shows progress, e.g. `12 assigned / 4 left`.
- Each row: select/play affordance, filename, timestamp, camera tag.
  Assigned rows also show a role badge (`[P]` / `[S]` / `ign`).
- The selected row gets the LED accent highlight. Because there is only ever
  one selection, this is what removes the dropdown-to-video ambiguity.

## Center -- detail pane

- Shared `<video>` on top. Directly beneath it, with no scroll gap, the
  assignment bar: stage `<select>` (`-- Unassigned --` + all stages), role
  toggles (Primary / Secondary / Ignore), beep-detect button + status,
  remove, and (multi-shooter only) move-to-shooter. Caption "Streaming
  source - scrub to identify the stage".
- Keyboard: wire the existing `useSpacePlayPause` hook (`src/lib/keyboard.ts`)
  into this player, gated (via its `enabled` arg) on a clip being selected.
  Add `Up`/`Down` (or `j`/`k`) to move selection through the list, and
  auto-advance to the next unassigned clip after an assignment. That is the
  one-at-a-time rhythm.
- Empty state when nothing is selected: "Select a clip to preview and
  assign" plus the key hints.

## Right -- stage reference drawer

- Same data as today's `StageReference` table: stage number, name, rounds
  label, targets label, video count, status dot (`deriveStageStatus` +
  `StageDot`). Refactored into a drawer shell with the hide-handle tab.
- Collapse state persists in `localStorage` (reuse the existing
  `splitsmith:ingest:stage-reference:collapsed` key). Keep the current
  auto-collapse-when-all-stages-have-footage behavior.
- Clicking a stage row assigns the currently selected clip to that stage --
  faster than the dropdown, and it turns "match the range sign to the list"
  into a single click.

## Component structure

`Ingest.tsx` is 1624 lines; the redesign is the moment to split it. Break
`ReviewState` into:

- `ClipList.tsx` -- work-queue + per-stage groups, selection affordance.
- `ClipDetail.tsx` -- player + assignment bar; reuses a shared video player
  wired to `useSpacePlayPause`.
- `StageReferenceDrawer.tsx` -- from the current
  `components/ingest/StageReference.tsx`: a drawer shell reusing the table
  body.

`selectedPath` and the keyboard-navigation handler are lifted to the
`ReviewState` container. `CameraCard`, `RoleToggles`,
`ShooterPickerPopover`, and the beep-detect bits are relocated into the new
panes, not rewritten.

## Data flow

No API changes. The same methods back the same actions:
`api.moveAssignment`, `api.removeVideo`, `api.moveShooter`,
`api.detectBeepForVideo`, `api.bulkSetCamera`, `api.getCalibratedCameraModels`,
`api.shooterVideoStreamUrl`. Selection (`selectedPath`) is pure client
state. Drawer collapse is `localStorage` only.

## Responsive

Desktop-first tool. Below ~1024px the drawer becomes an overlay and the list
and detail stack vertically. No further investment there unless the three-up
layout feels cramped at 1240px with the drawer collapsed.

## Styling

Reuse the existing Tailwind v4 utilities and the design tokens in
`src/styles/index.css` (`bg-surface-*`, `border-rule*`, `text-ink*`,
`text-led`, status colors `--color-live/done/beep`). The LED-red "Shot
Timer" aesthetic is unchanged; this is a layout restructure, not a visual
reskin. Verify token names against `index.css` before use (bare `var(--foo)`
refs fall back silently).

## Testing

- Component tests for: `ClipList` selection and work-queue ordering,
  keyboard navigation (space gated on selection, up/down moves selection,
  auto-advance after assign), and drawer collapse persistence.
- A manual / Playwright smoke of the full assign-a-clip flow on the running
  app before merge.

## Pain points -> fixes

1. Sticky table eating half the screen -> stage list is a right drawer that
   collapses to a handle; zero vertical cost.
2. Scroll-to-play then scroll-to-assign -> player and stage picker docked
   together, always in view.
3. Spacebar dead -> `useSpacePlayPause` wired in, gated on selection.
4. Which dropdown maps to which video -> exactly one player and one picker;
   the selected clip is highlighted in the list.

## Non-goals

- No backend, API, or import-path changes.
- No new dependencies.
- No visual reskin beyond the layout restructure.
- No auto-assignment or stage guessing (surface the data, never guess).
