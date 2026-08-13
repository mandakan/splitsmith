import { describe, expect, it } from "vitest";

import { hostedDownloads } from "./exportDownloads";

const stage = (over: Record<string, unknown> = {}) =>
  ({
    stage_number: 1,
    stage_name: "Stage 1",
    lossless_trim_present: true,
    trimmed_video_path: "/p/exports/stage1_stage-1_trimmed.mp4",
    overlay_path: null,
    secondaries: [],
    ...over,
  }) as never;

const matchFile = (filename: string) => ({ filename, last_export_at: null });

describe("hostedDownloads (#629)", () => {
  it("offers the match output and the selected stages' media", () => {
    const out = hostedDownloads({
      matchExports: [matchFile("bromma-2026-match.fcpxml")],
      stages: [stage()],
      selection: [1],
    });
    expect(out).toEqual([
      { label: "Match FCPXML", filename: "bromma-2026-match.fcpxml" },
      { label: "Stage 1 trim", filename: "stage1_stage-1_trimmed.mp4" },
    ]);
  });

  it("still lists everything when no export ran in this session", () => {
    // The regression. This derivation used to be gated on the export
    // job's in-session `result`, so a reload emptied it -- including the
    // per-stage links, which never depended on `result` at all. There is
    // no session-scoped input left to gate on, which is the fix.
    const out = hostedDownloads({
      matchExports: [matchFile("bromma-2026-match.fcpxml")],
      stages: [stage()],
      selection: [1],
    });
    expect(out.length).toBe(2);
    expect(out.map((d) => d.filename)).toContain("bromma-2026-match.fcpxml");
  });

  it("lists the match output even before any stage is selected", () => {
    // Selection filters per-stage media only. A user landing on the page
    // with nothing ticked must still be able to fetch the match bundle.
    const out = hostedDownloads({
      matchExports: [matchFile("bromma-2026-match.fcpxml"), matchFile("bromma-2026-match.srt")],
      stages: [stage()],
      selection: [],
    });
    expect(out.map((d) => d.filename)).toEqual([
      "bromma-2026-match.fcpxml",
      "bromma-2026-match.srt",
    ]);
  });

  it("skips a stage whose trim is only the scrub cache", () => {
    // trimmed_video_path also points at the short-GOP audit copy when no
    // lossless trim exists; offering that would hand over a file the user
    // never asked for.
    const out = hostedDownloads({
      matchExports: [],
      stages: [stage({ lossless_trim_present: false })],
      selection: [1],
    });
    expect(out).toEqual([]);
  });

  it("includes overlays and per-camera secondary trims", () => {
    const out = hostedDownloads({
      matchExports: [],
      stages: [
        stage({
          overlay_path: "/p/exports/stage1_stage-1_overlay.mov",
          secondaries: [
            { label: "Cam B", trim_present: true, trim_path: "/p/exports/stage1_cam_b_trimmed.mp4" },
            { label: "Cam C", trim_present: false, trim_path: null },
          ],
        }),
      ],
      selection: [1],
    });
    expect(out.map((d) => d.label)).toEqual([
      "Stage 1 trim",
      "Stage 1 overlay",
      "Stage 1 Cam B",
    ]);
  });

  it("omits stages the user has not selected", () => {
    const out = hostedDownloads({
      matchExports: [],
      stages: [stage(), stage({ stage_number: 2 })],
      selection: [2],
    });
    expect(out.map((d) => d.label)).toEqual(["Stage 2 trim"]);
  });
});
