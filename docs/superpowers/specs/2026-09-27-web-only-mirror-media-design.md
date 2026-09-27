# Web-only mirror media (auto-sync v1.1)

Date: 2026-09-27
Status: implemented
Builds on: `2026-09-27-auto-sync-reconciler-design.md` (v1, PR #1068)

## Goal

Stop storing full-resolution audit trims in R2 for desktop mirrors. A
mirror's hosted surfaces (Coach, Results, Compare, both audit screens)
play and waveform from the 720p `_web.mp4` rendition plus the pushed
`.params.json` sidecar. This is the storage half of "the idle desktop
does the work, hosted stays thin".

Success: a mirror synced with full media off has no `_trimmed.mp4`
objects in R2, and every hosted screen that played its trims before still
plays, with markers and waveform on the same timeline.

## Decisions (from the conversation)

- Default web-only for every push, manual or automatic. A per-match
  "Full media" switch on SyncCard uploads full trims for that match.
- Full trims already in R2 for a mirror are deleted by the next push while
  full media is off, once that clip's rendition is on R2.

## Current state (verified)

- `sync.plan.build_push_plan` uploads, per trimmed clip, `_trimmed.mp4`,
  its `.params.json` and `_web.mp4`.
- The push's `gc` phase deletes only `beep_review/` keys, and hosted
  `POST .../media/delete` refuses anything else (422
  "delete is beep_review-only").
- `_video_trim_anchor` (`ui/server.py`) reaches the web rendition only
  after it has found a full trim; with none it returns `source`, which a
  mirror does not have. Coach, Results and Compare read their clip kind
  from it.
- `stream_video` on hosted: `kind=web` redirects to the rendition, but
  `kind=auto` and `kind=trim` check only the full trim; `auto` then falls
  to the (absent) source and `trim` returns 404. Compare's primary tile
  asks `auto`; desktop-width Audit and MobileAudit (once peaks report a
  trim) ask `trim`.
- `audio.ensure_audit_audio` extracts the audit WAV from the full trim
  (pulled from storage), else from the source. Mirrors upload neither
  once the full trim is gone, so peaks and audio break.
- `_web.mp4` is `trim.transcode_web_trim` of the whole trim file: same
  window and timeline. Differences: GOP 30 vs 15 (scrubbing settles
  slower, stays frame-accurate), audio AAC 128k vs stream copy.
- `.params.json` carries `beep_time`, `stage_time_seconds`,
  `pre_buffer_seconds`, `post_buffer_seconds`: all the anchor needs.
- Hosted jobs that would need the full trim (shot detect, compare export,
  export preview) already require `EDIT`, which mirrors lack.

## Design

### 1. Push plan

`build_push_plan(match_root, *, sync_state, full_media: bool = False)`.
With `full_media` off the plan skips `*_trimmed.mp4` and keeps
`.params.json`, `_web.mp4` and beep snippets. A clip with no rendition
yet (the transcode failed) still uploads its full trim, so hosted never
has neither; the rendition backfill runs before planning, so this is the
failure case only.

`run_sync` / `run_push` read `full_media` from `auto_sync.json`
(`AutoSyncPrefs.full_media: bool = False`) and pass it through. The
status route's `pending_media` uses the same flag, so web-only never
reads as "N files changed" forever.

### 2. Removing full trims from R2

In the push's `gc` phase, with `full_media` off: every key in
`sync_state.items` that ends `_trimmed.mp4` is deleted from hosted when
the matching `_web.mp4` key is also in `sync_state.items` (pushed). Same
failure rule as the snippet gc: a failed delete stays in `sync_state` and
is retried next push; gc never fails a push. On success the key leaves
`sync_state.items`, so turning full media back on re-uploads it.

Hosted `POST /api/sync/matches/{id}/media/delete` widens from
beep_review-only to beep_review keys plus keys matching exactly
`.../trimmed/<name>_trimmed.mp4`. `_web.mp4` and `.params.json` stay
undeletable through this route. It remains mirror-only (`_resolve_mirror`).

### 3. Hosted fallback (mirrors only)

"Mirror" is `current_match_origin == "desktop"`. Hosted-native matches
keep today's rule that `kind=trim` never substitutes the rendition.

- **Anchor.** `_video_trim_anchor`: when no local or storage full trim
  exists, on a mirror, and `web_trim_available`, return
  `(min(beep_time, pre_buffer), "trim", <trim path>)` with `pre_buffer`
  from the pushed `.params.json` (pulled from storage when not local).
  `_video_clip_anchor` then promotes it to `web` exactly as it does today.
- **Stream.** `stream_video` hosted branch: on a mirror, when the full
  trim object is absent, `kind=trim` and `kind=auto` return the rendition
  redirect (`_hosted_web_redirect`) before any 404 or source fallback.
- **Audio and peaks.** `ensure_audit_audio`: when no full trim can be
  pulled, on a mirror, pull `_web.mp4` and extract the audit WAV from it
  (the same `_extract_audio` call). Cached as today. The peaks response's
  `trimmed` flag stays true: the timeline is the trim's.

### 4. UI

SyncCard gains a "Full media" switch (a `Segmented` Web / Full, shown
once the match has synced). While removals are pending (full media off,
full-trim keys still in `sync_state.items`), the status line says
"N full trims will be removed from hosted on the next sync". New fields
on `GET/PUT /api/match/sync/auto`: `full_media`, `full_trims_on_hosted`.

## Out of scope

- Pushing precomputed peaks from the desktop (hosted computes and caches
  them from the rendition's audio).
- A shorter GOP on the rendition. Revisit if phone scrubbing feels slow.
- Hosted-native matches: unchanged.

## Testing

Each test must fail against the pre-change code.

- Plan: `full_media` off skips `_trimmed.mp4`, keeps params and web; a
  clip without a rendition keeps its full trim; on includes it.
- GC: deletes a pushed full trim only when its rendition key is recorded;
  a failed delete stays recorded; a full-media push after removal
  re-uploads.
- Hosted delete route: accepts `*_trimmed.mp4`, refuses `_web.mp4` and
  `.params.json` (422), refuses a native match (409).
- Hosted mirror with only `_web.mp4` + params in moto S3: Coach payload
  kind is `web` with the params' anchor; `stream_video` `kind=trim` and
  `kind=auto` redirect to the rendition; stage peaks return data.
- Guard: a hosted-native match with no trim still 404s `kind=trim`.
- Status: `pending_media` with full media off does not count full trims.
- End to end: extend
  `test_phone_beep_confirm_reaches_the_desktop_reconciler`'s harness (or a
  sibling test) so a web-only push leaves no `_trimmed.mp4` in moto S3 and
  the mirror's Coach payload names `web`.
