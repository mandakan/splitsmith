import { describe, expect, it } from "vitest";

import {
  anyRenderOptionOn,
  cardsSupported,
  clampSeconds,
  DEFAULT_RENDER_OPTIONS,
  describeRenderOptions,
  gridExportFields,
  matchExportFields,
  MAX_HOLD_SECONDS,
  MIN_CARD_SECONDS,
  renderOptionsSeconds,
  stageCardsSupported,
  transitionsSupported,
  type RenderOptions,
} from "./renderOptions";
import { visibleSlots } from "./lookGallery";

const ON: RenderOptions = {
  titlePage: true,
  titleInfo: "  Production Optics  ",
  titlePageDurationSeconds: 4,
  closingCard: true,
  stageCardStyle: "slate",
  stageCardDurationSeconds: 2,
  summaryHoldSeconds: 3,
};

describe("cardsSupported", () => {
  it("is the rendered MP4 and nothing else", () => {
    expect(cardsSupported("mp4")).toBe(true);
    expect(cardsSupported("fcpxml")).toBe(false);
    expect(cardsSupported("fcp7xml")).toBe(false);
    expect(cardsSupported(undefined)).toBe(false);
  });
});

describe("matchExportFields", () => {
  it("sends every knob for an MP4, trimmed and clamped", () => {
    expect(matchExportFields(ON, "mp4")).toEqual({
      title_kind: "slate",
      title_duration_seconds: 2,
      title_page: true,
      title_info: "Production Optics",
      title_page_duration_seconds: 4,
      closing_card: true,
      summary_hold_seconds: 3,
    });
  });

  it("sends only the per-stage card for the XML formats, which FCPXML can draw", () => {
    const fields = matchExportFields(ON, "fcpxml");
    expect(fields).toEqual({ title_kind: "slate", title_duration_seconds: 2 });
    expect("title_page" in fields).toBe(false);
    expect("summary_hold_seconds" in fields).toBe(false);
  });

  it("defaults are every card off and the hold at zero", () => {
    expect(matchExportFields(DEFAULT_RENDER_OPTIONS, "mp4")).toEqual({
      title_kind: "none",
      title_duration_seconds: 1.5,
      title_page: false,
      title_info: null,
      title_page_duration_seconds: 3,
      closing_card: false,
      summary_hold_seconds: 0,
    });
  });

  it("a blank info line is null, not an empty line on the card", () => {
    expect(matchExportFields({ ...ON, titleInfo: "   " }, "mp4").title_info).toBeNull();
  });
});

describe("gridExportFields", () => {
  it("spells the per-stage card as stage_titles and carries no summary hold", () => {
    const fields = gridExportFields(ON);
    expect(fields).toEqual({
      stage_titles: "slate",
      title_duration_seconds: 2,
      title_page: true,
      title_info: "Production Optics",
      title_page_duration_seconds: 4,
      closing_card: true,
    });
    expect("summary_hold_seconds" in fields).toBe(false);
    expect("title_kind" in fields).toBe(false);
  });
});

describe("clampSeconds", () => {
  it("floors, caps, and turns a blank into the floor", () => {
    expect(clampSeconds(0.1, MIN_CARD_SECONDS)).toBe(MIN_CARD_SECONDS);
    expect(clampSeconds(999, 0)).toBe(MAX_HOLD_SECONDS);
    expect(clampSeconds(Number.NaN, 0)).toBe(0);
    expect(clampSeconds(2.5, 0)).toBe(2.5);
  });
});

describe("anyRenderOptionOn", () => {
  it("is false for the defaults and true once anything is on", () => {
    expect(anyRenderOptionOn(DEFAULT_RENDER_OPTIONS)).toBe(false);
    expect(anyRenderOptionOn({ ...DEFAULT_RENDER_OPTIONS, summaryHoldSeconds: 2 })).toBe(true);
    expect(anyRenderOptionOn({ ...DEFAULT_RENDER_OPTIONS, stageCardStyle: "lower-third" })).toBe(true);
  });
});

describe("stageCardsSupported", () => {
  it("is every format but the FCP 7 XML, and the mapper sends none there", () => {
    expect(stageCardsSupported("fcpxml")).toBe(true);
    expect(stageCardsSupported("mp4")).toBe(true);
    expect(stageCardsSupported("fcp7xml")).toBe(false);
    expect(matchExportFields(ON, "fcp7xml").title_kind).toBe("none");
    expect(matchExportFields(ON, "fcpxml").title_kind).toBe("slate");
  });
});

describe("describeRenderOptions", () => {
  it("names what is on in render order and nothing the format cannot draw", () => {
    expect(describeRenderOptions(DEFAULT_RENDER_OPTIONS, "single", "mp4")).toBeNull();
    expect(describeRenderOptions(ON, "single", "mp4")).toBe("title page · slate · summary 3 s · closing");
    expect(describeRenderOptions(ON, "grid", "mp4")).toBe("title page · slate · closing");
    expect(describeRenderOptions(ON, "single", "fcpxml")).toBe("slate");
    expect(describeRenderOptions(ON, "single", "fcp7xml")).toBeNull();
    expect(describeRenderOptions({ ...ON, stageCardStyle: "lower-third" }, "single", "fcpxml")).toBe("lower third");
  });
});

describe("renderOptionsSeconds", () => {
  it("adds a slate per stage, the match cards once each and a summary per stage", () => {
    expect(renderOptionsSeconds(ON, 3, "single", "mp4")).toBe(3 * 2 + 4 + 4 + 3 * 3);
    expect(renderOptionsSeconds(ON, 3, "grid", "mp4")).toBe(3 * 2 + 4 + 4);
    expect(renderOptionsSeconds(ON, 3, "single", "fcpxml")).toBe(3 * 2);
    expect(renderOptionsSeconds(ON, 3, "single", "fcp7xml")).toBe(0);
    expect(renderOptionsSeconds(ON, 0, "single", "mp4")).toBe(0);
  });
  it("a lower third adds nothing and a blank seconds field counts as the floor", () => {
    expect(renderOptionsSeconds({ ...DEFAULT_RENDER_OPTIONS, stageCardStyle: "lower-third" }, 3, "single", "mp4")).toBe(0);
    expect(
      renderOptionsSeconds(
        { ...DEFAULT_RENDER_OPTIONS, stageCardStyle: "slate", stageCardDurationSeconds: Number.NaN },
        2,
        "single",
        "mp4",
      ),
    ).toBe(2 * MIN_CARD_SECONDS);
  });
});

describe("the mappers against the gallery registry", () => {
  it("never emit a card field the gallery hides for the format", () => {
    for (const format of ["fcpxml", "fcp7xml", "mp4"] as const) {
      const fields = matchExportFields(ON, format);
      const slots = new Set(visibleSlots("single", format).map((s) => s.id));
      expect("title_page" in fields, format).toBe(slots.has("titlePage"));
      expect("closing_card" in fields, format).toBe(slots.has("closingCard"));
      expect("summary_hold_seconds" in fields, format).toBe(slots.has("summaryHold"));
      expect(fields.title_kind !== "none", format).toBe(slots.has("stageCard"));
      expect(transitionsSupported(format), format).toBe(slots.has("transition"));
    }
  });
});
