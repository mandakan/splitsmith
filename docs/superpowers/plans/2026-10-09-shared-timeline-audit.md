# Shared Timeline, PR 2 (Audit) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The desktop Audit page moves its waveform, shot markers and anomaly pins onto the shared full-width `Timeline` band, with Coach's layout: video left, shot list right, the band full width below.

**Architecture:** PR 1 built `components/timeline/Timeline.tsx` (viewport, ruler, zoom, wheel, follow, tracks positioned by percentage of a zoomed content div) and `WaveformTrack` (a viewport-sized canvas). Audit needs what Coach did not: drag-scrub on the waveform, double-click to add a marker, the beep / timer-stop lines and the loop region, and the anomaly pins. Those become band features or track overlays; `MarkerLayer` drops into the audio track unchanged because it already positions by percentage of its parent. Audit keeps its clip-absolute time axis: the band's domain is `[0, peaks.duration]` clip seconds and its `origin` is the beep, so the ruler reads seconds from the beep (negative before it).

**Tech Stack:** React 19 + TypeScript, Tailwind, vitest + Testing Library, pnpm in `src/splitsmith/ui_static`.

**Spec:** `docs/superpowers/specs/2026-10-09-shared-timeline-design.md` (issue #1352). PR 1: #1356.

## Global Constraints

- Desktop Audit only (`pages/Audit.tsx`). `pages/MobileAudit.tsx`, `components/audit/mobile/*`, `pages/Review.tsx`, `components/BeepSection.tsx` and `components/Waveform.tsx` are untouched.
- Layout (owner decision 2026-10-09): top row `lg:grid-cols-[minmax(0,1fr)_380px]` with the video (`MultiCamColumn` / `VideoPanel`) left and `ShotList` right; then full width: the header strip (camera chip, counts, legend), `TransportLine`, the band, `DesktopCommandLine` / desktop error, `CurrentShotLine`, in that order.
- Time: Audit's markers, beep and loop region stay in clip seconds. The band gets `duration = peaks.duration`, `origin = auditBeep ?? 0`, `currentTime` in clip seconds.
- Zoom: the band's (`null` Fit, up to 16x, never below Fit). Audit's own zoom state, `zoomToPixelsPerSecond` use, `waveformViewport` measuring, the static six-label ruler and the page's zoom-key branch go away; `TransportLine` loses its zoom cluster (the band header has it). `zoomToPixelsPerSecond` and `ZoomControls` stay exported for `Review.tsx`.
- Every existing Audit behaviour keeps working: scrub by dragging the waveform (rAF-throttled), double-click on the waveform adds a manual marker (Shift skips peak snapping), a double-click on a marker never adds one, marker drag / Esc / snap (MarkerLayer, unchanged), the beep line and timer-stop line shown only when the beep filter is on, the loop region, anomaly pins that jump on click, follow during playback.
- UI primitives only; no arbitrary `text-[...]` / `tracking-[...]`; red only for the playhead and focus. No new dependencies. Clean prose: ASCII punctuation.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01QpwUtKUhYZRaEbZthbv5HM`. `/usr/bin/git` only; never `git add -A`.

## Review Focus

1. **Scrub vs follow.** Dragging the waveform seeks continuously; the band's pointer-down rule must keep follow from scrolling under the drag, and the paused reveal rule from jumping on release. Test in Task 1.
2. **Double-click on a marker.** A double-click that lands on a marker (inside `[data-audit-marker]`) must not add a marker, through the band as it did through `Waveform`. Test in Task 1.
3. **Pins at the edges and when zoomed.** Anomaly pins sit at their time at every zoom and scroll, and clicking one jumps there. Test in Task 3.
4. **Beep filter off.** With the beep filter off the beep and timer-stop lines disappear and the ruler origin stays the beep (the axis must not jump). Test in Task 3.
5. **No peaks yet.** Before peaks load (`displayPeaks` null) the page renders the top row and no band, as it renders no waveform today; nothing measures a zero-width viewport into a stuck state. Test in Task 3.

---

### Task 1: Band interactions: scrub drag and double-click on seekable tracks

**Files:**
- Modify: `src/splitsmith/ui_static/src/components/timeline/Timeline.tsx`
- Test: `src/splitsmith/ui_static/src/components/timeline/Timeline.test.tsx`

**Interfaces:**
- Produces on `TimelineTrack`: `seekable?: boolean` now means press-and-drag scrubs (pointer capture, rAF-throttled `onSeek`), not only click; new `onDoubleClick?: (t: number, shiftKey: boolean) => void`. A pointer or double-click whose target is inside `[data-audit-marker]` is ignored by the track (MarkerLayer owns it).
- New `TimelineProps.onScrubEnd?: () => void`, called once when a scrub drag ends.

Behaviour:
- Pointer down (button 0) on a seekable track row, not inside `[data-audit-marker]`: `preventDefault`, capture the pointer, seek to the pointer's time; moves seek (one `onSeek` per animation frame, the latest time wins); up / cancel flush the pending time, release capture, call `onScrubEnd`. Times come from the content div's rect (same `tAt` as the ruler), clamped to the domain.
- The existing click-to-seek on a seekable row is replaced by the press (a press without movement seeks once).
- Double-click on a track with `onDoubleClick`, not inside `[data-audit-marker]`: `onDoubleClick(tAt(clientX), e.shiftKey)`.

- [ ] **Step 1: Failing tests** in `Timeline.test.tsx` (use the file's Harness; add a track with `seekable: true` and `onDoubleClick`, rendering `<div data-testid="track-s"><button data-audit-marker data-testid="marker" /></div>`):

```tsx
it("scrubs while dragging a seekable track, one seek per frame, and ends once", () => {
  const onSeek = vi.fn();
  const onScrubEnd = vi.fn();
  // render with onSeek, onScrubEnd and the seekable track
  const row = screen.getByTestId("track-s").parentElement!;
  fireEvent.pointerDown(row, { pointerId: 1, button: 0, clientX: 100 });
  fireEvent.pointerMove(row, { pointerId: 1, clientX: 200 });
  fireEvent.pointerMove(row, { pointerId: 1, clientX: 300 });
  act(() => vi.runOnlyPendingTimers()); // or flush rAF with the file's helper
  fireEvent.pointerUp(row, { pointerId: 1, clientX: 300 });
  expect(onSeek.mock.calls.map((c) => c[0])).toEqual([expect.closeTo(1, 2), expect.closeTo(3, 2)]);
  expect(onScrubEnd).toHaveBeenCalledTimes(1);
});

it("does not follow while scrubbing, and a paused release does not recentre", () => {
  // zoom 4, drag a seekable row from clientX 900 to 980, rerender with currentTime at each seek,
  // release, rerender once more with the last time: host.scrollLeft stays 0 throughout.
});

it("adds on double-click with the shift flag, but never on a marker", () => {
  const onDouble = vi.fn();
  // render with the track's onDoubleClick = onDouble
  fireEvent.doubleClick(screen.getByTestId("track-s").parentElement!, { clientX: 437, shiftKey: true });
  expect(onDouble).toHaveBeenCalledWith(expect.closeTo(4.37, 3), true);
  onDouble.mockClear();
  fireEvent.doubleClick(screen.getByTestId("marker"), { clientX: 437 });
  expect(onDouble).not.toHaveBeenCalled();
});

it("a press on a marker inside a seekable track does not scrub", () => {
  const onSeek = vi.fn();
  // render with onSeek
  fireEvent.pointerDown(screen.getByTestId("marker"), { pointerId: 2, button: 0, clientX: 437 });
  expect(onSeek).not.toHaveBeenCalled();
});
```

  These are drafts: rAF in jsdom needs `vi.useFakeTimers()` plus a `requestAnimationFrame` stub, or a direct stub that runs the callback on the next `runOnlyPendingTimers`; fix the harness, not the assertions. Show each failing first.

- [ ] **Step 2: Implement.** Keep the PR 1 rules: `onPointerDownCapture` on the band still marks the pointer down; follow still keys on `currentTime`.
- [ ] **Step 3: Run** `pnpm exec vitest run src/components/timeline`, typecheck, lint. Coach's seekable Audio track now scrubs on drag; run `pnpm exec vitest run src/pages/Coach.test.tsx` too.
- [ ] **Step 4: Commit** `feat(timeline): drag to scrub and double-click on seekable tracks (#1352)`.

---

### Task 2: Waveform overlays: beep, timer stop, loop region

**Files:**
- Modify: `src/splitsmith/ui_static/src/components/timeline/WaveformTrack.tsx`
- Test: `src/splitsmith/ui_static/src/components/timeline/WaveformTrack.test.tsx`

**Interfaces:**
- New optional props on `WaveformTrack`, all in clip seconds (the same axis as `from` / `to`): `beepTime?: number | null`, `timerStopTime?: number | null`, `loopRegion?: { start: number; end: number } | null`.
- Drawn as absolutely positioned DOM elements in the track (percent of the track width: `(t - from) / (to - from)`), not on the canvas, so they need no redraw on scroll and are testable: beep a dashed 1 px line in the beep colour (`border-beep`, `border-dashed`), timer stop a dotted 1.5 px line in the same colour, the loop region a `bg-beep/10` block behind the canvas. Each skipped when null or outside `[from, to]`. Test ids `wave-beep`, `wave-timer-stop`, `wave-loop`.

- [ ] **Step 1: Failing tests:** each overlay at the right percentage (`from 0, to 10`: beep 2.5 -> left 25%; loop 2-4 -> left 20% width 20%); null and out-of-range values render nothing; the overlays have `pointer-events-none`.
- [ ] **Step 2: Implement.** Wrap the canvas and overlays in a `relative` div of the track's full height; the canvas stays `sticky left-0`.
- [ ] **Step 3: Run** `pnpm exec vitest run src/components/timeline`, typecheck, lint.
- [ ] **Step 4: Commit** `feat(timeline): beep, timer-stop and loop overlays on the waveform track (#1352)`.

---

### Task 3: Audit on the band

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Audit.tsx` (grid ~1971-2199, zoom state ~256-281, pps ~1180, zoom keys ~1371-1382, ruler ~2040-2044)
- Modify: `src/splitsmith/ui_static/src/components/audit/TransportLine.tsx` (drop the zoom cluster and its props)
- Modify: `src/splitsmith/ui_static/src/components/audit/AuditChrome.test.tsx` (the TransportLine zoom test moves; see below)
- Test: a new `src/splitsmith/ui_static/src/pages/Audit.timeline.test.tsx` (render the page the way `Audit.refusal.test.tsx` does, with `getStagePeaks` stubbed)

**Interfaces:**
- Consumes: `Timeline` (with Task 1's scrub / double-click), `WaveformTrack` (with Task 2's overlays), `MarkerLayer` (unchanged), `AnomalyPins` (unchanged).

Wiring:
1. Layout per the Global Constraints. The band only renders when `displayPeaks` is set, as the waveform does today.
2. Band props: `duration={displayPeaks.duration}`, `origin={auditBeep ?? 0}`, `fps` from the active video when the page has it (else 30), `currentTime` (clip seconds, as the Waveform got), `playing` from the page's play state, `onSeek={handleScrub}`, `onScrubEnd` unset unless the page needs it, `zoom` / `onZoomChange` from a `useState<Zoom>(null)` that replaces the old `zoom` state, `menuExtra` empty (TransportLine keeps its own menus), `title="Waveform"`.
3. Tracks:
   - `flags`: rows `[{ label: "Flags", height: 18 }]`, renders `<AnomalyPins anomalies=... duration={displayPeaks.duration} view={{ contentWidth: geom.contentWidth, viewportWidth: geom.contentWidth, scrollLeft: 0 }} onJump=... />` so a pin's x is its content x (the band scrolls it). Only when the page has anomalies; otherwise omit the track.
   - `audio`: rows `[{ label: "Audio", height: 140 }]`, `seekable: true`, `onDoubleClick: handleAddManual`, renders `<WaveformTrack peaks={displayPeaks.peaks} clipDuration={displayPeaks.duration} from={0} to={displayPeaks.duration} geom={geom} height={140} beepTime={filters.beep ? auditBeep : null} timerStopTime={...the existing expression} loopRegion={loopRegion} />` followed by the existing `<MarkerLayer ... />` with its current props, inside one `relative h-full` wrapper so MarkerLayer's parent is the track div.
4. Delete: the old `zoom` state, `waveformViewport` + `waveformWrapperRef`, `pixelsPerSecond`, `waveView` + `setWaveView`, the static ruler, the zoom branch of the page's keydown handler (the band answers `+ - 0`), the `<Waveform>` import if unused. `TransportLine` drops `zoom` / `onZoomChange` and its zoom buttons; move its "zooms in from fit" test to Timeline coverage (delete it from AuditChrome.test.tsx and say so).
5. Keep `handleScrub`, `handleAddManual`, `loopRegion`, `filters`, `auditBeep`, marker handlers exactly as they are.

- [ ] **Step 1: Failing page tests** in `Audit.timeline.test.tsx`:
  - the band renders with Flags (when anomalies exist), Audio and `[data-audit-marker]` buttons inside the audio track, and is not inside the 380 px grid;
  - the video column precedes the shot list in the top row (DOM order);
  - double-clicking the audio row adds a manual marker (the marker count grows by one, through the page's real handler);
  - with the beep filter off, `wave-beep` and `wave-timer-stop` are absent and the ruler still labels `0` at the beep's x;
  - before peaks resolve there is no `timeline` test id and no crash;
  - `+` zooms the band (readout 1.5x) and the page's old zoom path is gone (no `Fit` text duplicated, no second zoom on one key press: assert onZoomChange-equivalent readout 1.5x, not 2.3x).
  Show each failing first.
- [ ] **Step 2: Implement the wiring.**
- [ ] **Step 3: Gate:** `pnpm typecheck && pnpm lint && pnpm test`.
- [ ] **Step 4: Commit** `feat(audit): the waveform, markers and pins on the full-width timeline (#1352)`.

---

### Task 4: Docs and the rendered check

**Files:**
- Modify: `CLAUDE.md` (the timeline paragraph in "Stage events" and the Audit entry in "UI: the visual budget": the band now hosts Audit's waveform; a new per-shot signal still goes on `ShotList`)
- Modify: `docs/superpowers/specs/2026-10-09-shared-timeline-design.md` (an "As built (PR 2)" section: layout decision, clip-absolute domain with origin at the beep, scrub/double-click on seekable tracks, DOM overlays, pins as a Flags track)
- Modify: `src/splitsmith/data/whats_new.json` (one entry, via the `whats-new` skill)

- [ ] **Step 1:** Edits; `uv run pytest tests/test_whats_new.py -q -n0`.
- [ ] **Step 2: Rendered check:** demo match (`scripts/seed_demo_match.py --media`, `SPLITSMITH_HOME` scratch, `SPLITSMITH_AUTO_SYNC=0`), Audit on stage 03 at 1440x900: Fit, zoomed around a shot, and a scrub mid-drag. Save to `~/.claude-tmp/timeline-pr2/`. The controller looks at the images.
- [ ] **Step 3: Commit** `docs: Audit on the shared timeline band (#1352)`.
