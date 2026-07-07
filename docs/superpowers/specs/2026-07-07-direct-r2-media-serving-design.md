# Direct-to-R2 media serving

Date: 2026-07-07
Status: approved, in implementation

## Problem

Hosted media playback buffers for a long time. Two compounding causes:

1. Beep review and audit players request full-resolution `source` (and `trim`)
   files, not the low-res `proxy`. The proxy exists but is only wired into
   ClipDetail.
2. In hosted mode, `stream_video` -> `resolve_video_path` ->
   `_mirror_from_storage` downloads the entire object from R2 to local disk
   (`shutil.copyfileobj`) before `FileResponse` sends a single byte. HTTP
   Range support is real but useless on a cold cache because the mirror is
   whole-file and synchronous. Every uncached source play stalls for the full
   download; every beep-review video switch hits a different, cold file.

Local/desktop mode is unaffected: files are already on disk.

## Goal

The app stops being a byte pipe in hosted mode. It authorizes, then hands the
browser a short-lived direct URL to R2; the browser does ranged reads straight
from storage. No whole-file mirror, no double hop. Proxies back every
preview/scrub surface. Security stays exactly as tight as today. No fallbacks:
local mode reads disk, hosted mode redirects, missing proxy is an explicit
state.

CDN is out of scope this round but the design keeps the endpoint opaque so a
CDN domain can drop in later with no frontend change.

## Serving model

One helper, `serve_media`, backs every media endpoint. Behaviour is chosen by
mode, with no overlap:

- **Hosted (storage bound, relative key):** resolve the R2 *key* (never
  mirror), issue a presigned `get_object` URL with `ResponseContentType` set
  (e.g. `video/mp4`) and `ResponseContentDisposition=inline`, return a **307**
  redirect. Browser does ranged reads directly against R2.
- **Local/desktop (no storage, or absolute path):** `FileResponse` from disk,
  Range-native. Unchanged.

The whole-file mirror in `resolve_video_path` is bypassed for serving. It stays
only for jobs that genuinely need the source file on local disk (detection,
trim, export workers).

## Storage capability

Add to the `Storage` protocol:

```python
def presign_get_url(
    self, path: str, *, expires_in: int,
    content_type: str | None = None, disposition: str = "inline",
) -> str: ...
```

- `S3Storage`: `generate_presigned_url("get_object", Params={Bucket, Key,
  ResponseContentType, ResponseContentDisposition}, ExpiresIn=expires_in)`.
  Mirror of the existing `presign_upload_part`.
- `FilesystemStorage`: raises `NotImplementedError` (local mode never
  redirects; callers branch on `storage is None` before reaching it).

## Key resolution per `kind`

The issuer maps to an R2 key, not a local path:

- `proxy` -> `proxy_key_for(raw_str)` (`raw_proxy/<name>.mp4`)
- `trim`  -> `trimmed/stage<N>_cam_<video_id>_trimmed.mp4`
- `source`-> the raw key (`video.path`, relative)

Same registration check as today (`project.find_video(path)` must succeed), so
owner and share-token authorization are inherited unchanged. Share requests are
already GET-only and tenant-pinned by `_share_alias`.

## Proxy everywhere (frontend contract)

- **Beep review** video `src`: `source` -> `proxy`. Waveform/peaks keep using
  the full-source WAV; the proxy is untrimmed and shares the source timeline
  origin, so playhead sync holds.
- **Audit** primary: `trim` when a trim exists, else `proxy` (was `source`).
  Secondaries: scoped stream endpoint with `proxy` (was unscoped `auto`).
- **ClipDetail**: already `proxy`, unchanged.
- `api.videoStreamUrl` gains `"proxy"` in its `kind` union.

## No ugly fallback: explicit "preview generating"

Today `kind=proxy` silently serves source when the proxy object is absent. New
rule:

- **Hosted, proxy key absent:** return `425` with structured body
  `{"code": "preview_generating", ...}`. The player renders an explicit
  "preview still processing" state. Never a silent full-res download.
- **Local mode, no proxy pipeline:** `kind=proxy` serves `source` from local
  disk (fast, no network). Mode-correct: local has no generating worker to wait
  on. This is the one place `proxy` resolves to source, and only when there is
  no storage layer at all.

## Security: TTL vs revocation

Presigned URLs are time-boxed bearer capabilities scoped to one object in one
`users/<id>/` prefix. Revoking a share token does not invalidate already-issued
presigned URLs, so share TTL must be short. Branch on the existing
`current_share_request` ContextVar:

- Owner request: **6 h** TTL (covers a long editing session).
- Share request: **15 min** TTL (revocation takes effect quickly).

## Rollout ordering

1. **R2 CORS first.** Direct browser fetch is cross-origin (app domain ->
   `*.r2.cloudflarestorage.com`). A bucket CORS rule (methods `GET, HEAD`;
   allow the app origins for staging + prod + localhost dev; allowed headers
   include `Range`; expose `Content-Range`, `Accept-Ranges`, `Content-Length`;
   sensible `MaxAgeSeconds`) must land before the frontend points at direct
   URLs. Shipped as an idempotent `scripts/` command using `put_bucket_cors`,
   not a dashboard click. Applied to both buckets before deploy.
2. **Proxy backfill check.** A `scripts/` command that lists hosted videos and
   verifies each has a `raw_proxy/` object; reports any missing so they don't
   sit in "preview generating" forever. Backfill (re-run generate_proxy) if
   any are missing.

## Testing

- Unit (moto): hosted issuer returns 307 to a presigned URL; local returns
  `FileResponse`; unregistered `path` -> 404; share request gets 15 min TTL,
  owner 6 h; hosted proxy-absent -> 425 `preview_generating`; local proxy-absent
  -> source from disk.
- Existing tests asserting byte bodies from these endpoints are rewritten to
  assert the redirect (retiring obsolete assertions, not preserving them).
- Frontend: SPA has no test runner; verify via typecheck + build + scoped
  eslint.
- Manual staging: `curl -H "Range: bytes=0-1" <presigned>` -> 206 with CORS
  headers, before and after.

## Out of scope

- CDN / custom media domain (endpoint stays opaque so it drops in later).
- Building proxies in local mode.
- Presigning the tiny beep-preview clips and peaks JSON (transfer is not the
  cost there).
