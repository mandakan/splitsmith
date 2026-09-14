import { describe, expect, it } from "vitest";
import { matchNavItems } from "@/components/match/navItems";

const base = {
  base: "/match/m1",
  shooterSlug: "s",
  hasFootage: true,
  beepReviewPendingCount: 0,
};

describe("matchNavItems shape", () => {
  it("has no jobs row; the progress strip and drawer own jobs", () => {
    expect(matchNavItems(base).find((i) => i.key === "jobs")).toBeUndefined();
  });

  it("groups rows by phase in loop order and carries the approved labels", () => {
    const items = matchNavItems(base);
    expect(items.map((i) => [i.key, i.group ?? null, i.label])).toEqual([
      ["overview", null, "Overview"],
      ["videos", "prepare", "Footage"],
      ["audit", "review", "Audit"],
      ["results", "analyse", "Splits"],
      ["coach", "analyse", "Coach"],
      ["export", "deliver", "Export"],
    ]);
  });
});

describe("matchNavItems compare entry", () => {
  it("shows Compare only on a multi-shooter match, landing on the first audited stage", () => {
    expect(matchNavItems(base).find((i) => i.key === "compare")).toBeUndefined();
    const items = matchNavItems({ ...base, multiShooter: true, compareStage: 3 });
    expect(items.map((i) => i.key)).toEqual(["overview", "videos", "audit", "results", "coach", "compare", "export"]);
    expect(items.find((i) => i.key === "compare")).toMatchObject({ group: "analyse", to: "/match/m1/compare/3", label: "Compare" });
  });
});

describe("matchNavItems audit entry", () => {
  it("badges the beeps still to confirm (beep confirmation is Audit's step 1)", () => {
    const audit = matchNavItems({ ...base, beepReviewPendingCount: 2 }).find((i) => i.key === "audit");
    expect(audit).toMatchObject({ count: 2, badgeKind: "pending", badgeAriaLabel: "2 beeps to confirm" });
    expect(matchNavItems(base).find((i) => i.key === "beep-review")).toBeUndefined();
  });
});

