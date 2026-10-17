import { describe, expect, it } from "vitest";

import { scrubSource } from "./scrubSource";

describe("scrubSource", () => {
  const base = { trimVersion: "t1", scrubVersion: "w1", fullRes: false, failed: false };

  it.each([
    ["a fresh rendition plays", base, { kind: "scrub", version: "w1" }],
    ["no rendition keeps the trim", { ...base, scrubVersion: null }, { kind: "trim", version: "t1" }],
    ["an undefined rendition keeps the trim", { ...base, scrubVersion: undefined }, { kind: "trim", version: "t1" }],
    ["the full-resolution switch keeps the trim", { ...base, fullRes: true }, { kind: "trim", version: "t1" }],
    ["a failed rendition falls back to the trim", { ...base, failed: true }, { kind: "trim", version: "t1" }],
    ["a trim without a version still plays", { ...base, scrubVersion: null, trimVersion: undefined }, { kind: "trim", version: null }],
  ] as const)("%s", (_name, args, expected) => {
    expect(scrubSource(args)).toEqual(expected);
  });

  it("plays the rendition on a phone even when full resolution is on", () => {
    expect(scrubSource({ ...base, fullRes: true, phone: true })).toEqual({ kind: "scrub", version: "w1" });
    expect(scrubSource({ ...base, fullRes: true, phone: false })).toEqual({ kind: "trim", version: "t1" });
  });

  it("falls back to the trim on a phone when the rendition failed", () => {
    expect(scrubSource({ ...base, failed: true, phone: true })).toEqual({ kind: "trim", version: "t1" });
  });
});
