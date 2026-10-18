import { describe, expect, it } from "vitest";

import type { StageEntry } from "@/lib/api";
import { indexRows } from "@/lib/breakdownIndex";

function stage(n: number, extra: Partial<StageEntry> = {}): StageEntry {
  return { stage_number: n, stage_name: `S${n}`, time_seconds: 12, skipped: false, figures: null, ...extra } as StageEntry;
}

describe("indexRows", () => {
  it("orders stages and words each one", () => {
    const rows = indexRows([
      stage(3, { figures: { shot_count: 20, regions: 4 } as never }),
      stage(1, { figures: { shot_count: 12, regions: null } as never }),
      stage(2, { skipped: true }),
      stage(4, { time_seconds: 0 }),
      stage(5, { figures: { shot_count: 8, regions: 1 } as never }),
      stage(6),
    ]);
    expect(rows.map((r) => [r.stage, r.note, r.open])).toEqual([
      [1, "Not broken down", true],
      [2, "Skipped", false],
      [3, "4 regions", true],
      [4, "No stage time", false],
      [5, "1 region", true],
      [6, "No shots", true],
    ]);
  });
});
