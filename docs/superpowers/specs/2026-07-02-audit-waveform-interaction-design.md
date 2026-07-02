# Audit waveform interaction batch: hit zones, peak-snap, region loop

Date: 2026-07-02
Issues: #52 (marker grab vs playhead), #28 (peak-snap on drop/add), #29 (region loop)
Status: approved design, pending implementation plan

## Problem

Three long-standing interaction papercuts in the audit waveform, all in the
surface where the user spends the most review time:

1. **#52** - markers render with a 20px-wide hit zone. On fast strings that is
   wider than the gap between candidates, so clicking to place the playhead
   near a candidate grabs the marker instead. The drag threshold half of #52
   already shipped (6px, `DRAG_THRESHOLD_PX` in `MarkerLayer.tsx`); the hit
   zone, Esc-cancel, and touch threshold have not.
2. **#28** - drag-drop and double-click-add land wherever the cursor was, not
   on the audio transient the user is aiming at.
3. **#29** - loop (R) always snaps back to the play anchor. Marker review
   wants "repeat this small window around the focused marker".

## Decisions already made

- Plain click on a marker keeps toggling keep/reject (current behavior).
  The #52 issue text predates the shipped drag threshold; its
  "click = select + seek" clause is superseded.
- Peak-snap is the default on drop and add; Shift disables all snapping
  (1ms grid). One rule: Shift means "exactly where I put it".
- Approach: targeted edits to `MarkerLayer.tsx` / `Waveform.tsx` plus one
  pure helper module. No shared loop hook - Audit and Review keep their own
  rAF ticks (they differ: multi-cam drift correction, time coordinate maps).
- No server-side snap endpoint. Snapping is client-side from a peaks array.

## 1. Marker pointer interaction (#52 remainder)

All changes in `src/splitsmith/ui_static/src/components/MarkerLayer.tsx`;
Audit, Review, and PromoteReview inherit them via the shared component.

- Hit zone: button width 20px -> 10px. The 18px glyph may overflow the
  button box visually; only the button intercepts pointers. The full-height
  guide line stays 1px and purely visual (no pointer events).
- Click: unchanged (toggle keep/reject below the drag threshold). Clicks
  falling outside the new 10px zone pass through to the waveform and seek.
- Esc during a drag cancels it: the marker returns to its drag-start time
  and pointer capture is released. Mechanism: store `startTime` in the drag
  ref; attach a `window` keydown listener only while a drag is live (the
  button cannot be assumed to hold keyboard focus because pointerdown calls
  `preventDefault()`). Cancel calls `onTimeChange(id, startTime)` then
  `onTimeChangeCommit(id, startTime)`; the parent's existing
  `fromTime === time` guard makes that a no-op (no undo entry, no audit
  event).
- Touch: drag threshold stays 6px for mouse/pen, becomes 10px when
  `e.pointerType === "touch"`.

## 2. Peak-snap on drop and add (#28)

- New pure module `src/splitsmith/ui_static/src/lib/peak-snap.ts`:

  ```
  snapToPeak(time, peaks, duration, toleranceS = 0.025): number | null
  ```

  Finds the local-maximum bin within +/-tolerance of `time` and returns the
  bin-center time. Returns `null` when the window has no meaningful local
  maximum (flat or silent region), in which case the caller keeps the
  unsnapped (grid) time.
- When it applies:
  - drag drop (pointerup) - snap the committed time;
  - double-click add - snap the new manual marker's time;
  - live drag preview keeps the current detector-grid snapping;
  - keyboard nudges are never peak-snapped (deliberate fine adjustment).
- Shift held at drop / dbl-click disables both peak-snap and grid snap
  (1ms grid, as today).
- Data: Audit and Review fetch a second, snap-only peaks array at
  `bins = clamp(ceil(duration / 0.010), 1200, 8192)` (about 10ms per bin,
  capped by the server's existing limit; the server caches per
  `(path, bins)`). The rendered waveform keeps its current 1200-bin fetch so
  visuals do not change. Until the snap fetch resolves, drops fall back to
  grid behavior - never blocking.
- Wiring: `MarkerLayer` gets an optional `snapPeaks` prop of shape
  `{peaks: number[]; duration: number}`. Pages that do not pass it behave
  exactly as today. Audit and Review are wired in this change; PromoteReview can be
  wired later if wanted.

## 3. Region loop around the focused marker (#29)

- When `loopMode` is on AND a marker is focused: loop region =
  `[max(0, t - 0.5), min(duration, t + 0.7)]` around the focused marker's
  time. No focused marker -> existing anchor-loop behavior, unchanged.
- rAF tick wraps to `region.start` once playback passes `region.end`
  (instead of only wrapping at end-of-clip). Pause-while-looping snaps to
  `region.start` instead of the anchor.
- Focus moving to another marker mid-playback (N/M stepping) recomputes the
  region and seeks the playhead to the new region start - the
  "step -> loop -> K -> step" review flow.
- Visual: `Waveform` gets `loopRegion?: {start: number; end: number} | null`
  and draws it in the existing canvas pass as a translucent fill behind the
  bars. Color comes from a `--color-waveform-*` token; verify the token
  exists in `styles/index.css` before referencing it (add one if none fits).
  Rendered in both fit and zoom modes.
- Applies to Audit and Review; each page computes its own region because
  their time coordinates differ.

## Out of scope

- Zoom-aware marker spacing / collision avoidance (deferred by #52).
- Beep-correction UX (#22).
- N/M stepping logic changes.
- Marker hover tooltips.

## Verification

The SPA has no test runner, so verification is:

- `tsc` typecheck, `pnpm build`, scoped eslint on touched files;
- bounded headless Playwright screenshot pass of the audit page
  (domcontentloaded; route is `/match/:matchId`) to eyeball hit zone,
  region shading, and drag behavior;
- `lib/peak-snap.ts` stays a pure function so it is unit-testable the day a
  runner lands.

## Rollout

One PR, three logically separate commits (one per issue) so any piece can be
reverted alone.
