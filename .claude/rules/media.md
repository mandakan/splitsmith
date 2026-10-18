---
paths:
  - "src/splitsmith/ui/audio.py"
  - "src/splitsmith/trim.py"
  - "src/splitsmith/derived.py"
  - "src/splitsmith/storage.py"
  - "src/splitsmith/media_check.py"
  - "src/splitsmith/match_trims.py"
  - "src/splitsmith/sync/plan.py"
  - "src/splitsmith/sync/push.py"
  - "src/splitsmith/sync/run.py"
  - "src/splitsmith/ui_static/src/lib/useScrubSource*"
  - "src/splitsmith/ui_static/src/lib/scrubSource*.ts"
  - "src/splitsmith/ui_static/src/lib/pending*.ts"
---

# Derived media: trims, web renditions, audit audio

## Two files per trim

- The audit trim (`trimmed/stage<N>_cam_<id>_trimmed.mp4`) is a
  full-resolution scrub cache (tens of Mbit/s, `moov` at the tail): right for
  the audit screen, wrong to stream from R2.
- Every trim has a `_web.mp4` beside it (720p, faststart, `WebTrimConfig`), cut
  *from the trim* by `trim.transcode_web_trim` so the two share a window and a
  beep anchor. `ui/audio._ensure_web_trim` cuts it whenever the trim is cut or
  found cached, best-effort: **a failed transcode never fails the trim job**.
  `sync.run.backfill_web_trims` cuts missing ones before every push, so a match
  trimmed before renditions existed is fixed by its next sync; don't add a
  re-trim path for it.
- The GOP stays 30: GOP 15 cost 35-47 % more bytes for ~10 ms of seek.

## Stream kinds

- `_video_clip_anchor` reports `kind: "web"` only when the byte path is a
  presigned redirect and the object exists; locally the clip anchor (Results,
  Coach) stays `trim`
  (`test_get_coach_entry_kind_stays_trim_locally_with_web_file`).
- On `stream_video` the kinds mean one thing in both modes:
  - `web`: the rendition, else the trim, else the source, **never a 404** (what
    Results, Coach and Compare rely on);
  - `trim`: never substitutes the rendition;
  - `scrub` (the Audit players' pin): the fresh rendition, else the trim, else
    404, **never the source**. A re-cut deletes both while it encodes, and a
    pinned player must error and remount, not play the source under trim
    offsets.
- "Fresh" is one rule, `audio.fresh_rendition` (non-empty, not older than the
  trim, equal timestamps fresh; on a mirror the rendition alone), fed by local
  files, `storage.stat`, and the request's `StoragePresence` listing (which keeps
  each object's size and `last_modified`) for the payload's `scrub_version`.
- `lib/useScrubSource` asks for `scrub` when the video dict carries
  `scrub_version`, unless `GlobalPrefs.full_res_scrub` is on (local only) or
  that rendition (path + version) already errored on the page (a 4K trim stalls
  software decode in Chromium's low-end mode).
- Hosted Compare prefers `trimmed/<...>_web.mp4` over the lossless export.

## Web-only mirrors (spec 2026-09-27 v1.1)

A desktop mirror has no `_trimmed.mp4` on R2 by default, only `_web.mp4` and
`.params.json`. On a mirror only (`_is_mirror()`, origin `desktop`): `kind=trim`
and `kind=auto` redirect to the rendition when the full trim is absent; the
clip anchor comes from the pushed params (`try_pull_web_trim` +
`trim_pre_buffer_seconds_for`); the audit WAV is the one the desktop pushed,
else cut from the rendition by a job. **A hosted-native match keeps "`kind=trim`
never substitutes"**
(`test_hosted_native_match_without_a_trim_still_404s_kind_trim`).

## Requests read derived media; jobs make it

Spec `2026-10-14-derived-media-off-the-request-path-design`. Concurrent requests
each cutting audio, one from a rendition another was still downloading, served
a phone a short WAV.

- **A request never runs ffmpeg, decodes a source or mirrors a large file.** The
  audio and peaks routes, the beep snap and the raw-file peaks read through
  `ui/audio.published_audit_audio` / `published_video_audio` /
  `published_take_audio`. On a miss `_audio_pending` queues one `derive_audio`
  job (deduplicated on kind, stage, shooter, video) and answers 202
  `{pending, active_job, job_id}`; the SPA's `lib/pending` (`whenReady`) asks
  again and the page says "Preparing audio".
- **A new route that needs a derived file reads it and queues its job; it never
  calls an `ensure_*` producer.**
- The trim job also cuts the audit WAV (`ensure_video_audit_wav`), and the
  desktop push carries it after the rendition (`sync/plan.audit_wav_for`, the
  `audio/` key in `_SYNC_MEDIA_KEY_RE`, never deletable). A stored WAV is current
  only when it is not older than the clip it was cut from.
- Every derived file is written through `splitsmith.derived`: a unique partial,
  a check (`valid_mp4`, `wav_problem` against the clip's `mvhd` length), a
  rename. A lock is opt-in (`produce(lock=True)`, `derived.locked`) for writers
  two local processes run at once; the queue is what coordinates.
- The beep preview clip stays synchronous (one clip per scrubbed `t`) but
  publishes whole.
