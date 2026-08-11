# Mobile beep review (mobile operator surfaces, slice 3)

Date: 2026-08-11
Status: approved
Parent: 2026-08-10-mobile-operator-surfaces-design.md (section 2)
Supersedes: the parent spec's "Bidirectional sync" paragraph claiming a phone
beep override re-runs trim and shot detection on hosted workers. The
2026-08-10-bidirectional-sync-design.md decision is authoritative: hosted
never re-derives; desktop re-derives on pull.

## Goal

The first mobile write surface: review, confirm, and correct beep times from
a phone against the hosted app, for both hosted-native matches and
desktop-pushed mirror matches. Writes on mirrors mark state only; the desktop
re-derives trim and shot detection on its next sync pull.

## Context and constraints

- The read_only_mirror gate in the `_match_id_alias` middleware
  (server.py ~6377) currently 403s every non-GET on `origin == "desktop"`
  matches except the `match/shares` family. All beep endpoints are
  match-scoped by the SPA, so every beep write on a mirror is blocked today.
- Mirrors have no raw video, no proxy, and no source audio hosted-side. Push
  uploads only docs plus trimmed clips (sync/plan.py). BeepVideoMini streams
  the raw proxy and waveform peaks are computed server-side from the source
  WAV, so the desktop beep review surface has no media to work with on a
  mirror.
- Latent bug: `_proxy_ready` (server.py ~13099) returns True for any
  non-`raw/` path, so the beep queue falsely reports video availability for
  mirror videos.
- The sync merge already treats all `beep_*` video fields as one atomic
  three-way-merged group with field-level LWW (sync/merge.py), and a
  remote-won beep group flips `processed.trim` (and `processed.shot_detect`
  for primaries) to false so the normal desktop job chain re-derives.

## Decisions

1. Mirror media = pushed beep snippet + peaks artifact (chosen over pushing
   full raw proxies, and over scoping the surface to hosted-native matches).
   Small, fast pushes; ear-first review per the parent spec.
2. Staleness is surfaced with a badge on affected stages (chosen over
   edit-time messaging only, and over no indication).
3. UI structure = shared hook + new mobile component (chosen over branching
   inside BeepReview.tsx and over a CSS-only responsive layout).
4. Write semantics on mirrors = mark state only, no job enqueue (locked by
   the bidirectional sync design; restated here because this slice implements
   the endpoint side of it).

## Design

### 1. Mirror write gate

Extend the read_only_mirror exemption in `_match_id_alias` to exactly two
write paths, following the shares exemption pattern but with a regex because
the shooter path has variable segments:

- `POST match/beep-queue/confirm`
- `POST shooters/{slug}/stages/{n}/videos/{video_id}/beep`

Re-detect (`.../detect-beep`), beep-window, beep/select, and beep/snap stay
403 on mirrors: they need source audio hosted does not have. Hosted-native
matches are untouched (the gate never applies to them).

### 2. Mark-state-only override on mirrors

`override_beep_for_video` on a mirror runs `_apply_beep_override` unchanged
(beep fields, `beep_source="manual"`, `processed.trim=false`, primary
`processed.shot_detect=false`, trim cache invalidated) but skips
`_maybe_chain_trim` and `_advance_sequential_chain`: no job enqueue, no
worker wake. The docs end up in exactly the shape the sync merge produces
for a remote-won beep group, so desktop pull re-derives through the existing
chain with no sync-side changes. `confirm_beep_in_queue` is already a pure
flag write and only needs the gate exemption. The mirror check reuses the
`origin` lookup the middleware already performs (passed via request state,
not re-fetched).

### 3. Push artifact: beep snippet + peaks

For each unconfirmed queue-worthy video (missing, low-confidence, or
unreviewed beep), desktop push generates and uploads two objects under
`matches/<match_id>/shooters/<slug>/beep_review/`:

- `<video_id>.m4a`: an AAC audio snippet covering the candidate span plus a
  margin; for missing beeps, the search window (or the default detection
  window when none is set).
- `<video_id>.peaks.json`: waveform peaks for the snippet range plus
  metadata: snippet start offset in video time, duration, and the candidate
  list.

Both participate in the existing content-hash-skip mechanism and are
regenerated when the video's beep group changes. Confirmed videos push
nothing, keeping pushes light. Hosted serves both objects through the
presigned-redirect pattern used for direct-R2 media.

### 4. Honest queue media descriptor

Fix `_proxy_ready` to report actual availability (false for non-`raw/`
paths on hosted) and add `snippet_ready` to each beep queue item. Items with
a snippet also carry URLs for the snippet audio and peaks JSON (resolved by
the server to the presigned-redirect endpoints), so the SPA never guesses R2
keys. The UI picks its media source per item: real proxy video
(hosted-native), snippet audio + pushed peaks (mirror), or a "review on
desktop" fallback card when neither exists.

### 5. Mobile UI

- Extract queue fetching, the confirm/override/redetect mutations, and the
  destructive-rerun warning copy from BeepReview.tsx into a shared
  `useBeepQueue` hook. Desktop BeepReview consumes the hook; behavior is
  unchanged.
- New `MobileBeepReview` card pager per the parent spec: snippet player with
  a "play around beep" control (about 1.5 s straddling the candidate),
  waveform strip with the candidate marker and tappable "Use this" pills for
  alt candidates, tap-to-place draft plus +-10 ms nudge steppers, large
  Confirm as primary action, Skip, next/prev with a "3 of 7" progress
  header, and the destructive-rerun warning as a bottom sheet using the
  Portal and z-token overlay architecture.
- The beep-review route renders `isMobile ? MobileBeepReview : BeepReview`,
  replacing DesktopGate for this route.
- Re-detect is hidden on mirrors (the endpoint stays blocked there).
- Video: BeepVideoMini renders only when the proxy is actually available;
  mirrors show a compact "video on desktop" placeholder instead.
- Conventions: 44 px minimum touch targets, WCAG 2.2 AA, status never by
  color alone, prefers-reduced-motion respected.

### 6. Staleness badge

Any stage with a video at `processed.trim === false` shows an "awaiting
desktop re-process" chip in mobile results and on beep-review cards. Derived
purely from the synced doc flags; no new state or endpoint.

## Out of scope

- Hosted-side beep detection or any hosted re-derivation.
- Pushing raw proxies or any per-frame video artifact.
- Audit triage (slice 4) and interval reclassify (slice 5); the
  needs_attention field arrives with slice 4.
- Picks outside the snippet range on mirrors: the user skips and handles the
  item on desktop.

## Testing

- pytest: mirror override returns 200 and submits zero jobs; re-detect,
  beep-window, select, and snap still 403 on mirrors; hosted-native behavior
  unchanged (jobs still chain); push plan includes snippet + peaks for
  unconfirmed videos only and re-uploads on beep-group change; queue media
  flags are honest for mirror and hosted-native items.
- vitest: useBeepQueue hook (queue shaping, mutation flows, warning gating);
  MobileBeepReview (media source pick, nudge steppers, confirm/skip flow,
  staleness chip).
- Acceptance: staging E2E in the slice-2 style. Push a match with
  unconfirmed beeps, review from a phone viewport (confirm one, override
  one), desktop pull re-derives and re-pushes, phone shows fresh results and
  the staleness chip clears, final re-push is a no-op.
