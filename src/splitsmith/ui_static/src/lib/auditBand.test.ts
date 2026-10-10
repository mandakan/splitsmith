import { describe, expect, it } from "vitest";

import { LEGEND, bandPipelineAction, bandReadouts } from "./auditBand";

describe("bandPipelineAction", () => {
  it("offers nothing before the peaks load", () => {
    expect(bandPipelineAction(null)).toBeNull();
  });

  it("offers a trim on an untrimmed clip and detection on a trimmed one", () => {
    expect(bandPipelineAction({ trimmed: false })).toBe("trim");
    expect(bandPipelineAction({ trimmed: true })).toBe("detect");
  });
});

describe("bandReadouts", () => {
  it("names the peak count and the clip length", () => {
    expect(bandReadouts({ peakCount: 1500, duration: 44.6871, cameras: 1 })).toEqual(["1500 peaks · 44.69 s"]);
  });

  it("adds the linked cameras line only with more than one camera", () => {
    expect(bandReadouts({ peakCount: 10, duration: 2, cameras: 2 })).toEqual([
      "10 peaks · 2.00 s",
      "All 2 cameras linked",
    ]);
  });
});

describe("LEGEND", () => {
  it("keeps the band's seven marker kinds in order", () => {
    expect(LEGEND.map((e) => e.label)).toEqual(["Beep", "Timer stop", "Shot", "Manual", "Rejected", "Flag", "Current"]);
  });
});
