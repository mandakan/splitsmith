import { describe, expect, it } from "vitest";

import type { StageEntry } from "./api";
import { camExportFields, DEFAULT_CAM_OPTIONS, syncedSecondaryCount } from "./camOptions";

function stage(n: number, videos: Array<[StageEntry["videos"][number]["role"], number | null]>) {
  return {
    stage_number: n,
    videos: videos.map(([role, beep_time], i) => ({ role, beep_time, video_id: `v${n}-${i}` })),
  } as unknown as Pick<StageEntry, "stage_number" | "videos">;
}

describe("syncedSecondaryCount", () => {
  const stages = [
    stage(1, [["primary", 5], ["secondary", 4.2], ["secondary", null]]),
    stage(2, [["primary", 5], ["secondary", 3.9]]),
    stage(3, [["primary", 5], ["ignored", 1]]),
  ];

  it("counts secondaries with a beep, never the unsynced or the ignored", () => {
    expect(syncedSecondaryCount(stages)).toBe(2);
  });

  it("narrows to the stages an export selects", () => {
    expect(syncedSecondaryCount(stages, [2, 3])).toBe(1);
    expect(syncedSecondaryCount(stages, [3])).toBe(0);
  });

  it("is zero for a single-camera project", () => {
    expect(syncedSecondaryCount([stage(1, [["primary", 5]])])).toBe(0);
  });
});

describe("camExportFields", () => {
  it("mirrors the server's defaults", () => {
    expect(camExportFields(DEFAULT_CAM_OPTIONS)).toEqual({ include_secondaries: true, pip_layout: "stacked" });
  });

  it("carries the layout even with the cams off, so it survives a toggle", () => {
    expect(camExportFields({ includeSecondaries: false, pipLayout: "pip-corners" })).toEqual({
      include_secondaries: false,
      pip_layout: "pip-corners",
    });
  });
});
