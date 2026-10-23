---
paths:
  - "src/splitsmith/ui/audio.py"
  - "src/splitsmith/trim.py"
  - "src/splitsmith/derived.py"
  - "src/splitsmith/storage.py"
  - "src/splitsmith/source_cache.py"
  - "src/splitsmith/media_check.py"
  - "src/splitsmith/proxy.py"
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

## Streaming a source to ffmpeg

- The hosted `generate_proxy` job hands ffmpeg a presigned GET URL for the raw
  object (`_proxy_source_url`, six-hour expiry) instead of downloading it
  first; a trailing `moov` costs one small range read, not a full pass.
- **A URL read is trusted only when ffmpeg's stderr is empty.** A connection
  that drops and cannot resume, or a range request that reads EOF, exits 0
  with a truncated or damaged output; `transcode_proxy` raises
  `ProxySourceReadError` and the job transcodes from a download instead
  (`test_transcode_proxy_from_url_raises_when_the_stream_cannot_resume`).
- `HTTP_INPUT_ARGS` carries `-multiple_requests 1`: without it ffmpeg may
  reuse a socket the server is closing and read EOF on the next seek
  (`test_transcode_proxy_from_url_matches_the_local_file` fails without it).
- A presigned URL is a credential: it never reaches an error message or log.

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
- A WAV cut from a trim goes through `_extract_audio`'s `aresample=async=1`
  (`_FILL_GAPS`): two clips joined with ffmpeg leave a hole in the audio
  timestamps, and a plain decode closes it, which made the WAV short of the
  clip (`wav_problem` refuses it) and put every sample after the join early
  (`tests/test_audio_gap_fill.py`). The full-source WAVs (`_run_audio_ffmpeg`)
  do not have it yet: beep detection reads those, so changing them is a
  detection change.
- A trim whose source was repaired in place is re-cut by the clip sheet's
  Advanced "Re-cut trim" (`POST .../trim?force=true`, `force` through
  `ensure_video_audit_trim`). The cache otherwise keys on the source's mtime
  and params only.
- **A storage mirror's mtime is the object's version, never the time this disk
  fetched or used it.** `resolve_video_path` stamps a download with the
  object's `last_modified` (only when a stat before and after the stream
  agree; else the download time stands) and a cache hit bumps only the atime
  (the LRU sweep reads the later of the two). Every derived cache is "not older than its
  source", so a bumped mtime made each later job on a worker re-cut its trim
  (`tests/test_hosted_trim_reuse.py`).
- Beep detection saves the beep before any trim work and chains the trim as a
  `trim` job, skipped only when the trim's params sidecar matches the current
  beep, stage time and buffers (`audio.trim_params_current`). Overrides,
  confirms and re-detections adopt a running trim (they dedupe on the trim
  kind), so a trim whose beep moved while it cut never publishes the cut
  (`ensure_video_audit_trim(still_current=)`) and queues a trim for the current
  beep itself. A moved beep drops the old trim locally and, from a job body,
  in storage (`invalidate_video_audit_trim(stored=True)`); a hosted reader
  keys on existence. `tests/ui/test_detect_beep_persist_early.py`.
- The beep preview clip stays synchronous (one clip per scrubbed `t`) but
  publishes whole.
