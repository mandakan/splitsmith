import { describe, expect, it } from "vitest";

import type { Job, MatchProject, ShooterListEntry, StageVideo } from "@/lib/api";
import { beepState, buildFootageRows, footageStats, shortName, unassignedVideos } from "@/lib/footage";

function video(over: Partial<StageVideo>): StageVideo {
  return { path: "raw/x.mp4", role: "primary", beep_time: 5.32, beep_reviewed: true, match_timestamp: null, ...over } as StageVideo;
}
function project(stages: { n: number; name: string; videos?: StageVideo[] }[], unassigned: StageVideo[] = []): MatchProject {
  return {
    stages: stages.map((s) => ({ stage_number: s.n, stage_name: s.name, videos: s.videos ?? [], placeholder: false })),
    unassigned_videos: unassigned,
  } as unknown as MatchProject;
}
function shooter(slug: string, name: string): ShooterListEntry {
  return { slug, name } as ShooterListEntry;
}

const ME = shooter("me", "Mathias");
const ANNA = shooter("anna", "Anna");

describe("buildFootageRows", () => {
  it("one row per stage across shooters, a cell per shooter with the primary's beep", () => {
    const projects = {
      me: project([
        { n: 1, name: "S1", videos: [video({}), video({ path: "raw/y.mp4", role: "secondary" })] },
        { n: 2, name: "S2" },
      ]),
      anna: project([{ n: 1, name: "S1", videos: [video({ path: "raw/a.mp4", beep_reviewed: false, beep_time: 4.9 })] }]),
    };
    const rows = buildFootageRows({ projects, shooters: [ME, ANNA], jobs: [] });
    expect(rows.map((r) => [r.stageNumber, r.covered])).toEqual([
      [1, true],
      [2, false],
    ]);
    expect(rows[0].cells[0].videos).toHaveLength(2);
    expect(rows[0].cells[0].primary?.path).toBe("raw/x.mp4");
    expect(rows[0].cells[1].beep).toEqual({ time: 4.9, reviewed: false, detecting: false });
    expect(rows[1].cells[1].videos).toEqual([]);
  });

  it("skips shooters without a project and flags a running beep detection", () => {
    const jobs = [{ kind: "detect_beep", status: "running", shooter_slug: "me", stage_number: 1 }] as unknown as Job[];
    const rows = buildFootageRows({ projects: { me: project([{ n: 1, name: "S1", videos: [video({ beep_time: null })] }]), anna: null }, shooters: [ME, ANNA], jobs });
    expect(rows[0].cells).toHaveLength(1);
    expect(rows[0].cells[0].beep.detecting).toBe(true);
  });
});

describe("unassignedVideos and footageStats", () => {
  it("orders by capture time within a shooter, timestamp-less last, and counts every video", () => {
    const projects = {
      me: project([{ n: 1, name: "S1", videos: [video({})] }], [
        video({ path: "raw/u2.mp4", match_timestamp: null }),
        video({ path: "raw/u1.mp4", match_timestamp: "2026-06-27T14:03:00" }),
      ]),
    };
    const un = unassignedVideos({ projects, shooters: [ME] });
    expect(un.map((u) => u.video.path)).toEqual(["raw/u1.mp4", "raw/u2.mp4"]);
    const rows = buildFootageRows({ projects, shooters: [ME], jobs: [] });
    expect(footageStats(rows, un, 1)).toEqual({ shooters: 1, videos: 3, covered: 1, total: 1, unassigned: 2 });
  });
});

describe("beepState / shortName", () => {
  const cell = (over: Partial<StageVideo> | null, detecting = false) => {
    const primary = over ? video(over) : null;
    return {
      slug: "me",
      shooterName: "M",
      videos: primary ? [primary] : [],
      primary,
      beep: { time: primary?.beep_time ?? null, reviewed: primary?.beep_reviewed ?? false, detecting },
    };
  };
  it("reads the four states", () => {
    expect(beepState(cell(null))).toEqual({ label: "—", tone: "none", confirmable: false });
    expect(beepState(cell({}, true)).label).toBe("detecting…");
    expect(beepState(cell({ beep_time: null }))).toMatchObject({ label: "no beep", tone: "warn" });
    expect(beepState(cell({ beep_time: 4.9, beep_reviewed: false }))).toEqual({ label: "4.90 · unconfirmed", tone: "warn", confirmable: true });
    expect(beepState(cell({}))).toEqual({ label: "5.32", tone: "ok", confirmable: false });
  });
  it("shortens long stems only", () => {
    expect(shortName("raw/VID_20260627_1403.MP4")).toBe("VID_…1403.MP4");
    expect(shortName("raw/demo.mp4")).toBe("demo.mp4");
  });
});
