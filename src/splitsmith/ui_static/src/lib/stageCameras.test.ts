import { describe, expect, it } from "vitest";

import type { CoachVideoEntry } from "@/lib/api";
import type { InsetStreamKind } from "@/lib/pip";
import { bigToPrimary, primaryToBig, stageCameras } from "@/lib/stageCameras";

const url = (v: CoachVideoEntry, kind: InsetStreamKind, version: string | null) => `${kind}:${v.path}:${version ?? ""}`;

const PRIMARY: CoachVideoEntry = { path: "p.mp4", role: "primary", beep_in_clip: 5, kind: "trim", trim_version: "t1", scrub_version: "s1" };
const CAM2: CoachVideoEntry = { path: "c2.mp4", role: "secondary", beep_in_clip: 3, kind: "trim", trim_version: "t2" };
const SOURCE: CoachVideoEntry = { path: "c3.mov", role: "secondary", beep_in_clip: 12, kind: "source" };
const NO_BEEP: CoachVideoEntry = { path: "c4.mp4", role: "secondary", beep_in_clip: null, kind: "trim" };

describe("stageCameras", () => {
  it("puts the primary first whatever the payload order and labels in that order", () => {
    const cams = stageCameras([CAM2, PRIMARY, SOURCE], url);
    expect(cams.map((c) => [c.id, c.label, c.primary])).toEqual([
      ["p.mp4", "Cam 1", true],
      ["c2.mp4", "Cam 2", false],
      ["c3.mov", "Cam 3", false],
    ]);
    expect(cams.map((c) => c.beepInClip)).toEqual([5, 3, 12]);
  });

  it("streams the 720p rendition when there is one, else the trim, and a source as pinned", () => {
    const cams = stageCameras([PRIMARY, CAM2, SOURCE], url);
    expect(cams.map((c) => [c.src, c.insetKind])).toEqual([
      ["scrub:p.mp4:s1", "scrub"],
      ["trim:c2.mp4:t2", "trim"],
      ["source:c3.mov:", "source"],
    ]);
  });

  it("falls back past the kinds that failed and is unavailable when none is left", () => {
    const cams = stageCameras([PRIMARY, SOURCE], url, {
      "p.mp4": new Set<InsetStreamKind>(["scrub"]),
      "c3.mov": new Set<InsetStreamKind>(["source"]),
    });
    expect(cams[0]).toMatchObject({ src: "trim:p.mp4:t1", insetKind: "trim", unavailable: false });
    expect(cams[1]).toMatchObject({ src: null, insetKind: null, unavailable: true });
  });

  it("keeps a camera without a beep (it never enters the inset: beepInClip null)", () => {
    expect(stageCameras([PRIMARY, NO_BEEP], url)[1].beepInClip).toBeNull();
  });
});

describe("primaryToBig / bigToPrimary", () => {
  it("line the clips up on their own beep and are each other's inverse", () => {
    expect(primaryToBig(7, 5, 3)).toBe(5);
    expect(bigToPrimary(5, 5, 3)).toBe(7);
    expect(primaryToBig(7, 5, null)).toBe(7);
    expect(bigToPrimary(7, null, 3)).toBe(7);
  });

  it("a moment before the big clip's start lands on its first frame, and the playhead goes there too", () => {
    // 4 s before the primary's beep is before Cam 2's clip (beep at 3 s).
    const landed = primaryToBig(1, 5, 3);
    expect(landed).toBe(0);
    expect(bigToPrimary(landed, 5, 3)).toBe(2); // 3 s before the beep, where the picture is
  });
});
