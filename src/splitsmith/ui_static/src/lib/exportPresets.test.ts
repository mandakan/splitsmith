/**
 * The preset half of the Export form (spec 2026-09-15 s1): what a
 * preset captures, what it never captures, and the last-used store.
 */
import { describe, expect, it } from "vitest";

import type { ExportPresetBody } from "@/lib/api";
import {
  applyBody,
  DEFAULT_EXPORT_SETTINGS,
  groupSummary,
  isDirty,
  loadLastUsed,
  saveLastUsed,
  settingsToBody,
  type ExportSettings,
} from "@/lib/exportPresets";

const YOUTUBE: ExportPresetBody = {
  ...settingsToBody(DEFAULT_EXPORT_SETTINGS),
  output_format: "mp4",
  youtube_preset: true,
  padding_preset: "action",
  head_pad_seconds: 0.5,
  tail_pad_seconds: 1,
  title_page: true,
  closing_card: true,
  stage_card_style: "slate",
  summary_hold_seconds: 3,
  overlay: true,
};

class MemoryStorage {
  map = new Map<string, string>();
  getItem(k: string) {
    return this.map.get(k) ?? null;
  }
  setItem(k: string, v: string) {
    this.map.set(k, v);
  }
}

describe("settingsToBody / applyBody", () => {
  it("round-trips the defaults", () => {
    const body = settingsToBody(DEFAULT_EXPORT_SETTINGS);
    expect(settingsToBody(applyBody(DEFAULT_EXPORT_SETTINGS, body))).toEqual(body);
  });

  it("round-trips a full body", () => {
    expect(settingsToBody(applyBody(DEFAULT_EXPORT_SETTINGS, YOUTUBE))).toEqual(YOUTUBE);
  });

  it("keeps the match-specific fields the body does not carry", () => {
    const s: ExportSettings = {
      ...DEFAULT_EXPORT_SETTINGS,
      renderOptions: { ...DEFAULT_EXPORT_SETTINGS.renderOptions, titleInfo: "Production Optics" },
      uploadOptions: { ...DEFAULT_EXPORT_SETTINGS.uploadOptions, publishAt: "2026-10-01T18:00" },
    };
    const applied = applyBody(s, YOUTUBE);
    expect(applied.renderOptions.titleInfo).toBe("Production Optics");
    expect(applied.uploadOptions.publishAt).toBe("2026-10-01T18:00");
    expect(settingsToBody(s)).not.toHaveProperty("titleInfo");
    expect(settingsToBody(s)).not.toHaveProperty("publish_at");
  });
});

describe("isDirty", () => {
  it("is false right after apply", () => {
    expect(isDirty(applyBody(DEFAULT_EXPORT_SETTINGS, YOUTUBE), YOUTUBE)).toBe(false);
  });

  it("is true after a recurring field changes", () => {
    const s = applyBody(DEFAULT_EXPORT_SETTINGS, YOUTUBE);
    expect(isDirty({ ...s, headPad: 2 }, YOUTUBE)).toBe(true);
    expect(isDirty({ ...s, includeOverlay: false }, YOUTUBE)).toBe(true);
    expect(isDirty({ ...s, uploadOptions: { ...s.uploadOptions, privacy: "public" } }, YOUTUBE)).toBe(true);
  });

  it("stays false after a match-specific field changes", () => {
    const s = applyBody(DEFAULT_EXPORT_SETTINGS, YOUTUBE);
    expect(isDirty({ ...s, renderOptions: { ...s.renderOptions, titleInfo: "L3" } }, YOUTUBE)).toBe(false);
    expect(
      isDirty({ ...s, uploadOptions: { ...s.uploadOptions, publishAt: "2026-10-01T18:00" } }, YOUTUBE),
    ).toBe(false);
  });

  it("ignores schema_version and unknown keys on the stored body", () => {
    const stored = { ...YOUTUBE, schema_version: 1, laser_wipe: true } as ExportPresetBody;
    expect(isDirty(applyBody(DEFAULT_EXPORT_SETTINGS, YOUTUBE), stored)).toBe(false);
  });
});

describe("groupSummary", () => {
  const ctx = { secondaryCount: 2 };

  it("names the output", () => {
    expect(groupSummary(DEFAULT_EXPORT_SETTINGS, "output", ctx)).toBe("FCPXML · 2 cams");
    const yt = applyBody(DEFAULT_EXPORT_SETTINGS, YOUTUBE);
    expect(groupSummary(yt, "output", ctx)).toBe("MP4 · YouTube preset · auto codec · 2 cams");
    expect(groupSummary({ ...yt, mode: "trims" }, "output", ctx)).toBe("Lossless trims");
    expect(groupSummary({ ...yt, mode: "compare", canvas: "hd" }, "output", ctx)).toBe("1080p");
    expect(groupSummary(DEFAULT_EXPORT_SETTINGS, "output", { secondaryCount: 0 })).toBe("FCPXML");
  });

  it("names the cut", () => {
    expect(groupSummary(DEFAULT_EXPORT_SETTINGS, "cut", ctx)).toBe("Full 5.0 / 5.0 s · cut");
    expect(
      groupSummary({ ...DEFAULT_EXPORT_SETTINGS, transitionKind: "zoom", transitionSeconds: 0.5 }, "cut", ctx),
    ).toBe("Full 5.0 / 5.0 s · zoom 0.5 s");
  });

  it("names the look", () => {
    expect(groupSummary(DEFAULT_EXPORT_SETTINGS, "look", ctx)).toBe("No cards");
    expect(groupSummary(applyBody(DEFAULT_EXPORT_SETTINGS, YOUTUBE), "look", ctx)).toBe(
      "title page · slate · summary 3 s · closing · overlay",
    );
  });
});

describe("last-used", () => {
  it("round-trips the body and the active preset id", () => {
    const storage = new MemoryStorage();
    const s = applyBody(DEFAULT_EXPORT_SETTINGS, YOUTUBE);
    saveLastUsed(storage, s, "builtin:youtube");
    expect(loadLastUsed(storage)).toEqual({ body: YOUTUBE, presetId: "builtin:youtube" });
  });

  it("returns null on a missing, broken or throwing store", () => {
    expect(loadLastUsed(new MemoryStorage())).toBeNull();
    const broken = new MemoryStorage();
    broken.setItem("splitsmith.export.lastUsed", "{nope");
    expect(loadLastUsed(broken)).toBeNull();
    const throwing = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    expect(loadLastUsed(throwing)).toBeNull();
    expect(() => saveLastUsed(throwing, DEFAULT_EXPORT_SETTINGS, null)).not.toThrow();
  });
});
