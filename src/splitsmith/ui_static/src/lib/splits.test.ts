import { describe, expect, it } from "vitest";

import type { CoachIntervalClass } from "@/lib/api";

import { statisticSplits } from "./splits";

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
    // index 0 is the draw, anything above transition_min (1.0s) is not a
    // split; the boundary itself is inclusive, as in split_color_band.
    const shots = [gap(1.5), gap(0.2), gap(2.6), gap(1.0), gap(0.3)];
    expect(statisticSplits(shots)).toEqual([0.2, 1.0, 0.3]);
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
