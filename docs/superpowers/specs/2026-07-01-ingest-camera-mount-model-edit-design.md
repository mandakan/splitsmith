# Ingest: edit + verify camera mount / model on the CameraCard

Date: 2026-07-01
Status: design, pending implementation

## Problem

The ingest page's `CameraCard` shows detected camera info ("Camera A, N files",
plus mount/model when present) but is **read-only** -- there is no way to verify
or correct it. This matters because camera mount and model drive detection:

- `camera_class_from_mount(mount)` routes a video through the matching ensemble
  threshold class (headcam vs handheld).
- `amp_floor_by_camera_model[model]` sets the per-model beep amplitude floor.

When ffprobe fails to yield a recognized make/model (e.g. an Insta360 X9), both
fall back to defaults silently, degrading detection with no user recourse.

Editing used to exist: `MountSelect.tsx` shipped on Ingest + Audit in #157, then
the Shot Timer ingest redesign (#325/#356) replaced the editable widgets with the
read-only `CameraCard` and never re-wired the editors. The components
(`MountSelect.tsx`, `CameraModelSelect.tsx`) and backend endpoints
(`set_camera_mount`, `set_camera_model`, `list_calibrated_camera_models`) still
exist, but no page renders the editors. This is a redesign regression, not a stub.

## Goals

- Verify: surface the raw probed make/model on the `CameraCard`, even when mount
  is blank, so the user can see what was detected.
- Edit: let the user set mount + model per camera, applied to all that camera's
  videos at once, in the current Shot Timer aesthetic.

## Non-goals

- Per-video overrides / splitting a mis-grouped camera. The card is per camera
  group (one physical camera = one setting); per-video granularity is out of scope.
- Editing camera settings for unassigned footage. The `CameraCard` is built from
  assigned videos; a same-camera unassigned clip picks up the setting once assigned.

## Design

### Backend -- bulk camera-set endpoint

`POST /api/stages/camera/bulk-set`, body:

```
{
  items: [{ stage_number: int, video_id: str }],
  set_mount: bool, mount: CameraMount | null,
  set_model: bool, make: str | null, model: str | null,
}
```

The `set_*` flags disambiguate "leave unchanged" from "set to null / (auto)", so
clearing an override is distinct from a no-op. Applies the change to every listed
video inside one transaction -> one project save -> one response. Reuses the
existing per-video mutation logic (the same field writes `set_camera_mount` /
`set_camera_model` perform), just looped. Handles both local files and hosted
`state_docs` in a single write, matching the move-shooter discipline. Unknown
`video_id`s are reported/skipped rather than aborting the batch. Returns the
updated `MatchProject`.

### Frontend -- CameraCard controls

- Add a compact control row to `CameraCard`: a **Mount** `<select>` (`CAMERA_MOUNTS`
  + "(auto)") and a **Model** `<select>` (`getCalibratedCameraModels()` results +
  "(auto)"), styled to match the existing VideoRow stage select
  (`border-rule bg-surface-3 ... focus:border-led`), not shadcn tokens.
- Verify: render the raw probed make/model text on the card even when mount is
  blank (today a blank mount renders nothing).
- On change: call `api.bulkSetCamera(...)` with every `(stage_number, video_id)`
  pair in that camera group, then reload the project. This requires threading the
  video ids into `CameraGroup` (it currently carries only `videoPaths`).
- Delete the orphaned `MountSelect.tsx` and `CameraModelSelect.tsx` (dead,
  shadcn-styled). No parallel widgets.
- Add `bulkSetCamera` + types to `lib/api.ts`.

### Data flow

1. `groupByCamera` now records `{ stage_number, video_id }` per member video.
2. Card dropdown change -> `bulkSetCamera({ items, set_mount|set_model, ... })`.
3. Server loops the per-video field writes, saves once, returns the project.
4. Client swaps in the returned project (re-groups, re-renders).

## Testing

- Backend: bulk-set applies mount + model across all listed videos; `set_mount`
  false leaves mount untouched while setting model (and vice versa); "(auto)"
  (null) clears an existing override; unknown `video_id` is skipped without
  aborting; hosted `state_docs` parity (change persists through the store).
- Frontend: `tsc -b --noEmit` + `vite build` clean.

## Open questions

None; all decisions resolved during brainstorming.
