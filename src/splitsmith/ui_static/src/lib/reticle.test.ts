import { describe, expect, it } from "vitest";

import {
  beepStartNear,
  columnPeaks,
  formatTime,
  initialPps,
  ppsBounds,
  ticks,
  tickStep,
  timeAtX,
  visibleGain,
  xAtTime,
} from "./reticle";

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

/** 1 ms bins over 10..20 s: a quiet hum, a quiet beep rising at 14.000 s
 *  (2 % of the shots) and a shot at 15.000 s. */
function beepAndShot(): number[] {
  const p = new Array(10000).fill(0.0004);
  const put = (t: number, levels: number[]) =>
    levels.forEach((v, k) => (p[Math.round((t - 10) * 1000) + k] = v));
  put(14.0, [0.004, 0.012, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02]);
  put(15.0, [0.2, 0.7, 1.0, 0.8, 0.5]);
  return p;
}

describe("the beep picker on a quiet beep", () => {
  const range = { start: 10, end: 20 };

  it("scales the visible window to its own loudest bar, never past the cap", () => {
    expect(visibleGain([0.02, 0.01, null])).toBeCloseTo(50);
    expect(visibleGain([0.5, 1.0])).toBe(1);
    expect(visibleGain([0.0001])).toBe(50);
    expect(visibleGain([null, null])).toBe(1);
  });

  it("finds a quiet beep's start near the line, however loud the shots", () => {
    expect(beepStartNear(beepAndShot(), range, 14.012)).toBeCloseTo(14.0, 3);
    expect(beepStartNear(beepAndShot(), range, 13.99)).toBeCloseTo(14.0, 3);
  });

  it("does not move the line where nothing rises", () => {
    expect(beepStartNear(beepAndShot(), range, 12.0)).toBeNull();
    expect(beepStartNear(new Array(10000).fill(0.2), range, 14.0)).toBeNull();
  });
});

