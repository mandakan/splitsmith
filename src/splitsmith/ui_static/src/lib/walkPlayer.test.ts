import { describe, expect, it } from "vitest";

import { passSamples } from "./walkPlayer";

describe("passSamples", () => {
  const samples = Float32Array.from({ length: 1000 }, (_, i) => i / 1000);

  it("is the window, then its silence", () => {
    const out = passSamples(samples, 100, 2, 4, 1.5);
    expect(out.length).toBe(200 + 150);
    expect(out[0]).toBeCloseTo(0.2);
    expect(out[199]).toBeCloseTo(0.399);
    expect(out.subarray(200).every((v) => v === 0)).toBe(true);
  });

  it("stays inside the clip", () => {
    expect(passSamples(samples, 100, -1, 0.5, 0).length).toBe(50);
    expect(passSamples(samples, 100, 9.5, 12, 0).length).toBe(50);
    expect(passSamples(samples, 100, 11, 12, 0).length).toBe(0);
  });
});
