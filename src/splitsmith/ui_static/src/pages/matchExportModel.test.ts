import { describe, expect, it } from "vitest";

import { DEFAULT_RENDER_OPTIONS } from "@/lib/renderOptions";
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

  it("sends no card field until a card is on, so an untouched panel leaves the body as it was", () => {
    const base = { stageNumbers: [1], audioFrom: "mathias", canvas: CANVAS_CHOICES[1], outputName: "g" };
    expect(buildCompareGridPayload({ ...base, render: DEFAULT_RENDER_OPTIONS })).toEqual(
      buildCompareGridPayload(base),
    );
    const on = buildCompareGridPayload({
      ...base,
      render: { ...DEFAULT_RENDER_OPTIONS, titlePage: true, titleInfo: " L2 " },
    });
    expect(on).toMatchObject({ title_page: true, title_info: "L2", closing_card: false, stage_titles: "none" });
    expect("summary_hold_seconds" in on).toBe(false);
  });

  it("sends the overlay, and the hold only with it, since the server refuses a hold alone", () => {
    const base = { stageNumbers: [1], audioFrom: "mathias", canvas: CANVAS_CHOICES[1], outputName: "g" };
    expect("overlay" in buildCompareGridPayload({ ...base, overlay: false, summaryHoldSeconds: 3 })).toBe(false);
    expect(buildCompareGridPayload({ ...base, overlay: true, summaryHoldSeconds: 3 })).toMatchObject({
      overlay: true,
      summary_hold_seconds: 3,
    });
    const noHold = buildCompareGridPayload({ ...base, overlay: true, summaryHoldSeconds: 0 });
    expect(noHold.overlay).toBe(true);
    expect("summary_hold_seconds" in noHold).toBe(false);
    expect(buildCompareGridPayload({ ...base, overlay: true, summaryHoldSeconds: Number.NaN }).summary_hold_seconds).toBeUndefined();
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
      skipped_stages: [],
      missing_trims: [],
    });

    expect(summary.partial).toBe(false);
    expect(summary.failedStages).toEqual([]);
    expect(summary.skippedStages).toEqual([]);
    expect(summary.missingTrims).toEqual([]);
    expect(summary.headline).toContain("2");
  });

  it("never calls a short render a complete success", () => {
    // The endpoint counts stages_total against what was requested, so a
    // stage nobody had a trim for shows up as a shortfall here. Reading
    // "Rendered all 2 stages" after asking for three is the defect.
    const summary = summarizeGridResult({
      output_path: "/m/exports/compare-grid.mp4",
      stages_rendered: 2,
      stages_total: 3,
      failed: [],
      skipped_stages: [3],
      missing_trims: [],
    });

    expect(summary.partial).toBe(true);
    expect(summary.headline).toContain("2 of 3");
    expect(summary.headline).not.toMatch(/all/i);
    expect(summary.skippedStages).toEqual([3]);
  });

  it("names the shooter and stage behind every missing trim", () => {
    const summary = summarizeGridResult({
      output_path: "/m/exports/compare-grid.mp4",
      stages_rendered: 2,
      stages_total: 2,
      failed: [],
      skipped_stages: [],
      missing_trims: [
        {
          shooter: "Anna",
          stage_number: 2,
          stage_name: "El Presidente",
          expected_path: "/m/anna/exports/stage2_el-presidente_trimmed.mp4",
          camera: null,
        },
      ],
    });

    // A black cell with no explanation looks exactly like a shooter who
    // skipped the stage, so the warning stands even when every selected
    // stage rendered.
    expect(summary.partial).toBe(true);
    expect(summary.missingTrims).toEqual([
      "Anna has no trim for stage 2 (El Presidente)",
    ]);
  });

  it("tolerates a result payload without the newer fields", () => {
    const summary = summarizeGridResult({
      output_path: "/m/exports/compare-grid.mp4",
      stages_rendered: 1,
      stages_total: 1,
      failed: [],
    });

    expect(summary.partial).toBe(false);
    expect(summary.skippedStages).toEqual([]);
    expect(summary.missingTrims).toEqual([]);
  });

  it("names the failed stages without calling the whole render a failure", () => {
    const summary = summarizeGridResult({
      output_path: "/m/exports/compare-grid.mp4",
      stages_rendered: 1,
      stages_total: 2,
      failed: [{ stage_number: 2, stage_name: "Stage 2", error: "boom" }],
      skipped_stages: [],
      missing_trims: [],
    });

    expect(summary.partial).toBe(true);
    expect(summary.failedStages).toEqual(["Stage 2"]);
    expect(summary.headline).toContain("1 of 2");
  });
});
