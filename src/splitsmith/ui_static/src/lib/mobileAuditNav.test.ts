import { describe, expect, it } from "vitest";

import type { ShooterListEntry } from "@/lib/api";
import { jumpShooters, nextTarget } from "@/lib/mobileAuditNav";

const shooter = (slug: string, name: string, stages: number[], audited = 0): ShooterListEntry =>
  ({
    slug,
    name,
    selected_shooter_id: null,
    selected_competitor_id: null,
    stages_audited: audited,
    stages_total: stages.length,
    video_count: stages.length,
    cameras: [],
    stages_missing_trim: 0,
    stage_statuses: stages.map((n) => ({ stage_number: n, status: "ready" as const })),
  }) as ShooterListEntry;

describe("nextTarget", () => {
  const shooters = [shooter("alice", "Alice", [1, 2]), shooter("bob", "Bob", [2, 3])];

  it("a next stage opens that stage for the same shooter", () => {
    expect(
      nextTarget({ kind: "stage", nextSlug: "alice", nextStage: 2, label: "", sublabel: "" }, shooters),
    ).toEqual({ slug: "alice", stage: 2 });
  });

  it("a next shooter opens their own first stage, not stage 1", () => {
    expect(nextTarget({ kind: "shooter", nextSlug: "bob", label: "", sublabel: "" }, shooters)).toEqual({
      slug: "bob",
      stage: 2,
    });
  });

  it("finish has nowhere to go", () => {
    expect(nextTarget({ kind: "finish", label: "", sublabel: "" }, shooters)).toBeNull();
  });
});

describe("jumpShooters", () => {
  it("lists every shooter with their stages in order and marks the current one", () => {
    const rows = jumpShooters([shooter("bob", "Bob", [3, 2]), shooter("alice", "Alice", [1])], "alice");
    expect(rows.map((r) => [r.slug, r.current, r.stages.map((s) => s.stageNumber)])).toEqual([
      ["bob", false, [2, 3]],
      ["alice", true, [1]],
    ]);
  });
});
