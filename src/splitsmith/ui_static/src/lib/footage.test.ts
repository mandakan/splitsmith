import { describe, expect, it } from "vitest";

import type { Job, MatchProject, ShooterListEntry, StageVideo } from "@/lib/api";
import {
  beepAction,
  buildFootageRows,
  chipBeepMark,
  footageStats,
  shortName,
  skippedSummary,
  unassignedVideos,
  videoBeep,
} from "@/lib/footage";

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

describe("beep states / shortName", () => {
  const cell = (over: Partial<StageVideo> | null, detecting = false) => {
    const primary = over ? video(over) : null;
    return {
      slug: "me",
      shooterName: "M",
      videos: primary ? [primary] : [],
      primary,
      beep: { time: primary?.beep_time ?? null, reviewed: primary?.beep_reviewed ?? false, detecting },
      beeps: primary ? { [primary.video_id]: videoBeep(primary, detecting) } : {},
    };
  };
  it("words what the primary's beep asks of the user, never a time", () => {
    expect(beepAction(cell(null))).toBeNull();
    expect(beepAction(cell({ beep_time: null }, true))).toEqual({ label: "Detecting beep…", tone: "muted", link: false });
    expect(beepAction(cell({ beep_time: null }))).toEqual({ label: "Place beep", tone: "warn", link: true });
    expect(beepAction(cell({ beep_time: 4.9, beep_reviewed: false }))).toEqual({ label: "Confirm beep", tone: "warn", link: true });
    expect(beepAction(cell({}))).toEqual({ label: "Beep confirmed", tone: "ok", link: false });
  });
  it("tells detected, uncertain and confirmed apart; a hand-placed beep is confirmed", () => {
    expect(videoBeep(video({ beep_time: 4.9, beep_reviewed: false, beep_confidence: 0.9 }), false)).toBe("detected");
    expect(videoBeep(video({ beep_time: 4.9, beep_reviewed: false, beep_confidence: 0.2 }), false)).toBe("low");
    expect(videoBeep(video({ beep_time: 4.9, beep_reviewed: true }), true)).toBe("confirmed");
    expect(videoBeep(video({ beep_time: null }), false)).toBe("missing");
  });
  it("marks a primary always and a secondary only when its beep is off", () => {
    const primary = video({});
    const secondary = video({ role: "secondary" });
    expect(chipBeepMark(primary, "detected")).toBe("detected");
    expect(chipBeepMark(secondary, "detected")).toBeNull();
    expect(chipBeepMark(secondary, "confirmed")).toBeNull();
    expect(chipBeepMark(secondary, "missing")).toBe("missing");
    expect(chipBeepMark(secondary, "low")).toBe("low");
    expect(chipBeepMark(video({ role: "ignored" }), "missing")).toBeNull();
  });
  it("counts a detect job for one file only against that file", () => {
    const jobs = [
      { kind: "detect_beep", status: "pending", shooter_slug: "me", stage_number: 1, video_id: "v2" },
    ] as unknown as Job[];
    const rows = buildFootageRows({
      projects: {
        me: project([
          {
            n: 1,
            name: "S1",
            videos: [video({ video_id: "v1", beep_time: null }), video({ video_id: "v2", role: "secondary", beep_time: null })],
          },
        ]),
      },
      shooters: [ME],
      jobs,
    });
    expect(rows[0].cells[0].beeps).toEqual({ v1: "missing", v2: "detecting" });
  });
  it("shortens long stems only", () => {
    expect(shortName("raw/VID_20260627_1403.MP4")).toBe("VID_…1403.MP4");
    expect(shortName("raw/demo.mp4")).toBe("demo.mp4");
  });
});

describe("skippedSummary", () => {
  it("names each skipped file with its reason", () => {
    expect(skippedSummary(["a.mov: already imported for Alice", "b.mov: in Bob's unassigned clips"])).toBe(
      "a.mov: already imported for Alice; b.mov: in Bob's unassigned clips",
    );
  });

  it("counts the rest past the first few", () => {
    const skipped = ["a: x", "b: x", "c: x", "d: x", "e: x"];
    expect(skippedSummary(skipped)).toBe("a: x; b: x; c: x; and 2 more");
    expect(skippedSummary(skipped.slice(0, 3))).toBe("a: x; b: x; c: x");
  });
});
