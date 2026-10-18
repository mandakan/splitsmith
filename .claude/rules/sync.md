---
paths:
  - "src/splitsmith/sync/**"
  - "src/splitsmith/ui/sync_api.py"
  - "src/splitsmith/ui/auto_sync.py"
  - "src/splitsmith/ui/command_runner.py"
  - "src/splitsmith/db/project_state.py"
  - "src/splitsmith/db/desktop_commands.py"
  - "src/splitsmith/db/desktop_tokens.py"
  - "src/splitsmith/ui_static/src/lib/desktopCommands*.ts"
  - "src/splitsmith/ui_static/src/lib/useDesktopCommands*.ts"
  - "src/splitsmith/ui_static/src/components/desktop/**"
  - "src/splitsmith/ui_static/src/components/match/SyncCard*.tsx"
  - "tests/test_sync*.py"
  - "tests/test_audit_lock_wiring.py"
---

# Desktop <-> hosted sync

Merge rules for stage regions and the stage note are in `stage-events.md`.

## State doc kinds and the allowlist

- **Adding a `doc_kind` to `state_docs` is not a local change.** The pull
  manifest (`ProjectStateStore.list_doc_meta`) is kind-agnostic and returns a
  new kind at once; `SyncClient._doc_path` maps an unknown kind onto the audit
  URL shape, so an unhandled kind fails the **entire** sync for that match.
- `sync.pull.PULLABLE_DOC_KINDS` is the allowlist that keeps a new kind inert.
  Widen it only together with a merge rule in `sync.run._apply_pull`.
- The allowlist is applied in **two** places that move together:
  `sync.pull.plan_pull` (client) and `sync_api.get_doc_manifest` (server).
  There is no client-version negotiation, so the server filter protects
  installs nobody controls and the client filter protects a new client talking
  to an older server. Pinned by
  `tests/test_sync_api.py::test_doc_manifest_omits_kinds_a_desktop_client_cannot_pull`
  and the `plan_pull` cases in `tests/test_sync_pull.py`.
- Cascades: `delete_match` filters on `match_id` alone and `delete_shooter` on
  `(match_id, slug)`, so both sweep a new per-shooter kind with no edit.
  `list_project_docs` / `list_audit_docs` are kind-*specific* and must not be
  generalised into the cascade's job.
- Per-user state (export presets, the shooter book, the account profile) is
  never a `state_docs` kind: a per-match kind would enter the sync manifest.
- Two `duration_seconds` mean different things:
  `export_runs.ExportRun.duration_seconds` is wall clock for the job body,
  `match_exports.MatchExportResult.duration_seconds` is the stitched timeline
  length. Never assign one from the other.

## Auto-sync (spec 2026-09-27)

- A running desktop (app or `splitsmith ui`) syncs every match it has synced
  before, both ways. `ui/auto_sync.AutoSyncService` polls
  `GET /api/sync/fingerprints` (`(doc_count, version_sum)` over the pullable
  kinds plus `versions_digest`, compared with `sync.state.local_fingerprint`)
  and marks a match dirty from an HTTP middleware on successful writes under
  `/api/matches/{id}/` and a job terminal listener.
- The pure core (`sync/auto.py`) decides when: pull immediately, push after
  45 s of quiet, never while the match has a job running, **never while a render
  (`RENDER_KINDS`) runs on any match** (a sync opens with the web-clip backfill,
  an encode per trim), one sync at a time, backoff to 15 min, a 401 pauses
  until the token changes. Automatic runs are the `auto_sync` job kind, hidden
  from the jobs strip unless they fail.
- **Fingerprints carry a digest**: `versions_digest` over identity -> version,
  computed **the same way** from `sync_state` and from a manifest; change one
  side and the comparison silently stops matching. A hosted without the digest
  is compared on the pair; one without `/fingerprints` falls back to per-match
  manifests.
- **A failure parks, it does not retry on a timer.** It waits for a local write,
  a hosted fingerprint that moved since the failure, a poll recovering from an
  outage, a successful manual sync or a restart (`status_for` reports
  `waiting_for_change`). The backoff still floors the retry after a release.
- **One auto-sync per machine, one sync per match**: `flock` on
  `~/.splitsmith/auto_sync.lock` (the other process shows "another splitsmith
  window ... is syncing" and takes over when the owner exits) and on
  `<match>/.sync.lock` around every sync job body.
- Per-match state (`enabled`, the failure memo, the last run, `full_media`,
  `hosted_deleted_at`) lives in `<match>/auto_sync.json`, **never
  `sync_state.json`**: `run_sync` saves that file from memory during a run and
  would undo a toggle. The global switch is `GlobalPrefs.auto_sync_enabled`.
- `SPLITSMITH_AUTO_SYNC=0` disables the service; `tests/conftest.py` sets it
  and a test that wants the service opts back in.

## The reconciler

Every sync, manual or automatic, ends with `sync/reconcile.py` (pure): from the
`processed` flags and audit docs it queues missing `trim` and `shot_detect`
steps (how a beep confirmed on the phone gets trimmed and detected on the
desktop). `_after_beep_reviewed` uses the same `video_step` with
`explicit=True`; **a new pipeline step belongs there, not in a second rule**. A
failed step is remembered in `auto_sync.json` with every input it depended on
(beep, stage time, and for a trim the buffers, source path and source presence)
and skipped until one changes. End-to-end check:
`tests/test_sync_integration.py::test_phone_beep_confirm_reaches_the_desktop_reconciler`.

## Audit docs and locking

- Audit GET/PUT carry `_version` (`audit_revision`, a content hash, the same in
  both modes, never stored); a stale PUT is a 409 `version_conflict` and both
  audit pages reload.
- The pull's audit read-merge-write holds `AppState.audit_lock`, the lock the
  PUT's compare-and-save holds. **Every local audit read-modify-write in
  `server.py` runs under `_audit_rmw()`** (the lock locally, a no-op hosted), or
  the pull can lose its edit; `tests/test_audit_lock_wiring.py` pins it with a
  probe lock.
- `find_active` is scoped to `current_match_id` when set: shooter slugs repeat
  across matches.

## Media in the push

- Pushes are web-only unless the match's `full_media` flag (the SyncCard's Web /
  Full switch) is on: `build_push_plan(full_media=...)` skips a `_trimmed.mp4`
  whose `_web.mp4` exists (a clip without a rendition keeps its trim). The gc
  deletes a remote full trim only when its rendition key is in
  `sync_state.items`.
- The hosted delete route accepts beep_review keys, `*_trimmed.mp4`, and a
  `*_web.mp4` only while its `_trimmed.mp4` is in storage (so a stale window is
  never preferred). The params sidecar is never deletable.

## A match deleted on hosted stays deleted

`run_sync` checks the manifest *before* `ensure_match` (which would re-adopt a
mirror): pushed docs recorded and none on hosted means deleted, so it records
`hosted_deleted_at`, raises `SyncMirrorGone` and auto-sync stops watching.
`POST /api/match/sync/republish` is the only way back (resets `sync_state.json`
and `sync_base/`). A 409 on one doc missing from the manifest fails naming the
doc, never three "could not converge" retries.

## The desktop command queue (spec 2026-09-28)

The phone asks, the desktop runs.

- Hosted: the `desktop_commands` table (never a `state_docs` kind); phone routes
  under `match/desktop-commands` (a REVIEW capability, mirrors only); desktop
  routes under `/api/sync/commands` on a 10 min lease; `pending_commands` on
  each fingerprint row.
- Desktop: `ui/command_runner.CommandRunner` inside `AutoSyncService` (so only
  the owner-lock process claims). It claims after that match's sync succeeds,
  refuses a stage whose audit revision moved since the request
  (`sync/commands.refuse_reason`), and completes only after a sync that started
  after the job ended. The result push uses `AutoSyncCore.request_push_now`,
  never a backdated `mark_dirty` (the job's own dirty mark would restart the
  quiet timer).
- A new command kind is `COMMAND_KINDS` hosted plus `RUNNABLE_KINDS` and the
  server's `_start_desktop_command` on the desktop.
- SPA: a desktop-synced match (`origin === "desktop"` with the review
  capability) asks instead of detecting: `lib/useDesktopCommands` (polls only
  while a request is active), `lib/desktopCommands` (all wording),
  `DesktopCommandLine`, the phone Audit's header menu and empty state, the
  desktop Audit overflow's `DetectShotsBadge` `desktop` prop, and the
  Overview's "Desktop requests" sheet. A new kind's phone entry goes through the
  same hook.
