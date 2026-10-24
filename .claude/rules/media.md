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
  - "src/splitsmith/compare/project_loader.py"
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
- **"This cut is current" has one definition**, `trim.audit_trim_matches`
  (on disk) and `audio.audit_trim_current` (on disk or stored, where a
  web-only project's rendition stands in the trim's place): the `.params.json`
  equals `trim.audit_trim_params` for the beep, stage time and buffers, and the
  cut is not older than the source when the source is on this disk. The trim
  job's cache hit, `trim_params_current`, `stored_trim_current` and the compare
  grid all read it, so a grid tile never uses a trim the trim job would re-cut.
- **A compare-grid tile with no exported trim** (`project_loader`, #1681):
  locally (desktop, CLI) it is cut from the audit trim on disk while that is
  current (`_current_audit_trim`); a stale one, or one with no sidecar, counts
  as missing. A storage-backed project keeps no full trim, so the hosted grid
  **job** cuts the stage trim from the source (`server._cut_stage_from_source`:
  `trim.trim_video` lossless, the export's window), one tile at a time, and on
  a memory-backed disk drops each source after its cut; only for a confirmed
  beep and a stage time. The request checks those and the source's listing and
  downloads nothing. The `_web.mp4` is never a render input. Pinned by
  `test_a_stale_audit_trim_counts_as_missing`,
  `test_hosted_grid_cuts_a_tile_with_no_export_from_its_source` and
  `test_compare_audit_trim_integration.py`.

## Storage holds no full trim (web-only)

- **Every storage-backed project is web-only** (`audio.stores_web_only`): its
  storage holds a trim's params sidecar, rendition and audit WAV, never the
  full trim. A desktop mirror by design; a hosted-native match because on 4K
  footage the trim is about five times the source's bytes for its window,
  nothing but the job that cut it reads it, and every byte a worker writes to
  R2 is billed GCP egress. A legacy full trim already
  in storage is still served while it is there.
- The cut pushes the params, then the rendition (`_publish_web_trim`); only a
  rendition that fails to cut puts the full trim up instead, so the stage
  keeps a playable clip (`test_a_failed_rendition_pushes_the_trim_instead`).
- **A re-cut drops storage's copies before it encodes** (`_drop_stored_trim`
  in `ensure_video_audit_trim`): with no trim to date it against, a stored
  rendition reads as current, and the rendition step would pull the old one
  back as the new cut's
  (`test_a_re_cut_drops_the_stored_rendition_before_it_encodes`).
- `fresh_rendition` dates the rendition against a trim whenever one is
  there, and takes it alone only when there is none and the project is
  web-only.
- A job that finds the products current in storage
  (`audio.stored_trim_current`: params for the current beep, the rendition,
  the WAV not older than it) needs neither the source nor a cut: the trim job
  skips both (a confirm chains one over a current cut), and shot detection
  reads the published WAV (`tests/test_hosted_web_only_trims.py`). Voter E
  reads the source, so it keeps the old path.
- **A memory-backed worker drops the full trim once its products are
  published** (`audio.release_local_trim`, Cloud Run by
  `source_cache.memory_backed_disk`): the trim job after its WAV, shot
  detection after it prepared its audio, the audit-audio job after its cut.
  Its disk is memory, and no later job reads the trim. A box with a real disk
  keeps it to reuse (`test_a_memory_backed_worker_keeps_no_full_trim_after_the_job`).
- **A hosted-native WAV is never cut from the rendition**: shot detection
  reads it, and the rendition's audio is re-encoded. A `derive_audio` job
  that finds the rendition but no trim cuts the trim again from the source;
  only a mirror, which has no source, extracts from the rendition.

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
- A presigned URL is a credential: it never reaches an error message or log
  (`proxy.redact_urls`).
- **The trim job cuts from a URL too** (`MatchProject.source_url`, then
  `ensure_video_audit_trim` with a `str` source). With `-ss` before `-i`
  ffmpeg seeks by range request and reads about the window's bytes, and
  nothing lands on the worker's disk: on Cloud Run that disk is memory, and
  a downloaded source plus the cut ran workers out of it. `trim_video` raises
  `SourceReadError` on any stderr from a URL read, and the job cuts again
  from a download (`meta.source_read`: `url`, `url_failed`, `download`). A
  source already mirrored on this disk is read from disk; `source_url`
  returns `None` for it. With no source on the disk, the trim's freshness
  check has nothing to date it against, as for any hosted source not
  mirrored here (`tests/test_trim_streamed_source.py`).
- **So does a hosted export's lossless cut**, of every camera
  (`server._export_source`: a URL plus its download fallback; the exporter's
  `_cut_lossless` cuts again from the download on `SourceReadError`). The
  report never names a URL as the stage's video
  (`tests/test_export_streamed_source.py`).

## Stream kinds

- `_video_clip_anchor` reports `kind: "web"` only when the byte path is a
  presigned redirect and the object exists; locally the clip anchor (Results,
  Coach) stays `trim`
  (`test_get_coach_entry_kind_stays_trim_locally_with_web_file`).
- On `stream_video` the kinds mean one thing in both modes:
  - `web`: the rendition, else the trim, else the source, **never a 404** (what
    Results, Coach and Compare rely on);
  - `trim`: never substitutes the rendition, except on a web-only project
    with no trim in storage, where the rendition is the trim;
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

## Web-only mirrors and native matches (spec 2026-09-27 v1.1)

A desktop mirror has no `_trimmed.mp4` on R2 by default, only `_web.mp4` and
`.params.json`; a hosted-native match is the same since the web-only change
above. On either (`stores_web_only`): `kind=trim` and `kind=auto` redirect to
the rendition when the full trim is absent, and the clip anchor comes from the
stored params (`try_pull_web_trim` + `trim_pre_buffer_seconds_for`)
(`test_hosted_native_match_without_a_trim_serves_the_rendition_for_kind_trim`).
A mirror's audit WAV is the one the desktop pushed, else cut from the rendition
by a job; that fallback is the mirror's alone (`_is_mirror()`).

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
