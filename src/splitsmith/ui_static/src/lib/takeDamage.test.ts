import { describe, expect, it } from "vitest";

import type { MatchProject } from "@/lib/api";
import { damagedTakes, storedFilename, takeDamageText } from "@/lib/takeDamage";

const TAKE = "raw/take.mp4";
const entry = (n: number) => ({ path: TAKE, role: "primary", beep_time: n * 100 });

function project(stageVideos: Record<number, unknown[]>, covers: number[] | null = [1, 2, 3]): MatchProject {
  return {
    stages: [1, 2, 3].map((n) => ({ stage_number: n, videos: stageVideos[n] ?? [] })),
    unassigned_videos: [],
    raw_videos: covers ? [{ storage_path: TAKE, original_filename: "GX01.MP4", covers_stages: covers }] : [],
  } as unknown as MatchProject;
}

describe("damagedTakes", () => {
  it("a healthy take has no damage", () => {
    expect(damagedTakes(project({ 1: [entry(1)], 2: [entry(2)], 3: [entry(3)] }))).toEqual([]);
  });

  it("a legitimately removed stage is not damage", () => {
    expect(damagedTakes(project({ 1: [entry(1)], 2: [entry(2)] }))).toEqual([]);
  });

  it("flags a stage listing the file twice, and names covered stages without a clip", () => {
    expect(damagedTakes(project({ 2: [entry(2), entry(1)], 3: [entry(3)] }))).toEqual([
      { storagePath: TAKE, filename: "GX01.MP4", stages: [2], unplaced: [1] },
    ]);
  });

  it("flags a duplicate even without a take record", () => {
    expect(damagedTakes(project({ 2: [entry(2), entry(1)] }, null))).toEqual([
      { storagePath: TAKE, filename: "take.mp4", stages: [2], unplaced: [] },
    ]);
  });
});

describe("takeDamageText", () => {
  it("names the file and the stage", () => {
    expect(takeDamageText({ storagePath: TAKE, filename: "take.mp4", stages: [4], unplaced: [] })).toBe(
      "take.mp4 is listed twice on stage 04.",
    );
  });

  it("adds the re-assign hint when a covered stage has no clip", () => {
    expect(takeDamageText({ storagePath: TAKE, filename: "take.mp4", stages: [2, 4], unplaced: [1, 3] })).toBe(
      "take.mp4 is listed twice on stages 02 and 04. Repair removes the extra entries; stages 01 and 03 may need their clip re-assigned.",
    );
  });

  it("repairs by the stored name", () => {
    expect(storedFilename({ storagePath: TAKE, filename: "GX01.MP4", stages: [2], unplaced: [] })).toBe("take.mp4");
  });
});
