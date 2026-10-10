import { describe, expect, it } from "vitest";

import { navRegionCount, nearestScrollTop, regionCounts } from "@/lib/breakdown";

describe("nearestScrollTop", () => {
  it("leaves a visible row alone and moves the least to show a hidden one", () => {
    expect(nearestScrollTop(0, 100, 40, 30)).toBe(0);
    expect(nearestScrollTop(0, 100, 150, 30)).toBe(80);
    expect(nearestScrollTop(200, 100, 60, 30)).toBe(60);
  });
});

describe("regionCounts", () => {
  it("splits confirmed (manual) from proposed (auto)", () => {
    expect(
      regionCounts([
        { id: "a", kind: "movement", start: 1, end: 2, source: "manual" },
        { id: "b", kind: "reload", start: 2, end: 3, source: "auto" },
        { id: "c", kind: "activation", start: 4, end: 5, source: "manual" },
      ]),
    ).toEqual({ confirmed: 2, proposed: 1 });
    expect(regionCounts([])).toEqual({ confirmed: 0, proposed: 0 });
  });
});

describe("navRegionCount", () => {
  it("sums the stages' confirmed regions", () => {
    expect(navRegionCount([{ figures: { regions: 2 } }, { figures: null }, {}, { figures: { regions: 1 } }])).toBe(3);
  });

  it("is undefined, never 0, when no stage has a region: the row shows nothing", () => {
    expect(navRegionCount([{ figures: { regions: null } }, { figures: null }])).toBeUndefined();
    expect(navRegionCount([])).toBeUndefined();
    expect(navRegionCount(null)).toBeUndefined();
  });
});
