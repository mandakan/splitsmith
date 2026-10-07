import { describe, expect, it } from "vitest";

import { camExportFields, DEFAULT_CAM_OPTIONS } from "@/lib/camOptions";
import { DEFAULT_EXPORT_SETTINGS as S } from "@/lib/exportPresets";
import { visibleTransitionKind } from "@/lib/lookGallery";
import { DEFAULT_RENDER_OPTIONS, clampSeconds, matchExportFields } from "@/lib/renderOptions";
import { DEFAULT_UPLOAD_OPTIONS, rowUploadOptions } from "@/lib/youtubeRows";
import {
  CANVAS_CHOICES,
  buildCompareGridPayload,
  buildMatchExportPayload,
  gridFreeCells,
  summarizeGridResult,
  type MatchExportPayloadInput,
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

  it("sends the tiles' inset only when one is chosen", () => {
    const base = { stageNumbers: [1], audioFrom: "mathias", canvas: CANVAS_CHOICES[1], outputName: "g" };
    expect(buildCompareGridPayload({ ...base, cams: DEFAULT_CAM_OPTIONS })).toEqual(buildCompareGridPayload(base));
    expect(
      buildCompareGridPayload({
        ...base,
        cams: { ...DEFAULT_CAM_OPTIONS, insetCamera: "head", insetCorner: "top-left", insetSize: "large" },
      }),
    ).toMatchObject({ inset_camera: "head", inset_corner: "top-left", inset_size: "large" });
  });

  it("sends the free square only when it is not black", () => {
    const base = { stageNumbers: [1], audioFrom: "mathias", canvas: CANVAS_CHOICES[1], outputName: "g" };
    expect(buildCompareGridPayload({ ...base, freeCell: "blank" })).toEqual(buildCompareGridPayload(base));
    expect(buildCompareGridPayload({ ...base, freeCell: "splits" })).toMatchObject({ free_cell: "splits" });
  });

  it("publishes like one shooter: the sidecar with YouTube on, the upload only when on and connected", () => {
    const base = { stageNumbers: [1], audioFrom: "mathias", canvas: CANVAS_CHOICES[1], outputName: "g" };
    const off = buildCompareGridPayload({ ...base, youtube: false, descriptionLead: "x" });
    expect(off.youtube_sidecar).toBeUndefined();
    expect(off.youtube_upload).toBeUndefined();

    const sidecarOnly = buildCompareGridPayload({
      ...base,
      youtube: true,
      descriptionLead: "  Every stage.  ",
      uploadOptions: { ...DEFAULT_UPLOAD_OPTIONS, enabled: true },
      youtubeConnected: false,
    });
    expect(sidecarOnly).toMatchObject({ youtube_sidecar: true, description_lead: "Every stage." });
    expect(sidecarOnly.youtube_upload).toBeUndefined();

    const upload = buildCompareGridPayload({
      ...base,
      youtube: true,
      descriptionLead: "",
      uploadOptions: { ...DEFAULT_UPLOAD_OPTIONS, enabled: true, privacy: "private" },
      youtubeConnected: true,
    });
    expect(upload).toMatchObject({
      youtube_sidecar: true,
      description_lead: null,
      youtube_upload: true,
      youtube_privacy: "private",
    });
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

describe("gridFreeCells", () => {
  it("counts the cells no shooter fills, by the server's choose_grid", () => {
    expect([1, 2, 3, 4, 5, 8, 9, 10].map(gridFreeCells)).toEqual([0, 0, 1, 0, 4, 1, 0, 6]);
  });
});

describe("buildMatchExportPayload", () => {
  const base: MatchExportPayloadInput = {
    stageNumbers: [1, 2],
    headPad: S.headPad,
    tailPad: S.tailPad,
    camOptions: S.camOptions,
    outputFormat: "mp4",
    transitionKind: S.transitionKind,
    transitionSeconds: S.transitionSeconds,
    renderOptions: S.renderOptions,
    youtube: true,
    descriptionLead: "",
    uploadOptions: { ...S.uploadOptions, enabled: true, playlistId: "PL1", playlist: "Match" },
    includeOverlay: S.includeOverlay,
    overlayCodec: S.overlayCodec,
    projectName: "Match",
    uploadTarget: "desk",
    youtubeConnected: true,
  };

  it("uploads only with a connected channel on the desk", () => {
    expect(buildMatchExportPayload({ ...base, uploadTarget: "desk", youtubeConnected: false }).youtube_upload).toBe(
      false,
    );
    expect(buildMatchExportPayload({ ...base, uploadTarget: "desk", youtubeConnected: true }).youtube_upload).toBe(
      true,
    );
  });

  it("always renders an MP4 that uploads, on the desktop", () => {
    const p = buildMatchExportPayload({ ...base, outputFormat: "fcpxml", uploadTarget: "desktop", youtubeConnected: false });
    expect(p.output_format).toBe("mp4");
    expect(p.youtube_sidecar).toBe(true);
    expect(p.youtube_upload).toBe(true);
    expect(p.youtube_playlist_id).toBeNull();
  });

  // Export.tsx's submitBundle used to build this object inline. Reconstructed
  // here from the same pure helpers it called (never from the builder under
  // test) so this test proves the refactor changed nothing for the desk.
  it("matches, field by field, what submitBundle sent to the desk before this refactor", () => {
    const renderedMp4 = base.outputFormat === "mp4"; // submitBundle only ever ran with mode === "single"
    const upload = rowUploadOptions(base.uploadOptions);
    const expected = {
      stage_numbers: base.stageNumbers,
      head_pad_seconds: base.headPad,
      tail_pad_seconds: base.tailPad,
      ...camExportFields(base.camOptions),
      output_format: base.outputFormat,
      transition_kind: visibleTransitionKind(base.transitionKind, base.outputFormat),
      transition_duration_seconds: clampSeconds(base.transitionSeconds, 0.1),
      ...matchExportFields(base.renderOptions, base.outputFormat),
      intro_path: undefined,
      outro_path: undefined,
      youtube_sidecar: renderedMp4 && base.youtube,
      description_lead: renderedMp4 && base.youtube ? base.descriptionLead.trim() || null : undefined,
      youtube_preset: renderedMp4 && base.youtube,
      youtube_upload: renderedMp4 && base.youtube && !!base.youtubeConnected && base.uploadOptions.enabled,
      youtube_privacy: upload.privacy,
      youtube_playlist: upload.playlist,
      youtube_playlist_id: upload.playlist_id,
      youtube_publish_at: upload.publish_at,
      youtube_notify_subscribers: upload.notify_subscribers,
      include_overlay: base.includeOverlay,
      overlay_codec: base.overlayCodec,
      overlay_max_height: null,
      overlay_max_fps: null,
      project_name: base.projectName,
    };

    expect(buildMatchExportPayload(base)).toEqual(expected);
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

describe("buildCompareGridPayload transitions (#1244)", () => {
  const base = { stageNumbers: [1, 2], audioFrom: "me", canvas: { width: 1920, height: 1080 } as never, outputName: "g" };
  it("sends the kind and its seconds when one is chosen, nothing otherwise", () => {
    const faded = buildCompareGridPayload({ ...base, transitionKind: "fade", transitionSeconds: 1 });
    expect(faded.transition_kind).toBe("fade");
    expect(faded.transition_duration_seconds).toBe(1);
    const cut = buildCompareGridPayload(base);
    expect("transition_kind" in cut).toBe(false);
    expect("transition_duration_seconds" in cut).toBe(false);
    const hidden = buildCompareGridPayload({ ...base, transitionKind: "zoom", transitionSeconds: 1 });
    expect("transition_kind" in hidden).toBe(false);
  });
});


describe("the Look on the payloads (#1246)", () => {
  const look = { look: "clean", titlePageVariant: "rise", stageCardVariant: "rise", closingCardVariant: "default" };

  it("the match export carries overlay_theme and the variants its format draws", () => {
    const base: MatchExportPayloadInput = {
      stageNumbers: [1],
      headPad: S.headPad,
      tailPad: S.tailPad,
      camOptions: S.camOptions,
      outputFormat: "mp4",
      transitionKind: S.transitionKind,
      transitionSeconds: S.transitionSeconds,
      renderOptions: S.renderOptions,
      youtube: false,
      descriptionLead: "",
      uploadOptions: S.uploadOptions,
      includeOverlay: false,
      overlayCodec: S.overlayCodec,
      projectName: "M",
      uploadTarget: "desk",
      youtubeConnected: false,
    };
    const payload = buildMatchExportPayload({ ...base, look });
    expect(payload.overlay_theme).toBe("clean");
    expect(payload.title_page_variant).toBe("rise");
    expect(payload.stage_card_variant).toBe("rise");
    expect(payload.closing_card_variant).toBe("default");
    expect(buildMatchExportPayload(base)).not.toHaveProperty("overlay_theme");
    const xml = buildMatchExportPayload({ ...base, look, outputFormat: "fcp7xml" });
    expect(xml.overlay_theme).toBe("clean");
    expect(xml).not.toHaveProperty("stage_card_variant");
  });

  it("the grid carries overlay_theme with the choice and the variants only with a card on", () => {
    const base = { stageNumbers: [1], audioFrom: "a", canvas: CANVAS_CHOICES[0], outputName: "g" };
    const plain = buildCompareGridPayload({ ...base, look });
    expect(plain.overlay_theme).toBe("clean");
    expect(plain).not.toHaveProperty("title_page_variant");
    const carded = buildCompareGridPayload({ ...base, look, render: { ...DEFAULT_RENDER_OPTIONS, titlePage: true } });
    expect(carded.title_page_variant).toBe("rise");
    expect(buildCompareGridPayload(base)).not.toHaveProperty("overlay_theme");
  });
});
