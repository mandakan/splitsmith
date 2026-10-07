/**
 * lib/looks: the pure reader of the Looks catalog (#1246).
 */
import { describe, expect, it } from "vitest";

import type { LookInfo } from "@/lib/api";
import { DEFAULT_EXPORT_SETTINGS } from "@/lib/exportPresets";
import {
  BUILTIN_LOOKS,
  DEFAULT_LOOK,
  previewSrc,
  requestLook,
  resolveLookChoice,
  stingsFor,
  variantsFor,
  visibleLook,
  visibleVariant,
} from "@/lib/looks";

const CATALOG: LookInfo[] = [
  {
    name: "splitsmith",
    label: "Splitsmith",
    source: "shipped",
    accent_series: ["#ff2d2d"],
    preview: "/api/looks/splitsmith/preview/look.png",
    slots: {
      title_page: [
        { name: "default", preview: "/api/looks/splitsmith/preview/title_page-default.png" },
        { name: "rise", preview: "/api/looks/splitsmith/preview/title_page-rise.png" },
      ],
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

describe("visibleLook", () => {
  it("is the stored Look when installed, else the default (a deleted user Look, another machine's preset)", () => {
    expect(visibleLook(CATALOG, "club")).toBe("club");
    expect(visibleLook(CATALOG, "gone")).toBe(DEFAULT_LOOK);
    expect(visibleLook(BUILTIN_LOOKS, "club")).toBe(DEFAULT_LOOK);
  });
});

describe("variantsFor / visibleVariant", () => {
  it("lists a slot's variants and keeps a stored variant only when the Look offers it", () => {
    expect(variantsFor(CATALOG, "splitsmith", "slate").map((v) => v.name)).toEqual(["default", "rise"]);
    expect(variantsFor(CATALOG, "club", "slate").map((v) => v.name)).toEqual(["default"]);
    expect(variantsFor(CATALOG, "gone", "slate").map((v) => v.name)).toEqual(["default", "rise"]);
    expect(visibleVariant(CATALOG, "splitsmith", "slate", "rise")).toBe("rise");
    expect(visibleVariant(CATALOG, "club", "slate", "rise")).toBe("default");
    expect(visibleVariant(CATALOG, "splitsmith", "slate", "nope")).toBe("default");
  });

  it("the stage card's variant is the slate's and the lower third's together", () => {
    expect(visibleVariant(CATALOG, "splitsmith", "stage_card", "rise")).toBe("rise");
  });
});

describe("stingsFor", () => {
  it("is the Look's transition slot, by kind id", () => {
    expect(stingsFor(CATALOG, "splitsmith").map((s) => s.id)).toEqual(["sting:wipe"]);
    expect(stingsFor(CATALOG, "club")).toEqual([]);
    expect(stingsFor(BUILTIN_LOOKS, "splitsmith")).toEqual([]);
  });
});

describe("resolveLookChoice", () => {
  it("resolves every stored field against the catalog before a request", () => {
    const settings = {
      ...DEFAULT_EXPORT_SETTINGS,
      look: "gone",
      titlePageVariant: "rise",
      stageCardVariant: "nope",
      closingCardVariant: "rise",
    };
    expect(resolveLookChoice(CATALOG, settings)).toEqual({
      look: "splitsmith",
      titlePageVariant: "rise",
      stageCardVariant: "default",
      closingCardVariant: "rise",
    });
    expect(resolveLookChoice(CATALOG, { ...settings, look: "club" })).toEqual({
      look: "club",
      titlePageVariant: "default",
      stageCardVariant: "default",
      closingCardVariant: "default",
    });
  });
});

describe("previewSrc", () => {
  it("is the API path, scoped like every other request, or null", () => {
    expect(previewSrc("/api/looks/splitsmith/preview/look.png")).toMatch(/\/api\/looks\/splitsmith\/preview\/look\.png$/);
    expect(previewSrc(null)).toBeNull();
  });
});


describe("requestLook", () => {
  const settings = {
    ...DEFAULT_EXPORT_SETTINGS,
    look: "club",
    titlePageVariant: "rise",
    stageCardVariant: "nope",
    closingCardVariant: "default",
    transitionKind: "sting:wipe" as const,
  };

  it("resolves against a loaded catalog and lists the chosen Look's stings", () => {
    const { choice, stings } = requestLook({ looks: CATALOG, loaded: true, failed: false }, { ...settings, look: "splitsmith" });
    expect(choice).toEqual({ look: "splitsmith", titlePageVariant: "rise", stageCardVariant: "default", closingCardVariant: "default" });
    expect(stings).toEqual(["sting:wipe"]);
    expect(requestLook({ looks: CATALOG, loaded: true, failed: false }, settings).stings).toEqual([]);
  });

  it("sends the stored names unresolved when the catalog could not be fetched, so the server decides", () => {
    const { choice, stings } = requestLook({ looks: BUILTIN_LOOKS, loaded: true, failed: true }, settings);
    expect(choice).toEqual({ look: "club", titlePageVariant: "rise", stageCardVariant: "nope", closingCardVariant: "default" });
    expect(stings).toEqual(["sting:wipe"]);
    expect(requestLook({ looks: BUILTIN_LOOKS, loaded: true, failed: true }, { ...settings, transitionKind: "fade" }).stings).toEqual([]);
  });

  it("keeps the stored names before the catalog answers too (the page gates Export on loaded)", () => {
    expect(requestLook({ looks: BUILTIN_LOOKS, loaded: false, failed: false }, settings).choice.look).toBe("club");
  });
});
