import { describe, expect, it } from "vitest";

import type { CoachIntervalClass, CoachMatchDistributions, CoachShot } from "@/lib/api";
import { matchBudget, timeBudget } from "@/lib/timeBudget";

// Stage 03 B6 Rear as seeded by scripts/seed_demo_match.py (real times
// read off staging 2026-09-13, classes from the auto-classifier).
const T = [1.97, 3.28, 4.21, 5.24, 5.91, 6.62, 7.42, 8.83, 9.31, 9.87, 10.36, 10.61, 13.76, 14.37, 14.69, 15.85, 16.37, 17.62, 18.24, 19.59, 20.64, 21.72, 22.37, 23.21, 23.75, 24.95, 25.72, 26.55, 31.48, 32.09];
const C: CoachIntervalClass[] = ["first_shot", "movement", "transition", "movement", "transition", "transition", "transition", "movement", "split", "transition", "split", "split", "movement", "transition", "split", "movement", "transition", "movement", "transition", "movement", "movement", "movement", "transition", "transition", "transition", "movement", "transition", "transition", "movement", "transition"];

function shots(times: number[], classes: (CoachIntervalClass | null)[]): CoachShot[] {
  return times.map((t, i) => ({
    id: `cand-${i + 1}`,
    shot_number: i + 1,
    ms_after_beep: Math.round(t * 1000),
    time_from_beep: t,
    time_absolute: t + 5,
    split: Math.round((t - (i === 0 ? 0 : times[i - 1])) * 1000) / 1000,
    interval_class: classes[i],
    interval_class_source: classes[i] ? "auto" : null,
    improvement_flag: false,
    coaching_note: null,
    stale: false,
    reload_hint: false,
  }));
}

const DIST = {
  distributions: [
    { interval_class: "first_shot", mean_s: 1.81, median_s: 1.8, count: 4 },
    { interval_class: "movement", mean_s: 1.41, median_s: 1.4, count: 30 },
    { interval_class: "transition", mean_s: 0.73, median_s: 0.7, count: 40 },
    { interval_class: "split", mean_s: 0.405, median_s: 0.4, count: 20 },
  ],
} as unknown as CoachMatchDistributions;

describe("timeBudget", () => {
  const b = timeBudget(shots(T, C), DIST);

  it("sums to the stage time within 1 ms and orders the taxonomy", () => {
    expect(Math.abs(b.total - 32.09)).toBeLessThan(0.001);
    expect(b.segments.map((s) => s.cls)).toEqual(["first_shot", "movement", "transition", "split"]);
    expect(b.classified).toBe(true);
  });

  it("reads the spec's stage-03 figures", () => {
    const by = Object.fromEntries(b.segments.map((s) => [s.cls, s]));
    expect(by.movement.seconds).toBeCloseTo(18.92, 2);
    expect(Math.round(by.movement.share * 100)).toBe(59);
    expect(by.movement.count).toBe(11);
    expect(by.transition.seconds).toBeCloseTo(9.66, 2);
    expect(Math.round(by.transition.share * 100)).toBe(30);
    expect(by.first_shot.seconds).toBeCloseTo(1.97, 2);
    expect(by.split.seconds).toBeCloseTo(1.54, 2);
    expect(by.split.avg).toBeCloseTo(0.385, 3);
    expect(by.split.vsMatch).toBeCloseTo(-0.02, 3);
  });

  it("names shots 13 and 29 as movement outliers (over twice the match median)", () => {
    const mv = b.segments.find((s) => s.cls === "movement")!;
    expect(mv.outliers.map((o) => o.shotNumber)).toEqual([13, 29]);
    expect(mv.outliers[1].seconds).toBeCloseTo(4.93, 2);
    expect(b.outlierCount).toBe(2);
  });

  it("an unclassified stage is one segment with no baseline judgement", () => {
    const u = timeBudget(shots([1.8, 2.1, 3.5], [null, null, null]), DIST);
    expect(u.segments).toHaveLength(1);
    expect(u.segments[0]).toMatchObject({ cls: "unclassified", count: 3, share: 1, vsMatch: null, outliers: [] });
    expect(u.classified).toBe(false);
    expect(u.total).toBeCloseTo(3.5, 3);
  });

  it("is empty without shots", () => {
    expect(timeBudget([], null)).toEqual({ total: 0, segments: [], outlierCount: 0, classified: false });
  });
});

describe("matchBudget", () => {
  it("shares the axis, sums classified seconds by class, counts outliers and classified stages", () => {
    const m = matchBudget(
      [
        { stageNumber: 3, stageName: "B6 Rear", shots: shots(T, C) },
        { stageNumber: 5, stageName: "B5 Rear", shots: shots([1.8, 2.1, 3.5], [null, null, null]) },
      ],
      DIST,
    );
    expect(m.maxTotal).toBeCloseTo(32.09, 3);
    expect(m.classifiedCount).toBe(1);
    expect(m.outlierCount).toBe(2);
    expect(Math.round((m.shareByClass.movement ?? 0) * 100)).toBe(59);
    expect(m.shareByClass.unclassified).toBeUndefined();
  });
});
