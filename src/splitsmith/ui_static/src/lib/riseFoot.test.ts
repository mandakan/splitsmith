import { describe, expect, it } from "vitest";

import type { AuditMarker } from "@/components/MarkerLayer";

import { earlierFootFor, footFor, footReadout, readSnapOn, seatAllMoves, writeSnapOn } from "./riseFoot";

/** 1 ms bins over 10 s: a quiet floor and a shot rising from each start bin. */
function trace(floor: number, shots: number[]): { peaks: number[]; duration: number } {
  const p = new Array(10000).fill(floor);
  for (const start of shots) {
    for (let i = 0; i < 10; i++) p[start + i] = Math.max(p[start + i], floor + ((1 - floor) * (i + 1)) / 10);
    for (let i = 10; i < 60; i++) p[start + i] = Math.max(p[start + i], floor + (1 - floor) * Math.exp(-(i - 10) / 15));
  }
  return { peaks: p, duration: 10 };
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

describe("snap setting", () => {
  it("is on until turned off, and remembered", () => {
    window.localStorage.clear();
    expect(readSnapOn()).toBe(true);
    writeSnapOn(false);
    expect(readSnapOn()).toBe(false);
    writeSnapOn(true);
    expect(readSnapOn()).toBe(true);
  });
});

describe("footReadout", () => {
  it("says where the shot sits against the rule", () => {
    expect(footReadout(3.0, 3.0)).toEqual({ text: "On the rise foot", onRule: true });
    expect(footReadout(2.993, 3.0)).toEqual({ text: "7 ms before the rise foot", onRule: false });
    expect(footReadout(3.012, 3.0)).toEqual({ text: "12 ms after the rise foot", onRule: false });
    expect(footReadout(3.0, null)).toBeNull();
  });
});

describe("seatAllMoves", () => {
  const peaks = trace(0.01, [3000, 5000]);

  it("moves every kept shot that is off its foot, and no other", () => {
    const markers = [
      m("on", "detected", 3.0), // already on its foot
      m("late", "manual", 5.012), // 12 ms late
      m("gone", "rejected", 5.02), // rejected: never moved
      m("silent", "detected", 8.0), // no sound: no foot
    ];
    expect(seatAllMoves(markers, peaks, (x) => x.time)).toEqual([{ id: "late", from: 5.012, to: 5.0 }]);
  });

  it("looks for each foot from where the shot was placed", () => {
    // Seated to 5.0 earlier; placed at 5.012: the foot comes from 5.012.
    const markers = [m("a", "detected", 5.0)];
    expect(seatAllMoves(markers, peaks, () => 5.012)).toEqual([]);
    expect(footFor(5.012, peaks)).toBe(5.0);
    expect(footFor(5.0, null)).toBeNull();
  });
});

describe("earlierFootFor", () => {
  /** A blast at 2.000 s, a quiet gap, then a louder reflection at 2.030 s:
   *  the hand and head cams' shape (2026-10-13) the rule seats on the reflection. */
  function blastThenReflection() {
    const p = new Array(10000).fill(0.01);
    for (let i = 0; i < 12; i++) p[2000 + i] = i < 2 ? 0.3 * (i + 1) : 0.6;
    for (let i = 0; i < 40; i++) p[2030 + i] = i < 2 ? 0.45 * (i + 1) : 0.9;
    return { peaks: p, duration: 10 };
  }

  it("finds the blast before the reflection the rule seats a shot on", () => {
    const tr = blastThenReflection();
    const seated = footFor(2.035, tr)!;
    expect(seated).toBeGreaterThan(2.025);
    const earlier = earlierFootFor(seated, tr)!;
    expect(earlier).toBeLessThan(2.003);
  });

  it("is null when nothing sounds before the shot within reach", () => {
    expect(earlierFootFor(2.0, trace(0.01, [2000]))).toBeNull();
    expect(earlierFootFor(2.0, null)).toBeNull();
  });
});

