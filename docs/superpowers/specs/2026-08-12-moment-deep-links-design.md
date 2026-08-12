# Moment deep links - design

Date: 2026-08-12
Status: approved pending review
Surfaces: ResultsStage (video page), Compare (multicam), both operator and `/share/:token` routes.

## Problem

Sharing a specific moment in a run today means screenshots or "scrub to about 4 seconds in". We want a first-class way to link to an exact timestamp on the video and comparison pages, with the link reproducing what the sharer was looking at. The same moment concept must later serve a bookmark feature (shooter/coach saves moments, labels them, groups them by topic) without redesign.

## Decisions made during brainstorming

- Time anchor: stage-relative seconds after the start beep, not raw clip seconds. Matches Compare's canonical `timeSinceBeep` clock and every domain marker (`time_after_beep`, `beep_offset_in_clip`). Survives re-trims, meaningful across cameras and shooters.
- Link scope: full view state - the link reproduces the sharer's view (time, focused camera, visible shooters), not just the seek position.
- Capture UX: explicit "Copy link at moment" action, not a continuously-updating URL.
- Encoding: readable query params (Option A), not hash fragments or opaque blobs.
- Bookmarks: designed for, not built. V1 ships deep links only.
- OG unfurls: moment links get proper previews; use the existing share-card pipeline, made moment-aware. This work also closes the existing gap that compare share pages have no OG shell at all.

## 1. The Moment concept

New module `src/splitsmith/ui_static/src/lib/moment.ts`:

```ts
export type Moment = {
  t: number        // seconds after beep; may be negative (pre-beep draw/holster); 2-decimal precision
  cam?: string     // focused/master camera shooter slug (Compare only)
  who?: string[]   // visible shooter slugs (Compare only)
}

export function momentToSearch(m: Moment): URLSearchParams
export function parseMoment(params: URLSearchParams): Moment | null
```

URL form: `?t=4.32&cam=alice&who=alice,bob`. Stage and (on Results) shooter are already path segments and are not duplicated. `parseMoment` returns `null` unless `t` parses as a finite number with |t| <= 3600; range clamping beyond that happens at apply time, against the actual clip bounds. Unknown params are ignored in both directions. This module is the single serializer/parser; the future bookmark feature stores the same object plus `{match, stage, surface, label, topics}` and navigates via `momentToSearch`.

## 2. Capture: "Copy link at moment"

A button in the player controls on both surfaces, plus the existing Snackbar for confirmation:

- ResultsStage: serializes `{t: currentClipSeconds - beepTime}`.
- Compare: serializes `{t: timeSinceBeep, cam: audioSlug, who: [...visibleSlugs]}`.

The action copies `location.pathname + '?' + momentToSearch(...)`. Because it builds from the current location it works identically on operator routes and `/share/:token/...` routes: recipients of a share link can copy deeper timestamped links. It is pure URL construction with no write API, so it stays on the read-only share view (no `isShareView` gating).

Owner flow for "send someone a timestamped share link" in v1: open your own share link, use the button there. Deferred: an "include current moment" option in ShareDialog.

## 3. Arrival: applying a moment

On mount, once clip metadata is loaded (beep offsets known), each page parses the params once and applies them:

- Compare: if `cam`/`who` slugs exist in the roster, set `audioSlug` / `visibleSlugs`; invalid slugs are dropped silently. Then `scrubTo(t)` - the existing seek path that converts `timeSinceBeep` to per-camera clip time via `beep_offset_in_clip`.
- ResultsStage: convert `t` to clip seconds via `beep_time`, clamp to `[0, duration]`, seek.
- Arrive paused, with a small moment marker on the scrub bar at the target position (distinct shape plus label, not color-only, per WCAG stance).
- The URL keeps its params; refresh returns to the moment. No continuous URL updates during playback. Anything malformed degrades to a normal page load - no error states.

Apply-once semantics: a ref guards against re-applying on data refetch or SSE updates.

## 4. OG unfurls

### Existing infrastructure (spec 2026-08-09)

`ui/share_og.py` serves share shells (`index.html` with OG tags injected) for `/share/{token}`, `/share/{token}/results`, and `/share/{token}/results/{slug}/{stage}`, backed by `og-meta` JSON routes and card PNGs (`share_card.py` models -> `share_card_render.cached_card_png`, Chromium-rasterized, cached in object storage keyed by `card_hash`). Failure philosophy: "no rich preview" is acceptable, "no page" is not; dead and unknown tokens are indistinguishable.

### Gap being closed

`/share/{token}/compare/{stage}` has no shell route - compare share links unfurl with generic tags only. This design adds it, since Compare is the primary moment-sharing surface.

### Moment-aware meta

Crawlers fetch the full URL including the query string, so shell routes gain query parsing (same defensive stance as `_parse_positive_int`: any malformed value degrades to the moment-free variant, never an error page):

- Stage shell with valid `t`: title gains a suffix - `"Alice - Stage 3 (Match) - moment at 4.32s"`; description unchanged.
- New compare shell: title `"Stage 3 comparison (Match)"` or with moment suffix; description lists compared shooter names (from `who` when valid, else the full roster).

### Moment-aware card image

The card models gain an optional moment:

- `StageCard.moment_t: float | None` - the stage card renders a "MOMENT 4.32s" badge strip when set. Baseline figures unchanged.
- New `CompareCard` model: stage name, match name, list of shooter names, optional `moment_t`. Rendered by the same HTML template family and Chromium pipeline.

New PNG routes (registered plain, reached via the share alias, added to `_SHARE_PATH_RE`):

- `/api/og/{slug}/{stage}.png?t=4.32` - stage card with badge.
- `/api/og/compare/{stage}.png[?t=...&who=...]` - compare card. Registered before the `{slug}/{stage}` route so the literal `compare` segment wins; a shooter actually slugged `compare` loses the stage-card URL shape (accepted - the og-meta route decides which image URL a shell emits, so no user-facing link ever depends on guessing).

Caching: moment cards are **not** written to object storage. `t` is a continuous value; per-`t` R2 objects would let anyone holding a token mint unbounded storage writes by iterating `t`. Instead moment-variant renders are served with `Cache-Control: public, max-age=31536000` (the URL carries `t` and the `?v=` card hash, so it is self-versioning) and rendered on demand (~0.6-1s Chromium render; crawler fetches are rare and Slack/X/Discord each fetch once). Moment-free cards keep the existing storage-backed path unchanged. The existing degraded-plate rule applies: a fallback plate is never long-cached.

`t` handling server-side: parse as float, require finite, clamp to [-60, 3600], format to 2 decimals before it reaches the card model or the cache key - this also bounds cache-key cardinality per second to 100.

### Deferred: real frame grabs

The app image carries static ffmpeg and clips live in R2, so a true video-frame-at-`t` preview (Loom-style) is feasible later - e.g. captured opportunistically at copy-time. Not v1: crawler fetches hit scale-to-zero cold starts plus a full R2 range-read, and crawlers time out in single-digit seconds. The card badge gives an honest, fast preview now; frame grabs slot in behind the same URLs later.

## 5. Bookmarks (forward design only)

A bookmark is `Moment + {match_id, stage, surface: 'results' | 'compare', slug?, label, topics: string[]}` owned by a user, stored server-side (per-user table satisfying the multi-tenant invariants, or a state_doc - decided when built). "Jump to bookmark" navigates to the URL built by `momentToSearch`. Nothing bookmark-shaped is built in v1; the constraint it imposes today is only that `Moment` stays a plain serializable object with no component coupling.

## 6. Error handling summary

| Failure | Behavior |
| --- | --- |
| Malformed/missing `t` (frontend) | Normal page load, no moment applied |
| Unknown `cam`/`who` slugs | Dropped silently; valid remainder applied |
| `t` outside clip range | Clamped to clip bounds |
| Malformed query params (shell routes) | Moment-free meta/card variant |
| Compare card build failure | Existing fallback ladder: match card, then generic tags |

## 7. Testing

- Vitest: `moment.ts` round-trip, negative `t`, junk tolerance, clamping; component tests that ResultsStage and Compare apply a moment (seek target plus view state) and ignore invalid slugs; copy action serializes live state.
- Pytest: shell routes with/without `t` (title suffix, degraded variants), compare shell parity with existing stage shell tests, moment card renders skip storage writes, `t` clamp/format, `_SHARE_PATH_RE` covers new routes.
- Manual: bounded headless screenshot of a moment link on staging; paste a moment share URL into a Slack/Discord unfurl checker.

## Out of scope

- Bookmark storage, UI, and topics.
- ShareDialog "include current moment".
- Frame-grab OG images.
- URL-reflected state beyond the moment (layout mode, playback speed).
