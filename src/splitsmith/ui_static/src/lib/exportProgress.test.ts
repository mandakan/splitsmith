import { describe, expect, it } from "vitest";

import { ApiError, type ActiveExports, type ExportView, type Job } from "@/lib/api";
import {
  exportLimitRunning,
  foldExports,
  limitReached,
  rowTimeLine,
  timeLeftLine,
} from "@/lib/exportProgress";

function job(over: Partial<Job>): Job {
  return {
    id: "j",
    kind: "export",
    status: "running",
    cancel_requested: false,
    acknowledged: false,
    created_at: "2026-10-25T12:00:00Z",
    updated_at: "2026-10-25T12:00:00Z",
    export_id: null,
    ...over,
  } as Job;
}

function view(over: Partial<ExportView>): ExportView {
  return {
    export_id: "e1",
    kind: "compare-grid",
    label: "Compare grid",
    match_id: "m1",
    status: "running",
    started_at: "2026-10-25T12:00:00Z",
    time_left_s: 540,
    finish_at: "2026-10-25T12:32:00Z",
    approximate: true,
    rows: [],
    ...over,
  } as ExportView;
}

describe("foldExports", () => {
  it("folds an export's jobs into its lead's row with their mean progress", () => {
    const folded = foldExports([
      job({ id: "s1", kind: "compare-grid-stage", export_id: "e1", status: "running", progress: 0.5 }),
      job({ id: "s2", kind: "compare-grid-stage", export_id: "e1", status: "pending", progress: null }),
      job({ id: "g", kind: "compare-grid", export_id: "e1", status: "pending", progress: 0.1 }),
      job({ id: "b", kind: "detect_beep", export_id: null }),
    ]);
    expect(folded.map((j) => j.id)).toEqual(["g", "b"]);
    expect(folded[0].status).toBe("running");
    expect(folded[0].progress).toBeCloseTo(0.2);
  });

  it("keeps an export with no lead as its first job", () => {
    const folded = foldExports([
      job({ id: "t1", export_id: "e2", status: "succeeded" }),
      job({ id: "t2", export_id: "e2", status: "pending" }),
    ]);
    expect(folded.map((j) => [j.id, j.status])).toEqual([["t1", "pending"]]);
  });
});

describe("timeLeftLine", () => {
  const now = new Date("2026-10-25T12:23:00Z");

  it("says about while the estimate has no history", () => {
    expect(timeLeftLine(view({ time_left_s: 540 }), now)).toMatch(/^About 9 min left, done by \d\d:\d\d$/);
  });

  it("drops about once the history speaks", () => {
    expect(timeLeftLine(view({ approximate: false, time_left_s: 45 }), now)).toMatch(/^Under a minute left/);
  });

  it("says taking longer when every part with work left is past its estimate", () => {
    const rows = [
      { job_id: "a", key: "k", label: "x", status: "running", progress: null, elapsed_s: 900, remaining_s: 0, overrun: true, retryable: false },
    ] as ExportView["rows"];
    expect(timeLeftLine(view({ time_left_s: 0, rows }), now)).toBe("Taking longer than usual");
  });
});

describe("limitReached", () => {
  const active = (limit: number | null, n: number): ActiveExports =>
    ({ limit, exports: Array.from({ length: n }, (_, i) => view({ export_id: `e${i}` })) }) as ActiveExports;

  it("is reached at the limit and never on desktop", () => {
    expect(limitReached(active(1, 1))).toBe(true);
    expect(limitReached(active(2, 1))).toBe(false);
    expect(limitReached(active(null, 5))).toBe(false);
    expect(limitReached(null)).toBe(false);
  });
});

describe("exportLimitRunning", () => {
  it("reads the running export from the 409", () => {
    const running = { export_id: "e1", kind: "compare-grid", label: "Compare grid", match_id: "m1" };
    const err = new ApiError(409, "x", { code: "export_limit", running });
    expect(exportLimitRunning(err)).toEqual(running);
    expect(exportLimitRunning(new ApiError(409, "busy", { code: "busy" }))).toBeNull();
    expect(exportLimitRunning(new Error("x"))).toBeNull();
  });
});

describe("rowTimeLine", () => {
  it("shows elapsed while running and the time taken once done", () => {
    expect(rowTimeLine({ status: "running", elapsed_s: 75 } as ExportView["rows"][number])).toBe("1:15");
    expect(rowTimeLine({ status: "done", elapsed_s: 200 } as ExportView["rows"][number])).toBe("took 3:20");
    expect(rowTimeLine({ status: "waiting", elapsed_s: null } as ExportView["rows"][number])).toBe("");
    // A failed part says so (its status word), not how long it ran.
    expect(rowTimeLine({ status: "failed", elapsed_s: 41 } as ExportView["rows"][number])).toBe("");
  });
});
