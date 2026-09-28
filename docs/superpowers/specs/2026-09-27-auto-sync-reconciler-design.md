# Auto-sync and the desktop reconciler

Date: 2026-09-27
Status: v1 implemented (auto-sync + reconciler, #1068); v1.1 web-only media implemented (#1079,
spec 2026-09-27-web-only-mirror-media-design.md); review follow-ups #1067-#1078 and #1088 merged
(released in 0.43.1); v2 command queue pending
Follow-up (v2, separate spec): the hosted-to-desktop command queue, tracked in #1100.

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

- A fingerprint feed on hosted (`GET /api/sync/fingerprints`).
- A desktop auto-sync service: watcher, dirty tracker, scheduler.
- A pure reconciler that derives missing pipeline steps from state.
- Audit revisions: a `_version` token on the audit GET/PUT so a stale
  editor gets 409 instead of overwriting a pull (both modes).
- `find_active` scoped to the submitting match.
- SyncCard and settings controls.

Automatic pushes use today's `full` media policy (full trims plus web
renditions). Nothing on hosted changes behaviour in v1.

Split out to v1.1 (separate spec): the web-only media policy. Planning
found that hosted playback for a mirror keys off the full trim object in
R2 (`_video_trim_anchor` checks `trim_available` before it considers the
web rendition; MobileAudit asks for `kind=trim` explicitly and gets a
404; audit peaks are computed from the pulled trim). Web-only needs the
hosted anchor, stream and peaks paths to treat `_web.mp4` plus the pushed
`.params.json` as the trim, which is its own surface and its own
verification.

Out of scope:

- The command queue (render an export, upload to YouTube, re-run
  detection on request). Intent-driven work gets its own table and spec.
- The hosted "processing on your desktop" hint. `desktop_tokens.last_used_at`
  already records last contact (stamped by `DesktopTokenAuth` on every
  call), so v2 reads that; nothing is added here.
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

### 1. Hosted fingerprint feed

`GET /api/sync/fingerprints` on the sync router, bearer-authed like the
rest of it. Returns `{matches: [{match_id, doc_count, version_sum}]}` for
every match of the caller's that has docs, counting only kinds in
`PULLABLE_DOC_KINDS`.

No cursor. `state_docs.version` only increases, so any write moves
`version_sum` and any insert moves `doc_count`. The desktop computes the
same pair from `sync_state.doc_versions` (whose keys are exactly the
pullable identities) and wants a pull when the two differ. After a push
the desktop records the versions the PUTs returned, so its own writes
never wake it. If the pair stays different after a sync that succeeded
(a doc deleted hosted-side, say), the scheduler remembers that server
fingerprint as settled and does not re-sync until it moves again.

One grouped query over `state_docs` filtered on `user_id`; no new table,
no migration.

### 2. Desktop auto-sync service (`sync/auto.py`)

One asyncio task started with the local app (`create_app` in local mode
only; hosted never starts it). State per match lives in memory; the
persistent parts live in `sync_state.json`.

**Watcher.** Polls `/fingerprints` every 60 s. After 30 min with no movement on
either side it backs off to 5 min; any local write or remote change
resets it to 60 s. A reported match with auto-sync on becomes
`pull_due`.

**Dirty tracker.** Two hooks set `push_due` with a timestamp: an HTTP
middleware that sees a successful (status < 400) POST, PUT, PATCH or
DELETE under `/api/matches/{match_id}/` (sync routes excluded), and a
terminal listener on the job registry for a succeeded job whose
`match_id` is set and whose kind is not a sync. Every local write goes
through one of the two.

**Scheduler.** Every tick it considers each auto-synced match:

- skip if any job for the match is pending or running (this is also what
  keeps a long job's in-memory `MatchProject` from racing a pull);
- skip if a `sync_match` job is active anywhere (existing one-at-a-time
  rule);
- submit an `auto_sync` job (the `sync_match` body, registered under its
  own kind so the SPA can tell them apart) if `pull_due`, or if
  `push_due` and the last dirtying write is at least 45 s old.

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

Per shooter, per stage, per video, reading the `processed` flags the
merge already maintains (a pulled `beep_time` change clears
`processed["trim"]` and, on a primary, `processed["shot_detect"]`):

| State | Step |
|---|---|
| `beep_reviewed`, `beep_time` set, `stage.time_seconds > 0`, not `processed["trim"]` | `trim` (its job chains detection for a reviewed primary, behind the existing `shot_detect_on_beep_verified` automation gate) |
| primary, `beep_reviewed`, `processed["trim"]`, not `processed["shot_detect"]`, audit doc missing or a stub (`is_stub_audit`), and the project's resolved `shot_detect_on_beep_verified` automation is on | `shot_detect` |

No web-cut step: `run_sync` already backfills missing `_web.mp4`
renditions before every push. The reconciler never detects over an audit
doc with real content; re-detecting a worked stage stays a manual action.

`_after_beep_reviewed` keeps its explicit-confirm semantics (a local
confirm on an already trimmed primary always queues detection) but
evaluates the same rule function with `explicit=True`, so the trim
condition and dedupe live in one place.

`ReconcileFailures` is a small map persisted in `auto_sync.json`:
`(slug, stage, video_id, step) -> input_key` where `input_key` is the
`beep_time` and trim params the step failed with. A step whose inputs
match a recorded failure is skipped; changed inputs or a manual retry
clear the entry. This keeps a broken clip from being resubmitted on every
poll.

The server runs the reconciler at the end of every sync, manual or
automatic (it is idempotent and cheap); at startup the service marks
every auto-synced match pull-due, so the first run reconciles too. It submits the steps
through the normal job registry, deduplicated with `find_active` as
`_after_beep_reviewed` does. Completion of those jobs sets `push_due`,
which closes the loop.

`merge.reprocess_video_ids` stays as the report's counter; the
reconciler, not the merge, decides what runs.

### 4. Match-scoped job dedupe

`JobRegistry.find_active` matches on `(kind, stage_number, shooter_slug
[, video_id])` and ignores the match. Shooter slugs repeat across matches
(the owner is in every one), so once the service processes background
matches, match A's stage 3 trim would dedupe against match B's. It gains
a match filter: when `current_match_id` is set in the caller's context,
only jobs with that `match_id` match. A context-free caller keeps today's
behaviour.

### 5. Audit revisions

The SPA's audit PUT carries no version on either mode (the route's own
comment: "assumes last-writer-wins"), and local `load_audit` reports 0.
A pull that lands while the Audit page is open is overwritten by the
page's next save, and the phone's edits are then pushed up as lost.

- `GET .../audit` adds `_version` to the returned doc: a 16-hex-char
  sha256 of the stored doc's canonical JSON (`sort_keys`, compact
  separators), the same in both modes.
- `PUT .../audit` pops `_version` from the payload. When present, the
  route loads the stored doc under `AppState.audit_lock`, compares
  revisions, and raises `AuditRevisionConflict` on a mismatch, which maps
  to the same 409 `version_conflict` body hosted uses. The save happens
  inside the same lock. The response carries the new `_version`. A PUT
  without `_version` (an older client, a script) behaves as today.
- `buildAuditJson` spreads the loaded doc, so `_version` round-trips with
  no change to how the payload is built. `MobileAudit` already reloads on
  409; desktop `Audit.tsx` gains the same handling.
- `AuditRevisionConflict` lives outside `splitsmith.db` so the slim local
  install can raise and map it (the #1057 import-surface rule).
- `run_sync` takes an optional lock and holds it around each audit doc's
  read-merge-write in `_apply_pull`; the server passes
  `state.audit_lock`.
- `_version` is never stored: the PUT pops it before saving and the sync
  push reads files, which never contain it.

### 6. Persistent per-match state

A new local file, `<match-root>/auto_sync.json` (`sync.auto_state`),
holds:

- `enabled: bool | None = None`: `None` means default, which is on once
  `sync_state.last_synced_at` is set.
- `reconcile_failures: dict[str, str]`: step key -> input key.
- `last_auto: AutoRunSummary | None`: time, outcome, message, conflict
  and note counts of the last automatic run, for the card.

Not in `sync_state.json`: `run_sync` saves that file repeatedly from its
in-memory copy, so a toggle made during a sync would be overwritten. The
new file has one writer helper that load-modify-saves under a module
lock. Root-level files are never in the push plan, so this needs no
`state_docs` kind and no sync allowlist change.

Global switch: `auto_sync_enabled: bool = True` in `GlobalPrefs`.

### 7. UI (local mode only)

- `SyncCard`: a `Segmented` Auto / Manual control; status line "Auto-sync
  on, synced 2 min ago", "Auto-sync paused: offline", or the last run's
  conflict and note counts with a link to details.
- `SyncSettingsDialog`: the global switch.
- Progress strip: `sync_match` jobs with `auto=True` are hidden while
  pending, running or succeeded, and shown when they fail. Reconciler
  jobs (trim, detect, web cut) show as usual.
- New endpoints: `GET/PUT /api/match/sync/auto` for the per-match flag
  and the service's current state for this match; `PUT
  /api/settings/auto-sync` for the global switch.

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
- `/fingerprints`: a PUT moves `version_sum`; an `export_runs` write does
  not; another user's docs never appear.
- `find_active`: a job in match B does not satisfy a lookup made in match
  A's context.
- Audit revisions: a stale `_version` returns 409 in local mode; a
  matching one saves and returns the new `_version`; no `_version` saves
  as today.
- End to end, by hand and recorded in the PR: seeded demo match with
  `--media`, synced to staging; confirm the beep through the hosted API;
  watch the desktop trim, detect and push with no clicks; read the shots
  back from hosted.

## Risks

- A pull landing between an endpoint's load and save of `project.json`.
  Endpoint handlers are short and synchronous; the scheduler never syncs a
  match with an active job. Accepted for v1.
- Poll cost with many matches: one request per poll regardless of match
  count, by design of `/fingerprints`.
- R2 storage keeps growing with full trims until v1.1 lands.
