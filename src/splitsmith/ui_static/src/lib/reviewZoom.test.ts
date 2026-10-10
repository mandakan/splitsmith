import { describe, expect, it } from "vitest";

import { displayBins, reviewMaxZoom } from "@/lib/reviewZoom";

describe("displayBins", () => {
  it("keeps the base resolution at fit", () => {
    expect(displayBins(52.7, null, 1500)).toBe(1500);
  });

  it("asks for about one bin per pixel when zoomed, as a power of two", () => {
    // 52.7 s at 300 px/s is 15 810 px: the next power of two.
    expect(displayBins(52.7, 300, 1500)).toBe(16384);
  });

  it("never asks for bins finer than 1 ms", () => {
    // 52.7 s at 5000 px/s would be 263 500 px; 1 ms caps it at 52 700.
    expect(displayBins(52.7, 5000, 1500)).toBe(52700);
  });

  it("never drops below the base resolution", () => {
    expect(displayBins(52.7, 10, 1500)).toBe(1500);
  });
});

describe("reviewMaxZoom", () => {
  it("lets a long stage zoom until 1 ms is 4 px wide", () => {
    // Fit is 1500 px over 52.7 s, about 28.5 px/s; 4000 px/s is ~140x.
    expect(reviewMaxZoom(52.7, 1500)).toBeCloseTo(4000 / (1500 / 52.7), 6);
  });

  it("never offers less than the shared 16x", () => {
    expect(reviewMaxZoom(1, 1500)).toBe(16);
    expect(reviewMaxZoom(52.7, 0)).toBe(16);
  });
});
