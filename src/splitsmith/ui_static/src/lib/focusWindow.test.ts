import { describe, expect, it } from "vitest";

import { focusWindow, fracOf, playWindow } from "@/lib/focusWindow";

describe("the focus window", () => {
  it("is the region plus 2 s each side", () => {
    expect(focusWindow({ start: 8, end: 9.4 }, 30)).toEqual({ from: 6, to: 11.4 });
  });
  it("keeps 3 s for a short region, also near the end of a short stage", () => {
    const w = focusWindow({ start: 10, end: 10.05 }, 30);
    expect(w.to - w.from).toBeCloseTo(4.05);
    const tiny = focusWindow({ start: 10, end: 10.05 }, 10.2);
    expect(tiny.to - tiny.from).toBeGreaterThanOrEqual(3);
    expect(tiny.to).toBe(10.2);
  });
  it("slides inside the stage at either end rather than shrinking", () => {
    // -1.7..3 slides right to 0..4.7; 27..32 slides left to 25..30.
    expect(focusWindow({ start: 0.3, end: 1 }, 30)).toEqual({ from: 0, to: 4.7 });
    expect(focusWindow({ start: 29, end: 30 }, 30)).toEqual({ from: 25, to: 30 });
  });
  it("is the whole stage when the stage is shorter than 3 s", () => {
    expect(focusWindow({ start: 0.5, end: 1 }, 2)).toEqual({ from: 0, to: 2 });
  });
  it("maps a time to a fraction of the window, clamped", () => {
    const w = { from: 6, to: 11 };
    expect(fracOf(8.5, w)).toBeCloseTo(0.5);
    expect(fracOf(2, w)).toBe(0);
    expect(fracOf(20, w)).toBe(1);
  });
  it("plays half a second either side, inside the stage", () => {
    expect(playWindow({ start: 8, end: 9 }, 30)).toEqual({ from: 7.5, to: 9.5 });
    expect(playWindow({ start: 0.2, end: 29.8 }, 30)).toEqual({ from: 0, to: 30 });
  });
});
