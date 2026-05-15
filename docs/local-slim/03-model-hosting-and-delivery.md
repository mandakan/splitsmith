# 03 -- Model hosting and delivery

This doc defines how exported ONNX artifacts reach the user's
machine: where they're hosted, how they're addressed, how the slim
runtime downloads and verifies them, and how the cache is laid out
on disk.

The decisions in doc 00 lock the basics: Cloudflare R2 behind
`models.splitsmith.app`, first-run download, no active update check
in v1, models pinned to the wheel via the bundled calibration. This
doc fills in the schema and the runtime behaviour.

## Hosting target -- Cloudflare R2

A single public-read bucket, fronted by a CNAME on the existing
`splitsmith.app` zone:

- **Bucket name:** `splitsmith-models` (private to the Cloudflare
  account; the public hostname is the only access path).
- **Public hostname:** `models.splitsmith.app` -- CNAME to the R2
  bucket's `*.r2.dev` hostname, with a Cloudflare-managed cert.
- **Egress cost:** $0 (R2's $0-egress is the entire reason R2 was
  picked over S3 in the SaaS doc set).
- **Cache headers:** content-addressed objects get
  `Cache-Control: public, max-age=31536000, immutable`. The manifest
  (see below) gets `Cache-Control: public, max-age=300` so future
  v2 channel-mode clients aren't pinned to a stale manifest.

The bucket is shared with the SaaS readiness plan's storage layer
in spirit but not in practice: SaaS user data lives in a separate
bucket. Model artifacts are public-read; user uploads are signed-URL
gated. Keeping them in different buckets means ACL changes on one
never affect the other.

## Object layout

```
models.splitsmith.app/
  manifest.json
  artifacts/<sha256>/<filename>
```

That's the whole tree. Two paths only:

1. **`manifest.json`** -- the top-level pointer. Catalogues every
   artifact every released slim wheel might ask for. Updated by the
   maintainer when a new artifact is uploaded; older artifacts stay
   referenced forever (immutable URLs).
2. **`artifacts/<sha256>/<filename>`** -- the actual artifact bytes.
   The path is **content-addressed**: a given SHA256 always points
   at exactly the bytes that hash to that value. Filenames inside
   the SHA256 directory are human-readable slugs
   (`clap_audio_encoder.onnx`, `pann_cnn14.onnx`, etc.) so a curious
   user clicking a URL sees something meaningful, but the hash is
   the actual identity.

Why this shape:

- **Immutability for free.** Once an SHA256-keyed object is
  uploaded, its URL never has to change.
- **Old wheels keep working forever.** A user who installs an older
  slim wheel after we've shipped a new model still gets a working
  download because their wheel pins the older SHAs, and those URLs
  are still live.
- **Manifest is reference, not source.** Even if `manifest.json` is
  lost, the cached calibration in any shipped wheel has enough info
  (SHA + filename) to reconstruct working download URLs.

## Manifest schema (`manifest.json`)

```json
{
  "schema_version": 1,
  "updated_at": "2026-05-15T14:00:00Z",
  "artifacts": {
    "clap_audio_encoder": {
      "current_sha256": "f1c4a8...d9",
      "versions": [
        {
          "sha256": "f1c4a8...d9",
          "filename": "clap_audio_encoder.onnx",
          "size_bytes": 41943040,
          "released_at": "2026-05-15T14:00:00Z",
          "wheel_version_introduced": "0.2.0",
          "notes": "Initial ONNX export of CLAP-HTSAT-unfused audio tower."
        }
      ]
    },
    "clap_text_embeddings": { "...": "same shape" },
    "pann_cnn14":            { "...": "same shape" },
    "clip_visual_encoder":   { "...": "same shape" }
  },
  "channels": {
    "stable": {
      "clap_audio_encoder":   "f1c4a8...d9",
      "clap_text_embeddings": "...",
      "pann_cnn14":           "...",
      "clip_visual_encoder":  "..."
    }
  }
}
```

Notes:

- **`schema_version`** lets us evolve the manifest without breaking
  older slim runtimes. v1 reads `schema_version == 1` and ignores
  anything else.
- **`current_sha256`** is what the `stable` channel points at right
  now. v1 slim runtimes never read it (they read the SHA pinned in
  their bundled calibration); v2's beta-channel mode would.
- **`channels.stable`** lets v2 clients address "the current
  blessed stable bundle" via a single name. v1 clients ignore the
  `channels` block entirely.
- **`versions[]`** keeps the history. Useful for the maintainer
  (release notes, rollback), not consumed by v1 clients.

The manifest grows monotonically. We never delete an artifact entry,
even if we deprecate it. Old wheels in the wild rely on the SHA URL
staying live; the manifest is the place we record that promise.

## Bundled calibration -- the consumer side

`src/splitsmith/data/ensemble_calibration.json` gains a top-level
`model_artifacts` block written by `scripts/build_ensemble_artifacts.py`
(doc 02). Each entry has exactly the fields the slim runtime needs
to fetch and verify the artifact:

```json
{
  "model_artifacts": {
    "clap_audio_encoder": {
      "filename": "clap_audio_encoder.onnx",
      "sha256": "f1c4a8...d9",
      "size_bytes": 41943040,
      "url": "https://models.splitsmith.app/artifacts/f1c4a8...d9/clap_audio_encoder.onnx"
    },
    "clap_text_embeddings": { ... },
    "pann_cnn14":            { ... },
    "clip_visual_encoder":   { ... }
  }
}
```

The `url` is recorded for the convenience of the slim runtime (so
it doesn't have to compose URLs from a base) and for offline
diagnostics, but the SHA256 is the truth: if the URL is reachable
but the bytes hash differently, the runtime refuses to use them.

A `model_artifacts.base_url` override is allowed for self-hosting
and air-gapped scenarios:

```json
{
  "model_artifacts": {
    "base_url": "https://my-mirror.example.com/splitsmith/",
    "clap_audio_encoder": { ... }
  }
}
```

When `base_url` is present, the slim runtime composes URLs as
`{base_url}artifacts/{sha256}/{filename}` and falls back to the
per-artifact `url` only if the composed URL 404s. v1 ships without
this set; the field exists in the schema so v2 self-hosters don't
have to wait for a runtime change.

## On-disk cache layout

```
~/.splitsmith/
  models/
    artifacts/
      <sha256>/
        <filename>
    .lock
```

- **`~/.splitsmith/`** is the XDG-friendly per-user state directory.
  On macOS and Linux it lives at `$HOME/.splitsmith/`. On Windows
  (not v1) it would map to `%LOCALAPPDATA%/Splitsmith/`.
- **`artifacts/<sha256>/<filename>`** mirrors the R2 layout exactly.
  This lets a sysadmin `rsync` a working cache onto another machine
  and have it work without manifest awareness.
- **`.lock`** prevents concurrent downloads from two parallel slim
  processes from corrupting partial files. The lock is held only
  during writes, not reads.

A cached artifact is considered valid if and only if
`sha256sum(file) == expected_sha256`. We verify on first load each
process (not every call -- the SHA check is fast but not free) and
cache the "verified" mark in memory. A
`splitsmith fetch-models --verify` command re-runs the hash check
across the whole cache.

## First-run download flow

When the slim runtime needs an artifact:

1. Read the artifact's `sha256` and `filename` from the bundled
   calibration JSON.
2. Compute the cache path:
   `~/.splitsmith/models/artifacts/<sha256>/<filename>`.
3. If the file exists, verify its SHA256 (once per process). If
   verified, hand the path to `onnxruntime.InferenceSession`.
4. If the file does not exist or fails verification:
   1. Acquire `~/.splitsmith/models/.lock`.
   2. Stream the artifact from the URL recorded in the calibration
      JSON (or composed via `base_url`).
   3. Write to a temp file in the same `sha256` directory.
   4. Verify SHA256 of the temp file.
   5. Atomic rename to the final filename.
   6. Release the lock.
5. If the download fails (HTTP error, hash mismatch, network drop),
   raise a typed exception that the CLI / UI layers translate into
   actionable user messages (see "Failure modes" below).

`huggingface_hub`'s `hf_hub_download` is used for the actual HTTP
streaming because it already handles resumable downloads, etag
caching, and proxy-aware HTTP. We point it at our own URL (it
doesn't care that it isn't HF Hub).

## Failure modes and UX

The slim runtime distinguishes three failure cases when downloading:

| Failure                          | User message                                          | Exit / fallback |
| -------------------------------- | ----------------------------------------------------- | --------------- |
| Network unreachable              | "Splitsmith needs to download ~180 MB of ML models on first use. Your machine couldn't reach `models.splitsmith.app`. Connect to the internet and run `splitsmith fetch-models` to retry." | Detection aborts with a non-zero exit code; the FastAPI handler returns 503 with a structured error body. |
| HTTP error (4xx / 5xx)           | "Model server returned HTTP {code}. This usually means the artifact moved; please run `uv tool upgrade splitsmith` to pick up a wheel that references the current URLs." | Same as above. |
| SHA256 mismatch                  | "Downloaded model `{slug}` failed integrity check. The cached file at `~/.splitsmith/models/artifacts/{sha}/...` has been removed. Try again, and if the failure persists, report this as a bug -- it likely means the model server is serving altered bytes." | Cache file deleted; detection aborts. |

No retries on hash mismatch -- that's a security-meaningful failure
mode and silent retry would mask it. Network errors get a single
exponential-backoff retry.

## Pre-fetching: `splitsmith fetch-models`

A new CLI command for users who prefer to pre-download:

```
$ splitsmith fetch-models
Downloading clap_audio_encoder (40 MB)...   ✓
Downloading clap_text_embeddings (96 KB)... ✓
Downloading pann_cnn14 (40 MB)...           ✓
Downloading clip_visual_encoder (60 MB)...  ✓
All models cached at /Users/you/.splitsmith/models/
```

Options:

- `--verify` -- re-hash every cached file and re-download any that
  fail. Useful for cache-corruption diagnostics.
- `--force` -- redownload everything, replacing any existing cache.
- `--list` -- print what would be downloaded and exit without
  fetching. The output also shows local cache status (present /
  missing / mismatched) so users can audit their state.

The command is fully scriptable: stdout is the progress UI, stderr
is errors, exit code is non-zero on any failure. CI environments
that need pre-warmed slim installs run `splitsmith fetch-models`
before tests.

## How the FastAPI server presents downloads

The CLI path is simple (print a progress bar). The UI server is
trickier: the first detection request kicked off after a slim
install has to wait for ~180 MB to download, and the user is sitting
in front of a browser.

v1 strategy:

1. On startup, the slim FastAPI server checks whether all required
   artifacts are present. If they are, business as usual.
2. If any are missing, a special endpoint `/api/models/status`
   returns a structured JSON describing what's missing and (when a
   download is in flight) progress.
3. The frontend renders a "Splitsmith is downloading detection
   models... 124 / 180 MB" overlay over the audit UI when the
   status endpoint reports missing or in-flight artifacts.
4. The first detection request that touches a missing artifact
   triggers the download synchronously; subsequent requests are
   serialised on the same download future to avoid duplicate
   fetches.

This keeps the wire shape of `/api/stages/{n}/shot-detect` clean:
detection requests can still take a long time, but the slow path
on first use is "download then detect" rather than "fail with a
cryptic error". An optional improvement is to start the download
eagerly when the server starts up so the UI shows progress
immediately rather than waiting for the user to click Detect.

## What this doc deliberately does not cover

- **Authentication for model downloads.** v1 models are public.
  Self-hosting users who want private models can override
  `base_url` to a private hostname; auth is then the user's
  responsibility (e.g. via a Cloudflare Access policy in front of
  their mirror).
- **Mirror selection.** v1 has one source (R2). The schema
  reserves `base_url` and per-artifact `url` so v2 can add mirror
  selection without a manifest rewrite.
- **The maintainer upload tooling.** Uploading a new artifact to
  R2 is a one-liner via `wrangler r2 object put` or the AWS CLI
  against R2's S3-compatible endpoint. The build script's
  `--upload` flag (doc 02) wraps it. There is no Splitsmith-owned
  publishing dashboard; we use the off-the-shelf R2 console.

## Open questions

- **Whether to ship a small fallback artifact in the wheel.** A
  ~5 MB GBDT-only path would let the slim app produce a degraded
  detection ("Voter A + Voter C with fewer features") even when
  offline on first run. The motivation is the "I'm on a plane and
  just installed it" case. Probably not v1, but cheap enough to
  re-evaluate.
- **`models.splitsmith.app` DNS provisioning.** Existing site is on
  Cloudflare Pages already (commit `06e5570`). Adding a subdomain
  CNAME is administrative work; no architectural blocker.
- **CDN warm-up.** R2 with Cloudflare's CDN in front is plenty fast
  for the file sizes here; if measured cold-fetch latency is poor
  from EU PoPs we can add Cloudflare Argo. Out of scope for v1.
