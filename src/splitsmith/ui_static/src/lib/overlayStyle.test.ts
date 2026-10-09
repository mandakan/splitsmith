import { describe, expect, it } from "vitest";

import type { LookInfo } from "@/lib/api";
import {
  DEFAULT_OVERLAY_STYLE,
  overlayStyleBody,
  overlayStyleFields,
  overlayStyleLabel,
  overlayStylePositions,
  overlayStylesFor,
  styleFromBody,
  visibleOverlayStyle,
} from "@/lib/overlayStyle";

const CATALOG: LookInfo[] = [
  {
    name: "splitsmith",
    label: "Splitsmith",
    source: "shipped",
    accent_series: [],
    preview: null,
    slots: {
      overlay: [
        { name: "pips", preview: "/api/looks/splitsmith/preview/overlay-pips.webp", positions: ["top-left", "top-right"] },
        { name: "plate", preview: "/api/looks/splitsmith/preview/overlay-plate.webp", positions: ["bottom-left", "top-right"] },
        { name: "timeline", preview: null, positions: [] },
      ],
    },
  },
  { name: "bare", label: "Bare", source: "user", accent_series: [], preview: null, slots: {} },
];

describe("overlay styles", () => {
  it("lists the chosen Look's styles from the catalog", () => {
    expect(overlayStylesFor(CATALOG, "splitsmith").map((v) => v.name)).toEqual(["pips", "plate", "timeline"]);
    expect(overlayStylesFor(CATALOG, "bare")).toEqual([]);
    expect(overlayStylesFor(CATALOG, "gone").map((v) => v.name)).toEqual(["pips", "plate", "timeline"]);
  });

  it("resolves a stored style the Look lacks to Classic, keeping the toggles", () => {
    const stored = { ...DEFAULT_OVERLAY_STYLE, variant: "ticker", landing: false };
    expect(visibleOverlayStyle(CATALOG, "splitsmith", stored)).toEqual({ ...stored, variant: "default" });
    const plate = { ...DEFAULT_OVERLAY_STYLE, variant: "plate" };
    expect(visibleOverlayStyle(CATALOG, "splitsmith", plate)).toBe(plate);
  });

  it("drops a position the style does not declare", () => {
    const stored = { ...DEFAULT_OVERLAY_STYLE, variant: "plate", position: "top-left" };
    expect(visibleOverlayStyle(CATALOG, "splitsmith", stored).position).toBeNull();
    expect(overlayStylePositions(CATALOG, "splitsmith", "plate")).toEqual(["bottom-left", "top-right"]);
    expect(overlayStylePositions(CATALOG, "splitsmith", "timeline")).toEqual([]);
  });

  it("sends nothing for Classic, so an untouched form sends the body it always sent", () => {
    expect(overlayStyleFields(DEFAULT_OVERLAY_STYLE)).toEqual({});
    expect(overlayStyleFields({ ...DEFAULT_OVERLAY_STYLE, landing: false })).toEqual({});
  });

  it("sends the style, its toggles and a chosen position", () => {
    expect(overlayStyleFields({ ...DEFAULT_OVERLAY_STYLE, variant: "plate", speedColors: false })).toEqual({
      overlay_variant: "plate",
      overlay_speed_colors: false,
      overlay_class_labels: true,
      overlay_landing: true,
      overlay_reload_chip: false,
      overlay_stage_bar: false,
    });
    expect(
      overlayStyleFields({ ...DEFAULT_OVERLAY_STYLE, variant: "plate", position: "top-right" }).overlay_position,
    ).toBe("top-right");
    expect(
      overlayStyleFields({ ...DEFAULT_OVERLAY_STYLE, variant: "plate", reloadChip: true, stageBar: true }),
    ).toMatchObject({ overlay_reload_chip: true, overlay_stage_bar: true });
  });

  it("round-trips through a preset body and reads an old body as Classic", () => {
    const style = {
      variant: "pips",
      speedColors: false,
      classLabels: true,
      landing: false,
      reloadChip: true,
      stageBar: true,
      position: "top-left",
    };
    expect(styleFromBody(overlayStyleBody(style))).toEqual(style);
    expect(styleFromBody({})).toEqual(DEFAULT_OVERLAY_STYLE);
  });

  it("an old preset body without the two toggles loads them off", () => {
    expect(styleFromBody({ overlay_variant: "pips", overlay_landing: false })).toEqual({
      ...DEFAULT_OVERLAY_STYLE,
      variant: "pips",
      landing: false,
    });
  });

  it("names a style for the rail", () => {
    expect(overlayStyleLabel("default")).toBe("Classic");
    expect(overlayStyleLabel("plate")).toBe("Plate");
  });
});
