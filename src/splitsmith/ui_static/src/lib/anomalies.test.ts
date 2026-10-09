import { describe, expect, it } from "vitest";

import { detectAnomalies } from "./anomalies";

const BEEP = 5;

/** Kept shots at these seconds from the beep, on a clip whose beep is at 5 s. */
function shots(fromBeep: number[]) {
  return fromBeep.map((t, i) => ({
    shot_number: i + 1,
    time: BEEP + t,
    time_from_beep: t,
    split: i === 0 ? t : t - fromBeep[i - 1],
  }));
}

function mismatch(fromBeep: number[], stageTime: number) {
  return detectAnomalies(shots(fromBeep), stageTime).filter((a) => a.kind === "stage_time_mismatch");
}

describe("stage time vs the timer", () => {
  it("flags a gap over a tenth of a second", () => {
    expect(mismatch([1.5, 11.429, 12.265], 12.15)).toHaveLength(1);
  });

  it("stays quiet within the timer's rounding", () => {
    expect(mismatch([1.5, 28.39], 28.37)).toHaveLength(0);
    expect(mismatch([1.5, 10.1], 10.0)).toHaveLength(0);
  });

  it("names the shots after the one the timer caught and anchors on the first", () => {
    const [flag] = mismatch([1.5, 41.588, 41.773], 41.57);
    expect(flag.shot_number).toBe(3);
    expect(flag.time).toBeCloseTo(BEEP + 41.773, 6);
    expect(flag.message).toContain("Shot 2 matches the timer");
    expect(flag.message).toContain("is shot 3 extra");
  });

  it("names a run of extra shots", () => {
    const [flag] = mismatch([1.5, 20.0, 20.3, 20.6], 20.01);
    expect(flag.shot_number).toBe(3);
    expect(flag.message).toContain("are shots 3 to 4 extra");
  });

  it("anchors on the last shot when no shot matches the timer", () => {
    const [flag] = mismatch([1.0, 1.5], 0.5);
    expect(flag.shot_number).toBe(2);
    expect(flag.message).toContain("the beep placed too early");
  });

  it("points at where the timer stopped when it ran past the last shot", () => {
    const [flag] = mismatch([1.5, 14.844], 15.51);
    expect(flag.shot_number).toBeNull();
    expect(flag.time).toBeCloseTo(BEEP + 15.51, 6);
    expect(flag.message).toBe(
      "Timer stopped 0.67 s after the last shot (15.51 s): look for a missed final shot, or a beep placed too late.",
    );
  });
});
