# Shared Timeline, PR 1 (core + Coach) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Coach's lane editor becomes a full-width, zoomable timeline band with an audio waveform track, built from a shared `Timeline` component that Audit and the beep step adopt in later PRs.

**Architecture:** All geometry (zoom, wheel rules, ruler ticks, follow-playhead, waveform columns) is a pure module, `lib/timelineView.ts`. `components/timeline/Timeline.tsx` owns the viewport (scroll host, ruler, playhead, controls, menu, wheel and keys) and renders page-supplied tracks inside a content div whose width is the zoomed width, so anything that positions by percentage of that div (the lane editor, `MarkerLayer`) works unchanged. `WaveformTrack` draws only the visible window into a viewport-sized canvas.

**Tech Stack:** React 19 + TypeScript, Tailwind, vitest + Testing Library (jsdom), pnpm in `src/splitsmith/ui_static`.

**Spec:** `docs/superpowers/specs/2026-10-09-shared-timeline-design.md` (issue #1352, epic #1321). Mockup: https://art.urdr.dev/dnsszdcnwyii

## Global Constraints

- Desktop only (`lg` and up). Phone surfaces (`MobileBeepReview`, `BeepReticle`, `ZoomLane`, `WrappedWaveform`) and `pages/Review.tsx` are untouched.
- Zoom is `number | null`: `null` is Fit, a number is a multiplier over Fit in `(1, 16]`; zooming out to 1 or below is Fit. `MAX_ZOOM` 16, `ZOOM_STEP` 1.5.
- Keys are `lib/zoomKeys.zoomActionForKey`, unchanged (`+`/`=`, `-`, `0` Fit, Cmd/Ctrl+1/2/3).
- Wheel, toggle off (default): plain vertical wheel is the page's (never `preventDefault`ed); Shift+wheel or a horizontal swipe pans; Ctrl/Cmd+wheel or pinch zooms around the pointer. Toggle on: plain vertical wheel zooms around the pointer.
- Prefs per browser: `splitsmith.timeline.wheelZooms` (default off), `splitsmith.timeline.followPlayhead` (default on), `useSyncExternalStore` stores in the shape of `lib/logoSpots.ts`.
- Lane-label gutter 96 px. Ruler in seconds from the beep (`origin`).
- Sidebar collapse shortcut: Cmd/Ctrl+B, ignored while typing (`isTypingTextTarget` from `lib/audit-input.ts`), `preventDefault`ed.
- Every rule in CLAUDE.md "Stage events" (pointer, keyboard, save) keeps holding: the lane editor's behaviour changes only in where it is drawn.
- UI primitives only (`components/ui`: `Label`, `Button`, `Menu`/`menuItemClass`, `Kbd`, `numeral`); no arbitrary `text-[...]` / `tracking-[...]`. Red (`led`) only for the playhead and focus.
- No new dependencies. Clean prose in copy and comments: ASCII punctuation, no em dashes.
- Commits: conventional, ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01QpwUtKUhYZRaEbZthbv5HM`.
- SPA commands run from `src/splitsmith/ui_static`: `pnpm exec vitest run <file>`, `pnpm typecheck`, `pnpm lint`, `pnpm test`.

## Rulings against the spec

- **Waveform drawing.** The spec has `Waveform.tsx` rebuilt on `WaveformTrack`. A full-content canvas fails at high zoom (Chromium caps a canvas side at 32767 device px: a 60 s stage at 16x on a 1200 px viewport at DPR 2 is 38400), so `WaveformTrack` draws a viewport-sized canvas from `columnPeaks`. `Waveform.tsx` is left alone here; Audit moves to `WaveformTrack` in PR 2 and Review keeps `Waveform`. Task 8 amends the spec.
- **Below-fit zoom.** Audit's `ZoomControls` allows 0.25x-1x (content narrower than the viewport). The timeline does not: content never narrower than the viewport. Audit inherits this in PR 2.
- **Coach domain.** The band spans `[0, stageTime]` seconds from the beep, the domain the lane editor already uses; the waveform shows the matching slice of the clip.

## Review Focus

1. **Follow-playhead during a drag.** Dragging a reload handle near the viewport edge seeks the video, which moves the playhead; if follow scrolls then, the content shifts under the pointer and the region jumps. Expected: no follow scroll while any pointer is down in the band. Test in Task 4.
2. **Huge canvas.** At 16x on a long stage the waveform must still draw. Expected: the canvas is never wider than the viewport (times DPR). Test in Task 3.
3. **Horizontal swipe at the edge.** A sideways trackpad swipe over the band at scroll 0 (or at Fit) must not trigger the browser's back navigation. Expected: a horizontal wheel over the band is always `preventDefault`ed. Test in Task 4.
4. **A drag that leaves the visible window.** Expected: the lane editor keeps mapping the pointer through the content div's rect, so times beyond the visible window are correct (no auto-pan in this PR). Test in Task 5.
5. **Stage change while zoomed.** Expected: Coach resets zoom to Fit and scroll to 0 on a new stage; a stale `scrollLeft` never survives into a shorter stage. Test in Task 6.

---

### Task 1: Pure geometry, `lib/timelineView.ts`

**Files:**
- Create: `src/splitsmith/ui_static/src/lib/timelineView.ts`
- Test: `src/splitsmith/ui_static/src/lib/timelineView.test.ts`

**Interfaces:**
- Produces: `Zoom`, `MAX_ZOOM`, `ZOOM_STEP`, `clampZoom(z: number): Zoom`, `zoomStep(zoom: Zoom, dir: 1 | -1): Zoom`, `contentWidth(zoom: Zoom, viewportPx: number): number`, `zoomAround(view: { zoom: Zoom; scrollLeft: number }, next: Zoom, anchorPx: number, viewportPx: number): { zoom: Zoom; scrollLeft: number }`, `WheelInput`, `WheelAction`, `wheelAction(e: WheelInput, wheelZooms: boolean): WheelAction`, `applyWheelZoom(zoom: Zoom, factor: number): Zoom`, `RulerTick`, `rulerTicks(t0: number, t1: number, pxPerSec: number, origin: number, fps: number): RulerTick[]`, `followScroll(playheadPx: number, scrollLeft: number, viewportPx: number, contentPx: number): number | null`, `columnPeaks(peaks: number[], clipDuration: number, from: number, to: number, contentPx: number, scrollLeft: number, columns: number): number[]`.

- [ ] **Step 1: Write the failing tests**

```ts
// src/splitsmith/ui_static/src/lib/timelineView.test.ts
import { describe, expect, it } from "vitest";

import {
  MAX_ZOOM,
  applyWheelZoom,
  clampZoom,
  columnPeaks,
  contentWidth,
  followScroll,
  rulerTicks,
  wheelAction,
  zoomAround,
  zoomStep,
  type WheelInput,
} from "./timelineView";

const wheel = (p: Partial<WheelInput>): WheelInput => ({
  deltaX: 0,
  deltaY: 0,
  deltaMode: 0,
  ctrlKey: false,
  metaKey: false,
  shiftKey: false,
  ...p,
});

describe("zoom", () => {
  it("treats 1x and below as Fit and caps at MAX_ZOOM", () => {
    expect(clampZoom(1)).toBeNull();
    expect(clampZoom(0.5)).toBeNull();
    expect(clampZoom(Number.NaN)).toBeNull();
    expect(clampZoom(40)).toBe(MAX_ZOOM);
    expect(clampZoom(2)).toBe(2);
  });

  it("steps 1.5x from Fit and back to Fit", () => {
    expect(zoomStep(null, 1)).toBe(1.5);
    expect(zoomStep(1.5, -1)).toBeNull();
    expect(zoomStep(null, -1)).toBeNull();
    expect(zoomStep(MAX_ZOOM, 1)).toBe(MAX_ZOOM);
  });

  it("content is the viewport at Fit and scales with zoom", () => {
    expect(contentWidth(null, 1000)).toBe(1000);
    expect(contentWidth(2, 1000)).toBe(2000);
  });

  it("keeps the anchor under the same pixel at every step from Fit to 16x and back", () => {
    const viewport = 1000;
    let view = { zoom: null as number | null, scrollLeft: 0 };
    const anchorPx = 730;
    const ratio = (v: typeof view) => (v.scrollLeft + anchorPx) / contentWidth(v.zoom, viewport);
    const start = ratio(view);
    for (let i = 0; i < 8; i++) {
      view = zoomAround(view, zoomStep(view.zoom, 1), anchorPx, viewport);
      expect(Math.abs(ratio(view) - start) * contentWidth(view.zoom, viewport)).toBeLessThan(1);
    }
    for (let i = 0; i < 8; i++) view = zoomAround(view, zoomStep(view.zoom, -1), anchorPx, viewport);
    expect(view).toEqual({ zoom: null, scrollLeft: 0 });
  });

  it("clamps the scroll to the content at both ends", () => {
    expect(zoomAround({ zoom: null, scrollLeft: 0 }, 4, 0, 1000).scrollLeft).toBe(0);
    expect(zoomAround({ zoom: 2, scrollLeft: 1000 }, 4, 1000, 1000).scrollLeft).toBe(3000);
  });

  it("wheel zoom multiplies and falls back to Fit", () => {
    expect(applyWheelZoom(null, 0.5)).toBeNull();
    expect(applyWheelZoom(2, 2)).toBe(4);
  });
});

describe("wheelAction", () => {
  it("leaves a plain vertical wheel to the page when the toggle is off", () => {
    expect(wheelAction(wheel({ deltaY: 100 }), false)).toBeNull();
  });

  it("zooms on a plain vertical wheel when the toggle is on, in = negative delta", () => {
    const a = wheelAction(wheel({ deltaY: -100 }), true);
    expect(a?.kind).toBe("zoom");
    expect(a && a.kind === "zoom" && a.factor).toBeGreaterThan(1);
  });

  it("zooms on Ctrl or Cmd wheel and on a pinch (ctrlKey, small delta) either way", () => {
    for (const on of [false, true]) {
      expect(wheelAction(wheel({ deltaY: 100, ctrlKey: true }), on)?.kind).toBe("zoom");
      expect(wheelAction(wheel({ deltaY: 100, metaKey: true }), on)?.kind).toBe("zoom");
      const pinch = wheelAction(wheel({ deltaY: -3, ctrlKey: true }), on);
      expect(pinch && pinch.kind === "zoom" && pinch.factor).toBeGreaterThan(1);
      expect(pinch && pinch.kind === "zoom" && pinch.factor).toBeLessThan(1.05);
    }
  });

  it("pans on Shift+wheel (vertical or already converted to horizontal) and on a sideways swipe", () => {
    for (const on of [false, true]) {
      expect(wheelAction(wheel({ deltaY: 40, shiftKey: true }), on)).toEqual({ kind: "pan", px: 40 });
      expect(wheelAction(wheel({ deltaX: 40, shiftKey: true }), on)).toEqual({ kind: "pan", px: 40 });
      expect(wheelAction(wheel({ deltaX: -25, deltaY: 3 }), on)).toEqual({ kind: "pan", px: -25 });
    }
  });

  it("normalises line and page delta modes", () => {
    expect(wheelAction(wheel({ deltaY: 3, deltaMode: 1, shiftKey: true }), false)).toEqual({ kind: "pan", px: 48 });
    expect(wheelAction(wheel({ deltaX: 1, deltaMode: 2 }), false)).toEqual({ kind: "pan", px: 800 });
  });

  it("is null for an empty event", () => {
    expect(wheelAction(wheel({}), true)).toBeNull();
  });
});

describe("rulerTicks", () => {
  const labels = (ticks: ReturnType<typeof rulerTicks>) => ticks.filter((t) => t.label !== undefined).map((t) => t.label);

  it("labels whole seconds every 5 s at a fit-like scale", () => {
    // 20 px/s: minor 1 s (20 px), major 5 s.
    expect(labels(rulerTicks(0, 20, 20, 0, 30))).toEqual(["0", "5", "10", "15", "20"]);
  });

  it("labels relative to the origin, negatives included", () => {
    // Origin 3 s, 20 px/s: majors every 5 s from the origin -> t = 3, 8 (and -2 is before t0).
    const ticks = rulerTicks(0, 10, 20, 3, 30).filter((t) => t.label !== undefined);
    expect(ticks.map((t) => [t.t, t.label])).toEqual([
      [3, "0"],
      [8, "5"],
    ]);
    const before = rulerTicks(-5, 0, 20, 3, 30).filter((t) => t.label !== undefined);
    expect(before.map((t) => t.label)).toEqual(["-5"]);
  });

  it("refines to tenths and then frames, and labels never sit closer than 60 px", () => {
    for (const [pps, fps] of [
      [20, 30],
      [80, 30],
      [400, 30],
      [3000, 60],
      [3000, 25],
      [3000, 24],
    ] as const) {
      const ticks = rulerTicks(0, 2, pps, 0, fps);
      const xs = ticks.filter((t) => t.label !== undefined).map((t) => t.t * pps);
      for (let i = 1; i < xs.length; i++) expect(xs[i] - xs[i - 1]).toBeGreaterThanOrEqual(60);
      const minor = ticks.length > 1 ? (ticks[1].t - ticks[0].t) * pps : Infinity;
      expect(minor).toBeGreaterThanOrEqual(6);
    }
    // 3000 px/s at 60 fps: a frame is 50 px, so the minor step is one frame.
    const fine = rulerTicks(0, 0.1, 3000, 0, 60);
    expect(fine[1].t - fine[0].t).toBeCloseTo(1 / 60, 9);
  });

  it("formats tenths with one decimal", () => {
    // 200 px/s: minor 0.1 s, major 0.5 s.
    expect(labels(rulerTicks(1, 2, 200, 0, 30))).toEqual(["1.0", "1.5", "2.0"]);
  });
});

describe("followScroll", () => {
  it("does nothing at Fit or while the playhead is inside the band", () => {
    expect(followScroll(500, 0, 1000, 1000)).toBeNull();
    expect(followScroll(500, 0, 1000, 4000)).toBeNull();
  });

  it("centres the playhead when it leaves the 10 % band, clamped to the content", () => {
    expect(followScroll(950, 0, 1000, 4000)).toBe(450);
    expect(followScroll(3990, 2000, 1000, 4000)).toBe(3000);
    expect(followScroll(10, 500, 1000, 4000)).toBe(0);
  });
});

describe("columnPeaks", () => {
  it("takes the loudest bin under each column of the visible window", () => {
    // 10 bins over a 10 s clip; the track shows clip 2..6 s over 400 px content, scrolled to 0.
    const peaks = [0, 0, 0.2, 0.9, 0.1, 0.4, 0, 0, 0, 0];
    const cols = columnPeaks(peaks, 10, 2, 6, 400, 0, 4);
    expect(cols).toEqual([0.2, 0.9, 0.1, 0.4]);
  });

  it("only covers the visible window when scrolled", () => {
    const peaks = [0, 0, 0.2, 0.9, 0.1, 0.4, 0, 0, 0, 0];
    expect(columnPeaks(peaks, 10, 2, 6, 400, 200, 2)).toEqual([0.1, 0.4]);
  });

  it("is zero outside the clip and empty without peaks", () => {
    expect(columnPeaks([1, 1], 2, -2, 2, 4, 0, 4)).toEqual([0, 0, 1, 1]);
    expect(columnPeaks([], 2, 0, 2, 4, 0, 4)).toEqual([0, 0, 0, 0]);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pnpm exec vitest run src/lib/timelineView.test.ts`
Expected: FAIL, cannot resolve `./timelineView`.

- [ ] **Step 3: Write the implementation**

```ts
// src/splitsmith/ui_static/src/lib/timelineView.ts
/**
 * Geometry of the shared timeline band (spec 2026-10-09): zoom, wheel
 * rules, ruler ticks, follow-playhead and the waveform's visible columns.
 * Pure: the component (components/timeline/Timeline.tsx) owns the DOM.
 *
 * Zoom is a multiplier over Fit; ``null`` is Fit. Content is never
 * narrower than the viewport, so 1x and below are Fit.
 */

export type Zoom = number | null;

export const MAX_ZOOM = 16;
export const ZOOM_STEP = 1.5;
/** exp(-deltaY * rate): a 100 px wheel notch is about 1.28x, a pinch frame a few percent. */
const ZOOM_WHEEL_RATE = 0.0025;
const LINE_PX = 16;
const PAGE_PX = 800;

export function clampZoom(z: number): Zoom {
  if (!Number.isFinite(z) || z <= 1 + 1e-9) return null;
  return Math.min(MAX_ZOOM, z);
}

export function zoomStep(zoom: Zoom, dir: 1 | -1): Zoom {
  return clampZoom((zoom ?? 1) * (dir === 1 ? ZOOM_STEP : 1 / ZOOM_STEP));
}

export function applyWheelZoom(zoom: Zoom, factor: number): Zoom {
  return clampZoom((zoom ?? 1) * factor);
}

export function contentWidth(zoom: Zoom, viewportPx: number): number {
  return zoom === null ? viewportPx : Math.round(viewportPx * zoom);
}

/** Zoom to ``next`` keeping the content point under ``anchorPx`` (viewport x) where it is. */
export function zoomAround(
  view: { zoom: Zoom; scrollLeft: number },
  next: Zoom,
  anchorPx: number,
  viewportPx: number,
): { zoom: Zoom; scrollLeft: number } {
  const before = contentWidth(view.zoom, viewportPx);
  const after = contentWidth(next, viewportPx);
  if (before <= 0 || after <= viewportPx) return { zoom: next, scrollLeft: 0 };
  const ratio = (view.scrollLeft + anchorPx) / before;
  const scrollLeft = Math.min(Math.max(ratio * after - anchorPx, 0), after - viewportPx);
  return { zoom: next, scrollLeft };
}

export interface WheelInput {
  deltaX: number;
  deltaY: number;
  /** WheelEvent.deltaMode: 0 pixels, 1 lines, 2 pages. */
  deltaMode: number;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
}

export type WheelAction = { kind: "zoom"; factor: number } | { kind: "pan"; px: number } | null;

/**
 * What a wheel over the band does. ``null`` leaves it to the page: a plain
 * vertical wheel with the toggle off must scroll the page, as everywhere
 * else. Browsers deliver a trackpad pinch as a wheel with ``ctrlKey``.
 */
export function wheelAction(e: WheelInput, wheelZooms: boolean): WheelAction {
  const k = e.deltaMode === 1 ? LINE_PX : e.deltaMode === 2 ? PAGE_PX : 1;
  const dx = e.deltaX * k;
  const dy = e.deltaY * k;
  if (e.ctrlKey || e.metaKey) return dy === 0 ? null : { kind: "zoom", factor: Math.exp(-dy * ZOOM_WHEEL_RATE) };
  if (e.shiftKey) {
    // Windows and Linux browsers turn Shift+wheel into deltaX already; macOS keeps deltaY.
    const px = dx !== 0 ? dx : dy;
    return px === 0 ? null : { kind: "pan", px };
  }
  if (dx !== 0 && Math.abs(dx) >= Math.abs(dy)) return { kind: "pan", px: dx };
  if (wheelZooms && dy !== 0) return { kind: "zoom", factor: Math.exp(-dy * ZOOM_WHEEL_RATE) };
  return null;
}

export interface RulerTick {
  /** Time in the timeline's own frame (not origin-relative). */
  t: number;
  /** Set on labelled (major) ticks: seconds from the origin. */
  label?: string;
}

const MIN_MINOR_PX = 6;
const MIN_LABEL_PX = 60;
/** Coarse to fine: minor step, major step, label decimals. */
const RUNGS: { minor: number; major: number; decimals: number }[] = [
  { minor: 10, major: 30, decimals: 0 },
  { minor: 5, major: 10, decimals: 0 },
  { minor: 1, major: 5, decimals: 0 },
  { minor: 1, major: 2, decimals: 0 },
  { minor: 0.5, major: 1, decimals: 0 },
  { minor: 0.1, major: 0.5, decimals: 1 },
  { minor: 0.1, major: 0.2, decimals: 1 },
];

/** Ticks for the visible window [t0, t1]. Frames are the finest minor step. */
export function rulerTicks(t0: number, t1: number, pxPerSec: number, origin: number, fps: number): RulerTick[] {
  if (!(pxPerSec > 0) || !(t1 > t0)) return [];
  let rung = RUNGS[0];
  for (const r of RUNGS) {
    if (r.minor * pxPerSec >= MIN_MINOR_PX && r.major * pxPerSec >= MIN_LABEL_PX) rung = r;
  }
  let { minor } = rung;
  const { major, decimals } = rung;
  const frame = fps > 0 ? 1 / fps : 0;
  if (frame > 0 && frame < minor && frame * pxPerSec >= MIN_MINOR_PX && major * pxPerSec >= MIN_LABEL_PX) minor = frame;
  const fmt = (rel: number) => {
    const text = rel.toFixed(decimals);
    return text === "-0" || text === "-0.0" ? text.slice(1) : text;
  };
  const range = (step: number) => [Math.ceil((t0 - origin) / step - 1e-9), Math.floor((t1 - origin) / step + 1e-9)];
  const out: RulerTick[] = [];
  // Majors on their own grid, so a frame step that does not divide them (24 fps) still labels whole tenths.
  const [m0, m1] = range(major);
  for (let k = m0; k <= m1; k++) out.push({ t: origin + k * major, label: fmt(k * major) });
  const [n0, n1] = range(minor);
  for (let k = n0; k <= n1; k++) {
    const q = (k * minor) / major;
    if (Math.abs(q - Math.round(q)) < 1e-6) continue;
    out.push({ t: origin + k * minor });
  }
  return out.sort((a, b) => a.t - b.t);
}

/** New scrollLeft when the playhead leaves the middle 80 %, else null. */
export function followScroll(playheadPx: number, scrollLeft: number, viewportPx: number, contentPx: number): number | null {
  if (contentPx <= viewportPx || viewportPx <= 0) return null;
  const margin = viewportPx * 0.1;
  if (playheadPx >= scrollLeft + margin && playheadPx <= scrollLeft + viewportPx - margin) return null;
  return Math.min(Math.max(playheadPx - viewportPx / 2, 0), contentPx - viewportPx);
}

/**
 * Loudest peak under each of ``columns`` viewport columns. The track shows
 * clip seconds [from, to] across ``contentPx`` and is scrolled by
 * ``scrollLeft``; peaks are ``peaks.length`` bins over ``clipDuration``.
 */
export function columnPeaks(
  peaks: number[],
  clipDuration: number,
  from: number,
  to: number,
  contentPx: number,
  scrollLeft: number,
  columns: number,
): number[] {
  const out = new Array<number>(Math.max(columns, 0)).fill(0);
  const n = peaks.length;
  if (n === 0 || clipDuration <= 0 || contentPx <= 0 || to <= from) return out;
  const secPerPx = (to - from) / contentPx;
  const binsPerSec = n / clipDuration;
  for (let c = 0; c < columns; c++) {
    const a = from + (scrollLeft + c) * secPerPx;
    const b = a + secPerPx;
    let lo = Math.floor(a * binsPerSec + 1e-9);
    let hi = Math.ceil(b * binsPerSec - 1e-9) - 1;
    if (hi < 0 || lo >= n) continue;
    lo = Math.max(lo, 0);
    hi = Math.min(Math.max(hi, lo), n - 1);
    let m = 0;
    for (let i = lo; i <= hi; i++) if (peaks[i] > m) m = peaks[i];
    out[c] = m;
  }
  return out;
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pnpm exec vitest run src/lib/timelineView.test.ts`
Expected: PASS. The ruler tests are a draft against this code: if one fails, check the code against the spec's ladder (5 s, 1 s, 0.5 s, 0.1 s, frames, labels never closer than 60 px) before changing a test, and report any change you make to a test.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/timelineView.ts src/splitsmith/ui_static/src/lib/timelineView.test.ts
git commit -m "feat(timeline): pure geometry for the shared timeline band (#1352)"
```

---

### Task 2: Per-browser prefs, `lib/timelinePrefs.ts`

**Files:**
- Create: `src/splitsmith/ui_static/src/lib/timelinePrefs.ts`
- Test: `src/splitsmith/ui_static/src/lib/timelinePrefs.test.ts`

**Interfaces:**
- Produces: `useWheelZooms(): [boolean, (on: boolean) => void]` (default false), `useFollowPlayhead(): [boolean, (on: boolean) => void]` (default true), `WHEEL_ZOOMS_KEY = "splitsmith.timeline.wheelZooms"`, `FOLLOW_PLAYHEAD_KEY = "splitsmith.timeline.followPlayhead"`, `resetTimelinePrefsForTests(): void`.

- [ ] **Step 1: Write the failing test**

```ts
// src/splitsmith/ui_static/src/lib/timelinePrefs.test.ts
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { FOLLOW_PLAYHEAD_KEY, WHEEL_ZOOMS_KEY, resetTimelinePrefsForTests, useFollowPlayhead, useWheelZooms } from "./timelinePrefs";

afterEach(() => {
  window.localStorage.clear();
  resetTimelinePrefsForTests();
});

describe("timeline prefs", () => {
  it("defaults: wheel zoom off, follow playhead on", () => {
    expect(renderHook(() => useWheelZooms()).result.current[0]).toBe(false);
    expect(renderHook(() => useFollowPlayhead()).result.current[0]).toBe(true);
  });

  it("persists and is shared by every mounted hook", () => {
    const a = renderHook(() => useWheelZooms());
    const b = renderHook(() => useWheelZooms());
    act(() => a.result.current[1](true));
    expect(b.result.current[0]).toBe(true);
    expect(window.localStorage.getItem(WHEEL_ZOOMS_KEY)).toBe("on");
    const f = renderHook(() => useFollowPlayhead());
    act(() => f.result.current[1](false));
    expect(window.localStorage.getItem(FOLLOW_PLAYHEAD_KEY)).toBe("off");
  });

  it("reads a stored value", () => {
    window.localStorage.setItem(WHEEL_ZOOMS_KEY, "on");
    window.localStorage.setItem(FOLLOW_PLAYHEAD_KEY, "off");
    resetTimelinePrefsForTests();
    expect(renderHook(() => useWheelZooms()).result.current[0]).toBe(true);
    expect(renderHook(() => useFollowPlayhead()).result.current[0]).toBe(false);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm exec vitest run src/lib/timelinePrefs.test.ts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement**

```ts
// src/splitsmith/ui_static/src/lib/timelinePrefs.ts
/**
 * The timeline band's two per-browser switches, shared by Coach, Audit and
 * the beep step (spec 2026-10-09): "Wheel zooms the timeline" (off: a plain
 * wheel scrolls the page) and "Follow playhead" (on). Same shape as
 * lib/logoSpots.ts.
 */
import { useSyncExternalStore } from "react";

export const WHEEL_ZOOMS_KEY = "splitsmith.timeline.wheelZooms";
export const FOLLOW_PLAYHEAD_KEY = "splitsmith.timeline.followPlayhead";

function makePref(key: string, fallback: boolean) {
  const listeners = new Set<() => void>();
  let current: boolean | null = null;
  const read = (): boolean => {
    try {
      const v = window.localStorage.getItem(key);
      return v === null ? fallback : v === "on";
    } catch {
      return fallback;
    }
  };
  const set = (on: boolean) => {
    try {
      window.localStorage.setItem(key, on ? "on" : "off");
    } catch {
      /* storage blocked: the switch still works for this page */
    }
    current = on;
    listeners.forEach((l) => l());
  };
  const snapshot = () => {
    if (current === null) current = read();
    return current;
  };
  const subscribe = (l: () => void) => {
    listeners.add(l);
    return () => listeners.delete(l);
  };
  return {
    use: (): [boolean, (on: boolean) => void] => [useSyncExternalStore(subscribe, snapshot, () => fallback), set],
    reset: () => {
      current = null;
    },
  };
}

const wheelZooms = makePref(WHEEL_ZOOMS_KEY, false);
const followPlayhead = makePref(FOLLOW_PLAYHEAD_KEY, true);

export const useWheelZooms = wheelZooms.use;
export const useFollowPlayhead = followPlayhead.use;

/** Drop the cached values so a test reads localStorage afresh. */
export function resetTimelinePrefsForTests(): void {
  wheelZooms.reset();
  followPlayhead.reset();
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `pnpm exec vitest run src/lib/timelinePrefs.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/timelinePrefs.ts src/splitsmith/ui_static/src/lib/timelinePrefs.test.ts
git commit -m "feat(timeline): per-browser wheel-zoom and follow-playhead switches (#1352)"
```

---

### Task 3: `components/timeline/WaveformTrack.tsx`

**Files:**
- Create: `src/splitsmith/ui_static/src/components/timeline/WaveformTrack.tsx`
- Test: `src/splitsmith/ui_static/src/components/timeline/WaveformTrack.test.tsx`

**Interfaces:**
- Consumes: `columnPeaks` (Task 1).
- Produces: `WaveformTrack(props: { peaks: number[] | null; clipDuration: number; from: number; to: number; geom: TimelineGeom; height: number })`. `TimelineGeom` is exported by Task 4's `Timeline.tsx`; to keep the tasks independent, this task declares it in `components/timeline/types.ts`:
  ```ts
  export interface TimelineGeom { contentWidth: number; viewportWidth: number; scrollLeft: number; pxPerSec: number }
  ```
  and Task 4 imports it from there.

The canvas is the viewport's width, `position: sticky; left: 0` inside the content div, so it stays in view while the content scrolls and is redrawn from `columnPeaks` on every geometry change. `peaks === null` renders a muted "No audio" line instead of a canvas. `clipDuration` is the peaks' clip length; `[from, to]` is the clip-time slice the band spans.

- [ ] **Step 1: Write the failing test**

```tsx
// src/splitsmith/ui_static/src/components/timeline/WaveformTrack.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { WaveformTrack } from "./WaveformTrack";

const geom = (zoomed: number) => ({ contentWidth: 1200 * zoomed, viewportWidth: 1200, scrollLeft: 0, pxPerSec: (1200 * zoomed) / 60 });

describe("WaveformTrack", () => {
  it("never sizes its canvas past the viewport, even at 16x on a long stage", () => {
    Object.defineProperty(window, "devicePixelRatio", { value: 2, configurable: true });
    const ctx = { setTransform: vi.fn(), clearRect: vi.fn(), fillRect: vi.fn(), fillStyle: "" };
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as unknown as CanvasRenderingContext2D);
    const { container } = render(
      <WaveformTrack peaks={new Array(4000).fill(0.5)} clipDuration={70} from={5} to={65} geom={geom(16)} height={56} />,
    );
    const canvas = container.querySelector("canvas")!;
    expect(canvas.width).toBe(2400);
    expect(canvas.style.width).toBe("1200px");
    expect(ctx.fillRect).toHaveBeenCalled();
  });

  it("says there is no audio when peaks are missing", () => {
    render(<WaveformTrack peaks={null} clipDuration={0} from={0} to={10} geom={geom(1)} height={56} />);
    expect(screen.getByText("No audio")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm exec vitest run src/components/timeline/WaveformTrack.test.tsx`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement**

```ts
// src/splitsmith/ui_static/src/components/timeline/types.ts
/** Scroll-host geometry handed to every timeline track. */
export interface TimelineGeom {
  /** Zoomed content width, CSS px (the viewport at Fit). */
  contentWidth: number;
  /** Visible width of the scroll host, CSS px. */
  viewportWidth: number;
  scrollLeft: number;
  pxPerSec: number;
}
```

```tsx
// src/splitsmith/ui_static/src/components/timeline/WaveformTrack.tsx
/**
 * The band's audio track. Draws only the visible window into a canvas the
 * size of the viewport (a full-content canvas passes Chromium's 32767 px
 * side limit at high zoom), held in view with ``sticky``. Bars are the
 * loudest peak under each column (``lib/timelineView.columnPeaks``).
 */
import { useEffect, useRef } from "react";

import { columnPeaks } from "@/lib/timelineView";

import type { TimelineGeom } from "./types";

export interface WaveformTrackProps {
  /** Server peaks over the clip, or null when there is no audio for this stage. */
  peaks: number[] | null;
  clipDuration: number;
  /** Clip seconds at the band's left and right edge. */
  from: number;
  to: number;
  geom: TimelineGeom;
  height: number;
}

function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

export function WaveformTrack({ peaks, clipDuration, from, to, geom, height }: WaveformTrackProps) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const { contentWidth, viewportWidth, scrollLeft } = geom;

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas || !peaks || viewportWidth <= 0) return;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.floor(viewportWidth));
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(height * dpr);
    canvas.style.width = `${w}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, height);
    ctx.fillStyle = cssVar("--color-muted", "#8E939B");
    const cols = columnPeaks(peaks, clipDuration, from, to, contentWidth, scrollLeft, w);
    const mid = height / 2;
    for (let x = 0; x < w; x += 2) {
      const p = Math.max(cols[x] ?? 0, cols[x + 1] ?? 0);
      const h = Math.max(1, p * (height - 4));
      ctx.fillRect(x, mid - h / 2, 1.5, h);
    }
  }, [peaks, clipDuration, from, to, contentWidth, viewportWidth, scrollLeft, height]);

  if (!peaks) {
    return (
      <div className="sticky left-0 flex items-center px-2 text-sm text-muted" style={{ width: viewportWidth || "100%", height }}>
        No audio
      </div>
    );
  }
  return <canvas ref={ref} data-testid="waveform-track" className="pointer-events-none sticky left-0 block" />;
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `pnpm exec vitest run src/components/timeline/WaveformTrack.test.tsx`
Expected: PASS. If `--color-muted` is not the token name for the muted ink in `src/index.css` (check with `grep -n "color-muted" src/index.css`), use the one that is.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/components/timeline/
git commit -m "feat(timeline): waveform track drawn per visible window (#1352)"
```

---

### Task 4: `components/timeline/Timeline.tsx`

**Files:**
- Create: `src/splitsmith/ui_static/src/components/timeline/Timeline.tsx`
- Test: `src/splitsmith/ui_static/src/components/timeline/Timeline.test.tsx`

**Interfaces:**
- Consumes: Task 1 (`Zoom`, `contentWidth`, `zoomStep`, `zoomAround`, `clampZoom`, `wheelAction`, `applyWheelZoom`, `rulerTicks`, `followScroll`, `MAX_ZOOM`), Task 2 (`useWheelZooms`, `useFollowPlayhead`), Task 3 (`TimelineGeom` in `./types`), `lib/zoomKeys.zoomActionForKey`, `components/ui/{Button,Label,Menu}`.
- Produces:
  ```ts
  export interface TimelineTrack {
    id: string;
    /** Gutter labels, one per row, and each row's height in px; the track spans their sum. */
    rows: { label: string; height: number }[];
    render: (geom: TimelineGeom) => ReactNode;
  }
  export interface TimelineProps {
    /** Domain [0, duration] in the page's own time. */
    duration: number;
    /** Ruler zero (the beep) in the same time; default 0. */
    origin?: number;
    fps?: number;
    currentTime: number;
    onSeek: (t: number) => void;
    tracks: TimelineTrack[];
    zoom: Zoom;
    onZoomChange: (zoom: Zoom) => void;
    /** Page entries appended to the band's "More" menu. */
    menuExtra?: ReactNode;
    title?: string; // header Label, default "Timeline"
  }
  export function Timeline(props: TimelineProps): JSX.Element
  ```
  Test ids: `timeline`, `timeline-host` (the scroll host), `timeline-content`, `timeline-ruler`, `timeline-playhead`.

Behaviour (all from the spec):
- Header: `Label` title; zoom group with `Button`s "Zoom out" / "Zoom in" (`size="sm" variant="ghost"`, lucide `Minus` / `Plus`), a range input (`aria-label="Zoom"`, `min=0`, `max=Math.log(MAX_ZOOM)`, `step=0.01`, value `Math.log(zoom ?? 1)`), a "Fit" `Button` (`aria-pressed={zoom === null}`), a readout ("Fit" or `2.3x`, `numeral text-sm text-muted`), then the "More" menu button (lucide `MoreHorizontal`, `aria-label="Timeline options"`). Buttons, the slider and keys anchor at the playhead when it is in view, else the viewport's centre.
- Menu (`Menu`, `align="right"`): two `menuitemcheckbox` rows, "Wheel zooms the timeline" and "Follow playhead", each with an on/off trailing `text-sm text-muted` value, styled `menuItemClass`; then `menuExtra`.
- Body: `grid grid-cols-[96px_minmax(0,1fr)]`. Gutter: a 24 px spacer for the ruler, then each track's rows as `Label`s, right-aligned, each row's `height`. Right cell: the scroll host (`overflow-x-auto overflow-y-hidden`), measured with a `ResizeObserver` on itself (`clientWidth`), containing the content div `relative` with `width: contentWidth(zoom, viewport)`.
- Content: ruler (24 px, `border-b border-rule`, ticks from `rulerTicks` over the visible window +-1 viewport, minor ticks `h-1 w-px bg-rule`, labels `numeral text-xs text-muted`; a click seeks to the pointer's time through the content div's rect, no snap), then each track in a `relative` div of its rows' summed height calling `render(geom)`, then the playhead (`pointer-events-none absolute inset-y-0 w-px bg-led`, left `x(currentTime)`).
- Wheel: one native `wheel` listener on the host with `{ passive: false }`, reading the latest zoom / pref through refs. `zoom` action: `preventDefault`, anchor at `clientX - host.left`. `pan` action: `preventDefault` always (even with nothing to scroll: a sideways swipe must not navigate back), `host.scrollLeft += px`. `null`: do nothing.
- Keys: one `window` `keydown` listener through `zoomActionForKey`; `in`/`out` step and anchor as the buttons do, `fit` sets `null`; `preventDefault` on a hit.
- Zoom changes go through `zoomAround`; the resulting `scrollLeft` is applied in a `useLayoutEffect` on the new content width (the content must be wide enough before the host can scroll there).
- Follow: when the pref is on and no pointer is down inside the band (a ref set by `onPointerDownCapture` on the band root, cleared by `window` `pointerup` / `pointercancel`), a `currentTime` change applies `followScroll`.
- Geometry handed to tracks: `{ contentWidth, viewportWidth, scrollLeft, pxPerSec: contentWidth / duration }`; `scrollLeft` is state updated from the host's `scroll` event.

- [ ] **Step 1: Write the failing test**

```tsx
// src/splitsmith/ui_static/src/components/timeline/Timeline.test.tsx
import { act, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetTimelinePrefsForTests } from "@/lib/timelinePrefs";
import type { Zoom } from "@/lib/timelineView";

import { Timeline } from "./Timeline";

const VIEWPORT = 1000;

function Harness(props: { currentTime?: number; onSeek?: (t: number) => void; initialZoom?: Zoom; onZoom?: (z: Zoom) => void }) {
  const [zoom, setZoom] = React.useState<Zoom>(props.initialZoom ?? null);
  return (
    <Timeline
      duration={10}
      origin={0}
      fps={30}
      currentTime={props.currentTime ?? 0}
      onSeek={props.onSeek ?? vi.fn()}
      zoom={zoom}
      onZoomChange={(z) => {
        setZoom(z);
        props.onZoom?.(z);
      }}
      tracks={[{ id: "a", rows: [{ label: "Audio", height: 40 }], render: () => <div data-testid="track-a" /> }]}
    />
  );
}

beforeEach(() => {
  vi.spyOn(Element.prototype, "clientWidth", "get").mockReturnValue(VIEWPORT);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    const w = this.dataset.testid === "timeline-content" ? parseFloat(this.style.width) || VIEWPORT : VIEWPORT;
    const left = this.dataset.testid === "timeline-content" ? -(screen.queryByTestId("timeline-host")?.scrollLeft ?? 0) : 0;
    return { width: w, height: 40, left, top: 0, right: left + w, bottom: 40, x: left, y: 0, toJSON: () => ({}) };
  });
});
afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
  resetTimelinePrefsForTests();
});

const wheel = (el: Element, init: WheelEventInit) => {
  const e = new WheelEvent("wheel", { bubbles: true, cancelable: true, ...init });
  act(() => {
    el.dispatchEvent(e);
  });
  return e;
};

describe("Timeline", () => {
  it("leaves a plain vertical wheel to the page by default", () => {
    const onZoom = vi.fn();
    render(<Harness onZoom={onZoom} />);
    const e = wheel(screen.getByTestId("timeline-host"), { deltaY: 100, clientX: 500 });
    expect(e.defaultPrevented).toBe(false);
    expect(onZoom).not.toHaveBeenCalled();
  });

  it("zooms around the pointer on Ctrl+wheel and consumes it", () => {
    const onZoom = vi.fn();
    render(<Harness onZoom={onZoom} />);
    const e = wheel(screen.getByTestId("timeline-host"), { deltaY: -200, ctrlKey: true, clientX: 800 });
    expect(e.defaultPrevented).toBe(true);
    expect(onZoom).toHaveBeenCalledWith(expect.any(Number));
    expect(onZoom.mock.calls[0][0]).toBeGreaterThan(1);
  });

  it("consumes a sideways swipe even at Fit, so it never navigates back", () => {
    render(<Harness />);
    const e = wheel(screen.getByTestId("timeline-host"), { deltaX: -60, clientX: 100 });
    expect(e.defaultPrevented).toBe(true);
  });

  it("zooms on a plain wheel once the switch is on, and the switch is shared", () => {
    const onZoom = vi.fn();
    render(<Harness onZoom={onZoom} />);
    fireEvent.click(screen.getByRole("button", { name: "Timeline options" }));
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: /Wheel zooms the timeline/ }));
    const e = wheel(screen.getByTestId("timeline-host"), { deltaY: -100, clientX: 500 });
    expect(e.defaultPrevented).toBe(true);
    expect(onZoom).toHaveBeenCalled();
    expect(window.localStorage.getItem("splitsmith.timeline.wheelZooms")).toBe("on");
  });

  it("steps with the buttons and returns to Fit", () => {
    const onZoom = vi.fn();
    render(<Harness onZoom={onZoom} />);
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(onZoom).toHaveBeenLastCalledWith(1.5);
    expect(screen.getByTestId("timeline-content").style.width).toBe("1500px");
    fireEvent.click(screen.getByRole("button", { name: "Fit" }));
    expect(onZoom).toHaveBeenLastCalledWith(null);
    expect(screen.getByTestId("timeline-content").style.width).toBe("1000px");
  });

  it("zooms with the keys", () => {
    const onZoom = vi.fn();
    render(<Harness onZoom={onZoom} />);
    fireEvent.keyDown(window, { key: "+" });
    expect(onZoom).toHaveBeenLastCalledWith(1.5);
    fireEvent.keyDown(window, { key: "0" });
    expect(onZoom).toHaveBeenLastCalledWith(null);
  });

  it("seeks from a ruler click without snapping", () => {
    const onSeek = vi.fn();
    render(<Harness onSeek={onSeek} />);
    fireEvent.click(screen.getByTestId("timeline-ruler"), { clientX: 437 });
    expect(onSeek).toHaveBeenCalledWith(expect.closeTo(4.37, 3));
  });

  it("labels the gutter and renders each track", () => {
    render(<Harness />);
    expect(screen.getByText("Audio")).toBeInTheDocument();
    expect(screen.getByTestId("track-a")).toBeInTheDocument();
  });

  it("follows the playhead while playing, but not while a pointer is down in the band", () => {
    const { rerender } = render(<Harness initialZoom={4} currentTime={0} />);
    const host = screen.getByTestId("timeline-host");
    // 4000 px of content: t = 9.5 s is at 3800 px, outside the first window.
    fireEvent.pointerDown(screen.getByTestId("track-a"), { pointerId: 1, button: 0 });
    rerender(<Harness initialZoom={4} currentTime={9.5} />);
    expect(host.scrollLeft).toBe(0);
    act(() => {
      window.dispatchEvent(new Event("pointerup"));
    });
    rerender(<Harness initialZoom={4} currentTime={9.6} />);
    expect(host.scrollLeft).toBeGreaterThan(0);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm exec vitest run src/components/timeline/Timeline.test.tsx`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement**

```tsx
// src/splitsmith/ui_static/src/components/timeline/Timeline.tsx
/**
 * The shared timeline band (spec 2026-10-09): full width under a page's
 * top row, a lane-label gutter, a ruler in seconds from the beep that
 * scrolls and refines with zoom, page tracks, the playhead, and one set of
 * zoom controls, wheel rules and keys for Coach, Audit and the beep step.
 *
 * Tracks are drawn inside a content div as wide as the zoomed content, so
 * anything positioned by percentage of that div (the lane editor,
 * MarkerLayer) needs no zoom maths of its own. Geometry is
 * lib/timelineView.ts; this file owns the DOM.
 */
import { Minus, MoreHorizontal, Plus } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Label } from "@/components/ui/Label";
import { Menu, menuItemClass } from "@/components/ui/Menu";
import { useFollowPlayhead, useWheelZooms } from "@/lib/timelinePrefs";
import {
  MAX_ZOOM,
  applyWheelZoom,
  clampZoom,
  contentWidth,
  followScroll,
  rulerTicks,
  wheelAction,
  zoomAround,
  zoomStep,
  type Zoom,
} from "@/lib/timelineView";
import { zoomActionForKey } from "@/lib/zoomKeys";

import type { TimelineGeom } from "./types";

export type { TimelineGeom } from "./types";

const RULER_H = 24;

export interface TimelineTrack {
  id: string;
  rows: { label: string; height: number }[];
  render: (geom: TimelineGeom) => ReactNode;
}

export interface TimelineProps {
  duration: number;
  origin?: number;
  fps?: number;
  currentTime: number;
  onSeek: (t: number) => void;
  tracks: TimelineTrack[];
  zoom: Zoom;
  onZoomChange: (zoom: Zoom) => void;
  menuExtra?: ReactNode;
  title?: string;
}

export function Timeline(props: TimelineProps) {
  const { duration, origin = 0, fps = 30, currentTime, onSeek, tracks, zoom, onZoomChange, menuExtra, title = "Timeline" } = props;
  const hostRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const [viewport, setViewport] = useState(0);
  const [scrollLeft, setScrollLeft] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const [wheelZooms, setWheelZooms] = useWheelZooms();
  const [follow, setFollow] = useFollowPlayhead();
  const pendingScroll = useRef<number | null>(null);
  const pointerDown = useRef(false);

  useLayoutEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const measure = () => setViewport(host.clientWidth);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(host);
    return () => ro.disconnect();
  }, []);

  const content = contentWidth(zoom, viewport);
  const span = Math.max(duration, 1e-6);
  const pxPerSec = content / span;
  const x = (t: number) => (Math.min(Math.max(t, 0), span) / span) * content;

  // Latest values for the native listeners.
  const live = useRef({ zoom, viewport, wheelZooms, currentTime });
  live.current = { zoom, viewport, wheelZooms, currentTime };

  const playheadAnchor = (): number => {
    const host = hostRef.current;
    const v = live.current.viewport;
    if (!host || v <= 0) return 0;
    const px = (Math.min(Math.max(live.current.currentTime, 0), span) / span) * contentWidth(live.current.zoom, v) - host.scrollLeft;
    return px >= 0 && px <= v ? px : v / 2;
  };

  const applyZoom = (next: Zoom, anchorPx: number) => {
    const host = hostRef.current;
    const { zoom: cur, viewport: v } = live.current;
    if (!host || v <= 0 || next === cur) return;
    const r = zoomAround({ zoom: cur, scrollLeft: host.scrollLeft }, next, anchorPx, v);
    pendingScroll.current = r.scrollLeft;
    onZoomChange(r.zoom);
  };
  const applyZoomRef = useRef(applyZoom);
  applyZoomRef.current = applyZoom;
  const anchorRef = useRef(playheadAnchor);
  anchorRef.current = playheadAnchor;

  useLayoutEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    if (pendingScroll.current !== null) {
      host.scrollLeft = pendingScroll.current;
      pendingScroll.current = null;
    }
    setScrollLeft(host.scrollLeft);
  }, [content]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const onWheel = (e: WheelEvent) => {
      const action = wheelAction(e, live.current.wheelZooms);
      if (!action) return;
      e.preventDefault();
      if (action.kind === "pan") {
        host.scrollLeft += action.px;
        return;
      }
      const anchor = e.clientX - host.getBoundingClientRect().left;
      applyZoomRef.current(applyWheelZoom(live.current.zoom, action.factor), anchor);
    };
    host.addEventListener("wheel", onWheel, { passive: false });
    return () => host.removeEventListener("wheel", onWheel);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const a = zoomActionForKey(e);
      if (!a) return;
      e.preventDefault();
      if (a === "fit") applyZoomRef.current(null, 0);
      else applyZoomRef.current(zoomStep(live.current.zoom, a === "in" ? 1 : -1), anchorRef.current());
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    const up = () => {
      pointerDown.current = false;
    };
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    return () => {
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
    };
  }, []);

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !follow || pointerDown.current) return;
    const next = followScroll(x(currentTime), host.scrollLeft, viewport, content);
    if (next !== null) host.scrollLeft = next;
    // x is derived from content and duration, both listed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentTime, follow, viewport, content, duration]);

  const tAt = (clientX: number) => {
    const rect = contentRef.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return 0;
    return (Math.min(Math.max(clientX - rect.left, 0), rect.width) / rect.width) * span;
  };

  const ticks = useMemo(() => {
    if (pxPerSec <= 0) return [];
    const t0 = Math.max(0, (scrollLeft - viewport) / pxPerSec);
    const t1 = Math.min(span, (scrollLeft + 2 * viewport) / pxPerSec);
    return rulerTicks(t0, t1, pxPerSec, origin, fps);
  }, [pxPerSec, scrollLeft, viewport, span, origin, fps]);

  const geom: TimelineGeom = { contentWidth: content, viewportWidth: viewport, scrollLeft, pxPerSec };
  const sliderMax = Math.log(MAX_ZOOM);

  return (
    <div
      data-testid="timeline"
      className="rounded-[10px] border border-rule bg-surface-2"
      onPointerDownCapture={() => {
        pointerDown.current = true;
      }}
    >
      <div className="flex items-center gap-3 border-b border-rule px-3 py-2">
        <Label>{title}</Label>
        <div className="ml-auto flex items-center gap-1" role="group" aria-label="Zoom controls">
          <Button size="sm" variant="ghost" aria-label="Zoom out" onClick={() => applyZoom(zoomStep(zoom, -1), playheadAnchor())}>
            <Minus className="size-3" aria-hidden />
          </Button>
          <input
            type="range"
            aria-label="Zoom"
            min={0}
            max={sliderMax}
            step={0.01}
            value={Math.log(zoom ?? 1)}
            onChange={(e) => applyZoom(clampZoom(Math.exp(Number(e.target.value))), playheadAnchor())}
            className="w-28 accent-ink-2"
          />
          <Button size="sm" variant="ghost" aria-label="Zoom in" onClick={() => applyZoom(zoomStep(zoom, 1), playheadAnchor())}>
            <Plus className="size-3" aria-hidden />
          </Button>
          <Button size="sm" variant="ghost" aria-pressed={zoom === null} onClick={() => applyZoom(null, 0)}>
            Fit
          </Button>
          <span className="numeral w-10 text-right text-sm text-muted" aria-live="polite">
            {zoom === null ? "Fit" : `${zoom.toFixed(1)}x`}
          </span>
          <span className="relative shrink-0">
            <Button
              type="button"
              size="icon"
              variant="ghost"
              aria-label="Timeline options"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((v) => !v)}
            >
              <MoreHorizontal className="size-4" aria-hidden />
            </Button>
            <Menu open={menuOpen} onClose={() => setMenuOpen(false)} align="right">
              <button
                type="button"
                role="menuitemcheckbox"
                aria-checked={wheelZooms}
                className={menuItemClass}
                onClick={() => setWheelZooms(!wheelZooms)}
              >
                Wheel zooms the timeline
                <span className="ml-auto text-sm text-muted">{wheelZooms ? "on" : "off"}</span>
              </button>
              <button
                type="button"
                role="menuitemcheckbox"
                aria-checked={follow}
                className={menuItemClass}
                onClick={() => setFollow(!follow)}
              >
                Follow playhead
                <span className="ml-auto text-sm text-muted">{follow ? "on" : "off"}</span>
              </button>
              {menuExtra}
            </Menu>
          </span>
        </div>
      </div>
      <div className="grid grid-cols-[96px_minmax(0,1fr)]">
        <div className="flex flex-col border-r border-rule">
          <div style={{ height: RULER_H }} />
          {tracks.flatMap((track) =>
            track.rows.map((row) => (
              <div key={`${track.id}-${row.label}`} className="flex items-center justify-end pr-2" style={{ height: row.height }}>
                <Label>{row.label}</Label>
              </div>
            )),
          )}
        </div>
        <div
          ref={hostRef}
          data-testid="timeline-host"
          className="overflow-x-auto overflow-y-hidden"
          onScroll={(e) => setScrollLeft(e.currentTarget.scrollLeft)}
        >
          <div ref={contentRef} data-testid="timeline-content" className="relative" style={{ width: content }}>
            <div
              data-testid="timeline-ruler"
              className="relative cursor-pointer border-b border-rule"
              style={{ height: RULER_H }}
              onClick={(e) => onSeek(tAt(e.clientX))}
            >
              {ticks.map((tick) => (
                <span key={tick.t} className="absolute bottom-0" style={{ left: x(tick.t) }}>
                  <span className={tick.label !== undefined ? "block h-2 w-px bg-rule-strong" : "block h-1 w-px bg-rule"} />
                  {tick.label !== undefined ? (
                    <span className="numeral absolute bottom-2 left-1 whitespace-nowrap text-xs text-muted">{tick.label}</span>
                  ) : null}
                </span>
              ))}
            </div>
            {tracks.map((track) => (
              <div key={track.id} className="relative" style={{ height: track.rows.reduce((a, r) => a + r.height, 0) }}>
                {track.render(geom)}
              </div>
            ))}
            <div
              data-testid="timeline-playhead"
              className="pointer-events-none absolute inset-y-0 w-px bg-led"
              style={{ left: x(currentTime) }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `pnpm exec vitest run src/components/timeline/`
Expected: PASS. The test file is a draft: jsdom has no layout, so the geometry mocks above stand in for it. If a test fails for a mock reason rather than a behaviour reason, fix the mock and say so in your report; never weaken an assertion about `defaultPrevented`, the zoom value or the follow-while-dragging rule. Confirm the follow test fails when you delete the `pointerDown.current` check (then restore it).

- [ ] **Step 5: Typecheck and lint**

Run: `pnpm typecheck && pnpm lint`
Expected: no errors. `Button`'s `size`/`variant` names: check `components/ui/Button.tsx` and use the existing ones.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui_static/src/components/timeline/
git commit -m "feat(timeline): the shared timeline band with zoom, wheel rules and follow (#1352)"
```

---

### Task 5: The lane editor becomes a timeline track

**Files:**
- Modify: `src/splitsmith/ui_static/src/components/coach/LaneEditor.tsx`
- Modify: `src/splitsmith/ui_static/src/components/coach/LaneEditor.test.tsx`

**Interfaces:**
- Produces: `LaneEditor` keeps its props except `menu` (removed: the menu moves to `Timeline`'s `menuExtra`). Exports `LANE_ROWS: { label: string; height: number }[]` (Shots 32, then Movement, Reload, Activation at 36 each: the current `h-8` / `h-9`) and `LaneHints({ readOnly }: { readOnly?: boolean })` (the hint line currently at the bottom of the editor, unchanged copy).

Changes to `LaneEditor.tsx`:
1. Delete the header row (`Label` "Lanes" and `{menu}`), the label column (`grid-cols-[5.5rem_1fr]` and its left `flex flex-col`), the ruler (`data-testid="lane-ruler"`, `ticks`, `labels`, `rulerLabels` import, `stripWidth` state and its `ResizeObserver`), the playhead (`data-testid="playhead"`; the band draws it) and the hint block.
2. The root is the strip: `<div ref={...} data-testid="lane-editor" tabIndex={0} onKeyDown={handleKeyDown} className="relative outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-led">` wrapping the Shots row and the three lane rows, which keep their heights (`h-8`, `h-9`) and every handler, test id and class. `stripRef` and `rootRef` become the same element (keep one ref; `tAt`, `maybeSnap` and `begin`'s focus call read it).
3. `LANE_ROWS` and `LaneHints` exported from the file. The hint copy moves verbatim.
4. Nothing in `laneDrag.ts`, `lib/events.ts` or any pointer, keyboard or commit rule changes. The drag pill and the overhang bracket stay.

Changes to `LaneEditor.test.tsx`:
1. Drop the ruler-label test (the band's ruler is `rulerTicks`, tested in Task 1) and any assertion on `lane-ruler`, `playhead` or the hint text; move a ruler-click seek assertion, if one exists, to nothing (Task 4 covers it).
2. Add the Review Focus 4 test:

```tsx
  it("maps a drag past the visible window through the content rect (zoomed and scrolled)", () => {
    // The strip is 4000 px wide (4x on a 1000 px viewport) and scrolled by 1500 px:
    // its rect starts at -1500. A pointer at clientX 1200 is content x 2700 -> 6.75 s of 10.
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      width: 4000, height: 32, left: -1500, top: 0, right: 2500, bottom: 32, x: -1500, y: 0, toJSON: () => ({}),
    });
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const lane = screen.getByTestId("lane-movement");
    fireEvent.pointerDown(lane, { pointerId: 1, clientX: 100, clientY: 10, button: 0, altKey: true });
    fireEvent.pointerMove(lane, { pointerId: 1, clientX: 600, clientY: 10, altKey: true });
    fireEvent.pointerMove(lane, { pointerId: 1, clientX: 1200, clientY: 10, altKey: true });
    fireEvent.pointerUp(lane, { pointerId: 1, clientX: 1200, clientY: 10, altKey: true });
    const committed = lastCommit(onChange)!;
    expect(committed[0].start).toBeCloseTo(4.0, 2);
    expect(committed[0].end).toBeCloseTo(6.75, 2);
  });
```

Alt skips snapping, so the times are the raw mapping (`(clientX + 1500) / 4000 * 10`).

- [ ] **Step 1: Add the new test and run it against the current editor**

Run: `pnpm exec vitest run src/components/coach/LaneEditor.test.tsx`
Expected: the new test PASSES already (the editor reads its own rect); it pins that the move into the band keeps it so.

- [ ] **Step 2: Make the editor a track (changes 1-4 above), update the tests (drop list above)**

- [ ] **Step 3: Run the editor tests**

Run: `pnpm exec vitest run src/components/coach/`
Expected: PASS, with only the dropped tests gone. Report the names of every test you removed or edited and why.

- [ ] **Step 4: Typecheck**

Run: `pnpm typecheck`
Expected: errors only in `pages/Coach.tsx` (the `menu` prop); Task 6 fixes them. Do not change `Coach.tsx` in this task beyond deleting the `menu={...}` prop if typecheck must pass for the commit hook; if you delete it, say so.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/components/coach/LaneEditor.tsx src/splitsmith/ui_static/src/components/coach/LaneEditor.test.tsx
git commit -m "refactor(coach): the lane editor draws as a timeline track (#1352)"
```

---

### Task 6: Coach on the band, with an audio track

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Coach.tsx` (layout at ~1058-1202; time mapping at ~977-992)
- Test: the Coach page test file (`grep -ln "Coach" src/pages/*.test.tsx`; add to the one that renders the page with mocked `api`)

**Interfaces:**
- Consumes: `Timeline`, `TimelineTrack` (Task 4), `WaveformTrack` (Task 3), `LaneEditor`, `LANE_ROWS`, `LaneHints` (Task 5), `api.getStagePeaks(slug, stageNumber, bins)` -> `PeaksResult { duration, peaks, beep_time, ... }`, `Zoom` (Task 1).

Wiring:
1. State: `const [timelineZoom, setTimelineZoom] = useState<Zoom>(null)`, reset to `null` in the same place the page resets per-stage state (find where `selectedEventId` or the coach payload is reset on stage change and add it there). Because the band remounts nothing on a stage change, also give `<Timeline key={stageNumber} ...>` so its scroll position starts at 0 (Review Focus 5).
2. Peaks: `const [peaks, setPeaks] = useState<PeaksResult | null>(null)`, loaded in an effect on `[slug, stageNumber]` exactly like `Audit.tsx`'s "Load peaks" effect (~541-567) with `PEAK_BINS = 4000`; an error sets `null` (the track then says "No audio"; never a page error).
3. Domain: the band spans `[0, stageTime]` from the beep (`stageTime` as today, ~989). The waveform shows clip seconds `[beep, beep + stageTime]` where `beep = peaks?.beep_time ?? coach.beep_time`.
4. Layout: the existing grid holds the video card (video, transport, `ShotRuler`) on the left and `CoachShotTable` on the right, nothing else. After the grid, full width: `<Timeline ...>` then `<LaneHints readOnly={eventsReadOnly} />`, `SaveNotice`, `EventList`, and the `EventCard` / `ShotEditor` block, in their current order.
5. The band:

```tsx
<Timeline
  key={stageNumber}
  duration={stageTime}
  origin={0}
  fps={30}
  currentTime={tFromBeep}
  onSeek={seekFromBeep}
  zoom={timelineZoom}
  onZoomChange={setTimelineZoom}
  menuExtra={
    scrub.available ? (
      <button
        type="button"
        role="menuitemcheckbox"
        aria-checked={scrub.fullRes}
        className={menuItemClass}
        onClick={() => scrub.setFullRes(!scrub.fullRes)}
      >
        Full-resolution video
        <span className="ml-auto text-sm text-muted">{scrub.fullRes ? "on" : "off"}</span>
      </button>
    ) : undefined
  }
  tracks={[
    {
      id: "audio",
      rows: [{ label: "Audio", height: 56 }],
      render: (geom) => (
        <WaveformTrack
          peaks={peaks?.peaks ?? null}
          clipDuration={peaks?.duration ?? 0}
          from={audioBeep}
          to={audioBeep + stageTime}
          geom={geom}
          height={56}
        />
      ),
    },
    {
      id: "lanes",
      rows: LANE_ROWS,
      render: () => (
        <LaneEditor
          shots={coach.shots}
          events={events}
          stageTime={stageTime}
          currentTime={tFromBeep}
          selectedId={selectedEventId}
          readOnly={eventsReadOnly}
          onSelect={selectEvent}
          onSeek={seekFromBeep}
          onChange={changeEvents}
          onCancel={cancelEvents}
        />
      ),
    },
  ]}
/>
```

   with `const audioBeep = peaks?.beep_time ?? coach.beep_time;`. Remove the old `moreOpen` state and its `Menu` if nothing else uses them.
6. The fps: if the coach payload or the primary video carries an fps the page already reads, pass it to both `Timeline` and `LaneEditor`; otherwise 30 as today (`LaneEditor`'s default).

- [ ] **Step 1: Write the failing page tests**

In the Coach page test file, using its existing mocked-`api` render helper (follow how it stubs `api.getStageCoach` or equivalent; add `getStagePeaks` to the stub):

```tsx
it("draws the lanes and an audio track in a full-width timeline band", async () => {
  // stub: api.getStagePeaks resolves { duration: 20, sample_rate: 8000, bins: 4, peaks: [0.1, 0.5, 0.2, 0.1], beep_time: 2, trimmed: true }
  renderCoach();
  const band = await screen.findByTestId("timeline");
  expect(within(band).getByText("Audio")).toBeInTheDocument();
  expect(within(band).getByText("Reload")).toBeInTheDocument();
  expect(within(band).getByTestId("lane-editor")).toBeInTheDocument();
  // The band is outside the two-column grid: no ancestor carries the 380 px column template.
  expect(band.closest("[class*='380px']")).toBeNull();
});

it("shows No audio when the peaks request fails, and the lanes still work", async () => {
  // stub: api.getStagePeaks rejects with new ApiError(404, "no trim")
  renderCoach();
  expect(await screen.findByText("No audio")).toBeInTheDocument();
  expect(screen.getByTestId("lane-editor")).toBeInTheDocument();
});

it("starts each stage at Fit", async () => {
  renderCoach();
  const band = await screen.findByTestId("timeline");
  fireEvent.click(within(band).getByRole("button", { name: "Zoom in" }));
  expect(within(band).getByText("1.5x")).toBeInTheDocument();
  // navigate to the next stage with the page's own next-stage control (its accessible name is "Next stage")
  fireEvent.click(screen.getByRole("button", { name: "Next stage" }));
  const next = await screen.findByTestId("timeline");
  expect(within(next).getByRole("button", { name: "Fit" })).toHaveAttribute("aria-pressed", "true");
});
```

   Adapt the stub names to the file's real helpers; keep the assertions. If the file has no multi-stage fixture, add a second stage to its fixture for the last test.

- [ ] **Step 2: Run to verify they fail**

Run: `pnpm exec vitest run <the Coach page test file>`
Expected: FAIL (no `timeline` test id).

- [ ] **Step 3: Implement the wiring (1-6 above)**

- [ ] **Step 4: Run the Coach tests, the coach components and the timeline**

Run: `pnpm exec vitest run <the Coach page test file> src/components/coach src/components/timeline src/lib/timelineView.test.ts`
Expected: PASS.

- [ ] **Step 5: Full SPA gate**

Run: `pnpm typecheck && pnpm lint && pnpm test`
Expected: no type or lint errors, every test passes.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/Coach.tsx <the Coach page test file>
git commit -m "feat(coach): the lane editor and the stage audio on a full-width zoomable timeline (#1352)"
```

---

### Task 7: Sidebar shortcut

**Files:**
- Modify: `src/splitsmith/ui_static/src/components/match/MatchShell.tsx` (~220-241, `toggleSidebar`)
- Modify: `src/splitsmith/ui_static/src/components/match/MatchSidebar.tsx` (~160-164, the collapse button's `title` / `aria-label`)
- Test: `MatchShell`'s test file (`ls src/components/match/*.test.tsx`), or a new `MatchShell.sidebarShortcut.test.tsx` beside it using the same render helper.

**Interfaces:**
- Consumes: `isTypingTextTarget` (`lib/audit-input.ts`), `modKeyLabel` (`lib/platform`).

1. In `MatchShell`, a `window` `keydown` effect: when `(e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "b" && !isTypingTextTarget(e.target)`, `preventDefault()` and `toggleSidebar()`.
2. In `MatchSidebar`, the button's `title` becomes `` `${collapsed ? "Expand sidebar" : "Collapse sidebar"} (${modKeyLabel()}+B)` ``; `aria-label` stays as it is (tests and screen readers rely on it).

- [ ] **Step 1: Write the failing test**

```tsx
it("Cmd/Ctrl+B collapses and expands the sidebar, except while typing", () => {
  renderShell(); // the file's existing helper
  const sidebar = screen.getByRole("button", { name: "Collapse sidebar" });
  fireEvent.keyDown(window, { key: "b", ctrlKey: true });
  expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeInTheDocument();
  const input = document.createElement("input");
  document.body.appendChild(input);
  fireEvent.keyDown(input, { key: "b", ctrlKey: true });
  expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeInTheDocument();
  input.remove();
  fireEvent.keyDown(window, { key: "b", metaKey: true });
  expect(screen.getByRole("button", { name: "Collapse sidebar" })).toBeInTheDocument();
  expect(sidebar).toBeDefined();
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm exec vitest run <that test file>`
Expected: FAIL (still "Collapse sidebar" after the first key).

- [ ] **Step 3: Implement 1-2**

- [ ] **Step 4: Run to verify it passes**

Run: `pnpm exec vitest run src/components/match/`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/components/match/
git commit -m "feat(shell): Cmd/Ctrl+B collapses the match sidebar (#1352)"
```

---

### Task 8: Docs, What's new, and the rendered check

**Files:**
- Modify: `CLAUDE.md` (the "Stage events (spec 2026-10-08)" section, the paragraph that starts "The coach payload carries")
- Modify: `docs/superpowers/specs/2026-10-09-shared-timeline-design.md` (append an "As built (PR 1)" section)
- Modify: `src/splitsmith/data/whats_new.json`

1. CLAUDE.md: after the sentence about ``components/coach/LaneEditor`` owning the DOM, add:
   "On Coach the lanes are a track of the shared timeline band (``components/timeline/Timeline``, spec 2026-10-09): the band owns the ruler, the playhead, zoom (``lib/timelineView``: ``null`` is Fit, a multiplier up to 16x, never narrower than the viewport), the wheel rules (a plain wheel is the page's unless ``splitsmith.timeline.wheelZooms`` is on; a horizontal wheel over the band is always consumed) and follow-playhead, which never scrolls while a pointer is down in the band. A track positions by percentage of the band's content div, so the editor's pointer maths reads its own rect and needs no zoom code; a new track does the same. ``WaveformTrack`` draws only the visible window into a viewport-sized canvas, never a full-content one. Audit and the beep step move onto the band next."
2. Spec: an "As built (PR 1)" section listing the three rulings at the top of this plan.
3. What's new: invoke the `whats-new` skill (`.claude/skills/whats-new/SKILL.md`) and add one entry for Coach's timeline: zoom, the audio track, the wheel rules and the switch. Run `uv run pytest tests/test_whats_new.py -q -n0`; it must pass.
4. Rendered check (CLAUDE.md "Verifying a screen locally without real footage"): seed `uv run python scripts/seed_demo_match.py ~/.claude-tmp/demo-timeline --media`, build the SPA (`pnpm build` in `src/splitsmith/ui_static`), start `SPLITSMITH_AUTO_SYNC=0 uv run splitsmith ui --project ~/.claude-tmp/demo-timeline --skip-system-check --no-browser --port 5174`, open a shooter's Coach page at 1440x900 with Playwright (or headless Chromium at `~/.cache/ms-playwright/chromium-1247/chrome-linux64/chrome`), and save screenshots at Fit and after three `+` key presses to `~/.claude-tmp/timeline-pr1/`. Report the paths; the controller publishes them. Stop the server afterwards.

- [ ] **Step 1: Edits 1-3, then `uv run pytest tests/test_whats_new.py -q -n0`**
- [ ] **Step 2: The rendered check (4)**
- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-10-09-shared-timeline-design.md src/splitsmith/data/whats_new.json
git commit -m "docs: the shared timeline band on Coach (#1352)"
```
