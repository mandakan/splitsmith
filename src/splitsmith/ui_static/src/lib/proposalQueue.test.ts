import { describe, expect, it } from "vitest";

import type { StageEvent } from "@/lib/api";
import { adjacentId, nextOpenId, prevOpenId, proposals, queuePosition } from "@/lib/proposalQueue";

const ev = (id: string, kind: StageEvent["kind"], start: number, source: "auto" | "manual" = "auto"): StageEvent => ({
  id,
  kind,
  start,
  end: start + 1,
  source,
});

const LIST = [ev("r2", "reload", 9), ev("m1", "movement", 3), ev("k", "reload", 5, "manual"), ev("r1", "reload", 3)];

describe("the proposal queue", () => {
  it("is the auto regions by start, a tie in lane order (movement first)", () => {
    expect(proposals(LIST).map((e) => e.id)).toEqual(["m1", "r1", "r2"]);
  });
  it("moves on to the next proposal after a time, wrapping to the first", () => {
    expect(nextOpenId(LIST, 3)).toBe("r2");
    expect(nextOpenId(LIST, 9)).toBe("m1");
    expect(nextOpenId(LIST, -Infinity)).toBe("m1");
    expect(nextOpenId([ev("k", "reload", 5, "manual")], 0)).toBeNull();
  });
  it("steps back to the proposal before, wrapping to the last", () => {
    expect(prevOpenId(LIST, 9)).toBe("r1");
    expect(prevOpenId(LIST, 3)).toBe("r2");
  });
  it("says where an open proposal sits, and nothing for a confirmed region", () => {
    expect(queuePosition(LIST, "r1")).toEqual({ index: 1, total: 3 });
    expect(queuePosition(LIST, "k")).toBeNull();
    expect(queuePosition(LIST, null)).toBeNull();
  });
  it("steps through the queue by place, so a proposal sharing a start is never skipped", () => {
    expect(adjacentId(LIST, "m1", 1)).toBe("r1");
    expect(adjacentId(LIST, "r1", 1)).toBe("r2");
    expect(adjacentId(LIST, "r2", 1)).toBe("m1");
    expect(adjacentId(LIST, "m1", -1)).toBe("r2");
    expect(adjacentId(LIST, "r1", -1)).toBe("m1");
  });
  it("from a confirmed region or nothing, steps by time; the last proposal has no neighbour", () => {
    expect(adjacentId(LIST, "k", 1)).toBe("r2");
    expect(adjacentId(LIST, "k", -1)).toBe("r1");
    expect(adjacentId(LIST, null, 1)).toBe("m1");
    expect(adjacentId([ev("m1", "movement", 3)], "m1", 1)).toBeNull();
  });
});
