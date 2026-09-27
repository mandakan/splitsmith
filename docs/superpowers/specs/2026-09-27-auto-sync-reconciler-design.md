# Auto-sync and the desktop reconciler

Date: 2026-09-27
Status: design approved in conversation, awaiting spec review
Follow-up (v2, separate spec): the hosted-to-desktop command queue.

## Goal

The hosted server stays a thin review and share surface; the idle desktop
does the compute and holds the files. A review action taken on the phone
(confirm a beep, move a beep, accept or edit an audit) reaches the
desktop, which runs trim, detection and the web cut on local hardware and
pushes the results back, with nobody touching the desktop. The owner
should not need to run hosted-only and pay for compute and storage there.

Success: confirm a stage's beep on the phone; within a few minutes the
stage's trim and shots appear on the phone, and hosted ran no detection.

## Scope

In v1:

- A change feed on hosted (`GET /api/sync/changes`).
- A desktop auto-sync service: watcher, dirty tracker, scheduler.
- A pure reconciler that derives missing pipeline steps from state.
- Web-only media on automatic pushes.
- Real optimistic versions for local audit saves.
- SyncCard and settings controls.

Out of v1:

- The command queue (render an export, upload to YouTube, re-run
  detection on request). Intent-driven work gets its own table and spec.
- The hosted "processing on your desktop" hint. The `desktop_last_seen_at`
  stamp lands now so v2 can read it.
- A background agent. Auto-sync runs only while the desktop app or
  `splitsmith ui` is running.
- Push events (websocket, SSE). A 60 s poll is enough to start; revisit if
  latency annoys in practice.

## Current state (verified)

- `sync.run.run_bidirectional_sync` does pull, three-way merge, apply, push,
  with optimistic-lock retries. It runs as the `sync_match` job, one at a
  time across matches.
- Hosted `_after_beep_reviewed` (`ui/server.py`) returns early for a
  `desktop`-origin match: "Desktop re-derives on its next sync pull."
  Nothing on the desktop does.
- `merge.merge_project_doc` collects `reprocess_video_ids` only when
  `beep_time` changed. A confirm-only change (`beep_reviewed` flips, time
  unchanged) flags nothing. The ids reach only a counter in the job
  message.
- `sync.plan.build_push_plan` pushes every trim at full resolution plus
  its `_web.mp4`.
- Local `load_audit` always returns version 0 and local `save_audit` never
  checks it, so a stale whole-doc audit PUT from an open page overwrites
  whatever a pull wrote.

## Design

### 1. Hosted change feed

`GET /api/sync/changes?since=<cursor>` on the sync router, bearer-authed
like the rest of it.

- Returns `{cursor, matches: [{match_id, max_version, updated_at}]}` for
  the caller's matches whose `state_docs` moved after `since`. Only kinds
  in `PULLABLE_DOC_KINDS` count, so an `export_runs` write does not wake a
  desktop into a pull that would then find nothing.
- The cursor is opaque to the client (server-side: the max `updated_at`
  seen, plus a tiebreak), so no row is delivered twice or skipped at a
  boundary.
- Each call stamps `desktop_last_seen_at` on the device token's row (or
  the user row if tokens have no row of their own; settle during
  planning). Nothing reads it in v1.
- No new table. One index on `state_docs(updated_at)` if the query needs
  it.

### 2. Desktop auto-sync service (`sync/auto.py`)

One asyncio task started with the local app (`create_app` in local mode
only; hosted never starts it). State per match lives in memory; the
persistent parts live in `sync_state.json`.

**Watcher.** Polls `/changes` every 60 s. After 30 min with no movement on
either side it backs off to 5 min; any local write or remote change
resets it to 60 s. A reported match with auto-sync on becomes
`pull_due`.

**Dirty tracker.** Local doc saves (project, audit, match) and job
completions for a match set `push_due` with a timestamp. Hooked where the
writes already funnel: `AppState.save_audit`, `MatchProject.save` callers
in the server, and the job registry's completion path.

**Scheduler.** Every tick it considers each auto-synced match:

- skip if any job for the match is pending or running (this is also what
  keeps a long job's in-memory `MatchProject` from racing a pull);
- skip if a `sync_match` job is active anywhere (existing one-at-a-time
  rule);
- submit `sync_match` with `auto=True` if `pull_due`, or if `push_due` and
  the last dirtying write is at least 45 s old.

**Which matches.** Every registered match whose `sync_state.json` has
`last_synced_at` set and `auto_sync` true. The match root is set through
the same `current_match_root` context the manual route uses.

**Failure handling.** Transport errors and 5xx back off exponentially from
1 min to 15 min, per match. A 401 stops the service for all matches until
the hosted-sync settings change. The reason is kept in memory and shown
on the card as "Auto-sync paused: <reason>".

### 3. Reconciler (`sync/reconcile.py`)

`plan_reconcile(match: LoadedMatch, failures: ReconcileFailures) ->
list[ReconcileStep]` is pure: it reads projects and audit docs already
loaded and returns steps; it performs no I/O and submits nothing.

Per shooter, per stage, per video:

| State | Step |
|---|---|
| `beep_reviewed`, stage time and beep allow a trim, trim missing or its params' `beep_time` differs | `trim` |
| primary, `beep_reviewed`, trim current, no detection in the audit doc (missing or stub) | `shot_detect` |
| trim current, `_web.mp4` missing | `web_trim` |

The trim rule is the one `_maybe_chain_trim` applies today. Both
`_after_beep_reviewed` and `_maybe_chain_trim` are refactored to evaluate
the same per-stage rule, so a desktop confirm and a phone confirm cannot
leave different state. The stub audit doc write moves with them.

`ReconcileFailures` is a small map persisted in `sync_state.json`:
`(slug, stage, video_id, step) -> input_key` where `input_key` is the
`beep_time` and trim params the step failed with. A step whose inputs
match a recorded failure is skipped; changed inputs or a manual retry
clear the entry. This keeps a broken clip from being resubmitted on every
poll.

The server runs the reconciler after every sync that pulled at least one
doc, and once at startup per auto-synced match. It submits the steps
through the normal job registry, deduplicated with `find_active` as
`_after_beep_reviewed` does. Completion of those jobs sets `push_due`,
which closes the loop.

`merge.reprocess_video_ids` stays as the report's counter; the
reconciler, not the merge, decides what runs.

### 4. Media policy on push

`build_push_plan(..., media: Literal["web", "full"])`.

- `web`: docs, `_web.mp4`, beep snippets, and `.params.json` sidecars. No
  full-resolution trims.
- `full`: today's behaviour.

Automatic runs use `web` unless the match's `full_media` flag is on.
Manual Sync uses `full`. Hosted streaming already prefers the web
rendition; the known cost is that phone-side audit scrubbing on a stage
whose full trim was never pushed uses the rendition's coarser GOP. We try
this and revisit after use.

Whether `kind=trim` should fall back to the web rendition when the trim
object is absent on a mirror is decided during planning, after checking
what the audit screen does with a missing trim today.

### 5. Local audit versioning

Local `load_audit` returns a real version: a stable integer derived from
the file (content hash truncated to 63 bits, so it survives a restart and
means the same thing on every worker). `save_audit` compares it under
`audit_lock` and raises the same `StateConflictError` hosted raises, which
the route already maps to 409 `version_conflict`. The sync apply path
writes through the same lock.

To confirm during planning: the SPA sends `version` on the local audit
PUT and handles 409 by reloading, as it does hosted. If it does not in
local mode, that is part of this change.

### 6. Persistent per-match state

Added to `SyncState` (all defaulted, so old files load):

- `auto_sync: bool | None = None`: `None` means default, which is on once
  `last_synced_at` is set.
- `full_media: bool = False`.
- `reconcile_failures: dict[str, str]`.
- `last_auto: AutoRunSummary | None`: time, outcome, conflicts and notes
  of the last automatic run, for the card.

`sync_state.json` is local and never synced, so none of this needs a
`state_docs` kind or the sync allowlist.

Global switch: `auto_sync_enabled: bool = True` in the global prefs next
to `hosted_base_url`.

### 7. UI (local mode only)

- `SyncCard`: a `Segmented` Auto / Manual control; status line "Auto-sync
  on, synced 2 min ago", "Auto-sync paused: offline", or the last run's
  conflict and note counts with a link to details. "Full media" as a
  secondary toggle.
- `SyncSettingsDialog`: the global switch.
- Progress strip: `sync_match` jobs with `auto=True` are hidden while
  pending, running or succeeded, and shown when they fail. Reconciler
  jobs (trim, detect, web cut) show as usual.
- New endpoints: `GET/PUT /api/match/sync/auto` for the per-match flags
  and the service's current state for this match.

## Testing

Each new test must fail against the pre-change code (delete the fix,
watch it fail).

- `plan_reconcile`: fixture states for confirm-only, moved beep with a
  stale trim, missing web cut, secondary (trim, no detect), and a
  recorded failure that is skipped until its inputs change.
- Refactored `_after_beep_reviewed`: existing tests still pass; a new
  test pins that a phone-side confirm pulled into the desktop and a local
  confirm submit the same jobs.
- Scheduler: fake clock and fake registry; debounce, busy match defers,
  one-at-a-time, backoff, 401 stops the loop.
- `/changes`: cursor delivers each change once across a boundary;
  non-pullable kinds do not appear.
- Local audit versioning: a stale save returns 409; a save after a pull
  with the old version returns 409.
- `build_push_plan(media="web")` omits full trims and keeps web renditions.
- End to end, by hand and recorded in the PR: seeded demo match with
  `--media`, synced to staging; confirm the beep through the hosted API;
  watch the desktop trim, detect and push with no clicks; read the shots
  back from hosted.

## Risks

- A pull landing between an endpoint's load and save of `project.json`.
  Endpoint handlers are short and synchronous; the scheduler never syncs a
  match with an active job. Accepted for v1.
- Poll cost with many matches: one request per poll regardless of match
  count, by design of `/changes`.
- The web-only policy may make phone audits feel worse. Revisit after use.
