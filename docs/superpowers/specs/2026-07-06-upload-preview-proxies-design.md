# Preview/proxy videos for uploads (fast stage-assignment streaming)

Date: 2026-07-06
Issue: #561

Approved decisions:

- Generate a proxy on **every upload** (single-shot and multipart-complete),
  before/independent of project attach.
- **Hosted only** (local mode streams from disk; the slowness is R2-cold-fetch
  specific).
- Ingest player **plays the original with a "Proxy generating" badge** as a
  fallback, then transparently swaps to the proxy once ready.
- The streamed source is decided **server-side per request**, so the SPA never
  relies on a proxy-status snapshot taken at session start. No real-time
  signaling required.

## Goal

In hosted mode, flipping through clips in the Ingest stage-assignment view is
slow because it streams the full-resolution original from object storage (tens
of MB per 2-minute clip; cold fetch + full-res decode on every clip switch).
Generate a lightweight 480p, keyframe-dense proxy for every uploaded raw video
and stream that in the assignment/scrub UI, keeping the original only for
trim/export.

Non-goals (explicitly out of scope):

- Net-new SSE/WebSocket push to the browser. The SPA is poll-based everywhere;
  the only event-stream endpoint (`/api/workers/channel`) is worker-only.
  Correct streaming does not need it (the source is chosen server-side per
  request, below); the badge refreshes on ordinary project refetches.
- Presigned-GET direct-from-R2 for originals (does not fix full-res decode or
  keyframe seek latency, which is the actual pain).
- Local mode (no proxy job, no proxy stream; `proxy_ready` reports true so no
  badge and the source streams as today).
- Proxying trim/export playback. Those keep using the original / audit trim.
- Regenerating proxies for videos uploaded before this feature ships
  (backfill). Old clips simply fall back to the original, same as a
  still-generating proxy.

## Why decoupled from the project

"On every upload" means the proxy job runs before a `RawVideo` exists in any
`MatchProject` state doc, so there is nowhere to persist a per-video
`proxy_ready` flag at generation time. The job therefore keys purely on the
**raw storage path + tenant prefix** and writes to a deterministic proxy key.
Readiness is derived at read time from object existence, not stored.

## Storage key convention

Raw uploads land at `raw/<name>.<ext>` (relative to the tenant prefix
`users/<user_id>/`). The proxy is written to:

    raw_proxy/<name>.mp4

Always `.mp4` regardless of source container. A pure helper
`proxy_key_for(raw_path: str) -> str` in the new proxy module does the mapping
(`raw/` -> `raw_proxy/`, force `.mp4`). Proxies live under the same tenant
prefix, so per-tenant isolation is unchanged.

## New module: `src/splitsmith/proxy.py`

Pure ffmpeg wrapper, mirroring `trim.py`'s shape (paths + config in, shells
ffmpeg via an injected runner, raises on failure, no storage I/O):

```python
def transcode_proxy(
    input_path: Path,
    output_path: Path,
    config: ProxyConfig,
    *,
    ffmpeg_binary: str,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> None: ...
```

- Video: `libx264`, `-crf {config.crf}`, `-preset {config.preset}`,
  `-vf scale=-2:{config.height}`, `-pix_fmt yuv420p`.
- Keyframe density (the point of the feature): `-g {config.gop}`
  `-keyint_min {config.gop}` `-sc_threshold 0` for uniform, seek-friendly GOPs.
- Audio: `-c:a aac -b:a {config.audio_bitrate}`.
- `-movflags +faststart` so the moov atom is front-loaded for progressive play.
- Raises `ProxyError` (module-local, like `FFmpegError`/`ThumbnailError`).

`proxy_key_for` also lives here.

## Config: `ProxyConfig` in `config.py`

Pydantic model, tunable per architecture rule 4 (config is data):

| field           | default     | note                                  |
|-----------------|-------------|---------------------------------------|
| `height`        | `480`       | 480p; width auto-even via `scale=-2`  |
| `crf`           | `30`        | low bitrate, quality fine for scrub   |
| `preset`        | `veryfast`  | fast worker-side transcode            |
| `gop`           | `15`        | keyframe every ~0.5s @30fps           |
| `audio_bitrate` | `"96k"`     | audio present for context, low weight |
| `video_codec`   | `"libx264"` | portable; not videotoolbox-gated      |

## Worker job: `generate_proxy`

Body `_run_generate_proxy(handle, *, raw_path)` in `register_job_bodies`
(`server.py` ~2807), registered via
`state.jobs.bodies.register("generate_proxy", _run_generate_proxy)`.

1. `storage = state.storage`; if `None` (local), set result `{skipped:
   "no-storage"}` and return - hosted-only guard.
2. `proxy_key = proxy_key_for(raw_path)`. If `storage.exists(proxy_key)`, set
   result `{proxy_key, skipped: "exists"}` and return (idempotent).
3. Download `raw_path` to an ephemeral temp file; `handle.update` progress.
   (Mirror-to-disk is what trim/shot_detect already do; ffmpeg wants a
   seekable input.)
4. `transcode_proxy(tmp_in, tmp_out, ProxyConfig(), ffmpeg_binary=
   process_runtime().ffmpeg_binary, runner=_cancellable_runner(handle))`
   inside a `handle.timer.phase("transcode")`.
5. `storage.upload_stream(proxy_key, open(tmp_out, "rb"))`.
6. `handle.set_result({video_id, proxy_key, size_bytes})`; clean temps.

`_rehydrate_args` (`queue.py`) needs no change: `raw_path` is a plain string.
The job carries `video_id` (derived from `raw_path` via the existing
path->video_id helper) for dedupe and so the Jobs rail can label it per clip;
no `match_id` (avoids the cross-process match-resolution constraint).

## Dispatch on upload

Both hosted upload completion points enqueue the job when `state.storage` is
present:

- `upload_raw_video` (single-shot `POST /api/me/raw/upload`) - after the
  stream lands and hash is known.
- multipart `POST /api/me/raw/upload/multipart/complete` - after
  `complete_multipart_upload`.

Each: `find_active(kind="generate_proxy", video_id=vid)` dedupe, then
`await state.jobs.submit(kind="generate_proxy", video_id=vid, args={"raw_path":
key})`. A helper `_dispatch_proxy_job(key)` avoids duplicating the two call
sites. Dispatch failures are logged but never fail the upload (the proxy is an
optimization; the original still streams).

## Streaming: `kind=proxy` with server-side fallback

Add `proxy` to the `kind` set of `stream_video`
(`GET /api/shooters/{slug}/videos/stream`) and its match-scoped alias
(`GET /api/match/shooters/{slug}/videos/stream`):

- Validate `path` against a registered project video (unchanged).
- For `kind=proxy`: compute `proxy_key_for(video.path)`; if
  `storage.exists(proxy_key)`, mirror it to disk and `FileResponse` it
  (Range/206 handled by Starlette as today). If it does **not** exist, fall
  back to the existing source path (identical to `kind=auto`/`source`).

Server-side fallback means playback is always correct regardless of the
`proxy_ready` flag: the source is chosen per request, at request time. Because
the ingest `<video key={video.path}>` remounts on every clip selection, each
clip open issues a fresh request and gets the current best source - the SPA
never acts on a session-start snapshot. The `proxy_ready` flag drives only the
cosmetic badge.

## Readiness signal: one LIST, no persisted state

When the project is serialized for the ingest view (the `getProject` /
match-project payload), in hosted mode do a single
`storage.list("raw_proxy/")` -> set of existing keys, and set a new
`proxy_ready: bool` on each `StageVideo` (`proxy_key_for(video.path) in set`).
Local mode (no storage) -> `proxy_ready = True` for all (no badge, source
streams). One LIST per project load, not N HEADs; no new DB column, no
state-doc write.

## Frontend

**Types (`ui_static/src/lib/api.ts`)**

- Add `proxy_ready?: boolean` to `StageVideo`.
- Add an optional `kind` param to `shooterVideoStreamUrl(slug, path, kind?)`
  (mirrors the existing `kind` on `videoStreamUrl`), producing
  `.../videos/stream?path=...&kind=proxy`.

**`ClipDetail.tsx`**

- `<video src>` uses the `kind=proxy` URL (server falls back to source when the
  proxy is absent, so this is safe even before generation finishes).
- When `!clip.video.proxy_ready`, render a `StatusPill` "Proxy generating"
  (in-progress amber tone) in the existing label row under the player. When
  `proxy_ready`, no badge.
- The element `key` is `${video.path}:${proxyReadyNonce}` so it remounts both on
  clip switch (always) and when a refetch flips this clip's `proxy_ready` while
  it is being viewed, so the source swaps to the ready proxy without navigation.

**Picking up newly ready proxies (no signaling)**

Streaming correctness needs no watcher: the source is server-chosen per request
and the element remounts per clip selection, so simply navigating to (or back
to) a clip streams the proxy once it exists.

The only stale surface is the cosmetic badge and the case where the user *stays
on* a clip whose proxy finishes mid-view. Handle both with a light periodic
refetch in the ingest surface (`ReviewLayout`/`Ingest.tsx`): while any clip in
the model has `proxy_ready === false`, refetch `api.getProject(slug)` on an
interval (~5s) and stop once none are pending. When a clip the user is
currently viewing flips to `proxy_ready`, bump a reload nonce mixed into the
`<video>` `key` so it remounts and swaps source to the now-ready proxy. No
per-job `pollJob` orchestration, no SSE.

**Jobs list (`components/Jobs.tsx`)**

- Add `generate_proxy` to `KIND_LABEL` (e.g. "Generating preview") and
  `KIND_ICON` (a `lucide-react` icon, e.g. `Film`/`Clapperboard`). Proxy jobs
  then appear in the global Jobs rail like every other kind. Uploading N clips
  produces N proxy jobs there, as intended.

## Error handling summary

| case                                  | behavior                                    |
|---------------------------------------|---------------------------------------------|
| proxy not yet generated               | `kind=proxy` serves the source; badge shows |
| transcode fails                       | job -> failed (in Jobs rail); source still streams; badge stays until a later successful regen (none auto) |
| dispatch fails at upload              | logged; upload still succeeds; no proxy     |
| local mode                            | no job, `proxy_ready=true`, source streams  |
| proxy exists but source video removed | proxy orphaned; cleaned with the raw delete (see below) |

## Cleanup

`DELETE /api/me/raw/{filename}` also deletes `proxy_key_for` for that file
(best-effort; missing proxy is not an error), so proxies do not outlive their
originals.

## Testing

- `proxy_key_for`: `raw/GX01.MP4` -> `raw_proxy/GX01.mp4`, extension forced,
  prefix swapped; rejects paths outside `raw/`.
- `transcode_proxy` (unit, mock runner per CLAUDE.md - no shell-out): asserts
  the ffmpeg argv contains scale, `-g`/`-keyint_min`/`-sc_threshold 0`, crf,
  faststart, and the right in/out paths; raises `ProxyError` on non-zero.
- `transcode_proxy` (`@pytest.mark.integration`, real ffmpeg on a short
  fixture): output is a valid, smaller mp4 at ~480p with denser keyframes than
  the source.
- Job body (hosted test app, moto/MinIO storage): upload -> job runs ->
  `raw_proxy/...` object exists; second run is a no-op (`skipped: "exists"`);
  no-storage path returns skipped.
- Dispatch: upload endpoints enqueue `generate_proxy` once per upload (mocked
  submit); dedupe prevents a second active job for the same video.
- Stream: `kind=proxy` serves the proxy when present, falls back to source when
  absent; path validation still rejects unregistered paths.
- Readiness: `getProject` sets `proxy_ready` from the `raw_proxy/` listing;
  local mode reports true.
- SPA: typecheck + build + scoped eslint (no test runner in ui_static).
- Gates before PR: ruff + black + pytest locally; `pytest -m docker` (this
  touches storage/job paths that the docker smoke covers). No migration, so no
  schema smoke needed beyond that.
