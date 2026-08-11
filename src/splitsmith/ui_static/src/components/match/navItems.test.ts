import { describe, expect, it } from "vitest";
import { matchNavItems } from "@/components/match/navItems";

describe("matchNavItems jobs entry", () => {
  it("links to the jobs page and badges the failed count", () => {
    const items = matchNavItems({
      base: "/match/m1",
      hasFootage: true,
      beepReviewPendingCount: 0,
      triageFlaggedCount: 0,
      jobsAttentionCount: 2,
    });
    const jobs = items.find((i) => i.key === "jobs");
    expect(jobs).toMatchObject({
      to: "/match/m1/jobs",
      label: "Jobs",
      count: 2,
      badgeKind: "pending",
    });
  });
});

describe("matchNavItems triage entry", () => {
  it("links to the triage page and badges the flagged count with a non-color-only aria-label", () => {
    const items = matchNavItems({
      base: "/match/m1",
      hasFootage: true,
      beepReviewPendingCount: 0,
      triageFlaggedCount: 2,
      jobsAttentionCount: 0,
    });
    const triage = items.find((i) => i.key === "triage");
    expect(triage).toMatchObject({
      to: "/match/m1/triage",
      label: "Triage",
      count: 2,
      badgeKind: "pending",
      badgeAriaLabel: "2 stages flagged for desktop",
    });
  });

  it("singularizes the aria-label when exactly one stage is flagged", () => {
    const items = matchNavItems({
      base: "/match/m1",
      hasFootage: true,
      beepReviewPendingCount: 0,
      triageFlaggedCount: 1,
      jobsAttentionCount: 0,
    });
    const triage = items.find((i) => i.key === "triage");
    expect(triage).toMatchObject({
      badgeAriaLabel: "1 stage flagged for desktop",
    });
  });
});
