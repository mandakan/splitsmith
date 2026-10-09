import { describe, expect, it } from "vitest";

import {
  DEFAULT_LOGO_SPOTS,
  logoPlanHelp,
  normalizeSpots,
  presetFor,
  spotsForPreset,
  toggleSpot,
} from "@/lib/logoPlan";

describe("logoPlan", () => {
  it("defaults to Polished, as the server does", () => {
    expect([...DEFAULT_LOGO_SPOTS]).toEqual(["summaries", "thumbnail", "wipe"]);
    expect(presetFor(DEFAULT_LOGO_SPOTS)).toBe("polished");
  });

  it("names the preset a set of spots is, in any order", () => {
    expect(presetFor([])).toBe("cards");
    expect(presetFor(["wipe", "thumbnail", "summaries"])).toBe("polished");
    expect(presetFor(["wipe", "summaries"])).toBe("custom");
    expect(presetFor(["wipe"])).toBe("custom");
  });

  it("drops a spot this version does not know", () => {
    expect(normalizeSpots(["wipe", "hologram", "wipe"])).toEqual(["wipe"]);
    expect(normalizeSpots(undefined)).toEqual([]);
  });

  it("switches presets and keeps the chosen spots on Choose", () => {
    expect(spotsForPreset("cards", ["wipe"])).toEqual([]);
    expect(spotsForPreset("polished", [])).toEqual(["summaries", "thumbnail", "wipe"]);
    expect(spotsForPreset("custom", ["wipe"])).toEqual(["wipe"]);
  });

  it("toggles one spot", () => {
    expect(toggleSpot(["wipe"], "summaries", true)).toEqual(["summaries", "wipe"]);
    expect(toggleSpot(["summaries", "wipe"], "wipe", false)).toEqual(["summaries"]);
  });

  it("says what the choice puts in the video", () => {
    expect(logoPlanHelp([])).toMatch(/Nowhere else\.$/);
    expect(logoPlanHelp(["wipe"])).toMatch(/Also your brand on the wipe between stages\.$/);
    expect(logoPlanHelp(["summaries", "wipe"])).toContain("the shooter's logo on the summaries and your brand on the wipe");
    expect(logoPlanHelp(["summaries", "thumbnail", "wipe"])).toContain(
      "the shooter's logo on the summaries, a designed thumbnail and your brand on the wipe",
    );
  });
});
