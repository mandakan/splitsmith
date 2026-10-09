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
    // 10 bins over a 10 s clip; the track shows clip 2..6 s over 4 px content (1 px/s), scrolled to 0.
    const peaks = [0, 0, 0.2, 0.9, 0.1, 0.4, 0, 0, 0, 0];
    const cols = columnPeaks(peaks, 10, 2, 6, 4, 0, 4);
    expect(cols).toEqual([0.2, 0.9, 0.1, 0.4]);
  });

  it("only covers the visible window when scrolled", () => {
    const peaks = [0, 0, 0.2, 0.9, 0.1, 0.4, 0, 0, 0, 0];
    expect(columnPeaks(peaks, 10, 2, 6, 4, 2, 2)).toEqual([0.1, 0.4]);
  });

  it("samples a narrower slice when zoomed in", () => {
    // 8 px over the 4 s window is 0.5 s/px; scrolled 2 px, 4 columns cover
    // 3.0-3.5, 3.5-4.0, 4.0-4.5, 4.5-5.0 s.
    const peaks = [0, 0, 0.2, 0.9, 0.1, 0.4, 0, 0, 0, 0];
    expect(columnPeaks(peaks, 10, 2, 6, 8, 2, 4)).toEqual([0.9, 0.9, 0.1, 0.1]);
  });

  it("is zero outside the clip and empty without peaks", () => {
    expect(columnPeaks([1, 1], 2, -2, 2, 4, 0, 4)).toEqual([0, 0, 1, 1]);
    expect(columnPeaks([], 2, 0, 2, 4, 0, 4)).toEqual([0, 0, 0, 0]);
  });
});
