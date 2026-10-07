import { describe, expect, it } from "vitest";

import type { Rgb } from "@/lib/api";
import { contrastRatio } from "@/lib/lookEditor";
import {
  isApplied,
  READY_MADE,
  SCHEMES,
  accentSeries,
  footageAccents,
  hexToRgbSafe,
  hslToRgb,
  hueOf,
  hueDistance,
  oklabDistance,
  paletteFrom,
  rgbToHsl,
  standOut,
  weakAccent,
} from "@/lib/palette";

const BASE: Record<string, Rgb> = {
  ink: [244, 244, 245],
  ink_2: [201, 204, 210],
  muted: [142, 147, 155],
  subtle: [107, 112, 121],
  surface: [20, 23, 28],
  rule: [38, 43, 51],
  stroke: [10, 11, 13],
  accent: [255, 45, 45],
  accent_fill: [220, 38, 38],
  accent_text: [255, 255, 255],
  split: [251, 191, 36],
  split_good: [74, 222, 128],
};

// A range: grass, berm, sand, sky, as frames of it cluster.
const RANGE = [
  { rgb: [78, 104, 52] as Rgb, share: 0.38 },
  { rgb: [122, 98, 70] as Rgb, share: 0.27 },
  { rgb: [176, 160, 128] as Rgb, share: 0.2 },
  { rgb: [168, 190, 214] as Rgb, share: 0.15 },
];
const RANGE_AVERAGE: Rgb = [118, 122, 92];

describe("colour maths", () => {
  it("converts between rgb and hsl", () => {
    for (const c of [[255, 45, 45], [20, 140, 200], [74, 222, 128], [128, 128, 128]] as Rgb[]) {
      const back = hslToRgb(rgbToHsl(c));
      back.forEach((v, i) => expect(Math.abs(v - c[i])).toBeLessThanOrEqual(1));
    }
    expect(hueDistance(350, 10)).toBe(20);
    expect(hexToRgbSafe("#ff0000")).toEqual([255, 0, 0]);
    expect(oklabDistance([255, 0, 0], [255, 0, 0])).toBe(0);
    expect(oklabDistance([255, 0, 0], [0, 0, 255])).toBeGreaterThan(oklabDistance([255, 0, 0], [255, 40, 0]));
  });
});

describe("paletteFrom", () => {
  it.each(SCHEMES.map((s) => s.id))("keeps every text pair readable (%s)", (scheme) => {
    for (const accent of [[255, 45, 45], [255, 220, 40], [30, 120, 255], [180, 240, 230], [40, 40, 40]] as Rgb[]) {
      const p = paletteFrom(accent, scheme, BASE);
      expect(contrastRatio(p.colors.accent_text, p.colors.accent_fill)).toBeGreaterThanOrEqual(4.5);
      expect(p.colors.ink).toEqual(BASE.ink);
      expect(p.colors.accent).toEqual(accent);
    }
  });

  it("places the split by the scheme and keeps the good split away from the accent", () => {
    const accent: Rgb = [40, 200, 90];
    const comp = paletteFrom(accent, "complementary", BASE);
    expect(hueDistance(hueOf(comp.colors.split), hueOf(accent))).toBeGreaterThan(150);
    const analogous = paletteFrom(accent, "analogous", BASE);
    expect(hueDistance(hueOf(analogous.colors.split), hueOf(accent))).toBeLessThan(60);
    expect(hueDistance(hueOf(comp.colors.split_good), hueOf(accent))).toBeGreaterThanOrEqual(30);
    expect(comp.series[0]).toBe("#28c85a");
    expect(comp.series).toHaveLength(6);
  });
});

describe("the footage", () => {
  it("suggests accents that stand out from a green and brown range", () => {
    const picks = footageAccents(RANGE, RANGE_AVERAGE, 4);
    expect(picks).toHaveLength(4);
    for (const pick of picks) {
      const hue = hueOf(pick);
      // Not the grass (70..130) nor the berm and sand (20..50).
      expect(hue >= 70 && hue <= 130).toBe(false);
      expect(hue >= 20 && hue <= 50).toBe(false);
    }
    for (let i = 0; i < picks.length; i += 1)
      for (let j = i + 1; j < picks.length; j += 1) expect(hueDistance(hueOf(picks[i]), hueOf(picks[j]))).toBeGreaterThanOrEqual(45);
  });

  it("ranks a magenta above an olive on that range", () => {
    expect(standOut([230, 30, 140], RANGE, RANGE_AVERAGE)).toBeGreaterThan(standOut([110, 120, 50], RANGE, RANGE_AVERAGE));
  });

  it("warns when the accent sits in the footage, and only then", () => {
    const olive = weakAccent({ ...BASE, accent: [96, 116, 58] }, RANGE, RANGE_AVERAGE);
    expect(olive?.message).toMatch(/blends into this footage/);
    expect(olive?.better).toBeTruthy();
    expect(weakAccent({ ...BASE, accent: [230, 30, 140] }, RANGE, RANGE_AVERAGE)).toBeNull();
    expect(weakAccent(BASE, [], null)).toBeNull();
  });
});

describe("accentSeries", () => {
  it("starts from the seed and keeps every shooter's colour apart", () => {
    const series = accentSeries([255, 45, 45], 6).map((h) => hexToRgbSafe(h) as Rgb);
    expect(series[0]).toEqual([255, 45, 45]);
    for (let i = 0; i < series.length; i += 1)
      for (let j = i + 1; j < series.length; j += 1) expect(oklabDistance(series[i], series[j])).toBeGreaterThan(0.12);
  });

  it("avoids the footage's colours when it has them, and keeps the hues spread", () => {
    const series = accentSeries([230, 30, 140], 6, RANGE).map((h) => hexToRgbSafe(h) as Rgb);
    expect(series.every((c) => !(hueOf(c) >= 75 && hueOf(c) <= 125))).toBe(true);
    for (let i = 0; i < series.length; i += 1)
      for (let j = i + 1; j < series.length; j += 1) expect(hueDistance(hueOf(series[i]), hueOf(series[j]))).toBeGreaterThanOrEqual(30);
  });
});

describe("ready-made palettes", () => {
  it("are complete and readable", () => {
    expect(READY_MADE.map((r) => r.id)).toEqual(["outdoor", "indoor", "sand", "snow"]);
    for (const r of READY_MADE) {
      const p = paletteFrom(r.accent, r.scheme, BASE);
      expect(Object.keys(BASE).every((t) => p.colors[t])).toBe(true);
      expect(contrastRatio(p.colors.accent_text, p.colors.accent_fill)).toBeGreaterThanOrEqual(4.5);
    }
  });
});


describe("isApplied", () => {
  const colors = { accent: [255, 45, 45] as Rgb, split: [251, 191, 36] as Rgb };
  it("is true only when every colour and the series match the draft", () => {
    const s = { id: "x", label: "X", colors, series: ["#ff2d2d"] };
    expect(isApplied(s, { colors: { ...colors, ink: [1, 2, 3] as Rgb }, accent_series: ["#ff2d2d"] })).toBe(true);
    expect(isApplied(s, { colors: { ...colors, accent: [255, 45, 46] as Rgb }, accent_series: ["#ff2d2d"] })).toBe(false);
    expect(isApplied(s, { colors, accent_series: [] })).toBe(false);
  });
});
