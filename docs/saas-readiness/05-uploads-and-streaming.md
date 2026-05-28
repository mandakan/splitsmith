# 05 -- Uploads and streaming

This doc defines **how bytes get from the user into the hosted
service** (audio + opt-in raw video) and **how they come back out**
(stream-on-demand for playback). It pins the upload protocol,
signed-URL pattern, and the v1 vs v2 scope on raw upload.

The top constraint is **don't move heavy data twice**. If the user's
audio is in their browser and we're going to run Tier 2 detection on
it, the audio goes to R2 once and the worker reads it from R2 -- not
"upload to API server, API server forwards to R2, worker downloads
again".

## Upload protocol: tus

We use the [tus.io](https://tus.io/) resumable-upload protocol via
[tus-js-client](https://github.com/tus/tus-js-client) on the browser
and [tus-py-server](https://github.com/tus-project) on the server.

Why tus over a custom multipart scheme:

- **Resume after network drop.** Critical for raw-video uploads
  (5-30 GB on home internet). Tus persists upload state on the
  server and lets the client resume from the last byte received.
- **Battle-tested.** Used by Vimeo, Cloudflare Stream, etc. We don't
  reinvent the upload state machine.
- **Standardised.** Client and server libraries exist for every
  major language; if we want a CLI uploader later, one already
  exists.
- **S3 multipart compatibility.** The tus-py-server has an S3 store
  that uses S3's multipart upload API under the hood, so the bytes
  go straight to R2 without buffering on our API server.

Why not direct presigned-PUT to R2:

- Presigned PUT works for small files but doesn't resume. The user
  loses their ~10 GB raw video when their Wi-Fi blips at 90%.
- Multipart upload via presigned URLs is doable but means
  reimplementing chunk tracking, retry, and abort logic. Tus is
  exactly that, packaged.

### Upload flow

```
Browser                          API server                   R2
  |                                  |                          |
  |-- POST /api/v1/uploads --------->|                          |
  |   { kind: 'audio',               |                          |
  |     project_id, stage_n,         |                          |
  |     size_bytes, sha256 }         |                          |
  |                                  |-- create multipart ----->|
  |                                  |<- upload_id -------------|
  |<-- { upload_id,                 -|                          |
  |     tus_endpoint } -------------|                          |
  |                                  |                          |
  |-- PATCH tus_endpoint ------------|------ stream chunks --->|
  |   (resumable; tus protocol)      |   (S3 multipart parts)   |
  |        ...                       |                          |
  |-- PATCH tus_endpoint (last) -----|------ complete -------->|
  |                                  |<- etag ------------------|
  |<-- 204 No Content ---------------|                          |
  |                                  |                          |
  |-- POST /api/v1/jobs ------------>|                          |
  |   { project_id, stage_n,         |                          |
  |     upload_id }                  |                          |
  |                                  |-- enqueue queue job      |
  |<-- { job_id } ------------------|                          |
```

### Upload session table

```sql
CREATE TABLE upload_sessions (
  id              TEXT PRIMARY KEY,        -- ULID, 'upload_id' in API
  user_id         TEXT NOT NULL REFERENCES users(id),
  project_id      TEXT REFERENCES projects(id),  -- nullable; new projects
  kind            TEXT NOT NULL,           -- 'audio' | 'raw_video' | 'project_tarball'
  stage_number    INT,                     -- nullable; tarballs span stages
  size_bytes      BIGINT NOT NULL,
  sha256          TEXT,                    -- declared by client
  storage_path    TEXT NOT NULL,           -- where the bytes will land
  s3_upload_id    TEXT,                    -- the underlying multipart id
  bytes_received  BIGINT NOT NULL DEFAULT 0,
  status          TEXT NOT NULL,           -- 'in_progress' | 'completed' | 'aborted'
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at    TIMESTAMPTZ,
  expires_at      TIMESTAMPTZ NOT NULL     -- 24h from creation; tus aborts after
);
```

The `expires_at` lets us abort stalled uploads -- a daily cron calls
`AbortMultipartUpload` on R2 for any session past its expiry. R2's
own "expire incomplete multiparts after 7 days" lifecycle rule is the
backstop.

### Hash verification

The client declares a sha256 in the create-upload request. After the
last chunk lands, the server verifies (we have the bytes anyway --
streaming sha256 during the multipart-complete callback). Mismatch =>
mark the upload aborted, return 422 to the client. Client re-uploads.

This catches:
- Bytes corrupted in transit (rare with TLS but happens).
- Browser/desktop bug encoding the audio differently than declared.

## Audio upload (Tier 2 default)

The browser extracts audio in WebAudio:

```javascript
// Pseudo-code
const ctx = new OfflineAudioContext(1, video.duration * 16000, 16000);
const audioBuffer = await ctx.decodeAudioData(videoBytes);
// downmix to mono if needed; resample to 16k
const wavBytes = encodeWav(audioBuffer.getChannelData(0), 16000);
```

The result is ~5 MB per stage at 16 kHz mono 16-bit, regardless of
the source video's resolution. This is the smallest input that
preserves the signal CLAP/PANN need.

Audio uploads land at:

```
projects/<project_id>/shooters/<slug>/stage_<n>/audio.wav
```

After upload completes, the client posts to `/api/v1/jobs` to enqueue
detection (see 04). The job reads from this exact path.

The audio file is **kept** after detection -- it's the input the user
re-runs detection with if they tweak the consensus threshold or audit
the result. We never re-extract from raw if audio.wav exists.

## Raw video upload (v2; opt-in)

**Not in v1.** Documented here so the v1 abstractions don't preclude
it.

Raw upload uses the same tus flow, with `kind='raw_video'` and a
larger `expires_at` window (72h instead of 24h, since 30 GB on home
internet can take hours). Lands at:

```
projects/<project_id>/raw/<original_filename>.<ext>
```

The browser shows a clear consent dialog before starting:

> Upload your raw video to Splitsmith?
>
> This makes the video available across your devices and to
> squadmates you share with. We store it in our EU datacenter
> (Cloudflare R2 - Frankfurt). You can delete the upload at any
> time from the project settings.
>
> The audio extracted from this video will be uploaded regardless --
> this option additionally uploads the full video.

Once raw is uploaded, Tier 3 becomes available for that project. Tier
2 (audio-only) remains the default detection tier; Tier 3 is offered
when the user explicitly wants the worker to re-run audio extraction
or wants to verify the trim alignments server-side.

### Raw video state per project

The `match.json` carries a `raw_videos` array:

```json
{
  "raw_videos": [
    {
      "original_filename": "GH010023.mp4",
      "size_bytes": 12345678901,
      "sha256": "...",
      "uploaded_at": "2026-05-15T12:34:56Z",
      "storage_path": "projects/<id>/raw/GH010023.mp4",
      "covers_stages": [1, 2, 3, 4]
    }
  ]
}
```

This handles the realistic case where one head-cam recording covers
multiple stages -- the user uploads once, the project knows which
stages map to which raw file (today's video-match logic continues to
do this lookup; it just runs against R2 paths now).

## Streaming back to the browser (playback)

Audio playback in the audit / coach views needs to fetch chunks of
audio.wav. Raw-video preview needs to fetch ranges of the source
file. Both flow through **signed URLs**.

```
Browser                      API server                      R2
  |                              |                            |
  |-- GET /api/v1/files/         |                            |
  |    signed-url?path=...&      |                            |
  |    method=GET&ttl=3600 ----->|                            |
  |                              |-- ACL check                |
  |                              |-- presign GET URL          |
  |<-- { url } ------------------|                            |
  |                                                           |
  |-- GET <signed_url> -------------------- range bytes 0-1m->|
  |<--------------------------------------- response ---------|
```

URL TTLs are short (15 minutes default; 1 hour for known-large
files). The browser refetches a new URL when the old one expires.

The frontend's existing audio waveform code (and the FCPXML preview
playback) only need to know "give me a URL I can fetch ranges
from"; the signed-URL helper hides the local-vs-hosted difference.

### Why not stream through the API

- **Cost.** R2 -> our API server -> browser doubles egress. Fly.io's
  egress isn't free (R2 -> R2 customer is free but R2 -> third-
  party server pays).
- **Latency.** Direct R2 fetch with a CDN in front (Cloudflare is
  free and sits in front of R2 anyway) is faster than proxying.
- **Range requests.** R2 handles HTTP range requests natively for
  multipart-uploaded objects. Implementing range proxying is
  another tarpit.

Local mode's `signed_url` returns `file:///` URLs that the local
server's `/api/files/signed/<token>` redirects to (token-bound, in-
memory map, 1-hour TTL -- no DB needed). The browser code is
identical in both modes.

## Project tarball upload (migration)

For users who don't want to install the desktop, the migration path
(see 07) is "export your local project to a tarball, upload it
through the web UI". The tarball flow uses the same tus protocol
with `kind='project_tarball'`, lands at:

```
imports/<upload_id>.tar.gz
```

After upload, a worker job:
1. Streams the tarball, validates structure (must contain
   `match.json` at the top level, schema-conformant).
2. Mints a new `project_id`, rewrites paths, uploads files to
   `projects/<new_id>/`.
3. Inserts the `projects` row + the owner `project_members` row
   for the importing user.
4. Deletes the source tarball.

The user sees the import progress in the jobs drawer.

## Bandwidth budgeting (rough)

- **Audio per stage:** ~5 MB. A 12-stage match = ~60 MB upload.
- **Raw video per stage:** 2-5 GB on a typical head-cam recording.
  A 12-stage match = 24-60 GB if all raw is uploaded.

The audio path is fine on any connection. Raw upload at 60 GB on a
50 Mbps uplink is ~3 hours. Tus resume is essential.

## Server-side limits (v1)

Per upload session:
- Max size: 50 GB. Above that, we need a conversation about whether
  the user really wants to upload that much.
- Max concurrent in-progress: 3 per user. Anything more probably
  means a runaway client; refuse with 429.

Per user (free tier):
- No raw upload at all (premium-only).
- Audio uploads: unlimited count, but jobs are gated by Tier 2
  access, which is premium-only. Free users use Tier 1 locally.

Per user (premium):
- v1 has no enforced quota. We measure usage (08) and add quotas in
  v2 if anyone abuses it.

## Open questions

- **Browser audio extraction quality.** WebAudio decodes most
  formats but exotic codecs in head-cam files might fail. Need to
  test against GoPro / Insta360 / Sony A7S samples. Fallback: ship
  ffmpeg.wasm for the unsupported formats (large but feasible).
- **Direct-to-R2 from desktop.** The desktop sync flow could write
  directly to R2 with desktop-bound credentials, skipping the API
  server entirely. Probably worth doing for raw video when v2 ships.
- **HEIC / HEVC raw video.** Some cameras output HEVC; ffmpeg in
  the cloud worker handles this fine, but HEVC playback in the
  browser is browser-dependent. May need a transcode step server-
  side for in-browser preview.
- **Encrypted client-side upload.** A user wanting client-side E2EE
  for raw video would need a key the worker can decrypt with --
  i.e. server-held key, which defeats the purpose. Skip until the
  user articulates a real threat model.
