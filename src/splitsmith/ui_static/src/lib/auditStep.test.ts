import { describe, expect, it } from "vitest";

import type { AuditMarker } from "@/components/MarkerLayer";
import type { Anomaly } from "@/lib/anomalies";
import type { StageEntry, StageVideo } from "@/lib/api";
import { beepStepVideos, headerState, nextFlaggedIndex, shotRows } from "@/lib/auditStep";

function video(over: Partial<StageVideo>): StageVideo {
  return {
    path: "raw/x.mp4",
    role: "primary",
    beep_time: 5.32,
    beep_reviewed: true,
    beep_confidence: 0.91,
    camera_mount: null,
    ...over,
  } as StageVideo;
}

function stage(videos: StageVideo[]): StageEntry {
  return { stage_number: 3, stage_name: "B6 Rear", videos } as StageEntry;
}

function marker(id: string, time: number, kind: AuditMarker["kind"] = "detected"): AuditMarker {
  return { id, kind, time, candidateNumber: null, confidence: 0.8, peakAmplitude: null, note: "" };
}

const FLAG: Anomaly = { kind: "long_pause", severity: "warn", message: "missed shot?", shot_number: 2, time: 8.0 };

describe("beepStepVideos", () => {
  it("is empty for a reviewed primary with no secondaries", () => {
    expect(beepStepVideos(stage([video({})]))).toEqual([]);
  });
  it("lists the primary when unreviewed or beepless", () => {
    expect(beepStepVideos(stage([video({ beep_reviewed: false })]))).toHaveLength(1);
    expect(beepStepVideos(stage([video({ beep_time: null, beep_reviewed: false })]))).toHaveLength(1);
  });
  it("lists an unreviewed secondary after the primary, never an ignored one", () => {
    const sec = video({ path: "raw/y.mp4", role: "secondary", beep_reviewed: false });
    const ign = video({ path: "raw/z.mp4", role: "ignored", beep_reviewed: false });
    const out = beepStepVideos(stage([video({ beep_reviewed: false }), sec, ign]));
    expect(out.map((v) => v.path)).toEqual(["raw/x.mp4", "raw/y.mp4"]);
  });
});

describe("shotRows", () => {
  const markers = [marker("c", 12.0), marker("b", 8.0), marker("r", 6.0, "rejected"), marker("a", 5.0)];
  it("orders by time, numbers kept shots, leaves rejected unnumbered and unflagged", () => {
    const { all } = shotRows(markers, [FLAG]);
    expect(all.map((r) => [r.marker.id, r.index, r.rejected])).toEqual([
      ["a", 1, false],
      ["r", null, true],
      ["b", 2, false],
      ["c", 3, false],
    ]);
  });
  it("puts flagged shots first, matched by time", () => {
    const { flagged } = shotRows(markers, [FLAG]);
    expect(flagged.map((r) => r.marker.id)).toEqual(["b"]);
    expect(flagged[0].flag).toBe("missed shot?");
    expect(flagged[0].index).toBe(2);
  });
});

describe("nextFlaggedIndex", () => {
  const rows = shotRows([marker("a", 5), marker("b", 8), marker("c", 12), marker("d", 15)], [
    FLAG,
    { ...FLAG, time: 15, shot_number: 4 },
  ]).all;
  it("finds the next flagged kept shot after the current one and wraps", () => {
    expect(nextFlaggedIndex(rows, 0)).toBe(1);
    expect(nextFlaggedIndex(rows, 1)).toBe(3);
    expect(nextFlaggedIndex(rows, 3)).toBe(1);
  });
  it("is null with no flags", () => {
    expect(nextFlaggedIndex(shotRows([marker("a", 5)], []).all, 0)).toBeNull();
  });
});

describe("headerState", () => {
  it("reads the three beep states and the camera mount", () => {
    expect(headerState({ primary: video({}), keptCount: 30, flagCount: 2 })).toEqual({
      camera: "Head cam",
      beep: { label: "Beep 5.32 · confirmed", tone: "neutral", tick: "movement" },
      shots: "30 shots",
      flags: "2 flags",
    });
    expect(headerState({ primary: video({ beep_reviewed: false, beep_confidence: 0.42, camera_mount: "chest" }), keptCount: 0, flagCount: 0 })).toMatchObject({
      camera: "Chest cam",
      beep: { label: "Beep 5.32 · unconfirmed · 0.42", tone: "warn" },
      shots: "no shots yet",
      flags: null,
    });
    expect(headerState({ primary: video({ beep_time: null }), keptCount: 1, flagCount: 1 })).toMatchObject({
      beep: { label: "No beep", tone: "warn" },
      shots: "1 shot",
      flags: "1 flag",
    });
  });
});
