# Audit Waveform Interaction Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the three audit-waveform interaction papercuts - marker grab vs playhead (#52 remainder), peak-snap on drop/add (#28), region loop around the focused marker (#29).

**Architecture:** Targeted edits to the shared `MarkerLayer.tsx` / `Waveform.tsx` components plus one new pure helper `lib/peak-snap.ts`. Audit and Review pages each wire the new optional props into their existing state; their rAF playback loops stay page-owned. Spec: `docs/superpowers/specs/2026-07-02-audit-waveform-interaction-design.md`.

**Tech Stack:** React 18 + TypeScript SPA in `src/splitsmith/ui_static/` (pnpm only, never npm). No test runner exists for the SPA - verification is `pnpm typecheck`, `pnpm build`, and scoped eslint.

## Global Constraints

- All SPA paths below are relative to `src/splitsmith/ui_static/`.
- New prose/comments use single ASCII dash `-`, never em dash, never `--`.
- No new dependencies.
- CSS `var(--foo)` references silently fall back - any new token MUST be added to `src/styles/index.css` in the same commit that references it.
- pnpm only in the SPA (`pnpm typecheck`, `pnpm build`, `pnpm exec eslint <files>`). Run from `src/splitsmith/ui_static/`.
- One commit per issue so each piece is independently revertable.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01G2Z121nV8dPjgu248huLMX
  ```

---

### Task 0: Branch setup

The design-spec commit `a216632` currently sits on local `main` (unpushed). Move it onto the feature branch and restore local `main` to `origin/main`.

**Files:** none (git only)

- [ ] **Step 1: Create the branch and reset main**

```bash
cd /Users/mathias/work/splitsmith
git checkout -b fix/audit-waveform-interaction
git update-ref refs/heads/main origin/main
git log --oneline -2   # expect: a216632 docs: design spec ... on top of origin/main
```

---

### Task 1: Marker pointer interaction (#52 remainder)

**Files:**
- Modify: `src/components/MarkerLayer.tsx`

**Interfaces:**
- Consumes: existing `MarkerLayerProps` (unchanged in this task).
- Produces: same public API; behavior changes only (10px hit zone, Esc cancel, touch threshold).

- [ ] **Step 1: Add the touch threshold constant and extend the drag ref**

In `src/components/MarkerLayer.tsx`, below `DRAG_THRESHOLD_PX` (line ~41), add:

```ts
/** Touch pointers get a wider threshold - fingers wobble more than mice,
 *  and the 10px hit zone means most touch presses start slightly off the
 *  marker center. */
const TOUCH_DRAG_THRESHOLD_PX = 10;
```

Replace the `dragRef` declaration (currently `{ pointerId, element, startX, startY, moved }`) with:

```ts
const dragRef = useRef<{
  pointerId: number;
  element: HTMLButtonElement;
  markerId: string;
  startX: number;
  startY: number;
  /** Marker time at pointerdown - restored on Esc cancel. */
  startTime: number;
  thresholdPx: number;
  moved: boolean;
} | null>(null);
```

- [ ] **Step 2: Populate the new fields in handlePointerDown**

In `handlePointerDown`, replace the `dragRef.current = {...}` assignment with:

```ts
dragRef.current = {
  pointerId: e.pointerId,
  element: el,
  markerId: marker.id,
  startX: e.clientX,
  startY: e.clientY,
  startTime: marker.time,
  thresholdPx:
    e.pointerType === "touch" ? TOUCH_DRAG_THRESHOLD_PX : DRAG_THRESHOLD_PX,
  moved: false,
};
```

- [ ] **Step 3: Use the per-gesture threshold in handlePointerMove**

Replace the threshold check inside `handlePointerMove`:

```ts
if (dx * dx + dy * dy < drag.thresholdPx * drag.thresholdPx) return;
```

- [ ] **Step 4: Add Esc-to-cancel**

Add after `handlePointerUp` (order matters only for readability):

```ts
// Esc cancels an in-flight drag: restore the pointerdown-time position
// and release capture. Listens on window because pointerdown calls
// preventDefault(), so the button cannot be assumed to hold keyboard
// focus mid-drag. Committing with the original time is a no-op for
// parents that guard on from === to (Audit) and a harmless restore for
// parents that log per-change (Review).
const cancelDrag = useCallback(() => {
  const drag = dragRef.current;
  if (!drag) return;
  if (drag.element.hasPointerCapture(drag.pointerId)) {
    drag.element.releasePointerCapture(drag.pointerId);
  }
  const { markerId, startTime, moved } = drag;
  dragRef.current = null;
  if (moved) {
    onTimeChange(markerId, startTime);
    onTimeChangeCommit?.(markerId, startTime);
  }
}, [onTimeChange, onTimeChangeCommit]);

useEffect(() => {
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape" && dragRef.current) {
      e.preventDefault();
      cancelDrag();
    }
  };
  window.addEventListener("keydown", onKey);
  return () => window.removeEventListener("keydown", onKey);
}, [cancelDrag]);
```

Note: after `releasePointerCapture`, the browser fires a `pointerup`/`lostpointercapture`; `handlePointerUp` guards on `drag?.pointerId !== e.pointerId` and `dragRef.current` is already null, so it returns without double-committing.

- [ ] **Step 5: Narrow the hit zone**

In the marker `<button>` style (line ~328), change `width: "20px"` to `width: "10px"`. The 18px glyph may overflow the 10px button box; that is intentional - only the button intercepts pointers. Update the component doc comment at the top of the file to mention the 10px hit zone and Esc cancel.

- [ ] **Step 6: Typecheck and lint**

```bash
cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static
pnpm typecheck
pnpm exec eslint src/components/MarkerLayer.tsx
```
Expected: both exit 0.

- [ ] **Step 7: Commit**

```bash
cd /Users/mathias/work/splitsmith
git add src/splitsmith/ui_static/src/components/MarkerLayer.tsx
git commit -m "fix(audit): narrow marker hit zone, Esc-cancel drag, touch threshold (#52)"
```
(with the Global Constraints trailer lines appended to the message)

---

### Task 2: Peak-snap on drop and add (#28)

**Files:**
- Create: `src/lib/peak-snap.ts`
- Modify: `src/components/MarkerLayer.tsx` (snapPeaks prop, drop snap)
- Modify: `src/components/Waveform.tsx` (dblclick shiftKey passthrough)
- Modify: `src/pages/Audit.tsx` (snap-peaks fetch, add-snap, prop wiring)
- Modify: `src/pages/Review.tsx` (same as Audit)

**Interfaces:**
- Produces: `snapToPeak(time: number, snapPeaks: SnapPeaks, toleranceS?: number): number | null` and `interface SnapPeaks { peaks: number[]; duration: number }` from `@/lib/peak-snap`.
- Produces: `MarkerLayerProps.snapPeaks?: SnapPeaks` (optional; absent = today's behavior).
- Changes: `WaveformProps.onDoubleClick?: (timeSeconds: number, shiftKey: boolean) => void` - existing single-param handlers remain type-compatible (TS allows fewer params), but Audit/Review are updated to use the flag.

- [ ] **Step 1: Create the pure helper**

Create `src/lib/peak-snap.ts`:

```ts
/**
 * Nearest-local-peak snapping for marker drop / add gestures (#28).
 *
 * Pure function over a server-computed peaks array (see
 * splitsmith.waveform.compute_peaks). Kept side-effect-free so it is
 * unit-testable the day the SPA gains a test runner.
 */

export interface SnapPeaks {
  peaks: number[];
  duration: number;
}

/** Default snap window, seconds. Transients the user aims at are a few
 *  ms wide; 25 ms of forgiveness covers cursor slop without jumping to
 *  the neighboring shot on a fast string (typical splits 150-400 ms). */
export const PEAK_SNAP_TOLERANCE_S = 0.025;

/** Minimum normalized amplitude for a bin to count as a peak. Below this
 *  the window is treated as silence and the gesture keeps its raw time. */
const MIN_PEAK_AMPLITUDE = 0.05;

/** Returns the bin-center time of the strongest local peak within
 *  +/- toleranceS of `time`, or null when the window has no meaningful
 *  local maximum (silence, or the max sits on the window edge with the
 *  envelope still rising outside - that is the slope of a farther peak,
 *  not one the user aimed at). */
export function snapToPeak(
  time: number,
  snapPeaks: SnapPeaks,
  toleranceS: number = PEAK_SNAP_TOLERANCE_S,
): number | null {
  const { peaks, duration } = snapPeaks;
  const n = peaks.length;
  if (n === 0 || duration <= 0 || !Number.isFinite(time)) return null;
  const binW = duration / n;
  const center = Math.min(n - 1, Math.max(0, Math.floor(time / binW)));
  const radius = Math.max(1, Math.round(toleranceS / binW));
  const lo = Math.max(0, center - radius);
  const hi = Math.min(n - 1, center + radius);
  let maxIdx = lo;
  for (let i = lo + 1; i <= hi; i++) {
    if (peaks[i] > peaks[maxIdx]) maxIdx = i;
  }
  if (peaks[maxIdx] < MIN_PEAK_AMPLITUDE) return null;
  if (maxIdx === lo && lo > 0 && peaks[lo - 1] > peaks[lo]) return null;
  if (maxIdx === hi && hi < n - 1 && peaks[hi + 1] > peaks[hi]) return null;
  return (maxIdx + 0.5) * binW;
}
```

- [ ] **Step 2: MarkerLayer - snapPeaks prop and drop snap**

In `src/components/MarkerLayer.tsx`:

Add the import:

```ts
import { snapToPeak, type SnapPeaks } from "@/lib/peak-snap";
```

Add to `MarkerLayerProps`:

```ts
/** High-resolution peaks used to snap a drag-drop onto the nearest audio
 *  transient (#28). Absent = no peak snapping (grid snap only). Shift
 *  held at drop always bypasses. */
snapPeaks?: SnapPeaks;
```

Destructure `snapPeaks` in `MarkerLayerInner`. In `handlePointerUp`, replace the commit block (after the `if (!moved)` early return) with:

```ts
const live = markers.find((m) => m.id === marker.id);
let final = live?.time ?? marker.time;
// Peak-snap the drop unless Shift is held (Shift = exactly where I put
// it). Live drag keeps detector-grid snapping; only the commit snaps.
if (!e.shiftKey && snapPeaks) {
  const snapped = snapToPeak(final, snapPeaks);
  if (snapped != null && snapped !== final) {
    final = snapped;
    onTimeChange(marker.id, snapped);
  }
}
onTimeChangeCommit?.(marker.id, final);
```

Add `snapPeaks` to the `handlePointerUp` dependency array. Update the doc comment at the top of the file (drag drop peak-snaps when `snapPeaks` is provided; Shift bypasses).

- [ ] **Step 3: Waveform - pass shiftKey through onDoubleClick**

In `src/components/Waveform.tsx`, change the prop type:

```ts
/** Fires on a double-click on the waveform background. The parent can
 *  use this to add a manual marker at the clicked time (issue #15).
 *  `shiftKey` lets the parent bypass peak-snapping (#28). */
onDoubleClick?: (timeSeconds: number, shiftKey: boolean) => void;
```

and the call site:

```ts
onDoubleClick(timeFromEvent(e.clientX), e.shiftKey);
```

(PromoteReview does not pass `onDoubleClick`, and narrower handlers stay assignable, so no other call sites change.)

- [ ] **Step 4: Audit.tsx - snap-peaks fetch, snap on add, wire the prop**

In `src/pages/Audit.tsx`:

Add imports:

```ts
import { snapToPeak, type SnapPeaks } from "@/lib/peak-snap";
```

Add state next to the peaks state (line ~140):

```ts
const [snapPeaks, setSnapPeaks] = useState<SnapPeaks | null>(null);
```

Add an effect directly after the existing "Load peaks." effect (line ~547). Keying on `peaks` means every refetch path (initial load, post-detect refresh at lines ~1613/~1771) refreshes the snap array too:

```ts
// High-resolution peaks for drop/add peak-snapping (#28). The render
// fetch stays at PEAK_BINS so visuals do not change; snapping wants
// ~10 ms bins, which PEAK_BINS only delivers on clips under ~15 s.
// Until this resolves, drops fall back to grid snapping - never blocks.
useEffect(() => {
  if (!peaks || stageNumber == null) {
    setSnapPeaks(null);
    return;
  }
  const bins = Math.min(8192, Math.max(PEAK_BINS, Math.ceil(peaks.duration / 0.01)));
  if (bins <= PEAK_BINS) {
    setSnapPeaks({ peaks: peaks.peaks, duration: peaks.duration });
    return;
  }
  let alive = true;
  api
    .getStagePeaks(slug, stageNumber, bins)
    .then((p) => {
      if (alive) setSnapPeaks({ peaks: p.peaks, duration: p.duration });
    })
    .catch(() => {
      if (alive) setSnapPeaks(null);
    });
  return () => {
    alive = false;
  };
}, [peaks, slug, stageNumber]);
```

Change `handleAddManual` (line ~876) to accept and use the shift flag:

```ts
const handleAddManual = useCallback(
  (time: number, shiftKey = false) => {
    const snapped = !shiftKey && snapPeaks ? snapToPeak(time, snapPeaks) : null;
    const t = snapped ?? time;
    const id = `manual-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    recordEvent("marker_added_manual", { id, time: t });
    mutate([
      ...markers,
      {
        id,
        kind: "manual",
        time: t,
        candidateNumber: null,
        confidence: null,
        peakAmplitude: null,
        note: "",
      },
    ]);
    setFocusedMarkerId(id);
  },
  [markers, mutate, recordEvent, snapPeaks],
);
```

Pass the prop in the render (line ~1855): add `snapPeaks={snapPeaks ?? undefined}` to `<MarkerLayer ...>`.

- [ ] **Step 5: Review.tsx - same wiring**

In `src/pages/Review.tsx`:

Add the same import. Add `const [snapPeaks, setSnapPeaks] = useState<SnapPeaks | null>(null);` next to the peaks state. Add the fetch effect after the peaks-load effect (line ~190), using the fixture endpoint:

```ts
// High-resolution peaks for drop/add peak-snapping (#28); see Audit.tsx.
useEffect(() => {
  if (!peaks || !fixturePath) {
    setSnapPeaks(null);
    return;
  }
  const bins = Math.min(8192, Math.max(PEAK_BINS, Math.ceil(peaks.duration / 0.01)));
  if (bins <= PEAK_BINS) {
    setSnapPeaks({ peaks: peaks.peaks, duration: peaks.duration });
    return;
  }
  let alive = true;
  api
    .getFixturePeaks(fixturePath, bins)
    .then((p) => {
      if (alive) setSnapPeaks({ peaks: p.peaks, duration: p.duration });
    })
    .catch(() => {
      if (alive) setSnapPeaks(null);
    });
  return () => {
    alive = false;
  };
}, [peaks, fixturePath]);
```

(Adjust the exact peaks/fixturePath variable names to what the file uses - check the peaks-load effect at line ~190.)

Change `handleAddManual` (line ~280) exactly as in Audit (accept `shiftKey = false`, snap via `snapToPeak`, record and store the snapped time, dep on `snapPeaks`).

Pass `snapPeaks={snapPeaks ?? undefined}` to `<MarkerLayer ...>` (line ~766).

- [ ] **Step 6: Typecheck and lint**

```bash
cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static
pnpm typecheck
pnpm exec eslint src/lib/peak-snap.ts src/components/MarkerLayer.tsx src/components/Waveform.tsx src/pages/Audit.tsx src/pages/Review.tsx
```
Expected: both exit 0.

- [ ] **Step 7: Commit**

```bash
cd /Users/mathias/work/splitsmith
git add src/splitsmith/ui_static/src/lib/peak-snap.ts \
        src/splitsmith/ui_static/src/components/MarkerLayer.tsx \
        src/splitsmith/ui_static/src/components/Waveform.tsx \
        src/splitsmith/ui_static/src/pages/Audit.tsx \
        src/splitsmith/ui_static/src/pages/Review.tsx
git commit -m "feat(audit): peak-snap marker drops and manual adds to the nearest transient (#28)"
```
(with trailer lines)

---

### Task 3: Region loop around the focused marker (#29)

**Files:**
- Modify: `src/styles/index.css` (new `--color-waveform-loop` token)
- Modify: `src/components/Waveform.tsx` (loopRegion prop + shaded rect)
- Modify: `src/pages/Audit.tsx` (loopRegion memo, rAF wrap, pause snap, focus seek)
- Modify: `src/pages/Review.tsx` (same)

**Interfaces:**
- Produces: `WaveformProps.loopRegion?: { start: number; end: number } | null`.
- Consumes: page-local `loopMode`, `focusedMarkerId`, `markers`, `peaks` state (already present in both pages).

- [ ] **Step 1: Add the CSS token**

In `src/styles/index.css`, next to the other waveform tokens (line ~201-203), add:

```css
--color-waveform-loop:            rgba(6, 182, 212, 0.12);
```

(Cyan family = playback/beep accents in the Shot Timer palette; the region is a spatial rect so color is not the sole carrier.)

- [ ] **Step 2: Waveform - loopRegion prop and shaded rect**

In `src/components/Waveform.tsx`, add to `WaveformProps`:

```ts
/** Shaded region indicating the section that repeats while loop mode is
 *  on (#29). Null/undefined = no shading. */
loopRegion?: { start: number; end: number } | null;
```

Destructure `loopRegion` in the component. In the draw effect, right after `ctx.clearRect(...)` and the `cssVar` reads, draw the region BEHIND the bars:

```ts
if (loopRegion && duration > 0) {
  const x1 = (Math.min(Math.max(loopRegion.start, 0), duration) / duration) * cssWidth;
  const x2 = (Math.min(Math.max(loopRegion.end, 0), duration) / duration) * cssWidth;
  if (x2 > x1) {
    ctx.fillStyle = cssVar("--color-waveform-loop", "rgba(6, 182, 212, 0.12)");
    ctx.fillRect(x1, 0, x2 - x1, cssHeight);
  }
}
```

Add `loopRegion` to the draw effect's dependency array.

- [ ] **Step 3: Audit.tsx - region computation and playback semantics**

In `src/pages/Audit.tsx`:

Add constants near `PEAK_BINS` (line ~108):

```ts
/** Region-loop window around the focused marker (#29): 0.5 s of pre-roll
 *  to hear the shot coming, 0.7 s of tail to catch echo/AGC behavior. */
const LOOP_PRE_S = 0.5;
const LOOP_POST_S = 0.7;
```

Add the memo after the `loopAnchorRef` declaration (line ~242):

```ts
// Loop region around the focused marker (#29). Loop on + a focused
// marker = repeat a tight window around it; loop on + no focus keeps
// the old loop-to-anchor behavior.
const loopRegion = useMemo(() => {
  if (!loopMode || !focusedMarkerId) return null;
  const m = markers.find((x) => x.id === focusedMarkerId);
  const dur = peaks?.duration;
  if (!m || dur == null) return null;
  return {
    start: Math.max(0, m.time - LOOP_PRE_S),
    end: Math.min(dur, m.time + LOOP_POST_S),
  };
}, [loopMode, focusedMarkerId, markers, peaks]);
```

(`markers` and `focusedMarkerId` state are declared around lines 146-148; place the memo after both.)

In the rAF tick (line ~604), replace the wrap condition so one branch serves both modes:

```ts
const regionEnd = loopRegion?.end ?? (dur != null ? dur - 0.05 : null);
if (loopMode && regionEnd != null && auditT >= regionEnd) {
  const target = loopRegion?.start ?? loopAnchorRef.current ?? 0;
  v.currentTime = target + beepOffset;
  setCurrentTime(target);
  // Snap secondaries to the loop target too.
  for (const [path, sv] of secondaryRefsMap.current) {
    const off = secondaryOffsetsRef.current.get(path);
    if (off != null) sv.currentTime = target + off;
  }
} else {
  ...existing else branch unchanged...
}
```

Add `loopRegion` to that effect's dependency array (`[isPlaying, beepOffset, loopMode, peaks, loopRegion]`).

In `togglePlay` (line ~704), the pause branch becomes region-aware:

```ts
if (loopMode && (loopRegion != null || loopAnchorRef.current != null)) {
  const target = loopRegion?.start ?? loopAnchorRef.current ?? 0;
  ...existing seek block using target, unchanged...
}
```

Add `loopRegion` to `togglePlay`'s dependency array.

Add a focus-follow effect after `togglePlay`:

```ts
// Stepping focus to another marker while region-looping seeks to the
// new region's pre-roll so the "step -> loop -> K -> step" review flow
// needs no extra scrubbing. Keyed on focusedMarkerId only: marker drags
// recompute loopRegion each frame and must not re-trigger the seek.
useEffect(() => {
  if (!loopMode || !isPlaying || !loopRegion) return;
  handleScrub(loopRegion.start);
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, [focusedMarkerId]);
```

Pass the prop in the render (line ~1845): add `loopRegion={loopRegion}` to `<Waveform ...>`.

- [ ] **Step 4: Review.tsx - same semantics in fixture coordinates**

In `src/pages/Review.tsx`:

Add the same `LOOP_PRE_S` / `LOOP_POST_S` constants near `PEAK_BINS` (line ~67) and the same `loopRegion` memo (state names match: `loopMode`, `focusedMarkerId`, `markers`, `peaks`).

In the rAF tick (line ~392), replace the wrap branch:

```ts
const regionEnd = loopRegion?.end ?? (dur != null ? dur - 0.05 : null);
if (loopMode && regionEnd != null && t >= regionEnd) {
  const target = loopRegion?.start ?? loopAnchorRef.current ?? 0;
  el.currentTime = mediaFromElapsed(target, el);
  setCurrentTime(target);
} else {
  setCurrentTime(t);
}
```

Add `loopRegion` to the effect deps. In `togglePlay` (line ~375), same region-aware pause target as Audit (using `mediaFromElapsed(target, el)`). Add the same focus-follow effect (Review's `handleScrub` already maps coordinates). Pass `loopRegion={loopRegion}` to `<Waveform ...>` (line ~752).

- [ ] **Step 5: Update loop button copy if it promises anchor behavior**

```bash
cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static
grep -rn -i "loop" src/components/VideoPanel.tsx src/components/audit/MultiCamColumn.tsx src/pages/Review.tsx --include=*.tsx | grep -i "title\|aria-label\|Loop the\|Loop off\|Loop on"
```

Where a `title`/`aria-label` describes loop as returning to the start (e.g. "Loop the fixture (R)"), extend it to "Loop (R) - repeats around the focused shot when one is selected". Keep it one short phrase; do not restructure components.

- [ ] **Step 6: Typecheck, lint, build**

```bash
cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static
pnpm typecheck
pnpm exec eslint src/components/Waveform.tsx src/pages/Audit.tsx src/pages/Review.tsx
pnpm build
```
Expected: all exit 0.

- [ ] **Step 7: Commit**

```bash
cd /Users/mathias/work/splitsmith
git add src/splitsmith/ui_static/src/styles/index.css \
        src/splitsmith/ui_static/src/components/Waveform.tsx \
        src/splitsmith/ui_static/src/pages/Audit.tsx \
        src/splitsmith/ui_static/src/pages/Review.tsx
# plus any copy-touched files from Step 5 - enumerate them explicitly, no globs
git commit -m "feat(audit): region loop around the focused marker (#29)"
```
(with trailer lines)

---

### Task 4: Verification and PR

**Files:** none new

- [ ] **Step 1: Full local gates**

```bash
cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static
pnpm typecheck && pnpm build
cd /Users/mathias/work/splitsmith
uv run ruff check . && uv run black --check .
uv run pytest -q -m "not integration and not docker"
```
Expected: all green (the diff is SPA-only; Python gates guard against accidental touches).

- [ ] **Step 2: Visual spot-check (best effort)**

Per the UI-verification notes: Playwright MCP `navigate` hangs on the SPA (live SSE), so use a bounded headless screenshot with `domcontentloaded`; the route is `/match/:matchId` (singular). If no local match project with audio is available, skip and say so in the PR body.

Check: markers render unchanged (18px glyphs), region shading appears when R is on and a marker is focused, drag still works, click near (but not on) a marker seeks.

- [ ] **Step 3: Push and open the PR**

```bash
cd /Users/mathias/work/splitsmith
git push -u origin fix/audit-waveform-interaction
gh pr create --title "fix(audit): waveform interaction batch - hit zones, peak-snap, region loop" --body "$(cat <<'EOF'
Closes #52, closes #28, closes #29.

Three audit-waveform interaction fixes, one commit each:

- **#52** marker hit zone 20px -> 10px (glyph unchanged), Esc cancels an
  in-flight drag, touch pointers get a 10px drag threshold. The 6px
  mouse drag threshold shipped earlier; this completes the issue.
- **#28** drag-drops and double-click adds snap to the nearest audio
  transient within 25 ms (Shift = raw, 1 ms grid, no snapping). Snapping
  is client-side over a second ~10 ms-per-bin peaks fetch; the rendered
  waveform keeps its 1500-bin fetch, so visuals are unchanged.
- **#29** loop (R) with a focused marker repeats [t-0.5, t+0.7] around
  it, shaded on the waveform; no focused marker keeps loop-to-anchor.
  Stepping N/M while looping follows the focus.

Design spec: docs/superpowers/specs/2026-07-02-audit-waveform-interaction-design.md

Verification: pnpm typecheck + build, scoped eslint, ruff/black/pytest
(SPA has no test runner; lib/peak-snap.ts is pure for the day one lands).

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01G2Z121nV8dPjgu248huLMX
EOF
)"
```

---

## Self-review notes

- Spec coverage: #52 remainder = Task 1; #28 = Task 2; #29 = Task 3; verification + rollout = Task 4. Click-toggles-keep decision needs no code (current behavior kept).
- Type consistency: `SnapPeaks` defined once in `lib/peak-snap.ts`, imported everywhere; `loopRegion` type is the inline `{start,end} | null` in both pages and the Waveform prop.
- The `handleAddManual` two-param change is backward compatible at the `Waveform.onDoubleClick` call site because the prop type change and both callers land in the same commit.
