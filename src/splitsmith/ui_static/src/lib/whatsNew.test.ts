import { describe, expect, it } from "vitest";

import type { WhatsNewEntry } from "@/lib/api";
import { CHIP_DAYS, byMonth, chipActive, unseen } from "@/lib/whatsNew";

const entry = (id: string, date: string, chip?: string): WhatsNewEntry => ({
  id,
  date,
  title: id,
  body: `${id}.`,
  chip: chip ?? null,
});

const ENTRIES = [
  entry("editor", "2026-10-07", "look-editor"),
  entry("stings", "2026-10-07"),
  entry("division", "2026-09-28"),
];

describe("unseen", () => {
  it("keeps the file's order and drops what was seen", () => {
    expect(unseen(ENTRIES, ["stings"]).map((e) => e.id)).toEqual(["editor", "division"]);
    expect(unseen(ENTRIES, ENTRIES.map((e) => e.id))).toEqual([]);
  });
});

describe("byMonth", () => {
  it("groups under a month heading, newest first", () => {
    expect(byMonth(ENTRIES).map((g) => [g.month, g.entries.map((e) => e.id)])).toEqual([
      ["October 2026", ["editor", "stings"]],
      ["September 2026", ["division"]],
    ]);
  });
});

describe("chipActive", () => {
  const today = new Date("2026-10-20T12:00:00Z");

  it("shows a feature's chip until it is dismissed or old", () => {
    expect(chipActive(ENTRIES, [], "look-editor", today)).toBe(true);
    expect(chipActive(ENTRIES, ["chip:look-editor"], "look-editor", today)).toBe(false);
    expect(chipActive(ENTRIES, [], "nothing", today)).toBe(false);
    const later = new Date("2026-10-07T00:00:00Z");
    later.setUTCDate(later.getUTCDate() + CHIP_DAYS + 1);
    expect(chipActive(ENTRIES, [], "look-editor", later)).toBe(false);
  });

  it("stays off while the feed is unknown", () => {
    expect(chipActive(null, null, "look-editor", today)).toBe(false);
  });
});
