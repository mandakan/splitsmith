/**
 * The Look gallery registry (spec 2026-09-15 s2): the one description
 * of what the gallery offers, which mode and format can draw each
 * variant, and which settings field each tile writes.
 */
import { existsSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import type { LookInfo } from "@/lib/api";
import { DEFAULT_EXPORT_SETTINGS, type ExportSettings } from "@/lib/exportPresets";
import {
  LOOK_SLOTS,
  THUMBNAIL_FILES,
  VARIANT_FIELD,
  slotsForLook,
  thumbnailUrl,
  visibleSlots,
  visibleVariants,
  visibleTransitionKind,
  type LookSlot,
} from "@/lib/lookGallery";
import { BUILTIN_LOOKS } from "@/lib/looks";
import { FAMILIES, FAMILY_KINDS } from "@/test/transitionFamilies";
import type { RenderOptions } from "@/lib/renderOptions";

const ASSETS = resolve(__dirname, "../assets/look");
const CATALOG: LookInfo[] = [
  {
    name: "splitsmith",
    label: "Splitsmith",
    source: "shipped",
    accent_series: [],
    preview: "/api/looks/splitsmith/preview/look.png",
    slots: {
      title_page: [{ name: "default", preview: null }, { name: "rise", preview: "/api/looks/splitsmith/preview/title_page-rise.png" }],
      slate: [{ name: "default", preview: null }, { name: "rise", preview: null }],
      lower_third: [{ name: "default", preview: null }, { name: "rise", preview: null }],
      summary: [],
      closing: [{ name: "default", preview: null }, { name: "rise", preview: null }],
      transition: [{ name: "wipe", preview: "/api/looks/splitsmith/preview/transition-wipe.png" }],
    },
  },
  {
    name: "club",
    label: "Club",
    source: "user",
    accent_series: [],
    preview: null,
    slots: {
      title_page: [{ name: "default", preview: null }],
      slate: [{ name: "default", preview: null }],
      lower_third: [{ name: "default", preview: null }],
      summary: [],
      closing: [{ name: "default", preview: null }],
      transition: [],
    },
  },
];
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
    const committed = import.meta.glob("../assets/look/*.{png,webp}", { eager: true, import: "default" }) as Record<
      string,
      string
    >;
    const names = Object.keys(committed)
      .map((p) => p.split("/").pop()!)
      .sort();
    expect(names).toEqual([...THUMBNAIL_FILES].sort());
    expect(thumbnailUrl(names[0])).toMatch(/\.(png|webp)$/);
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
    visibleSlots(mode, format, slotsForLook(BUILTIN_LOOKS, DEFAULT_EXPORT_SETTINGS, FAMILIES)).map((s) => s.id);

  it("single + MP4 offers everything, the transition included (#1244)", () => {
    expect(ids("single", "mp4")).toEqual([
      "titlePage",
      "stageCard",
      "closingCard",
      "summaryHold",
      "matchSummary",
      "overlay",
      "transition",
    ]);
  });

  it("a stored kind the format cannot draw is sent as none, so the tile and the render agree", () => {
    expect(visibleTransitionKind("zoom", "mp4")).toBe("none");
    expect(visibleTransitionKind("static", "mp4")).toBe("none");
    expect(visibleTransitionKind("fade", "mp4", "single", FAMILY_KINDS)).toBe("fade");
    expect(visibleTransitionKind("fade", "mp4")).toBe("none");
    expect(visibleTransitionKind("fade", "fcpxml", "single", FAMILY_KINDS)).toBe("none");
    expect(visibleTransitionKind("zoom", "fcpxml")).toBe("zoom");
    expect(visibleTransitionKind("zoom", "fcp7xml")).toBe("none");
    expect(visibleTransitionKind("none", "mp4")).toBe("none");
    expect(visibleTransitionKind("sting:wipe", "mp4", "single", ["sting:wipe"])).toBe("sting:wipe");
    expect(visibleTransitionKind("sting:wipe", "mp4", "compare", ["sting:wipe"])).toBe("sting:wipe");
    expect(visibleTransitionKind("sting:wipe", "fcpxml", "single", ["sting:wipe"])).toBe("none");
    expect(visibleTransitionKind("sting:nope", "mp4", "single", ["sting:wipe"])).toBe("none");
  });

  it("the transition slot offers the server's families to MP4 and the two FCP effects to FCPXML", () => {
    const transition = slotsForLook(BUILTIN_LOOKS, DEFAULT_EXPORT_SETTINGS, FAMILIES).find((s) => s.id === "transition")!;
    expect(visibleVariants(transition, "single", "mp4").map((v) => v.id)).toEqual(["cut", "fade", "slide", "wind", "circle"]);
    expect(visibleVariants(transition, "compare", "mp4").map((v) => v.id)).toEqual(["cut", "fade", "slide", "wind", "circle"]);
    expect(visibleVariants(transition, "single", "fcpxml").map((v) => v.id)).toEqual(["cut", "static", "zoom"]);
    expect(transition.variants.find((v) => v.id === "wind")!.previewUrl).toBe("/api/looks/_transitions/preview/wind.webp");
  });

  it("single + FCPXML offers the stage card, the overlay and the transition only", () => {
    expect(ids("single", "fcpxml")).toEqual(["stageCard", "overlay", "transition"]);
  });

  it("single + FCP 7 XML offers the overlay only", () => {
    expect(ids("single", "fcp7xml")).toEqual(["overlay"]);
  });

  it("compare offers the match cards, the stage card, the match summary, the grid overlay and the transition, never the hold", () => {
    expect(ids("compare", "mp4")).toEqual([
      "titlePage",
      "stageCard",
      "closingCard",
      "matchSummary",
      "overlay",
      "transition",
    ]);
    // The grid's tile has its own thumbnail: a tile per shooter.
    const slot = visibleSlots("compare", "mp4", slotsForLook(BUILTIN_LOOKS, DEFAULT_EXPORT_SETTINGS, FAMILIES)).find(
      (s) => s.id === "matchSummary",
    );
    expect(visibleVariants(slot!, "compare", "mp4").map((v) => v.thumbnail)).toEqual([
      "none.png",
      "match-summary-grid.png",
    ]);
    expect(visibleTransitionKind("fade", "mp4", "compare", FAMILY_KINDS)).toBe("fade");
    expect(visibleTransitionKind("zoom", "mp4", "compare")).toBe("none");
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


describe("slotsForLook (#1246)", () => {
  const S = DEFAULT_EXPORT_SETTINGS;
  const idsFor = (looks: LookInfo[], mode: "single" | "compare", format: "fcpxml" | "mp4") =>
    visibleSlots(mode, format, slotsForLook(looks, S)).map((s) => s.id);

  it("with the built-in catalog alone the table is the one pinned above", () => {
    expect(idsFor(BUILTIN_LOOKS, "single", "mp4")).toEqual(visibleSlots("single", "mp4").map((s) => s.id));
    expect(idsFor(BUILTIN_LOOKS, "single", "fcpxml")).toEqual(visibleSlots("single", "fcpxml").map((s) => s.id));
    expect(slotsForLook(BUILTIN_LOOKS, S).find((s) => s.id === "transition")!.variants.map((v) => v.id)).toEqual(
      LOOK_SLOTS.find((s) => s.id === "transition")!.variants.map((v) => v.id),
    );
  });

  it("a second installed Look puts the Look tiles first, with their previews", () => {
    expect(idsFor(CATALOG, "single", "mp4")[0]).toBe("look");
    expect(idsFor(CATALOG, "single", "fcpxml")[0]).toBe("look");
    const look = slotsForLook(CATALOG, S)[0];
    expect(look.variants.map((v) => [v.id, v.name, v.previewUrl])).toEqual([
      ["splitsmith", "Splitsmith", "/api/looks/splitsmith/preview/look.png"],
      ["club", "Club", null],
    ]);
    expect(look.read(S)).toBe("splitsmith");
    expect(look.read({ ...S, look: "gone" })).toBe("splitsmith");
    expect(look.write(S, "club")).toEqual({ look: "club" });
  });

  it("the selected Look's stings join the transition tiles on MP4, both modes, never on FCPXML", () => {
    const transition = slotsForLook(CATALOG, S).find((s) => s.id === "transition")!;
    const sting = transition.variants.find((v) => v.id === "sting:wipe")!;
    expect(sting.previewUrl).toBe("/api/looks/splitsmith/preview/transition-wipe.png");
    expect(visibleVariants(transition, "single", "mp4").map((v) => v.id)).toContain("sting:wipe");
    expect(visibleVariants(transition, "compare", "mp4").map((v) => v.id)).toContain("sting:wipe");
    expect(visibleVariants(transition, "single", "fcpxml").map((v) => v.id)).not.toContain("sting:wipe");
    const club = slotsForLook(CATALOG, { ...S, look: "club" }).find((s) => s.id === "transition")!;
    expect(club.variants.map((v) => v.id)).not.toContain("sting:wipe");
  });

  it("visibleTransitionKind admits a sting only when the Look offers it", () => {
    expect(visibleTransitionKind("sting:wipe", "mp4", "single", ["sting:wipe"])).toBe("sting:wipe");
    expect(visibleTransitionKind("sting:wipe", "mp4", "compare", ["sting:wipe"])).toBe("sting:wipe");
    expect(visibleTransitionKind("sting:wipe", "mp4", "single", [])).toBe("none");
    expect(visibleTransitionKind("sting:wipe", "fcpxml", "single", ["sting:wipe"])).toBe("none");
    expect(visibleTransitionKind("sting:other", "mp4", "single", ["sting:wipe"])).toBe("none");
  });

  it("names the settings field and the catalog slot each card slot's Style writes", () => {
    expect(VARIANT_FIELD.titlePage).toEqual({ field: "titlePageVariant", slot: "title_page" });
    expect(VARIANT_FIELD.stageCard).toEqual({ field: "stageCardVariant", slot: "stage_card" });
    expect(VARIANT_FIELD.closingCard).toEqual({ field: "closingCardVariant", slot: "closing" });
    expect(VARIANT_FIELD.overlay).toBeUndefined();
  });
});


describe("transition tiles (#1246)", () => {
  it("the xfade tiles are looping WebP clips; the cut and the FCP effects stay stills", () => {
    const transition = slot("transition");
    for (const v of transition.variants) {
      const still = ["cut", "static", "zoom"].includes(v.id);
      expect(v.thumbnail.endsWith(still ? ".png" : ".webp"), v.id).toBe(true);
    }
  });
});


describe("transition families (#1259)", () => {
  const S = DEFAULT_EXPORT_SETTINGS;
  const transition = () => slotsForLook(BUILTIN_LOOKS, S, FAMILIES).find((s) => s.id === "transition")!;

  it("a stored kind reads as its family's tile, an old preset's included", () => {
    expect(transition().read({ ...S, transitionKind: "vuwind" })).toBe("wind");
    expect(transition().read({ ...S, transitionKind: "slideleft" })).toBe("slide");
    expect(transition().read({ ...S, transitionKind: "fade" })).toBe("fade");
    expect(transition().read({ ...S, transitionKind: "none" })).toBe("cut");
    expect(transition().read({ ...S, transitionKind: "zoom" })).toBe("zoom");
  });

  it("picking a family selects its first direction, and keeps the direction already picked", () => {
    expect(transition().write({ ...S, transitionKind: "fade" }, "wind")).toEqual({ transitionKind: "hlwind" });
    expect(transition().write({ ...S, transitionKind: "vuwind" }, "wind")).toEqual({ transitionKind: "vuwind" });
    expect(transition().write({ ...S, transitionKind: "vuwind" }, "slide")).toEqual({ transitionKind: "slideleft" });
    expect(transition().write(S, "cut")).toEqual({ transitionKind: "none" });
    expect(transition().write(S, "zoom")).toEqual({ transitionKind: "zoom" });
  });

  it("a family tile carries its directions for the Direction control", () => {
    const wind = transition().variants.find((v) => v.id === "wind")!;
    expect(wind.directions!.map((d) => d.name)).toEqual(["left", "right", "up", "down"]);
    expect(transition().variants.find((v) => v.id === "fade")!.directions).toHaveLength(1);
  });

  it("without the server's families the MP4 transition row is hidden, never a list of its own", () => {
    expect(visibleSlots("single", "mp4").map((s) => s.id)).not.toContain("transition");
  });
});


describe("a stored transition without the catalog (review of #1259)", () => {
  it("a stored kind keeps its tile while the server's families are not there, so it can be changed or cut", () => {
    const stored = { ...DEFAULT_EXPORT_SETTINGS, look: "splitsmith", transitionKind: "vuwind" };
    const transition = visibleSlots("single", "mp4", slotsForLook(BUILTIN_LOOKS, stored, [])).find((s) => s.id === "transition");
    expect(transition).toBeDefined();
    expect(visibleVariants(transition!, "single", "mp4").map((v) => [v.id, v.name])).toEqual([
      ["cut", "Hard cut"],
      ["vuwind", "vuwind"],
    ]);
    expect(transition!.read(stored)).toBe("vuwind");
    expect(transition!.write(stored, "cut")).toEqual({ transitionKind: "none" });
  });

  it("no stray tile once the families are there, or for the cut and the FCP effects", () => {
    const stored = { ...DEFAULT_EXPORT_SETTINGS, transitionKind: "vuwind" };
    const loaded = slotsForLook(BUILTIN_LOOKS, stored, FAMILIES).find((s) => s.id === "transition")!;
    expect(loaded.variants.filter((v) => v.id === "vuwind")).toEqual([]);
    for (const kind of ["none", "zoom", "static"]) {
      const slot = slotsForLook(BUILTIN_LOOKS, { ...stored, transitionKind: kind }, []).find((s) => s.id === "transition")!;
      expect(slot.variants.map((v) => v.id)).toEqual(["cut", "static", "zoom"]);
    }
  });
});
