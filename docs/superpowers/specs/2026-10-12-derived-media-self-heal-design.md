# Derived media that heals itself

Date: 2026-10-12. Status: proposed.

## Why

On 2026-10-12 the user synced every local match to splitsmith.app and found that video played in one match out of twelve (Höstfinalen XI). The documents were all identical on both sides; the media was not usable. Three causes, all in how the desktop treats the files it derives from a camera's original (the audit trim, its 720p web rendition, and their params sidecar):

| Cause | Matches (stage videos) | Mechanism |
|---|---|---|
| Corrupt web renditions | Stockholm IPSC Open 2026 (40), Oden Cup 2026 (22) | Every transcode writes to `<stem>.partial.mp4`, the same name in every process. On 2026-09-27 (20:23 to 20:31) two processes cut the same renditions at once (the desktop app and a server started on the real home with auto-sync on) and interleaved their bytes in that one file; one renamed it into place. The only check is `st_size > 0`, `fresh_web_trim` then trusts the file forever, and the sync uploads it. The files have a full-length `moov` whose first child claims 16.8 MB. |
| Older-keyed caches | Bofors Bombardment 2026 (18), VADS Easter Shoot (12), Jinglebell Challenge 2026 (4), Blacksmith Handgun Open 2026 (20) | The take-spec `video_id` change left trims, renditions and sidecars under the old path-only id. The desktop reads them through `_legacy_cache_fallback` ("remove the legacy leg once a cache migration tool exists"); no migration was written. The sync pushes them under their old names; hosted looks only for the new-keyed rendition (by design, `_video_trim_anchor`), finds none, reports `kind: "source"`, and the player is redirected to an original no sync ever uploads. |
| Missing trims | Tallmilan 2026 (25), Tallmilan 2025 (6), Blacksmith Handgun Open 2026 (11) | The reconciler (`sync/reconcile.py`) decides from the `processed` flags, not from the files: a stage marked trimmed whose trim is gone is never cut again. 40 of the 42 originals are on disk; 2 (Tallmilan 2025) are not, and 4 Blacksmith videos have an unconfirmed beep. |

Nothing the user authored is involved: audits, beeps, notes and regions are intact. Every broken file is a cache, reproducible from an original and the project.

## Principle

Trims, renditions and their sidecars are caches. A cache is never trusted for existing; it is trusted for being valid, under the name the current code reads, and it is rebuilt when it is not. That is reconciliation: describe the desired state, check what exists cheaply, rebuild what is missing or invalid idempotently, retry with a memo, and report what cannot be rebuilt. It is the standard way to keep derived state correct, and it is safe here because rebuilding a cache never touches authored data.

Limits, so that healing never hides a problem:

- Never change an audit, a beep, a stage time, a note or a region.
- Never invent an original. A video whose original is not on this disk is reported ("original not on this disk"), never retried on a timer.
- Encodes are expensive: they wait their turn under the rules auto-sync already follows (never during a render, one sync at a time) and run through the job queue so they show in the jobs strip.
- Never upload a file that fails the check.

## Design

### 1. A derived file is published only when it decodes

- **Per-process temporary names.** `_web_partial` (and the trim's partial) become `<stem>.<pid>-<random>.partial.mp4`. Two writers can no longer share bytes. The match cleanup that sweeps `*.partial.mp4` keeps sweeping the new names.
- **A per-file lock** (`flock` on `<stem>.lock` beside the file) around transcode-and-publish, so the second writer waits and then finds a fresh, valid file instead of encoding it again.
- **A structural check before the rename**, `media_check.valid_mp4(path) -> MediaCheck`: parse the top-level boxes and the `moov` (no ffprobe on the hot path), require a `moov` whose children parse to their stated sizes, at least one video track with samples, and a duration within 0.5 s of the trim's. Pure Python, a few milliseconds per file. A failure deletes the partial, logs it, and records it in the job's result; it never replaces a good file.
- **`fresh_web_trim` and `trim_available` check validity**, cached by (path, size, mtime) so the parse runs once per file version, not per request. An invalid file is treated as absent: the next `_ensure_web_trim` cuts a new one.

### 2. Migrate older-keyed caches once

- `sync/migrate.py`, `migrate_legacy_caches(match_root) -> MigrationReport`: for every stage video whose new-keyed trim is absent and whose legacy-keyed trim is present, and where `_legacy_cache_fallback` resolves unambiguously (the same rule the read path uses today, so no behaviour changes for a match the desktop already reads correctly), rename the trim, its web rendition, its params sidecar and the audit WAV and peaks to the new keys. Renames within one folder; same bytes; no re-encode.
- Idempotent and recorded: `auto_sync.json` gains `cache_schema` (1 after this migration); a match at the current schema skips the scan. Run before `build_push_plan` in every sync and at match open on the desktop.
- Ambiguous registrations (a multi-stage single take whose legacy id cannot tell its stages apart) are left alone and reported; the reconciler re-cuts them under the new key from the original.
- Once every known install has run it (one release later), `_legacy_cache_fallback` and the read-side legacy leg are removed, as their comments ask.
- Hosted keeps its new-key-only rule. Old-keyed objects already in storage are deleted by the push's gc once their new-keyed counterpart is pushed (the same key rule the gc applies to renditions today).

### 3. Reconcile files, not flags

`plan_reconcile` gains a file-level pass. For each stage video with a confirmed beep and a stage time, the desired state is: a valid new-keyed trim and a valid new-keyed web rendition locally, and the rendition plus its params sidecar on hosted.

- Trim missing or invalid, original present: queue `trim` (and its rendition), regardless of `processed["trim"]`, which is set back to false so every reader agrees.
- Trim valid, rendition missing or invalid: queue a new `web_trim` step (just the transcode).
- Original missing: no step; report it per stage ("original not on this disk: <name>"). The match keeps syncing everything else.
- Beep not confirmed: no step, as today.

Steps go through the existing job kinds and the existing failure memo (`reconcile_failures`, keyed on every input), so a stage that fails is not retried until an input changes. `build_push_plan` excludes any file that fails `valid_mp4`, so an invalid rendition is never uploaded while its re-cut is pending.

### 4. Hosted says what it has, and asks the desktop for the rest

- When a desktop-origin mirror has no rendition for a video, `_video_clip_anchor` reports `kind: "missing"` instead of `"source"`, and `stream_video` answers 404 `not_synced` instead of redirecting to an original that was never uploaded. A hosted-native match keeps its source fallback (its originals are uploaded).
- The SPA's players map `missing` to one line in place of the video: "This video is not on splitsmith.app yet. Your desktop uploads it on its next sync." No spinner.
- Opening such a stage enqueues a desktop command (`COMMAND_KINDS` gains `heal_media`, spec 2026-09-28's queue), so the desktop rebuilds that stage on its next run even if its own reconcile has not reached it. The command runs the same reconcile steps for that stage only.

### 5. `splitsmith sync verify`

A CLI command (and a `/api/match/sync/verify` route for the SyncCard) that answers the question the 2026-10-12 check could not: will this match play on hosted?

- Locally: every stage video's trim and rendition, valid and new-keyed (`valid_mp4`).
- On hosted: the media keys the desktop expects are present, with sizes matching `sync_state.items`. Needs `GET /api/sync/matches/{id}/media` (a listing of the match's keys and sizes, sync-scoped like the rest of the sync API).
- Output per match: playable, healing (steps queued), or blocked (original missing, beep unconfirmed), with counts and stage names. Exit status non-zero when anything is blocked.

## What this does to the user's matches

No hand edits. After pieces 1 to 3 ship, the next sync of each match:

- renames 54 older-keyed stage videos' caches (Bofors 18, VADS 12, Jinglebell 4, Blacksmith 20);
- re-cuts 62 corrupt renditions from their trims (Stockholm 40, Oden 22);
- re-trims 36 stage videos from their originals (Tallmilan 2026 25, Tallmilan 2025 4, Blacksmith 7; the other 4 Blacksmith videos have unconfirmed beeps and wait for the user);
- uploads only files that pass the check, and gc removes the old-keyed objects.

`splitsmith sync verify` then reports two blocked stage videos (Tallmilan 2025, originals missing) and everything else playable.

## Tests (each fails on today's code)

- Two concurrent `_ensure_web_trim` calls on one trim, the second starting mid-encode: today both write one partial; after, both finish with one valid rendition and no interleaving (a fake runner that writes in chunks with a barrier).
- `valid_mp4` rejects the Stockholm stage 1 file's shape (a `moov` whose first child overruns it), a file with no video track, a truncated `mdat`; accepts every rendition of a fresh transcode.
- `fresh_web_trim` treats an invalid file as absent.
- `migrate_legacy_caches` renames a legacy-keyed trim and rendition so `_video_trim_anchor` (hosted's lookup) finds them, leaves an ambiguous single take alone, and is a no-op on a second run.
- `plan_reconcile` queues `trim` for a stage marked trimmed whose trim is gone, `web_trim` for an invalid rendition, nothing for a missing original (reported instead).
- `build_push_plan` leaves out an invalid rendition.
- Hosted: a mirror stage with no rendition reports `kind: "missing"` and `stream_video` 404s `not_synced`; a hosted-native match still falls back to the source.
- An end-to-end sync test (`tests/test_sync_integration.py`'s fixtures) where a match starts with one corrupt rendition, one legacy-keyed trim and one missing trim, and ends verify-clean.

## Order

1. Validity check and per-process partials with the lock (small; stops new corruption at once).
2. The migration (small; makes four matches playable on its own).
3. File-level reconcile and the push filter (medium; heals the rest).
4. `sync verify` and the media listing route (small).
5. Hosted `missing` and the desktop command (medium; makes the failure honest and lets a phone trigger the heal).

Each is its own PR with its tests; 1 to 3 together are what the user's matches need.

## Not in scope

- Re-encoding renditions to change their format (full range `yuvj420p`, start offsets): #1447 covers the rendition contract.
- Hosted-native matches (their media pipeline runs on the worker).
- Any change to how authored documents sync.
