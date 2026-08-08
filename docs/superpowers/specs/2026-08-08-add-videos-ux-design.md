# Add-videos UX rework: mode-gated drops + single-scroll folder picker

Date: 2026-08-08
Status: approved

## Problem

Two defects in the add-footage workflow:

1. **Drag-to-upload lies in local mode.** The Ingest empty-state panel says
   "Drop a folder of videos" but has no drag handlers at all - it is
   decorative (`Ingest.tsx` DropZone). Worse, a drop anywhere in the SPA
   navigates the browser into the video file and destroys session state.
   The only real drop target is inside the hosted upload modal, and it
   uses a naive `isDragging` boolean with no enter/leave counter and no
   window-level listeners. Deployment mode also resolves asynchronously
   and defaults to `"local"` in flight, so hosted users can briefly see
   the local modal variant.

2. **The folder picker is a scroll-in-scroll mess.** `FolderPicker` is
   embedded inline inside `AddFootageModal`'s own scrolling body, giving
   two stacked scroll containers (the inner listing capped at
   `max-h-80`), a queue UI (`QueueView`), auto-commit file selection, and
   folder-vs-files mutex logic all layered in one card. Mounted volumes
   are discovered server-side (`_discover_mounts`) and shipped in every
   listing, but the sidebar is buried in the nested grid and there is no
   root entry - reaching `/Volumes/...` in practice means hand-editing
   the path field.

## Decisions made during brainstorming

- The drag failure that prompted this was in **local mode**: gate the
  drop affordance by deployment mode and show a local-appropriate
  surface instead. A browser drop cannot expose absolute host paths, so
  local (symlink/copy-by-path) registration can never be fed by a drop.
- Rework covers **all three FolderPicker call sites**: the Ingest
  add-footage flow, CreateMatch's parent-folder picker, and
  RelinkDialog. One component, redesigned once.
- The multi-source queue dies: **one source per pass**, import
  immediately on commit. Adding more footage means reopening the picker.
- Picker shape: **full-height picker modal** - the picker IS the modal.
- Places must include mounted volumes and a Computer (`/`) entry so any
  location on macOS is reachable by clicking, never by typing.

## Section A - Ingest screen and drop behavior

**Mode-aware empty state.** The empty-state card on Ingest becomes
mode-gated:

- **Local:** an "Add footage" card whose primary action is a
  "Pick a folder" button that opens the picker dialog. No drop
  invitation anywhere. The decorative drop styling (dashed border,
  corner brackets, "Drop a folder of videos" copy) is deleted.
- **Hosted:** the whole Ingest page is the drop target. Window-level
  `dragenter`/`dragleave` with a depth counter drives a full-page
  overlay ("Drop videos to upload"); a drop anywhere on the page
  enqueues into the existing `useUploads` queue. The card offers
  "Browse files" as the click path (existing hidden file input).

**App-wide drop guard.** A root-level (App) `dragover` + `drop`
`preventDefault` listener ensures an unhandled drop never navigates the
SPA into the dropped file. In local mode, a drop on the guard shows a
short toast: dropping is not supported locally - use Pick a folder.

**Deployment-mode resolution.** `useDeploymentMode` grows a `resolved`
flag (or equivalent loading state). Add-footage affordances render a
neutral skeleton until the mode is known; neither the local picker nor
the hosted upload surface flashes incorrectly.

## Section B - the picker dialog

One component (`FolderPicker`), one shell (modal only). Fixed-height
dialog: `h-[min(680px,90vh)]`, `max-w-3xl`, three fixed regions so
exactly one scrollbar exists.

**Header.** Title + call-site subtitle + close button. Below it a
breadcrumb bar; a pencil affordance (or pressing `/`) swaps the
breadcrumbs for an editable path input for the rare manual case. The
always-visible PathBar + separate breadcrumb row from today collapse
into this single bar.

**Body.** Two panes:

- **Left: permanent Places sidebar** (fixed width, its own overflow if
  long). Groups: Recent (last scanned dir), Home (~, Movies, Videos,
  Downloads, Desktop), Places (every mount from `_discover_mounts` -
  removable and network - plus a static "Computer" entry navigating to
  `/`). Backend already ships `suggested_starts` on every listing; the
  frontend adds the root entry.
- **Right: the listing** - sort header (name / date cycling, as today)
  above a single `flex-1 min-h-0 overflow-y-auto` list. The `max-h-80`
  cap dies; the outer modal body no longer scrolls. Rows keep current
  behavior: dir rows with shallow video counts, video rows with
  checkbox, on-hover probe (duration + thumbnail float), size, mtime,
  match-window highlight, and the select-in-match-window helper.

**Footer.** Selection summary + the copy/link storage toggle
(add-footage call site only) + Cancel + one primary action:

- nothing checked: "Add this folder" (call sites can override the label,
  e.g. "Use this folder" for CreateMatch/Relink);
- N files checked: "Add N files".

Commit acts immediately: the footer swaps to inline scan progress, the
dialog closes on success, and Ingest refreshes via the existing
`onImported` path. No queue, no second Import step. Selection still
resets on navigation.

**Errors.** Listing errors render inline in the list panel with a retry.
Scan/commit errors keep the dialog open and surface the server detail
inline in the footer region.

**Accessibility.** The dialog keeps `useDialogFocus` (focus trap,
Escape, focus restore) and the body Portal per the overlay convention.
The drag overlay state is announced via a visually-hidden live region;
color is never the sole carrier of the selected/in-window row states
(existing border + background treatment stays).

## Section C - deletions and call-site adaptations

Deleted outright (no parallel legacy paths):

- `FolderPicker` inline shell (`shell="inline"`) and `mode="inline"`.
- `QueueView`, all queue state and `QueueItem` handling in
  `AddFootageModal`, and the two-phase Import flow.
- `autoCommitFiles` mode and the `onFolderFilesChange` sync effect.
- The folder-vs-files mutex logic (`pickerFolderAlreadyWhole`,
  `pickerFolderHasFileChecks`).
- The `DirectoryPickerModal` facade - call sites use `FolderPicker`
  directly.
- The decorative DropZone drag styling and copy on Ingest.

Call sites:

- **Ingest add footage (local):** opens the picker dialog directly;
  storage toggle in the footer; commit runs `scanVideos` /
  `scanFiles` and closes.
- **CreateMatch:** same dialog, `contentMode="directories"`,
  `allowEmptyFolder`, label "Use this folder".
- **RelinkDialog:** same dialog, directories mode, existing relink
  commit.
- **Hosted:** `AddFootageModal` still swaps to `HostedUploadBody`; that
  surface gains the depth-counter drag tracking and the page-level drop
  target from Section A. Upload mechanics (single-shot + multipart,
  attach flow) are unchanged.

## Testing

- Vitest component tests:
  - local mode renders no drop affordance; hosted renders no picker;
    nothing mode-specific renders before the mode resolves;
  - the app-level guard prevents default on unhandled drops; hosted
    page-level drop enqueues; local drop shows the toast;
  - picker commit flows: whole-folder commit, N-files commit,
    empty-folder rules per call site (`allowEmptyFolder` on for
    CreateMatch, off for add-footage);
  - selection resets on navigation; sidebar navigation (volume entry,
    Computer entry) triggers a listing load with the right path.
- Layout verification: bounded headless screenshot pass (domcontentloaded,
  the usual recipe) confirming a single scrollbar in the dialog and the
  sidebar permanently visible.
- Existing tests covering deleted behavior (queue, mutex, inline shell)
  are deleted with it, per the delete-obsolete-tests practice.

## Out of scope

- Resumable/multipart upload changes (#557), bulk move-to-shooter
  (#560), in-picker video preview pane (#25), smart folder structures
  within raw/ (#283).
- Backend endpoint changes: listing, scan, upload, and attach endpoints
  are all unchanged. The root ("Computer") entry is a static frontend
  sidebar item, not a new suggested-start kind.
