# Background Uploads (within-session) -- Design

Date: 2026-07-06
Status: Approved (design), pending implementation
Scope: Hosted-mode video upload UX in the SPA (`src/splitsmith/ui_static/`)

## Problem

Hosted-mode footage upload currently runs inside a full-screen modal
(`AddFootageModal` -> `HostedUploadBody`) that **locks the whole app** while
transfers are in flight: backdrop click disabled, Escape disabled, close (X)
hidden, footer shows "Uploading...", and a `beforeunload` guard blocks
navigation.

The lock is not a deliberate UX choice. The entire upload queue -- `uploads[]`,
progress, abort controllers, the pump effect -- lives in local React state
inside the modal component. Closing the sheet or leaving the Ingest route
unmounts the component, which aborts the in-flight transfer and discards the
queue. The lock exists only to stop the user from destroying an in-memory queue
that cannot survive navigation.

A typical session is 8-12 files at 300 MB - 1 GB each (3-6 GB total): ~5 min on
fast fiber, realistically 15-30+ min on normal or venue wifi. Trapping the user
for that long -- unable to review already-uploaded footage or even glance at
another stage -- is unacceptable.

## Non-goals

- **Survive reload / tab-close.** Out of scope. The browser cannot hand back
  `File` bytes after a reload without the user re-picking the files (hard
  security limit), so true resume would require IndexedDB-persisted multipart
  state *plus* manual re-selection for little real benefit. An accidental reload
  is rare and already guarded by `beforeunload`.
- **Parallel uploads.** Keep the current one-file-at-a-time pump. Concurrency is
  a separate optimization.
- **Server-side upload jobs.** Uploads stream bytes from the browser (single-shot
  to `/api/me/raw/upload` for < 64 MB; presigned multipart direct to R2 for
  larger). They cannot be handed to the server worker fleet, so they are not
  part of the `/api/me/jobs` model.

## Approach

Lift the upload queue out of the modal into an app-level **`UploadProvider`**
(React context) mounted in `AppShell`, above the router. The provider owns the
transfer lifecycle; the modal becomes a thin view; a dedicated **`UploadDock`**
surfaces background progress.

### Components

1. **`UploadProvider`** (new) -- context provider mounted in `AppShell` above the
   router. Owns:
   - the `uploads[]` queue (`PendingUpload` shape, unchanged: `id`, `file`,
     `status`, `bytesSent`, `errorMessage?`, `controller?`)
   - the single-active-XHR pump (moved verbatim from `HostedUploadBody`, keyed on
     queue + a pump tick, re-entrancy-guarded)
   - the active `AbortController` ref
   - probe data (`probeByFilenameRef`) and the auto-attach-on-completion call
   - actions exposed via context: `enqueue(files)`, `cancel(id)`, `cancelAll()`,
     `acknowledge(id)` / `clearFinished()`
   - derived selectors: counts (queued/uploading/done/error), aggregate percent,
     per-file rows
   Because the pump lives here (not in a component that unmounts), closing the
   sheet or changing routes no longer aborts anything.

2. **`AddFootageModal` / `HostedUploadBody`** (refactor) -- becomes a thin view
   over `UploadProvider`. It renders the drop zone, the file list, and coverage
   controls, but reads queue state and dispatches actions to the provider instead
   of owning `useState`/`useRef` transfer state. The modal is now freely
   dismissable while uploads run; the unmount-aborts cleanup is removed.
   - Footer/hint copy while in flight: "You can close this and keep working --
     uploads continue in the background."
   - Backdrop click, Escape, and the X close are re-enabled during upload.
   - Local-mode (scan-queue) branch is unchanged -- it moves no bytes and keeps
     its current behavior.

3. **`UploadDock`** (new) -- a persistent activity surface, visually consistent
   with the existing `JobsRail`, mounted alongside it in `AppShell`. Fed only by
   `UploadProvider` (kept separate from `Jobs.tsx`, which continues to mirror
   *server* jobs -- different data source, one clear purpose each).
   - Collapsed: "Uploading 3 of 11 . 42%" with an aggregate progress bar; hidden
     when the queue is empty and nothing awaits acknowledgement.
   - Expanded: per-file rows (name, percent, status) with per-file cancel and a
     "Cancel all" action.
   - Finished/failed rows persist as a summary until acknowledged, so results are
     visible after navigating back.
   - One-line note: "Uploads run in the background, but don't reload the page
     until they finish."

### Data flow

- User drops/picks files in the modal -> `enqueue(files)` on the provider.
- Provider pump uploads one file at a time via existing `api.uploadRawFile`
  (< 64 MB) or `api.uploadRawMultipart` (>= 64 MB), reporting `bytesSent`.
- On each file's bytes landing, provider calls the existing `autoAttach` path
  (`POST /api/shooters/{slug}/raw-videos/attach`, `covers_stages` omitted ->
  unassigned tray) and fires `onImported`-equivalent so the Ingest tray reloads.
- `UploadDock` and the modal both render from provider-derived selectors; they
  stay in sync because there is a single source of truth.
- Cancel/cancelAll abort the relevant transfer(s) via `AbortController`.

### Preserved behavior

- `beforeunload` warning stays (reload/tab-close still kills in-flight uploads).
- Size-adaptive transfer mechanics, probe-on-add, dedup-by-storage-path, and the
  manual attach + coverage fallback are unchanged -- they move, they don't change.
- Auto-attach semantics from #570 are preserved.

## Testing

The SPA has no test runner (see project notes), so verification is:
`pnpm typecheck && pnpm lint && pnpm build` in `ui_static`, plus manual smoke:

1. Start an 8+ file upload, close the sheet, navigate to another stage -> uploads
   continue, dock shows progress.
2. Assign coverage to a file that finished while others are still uploading.
3. Cancel one file mid-transfer; cancel all.
4. Reload mid-upload -> `beforeunload` warning fires; on confirm, uploads are
   gone (expected, documented).
5. Finished summary persists in the dock after navigating away and back until
   acknowledged.

## Risks

- **Refactor surface.** `HostedUploadBody` is ~500 lines with intertwined state;
  moving the pump without regressing auto-attach, probe, and multipart is the
  main risk. Mitigate by moving logic verbatim into the provider first, then
  thinning the view.
- **Double-mount / StrictMode.** The pump's re-entrancy guard must survive being
  hoisted to a provider; verify it does not double-start transfers.
