import { describe, expect, it } from "vitest";

import type { CoachShot, StageEvent } from "@/lib/api";
import { playheadX, shotLabel, stepShot, stripGeometry, stripTarget } from "@/lib/stageStrip";

function shot(n: number, t: number, cls: CoachShot["interval_class"]): CoachShot {
  return {
    id: `c${n}`,
    shot_number: n,
    ms_after_beep: Math.round(t * 1000),
    time_from_beep: t,
    time_absolute: t + 5,
    split: 0,
    interval_class: cls,
    interval_class_source: "auto",
    improvement_flag: false,
    coaching_note: null,
    stale: false,
    reload_hint: false,
  };
}

const SHOTS = [shot(1, 2, "first_shot"), shot(2, 2.3, "split"), shot(3, 6, "movement"), shot(4, 9, null)];

describe("stripGeometry", () => {
  it("spans the stage time and places each shot as a fraction, in its class's tick", () => {
    const g = stripGeometry(SHOTS, [], 10);
    expect(g.duration).toBe(10);
    g.ticks.forEach((t, i) => expect(t.x).toBeCloseTo([0.2, 0.23, 0.6, 0.9][i]));
    expect(g.ticks.map((t) => t.tick)).toEqual(["draw", "fire", "movement", "muted"]);
  });

  it("stretches to a last shot past the stage time", () => {
    expect(stripGeometry(SHOTS, [], 8).duration).toBe(9);
  });

  it("never divides by zero", () => {
    expect(stripGeometry([], [], 0).duration).toBe(1);
  });

  it("draws confirmed regions only, tall bars first and reloads over them, clamped to the strip", () => {
    const events: StageEvent[] = [
      { id: "r", kind: "reload", start: 4, end: 5, source: "manual" },
      { id: "m", kind: "movement", start: 3, end: 12, source: "manual" },
      { id: "a", kind: "activation", start: 1, end: 2, source: "auto" },
    ];
    const g = stripGeometry(SHOTS, events, 10);
    expect(g.bars.map((b) => b.id)).toEqual(["m", "r"]);
    expect(g.bars[0]).toMatchObject({ x0: 0.3, x1: 1, tall: true });
    expect(g.bars[1]).toMatchObject({ x0: 0.4, x1: 0.5, tall: false });
  });
});

describe("stripTarget", () => {
  const g = stripGeometry(SHOTS, [], 10);
  it("snaps to the nearest shot within the snap distance", () => {
    expect(stripTarget(605, 1000, g)).toEqual({ t: 6, shotNumber: 3 });
    // 2.0 and 2.3 are 30 px apart at this width: the nearer one wins.
    expect(stripTarget(226, 1000, g)).toEqual({ t: 2.3, shotNumber: 2 });
  });

  it("is the raw time beyond the snap distance", () => {
    const r = stripTarget(450, 1000, g);
    expect(r.shotNumber).toBeNull();
    expect(r.t).toBeCloseTo(4.5);
  });

  it("clamps a tap past either end", () => {
    expect(stripTarget(-20, 1000, g).t).toBe(0);
    expect(stripTarget(2000, 1000, g).t).toBe(10);
  });
});

describe("keys and labels", () => {
  const ticks = stripGeometry(SHOTS, [], 10).ticks;
  it("steps between shots and stops at the ends", () => {
    expect(stepShot(ticks, 2, "ArrowRight")).toBe(3);
    expect(stepShot(ticks, 2, "ArrowLeft")).toBe(1);
    expect(stepShot(ticks, 1, "ArrowLeft")).toBe(1);
    expect(stepShot(ticks, 4, "ArrowRight")).toBe(4);
    expect(stepShot(ticks, null, "ArrowRight")).toBe(1);
    expect(stepShot(ticks, 2, "End")).toBe(4);
    expect(stepShot([], 2, "Home")).toBeNull();
  });

  it("names a shot by ordinal, time and class", () => {
    expect(shotLabel({ shot_number: 7, time_from_beep: 11.25, interval_class: "split" })).toBe("Shot 07, 11.25 s, fire");
    expect(shotLabel({ shot_number: 12, time_from_beep: 3, interval_class: null })).toBe("Shot 12, 3.00 s, unclassified");
  });

  it("places the playhead", () => {
    expect(playheadX(5, 10)).toBe(0.5);
    expect(playheadX(-1, 10)).toBe(0);
  });
});
