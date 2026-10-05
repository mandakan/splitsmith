# Audit scrubs the 720p rendition (#1192, fixes #1191)

Status: approved design, 2026-10-05.

## Problem

The local Audit screen streams the audit trim: full resolution, libx264
`ultrafast` CRF 20, GOP 15. From a 4K headcam that comes out at
~150 Mbit/s, larger than the camera's original. Two consequences:

- **Scrubbing is slow** on software decode and over a NAS (#1192).
- **Playback stops after ~2 s** in Chromium's low-end device mode, which
  Chromium turns on by itself on low-RAM machines (#1191). No `error`;
  `ended` fires and `currentTime` jumps to the duration.

Every trim already has a 720p faststart rendition beside it
(`_web.mp4`, #1031, `WebTrimConfig`: CRF 26, `veryfast`, GOP 30), cut
from the trim itself. Hosted Results / Coach / Compare stream it; the
Audit screen never does, because of the documented rule that
`kind=trim` never substitutes the rendition.

## Spike (2026-10-05)

Playwright Chromium, a 12 s 4K clip cut from real corpus footage at
171 Mbit/s with the audit trim's settings, its renditions cut by the
real `transcode_web_trim`. Throwaway scripts, not committed.

| | 4K trim | rendition GOP 30 | rendition GOP 15 |
|---|---|---|---|
| Low-end mode + software decode | `ended` at 2.7 s, 73/360 frames | 360/360 | 360/360 |
| Seek, software decode, 2 cores: median / p90 | 503-509 / 707-803 ms | 90-92 / 137-153 ms | 76-82 / 112-116 ms |

- The rendition's video starts at 0 with the same 360 frames as the
  trim: marker positions map 1:1.
- GOP 15 cost +35 % to +47 % in rendition size on the four 1080p60
  corpus clips, for every hosted and phone viewer, for ~10 ms of median
  seek. Browser seeks are frame-accurate at any GOP; the GOP only sets
  decode work, which at 720p is small.

**Decision: keep GOP 30.** The resolution is the whole win. With no
encode change there is no need to tell old renditions from new ones, so
#1192's tag / probe / re-cut proposal is dropped: every rendition on
disk today qualifies the day this ships.

## Design

### Server

1. **Local `kind=web` serves the rendition.** Today local mode treats
   `web` as `auto` (the trim). It becomes: the rendition from disk when
   it is fresh, else the trim, else the source. Never a 404, matching
   hosted. Fresh means the rule `_ensure_web_trim` already uses: non-empty
   and `mtime >= trim mtime`. No current local caller sends `kind=web`
   (the clip anchor reports `trim` locally), so nothing existing moves.
2. **`kind=trim` is untouched.** It still never substitutes. The new
   rule sits beside it: the audit players *ask* for `web` when the
   payload says a fresh rendition exists.
3. **`scrub_version` on each video dict**, next to `trim_version` in the
   project payload loop: `"<mtime_ns hex>-<size hex>"` of a fresh local
   rendition, else `null`. One shared freshness helper feeds both this
   and the `kind=web` branch, so the version always names the bytes
   behind the URL (the `_trim_version_for` contract). Local files only,
   no storage call; hosted reports `null`.
4. **`GlobalPrefs.full_res_scrub: bool = False`.** `GET` and
   `PUT /api/settings/scrub` (`{"full_res_scrub": bool}`), local only,
   404 hosted, beside `PUT /api/settings/auto-sync`.

### SPA

5. **`lib/scrubSource.ts`**, pure: given the video's `trim_version`,
   `scrub_version`, the preference and whether this video already failed
   on the rendition, returns `{ kind: "web" | "trim", version }`. `web`
   only when `scrub_version` is set, the preference is off and the video
   has not failed.
6. **`planServedClip` stays the one place an audit `kind` is chosen.**
   Where it returns `trim` today, `Audit.tsx` maps that through
   `scrubSource`. The active `<video>` in `MultiCamColumn` / the grid
   modal is the same element, so primary and secondary angles both
   switch. Offsets are unchanged.
7. **`MobileAudit.tsx`** applies the same mapping and fallback to its `kind=trim` URL.
8. **Fallback.** An `error` event on a `web` source adds the video to a
   page-lifetime failed set; the URL changes to `kind=trim` and the
   player remounts. No fallback on #1191's early `ended`: that symptom
   is the trim's, so falling back to the trim would land in it.
9. **Switch.** "Full-resolution video" toggle on `TransportLine`'s
   overflow menu (the documented home for a new Audit control), local
   mode only, read once per page from `GET /api/settings/scrub`, written
   with `PUT`. A change re-renders the URL.

### Docs

10. CLAUDE.md's #1031 section: local `kind=web` behaviour, `scrub_version`,
    and the audit players' rule. The `kind=trim never substitutes`
    sentence stays true.

## Out of scope

- **Hosted Audit** on a hosted-native match still streams the full trim
  from R2 (`scrub_version` is `null` hosted). The same switch would help
  phones there, but needs a storage presence check in the payload. A
  follow-up issue.
- Hardware decode and encode (#1193-#1195).
- The trim's own encode.

## Testing

Every test gets a mutation drill: remove the change, watch it fail.

- **Freshness helper:** fresh, stale (older than the trim), empty and
  missing renditions.
- **`stream_video` local:** `kind=web` serves the rendition when fresh;
  the trim when stale or missing; the source with no trim. `kind=trim`
  still serves the trim with a rendition present.
- **Payload:** `scrub_version` set for a fresh rendition, `null` when
  stale, and changes when the rendition is re-cut.
- **Settings route:** round trip, default `false`, 404 hosted.
- **`scrubSource.test.ts`:** table over version / preference / failed.
- **`camPlayback` + Audit:** the URL carries `kind=web&v=<scrub_version>`
  for a trimmed angle with a rendition, `kind=trim` without one, and
  `kind=proxy` is untouched.
- **Fallback:** a dispatched `error` on the `web` video moves the src to
  `kind=trim`.
- **Browser, by hand:** the demo match with `--media`, Audit plays the
  rendition (network tab), screenshot published as an Artifact; the
  spike's low-end-mode run repeated against the served URL.
- **Review:** one whole-branch pass over the seams before merge.
