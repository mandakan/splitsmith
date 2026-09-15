/**
 * The Look gallery registry (spec 2026-09-15 s2): the one description
 * of what the gallery offers, which mode and format can draw each
 * variant, and which settings field each tile writes.
 */
import { existsSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { DEFAULT_EXPORT_SETTINGS, type ExportSettings } from "@/lib/exportPresets";
import {
  LOOK_SLOTS,
  THUMBNAIL_FILES,
  thumbnailUrl,
  visibleSlots,
  visibleVariants,
  type LookSlot,
} from "@/lib/lookGallery";
import type { RenderOptions } from "@/lib/renderOptions";

const ASSETS = resolve(__dirname, "../assets/look");
const slot = (id: LookSlot["id"]) => LOOK_SLOTS.find((s) => s.id === id)!;
const apply = (s: ExportSettings, p: Partial<ExportSettings>): ExportSettings => ({ ...s, ...p });

describe("registry shape", () => {
  it("every slot leads with its none variant and every variant has a committed thumbnail", () => {
    for (const s of LOOK_SLOTS) {
      expect(s.variants.length).toBeGreaterThanOrEqual(2);
      expect(["none", "cut"]).toContain(s.variants[0].id);
      for (const v of s.variants) {
        expect(THUMBNAIL_FILES).toContain(v.thumbnail);
        expect(existsSync(resolve(ASSETS, v.thumbnail)), v.thumbnail).toBe(true);
      }
    }
  });

  it("references every committed thumbnail and nothing else", () => {
    const committed = import.meta.glob("../assets/look/*.png", { eager: true, import: "default" }) as Record<
      string,
      string
    >;
    const names = Object.keys(committed)
      .map((p) => p.split("/").pop()!)
      .sort();
    expect(names).toEqual([...THUMBNAIL_FILES].sort());
    expect(thumbnailUrl(names[0])).toMatch(/\.png$/);
  });

  it("every RenderOptions field except titleInfo is written by exactly one slot or param", () => {
    const fields: (keyof RenderOptions)[] = [
      "titlePage",
      "titlePageDurationSeconds",
      "closingCard",
      "stageCardStyle",
      "stageCardDurationSeconds",
      "summaryHoldSeconds",
    ];
    const before = DEFAULT_EXPORT_SETTINGS.renderOptions;
    const changes = (patch: Partial<ExportSettings>, field: keyof RenderOptions) =>
      patch.renderOptions !== undefined && patch.renderOptions[field] !== before[field];
    for (const field of fields) {
      // A slot counts once whether its tile or one of its params moves the field.
      const writers = LOOK_SLOTS.filter((s) => {
        const on = s.variants.find((v) => v.id !== s.variants[0].id)!;
        const bySlot = changes(s.write(DEFAULT_EXPORT_SETTINGS, on.id), field);
        const byParam = s.variants.some((v) =>
          v.params.some((p) => changes(p.write(DEFAULT_EXPORT_SETTINGS, 7), field)),
        );
        return bySlot || byParam;
      });
      // The title page and the closing card share one seconds field.
      expect(writers.map((w) => w.id), field).toHaveLength(field === "titlePageDurationSeconds" ? 2 : 1);
    }
  });
});

describe("read / write", () => {
  it("title page and closing card are two slots over the two booleans", () => {
    let s = DEFAULT_EXPORT_SETTINGS;
    expect(slot("titlePage").read(s)).toBe("none");
    s = apply(s, slot("titlePage").write(s, "on"));
    expect(s.renderOptions).toMatchObject({ titlePage: true, closingCard: false });
    s = apply(s, slot("closingCard").write(s, "on"));
    expect(s.renderOptions).toMatchObject({ titlePage: true, closingCard: true });
    s = apply(s, slot("titlePage").write(s, "none"));
    expect(s.renderOptions).toMatchObject({ titlePage: false, closingCard: true });
    expect(slot("closingCard").read(s)).toBe("on");
  });

  it("stage card maps the style and its seconds", () => {
    let s = apply(DEFAULT_EXPORT_SETTINGS, slot("stageCard").write(DEFAULT_EXPORT_SETTINGS, "lower-third"));
    expect(s.renderOptions.stageCardStyle).toBe("lower-third");
    const seconds = slot("stageCard").variants.find((v) => v.id === "lower-third")!.params[0];
    s = apply(s, seconds.write(s, 2.5));
    expect(seconds.read(s)).toBe(2.5);
    expect(s.renderOptions.stageCardDurationSeconds).toBe(2.5);
  });

  it("summary hold turns on at 3 s and reads back on while above zero", () => {
    let s = apply(DEFAULT_EXPORT_SETTINGS, slot("summaryHold").write(DEFAULT_EXPORT_SETTINGS, "on"));
    expect(s.renderOptions.summaryHoldSeconds).toBe(3);
    expect(slot("summaryHold").read(s)).toBe("on");
    s = apply(s, slot("summaryHold").write(s, "none"));
    expect(s.renderOptions.summaryHoldSeconds).toBe(0);
    // A cleared seconds field (NaN) keeps the slot on so the input stays.
    const blank = { ...s, renderOptions: { ...s.renderOptions, summaryHoldSeconds: Number.NaN } };
    expect(slot("summaryHold").read(blank)).toBe("on");
  });

  it("overlay writes the single-shooter flag in single mode and the grid flag in compare", () => {
    const single = apply(DEFAULT_EXPORT_SETTINGS, slot("overlay").write(DEFAULT_EXPORT_SETTINGS, "on"));
    expect(single).toMatchObject({ includeOverlay: true, gridOverlay: false });
    const grid0 = { ...DEFAULT_EXPORT_SETTINGS, mode: "compare" as const };
    const grid = apply(grid0, slot("overlay").write(grid0, "on"));
    expect(grid).toMatchObject({ includeOverlay: false, gridOverlay: true });
    expect(slot("overlay").read(grid)).toBe("on");
    const hold = slot("overlay")
      .variants.find((v) => v.id === "on")!
      .params.find((p) => p.id === "grid-hold")!;
    expect(hold.modes).toEqual(["compare"]);
    expect(hold.read(apply(grid, hold.write(grid, 2)))).toBe(2);
  });

  it("transition maps the kind and its seconds", () => {
    let s = apply(DEFAULT_EXPORT_SETTINGS, slot("transition").write(DEFAULT_EXPORT_SETTINGS, "zoom"));
    expect(s.transitionKind).toBe("zoom");
    const seconds = slot("transition").variants.find((v) => v.id === "zoom")!.params[0];
    s = apply(s, seconds.write(s, 0.8));
    expect(s.transitionSeconds).toBe(0.8);
    expect(slot("transition").variants[0].params).toEqual([]);
    expect(slot("transition").read(DEFAULT_EXPORT_SETTINGS)).toBe("cut");
    expect(slot("transition").write(s, "cut")).toEqual({ transitionKind: "none" });
  });
});

describe("visibility, pinned to the rules the render panel applied", () => {
  const ids = (mode: "single" | "trims" | "compare", format: "fcpxml" | "fcp7xml" | "mp4") =>
    visibleSlots(mode, format).map((s) => s.id);

  it("single + MP4 offers everything but the transition", () => {
    expect(ids("single", "mp4")).toEqual(["titlePage", "stageCard", "closingCard", "summaryHold", "overlay"]);
  });

  it("single + FCPXML offers the stage card, the overlay and the transition only", () => {
    expect(ids("single", "fcpxml")).toEqual(["stageCard", "overlay", "transition"]);
  });

  it("single + FCP 7 XML offers the overlay only", () => {
    expect(ids("single", "fcp7xml")).toEqual(["overlay"]);
  });

  it("compare offers the match cards, the stage card and the grid overlay, never the hold or a transition", () => {
    expect(ids("compare", "mp4")).toEqual(["titlePage", "stageCard", "closingCard", "overlay"]);
  });

  it("trims offers nothing", () => {
    expect(ids("trims", "fcpxml")).toEqual([]);
  });

  it("a variant hidden for the format is not offered either", () => {
    expect(visibleVariants(slot("stageCard"), "single", "fcp7xml").map((v) => v.id)).toEqual([]);
    expect(visibleVariants(slot("stageCard"), "single", "fcpxml").map((v) => v.id)).toEqual([
      "none",
      "slate",
      "lower-third",
    ]);
  });
});
