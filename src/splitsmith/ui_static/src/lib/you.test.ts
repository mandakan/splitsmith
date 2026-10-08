import { describe, expect, it } from "vitest";

import type { ScoreboardIdentity } from "@/lib/api";
import { identityIsSet, isYou, sortBook, sourceLine } from "@/lib/you";

const ME: ScoreboardIdentity = { shooter_id: 42, display_name: "Mathias", division: null, club: null, base_url: null };

describe("isYou", () => {
  it("matches by SSI shooter id only", () => {
    expect(isYou(42, ME)).toBe(true);
    expect(isYou(7, ME)).toBe(false);
    expect(isYou(null, ME)).toBe(false);
    expect(isYou(undefined, ME)).toBe(false);
    expect(isYou(42, null)).toBe(false);
  });
});

describe("sourceLine", () => {
  it("says where the look comes from and what saving does", () => {
    expect(sourceLine("book", true)).toMatch(/shooter book/);
    expect(sourceLine("match", true)).toMatch(/updates your shooter book/);
    expect(sourceLine("match", false)).toMatch(/Link this shooter's scoreboard entry/);
    expect(sourceLine("none", false)).toMatch(/Link this shooter's scoreboard entry/);
    expect(sourceLine("match", true, false)).toBe("Set for this match.");
    expect(sourceLine("none", true, false)).not.toMatch(/book/);
    for (const line of [sourceLine("book", true), sourceLine("none", true)]) {
      expect(line).toMatch(/^[ -~]+$/);
      expect(line).not.toMatch(/ - |--/);
    }
  });
});

describe("identityIsSet", () => {
  it("treats an all-empty identity as nothing", () => {
    expect(identityIsSet(null)).toBe(false);
    expect(identityIsSet({ accent: null, logo: null, club: null })).toBe(false);
    expect(identityIsSet({ accent: null, logo: null, club: "PK" })).toBe(true);
  });
});

describe("sortBook", () => {
  it("puts you first, then by name", () => {
    const rows = [
      { shooter_id: 9, label: "Zara" },
      { shooter_id: 3, label: "Anna" },
      { shooter_id: 42, label: "Mathias" },
    ];
    expect(sortBook(rows, ME).map((r) => r.shooter_id)).toEqual([42, 3, 9]);
    expect(sortBook(rows, null).map((r) => r.shooter_id)).toEqual([3, 42, 9]);
  });
});
