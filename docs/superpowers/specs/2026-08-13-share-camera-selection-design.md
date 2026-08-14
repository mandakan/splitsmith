# Share camera selection - design

Date: 2026-08-13
Status: approved (approach A)

## Goal

Viewers of shared matches can pick which camera to watch when a stage run has
more than one. Today the coach payload already carries every camera and the
share whitelist already admits every camera's stream; the gap is UI only -
`ResultsStage.tsx` hard-selects the `role === "primary"` entry and renders a
single player.

Surfaces in scope:

1. Results stage viewer (`pages/ResultsStage.tsx`) - both owner
   (`/results/:slug/:stage`) and share (`/share/:token/results/:slug/:stage`)
   routes, since they mount the same component.
2. Compare view (`pages/Compare.tsx`) - per-shooter camera choice on tiles.
3. Moment deep links - a shared link can open on a chosen camera.

## Approach

Approach A: a new read-only presentational picker component consumed by the
share/results surfaces. The owner Audit picker (`MultiCamColumn` /
`CamGridModal`) stays untouched - it is coupled to owner write paths
(promote-to-primary, beep sync) and the heavier `StageVideo` type, so
generalizing it was rejected in favor of a small component over the lighter
`CoachVideoEntry` shape.

## Components

### CamPicker (new, `components/results/CamPicker.tsx`)

Presentational only. Props:

- `entries: CoachVideoEntry[]` (or the minimal subset: `path`, `role`,
  `beep_in_clip`)
- `activeIndex: number`
- `onSelect(index: number): void`
- `srcFor(entry): string` - caller builds stream URLs so the component stays
  route-agnostic (owner vs share scoping lives in `lib/api.ts` already).

Rendering: a horizontal strip of click-to-focus tiles, one per camera, shown
only when `entries.length > 1`. Each tile is a `<video preload="metadata">`
paused at its `beep_in_clip` for a representative frame, with a text label
("Primary", "Cam 2", ...). Only the focus slot (the main player) ever plays -
same rule as the owner picker (PR #803 decision). No grid modal on the share
surface; the strip covers 2-4 cameras, which is the realistic range.

Accessibility: tiles are real `<button>`s with visible focus rings; the active
tile is marked by `aria-pressed` plus a border AND a label change, never color
alone; labels read "Camera 2 of 3".

### ResultsStage changes

- Replace the hard `primary` pick with `activeCamIndex` state. Ordering is the
  payload order: primary first, then secondaries by `added_at` (built by
  `_coach_video_entries`, `server.py:10962`).
- Default index: from the `v` URL param if valid, else 0. Out-of-range or
  malformed `v` falls back to 0 silently.
- Fallback fix: when no `role === "primary"` entry exists but other cameras
  do, index 0 of whatever is present plays instead of today's "No primary
  video for this stage." dead end.
- Switching cameras preserves the viewer's place in the run by mapping through
  beep offsets: `newTime = currentTime - oldBeepInClip + newBeepInClip`,
  clamped to `[0, duration]`. Playback state (playing/paused) carries over.
- `CamPicker` renders under the player, owner and share routes alike.

### Compare changes

- Each shooter tile whose shooter has more than one camera gets a native
  `<select>` in the tile header (the shooter-switcher precedent from the
  ResultsStage header): text labels, active camera marked by value, beepless
  cameras disabled. Picking one swaps that tile's clip. Tiles are too small
  for an embedded thumbnail strip, and the tile's overflow-hidden would clip
  a custom popover - the OS picker is the accessible overlay.
- The swapped clip stays beep-synced: Compare's sync math uses that camera's
  `beep_in_clip` instead of the primary's.
- Data source: Compare must have the full camera list per shooter. If its
  current payload only carries the primary clip, extend the frontend to reuse
  the per-shooter coach payload (already share-whitelisted); no new backend
  endpoint.

## URL scheme (moment deep links)

`?cam=` on Compare already means "which shooter provides audio", so camera
selection uses a new key:

- Results stage: `?v=<index>` (only written when a non-primary camera is
  active; absent means primary).
- Compare: `?v=<slug>:<index>[,<slug>:<index>...]`, entries only for shooters
  on a non-primary camera.

Camera identity is the payload index (primary = 0). Cameras have no stable
public ID, so if the owner later adds or removes cameras an old link may
resolve to a different camera - accepted as graceful drift; invalid entries
fall back to primary.

The share/copy-link builders in `lib/moment.ts` gain the `v` param alongside
`t`/`cam`/`who`; parsing is tolerant (bad tokens ignored).

## Error handling

- Invalid or stale `v` param: silent fallback to primary, no error UI.
- Stream error on a selected camera: existing player error state; the picker
  stays usable so the viewer can switch back.
- Single-camera runs: picker not rendered at all - zero visual change.

## Non-goals

- No promote-to-primary, beep-sync, or any write path on share surfaces.
- No persistence of the viewer's camera choice beyond the URL.
- No backend or share-whitelist changes (verified already sufficient).
- No grid modal for shares.

## Testing

- Unit (vitest): beep-offset time mapping, `v` param parse/serialize for both
  forms, fallback rules (no primary, out-of-range index).
- Component: CamPicker renders n tiles, hides at n=1, keyboard-activates
  selection, marks active tile accessibly.
- ResultsStage: switching cameras swaps `src` and preserves mapped time.
- Scoped test runs per task; full SPA gate (typecheck + pnpm test + scoped
  eslint) at end of branch.
