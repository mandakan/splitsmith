import { describe, expect, it } from "vitest";

import type { CoachShot, StageEvent } from "@/lib/api";
import { reviewFigures, shotAtOrBefore, shotOrdinal } from "@/lib/coachReview";

function shot(n: number, t: number, split: number, cls: CoachShot["interval_class"]): CoachShot {
  return {
    id: `c${n}`,
    shot_number: n,
    ms_after_beep: Math.round(t * 1000),
    time_from_beep: t,
    time_absolute: t + 5,
    split,
    interval_class: cls,
    interval_class_source: "auto",
    improvement_flag: false,
    coaching_note: null,
    stale: false,
    reload_hint: false,
  };
}

const SHOTS = [shot(1, 1.5, 1.5, "first_shot"), shot(2, 1.8, 0.3, "split"), shot(3, 2.3, 0.5, "split"), shot(4, 5, 2.7, "movement")];

describe("reviewFigures", () => {
  it("averages the fire splits only and reads the draw off the first shot", () => {
    const f = reviewFigures(SHOTS, []);
    expect(f.avgSplit).toBeCloseTo(0.4);
    expect(f.draw).toBe(1.5);
  });

  it("orders by time, whatever order the payload has", () => {
    expect(reviewFigures([...SHOTS].reverse(), []).draw).toBe(1.5);
  });

  it("counts confirmed regions only", () => {
    const events: StageEvent[] = [
      { id: "m", kind: "movement", start: 4, end: 6, source: "auto" },
      { id: "r", kind: "reload", start: 3, end: 3.5, source: "auto" },
    ];
    expect(reviewFigures(SHOTS, events)).toMatchObject({ movingShots: 0, reloads: 0, exposedReload: 0 });
    const kept = events.map((e) => ({ ...e, source: "manual" as const }));
    const f = reviewFigures(SHOTS, kept);
    expect(f.movingShots).toBe(1);
    expect(f.reloads).toBe(1);
    expect(f.exposedReload).toBeCloseTo(0.5);
  });

  it("has no figures without shots", () => {
    expect(reviewFigures([], [])).toEqual({ avgSplit: null, draw: null, movingShots: 0, reloads: 0, exposedReload: 0 });
  });
});

describe("shotAtOrBefore", () => {
  it("finds the shot the playhead has passed, in any input order", () => {
    expect(shotAtOrBefore([...SHOTS].reverse(), 2.4)).toBe(3);
    expect(shotAtOrBefore(SHOTS, 2.3)).toBe(3);
    expect(shotAtOrBefore(SHOTS, 1)).toBeNull();
  });
});

describe("shotOrdinal", () => {
  it("pads to two digits", () => {
    expect(shotOrdinal(7)).toBe("07");
    expect(shotOrdinal(31)).toBe("31");
  });
});
