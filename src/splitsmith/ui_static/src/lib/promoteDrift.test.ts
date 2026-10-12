import { describe, expect, it } from "vitest";

import { driftLabel, worthPromoting, type AuditDrift } from "./promoteDrift";

const d = (over: Partial<AuditDrift>): AuditDrift => ({
  state: "same",
  fixture_shots: 34,
  audit_shots: 34,
  moved: 0,
  ...over,
});

describe("driftLabel", () => {
  it("says what changed since the fixture was made", () => {
    expect(driftLabel(false, undefined)).toBe("new");
    expect(driftLabel(true, undefined)).toBe("...");
    expect(driftLabel(true, null)).toBe("not compared");
    expect(driftLabel(true, d({}))).toBe("same");
    expect(driftLabel(true, d({ state: "count", audit_shots: 37 }))).toBe("audit has 3 more shots");
    expect(driftLabel(true, d({ state: "count", audit_shots: 33 }))).toBe("audit has 1 fewer shot");
    expect(driftLabel(true, d({ state: "times", fixture_shots: 15, audit_shots: 15, moved: 15 }))).toBe(
      "15 of 15 times moved",
    );
  });
});

describe("worthPromoting", () => {
  it("is a new fixture, or one the audit moved on from", () => {
    expect(worthPromoting(false, undefined)).toBe(true);
    expect(worthPromoting(true, d({ state: "count", audit_shots: 37 }))).toBe(true);
    expect(worthPromoting(true, d({ state: "times", moved: 2 }))).toBe(true);
    expect(worthPromoting(true, d({}))).toBe(false);
    expect(worthPromoting(true, null)).toBe(false);
    expect(worthPromoting(true, undefined)).toBe(false);
  });
});
