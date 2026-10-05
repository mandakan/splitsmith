/**
 * The Footage page's optimistic mirrors on a multi-stage single take: one
 * source path registered on several stages, each with its own beep. A
 * move, role change or remove on one stage's clip touches that stage's
 * registration only (the server does the same).
 */
import { describe, expect, it } from "vitest";

import type { MatchProject, StageVideo } from "@/lib/api";
import { applyAssignmentLocally, removeVideoLocally } from "@/pages/ingest/model";

const TAKE = "raw/take.mp4";

function take(): MatchProject {
  const video = (n: number): StageVideo => ({ path: TAKE, role: "primary", beep_time: n * 100 }) as StageVideo;
  return {
    stages: [1, 2, 3].map((n) => ({ stage_number: n, videos: [video(n)] })),
    unassigned_videos: [],
  } as unknown as MatchProject;
}

const beeps = (p: MatchProject) =>
  Object.fromEntries(p.stages.map((s) => [s.stage_number, (s.videos ?? []).map((v) => [v.role, v.beep_time])]));

describe("optimistic footage edits on a single take", () => {
  it("a role change touches the named stage only", () => {
    const next = applyAssignmentLocally(take(), TAKE, 2, "ignored", 2);
    expect(beeps(next)).toEqual({ 1: [["primary", 100]], 2: [["ignored", 200]], 3: [["primary", 300]] });
  });

  it("unassigning the named stage sends that registration to the tray", () => {
    const next = applyAssignmentLocally(take(), TAKE, null, "secondary", 3);
    expect(beeps(next)).toEqual({ 1: [["primary", 100]], 2: [["primary", 200]], 3: [] });
    expect((next.unassigned_videos ?? []).map((v) => v.beep_time)).toEqual([300]);
  });

  it("removing the named stage leaves the rest of the take", () => {
    const next = removeVideoLocally(take(), TAKE, 3);
    expect(beeps(next)).toEqual({ 1: [["primary", 100]], 2: [["primary", 200]], 3: [] });
  });

  it("a tray item still moves when no stage is named", () => {
    const tray = { path: "raw/loose.mp4", role: "secondary", beep_time: null } as unknown as StageVideo;
    const project = { ...take(), unassigned_videos: [tray] } as MatchProject;
    const next = applyAssignmentLocally(project, "raw/loose.mp4", 1, "secondary", null);
    expect(next.unassigned_videos).toEqual([]);
    expect(next.stages[0].videos?.map((v) => v.path)).toEqual([TAKE, "raw/loose.mp4"]);
  });
});
