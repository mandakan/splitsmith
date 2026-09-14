import { describe, expect, it } from "vitest";

import {
  anyRenderOptionOn,
  cardsSupported,
  clampSeconds,
  DEFAULT_RENDER_OPTIONS,
  gridExportFields,
  matchExportFields,
  MAX_HOLD_SECONDS,
  MIN_CARD_SECONDS,
  type RenderOptions,
} from "./renderOptions";

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
