import { describe, expect, it } from "vitest";

import type { ScoreboardIdentity } from "@/lib/api";
import { identityIsSet, isYou, pinBody, sortBook } from "@/lib/you";

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

describe("pinBody", () => {
  const pinned: ScoreboardIdentity = { ...ME, club: "Bromma PK", division: "Production Optics", base_url: "https://ssi.example" };

  it("keeps what the pin knows when it is the same shooter", () => {
    expect(pinBody({ shooterId: 42, name: "Mathias Axell" }, pinned)).toEqual({
      shooter_id: 42,
      display_name: "Mathias Axell",
      club: "Bromma PK",
      division: "Production Optics",
      base_url: "https://ssi.example",
    });
  });

  it("takes the new shooter's own club and division, and keeps the address", () => {
    expect(pinBody({ shooterId: 7, name: "Anna", club: "Solna PK", division: null }, pinned)).toEqual({
      shooter_id: 7,
      display_name: "Anna",
      club: "Solna PK",
      division: null,
      base_url: "https://ssi.example",
    });
    expect(pinBody({ shooterId: 7, name: "Anna" }, null)).toEqual({
      shooter_id: 7,
      display_name: "Anna",
      club: null,
      division: null,
      base_url: null,
    });
  });
});
