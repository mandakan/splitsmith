import { describe, expect, it } from "vitest";

import type { ShooterRosterRow } from "@/lib/api";
import { canEdit, initials, rowKey, rowNote } from "@/lib/shooters";

function row(extra: Partial<ShooterRosterRow> = {}): ShooterRosterRow {
  return {
    shooter_id: 7,
    name: "Anna Jonsson",
    club: null,
    accent: null,
    logo_url: null,
    match_count: 2,
    last_match_at: "2026-10-01T00:00:00Z",
    last_match_name: "Höstfinalen XI",
    you: false,
    source: "book",
    match_id: null,
    slug: null,
    ...extra,
  };
}

describe("shooters", () => {
  it("makes initials from the first and last name", () => {
    expect(initials("Anna Jonsson")).toBe("AJ");
    expect(initials("Mathias Bo Axell")).toBe("MA");
    expect(initials("Cy")).toBe("CY");
    expect(initials("  ")).toBe("?");
  });

  it("counts matches and says when no look is set", () => {
    expect(rowNote(row())).toBe("2 matches");
    expect(rowNote(row({ match_count: 1, source: "none" }))).toBe("1 match · no look set");
  });

  it("says why a shooter without an SSI id cannot be edited", () => {
    const guest = row({ shooter_id: null, match_id: "m1", slug: "guest", match_count: 1 });
    expect(rowNote({ ...guest, source: "none" })).toMatch(/^In Höstfinalen XI\./);
    expect(canEdit(guest)).toBe(false);
    expect(rowNote(guest)).toMatch(/^Set in Höstfinalen XI\. Link them to the scoreboard/);
    expect(rowKey(guest)).toBe("m-m1-guest");
    expect(rowKey(row())).toBe("id-7");
  });
});
