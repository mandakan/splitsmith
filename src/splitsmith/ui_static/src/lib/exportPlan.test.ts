import { describe, expect, it } from "vitest";

import type { StageExportStatus } from "@/lib/api";
import { estimateDuration, exportRows, stageBlock, summaryLines } from "@/lib/exportPlan";

function stage(over: Partial<StageExportStatus> = {}): StageExportStatus {
  return {
    stage_number: 3,
    stage_name: "B6 Rear",
    skipped: false,
    has_primary: true,
    primary_processed: { beep: true, shot_detect: true, trim: true },
    audit_shot_count: 30,
    total_candidate_count: 119,
    audit_path: "audit.json",
    trimmed_video_path: null,
    lossless_trim_present: false,
    csv_path: null,
    fcpxml_path: null,
    report_path: null,
    overlay_path: null,
    has_exports: false,
    last_export_at: null,
    ready_to_export: true,
    ready_to_trim: true,
    source_reachable: true,
    secondaries: [],
    ...over,
  };
}

describe("stageBlock ladder", () => {
  it("passes a ready stage in every mode", () => {
    for (const mode of ["single", "trims", "compare"] as const) {
      expect(stageBlock(stage(), 32.09, mode, false)).toBeNull();
    }
  });
  it("skipped beats everything", () => {
    expect(stageBlock(stage({ skipped: true, source_reachable: false }), 10, "single", true)).toEqual({ reason: "Skipped" });
  });
  it("an unreachable source is a drive problem locally and an upload problem hosted", () => {
    const local = stageBlock(stage({ source_reachable: false }), 10, "single", false)!;
    expect(local.reason).toMatch(/source offline/i);
    expect(local.fix).toEqual({ label: "Relink", to: "relink" });
    expect(local.warn).toBe(true);
    const hosted = stageBlock(stage({ source_reachable: false }), 10, "single", true)!;
    expect(hosted.reason).toMatch(/upload missing/i);
    expect(hosted.fix).toEqual({ label: "Re-upload", to: "footage" });
    expect(`${hosted.reason} ${hosted.fix?.label}`).not.toMatch(/drive|relink|mount/i);
  });
  it("no footage, then no stage time, then the mode's own rule", () => {
    expect(stageBlock(stage({ has_primary: false, ready_to_export: false }), 10, "single", false)).toEqual({
      reason: "No footage",
      fix: { label: "Footage", to: "footage" },
    });
    expect(stageBlock(stage({ ready_to_export: false }), 0, "single", false)).toEqual({
      reason: "No stage time",
      fix: { label: "Import scores", to: "scores" },
    });
    expect(stageBlock(stage({ ready_to_export: false, audit_shot_count: 0 }), 10, "single", false)).toEqual({
      reason: "Not audited yet",
      fix: { label: "Audit", to: "audit" },
    });
  });
  it("trims mode wants a beep, not an audit; the grid only rules out skipped and unreachable stages", () => {
    const unaudited = stage({ ready_to_export: false, ready_to_trim: true, audit_shot_count: 0 });
    expect(stageBlock(unaudited, 10, "trims", false)).toBeNull();
    expect(stageBlock(stage({ ready_to_trim: false, ready_to_export: false }), 10, "trims", false)).toEqual({
      reason: "No confirmed beep",
      fix: { label: "Audit", to: "audit" },
    });
    expect(stageBlock(stage({ has_primary: false, ready_to_export: false, ready_to_trim: false }), 0, "compare", false)).toBeNull();
  });
});

describe("exportRows", () => {
  it("carries the project time and the shot count, null when absent", () => {
    const rows = exportRows(
      [stage(), stage({ stage_number: 4, audit_shot_count: 0, ready_to_export: false })],
      new Map([[3, 32.09]]),
      "single",
      false,
    );
    expect(rows[0]).toMatchObject({ time: 32.09, shots: 30, eligible: true, block: null });
    expect(rows[1]).toMatchObject({ time: null, shots: null, eligible: false });
    expect(rows[1].block?.reason).toBe("No stage time");
  });
});

describe("estimateDuration", () => {
  const times = new Map([[1, 20], [2, 30]]);
  it("adds pads, one transition between stages and a slate per stage in timeline mode", () => {
    expect(
      estimateDuration([1, 2], times, {
        mode: "single", head: 3, tail: 2, transitionKind: "cross-dissolve", transitionSeconds: 0.5, titleKind: "slate", titleSeconds: 1.5,
      }),
    ).toBeCloseTo(20 + 30 + 2 * 5 + 0.5 + 2 * 1.5, 6);
  });
  it("trims and the grid add only the project buffers", () => {
    expect(
      estimateDuration([1, 2], times, {
        mode: "trims", head: 5, tail: 5, transitionKind: "cross-dissolve", transitionSeconds: 0.5, titleKind: "slate", titleSeconds: 1.5,
      }),
    ).toBe(70);
  });
});

describe("summaryLines", () => {
  const base = {
    selected: 4, eligible: 4, head: 3, tail: 2, transitionKind: "none", transitionSeconds: 0.5, titleKind: "none", overlay: false, gridCamera: null, reference: null, canvas: null,
  };
  it("reads the timeline options with defaults dimmed", () => {
    const lines = summaryLines({ ...base, mode: "single" });
    expect(lines.map((l) => l.label)).toEqual(["Stages", "Padding", "Transitions", "Titles", "Overlay"]);
    expect(lines[2]).toEqual({ label: "Transitions", value: "cut", dim: true });
  });
  it("trims show the grid camera, the grid shows reference and canvas", () => {
    expect(summaryLines({ ...base, mode: "trims", gridCamera: "head" })[1]).toEqual({ label: "Grid camera", value: "head", dim: false });
    expect(summaryLines({ ...base, mode: "compare", reference: "Mathias", canvas: "4K UHD" }).map((l) => l.value)).toEqual(["4 / 4", "Mathias", "4K UHD"]);
  });
});
