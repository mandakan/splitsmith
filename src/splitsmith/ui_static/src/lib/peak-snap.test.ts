import { describe, expect, it } from "vitest";

import { EXACT_PX_PER_SECOND, placeTime, snapToLeadingEdge } from "@/lib/peak-snap";

/** 10 s of 1 ms bins at ``floor``, with shots given as [start bin, peak]. */
function trace(floor: number, shots: [number, number][]): number[] {
  const p = new Array(10000).fill(floor);
  for (const [start, peak] of shots) {
    for (let i = 0; i < 10; i++) p[start + i] = Math.max(p[start + i], floor + ((peak - floor) * (i + 1)) / 10);
    for (let i = 10; i < 60; i++) p[start + i] = Math.max(p[start + i], floor + (peak - floor) * Math.exp(-(i - 10) / 15));
  }
  return p;
}

const SNAP = (peaks: number[]) => ({ peaks, duration: 10 });

describe("snapToLeadingEdge", () => {
  it("lands on the foot of the rise, not the loudest bin", () => {
    const peaks = trace(0.01, [[3000, 1]]);
    // Dropped 12 ms in, on the loud part: the edge is the shot's first bin.
    expect(snapToLeadingEdge(3.012, SNAP(peaks))).toBeCloseTo(3.0, 6);
  });

  it("stops at the noise floor in a noisy stage", () => {
    // 5 % of the peak (0.05) is below this floor (0.12): a walk that only
    // compares with the peak would run back through the noise.
    const peaks = trace(0.12, [[3000, 1]]);
    const edge = snapToLeadingEdge(3.01, SNAP(peaks))!;
    expect(edge).toBeGreaterThanOrEqual(2.999);
    expect(edge).toBeLessThanOrEqual(3.002);
  });

  it("does not walk onto an earlier echo", () => {
    // An echo of the previous shot still decaying when this one starts.
    const peaks = trace(0.01, [
      [2950, 0.6],
      [3000, 1],
    ]);
    expect(snapToLeadingEdge(3.008, SNAP(peaks))).toBeCloseTo(3.0, 2);
  });

  it("leaves silence alone", () => {
    expect(snapToLeadingEdge(5, SNAP(trace(0.01, [])))).toBeNull();
  });
});

describe("placeTime", () => {
  const peaks = trace(0.01, [[3000, 1]]);

  it("snaps to the leading edge when a pixel is coarser than 2 ms", () => {
    expect(placeTime(3.012, { pxPerSecond: 100, shiftKey: false, peaks: SNAP(peaks) })).toBeCloseTo(3.0, 6);
  });

  it("places exactly, on the 1 ms grid, once a pixel is 2 ms or finer", () => {
    expect(EXACT_PX_PER_SECOND).toBe(500);
    expect(placeTime(3.0124, { pxPerSecond: 500, shiftKey: false, peaks: SNAP(peaks) })).toBeCloseTo(3.012, 6);
  });

  it("places exactly with Shift at any zoom", () => {
    expect(placeTime(3.0124, { pxPerSecond: 100, shiftKey: true, peaks: SNAP(peaks) })).toBeCloseTo(3.012, 6);
  });

  it("places exactly when there is nothing to snap to", () => {
    expect(placeTime(5.0004, { pxPerSecond: 100, shiftKey: false, peaks: SNAP(peaks) })).toBeCloseTo(5.0, 6);
    expect(placeTime(3.0124, { pxPerSecond: 100, shiftKey: false, peaks: null })).toBeCloseTo(3.012, 6);
  });
});
