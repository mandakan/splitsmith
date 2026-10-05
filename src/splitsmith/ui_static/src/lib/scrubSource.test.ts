import { describe, expect, it } from "vitest";

import { scrubSource } from "./scrubSource";

describe("scrubSource", () => {
  const base = { trimVersion: "t1", scrubVersion: "w1", fullRes: false, failed: false };

  it.each([
    ["a fresh rendition plays", base, { kind: "web", version: "w1" }],
    ["no rendition keeps the trim", { ...base, scrubVersion: null }, { kind: "trim", version: "t1" }],
    ["an undefined rendition keeps the trim", { ...base, scrubVersion: undefined }, { kind: "trim", version: "t1" }],
    ["the full-resolution switch keeps the trim", { ...base, fullRes: true }, { kind: "trim", version: "t1" }],
    ["a failed rendition falls back to the trim", { ...base, failed: true }, { kind: "trim", version: "t1" }],
    ["a trim without a version still plays", { ...base, scrubVersion: null, trimVersion: undefined }, { kind: "trim", version: null }],
  ] as const)("%s", (_name, args, expected) => {
    expect(scrubSource(args)).toEqual(expected);
  });
});
