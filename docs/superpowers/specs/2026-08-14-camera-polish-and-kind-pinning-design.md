# Camera polish + stream-kind pinning - design

Date: 2026-08-14
Status: approved
Issue: #870 (follow-ups from the share-camera-selection review, PR #868)

## Context

Camera selection shipped in PR #868. The review left four follow-ups. Two
findings changed during investigation:

- "Trim caches for secondaries" is already implemented: the trim pipeline is
  role-generic end-to-end (`_audit_trim_targets` targets every non-ignored
  angle, `_run_trim` keys per video_id, `kind=auto` streaming and
  `_video_beep_in_clip` resolve per video). Verified empirically: secondary
  trims exist on disk with `processed.trim: True`.
- What remains real is a race: `beep_in_clip` is measured against whatever
  clip exists at coach-fetch time, but `kind=auto` re-resolves per request.
  A trim job completing between coach fetch and playback shifts the served
  bytes under a stale `beep_in_clip` - the same mid-playback-swap class the
  Audit page already avoids by pinning `kind` explicitly.

Two PRs, in order, both cut from main.

## PR A - SPA polish (frontend only)

1. Share-mount `?v=` test. `ResultsStage.cameras.test.tsx` gains one test
   rendering the `/share/:token/results/:slug/:stage` route with
   `?t=1.00&v=1` and asserting the player opens on camera index 1. Pins the
   share-route contract; api is mocked, so this verifies page behavior, not
   server scoping (that stays covered structurally by `scopeRequestPath`).

2. Compare resync listener hygiene. The resync-after-swap effect:
   - keeps at most one pending `loadedmetadata` listener per tile, tracked
     in a ref keyed by slug, removing the previous listener before adding a
     new one and removing all of them in the effect cleanup;
   - reads `timeSinceBeep` and `isPlaying` through refs kept current by a
     tiny mirror effect, and drops them from its dependency array, so the
     effect body runs only when `camIndexBySlug` / `camsBySlug` /
     `orderedShooters` / `effectiveBeep` change - not per 120ms sync tick.
   Behavior is otherwise identical (same 0.3 drift guard, same target math).

3. ResultsStage cam-index DRY. Two module-level pure helpers:

   ```ts
   function resolveCamIndex(coach: CoachStageResponse, raw: number): number
   // raw if coach.videos[raw] exists, else 0

   function camBeep(coach: CoachStageResponse, index: number): number
   // coach.videos[index]?.beep_in_clip ?? coach.beep_time
   ```

   replace the three duplicated
   `coach.videos[coach.videos[activeCamIndex] ? activeCamIndex : 0]?.beep_in_clip ?? coach.beep_time`
   expressions (camDeltaForShots, momentTime, the post-return derivation).
   `handleSelectCam` computes `prevBeep` via
   `camBeep(coach, resolveCamIndex(coach, prev))`, closing the stale-index
   edge flagged in the Task 3 review. Pure refactor: no behavior change,
   existing tests must stay green untouched (except imports if any).

## PR B - pin the measured stream kind (backend + SPA)

Backend (`src/splitsmith/ui/server.py`):
- `_video_beep_in_clip` already resolves whether a trim or the source was
  measured; surface that answer instead of discarding it. Refactor it (or
  add a sibling) to return `(beep_in_clip, kind)` where
  `kind: Literal["trim", "source"]`.
- `_coach_video_entries` adds `"kind"` to each entry dict:
  `{"path", "role", "beep_in_clip", "kind"}`. For a video with no beep
  (`beep_in_clip: null`), `kind` reflects the same resolution (source when
  no trim exists) - the SPA disables those cameras anyway.
- No schema/DB changes; the coach response is built per request.

SPA:
- `CoachVideoEntry` gains `kind: "trim" | "source"`.
- ResultsStage: the main player src and `CamPicker`'s `srcFor` pass
  `entry.kind` as `videoStreamUrl`'s kind argument (was `auto`).
- Compare: `tileSrc` passes `entry.kind` for indices > 0; index 0 keeps the
  bundle's `video_ref` path untouched.
- Result: the bytes served always match the clip `beep_in_clip` was
  measured against, for the whole life of that coach payload.

Error handling: pinning `trim` for a trim deleted mid-session 404s into the
player's existing error + retry state (retry refetches coach and re-pins).
Accepted: strictly rarer than the desync it prevents. `kind=source` always
serves.

Share surface: no whitelist change needed - the stream route and its query
params are already admitted; `kind` is a query param on the same path.

## Testing

- PR A: new share-mount test; Compare resync tests keep passing; a test
  asserting the resync effect does not thrash per tick is NOT required
  (jsdom timing tests of that kind are flaky - rely on review of the dep
  array). Full SPA gate at branch end.
- PR B: backend pytest covering `_coach_video_entries` kind with and
  without a trim on disk (local mode, tmp project fixture - follow the
  existing coach endpoint test patterns); SPA tests asserting `kind=trim` /
  `kind=source` appear in the player and tile URLs per the mocked payload.
  Gates: SPA full suite + ruff + black + scoped pytest. No docker smoke
  (no DB/queue/connector changes).

## Non-goals

- No lossless exports for secondary cameras (Compare index 0 quality nuance
  accepted; revisit only on user demand).
- No changes to trim job scheduling or the rebuild endpoint (already
  role-generic).
- No `kind` pinning for the Compare `video_ref` path (it resolves a
  concrete file server-side already).
