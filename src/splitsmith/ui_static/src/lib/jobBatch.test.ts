import { describe, expect, it } from "vitest";

import type { Job } from "./api";
import { batchProgress, nextBatch } from "./jobBatch";

const job = (id: string, status: Job["status"] = "running"): Job =>
  ({ id, kind: "trim", status, match_id: "m" }) as Job;

describe("nextBatch", () => {
  it("starts with the jobs active on the first poll", () => {
    expect([...nextBatch(new Set(), [job("a"), job("b", "pending")])]).toEqual(["a", "b"]);
  });

  it("keeps finished members and adds newly active jobs", () => {
    expect([...nextBatch(new Set(["a", "b"]), [job("b"), job("c", "pending")])].sort()).toEqual([
      "a",
      "b",
      "c",
    ]);
  });

  it("resets when nothing is active", () => {
    expect(nextBatch(new Set(["a", "b"]), []).size).toBe(0);
  });

  it("returns the same set when nothing changed", () => {
    const prev = new Set(["a", "b"]);
    expect(nextBatch(prev, [job("b")])).toBe(prev);
    const empty = new Set<string>();
    expect(nextBatch(empty, [])).toBe(empty);
  });
});

describe("batchProgress", () => {
  it("counts batch members that left the active set as done", () => {
    expect(batchProgress(new Set(["a", "b", "c"]), [job("c")])).toEqual({ done: 2, total: 3 });
  });

  it("folds in active jobs the stored batch has not seen yet", () => {
    expect(batchProgress(new Set(["a"]), [job("b"), job("c", "pending")])).toEqual({
      done: 1,
      total: 3,
    });
  });
});
