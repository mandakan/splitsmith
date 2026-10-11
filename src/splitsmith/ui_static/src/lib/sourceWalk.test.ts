import { describe, expect, it } from "vitest";

import type { AuditMarker } from "@/components/MarkerLayer";

import {
  alignToSource,
  alignmentOk,
  mapSourceTime,
  soundOnsets,
  sourceStops,
  type SourceAlignment,
} from "./sourceWalk";

/** 1 ms bins of quiet with a shot-like burst at each onset. */
function envelope(duration: number, onsets: number[]): { peaks: number[]; duration: number } {
  const n = Math.round(duration * 1000);
  const peaks = new Array<number>(n).fill(0.01);
  for (const t of onsets) {
    const i0 = Math.round(t * 1000);
    for (let k = 0; k < 120 && i0 + k < n; k++) {
      peaks[i0 + k] = Math.max(peaks[i0 + k], k < 3 ? 0.3 + 0.35 * k : Math.exp(-(k - 3) / 25));
    }
  }
  return { peaks, duration };
}

const m = (id: string, kind: AuditMarker["kind"], time: number): AuditMarker => ({
  id,
  kind,
  time,
  candidateNumber: null,
  confidence: null,
  peakAmplitude: null,
  note: "",
});

// A source stage, and this camera hearing it 0.19 s earlier with its clock
// running 2 ms a second slow, plus a shot from the next bay.
const SOURCE = [2.216, 3.406, 4.153, 4.747, 5.688, 6.296, 7.772, 8.133];
const here = (s: number) => s - 0.19 - 0.002 * (s - SOURCE[0]);

describe("alignToSource", () => {
  it("finds the offset and the drift from the shot pattern alone", () => {
    const onsets = [...SOURCE.map(here), 5.1].sort((a, b) => a - b);
    const a = alignToSource(SOURCE, onsets)!;
    expect(a.matched).toBe(SOURCE.length);
    for (const s of SOURCE) expect(Math.abs(mapSourceTime(a, s) - here(s))).toBeLessThan(0.001);
    expect(alignmentOk(a)).toBe(true);
  });

  it("does not trust a clip where few source shots land on a sound", () => {
    const a = alignToSource(SOURCE, [1.0, 9.5, 12.25]);
    expect(alignmentOk(a)).toBe(false);
    expect(alignToSource(SOURCE, [])).toBeNull();
  });

  it("reads onsets off the audio at each burst's foot", () => {
    const onsets = soundOnsets(envelope(10, SOURCE.map(here)), 0);
    expect(onsets.length).toBe(SOURCE.length);
    onsets.forEach((t, i) => expect(Math.abs(t - here(SOURCE[i]))).toBeLessThanOrEqual(0.002));
  });
});

describe("sourceStops", () => {
  const align: SourceAlignment = { offset: -0.19, drift: 0, s0: SOURCE[0], matched: 3, total: 3 };
  const source = SOURCE.slice(0, 3);
  const onsets = source.map((s) => s - 0.19);

  it("puts each source shot on the kept shot nearest it, and lists the rest as extras", () => {
    const markers = [
      m("a", "detected", 2.03), // shot 1, 4 ms late
      m("dup", "detected", 2.06), // a second shot on the same sound
      m("b", "detected", 3.25), // shot 2, on the old snap 34 ms late
      m("r", "rejected", 3.963), // shot 3 was rejected
    ];
    const stops = sourceStops(source, align, onsets, markers);
    expect(stops.map((s) => [s.origin, s.markerId, s.time])).toEqual([
      ["source", "a", 2.03],
      ["extra", "dup", 2.06],
      ["source", "b", 3.25],
      ["source", null, 3.963],
    ]);
    expect(stops[3].source).toMatchObject({ index: 2, count: 3, heard: true });
    expect(stops[3].source!.expected).toBeCloseTo(3.963, 6);
  });

  it("falls back to the fitted time when this camera heard nothing there", () => {
    const stops = sourceStops(source, align, onsets.slice(0, 2), []);
    expect(stops[2]).toMatchObject({ markerId: null, time: 3.963, source: { heard: false } });
  });
});

describe("sourceStops repairs a snap that landed on the wrong sounds", () => {
  it("moves each misplaced kept shot onto an unclaimed source shot, and keeps the rest as extras", () => {
    const align: SourceAlignment = { offset: -0.19, drift: 0, s0: SOURCE[0], matched: 3, total: 3 };
    const source = SOURCE.slice(0, 3);
    const onsets = source.map((s) => s - 0.19);
    // The snap put every shot 170 ms late, and added one far from any shot.
    const markers = [...onsets.map((t, i) => m(`p${i}`, "detected", t + 0.17)), m("far", "detected", 9.0)];
    const stops = sourceStops(source, align, onsets, markers);
    const moved = stops.filter((s) => s.moveFrom != null);
    expect(moved.map((s) => [s.markerId, s.time])).toEqual([
      ["p0", 2.026],
      ["p1", 3.216],
      ["p2", 3.963],
    ]);
    expect(moved[0].moveFrom).toBeCloseTo(2.196, 6);
    expect(stops.filter((s) => s.origin === "extra").map((s) => s.markerId)).toEqual(["far"]);
  });
});
