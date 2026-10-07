/**
 * The rail preview's plain logic (spec 2026-09-15 s3): which card a tile
 * previews, the request body from the form, the caption, the state lines.
 */
import { describe, expect, it } from "vitest";

import type { LookInfo } from "@/lib/api";
import { slotsForLook } from "@/lib/lookGallery";
import { BUILTIN_LOOKS } from "@/lib/looks";
import { FAMILIES } from "@/test/transitionFamilies";

import { DEFAULT_EXPORT_SETTINGS } from "@/lib/exportPresets";
import { previewBody, previewCaption, previewCardFor, previewLine } from "@/lib/exportPreview";

describe("previewCardFor", () => {
  it("maps every tile onto its card, the off tiles onto the frame, and transitions onto nothing", () => {
    expect(previewCardFor(null)).toBe("frame");
    expect(previewCardFor({ slotId: "titlePage", variantId: "on" })).toBe("title");
    expect(previewCardFor({ slotId: "titlePage", variantId: "none" })).toBe("frame");
    expect(previewCardFor({ slotId: "closingCard", variantId: "on" })).toBe("closing");
    expect(previewCardFor({ slotId: "stageCard", variantId: "slate" })).toBe("slate");
    expect(previewCardFor({ slotId: "stageCard", variantId: "lower-third" })).toBe("lower-third");
    expect(previewCardFor({ slotId: "summaryHold", variantId: "on" })).toBe("summary");
    expect(previewCardFor({ slotId: "overlay", variantId: "on" })).toBe("overlay");
    expect(previewCardFor({ slotId: "transition", variantId: "zoom" })).toBeNull();
    expect(previewCardFor({ slotId: "transition", variantId: "cut" })).toBeNull();
  });
});

describe("previewBody", () => {
  it("carries the title line, the pads and the width from the form", () => {
    const s = {
      ...DEFAULT_EXPORT_SETTINGS,
      headPad: 0.5,
      tailPad: 1,
      renderOptions: { ...DEFAULT_EXPORT_SETTINGS.renderOptions, titleInfo: "  Production Optics " },
    };
    expect(previewBody(s, "title", 3, "Bromma - Final Cut")).toEqual({
      card: "title",
      stage_number: 3,
      width: 960,
      title_info: "Production Optics",
      title_division: true,
      project_name: "Bromma - Final Cut",
      head_pad_seconds: 0.5,
      tail_pad_seconds: 1,
      motion: true,
    });
    expect(previewBody(DEFAULT_EXPORT_SETTINGS, "frame", 1).title_info).toBeNull();
    expect(previewBody(DEFAULT_EXPORT_SETTINGS, "frame", 1, "  ").project_name).toBeNull();
  });

  it("asks for motion on the cards a template draws, and only those (#1249)", () => {
    for (const card of ["title", "slate", "lower-third", "closing"] as const) {
      expect(previewBody(DEFAULT_EXPORT_SETTINGS, card, 1).motion).toBe(true);
    }
    for (const card of ["frame", "summary", "overlay"] as const) {
      expect(previewBody(DEFAULT_EXPORT_SETTINGS, card, 1).motion).toBeUndefined();
    }
  });

  it("uses the project's own buffers for the pads outside single mode", () => {
    const s = { ...DEFAULT_EXPORT_SETTINGS, mode: "compare" as const, headPad: 0.5, tailPad: 1 };
    const body = previewBody(s, "slate", 2);
    expect(body.head_pad_seconds).toBeUndefined();
    expect(body.tail_pad_seconds).toBeUndefined();
  });

  it("never sends a blank pad field", () => {
    const s = { ...DEFAULT_EXPORT_SETTINGS, headPad: Number.NaN };
    expect(previewBody(s, "frame", 1).head_pad_seconds).toBe(5);
  });
});

describe("previewCaption / previewLine", () => {
  it("names the tile and the stage with a two-digit ordinal", () => {
    expect(previewCaption(null, 3)).toBe("Stage 03");
    expect(previewCaption({ slotId: "stageCard", variantId: "slate" }, 3)).toBe("Slate · Stage 03");
    expect(previewCaption({ slotId: "overlay", variantId: "on" }, 12)).toBe("Shot counter · Stage 12");
    expect(previewCaption({ slotId: "transition", variantId: "zoom" }, 1)).toBe("Zoom blur");
  });

  it("has one line per failure", () => {
    expect(previewLine(503)).toBe("Preview needs a browser");
    expect(previewLine(409)).toBe("Overlay needs audited shots");
    expect(previewLine(500)).toBe("No preview");
    expect(previewLine(null)).toBe("No preview");
  });
});


describe("previewBody with a Look (#1246)", () => {
  const two = [{ name: "default", preview: null }, { name: "rise", preview: null }];
  const CLEAN: LookInfo[] = [
    {
      name: "clean",
      label: "Clean",
      source: "shipped",
      accent_series: [],
      preview: null,
      slots: { title_page: two, slate: [two[0]], lower_third: [], summary: [], closing: two, transition: [] },
    },
  ];
  it("carries the Look and the focused slot's variant", () => {
    const settings = {
      ...DEFAULT_EXPORT_SETTINGS,
      look: "clean",
      titlePageVariant: "rise",
      stageCardVariant: "default",
      closingCardVariant: "rise",
    };
    expect(previewBody(settings, "title", 1, "", CLEAN).look).toBe("clean");
    expect(previewBody(settings, "title", 1, "", CLEAN).variant).toBe("rise");
    expect(previewBody(settings, "slate", 1, "", CLEAN).variant).toBeUndefined();
    expect(previewBody(settings, "lower-third", 1, "", CLEAN).variant).toBeUndefined();
    expect(previewBody(settings, "closing", 1, "", CLEAN).variant).toBe("rise");
    expect(previewBody(settings, "frame", 1, "", CLEAN).variant).toBeUndefined();
    expect(previewBody(DEFAULT_EXPORT_SETTINGS, "title", 1)).not.toHaveProperty("look");
  });

  it("resolves the stored Look and variant against the catalog, so an uninstalled Look never 422s", () => {
    const catalog: LookInfo[] = [
      {
        name: "clean",
        label: "Clean",
        source: "shipped",
        accent_series: [],
        preview: null,
        slots: { title_page: [{ name: "default", preview: null }], slate: [], lower_third: [], summary: [], closing: [], transition: [] },
      },
    ];
    const gone = { ...DEFAULT_EXPORT_SETTINGS, look: "club", titlePageVariant: "rise" };
    expect(previewBody(gone, "title", 1, "", catalog)).not.toHaveProperty("look");
    expect(previewBody(gone, "title", 1, "", catalog)).not.toHaveProperty("variant");
    const clean = { ...DEFAULT_EXPORT_SETTINGS, look: "clean", titlePageVariant: "rise" };
    expect(previewBody(clean, "title", 1, "", catalog).look).toBe("clean");
    expect(previewBody(clean, "title", 1, "", catalog)).not.toHaveProperty("variant");
  });
});


describe("previewCardFor with the Look slot (#1246)", () => {
  it("a Look tile previews the title page in the chosen Look", () => {
    expect(previewCardFor({ slotId: "look", variantId: "club" })).toBe("title");
  });
});


describe("previewCaption with the catalog's slots (review of #1259)", () => {
  it("a transition family tile captions with its name, a card with its stage", () => {
    const slots = slotsForLook(BUILTIN_LOOKS, DEFAULT_EXPORT_SETTINGS, FAMILIES);
    expect(previewCaption({ slotId: "transition", variantId: "wind" }, 3, slots)).toBe("Wind");
    expect(previewCaption({ slotId: "transition", variantId: "fade" }, 3, slots)).toBe("Fade");
    expect(previewCaption({ slotId: "stageCard", variantId: "slate" }, 3, slots)).toBe("Slate · Stage 03");
  });
});
