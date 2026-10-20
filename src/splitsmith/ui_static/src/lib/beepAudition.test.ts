import { describe, expect, it } from "vitest";

import { AUDITION_LEAD_S, auditionStart, isAuditionKey } from "@/lib/beepAudition";

describe("beepAudition", () => {
  it("starts the lead before the line, never before the media's start", () => {
    expect(auditionStart(25)).toBeCloseTo(25 - AUDITION_LEAD_S, 6);
    expect(auditionStart(0.1)).toBe(0);
    expect(auditionStart(3, 1)).toBe(2);
  });

  it("takes a plain B only", () => {
    const k = (key: string, mods: Partial<KeyboardEvent> = {}) =>
      isAuditionKey({ key, metaKey: false, ctrlKey: false, altKey: false, ...mods });
    expect(k("b")).toBe(true);
    expect(k("B")).toBe(true);
    expect(k("b", { metaKey: true })).toBe(false);
    expect(k("b", { ctrlKey: true })).toBe(false);
    expect(k("b", { altKey: true })).toBe(false);
    expect(k("n")).toBe(false);
  });
});
