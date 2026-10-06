import { describe, expect, it } from "vitest";

import type { MatchProject } from "@/lib/api";
import { damagedTakes, takeDamageText } from "@/lib/takeDamage";

const TAKE = "raw/take.mp4";
const entry = (n: number) => ({ path: TAKE, role: "primary", beep_time: n * 100 });

function project(stageVideos: Record<number, unknown[]>, covers = [1, 2, 3]): MatchProject {
  return {
    stages: [1, 2, 3].map((n) => ({ stage_number: n, videos: stageVideos[n] ?? [] })),
    unassigned_videos: [],
    raw_videos: [{ storage_path: TAKE, original_filename: "take.mp4", covers_stages: covers }],
  } as unknown as MatchProject;
}

describe("damagedTakes", () => {
  it("a healthy take has no damage", () => {
    expect(damagedTakes(project({ 1: [entry(1)], 2: [entry(2)], 3: [entry(3)] }))).toEqual([]);
  });

  it("flags a stage listing the file twice and the stage left without it", () => {
    expect(damagedTakes(project({ 1: [], 2: [entry(2), entry(1)], 3: [entry(3)] }))).toEqual([
      { storagePath: TAKE, filename: "take.mp4", stages: [1, 2] },
    ]);
  });

  it("ignores stages the take does not cover", () => {
    expect(damagedTakes(project({ 1: [entry(1)], 2: [entry(2)], 3: [] }, [1, 2]))).toEqual([]);
  });

  it("ignores a file that is not a take", () => {
    const p = project({ 2: [entry(2), entry(1)] });
    (p as unknown as { raw_videos: unknown[] }).raw_videos = [];
    expect(damagedTakes(p)).toEqual([]);
  });
});

describe("takeDamageText", () => {
  it("names the file and the stages as ordinals", () => {
    expect(takeDamageText({ storagePath: TAKE, filename: "take.mp4", stages: [1, 2] })).toBe(
      "take.mp4 is registered wrongly on stages 01 and 02.",
    );
    expect(takeDamageText({ storagePath: TAKE, filename: "take.mp4", stages: [3] })).toBe(
      "take.mp4 is registered wrongly on stage 03.",
    );
    expect(takeDamageText({ storagePath: TAKE, filename: "t.mp4", stages: [1, 2, 4] })).toBe(
      "t.mp4 is registered wrongly on stages 01, 02 and 04.",
    );
  });
});
