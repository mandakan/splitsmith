import { describe, expect, it } from "vitest";

import { driftLabel, labelCarryLabel, worthPromoting, type AuditDrift, type LabelCarry } from "./promoteDrift";

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

describe("labelCarryLabel", () => {
  const carry = (over: Partial<LabelCarry>): LabelCarry => ({
    reasons_carried: 0,
    subclasses_carried: 0,
    walk_decisions_carried: 0,
    reasons_dropped: [],
    subclasses_dropped: [],
    ...over,
  });

  it("says nothing for a first promote", () => {
    expect(labelCarryLabel(null)).toBeNull();
    expect(labelCarryLabel(undefined)).toBeNull();
  });

  it("counts what was kept", () => {
    expect(labelCarryLabel(carry({ reasons_carried: 1, subclasses_carried: 3 }))).toBe(
      "kept 1 reason, 3 subclasses",
    );
  });

  it("lists every dropped label by time", () => {
    expect(
      labelCarryLabel(
        carry({
          reasons_carried: 2,
          reasons_dropped: [{ time: 4.5, label: "echo", why: "no candidate" }],
          subclasses_dropped: [{ time: 2.25, label: "steel", why: "no shot" }],
        }),
      ),
    ).toBe("kept 2 reasons, 0 subclasses; dropped 2.250 s steel (no shot), 4.500 s echo (no candidate)");
  });
});
