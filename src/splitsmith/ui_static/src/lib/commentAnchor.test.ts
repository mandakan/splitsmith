import { describe, expect, it } from "vitest";

import { SNAP_TOLERANCE_S, snapToShot } from "./commentAnchor";
import type { CoachShot } from "./api";

function shot(n: number, t: number, id: string | null = `cand-${n}`): CoachShot {
  return {
    id,
    shot_number: n,
    ms_after_beep: t * 1000,
    time_from_beep: t,
    time_absolute: t + 10,
    split: 0.2,
    interval_class: null,
    interval_class_source: null,
    improvement_flag: false,
    coaching_note: null,
    stale: false,
    reload_hint: false,
  };
}

const SHOTS = [shot(1, 1.0), shot(2, 1.2), shot(3, 5.0)];

describe("snapToShot", () => {
  it("snaps when inside the tolerance", () => {
    expect(snapToShot(5.05, SHOTS)).toEqual({
      anchor_kind: "shot",
      anchor_shot_id: "cand-3",
      shot_number: 3,
    });
  });

  it("does not snap outside the tolerance", () => {
    expect(snapToShot(3.0, SHOTS)).toEqual({
      anchor_kind: "time",
      anchor_shot_id: null,
      shot_number: null,
    });
  });

  it("does not snap exactly at the tolerance boundary", () => {
    expect(snapToShot(5.0 + SNAP_TOLERANCE_S, SHOTS).anchor_kind).toBe("time");
  });

  it("snaps just inside the boundary", () => {
    expect(snapToShot(5.0 + SNAP_TOLERANCE_S - 0.001, SHOTS).anchor_kind).toBe("shot");
  });

  it("picks the nearer of two close shots", () => {
    expect(snapToShot(1.19, SHOTS).anchor_shot_id).toBe("cand-2");
    expect(snapToShot(1.02, SHOTS).anchor_shot_id).toBe("cand-1");
  });

  it("falls back to a time anchor when the nearest shot has no id", () => {
    expect(snapToShot(1.0, [shot(1, 1.0, null)])).toEqual({
      anchor_kind: "time",
      anchor_shot_id: null,
      shot_number: null,
    });
  });

  it("handles an empty shot table", () => {
    expect(snapToShot(1.0, []).anchor_kind).toBe("time");
  });

  it("handles a negative t (pre-beep draw)", () => {
    expect(snapToShot(-0.5, SHOTS).anchor_kind).toBe("time");
  });
});
