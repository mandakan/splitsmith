import { describe, expect, it } from "vitest";

import {
  SPLIT_STEP_LARGE_PX,
  SPLIT_STEP_PX,
  VIEWER_MIN_PX,
  audioRowHeight,
  bandFloor,
  bandForKey,
  bandLimits,
  bandRowsHeight,
  clampBand,
  navRegionCount,
  nearestScrollTop,
  regionCounts,
  toggleBandLarge,
} from "@/lib/breakdown";

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

describe("the band splitter (#1373)", () => {
  const limits = bandLimits(700, 189);

  it("keeps the viewer at 200 px or more and the band at its floor or more", () => {
    expect(VIEWER_MIN_PX).toBe(200);
    expect(limits).toEqual({ min: 189, max: 500 });
    // A room too short for both: the band's floor wins, the viewer takes what is left.
    expect(bandLimits(300, 189)).toEqual({ min: 189, max: 189 });
    // The transport under the video is not video: the video keeps its 200.
    expect(bandLimits(700, 189, 53)).toEqual({ min: 189, max: 447 });
    expect(clampBand(50, limits)).toBe(189);
    expect(clampBand(900, limits)).toBe(500);
    expect(clampBand(300.4, limits)).toBe(300);
  });

  it("arrow keys move the separator a step (Shift a larger one), Home and End go to the limits", () => {
    expect(bandForKey("ArrowUp", false, 300, limits)).toBe(300 + SPLIT_STEP_PX);
    expect(bandForKey("ArrowDown", false, 300, limits)).toBe(300 - SPLIT_STEP_PX);
    expect(bandForKey("ArrowUp", true, 300, limits)).toBe(300 + SPLIT_STEP_LARGE_PX);
    expect(bandForKey("ArrowDown", false, 190, limits)).toBe(189);
    expect(bandForKey("ArrowUp", true, 480, limits)).toBe(500);
    // Home: the separator at the top, the viewer at its floor; End: the band at its floor.
    expect(bandForKey("Home", false, 300, limits)).toBe(500);
    expect(bandForKey("End", false, 300, limits)).toBe(189);
    expect(bandForKey("ArrowLeft", false, 300, limits)).toBeNull();
    expect(bandForKey("Enter", false, 300, limits)).toBeNull();
  });

  it("double-click goes to the band-large preset and back to the split it replaced", () => {
    const large = toggleBandLarge(320, 320, limits, null);
    expect(large).toEqual({ next: 500, previous: 320 });
    expect(toggleBandLarge(500, 500, limits, large.previous)).toEqual({ next: 320, previous: null });
    // From the page's own layout (nothing stored) and back to it.
    const fromDefault = toggleBandLarge(null, 267, limits, null);
    expect(fromDefault).toEqual({ next: 500, previous: null });
    expect(toggleBandLarge(500, 500, limits, fromDefault.previous)).toEqual({ next: null, previous: null });
  });

  it("extra band height goes to the Audio row; a smaller band leaves it alone", () => {
    expect(audioRowHeight(null, 267, 56)).toBe(56);
    expect(audioRowHeight(267, 267, 56)).toBe(56);
    expect(audioRowHeight(367, 267, 56)).toBe(156);
    expect(audioRowHeight(200, 267, 56)).toBe(56);
    // Not measured yet: the base height.
    expect(audioRowHeight(400, 0, 56)).toBe(56);
  });

  it("a band under its natural height gives its rows less height, down to Audio, Shots and one lane", () => {
    // Rows: Audio 56 + Shots 32 + three 36 px lanes = 196; least shown 124.
    expect(bandFloor(303, 196, 124)).toBe(231);
    expect(bandRowsHeight(null, 303, 196)).toBeNull();
    expect(bandRowsHeight(303, 303, 196)).toBeNull();
    expect(bandRowsHeight(400, 303, 196)).toBeNull();
    expect(bandRowsHeight(231, 303, 196)).toBe(124);
    expect(bandRowsHeight(270, 303, 196)).toBe(163);
    expect(bandRowsHeight(270, 0, 196)).toBeNull();
  });
});
