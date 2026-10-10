import { describe, expect, it } from "vitest";

import type { StageVideo } from "@/lib/api";

import { addFailedKind, auditPipCameras } from "./auditPip";

const v = (extra: Partial<StageVideo>): StageVideo =>
  ({
    video_id: "v1",
    role: "primary",
    path: "raw/a.mp4",
    beep_time: 12,
    processed: { beep: true, shot_detect: true, trim: true },
    trim_version: "t1",
    scrub_version: null,
    ...extra,
  }) as unknown as StageVideo;

const primary = v({});
// A trimmed secondary: its trim is cut around its own beep (5 s pre-buffer).
const trimmed = v({ video_id: "v2", role: "secondary", path: "raw/b.mp4", beep_time: 30, scrub_version: "s2" });
// An untrimmed secondary streams its source (proxy), beep in source seconds.
const untrimmed = v({
  video_id: "v3",
  role: "secondary",
  path: "raw/c.mp4",
  beep_time: 41,
  processed: { beep: true, shot_detect: false, trim: false },
});
const noBeep = v({ video_id: "v4", role: "secondary", path: "raw/d.mp4", beep_time: null });

const peaks = { beep_time: 5, trimmed: true };
const cams = (failed = {}) =>
  auditPipCameras({ slug: "alice", stageNumber: 3, videos: [primary, trimmed, untrimmed, noBeep], peaks, preBufferSeconds: 5, failed });

describe("auditPipCameras", () => {
  it("puts each camera's beep where its served clip has it", () => {
    const [a, b, c, d] = cams();
    expect(a.beepInClip).toBe(5); // the audit timeline
    expect(b.beepInClip).toBe(5); // min(30, pre-buffer)
    expect(c.beepInClip).toBe(41); // the source's own beep
    expect(d.beepInClip).toBeNull(); // cannot be lined up
    expect(cams().map((x) => x.label)).toEqual(["Cam 1", "Cam 2", "Cam 3", "Cam 4"]);
    expect(cams().map((x) => x.primary)).toEqual([true, false, false, false]);
  });

  it("streams the rendition when there is one, else the trim, and the proxy for an untrimmed camera", () => {
    const [a, b, c] = cams();
    expect([a.kind, b.kind, c.kind]).toEqual(["trim", "scrub", "proxy"]);
    expect(b.src).toContain("kind=scrub");
    expect(b.src).toContain("v=s2");
    expect(b.src).toContain("stage=3");
    expect(c.src).toContain("kind=proxy");
  });

  it("falls back past a failed kind and is unavailable when nothing is left", () => {
    let failed = addFailedKind({}, "v2", "scrub");
    expect(cams(failed)[1].kind).toBe("trim");
    failed = addFailedKind(failed, "v2", "trim");
    expect(cams(failed)[1]).toMatchObject({ kind: null, src: null, unavailable: true });
    failed = addFailedKind(failed, "v3", "proxy");
    expect(cams(failed)[2]).toMatchObject({ kind: null, unavailable: true });
  });

  it("lines nothing up before peaks load", () => {
    const list = auditPipCameras({ slug: "alice", stageNumber: 3, videos: [primary, trimmed], peaks: null, preBufferSeconds: 5 });
    expect(list.map((c) => c.beepInClip)).toEqual([null, null]);
  });
});

describe("addFailedKind", () => {
  it("keeps the same map for a kind it already has, or none", () => {
    const a = addFailedKind({}, "v2", "scrub");
    expect(addFailedKind(a, "v2", "scrub")).toBe(a);
    expect(addFailedKind(a, "v2", null)).toBe(a);
    expect([...addFailedKind(a, "v2", "trim").v2]).toEqual(["scrub", "trim"]);
  });
});
