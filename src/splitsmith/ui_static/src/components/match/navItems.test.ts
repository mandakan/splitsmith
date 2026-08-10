import { describe, expect, it } from "vitest";
import { matchNavItems } from "@/components/match/navItems";

describe("matchNavItems jobs entry", () => {
  it("links to the jobs page and badges the failed count", () => {
    const items = matchNavItems({
      base: "/match/m1",
      hasFootage: true,
      beepReviewPendingCount: 0,
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
