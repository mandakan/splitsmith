import { describe, expect, it } from "vitest";

import { ApiError, type LookInfo } from "@/lib/api";
import {
  CARD_STYLE_SLOTS,
  PREVIEW_CARDS,
  TOKEN_GROUPS,
  contrastRatio,
  contrastWarnings,
  draftErrors,
  duplicateName,
  hexToRgb,
  isDirty,
  previewRequest,
  rgbToHex,
  setStyle,
  serialQueue,
  styleOptions,
  type LookDraft,
} from "@/lib/lookEditor";

const COLORS: LookDraft["colors"] = {
  ink: [244, 244, 245],
  ink_2: [201, 204, 210],
  muted: [142, 147, 155],
  subtle: [107, 112, 121],
  surface: [20, 23, 28],
  rule: [38, 43, 51],
  stroke: [10, 11, 13],
  accent: [255, 45, 45],
  accent_fill: [220, 38, 38],
  accent_text: [255, 255, 255],
  split: [251, 191, 36],
  split_good: [74, 222, 128],
};

const draft = (over: Partial<LookDraft> = {}): LookDraft => ({
  label: "Club",
  base: "splitsmith",
  colors: COLORS,
  accent_series: ["#ff2d2d"],
  styles: {},
  ...over,
});

const info = (slots: Record<string, string[]>): LookInfo => ({
  name: "club",
  label: "Club",
  source: "user",
  editable: true,
  accent_series: [],
  preview: null,
  slots: Object.fromEntries(
    Object.entries(slots).map(([k, v]) => [
      k,
      v.map((name) => ({ name, preview: null })),
    ]),
  ),
});

describe("tokens", () => {
  it("groups every required token once, each with where it shows", () => {
    const tokens = TOKEN_GROUPS.flatMap((g) => g.tokens);
    expect(new Set(tokens.map((t) => t.token)).size).toBe(tokens.length);
    for (const required of Object.keys(COLORS))
      expect(tokens.map((t) => t.token)).toContain(required);
    expect(tokens.every((t) => t.help.length > 0)).toBe(true);
    expect(TOKEN_GROUPS.map((g) => g.group)).toEqual([
      "Text",
      "Surfaces",
      "Accent",
      "Splits",
    ]);
  });

  it("converts between hex and triples", () => {
    expect(rgbToHex([255, 45, 0])).toBe("#ff2d00");
    expect(hexToRgb("#FF2D00")).toEqual([255, 45, 0]);
    expect(hexToRgb("red")).toBeNull();
    expect(hexToRgb("#ff2d0")).toBeNull();
  });
});

describe("errors and warnings", () => {
  it("a clean draft has no errors", () => {
    expect(draftErrors(draft())).toEqual({});
  });

  it("names the field: a long label, a missing token, a bad accent", () => {
    const { split_good: _gone, ...missing } = COLORS;
    const errors = draftErrors(
      draft({
        label: "x".repeat(61),
        colors: missing,
        accent_series: ["#ff2d2d", "red"],
      }),
    );
    expect(Object.keys(errors).sort()).toEqual([
      "accent_series.1",
      "colors.split_good",
      "label",
    ]);
  });

  it("measures contrast as WCAG does", () => {
    expect(contrastRatio([0, 0, 0], [255, 255, 255])).toBeCloseTo(21, 0);
    expect(contrastRatio([255, 255, 255], [255, 255, 255])).toBeCloseTo(1, 5);
  });

  it("warns when text on the accent fill is hard to read, and never blocks", () => {
    const low = contrastWarnings({ ...COLORS, accent_text: [240, 60, 60] });
    expect(low.map((w) => w.token)).toEqual(["accent_text"]);
    expect(low[0].message).toMatch(/contrast \d\.\d:1/);
    expect(contrastWarnings(COLORS)).toEqual([]);
    expect(
      draftErrors(draft({ colors: { ...COLORS, accent_text: [240, 60, 60] } })),
    ).toEqual({});
  });
});

describe("card styles", () => {
  it("offers each card slot's variants and stores only a non-default choice", () => {
    const look = info({ title_page: ["default", "rise"], slate: ["default"] });
    expect(CARD_STYLE_SLOTS.map((s) => s.slot)).toEqual([
      "title_page",
      "slate",
      "lower_third",
      "closing",
    ]);
    expect(styleOptions(look, "title_page")).toEqual(["default", "rise"]);
    expect(styleOptions(look, "lower_third")).toEqual(["default"]);
    const risen = setStyle(draft(), "title_page", "rise");
    expect(risen.styles).toEqual({ title_page: "rise" });
    expect(setStyle(risen, "title_page", "default").styles).toEqual({});
  });
});

describe("duplicate and dirty", () => {
  it("names a copy the server will take and nobody has", () => {
    expect(duplicateName("splitsmith", ["splitsmith"])).toBe("splitsmith-copy");
    expect(duplicateName("splitsmith", ["splitsmith-copy"])).toBe(
      "splitsmith-copy-2",
    );
    const long = "a".repeat(32);
    const name = duplicateName(long, []);
    expect(name.length).toBeLessThanOrEqual(32);
    expect(name).toMatch(/^[a-z][a-z0-9_-]{0,31}$/);
  });

  it("is dirty only when a stored field moved", () => {
    expect(isDirty(draft(), draft())).toBe(false);
    expect(
      isDirty(draft(), draft({ colors: { ...COLORS, accent: [1, 2, 3] } })),
    ).toBe(true);
    expect(isDirty(draft(), draft({ styles: { slate: "rise" } }))).toBe(true);
  });
});

describe("preview", () => {
  it("lists the cards with how long each runs", () => {
    expect(PREVIEW_CARDS.map((c) => c.card)).toEqual([
      "title",
      "slate",
      "lower-third",
      "summary",
      "closing",
      "sting",
    ]);
    expect(PREVIEW_CARDS.find((c) => c.card === "sting")?.seconds).toBe(1);
  });

  it("asks for the draft of the Look on the stage, at a time when one is set", () => {
    const d = draft({ styles: { slate: "rise" } });
    expect(
      previewRequest({
        card: "slate",
        look: "club",
        draft: d,
        stageNumber: 2,
        width: 320,
        at: null,
        sting: "wipe",
      }),
    ).toEqual({
      card: "slate",
      stage_number: 2,
      width: 320,
      look: "club",
      draft: d,
    });
    expect(
      previewRequest({
        card: "sting",
        look: "club",
        draft: d,
        stageNumber: 2,
        width: 960,
        at: 0.25,
        sting: "wipe",
      }),
    ).toEqual({
      card: "sting",
      stage_number: 2,
      width: 960,
      look: "club",
      draft: d,
      variant: "wipe",
      at: 0.25,
    });
  });
});

describe("serialQueue", () => {
  it("runs one task at a time in order and retries a busy answer", async () => {
    const log: string[] = [];
    let running = 0;
    let busyOnce = true;
    const queue = serialQueue({ retryDelayMs: 1, retries: 2 });
    const task = (name: string) => async () => {
      running += 1;
      expect(running).toBe(1);
      await new Promise((r) => setTimeout(r, 2));
      running -= 1;
      if (name === "b" && busyOnce) {
        busyOnce = false;
        throw new ApiError(429, "busy");
      }
      log.push(name);
      return name;
    };
    const results = await Promise.all([queue(task("a")), queue(task("b")), queue(task("c"))]);
    expect(results).toEqual(["a", "b", "c"]);
    expect(log).toEqual(["a", "b", "c"]);
  });

  it("gives up after its retries and passes other errors through", async () => {
    const queue = serialQueue({ retryDelayMs: 1, retries: 1 });
    await expect(queue(async () => Promise.reject(new ApiError(429, "busy")))).rejects.toMatchObject({ status: 429 });
    await expect(queue(async () => Promise.reject(new ApiError(503, "no browser")))).rejects.toMatchObject({
      status: 503,
    });
    expect(await queue(async () => "after")).toBe("after");
  });
});
