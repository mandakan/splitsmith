import { describe, expect, it } from "vitest";

import type { CoachIntervalClass } from "@/lib/api";

import { statisticSplits, splitsFromTimeline } from "./splits";

// Mirror of the Python tests for ``splitsmith.coach.statistic_splits``
// (tests/test_coach_classify.py) - the two implementations share one
// rule, so they share one test table (issue #772).

function gap(split: number, interval_class: CoachIntervalClass | null = null) {
  return { split, interval_class };
}

describe("statisticSplits", () => {
  it("counts only split-classed intervals on a classified stage", () => {
    const shots = [
      gap(1.5, "first_shot"),
      gap(0.2, "split"),
      gap(2.6, "reload"),
      gap(0.3, "split"),
      gap(0.8, "transition"),
      gap(1.9, "movement"),
    ];
    expect(statisticSplits(shots)).toEqual([0.2, 0.3]);
  });

  it("falls back to the draw + threshold rule on an unclassified stage", () => {
    // index 0 is the draw, anything above split_max_s (0.5s) is not a
    // split; the boundary itself is inclusive, as in the auto-classifier,
    // so classifying the stage never moves the figures (issue #773).
    const shots = [gap(1.5), gap(0.2), gap(2.6), gap(0.5), gap(0.3), gap(0.8)];
    expect(statisticSplits(shots)).toEqual([0.2, 0.5, 0.3]);
  });

  it("trusts the classes once any shot is classified", () => {
    const shots = [gap(1.5), gap(0.2, "split"), gap(0.25)];
    expect(statisticSplits(shots)).toEqual([0.2]);
  });

  it("yields nothing when no interval is a split", () => {
    const shots = [gap(1.5, "first_shot"), gap(2.6, "reload"), gap(1.2, "transition")];
    expect(statisticSplits(shots)).toEqual([]);
    expect(statisticSplits([])).toEqual([]);
  });
});

describe("splitsFromTimeline", () => {
  it("pairs time-ordered gaps with each shot's class; first gap is the draw", () => {
    const pairs = splitsFromTimeline([
      { time_after_beep: 1.8, interval_class: "split" },
      { time_after_beep: 1.5, interval_class: "first_shot" },
      { time_after_beep: 4.4, interval_class: "reload" },
    ]);
    expect(pairs).toEqual([
      { split: 1.5, interval_class: "first_shot" },
      { split: expect.closeTo(0.3, 5), interval_class: "split" },
      { split: expect.closeTo(2.6, 5), interval_class: "reload" },
    ]);
  });

  it("feeds statisticSplits the classified rule end-to-end", () => {
    const pairs = splitsFromTimeline([
      { time_after_beep: 1.5, interval_class: "first_shot" },
      { time_after_beep: 1.8, interval_class: "split" },
      { time_after_beep: 4.4, interval_class: "reload" },
      { time_after_beep: 4.7, interval_class: "split" },
    ]);
    expect(statisticSplits(pairs)).toEqual([
      expect.closeTo(0.3, 5),
      expect.closeTo(0.3, 5),
    ]);
  });

  it("returns [] for an empty timeline", () => {
    expect(splitsFromTimeline([])).toEqual([]);
  });
});
