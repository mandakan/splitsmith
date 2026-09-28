# Desktop command queue (auto-sync v2)

Date: 2026-09-28
Status: S1 (hosted queue), S2 (desktop runner) and S3 (phone UI) implemented; re-detect works end to end. Render and YouTube upload are later kinds. Tracking issue #1100.
Builds on: `2026-09-27-auto-sync-reconciler-design.md` (v1, v1.1).

## Goal

v1 made **state-derived** work flow from the phone to the desktop: a beep
confirmed on the phone syncs down, and the reconciler turns the synced state
into a trim and a detection. Some work cannot be derived from state because
it is a **request**: "re-detect this stage from scratch", "render this
match", "upload that render to YouTube". v2 adds a queue for those requests:
the phone (hosted) asks, the desktop claims and runs it, the result comes
back through the existing sync, and the phone sees the outcome.

Success for the first slice: on the phone, in a desktop-synced match, press
"Re-detect on desktop" on a stage; within about a minute of the desktop
being up, the stage shows the new detection, and hosted ran no detection.

## Decisions (agreed 2026-09-28)

1. **First command: re-run detection** (`shot_detect` with `reset`). Its
   result is an audit doc, which sync already carries back, so it proves the
   whole loop (request, claim, lease, run, report) with no new byte path.
   Render and YouTube upload follow on the same queue once #752 (a
   desktop-origin byte path for rendered exports) exists.
2. **Entry points: per stage, plus a match-level menu.** Re-detect sits on
   the stage the phone user is looking at. The match menu lists the match's
   requests (status, cancel) and later carries the match-wide kinds
   (render, upload).
3. **No desktop around: the request waits, and can be cancelled.** No
   expiry: a request made at the range runs when the laptop opens at home.
4. **Presence hint: a desktop counts as around when seen in the last
   5 minutes**, from `desktop_tokens.last_used_at` (auto-sync polls every
   60 s, 300 s idle, so a running desktop always touches it within 5 min).

## Scope

In v2, slice by slice (each its own PR):

- **S1, hosted queue.** A `desktop_commands` table, the request / list /
  cancel routes for the phone, the claim / heartbeat / complete routes for
  the desktop, a pending count on `GET /api/sync/fingerprints`, and the
  presence route.
- **S2, desktop runner.** Auto-sync claims pending commands for the matches
  it watches, runs them as local jobs, and completes them after the result
  has been pushed.
- **S3, phone UI.** "Re-detect on desktop" on the stage, the request's
  status line with the presence hint, and the match menu's request list.

Later, on the same queue (not in this spec's slices):

- `render_export` once #752 gives a rendered MP4 a path to hosted.
- `youtube_upload` (the OAuth token lives on the desktop, so it has to run
  there; `ui/youtube_api.py` routes 404 hosted).

Out of scope:

- Commands for hosted-native matches. They have hosted compute; the queue is
  for mirrors (`matches.origin == "desktop"`), and the request route
  refuses anything else.
- A background agent or push channel. As in v1, work runs only while the
  desktop app or `splitsmith ui` runs, and the 60 s poll is the transport.
- Choosing *which* desktop runs a command when several are linked. The
  first to claim wins; the per-machine owner lock (#1076) already keeps one
  process per machine.

## Current state (verified 2026-09-28)

- `desktop_tokens` (`db/models.py`) has `last_used_at`, stamped by
  `DesktopTokenAuth` on every call, and `scope` (`sync` for the device-flow
  tokens the desktop uses). Nothing reads `last_used_at` for presence yet.
- `GET /api/sync/fingerprints` returns `{match_id, doc_count, version_sum,
  digest}` per match; fields are additive (#1072 added `digest` without
  breaking older desktops), and a desktop falls back to manifests when the
  route is missing (#1071).
- Mirrors are capability-gated (`ui/capabilities.py`): origin `desktop`
  grants `review`, `share_manage`, `comment_write`, never `edit`. The
  existing `POST .../stages/{n}/shot-detect` requires `edit`, so a mirror
  403s it today: there is no way to re-detect a synced stage from the phone.
- Desktop side: `AutoSyncService` (`ui/auto_sync.py`) ticks every 5 s, polls
  every 60 s (300 s idle), holds the per-machine owner lock
  (`~/.splitsmith/auto_sync.lock`), and runs syncs as `auto_sync` jobs under
  `<match>/.sync.lock`. `_run_shot_detect(slug, stage_number, reset)` is the
  local job body; a reset wipes `shots[]` and records `marker_deleted`
  events for identified shots.
- Audit docs carry `_version` = `audit_revision(doc)`, a content hash that
  is the same in both modes and never stored.
- A match deleted on hosted is never re-created by the desktop (#1088); the
  hosted delete cascade (`ui/match_delete.py`) sweeps per-match tables by
  `match_id`.

## Design

### Table: `desktop_commands` (hosted, Postgres; alembic migration)

| column | type | notes |
|---|---|---|
| `id` | str PK | ulid |
| `user_id` | FK users, index | every query filters on it, like `compute_jobs` |
| `match_id` | str, index | |
| `kind` | str | `shot_detect` in S1; allowlisted |
| `slug` | str null | shooter |
| `stage_number` | int null | |
| `args` | JSON | `{"reset": true}` for re-detect |
| `expected_revision` | str null | the stage audit's `_version` when requested (see Safety) |
| `status` | str | `pending`, `claimed`, `succeeded`, `failed`, `cancelled` |
| `claimed_by` | FK desktop_tokens null | which linked desktop |
| `lease_expires_at` | timestamptz null | claim lease |
| `cancel_requested` | bool | set by a cancel on a claimed command |
| `progress_message` | str null | last heartbeat's message |
| `error` | str null | plain-language reason on `failed` |
| `result` | JSON null | e.g. `{"shots": 23}` |
| `requested_at`, `claimed_at`, `finished_at` | timestamptz | |

Not a `state_docs` kind: a new doc kind enters the sync manifest (CLAUDE.md,
"State doc kinds and the sync allowlist"). The hosted delete cascade gains a
`desktop_commands` sweep by `match_id`.

Dedupe: requesting a `(match_id, kind, slug, stage_number)` that already has
a `pending` or `claimed` command returns that command instead of queueing a
second.

### Hosted routes for the phone (session auth, match alias)

- `POST /api/match/desktop-commands` `{kind, slug, stage_number, args}` ->
  201 with the command (200 with the existing one on dedupe). Requires the
  `review` capability (a mirror has it) and 409 `not_a_mirror` for a
  hosted-native match. For `shot_detect` it validates what the local route
  validates (primary, beep, stage time) so the phone fails fast, and it
  records `expected_revision` from the stored audit doc.
- `GET /api/match/desktop-commands` -> the match's commands, newest first,
  last 20, plus `presence` (below) so the phone's poll is one request.
- `POST /api/match/desktop-commands/{id}/cancel` -> `pending` becomes
  `cancelled` at once; `claimed` gets `cancel_requested` (the desktop
  honours it at its next heartbeat, since local jobs cancel cooperatively).

### Hosted routes for the desktop (`/api/sync`, sync-scoped bearer)

- `GET /api/sync/fingerprints` gains `pending_commands: int` per match row
  (additive). The desktop already polls it, so there is no second loop.
- `POST /api/sync/commands/claim` `{match_ids: [...]}` -> claims, in one
  transaction, up to N `pending` commands (and `claimed` ones whose lease
  expired) for those matches; sets `claimed_by`, `claimed_at`,
  `lease_expires_at = now + 10 min`. Returns them. `match_ids` is what the
  desktop watches, so a match the desktop does not have (or has with
  auto-sync off) is never claimed by it.
- `POST /api/sync/commands/{id}/heartbeat` `{message}` -> extends the lease,
  stores `progress_message`, returns `{cancel_requested}`.
- `POST /api/sync/commands/{id}/complete` `{status, error?, result?}` ->
  terminal. Idempotent: completing an already-terminal command is a no-op
  200, so a desktop that crashed after completing can safely retry.

### Presence

`GET /api/me/desktop-presence` (also embedded in the list response):
`{last_seen_at, around}` where `last_seen_at` is the newest `last_used_at`
over the user's non-revoked desktop tokens (any scope: a legacy `full` token is a desktop too) and `around` is
`last_seen_at > now - 5 min`. The phone words it:

- around: "Your desktop will pick this up shortly."
- not around: "Waiting for your desktop (last seen 2 h ago)." / "No desktop
  linked." when there is none.

### Desktop runner (S2)

In `AutoSyncService`, behind the per-machine owner lock (#1076):

1. A poll that reports `pending_commands > 0` for a watched match marks it
   **commands due**. `pick` treats that like `pull_due` (pull first: the
   command acts on the latest state).
2. After that match's sync succeeds, the service claims for the matches it
   watches. Each claimed command becomes a local job:
   - `shot_detect`: the **Safety** check below, then submit `shot_detect`
     with `reset=args.reset` for `(slug, stage_number)`, deduped by
     `find_active` like any other submit.
3. While the job runs, each tick heartbeats the command with the job's
   progress message; a `cancel_requested` reply cancels the local job.
4. When the job ends, the match is marked dirty and synced at once (no 45 s
   quiet period). The command is completed **after that push succeeds**, so
   by the time the phone reads "done", hosted has the new audit doc.
   Failures complete as `failed` with the job's error text. No retry on a
   timer (the v1 rule, #1070): the user asks again.
5. Commands and their local jobs are remembered in memory keyed by command
   id; a restart loses that, the lease expires, and the command is claimable
   again. The Safety check makes the re-run safe.

### Safety: a reset never wipes edits it did not see

A reset re-detect wipes `shots[]`. Two things could make it wipe work the
user did not mean to lose: a lease that expired after the first run already
succeeded (the command runs twice), and edits made on either side between
the request and the run. `expected_revision` covers both: after the pre-run
pull, the desktop compares the stage audit's `audit_revision` with the
command's `expected_revision` and, if they differ, completes the command as
`failed` with "the stage changed after this was requested; ask again". The
first run of a doubled command changes the doc, so the second refuses.

### Phone UI (S3)

- **Stage**: on a desktop-synced match, MobileAudit and the stage page offer "Re-detect on desktop". It confirms first ("This replaces the
  stage's shots with a fresh detection on your desktop. Edits made after you
  press this stop it from running."), then shows a status line under the
  stage header: waiting (with the presence wording), running (progress
  message), done, failed (reason), cancelled. The existing local "Re-detect"
  stays for hosted-native matches, where hosted runs it.
- **Match menu**: "Desktop requests" lists the match's commands with status
  and a cancel for pending / running ones. Render and upload join this menu
  when their kinds ship.
- The phone polls `GET /api/match/desktop-commands` every 10 s while any
  command is non-terminal, and not at all otherwise.

## Error handling

| Situation | Behaviour |
|---|---|
| No desktop ever linked | request allowed; status line says "No desktop linked" |
| Desktop offline | waits (no expiry); presence says when it was last seen |
| Match deleted on hosted | cascade deletes its commands |
| Match not on this desktop / auto-sync off there | never claimed by it; stays waiting |
| Desktop crashes mid-run | lease expires after 10 min, claimable again; Safety makes the re-run refuse if the first run landed |
| Pre-run check fails (stage changed) | `failed`, reason shown, user asks again |
| Job fails | `failed` with the job's error |
| Push after the job fails | command stays claimed and heartbeating; completes after the next successful sync, or the lease lapses if the desktop quits |
| Older desktop (pre-v2) | ignores `pending_commands`; commands wait until a v2 desktop claims them |

## Testing

- Store: claim is atomic and user-scoped; expired leases are reclaimable;
  complete is idempotent; dedupe returns the existing command; the cascade
  sweeps commands.
- Routes: a mirror can request, a hosted-native match 409s, a share token
  cannot reach any of them; the desktop routes refuse a non-sync token.
- Runner (pure core plus the service, fake clock as in v1): commands due
  pulls first; claim after the sync; heartbeat extends; cancel reaches the
  local job; complete only after the push; the revision guard refuses a
  changed stage and a doubled command.
- End to end in `tests/test_sync_integration.py`: request on the hosted app,
  a desktop service claims it, a real detection runs on synthetic media, the
  push lands, the command reads `succeeded`, and the hosted audit has the
  new shots.

## Open questions for the later kinds

- `render_export`: which export options the phone can set (presets are per
  user, so likely "render with preset X"), and the byte path back (#752).
- `youtube_upload`: request by export run id; the result is the video URL.
