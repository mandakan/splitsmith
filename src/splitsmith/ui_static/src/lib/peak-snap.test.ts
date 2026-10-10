import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { EXACT_PX_PER_SECOND, placeTime, snapToLeadingEdge } from "@/lib/peak-snap";

interface RiseFootCase {
  name: string;
  floor: number;
  lines?: [number, number, number, number][];
  bursts?: [number, number][];
  time: number;
  expect: number | null;
}
interface RiseFootFixture {
  bins: number;
  duration: number;
  burst: { rise_bins: number; decay_bins: number; decay_tau_bins: number };
  cases: RiseFootCase[];
}

// The same file tests/test_rise_foot.py reads: the shot-time definition
// (splitsmith.rise_foot) holds on both sides or not at all.
const here = dirname(fileURLToPath(import.meta.url));
const RISE_FOOT = JSON.parse(
  readFileSync(join(here, "../../../../../tests/fixtures/rise_foot/cases.json"), "utf8"),
) as RiseFootFixture;

function buildCase(c: RiseFootCase): number[] {
  const { rise_bins: rise, decay_bins: decay, decay_tau_bins: tau } = RISE_FOOT.burst;
  const p = new Array<number>(RISE_FOOT.bins).fill(c.floor);
  for (const [start, end, a, b] of c.lines ?? []) {
    for (let k = start; k < end; k++) p[k] = Math.max(p[k], a + ((b - a) * (k - start)) / Math.max(1, end - start));
  }
  for (const [start, peak] of c.bursts ?? []) {
    for (let i = 0; i < rise; i++) p[start + i] = Math.max(p[start + i], c.floor + ((peak - c.floor) * (i + 1)) / rise);
    for (let i = rise; i < decay + rise; i++) {
      p[start + i] = Math.max(p[start + i], c.floor + (peak - c.floor) * Math.exp(-(i - rise) / tau));
    }
  }
  return p;
}

describe("rise foot parity with splitsmith.rise_foot", () => {
  it.each(RISE_FOOT.cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const got = snapToLeadingEdge(c.time, { peaks: buildCase(c), duration: RISE_FOOT.duration });
    if (c.expect === null) expect(got).toBeNull();
    else expect(got).toBeCloseTo(c.expect, 6);
  });
});

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
