import { describe, expect, it } from "vitest";

import { capabilityDenied } from "./api";

describe("capabilityDenied", () => {
  it("denies only when the set is known and lacks the capability", () => {
    expect(capabilityDenied(["review", "share_manage"], "edit")).toBe(true);
    expect(capabilityDenied(["edit", "review"], "edit")).toBe(false);
    expect(capabilityDenied([], "edit")).toBe(true);
  });

  it("never denies while the set is unknown (null/undefined)", () => {
    // Pages keep their optimistic first render until the shell's fetch
    // resolves - unknown must not flash-hide controls on editable
    // matches.
    expect(capabilityDenied(null, "edit")).toBe(false);
    expect(capabilityDenied(undefined, "edit")).toBe(false);
  });
});
