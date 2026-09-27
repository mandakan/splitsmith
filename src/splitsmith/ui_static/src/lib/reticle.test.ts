import { describe, expect, it } from "vitest";

import { columnPeaks, formatTime, initialPps, ppsBounds, ticks, tickStep, timeAtX, xAtTime } from "./reticle";

const range = { start: 10, end: 40 };

describe("reticle geometry", () => {
  it("centre of the canvas is the centre time, and x/time round-trip", () => {
    const view = { center: 20, pps: 50, width: 300 };
    expect(timeAtX(view, 150)).toBe(20);
    expect(timeAtX(view, 200)).toBe(21);
    expect(xAtTime(view, timeAtX(view, 37))).toBeCloseTo(37);
  });

  it("zoom runs from the whole range to a quarter second", () => {
    const b = ppsBounds(300, range);
    expect(300 / b.min).toBeCloseTo(30);
    expect(300 / b.max).toBeCloseTo(0.25);
  });

  it("opens on an 8 s span, or the whole range when it is shorter", () => {
    expect(300 / initialPps(300, range)).toBeCloseTo(8);
    expect(300 / initialPps(300, { start: 0, end: 3 })).toBeCloseTo(3);
  });

  it("labelled ticks stay at least 56 px apart and get finer as you zoom", () => {
    expect(tickStep(10)).toBe(10); // 30 s across 300 px
    expect(tickStep(37.5)).toBe(2); // 8 s
    expect(tickStep(1200)).toBe(0.05); // 0.25 s
  });

  it("ruler stops at the edges of the audio", () => {
    const view = { center: 10, pps: 37.5, width: 300 };
    const ts = ticks(view, range);
    expect(Math.min(...ts.map((t) => t.time))).toBeGreaterThanOrEqual(10);
    expect(ts.find((t) => t.time === 10)?.label).toBe("10");
    expect(ts.filter((t) => t.label != null).map((t) => t.label)).toEqual(["10", "12", "14"]);
  });

  it("formats past a minute as m:ss", () => {
    expect(formatTime(65.5, 0.1)).toBe("1:05.5");
    expect(formatTime(125, 1)).toBe("2:05");
  });

  it("column peaks are the max over covered bins, null outside the audio", () => {
    const peaks = [0.1, 0.9, 0.2, 0.4]; // 4 bins over 10..14, 1 s each
    const r = { start: 10, end: 14 };
    // 1 px per second, centred so columns 0..9 cover 5..15 s.
    const cols = columnPeaks(peaks, r, { center: 10, pps: 1, width: 10 });
    expect(cols.slice(0, 5)).toEqual([null, null, null, null, null]);
    expect(cols.slice(5, 9)).toEqual([0.1, 0.9, 0.2, 0.4]);
    expect(cols[9]).toBeNull();
    // Zoomed out: one column covers all four bins.
    const wide = columnPeaks(peaks, r, { center: 12, pps: 0.25, width: 1 });
    expect(wide).toEqual([0.9]);
  });
});
