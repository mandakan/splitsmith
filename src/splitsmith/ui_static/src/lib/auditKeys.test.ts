import { describe, expect, it } from "vitest";

import { isPeekKey } from "@/lib/auditKeys";

describe("isPeekKey", () => {
  it("P, and Q for the left hand, either case", () => {
    expect(["p", "P", "q", "Q"].every(isPeekKey)).toBe(true);
  });

  it("nothing else", () => {
    expect(["o", "w", "e", " ", "Shift"].some(isPeekKey)).toBe(false);
  });
});
