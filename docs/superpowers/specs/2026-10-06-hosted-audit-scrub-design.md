# Hosted Audit scrubs the 720p rendition (#1209)

Status: approved design, 2026-10-06. Follows
`2026-10-05-audit-scrub-rendition-design.md` (#1192, local only).

## Problem

On a hosted-native match the Audit players (desktop `pages/Audit.tsx` for
every angle, `pages/MobileAudit.tsx`) pin `kind=trim` and stream the
full-resolution audit trim from R2: ~150 Mbit/s from a 4K headcam, `moov`
at the tail. On a phone or a laptop over the internet that is the file
that stalls software decode and ends after ~2 s in Chromium's low-end
mode (#1191). Every trim already has a 720p `_web.mp4` rendition on R2.
`scrub_version` (#1192) is computed from local files only, so on hosted
it is null and the players never ask for it.

One name, two contracts, is the obstacle. Hosted `kind=web` means
"rendition, else trim, else source, never 404": Results, Coach and
Compare rely on the source fallback for matches synced before the
rendition existed. The Audit pin needs "rendition, else trim, else 404":
during a re-cut the trim and rendition are deleted while the new trim
encodes, and a pinned player must error and remount on the new version,
not play the source under trim offsets (the #1208 review finding that
made local `kind=web` 404).

## Decisions

- **A new `kind=scrub`** carries the pin contract, in both modes; `kind=web`
  keeps one meaning everywhere (the streaming rendition with source
  fallback). Chosen over a `pin` flag on `web` and over accepting the
  re-cut window on hosted.
- **No full-resolution switch on hosted.** The per-rendition fallback on a
  playback `error` stays. `GlobalPrefs.full_res_scrub` and
  `/api/settings/scrub` stay local only.

## Design

### 1. Stream contract (`stream_video` and `stream_shooter_video`)

| kind | Local | Hosted |
|---|---|---|
| `trim` | trim, else 404 (mirror: rendition) | unchanged |
| `web` | fresh rendition, else trim, else source | unchanged: rendition, else trim, else source |
| `scrub` (new) | fresh rendition, else trim, else 404 | fresh rendition, else trim, else 404; on a mirror the pushed rendition |
| `auto`, `source`, `proxy` | unchanged | unchanged |

Local `kind=web` goes back to the hosted meaning: its pin behaviour moves
to `scrub`. No local caller other than the Audit players sends `web`
(the clip anchor reports `trim` locally).

### 2. Freshness: one rule

A pure `audio.fresh_rendition(trim, web, *, trim_required: bool)` over
two `(size, mtime)` facts (`StorageObject`-shaped, or `None` when
absent) returns whether the rendition is current: present, non-empty,
and -- when `trim_required` -- the trim present and the rendition not
older than it. `trim_required` is false only on a mirror, which has no
trim on R2 by design (spec 2026-09-27 v1.1).

- Local: `fresh_web_trim(trimmed)` builds the two facts from `os.stat`
  and calls the rule (behaviour unchanged).
- Hosted stream route: `storage.stat` on the trim key and the web key
  (two HEADs, as today's two `exists`).
- Hosted payload: the per-request `StoragePresence` listing.

The rule holds on R2 because the trim is always written before its
rendition: the worker cuts and uploads the trim, then cuts the rendition
from it; desktop sync uploads a clip's trim before its `_web.mp4` (push
plan order).

### 3. `StoragePresence` keeps object metadata

The index stores `key -> StorageObject` instead of a key set (still one
listing per prefix per request). `has_key` is unchanged; a new
`object(key) -> StorageObject | None` exposes the metadata. Existing
callers are untouched.

### 4. `scrub_version` on hosted

The project payload builds one `StoragePresence` per request. For each
stage video on a storage-backed project (presigned GET), `scrub_version`
is `"<last_modified ns hex>-<size hex>"` of a fresh rendition from the
listing (`trim_required = not mirror`), else null. Without storage it
stays the local computation. One listing of `<scope>/trimmed/` per
request, whatever the video count; pinned by a storage-call count test.

### 5. SPA

`scrubSource` returns `kind: "scrub"` where it returned `"web"`;
`useScrubSource`, `auditVideoSrc` and MobileAudit pass it through. The
fallback's guard (`src.includes("kind=web")`) becomes `kind=scrub`. On
hosted `available` is already false (no settings fetch), so no switch is
shown; `choose` works from `scrub_version` alone.

### 6. Docs

CLAUDE.md's #1031 section states the three kinds once (the table above).

## Out of scope

- A hosted full-resolution switch (decision above).
- Deleting a stale rendition on R2; the freshness rule only declines to
  serve one.

## Testing

Every new test fails against the pre-change code; mutation drills on
each rule.

- `fresh_rendition`: present/absent/empty/older/newer, `trim_required`
  both ways.
- Local stream: `kind=scrub` takes over the 404-without-trim and re-cut
  tests; `kind=web` falls back to the source again.
- Hosted stream (moto): `scrub` redirects to a fresh rendition, to the
  trim when the rendition is older, 404s with no trim on a native match,
  redirects to the rendition on a mirror; `web` unchanged (existing
  tests).
- Hosted payload: `scrub_version` set when fresh, null when stale or
  absent; the payload makes one `trimmed/` listing per request.
- `StoragePresence.object` returns the listed metadata; `has_key`
  unchanged.
- SPA: `scrubSource`, `useScrubSource`, `auditVideoSrc` and MobileAudit
  expectations move to `scrub`.
- Hosted end to end needs staging, which serves released code: checked
  after the release, by opening a hosted-native Audit page and reading
  the video's `src` and response.
