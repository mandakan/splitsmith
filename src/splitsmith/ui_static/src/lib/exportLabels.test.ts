import { describe, expect, it } from "vitest";

import { matchExportLabel } from "./exportLabels";

describe("matchExportLabel", () => {
  it("names each match deliverable by its extension", () => {
    expect(matchExportLabel("bromma-2026-match.fcpxml")).toBe("Match FCPXML");
    expect(matchExportLabel("bromma-2026-match.xml")).toBe("Match FCP7 XML");
    expect(matchExportLabel("bromma-2026-match.mp4")).toBe("Match video");
    expect(matchExportLabel("bromma-2026-match.srt")).toBe("Match subtitles");
    expect(matchExportLabel("bromma-2026-match.json")).toBe("Match YouTube sidecar");
  });

  it("is case-insensitive about the extension", () => {
    expect(matchExportLabel("BROMMA-MATCH.FCPXML")).toBe("Match FCPXML");
  });

  it("falls back to the filename rather than a generic word", () => {
    // Two unrecognised deliverables would otherwise both read "Match
    // export", leaving the user unable to tell the rows apart.
    expect(matchExportLabel("bromma-2026-match.edl")).toBe("bromma-2026-match.edl");
    expect(matchExportLabel("noextension")).toBe("noextension");
    expect(matchExportLabel(".hidden")).toBe(".hidden");
  });
});
