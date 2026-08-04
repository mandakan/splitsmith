import { describe, expect, it } from "vitest";

import {
  CANVAS_CHOICES,
  buildCompareGridPayload,
  summarizeGridResult,
} from "@/pages/matchExportModel";

describe("buildCompareGridPayload", () => {
  it("carries the selection and the chosen canvas", () => {
    const payload = buildCompareGridPayload({
      stageNumbers: [3, 1, 2],
      audioFrom: "mathias",
      canvas: CANVAS_CHOICES[0],
      outputName: "bromma-grid",
    });

    expect(payload.stage_numbers).toEqual([1, 2, 3]);
    expect(payload.audio_from).toBe("mathias");
    expect(payload.canvas_width).toBe(3840);
    expect(payload.canvas_height).toBe(2160);
    expect(payload.output_name).toBe("bromma-grid");
  });

  it("defaults to 4K UHD as the first canvas choice", () => {
    expect(CANVAS_CHOICES[0].width).toBe(3840);
    expect(CANVAS_CHOICES[0].height).toBe(2160);
  });
});

describe("summarizeGridResult", () => {
  it("reports a clean render without a partial warning", () => {
    const summary = summarizeGridResult({
      output_path: "/m/exports/compare-grid.mp4",
      stages_rendered: 2,
      stages_total: 2,
      failed: [],
    });

    expect(summary.partial).toBe(false);
    expect(summary.failedStages).toEqual([]);
    expect(summary.headline).toContain("2");
  });

  it("names the failed stages without calling the whole render a failure", () => {
    const summary = summarizeGridResult({
      output_path: "/m/exports/compare-grid.mp4",
      stages_rendered: 1,
      stages_total: 2,
      failed: [{ stage_number: 2, stage_name: "Stage 2", error: "boom" }],
    });

    expect(summary.partial).toBe(true);
    expect(summary.failedStages).toEqual(["Stage 2"]);
    expect(summary.headline).toContain("1 of 2");
  });
});
