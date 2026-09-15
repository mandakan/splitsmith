# Export Look gallery (part 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Export page's Look group becomes a gallery: every card, the overlay and the transitions are tiles with a generic thumbnail, in the style of Final Cut's titles browser, with the selected variant's parameters under its row.

**Architecture:** One registry, `lib/lookGallery.ts`, is the single description of the gallery: slots, their variants, each variant's thumbnail, parameters and the modes / formats that can draw it. The rule for what a mode and format can draw moves there from the render panel (the mappers keep their field-level rules and are tested against the registry). Thumbnails are static 480x270 PNGs under `ui_static/src/assets/look/`, generated once by `scripts/render_look_thumbnails.py` from the same card builders the renderers use, with placeholder text over a PIL-drawn backdrop, so the gallery never needs Chromium at runtime. `components/export/LookGallery.tsx` replaces `RenderOptionsPanel` inside `LookGroup`; transitions move from Cut into the gallery and the title line moves to Details.

**Tech Stack:** React 19, Tailwind 4, vitest + testing-library, Vite `import.meta.glob` for the assets; Python 3.11+, PIL, the existing `overlay_card` / `overlay_summary_cell` / `overlay_single` builders and `ChromiumRasterizer` (Playwright) for the one-off thumbnail script.

**Spec:** `docs/superpowers/specs/2026-09-15-export-presets-and-look-gallery-design.md`, section 2 ("The Look gallery"). Part 1 (presets and groups) is merged as #1038; part 3 (the real-match preview and the rail) is a separate plan.

## Global Constraints

- `uv` for Python, `pnpm` in `src/splitsmith/ui_static`; ruff + black (110) on Python, eslint + tsc on the SPA.
- Visual budget: primitives from `components/ui`; a tile is neutral outline with a tick when selected, never a coloured fill; red stays with the Export button; no `text-[...]` outside `components/ui`; `Label` for slot names (1-3 words); no issue numbers in UI copy.
- Prose: ASCII punctuation, no dashes as punctuation.
- Thumbnails: 480x270 PNG, committed under `src/splitsmith/ui_static/src/assets/look/`, named `<slot>-<variant>.png`. Regenerate with the script when a card's design changes.
- The gallery never needs Chromium at runtime; only the script does.
- A slot the current mode or format cannot draw is hidden, not disabled; a hidden slot's value must not reach the request body.
- Run Python tests targeted (`uv run pytest tests/<file> -n0`).
- Part 2 does not build the rail preview; hover on a tile does nothing beyond the tile until part 3 mounts `PreviewPane`.

---

## File map

Create:
- `src/splitsmith/ui_static/src/lib/lookGallery.ts` + `lookGallery.test.ts`: the registry, `visibleSlots`, `thumbnailUrl`.
- `src/splitsmith/ui_static/src/components/export/LookGallery.tsx` + `LookGallery.test.tsx`: the tiles and the parameter rows.
- `src/splitsmith/ui_static/src/components/export/Seconds.tsx`: the seconds input, moved out of the render panel.
- `src/splitsmith/ui_static/src/assets/look/*.png`: 13 thumbnails.
- `scripts/render_look_thumbnails.py` + `tests/test_render_look_thumbnails.py`.

Modify:
- `src/splitsmith/ui_static/src/lib/renderOptions.ts`: `transitionsSupported`.
- `src/splitsmith/ui_static/src/lib/exportPresets.ts`: Look summary includes the transition; Cut summary drops it.
- `src/splitsmith/ui_static/src/components/export/LookGroup.tsx`, `CutGroup.tsx`, `DetailsGroup.tsx`, `pages/Export.tsx`.
- The Export page tests that reach the old controls.
- `CLAUDE.md` (a paragraph under "Export presets").

Delete:
- `src/splitsmith/ui_static/src/components/render/RenderOptionsPanel.tsx` + `.test.tsx` (its cases move into `LookGallery.test.tsx`).

---

### Task 1: The registry

**Files:**
- Create: `src/splitsmith/ui_static/src/lib/lookGallery.ts`
- Modify: `src/splitsmith/ui_static/src/lib/renderOptions.ts` (add `transitionsSupported`)
- Test: `src/splitsmith/ui_static/src/lib/lookGallery.test.ts`

**Interfaces:**
- Produces:

```ts
export type LookSlotId = "titlePage" | "stageCard" | "closingCard" | "summaryHold" | "overlay" | "transition";
export interface LookParam {
  id: string;                // unique within the variant; the input id
  label: string;             // aria-label of the input
  min: number;
  read(s: ExportSettings): number;
  write(s: ExportSettings, n: number): Partial<ExportSettings>;
  modes?: ExportMode[];      // default: the variant's
}
export interface LookVariant {
  id: string;                // "none" | "on" | "slate" | ...
  name: string;              // tile caption
  thumbnail: string;         // file name under assets/look/
  help: string;              // one line under the row while selected
  params: LookParam[];
  modes: ExportMode[];
  formats: OutputFormat[];
}
export interface LookSlot {
  id: LookSlotId;
  label: string;
  variants: LookVariant[];   // [0] is always the "none" / default variant
  read(s: ExportSettings): string;
  write(s: ExportSettings, variantId: string): Partial<ExportSettings>;
}
export const LOOK_SLOTS: readonly LookSlot[];
export function visibleSlots(mode: ExportMode, format: OutputFormat): LookSlot[];
export function visibleVariants(slot: LookSlot, mode: ExportMode, format: OutputFormat): LookVariant[];
export function thumbnailUrl(file: string): string;
export const THUMBNAIL_FILES: readonly string[];   // every file the registry references
```
- `renderOptions.transitionsSupported(format)` -> `format === "fcpxml"`.

- [ ] **Step 1: Write the failing tests**

```ts
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
  visibleSlots,
  visibleVariants,
  thumbnailUrl,
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
        expect(existsSync(resolve(ASSETS, v.thumbnail))).toBe(true);
      }
    }
  });

  it("references every committed thumbnail and nothing else", () => {
    const committed = (import.meta.glob("../assets/look/*.png", { eager: true, import: "default" }) as Record<string, string>);
    const names = Object.keys(committed).map((p) => p.split("/").pop()!).sort();
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
        const byParam = s.variants.some((v) => v.params.some((p) => changes(p.write(DEFAULT_EXPORT_SETTINGS, 7), field)));
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
  });

  it("overlay writes the single-shooter flag in single mode and the grid flag in compare", () => {
    const single = apply(DEFAULT_EXPORT_SETTINGS, slot("overlay").write(DEFAULT_EXPORT_SETTINGS, "on"));
    expect(single).toMatchObject({ includeOverlay: true, gridOverlay: false });
    const grid0 = { ...DEFAULT_EXPORT_SETTINGS, mode: "compare" as const };
    const grid = apply(grid0, slot("overlay").write(grid0, "on"));
    expect(grid).toMatchObject({ includeOverlay: false, gridOverlay: true });
    expect(slot("overlay").read(grid)).toBe("on");
    const hold = slot("overlay").variants.find((v) => v.id === "on")!.params.find((p) => p.id === "grid-hold")!;
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
    expect(visibleVariants(slot("stageCard"), "single", "fcpxml").map((v) => v.id)).toEqual(["none", "slate", "lower-third"]);
  });
});
```

`import.meta.glob` with a relative pattern works in vitest because the test file sits in `src/lib/` and the assets in `src/assets/look/`; the registry uses the same relative pattern from the same directory.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/lib/lookGallery.test.ts`
Expected: FAIL, module not found.

- [ ] **Step 3: Add `transitionsSupported` to `renderOptions.ts`**

After `stageCardsSupported`:

```ts
/** Transitions exist only in the FCPXML export today; the FCP 7 XML and
 *  the MP4 record an "ignored" anomaly for one, and a slate cannot be
 *  combined with one there either. */
export function transitionsSupported(outputFormat: OutputFormat | undefined): boolean {
  return outputFormat === "fcpxml";
}
```

- [ ] **Step 4: Write the registry**

`src/splitsmith/ui_static/src/lib/lookGallery.ts`:

```ts
/**
 * The Look gallery registry (spec 2026-09-15 s2). One description of
 * every card, the overlay and the transitions as slots of variants:
 * each variant names its thumbnail, the parameters it exposes under
 * its tile, and the modes and formats that can draw it. The gallery
 * component renders whatever is here; adding an effect is one entry
 * and one committed thumbnail (``scripts/render_look_thumbnails.py``).
 *
 * The visibility rule used to live in the render panel (the match
 * cards and the hold are MP4-only, the stage card reaches FCPXML too,
 * FCP 7 XML draws none). It is data here so the panel and the request
 * mappers cannot drift: ``lookGallery.test.ts`` pins the per-format
 * table, and ``renderOptions.test.ts`` pins that the mappers never emit
 * a field the registry hides.
 */
import type { ExportMode } from "@/lib/exportPlan";
import type { ExportSettings } from "@/lib/exportPresets";
import {
  cardsSupported,
  stageCardsSupported,
  transitionsSupported,
  MIN_CARD_SECONDS,
  type OutputFormat,
} from "@/lib/renderOptions";

export type LookSlotId = "titlePage" | "stageCard" | "closingCard" | "summaryHold" | "overlay" | "transition";

export interface LookParam {
  /** Unique within the variant; becomes the input id. */
  id: string;
  label: string;
  min: number;
  read(s: ExportSettings): number;
  write(s: ExportSettings, n: number): Partial<ExportSettings>;
  /** Narrower than the variant's modes when a parameter is one mode's own. */
  modes?: ExportMode[];
}

export interface LookVariant {
  id: string;
  name: string;
  /** File under ``assets/look/``. */
  thumbnail: string;
  /** One line under the row while this variant is selected. */
  help: string;
  params: LookParam[];
  modes: ExportMode[];
  formats: OutputFormat[];
}

export interface LookSlot {
  id: LookSlotId;
  /** 1-3 words; rendered as a Label. */
  label: string;
  /** ``[0]`` is the off / default variant. */
  variants: LookVariant[];
  read(s: ExportSettings): string;
  write(s: ExportSettings, variantId: string): Partial<ExportSettings>;
}

const ALL_MODES: ExportMode[] = ["single", "compare"];
const ALL_FORMATS: OutputFormat[] = ["fcpxml", "fcp7xml", "mp4"];
const MP4: OutputFormat[] = ["mp4"];
const STAGE_CARD_FORMATS: OutputFormat[] = ALL_FORMATS.filter(stageCardsSupported);
const MATCH_CARD_FORMATS: OutputFormat[] = ALL_FORMATS.filter(cardsSupported);
const TRANSITION_FORMATS: OutputFormat[] = ALL_FORMATS.filter(transitionsSupported);

/** What the hold turns on at: the YouTube built-in's value. */
export const DEFAULT_SUMMARY_HOLD_SECONDS = 3;

const render = (s: ExportSettings, patch: Partial<ExportSettings["renderOptions"]>): Partial<ExportSettings> => ({
  renderOptions: { ...s.renderOptions, ...patch },
});

const titleSeconds: LookParam = {
  id: "title-seconds",
  label: "Title page seconds",
  min: MIN_CARD_SECONDS,
  read: (s) => s.renderOptions.titlePageDurationSeconds,
  write: (s, n) => render(s, { titlePageDurationSeconds: n }),
};

const stageSeconds: LookParam = {
  id: "stage-seconds",
  label: "Stage card seconds",
  min: MIN_CARD_SECONDS,
  read: (s) => s.renderOptions.stageCardDurationSeconds,
  write: (s, n) => render(s, { stageCardDurationSeconds: n }),
};

const none = (thumbnail: string, help: string, modes: ExportMode[], formats: OutputFormat[]): LookVariant => ({
  id: "none",
  name: "None",
  thumbnail,
  help,
  params: [],
  modes,
  formats,
});

export const LOOK_SLOTS: readonly LookSlot[] = [
  {
    id: "titlePage",
    label: "Title page",
    variants: [
      none("none.png", "No card before the first stage.", ALL_MODES, MP4),
      {
        id: "on",
        name: "Title page",
        thumbnail: "title-page.png",
        help: "Match name and date before the first stage, with the title line from Details under it.",
        params: [titleSeconds],
        modes: ALL_MODES,
        formats: MATCH_CARD_FORMATS,
      },
    ],
    read: (s) => (s.renderOptions.titlePage ? "on" : "none"),
    write: (s, id) => render(s, { titlePage: id === "on" }),
  },
  {
    id: "stageCard",
    label: "Stage card",
    variants: [
      none("none.png", "Stages follow each other with no card.", ALL_MODES, STAGE_CARD_FORMATS),
      {
        id: "slate",
        name: "Slate",
        thumbnail: "stage-card-slate.png",
        help: "Stage name and round count on its own card before each stage.",
        params: [stageSeconds],
        modes: ALL_MODES,
        formats: STAGE_CARD_FORMATS,
      },
      {
        id: "lower-third",
        name: "Lower third",
        thumbnail: "stage-card-lower-third.png",
        help: "Stage name and round count over the stage's first seconds.",
        params: [stageSeconds],
        modes: ALL_MODES,
        formats: STAGE_CARD_FORMATS,
      },
    ],
    read: (s) => s.renderOptions.stageCardStyle,
    write: (s, id) => render(s, { stageCardStyle: id as ExportSettings["renderOptions"]["stageCardStyle"] }),
  },
  {
    id: "closingCard",
    label: "Closing card",
    variants: [
      none("none.png", "The video ends on the last stage.", ALL_MODES, MP4),
      {
        id: "on",
        name: "Closing card",
        thumbnail: "closing-card.png",
        help: "Repeats the title page after the last stage, for the same seconds.",
        params: [titleSeconds],
        modes: ALL_MODES,
        formats: MATCH_CARD_FORMATS,
      },
    ],
    read: (s) => (s.renderOptions.closingCard ? "on" : "none"),
    write: (s, id) => render(s, { closingCard: id === "on" }),
  },
  {
    id: "summaryHold",
    label: "Stage summary",
    variants: [
      none("none.png", "The next stage follows the last shot.", ["single"], MP4),
      {
        id: "on",
        name: "Summary hold",
        thumbnail: "summary-hold.png",
        help: "Held after each stage: name, scoring, splits.",
        params: [
          {
            id: "summary-seconds",
            label: "Summary hold seconds",
            min: MIN_CARD_SECONDS,
            read: (s) => s.renderOptions.summaryHoldSeconds,
            write: (s, n) => render(s, { summaryHoldSeconds: n }),
          },
        ],
        modes: ["single"],
        formats: MP4,
      },
    ],
    read: (s) => (s.renderOptions.summaryHoldSeconds > 0 ? "on" : "none"),
    write: (s, id) => render(s, { summaryHoldSeconds: id === "on" ? DEFAULT_SUMMARY_HOLD_SECONDS : 0 }),
  },
  {
    id: "overlay",
    label: "Overlay",
    variants: [
      none("none.png", "No counters on the footage; the FCPXML still carries shot markers.", ALL_MODES, ALL_FORMATS),
      {
        id: "on",
        name: "Shot counter",
        thumbnail: "overlay.png",
        help: "Burned-in shot counter and splits over the footage; a slower render. The codec is under Output.",
        params: [
          {
            id: "grid-hold",
            label: "Grid summary hold seconds",
            min: 0,
            read: (s) => s.gridHoldSeconds,
            write: (s, n) => ({ gridHoldSeconds: n }),
            modes: ["compare"],
          },
        ],
        modes: ALL_MODES,
        formats: ALL_FORMATS,
      },
    ],
    read: (s) => ((s.mode === "compare" ? s.gridOverlay : s.includeOverlay) ? "on" : "none"),
    write: (s, id) => (s.mode === "compare" ? { gridOverlay: id === "on" } : { includeOverlay: id === "on" }),
  },
  {
    id: "transition",
    label: "Transition",
    variants: [
      {
        id: "cut",
        name: "Hard cut",
        thumbnail: "transition-cut.png",
        help: "Stages follow each other on the next frame.",
        params: [],
        modes: ["single"],
        formats: TRANSITION_FORMATS,
      },
      {
        id: "static",
        name: "Static frame",
        thumbnail: "transition-static.png",
        help: "Holds the last frame of a stage before the next one starts.",
        params: [transitionSeconds()],
        modes: ["single"],
        formats: TRANSITION_FORMATS,
      },
      {
        id: "zoom",
        name: "Zoom blur",
        thumbnail: "transition-zoom.png",
        help: "Zooms and blurs out of a stage and into the next.",
        params: [transitionSeconds()],
        modes: ["single"],
        formats: TRANSITION_FORMATS,
      },
    ],
    read: (s) => (s.transitionKind === "none" ? "cut" : s.transitionKind),
    write: (s, id) => ({ transitionKind: id === "cut" ? "none" : (id as ExportSettings["transitionKind"]) }),
  },
];

function transitionSeconds(): LookParam {
  return {
    id: "transition-seconds",
    label: "Transition seconds",
    min: 0.1,
    read: (s) => s.transitionSeconds,
    write: (s, n) => ({ transitionSeconds: n }),
  };
}

export function visibleVariants(slot: LookSlot, mode: ExportMode, format: OutputFormat): LookVariant[] {
  return slot.variants.filter((v) => v.modes.includes(mode) && v.formats.includes(format));
}

/** A slot shows when at least one variant beyond the default can be drawn. */
export function visibleSlots(mode: ExportMode, format: OutputFormat): LookSlot[] {
  return LOOK_SLOTS.filter((slot) => visibleVariants(slot, mode, format).length > 1);
}

/** Every thumbnail the registry references, for the coverage test and the script. */
export const THUMBNAIL_FILES: readonly string[] = [
  ...new Set(LOOK_SLOTS.flatMap((s) => s.variants.map((v) => v.thumbnail))),
];

const THUMBNAILS = import.meta.glob("../assets/look/*.png", { eager: true, import: "default" }) as Record<string, string>;

/** The bundled URL for a thumbnail file name; the glob keeps the images
 *  in the build without a runtime fetch of anything but the PNG. */
export function thumbnailUrl(file: string): string {
  const hit = Object.entries(THUMBNAILS).find(([path]) => path.endsWith(`/${file}`));
  return hit ? hit[1] : "";
}
```

`MIN_CARD_SECONDS` is exported from `renderOptions.ts` already (0.5). The thumbnail files do not exist until Task 2; the two tests that check the disk and the glob stay red until then. That is expected: run the other cases now with `-t "read|visibility"`.

- [ ] **Step 5: Run the read / write and visibility tests**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/lib/lookGallery.test.ts -t "read|visibility" && pnpm typecheck`
Expected: PASS; the two "registry shape" thumbnail cases are deferred to Task 2.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/lookGallery.ts src/splitsmith/ui_static/src/lib/lookGallery.test.ts src/splitsmith/ui_static/src/lib/renderOptions.ts
git commit -m "feat(ui): Look gallery registry"
```

---

### Task 2: The thumbnail script and the committed thumbnails

**Files:**
- Create: `scripts/render_look_thumbnails.py`
- Create: `src/splitsmith/ui_static/src/assets/look/*.png` (generated)
- Test: `tests/test_render_look_thumbnails.py`

**Interfaces:**
- Produces: `THUMBNAILS: tuple[str, ...]` (the 13 file names, matching `THUMBNAIL_FILES` in the registry), `build_thumbnails(out: Path, *, rasterizer: Rasterizer, theme: OverlayTheme) -> list[Path]`, `WIDTH = 480`, `HEIGHT = 270`, `main()`.

- [ ] **Step 1: Write the failing test**

```python
"""``scripts/render_look_thumbnails.py``: the gallery's generic tiles.

The rasterizer is a stub that returns one transparent PNG, so the test
runs without Chromium; what it pins is the file set, the size and that
every builder path composes without the browser doing anything.
"""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path

from PIL import Image

from splitsmith.overlay_theme import load_theme

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "render_look_thumbnails.py"
REGISTRY = Path(__file__).resolve().parent.parent / "src/splitsmith/ui_static/src/lib/lookGallery.ts"


def _load():
    spec = importlib.util.spec_from_file_location("render_look_thumbnails", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _StubRasterizer:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.calls.append((width, height))
        buf = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buf, format="PNG")
        return buf.getvalue()


def test_writes_every_thumbnail_at_the_gallery_size(tmp_path: Path) -> None:
    mod = _load()
    raster = _StubRasterizer()
    written = mod.build_thumbnails(tmp_path, rasterizer=raster, theme=load_theme("splitsmith"))
    assert sorted(p.name for p in written) == sorted(mod.THUMBNAILS)
    for path in written:
        with Image.open(path) as im:
            assert im.size == (mod.WIDTH, mod.HEIGHT), path.name
            assert im.mode == "RGB"
    # Every text card went through the rasterizer at the tile size.
    assert raster.calls and all(c == (mod.WIDTH, mod.HEIGHT) for c in raster.calls)


def test_file_set_matches_the_registry() -> None:
    """The TS registry names the files; the script must write exactly those."""
    mod = _load()
    source = REGISTRY.read_text(encoding="utf-8")
    named = {name for name in mod.THUMBNAILS if f'"{name}"' in source}
    assert named == set(mod.THUMBNAILS)
    import re

    referenced = set(re.findall(r'"([a-z0-9-]+\.png)"', source))
    assert referenced == set(mod.THUMBNAILS)


def test_transition_tiles_differ_from_each_other(tmp_path: Path) -> None:
    mod = _load()
    mod.build_thumbnails(tmp_path, rasterizer=_StubRasterizer(), theme=load_theme("splitsmith"))
    bytes_of = {n: (tmp_path / n).read_bytes() for n in ("transition-cut.png", "transition-static.png", "transition-zoom.png")}
    assert len(set(bytes_of.values())) == 3
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_render_look_thumbnails.py -n0 -q`
Expected: FAIL, the script does not exist.

- [ ] **Step 3: Write the script**

`scripts/render_look_thumbnails.py`:

```python
"""Render the Look gallery's generic thumbnails (spec 2026-09-15 s2).

One 480x270 PNG per gallery variant, under
``src/splitsmith/ui_static/src/assets/look/``, drawn by the same card
builders the two MP4 renderers use (``overlay_card``,
``overlay_summary_cell``, ``overlay_single``) with placeholder text over
a backdrop this script paints itself, so the tiles need neither footage
nor ffmpeg. They need a Chromium the rasterizer can launch, once, here;
the gallery serves the committed files and never rasterizes anything.

Re-run after a card's design changes and commit the result::

    uv run python scripts/render_look_thumbnails.py

``tests/test_render_look_thumbnails.py`` runs ``build_thumbnails`` with a
stub rasterizer and pins the file set against the TS registry.
"""

from __future__ import annotations

import argparse
import io
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from splitsmith import composition  # noqa: E402
from splitsmith.match_project import StageScorecard  # noqa: E402
from splitsmith.overlay_card import build_card_still, build_lower_third, card_scale  # noqa: E402
from splitsmith.overlay_html import single_html  # noqa: E402
from splitsmith.overlay_raster import ChromiumRasterizer, Rasterizer  # noqa: E402
from splitsmith.overlay_single import OverlayRun, run_groups  # noqa: E402
from splitsmith.overlay_still import backdrop_from_frame  # noqa: E402
from splitsmith.overlay_summary_cell import build_summary_still  # noqa: E402
from splitsmith.overlay_theme import OverlayTheme, load_theme  # noqa: E402
from splitsmith.stage_summary_data import TileShot, TileStageData  # noqa: E402

WIDTH = 480
HEIGHT = 270
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "src/splitsmith/ui_static/src/assets/look"

#: The file set the TS registry (``lib/lookGallery.ts``) references.
THUMBNAILS: tuple[str, ...] = (
    "none.png",
    "title-page.png",
    "closing-card.png",
    "stage-card-slate.png",
    "stage-card-lower-third.png",
    "summary-hold.png",
    "overlay.png",
    "transition-cut.png",
    "transition-static.png",
    "transition-zoom.png",
)

MATCH = "Match title"
STAGE = "Stage 03 . Standards"
SHOOTER = "A. Shooter"
SPLITS_MS = (1420, 260, 240, 1180, 250, 270, 980, 230, 260)


def paint_backdrop() -> Image.Image:
    """A range-like scene: a dark sky gradient over a lighter ground band
    and a few target silhouettes. Enough for the blur to have something
    to blur; nothing a viewer would mistake for footage."""
    im = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(im)
    for y in range(HEIGHT):
        t = y / HEIGHT
        sky = (int(38 + 30 * t), int(46 + 34 * t), int(58 + 30 * t))
        draw.line([(0, y), (WIDTH, y)], fill=sky)
    horizon = int(HEIGHT * 0.62)
    draw.rectangle([0, horizon, WIDTH, HEIGHT], fill=(96, 84, 66))
    for x in (110, 240, 370):
        draw.rectangle([x - 22, horizon - 70, x + 22, horizon], fill=(180, 140, 92))
        draw.rectangle([x - 12, horizon - 100, x + 12, horizon - 70], fill=(180, 140, 92))
    return im


def _backdrop_path(tmp: Path) -> Path:
    path = tmp / "backdrop.png"
    paint_backdrop().save(path)
    return path


def _sample_tile() -> TileStageData:
    shots: list[TileShot] = []
    t = 0.0
    for i, ms in enumerate(SPLITS_MS):
        t += ms / 1000
        shots.append(TileShot(time_from_beep=t, split=ms / 1000 if i else t))
    return TileStageData(
        label=SHOOTER,
        stage_number=3,
        shots=tuple(shots),
        stage_time_seconds=18.42,
        scorecard=StageScorecard(hit_factor=6.21, alphas=14, charlies=3, deltas=1, misses=0),
    )


def _overlay(backdrop: Image.Image, *, rasterizer: Rasterizer, theme: OverlayTheme) -> Image.Image:
    run = OverlayRun(start_frame=0, frame_count=1, shots_fired=7, shot_count=18, last_split=0.26)
    html = single_html(run_groups(run), width=WIDTH, height=HEIGHT, scale=card_scale(HEIGHT), theme=theme)
    png = rasterizer.png(html, width=WIDTH, height=HEIGHT)
    with Image.open(io.BytesIO(png)) as text:
        out = backdrop.convert("RGBA")
        out.alpha_composite(text.convert("RGBA"))
    return out.convert("RGB")


def _transition(kind: str, backdrop: Image.Image) -> Image.Image:
    """Two half-frames with the transition drawn on the seam: a hard edge,
    a desaturated held band, or a zoom-blurred band."""
    left = backdrop
    right = backdrop.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    out = Image.new("RGB", (WIDTH, HEIGHT))
    half = WIDTH // 2
    out.paste(left.crop((0, 0, half, HEIGHT)), (0, 0))
    out.paste(right.crop((half, 0, WIDTH, HEIGHT)), (half, 0))
    band = (half - 60, 0, half + 60, HEIGHT)
    if kind == "static":
        strip = left.crop(band).convert("L").convert("RGB")
        out.paste(strip, band[:2])
    elif kind == "zoom":
        strip = left.crop(band)
        blurred = strip
        for scale in (1.03, 1.06, 1.09):
            w, h = strip.size
            bigger = strip.resize((int(w * scale), int(h * scale))).crop(
                ((int(w * scale) - w) // 2, (int(h * scale) - h) // 2, (int(w * scale) - w) // 2 + w, (int(h * scale) - h) // 2 + h)
            )
            blurred = Image.blend(blurred, bigger, 0.5)
        out.paste(blurred.filter(ImageFilter.GaussianBlur(2)), band[:2])
    return out


def build_thumbnails(out: Path, *, rasterizer: Rasterizer, theme: OverlayTheme) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        backdrop_png = _backdrop_path(tmp)
        plain = paint_backdrop()

        def save(name: str, image: Image.Image | None) -> None:
            if image is None:
                raise RuntimeError(f"{name}: the card did not compose")
            path = out / name
            image.convert("RGB").save(path, optimize=True)
            written.append(path)

        save("none.png", plain)
        title = composition.MatchTitle(text=MATCH, info=("2026-06-27", "Production Optics"))
        card = dict(width=WIDTH, height=HEIGHT, theme=theme, rasterizer=rasterizer, backdrop=backdrop_png)
        save("title-page.png", build_card_still(title, **card))
        save("closing-card.png", build_card_still(composition.MatchTitle(text=MATCH, info=("2026-06-27",)), **card))
        slate = composition.TitleCard(text=STAGE, duration_seconds=1.5, style="slate", info=("24 rounds",))
        save("stage-card-slate.png", build_card_still(slate, **card))
        lower = composition.TitleCard(text=STAGE, duration_seconds=1.5, style="lower-third", info=("24 rounds",))
        third = build_lower_third(lower, width=WIDTH, height=HEIGHT, theme=theme, rasterizer=rasterizer)
        if third is None:
            raise RuntimeError("lower third did not compose")
        over = plain.convert("RGBA")
        over.alpha_composite(third)
        save("stage-card-lower-third.png", over)
        save(
            "summary-hold.png",
            build_summary_still(
                _sample_tile(), SHOOTER, width=WIDTH, height=HEIGHT, theme=theme, rasterizer=rasterizer, backdrop=backdrop_png
            ),
        )
        save("overlay.png", _overlay(plain, rasterizer=rasterizer, theme=theme))
        for kind in ("cut", "static", "zoom"):
            save(f"transition-{kind}.png", _transition(kind, plain))
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--theme", choices=("splitsmith", "clean"), default="splitsmith")
    args = parser.parse_args()
    with ChromiumRasterizer() as rasterizer:
        written = build_thumbnails(args.out, rasterizer=rasterizer, theme=load_theme(args.theme))
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Check three signatures before running: `backdrop_from_frame` is imported for the type only, drop the import if unused; `StageScorecard`'s field names (`hit_factor`, `alphas`, `charlies`, `deltas`, `misses` are used by `scripts/render_match_frames.py`, so they exist); `MatchTitle(text, info, duration_seconds)` and `TitleCard(text, duration_seconds, style, info)` are the dataclass fields in `composition.py:212-262`. The zoom band's crop arithmetic is what it is; if the result looks wrong in Step 6, replace the loop with `strip.filter(ImageFilter.GaussianBlur(4))` and keep the test's "three tiles differ" property.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_render_look_thumbnails.py -n0 -q && uv run ruff check scripts/render_look_thumbnails.py tests/test_render_look_thumbnails.py && uv run black --check scripts/render_look_thumbnails.py tests/test_render_look_thumbnails.py`
Expected: PASS (the registry-match test reads `lookGallery.ts` from Task 1).

- [ ] **Step 5: Generate the real thumbnails**

Run: `uv run python scripts/render_look_thumbnails.py`
Expected: ten paths printed under `src/splitsmith/ui_static/src/assets/look/`. If the rasterizer is unavailable the script raises `RasterizerUnavailableError`; install the browser it names and re-run.

- [ ] **Step 6: Look at them**

Open each PNG (the Read tool renders images). Check: the title page and closing card centre the match title with the info lines under it; the slate reads "Stage 03 . Standards" with "24 rounds"; the lower third sits bottom-left on the un-blurred scene; the summary shows the shooter, time, scoring and splits at a legible size; the overlay shows "7/18" top-left and "0.26" bottom-centre; the three transition tiles differ at the seam and the "none" tile is the plain scene. Fix what is wrong in the script, not in the PNG.

- [ ] **Step 7: Run the registry's thumbnail tests now that the files exist**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/lib/lookGallery.test.ts`
Expected: all PASS, including the two "registry shape" cases.

- [ ] **Step 8: Commit**

```bash
git add scripts/render_look_thumbnails.py tests/test_render_look_thumbnails.py src/splitsmith/ui_static/src/assets/look/
git commit -m "feat(export): Look gallery thumbnails and the script that draws them"
```

---

### Task 3: `LookGallery`, the moves, and the page

**Files:**
- Create: `src/splitsmith/ui_static/src/components/export/Seconds.tsx` (moved from the render panel)
- Create: `src/splitsmith/ui_static/src/components/export/LookGallery.tsx`
- Test: `src/splitsmith/ui_static/src/components/export/LookGallery.test.tsx`
- Modify: `components/export/LookGroup.tsx`, `CutGroup.tsx`, `DetailsGroup.tsx`, `pages/Export.tsx`, `lib/exportPresets.ts` + test, `lib/renderOptions.test.ts`
- Delete: `components/render/RenderOptionsPanel.tsx`, `components/render/RenderOptionsPanel.test.tsx`
- Modify: `pages/Export.renderOptions.test.tsx`, `Export.bare.test.tsx`, `Export.presets.test.tsx`, `Export.compareGrid.test.tsx` where they reach the old controls

**Interfaces:**
- Consumes: `LOOK_SLOTS`, `visibleSlots`, `visibleVariants`, `thumbnailUrl` (Task 1); `transitionsSupported`.
- Produces: `LookGallery({ settings, patch, busy, bareHints })` where `bareHints: Partial<Record<LookSlotId, string | null>>` supplies the per-slot bare-stage line (`summaryHold` and `overlay` today). `Seconds` keeps its signature.

- [ ] **Step 1: Move `Seconds`**

Create `components/export/Seconds.tsx` with the `Seconds` function cut verbatim from `RenderOptionsPanel.tsx` (lines 197-228, the doc comment included) and `import { Field, inputClass }` reduced to `inputClass`. Update `LookGroup.tsx`'s import to `@/components/export/Seconds`. Run `pnpm typecheck`.

- [ ] **Step 2: Write the failing gallery test**

```tsx
/**
 * The Look gallery (spec 2026-09-15 s2): tiles per slot, the selected
 * variant's parameters under its row, and only what the mode and format
 * can draw. Folds in the render panel's cases (the two-boolean title
 * page mapping, the seconds fields, the per-format rules).
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LookGallery } from "@/components/export/LookGallery";
import { DEFAULT_EXPORT_SETTINGS, type ExportSettings } from "@/lib/exportPresets";

function setup(over: Partial<ExportSettings> = {}, bareHints = {}) {
  const settings = { ...DEFAULT_EXPORT_SETTINGS, ...over };
  const patch = vi.fn();
  render(<LookGallery settings={settings} patch={patch} busy={false} bareHints={bareHints} />);
  return { settings, patch, user: userEvent.setup() };
}

const tile = (slot: string, name: string) =>
  within(screen.getByRole("radiogroup", { name: slot })).getByRole("radio", { name });

describe("LookGallery", () => {
  it("on MP4 offers every slot, each tile with its thumbnail, and the transition stays hidden", () => {
    setup({ outputFormat: "mp4" });
    for (const slot of ["Title page", "Stage card", "Closing card", "Stage summary", "Overlay"]) {
      expect(screen.getByRole("radiogroup", { name: slot })).toBeInTheDocument();
    }
    expect(screen.queryByRole("radiogroup", { name: "Transition" })).toBeNull();
    expect(tile("Title page", "None")).toBeChecked();
    expect(within(tile("Stage card", "Slate")).getByRole("img")).toHaveAttribute("src", expect.stringMatching(/stage-card-slate/));
  });

  it("on FCPXML offers the stage card, the overlay and the transition only", () => {
    setup({ outputFormat: "fcpxml" });
    expect(screen.getAllByRole("radiogroup").map((g) => g.getAttribute("aria-label"))).toEqual([
      "Stage card",
      "Overlay",
      "Transition",
    ]);
  });

  it("on FCP 7 XML offers the overlay only", () => {
    setup({ outputFormat: "fcp7xml" });
    expect(screen.getAllByRole("radiogroup").map((g) => g.getAttribute("aria-label"))).toEqual(["Overlay"]);
  });

  it("on the grid offers the cards and the grid overlay with its hold, never the summary", () => {
    setup({ mode: "compare", gridOverlay: true, gridHoldSeconds: 2 });
    expect(screen.queryByRole("radiogroup", { name: "Stage summary" })).toBeNull();
    expect(screen.queryByRole("radiogroup", { name: "Transition" })).toBeNull();
    expect(tile("Overlay", "Shot counter")).toBeChecked();
    expect(screen.getByLabelText("Grid summary hold seconds")).toHaveValue(2);
  });

  it("selecting a tile patches through the slot, keeping the rest of the options", async () => {
    const { user, patch, settings } = setup({ outputFormat: "mp4" });
    await user.click(tile("Stage card", "Lower third"));
    expect(patch).toHaveBeenCalledWith({ renderOptions: { ...settings.renderOptions, stageCardStyle: "lower-third" } });
    await user.click(tile("Title page", "Title page"));
    expect(patch).toHaveBeenLastCalledWith({ renderOptions: { ...settings.renderOptions, titlePage: true } });
    expect(settings.renderOptions.stageCardStyle).toBe("none");
  });

  it("shows the selected variant's parameters and help, and none for the off tile", async () => {
    const { user, patch } = setup({
      outputFormat: "mp4",
      renderOptions: { ...DEFAULT_EXPORT_SETTINGS.renderOptions, stageCardStyle: "slate", stageCardDurationSeconds: 1.5 },
    });
    expect(screen.getByLabelText("Stage card seconds")).toHaveValue(1.5);
    expect(screen.getByText(/on its own card before each stage/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Title page seconds")).toBeNull();
    await user.clear(screen.getByLabelText("Stage card seconds"));
    await user.type(screen.getByLabelText("Stage card seconds"), "2");
    expect(patch).toHaveBeenLastCalledWith({
      renderOptions: expect.objectContaining({ stageCardDurationSeconds: 2 }),
    });
  });

  it("the summary hold tile turns the hold on at 3 s and its seconds edit the hold", async () => {
    const { user, patch } = setup({ outputFormat: "mp4" });
    await user.click(tile("Stage summary", "Summary hold"));
    expect(patch).toHaveBeenCalledWith({ renderOptions: expect.objectContaining({ summaryHoldSeconds: 3 }) });
  });

  it("appends the bare hint to the slot's help while its variant is on", () => {
    setup(
      { outputFormat: "mp4", includeOverlay: true },
      { overlay: "Skipped on 2 stages without splits." },
    );
    expect(screen.getByText(/Skipped on 2 stages without splits\./)).toBeInTheDocument();
  });

  it("disables every tile and input while busy", () => {
    render(<LookGallery settings={{ ...DEFAULT_EXPORT_SETTINGS, outputFormat: "mp4" }} patch={vi.fn()} busy bareHints={{}} />);
    for (const r of screen.getAllByRole("radio")) expect(r).toBeDisabled();
  });

  it("never paints a coloured fill and has no primary action", () => {
    const { container } = render(
      <LookGallery settings={{ ...DEFAULT_EXPORT_SETTINGS, outputFormat: "mp4" }} patch={vi.fn()} busy={false} bareHints={{}} />,
    );
    expect(container.querySelector(".btn-primary")).toBeNull();
    expect(container.querySelector("[class*='bg-led']:not([data-tick])")).toBeNull();
  });
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/components/export/LookGallery.test.tsx`
Expected: FAIL, module not found.

- [ ] **Step 4: Write the gallery**

`components/export/LookGallery.tsx`:

```tsx
/**
 * LookGallery -- the Look group as tiles (spec 2026-09-15 s2). One row
 * per slot the mode and format can draw: a Label, a radio group of
 * tiles (thumbnail plus name, neutral outline, a tick on the selected
 * one), and under the row the selected variant's help and parameters.
 * Everything it offers comes from ``lib/lookGallery``; it owns no state
 * and knows no endpoint.
 */
import { Seconds } from "@/components/export/Seconds";
import { Label } from "@/components/ui/Label";
import type { ExportSettings } from "@/lib/exportPresets";
import { thumbnailUrl, visibleSlots, visibleVariants, type LookSlot, type LookSlotId, type LookVariant } from "@/lib/lookGallery";
import { cn } from "@/lib/utils";

export interface LookGalleryProps {
  settings: ExportSettings;
  patch: (p: Partial<ExportSettings>) => void;
  busy: boolean;
  /** Per slot, the line about the selected stages exporting without
   *  splits (``bareHint``); appended to the help while the variant is on. */
  bareHints: Partial<Record<LookSlotId, string | null>>;
}

export function LookGallery({ settings, patch, busy, bareHints }: LookGalleryProps) {
  const format = settings.mode === "compare" ? "mp4" : settings.outputFormat;
  const slots = visibleSlots(settings.mode, format);
  return (
    <>
      {slots.map((slot) => (
        <SlotRow
          key={slot.id}
          slot={slot}
          variants={visibleVariants(slot, settings.mode, format)}
          settings={settings}
          patch={patch}
          busy={busy}
          bareHint={bareHints[slot.id] ?? null}
        />
      ))}
    </>
  );
}

function SlotRow({
  slot,
  variants,
  settings,
  patch,
  busy,
  bareHint,
}: {
  slot: LookSlot;
  variants: LookVariant[];
  settings: ExportSettings;
  patch: (p: Partial<ExportSettings>) => void;
  busy: boolean;
  bareHint: string | null;
}) {
  const selectedId = slot.read(settings);
  const selected = variants.find((v) => v.id === selectedId) ?? variants[0];
  const on = selected.id !== slot.variants[0].id;
  const params = selected.params.filter((p) => !p.modes || p.modes.includes(settings.mode));
  return (
    <div className="grid grid-cols-1 gap-x-4 gap-y-1.5 border-b border-rule px-3.5 py-3 last:border-b-0 sm:grid-cols-[150px_minmax(0,1fr)]">
      <div className="sm:pt-2">
        <Label>{slot.label}</Label>
      </div>
      <div className="min-w-0">
        <div role="radiogroup" aria-label={slot.label} className="flex flex-wrap gap-2">
          {variants.map((v) => {
            const checked = v.id === selected.id;
            return (
              <button
                key={v.id}
                type="button"
                role="radio"
                aria-checked={checked}
                disabled={busy}
                onClick={() => {
                  if (!checked) patch(slot.write(settings, v.id));
                }}
                className={cn(
                  "flex w-40 flex-col gap-1.5 rounded-md border p-1.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led disabled:opacity-50",
                  checked ? "border-ink bg-surface-2" : "border-rule-strong hover:border-ink-2",
                )}
              >
                <img src={thumbnailUrl(v.thumbnail)} alt="" role="img" aria-label={v.name} className="aspect-video w-full rounded-sm bg-surface-3 object-cover" />
                <span className="inline-flex items-center gap-1.5 text-sm text-ink-2">
                  {checked ? <i data-tick aria-hidden className="size-1.5 shrink-0 rounded-full bg-ink" /> : null}
                  {v.name}
                </span>
              </button>
            );
          })}
        </div>
        {params.length > 0 ? (
          <div className="mt-2 flex flex-wrap items-center gap-3">
            {params.map((p) => (
              <Seconds
                key={p.id}
                id={`look-${slot.id}-${p.id}`}
                label={p.label}
                value={p.read(settings)}
                min={p.min}
                disabled={busy}
                onChange={(n) => patch(p.write(settings, n))}
              />
            ))}
          </div>
        ) : null}
        <div className="mt-1.5 max-w-[52ch] text-[12px] text-muted">
          {selected.help}
          {on && bareHint ? ` ${bareHint}` : ""}
        </div>
      </div>
    </div>
  );
}
```

The `text-[12px]` on the help line is the same class `components/ui/Field` uses for its help; if the lint rule rejects it outside `components/ui`, wrap the row in `Field` instead (`<Field label={slot.label} help={...}>` with the radio group and params as children) and drop the hand-rolled grid. Prefer that if it fits: it is what CutGroup does.

The `<img>` carries `aria-label={v.name}` so the tile's accessible name is the variant name (the test queries `getByRole("radio", { name })`); the caption text repeats it for sighted users.

- [ ] **Step 5: Run the gallery test**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/components/export/LookGallery.test.tsx && pnpm typecheck && pnpm exec eslint src/components/export/LookGallery.tsx`
Expected: PASS, no lint error on the help line (else switch to `Field` as above).

- [ ] **Step 6: Wire the gallery in, move transitions and the title line**

`LookGroup.tsx` becomes:

```tsx
/**
 * Look -- the cards, the overlay and the transitions as a gallery (spec
 * 2026-09-15 s2). What it offers and which mode and format can draw it
 * is ``lib/lookGallery``'s; this maps the page's bare-stage hints onto
 * the slots.
 */
import { LookGallery } from "@/components/export/LookGallery";
import { bareHint } from "@/lib/exportPlan";
import type { ExportSettings } from "@/lib/exportPresets";

export interface LookGroupProps {
  settings: ExportSettings;
  patch: (p: Partial<ExportSettings>) => void;
  busy: boolean;
  bareSelected: number;
}

export function LookGroup({ settings, patch, busy, bareSelected }: LookGroupProps) {
  const compare = settings.mode === "compare";
  return (
    <LookGallery
      settings={settings}
      patch={patch}
      busy={busy}
      bareHints={compare ? {} : { summaryHold: bareHint("summary", bareSelected), overlay: bareHint("overlay", bareSelected) }}
    />
  );
}
```

`CutGroup.tsx`: delete the `Transition` `Field` and the `TRANSITIONS` import; keep padding and `NumInput`.

`DetailsGroup.tsx`: add, after the bundle name and before the description, shown when `settings.mode === "single" && settings.outputFormat === "mp4" && (settings.renderOptions.titlePage || settings.renderOptions.closingCard)`:

```tsx
      {titleCard ? (
        <Field label="Title line" htmlFor="export-title-info" help="Under the match name on the title page and the closing card: division, level, anything.">
          <input
            id="export-title-info"
            aria-label="Title page info line"
            type="text"
            className={cn(inputClass, "max-w-md")}
            value={settings.renderOptions.titleInfo}
            disabled={busy}
            onChange={(e) => patch({ renderOptions: { ...settings.renderOptions, titleInfo: e.target.value } })}
          />
        </Field>
      ) : null}
```

with `const titleCard = renderedMp4 && (settings.renderOptions.titlePage || settings.renderOptions.closingCard);`. (The compare grid also draws the title page; its title line stays on the grid's Details in part 3 when Details opens for compare. Today Details renders only in single mode, as before.)

`pages/Export.tsx`:
- `submitBundle`: `transition_kind: transitionsSupported(outputFormat) ? transitionKind : "none"` and `transition_duration_seconds: transitionSeconds` unchanged; import `transitionsSupported` from `@/lib/renderOptions`.
- `estimateDuration(...)`: pass `transitionKind: transitionsSupported(outputFormat) ? transitionKind : "none"`.
- `summaryLines(...)`: same substitution for `transitionKind`.
- Delete the `RenderOptionsPanel` import if any remains.

`lib/exportPresets.ts` `groupSummary`: `cut` returns `${pad} ${head} / ${tail} s` only; `look` appends the transition when `s.mode === "single" && transitionsSupported(s.outputFormat) && s.transitionKind !== "none"` as `${s.transitionKind} ${s.transitionSeconds.toFixed(1)} s`. Update `exportPresets.test.ts`: the cut expectations lose ` · cut` / ` · zoom 0.5 s`; add a look case: `groupSummary({ ...DEFAULT_EXPORT_SETTINGS, transitionKind: "zoom" }, "look", ctx)` is `"zoom 0.5 s"` on FCPXML and `"No cards"` when `outputFormat: "mp4"`.

`lib/renderOptions.test.ts`: add

```ts
it("the mappers never emit a field the gallery hides for the format", () => {
  const on: RenderOptions = { ...DEFAULT_RENDER_OPTIONS, titlePage: true, closingCard: true, stageCardStyle: "slate", summaryHoldSeconds: 3 };
  for (const format of ["fcpxml", "fcp7xml", "mp4"] as const) {
    const fields = matchExportFields(on, format);
    const slots = new Set(visibleSlots("single", format).map((s) => s.id));
    expect("title_page" in fields).toBe(slots.has("titlePage"));
    expect("summary_hold_seconds" in fields).toBe(slots.has("summaryHold"));
    expect(fields.title_kind !== "none").toBe(slots.has("stageCard"));
  }
});
```

Delete `components/render/RenderOptionsPanel.tsx` and its test.

- [ ] **Step 7: Update the page tests that reached the old controls**

The old `Segmented` groups "Title page", "Stage card style", "Grid overlay", "Overlay", "Transition" and the "Title page info line" input under Look are gone. In `Export.renderOptions.test.tsx`, `Export.bare.test.tsx`, `Export.presets.test.tsx` and `Export.compareGrid.test.tsx` replace:

| old | new |
|---|---|
| `choice("Title page", "Opening")` | `tile("Title page", "Title page")` |
| `choice("Title page", "Opening + closing")` | `tile("Title page", "Title page")` then `tile("Closing card", "Closing card")` |
| `choice("Stage card style", "Slate")` | `tile("Stage card", "Slate")` |
| `choice("Grid overlay", "Counter + splits")` | `tile("Overlay", "Shot counter")` |
| `within(getByRole("group", { name: "Overlay" })).getByRole("button", { name: "Shot counter + splits" })` | `tile("Overlay", "Shot counter")` |
| `choice("Transition", ...)` | `tile("Transition", "Zoom blur")` (FCPXML only) |
| `getByLabelText("Title page info line")` | unchanged label, now under Details |
| `getByLabelText("Summary hold seconds")` | first `tile("Stage summary", "Summary hold")`, then the same label |

with one helper per file:

```ts
function tile(slot: string, name: string): HTMLElement {
  return within(screen.getByRole("radiogroup", { name: slot })).getByRole("radio", { name });
}
```

Every assertion on the request bodies stays as it is: that is the point of the exercise. `Export.bare.test.tsx`'s hint assertions now read the gallery's help line; the wording is unchanged because `bareHint` is unchanged.

Run: `cd src/splitsmith/ui_static && pnpm test && pnpm typecheck && pnpm lint`
Expected: all PASS; lint at the same warning count as main (44).

- [ ] **Step 8: Look at it**

```bash
cd src/splitsmith/ui_static && pnpm build
uv run python scripts/seed_demo_match.py ~/.claude-tmp/demo-match --media   # if not already seeded
SPLITSMITH_HOME=$HOME/.claude-tmp/demo-home uv run splitsmith ui --project ~/.claude-tmp/demo-match --skip-system-check --no-browser --port 5199
```

Screenshot the Export page at 1440 wide with a tall viewport (not Playwright's `full_page`, which remounts the shell) on: the YouTube preset with Look open (five slots, tiles with thumbnails, the selected ones ticked, the seconds under Slate and Summary hold); the Final Cut preset with Look open (stage card, overlay, transition only); the Compare grid preset on the two-shooter demo. Check the tiles read at 160 px, the row wraps at narrow widths, and no tile is red. Show the frames before calling the task done.

- [ ] **Step 9: Commit**

```bash
git add -A src/splitsmith/ui_static/src
git commit -m "feat(ui): the Look gallery replaces the render panel; transitions and the title line move"
```

---

### Task 4: Docs, review and PR

- [ ] **Step 1: CLAUDE.md**

Append to the "Export presets" section:

```markdown
The Look group is a gallery (spec s2): ``lib/lookGallery.ts`` is the one
registry of slots, variants, thumbnails, parameters and which mode and
format can draw each; ``components/export/LookGallery.tsx`` renders it
and owns nothing. A new effect is one registry entry plus one thumbnail
from ``scripts/render_look_thumbnails.py`` (Chromium once, at authoring
time; the gallery never rasterizes). ``lookGallery.test.ts`` pins the
per-format visibility table and that every committed thumbnail is
referenced; ``renderOptions.test.ts`` pins that the mappers never send a
field the registry hides. Transitions live in Look (FCPXML only) and
the title line in Details.
```

- [ ] **Step 2: Review pass**

One reviewer, the implementation report treated as unverified, with these claims:

- The request bodies for every (mode, format) the old panel tests covered are unchanged except `transition_kind`, which is now `"none"` on MP4 and FCP 7 XML. Diff the mock calls in the page tests against main.
- Every control the render panel offered is reachable through a tile or a param under the same conditions; list any that is not (the title line moved to Details on purpose).
- `visibleSlots` equals the render panel's per-format table on main (`cardsSupported`, `stageCardsSupported`) for all three formats and both surfaces.
- The overlay slot's `read` on a compare settings object reads `gridOverlay`, and its `write` never touches `includeOverlay` there (and vice versa).
- The thumbnails on disk are exactly the registry's set; open two at random and confirm they are the card they claim to be.
- For each new test, name one that would pass with the feature deleted.

- [ ] **Step 3: Verify and open the PR**

```bash
uv run pytest tests/test_render_look_thumbnails.py -n0 -q
cd src/splitsmith/ui_static && pnpm test && pnpm typecheck && pnpm lint
git push -u origin HEAD
gh pr create --title "feat(export): Look gallery with generic thumbnails" --body-file - <<'EOF'
Part 2 of the export redesign (spec docs/superpowers/specs/2026-09-15-export-presets-and-look-gallery-design.md, s2).

- `lib/lookGallery.ts`: the registry (slots, variants, thumbnails, params, per-mode / per-format visibility). The render panel's format rules moved here as data; the mappers are tested against it.
- `components/export/LookGallery.tsx` replaces `RenderOptionsPanel`; transitions moved from Cut into the gallery (FCPXML only, hidden and not sent elsewhere); the title line moved to Details.
- `scripts/render_look_thumbnails.py` draws ten 480x270 tiles from the real card builders over a painted backdrop; committed under `ui_static/src/assets/look/`. Chromium at authoring time only.

Not in this PR: the rail preview (part 3); hover on a tile does nothing yet.

Screenshots: (attach)
EOF
```

---

## Self-review

**Spec coverage (section 2):** slots and variants as a registry with thumbnail, params, modes and formats (Task 1); the format rule moves into the registry with the mappers tested against it (Tasks 1, 3); tests that every variant has a thumbnail and every `RenderOptions` field belongs to one variant (Task 1); tiles with Label, wrapping row, neutral outline and tick, params under the row, hidden slots not rendered, keyboard reachable radio group (Task 3); static 480x270 PNGs from a sibling of `render_match_frames.py` with placeholder text and a painted backdrop, transitions drawn as two half-frames (Task 2); transitions become a Look slot and Cut is padding alone (Task 3, per the spec's part-1 note). Deferred and stated: hover to the rail preview (part 3).

**Placeholder scan:** none; every code step is inline. Task 3 step 7 is a mapping table over existing test lines, with the helper given.

**Type consistency:** `LookParam.write(s, n): Partial<ExportSettings>` and `LookSlot.write(s, id): Partial<ExportSettings>` are what `LookGallery` passes to `patch`; `bareHints: Partial<Record<LookSlotId, string | null>>` matches `LookGroup`'s call; `THUMBNAILS` (Python) and `THUMBNAIL_FILES` (TS) are pinned equal by `test_file_set_matches_the_registry`; `transitionsSupported` is defined in Task 1 before its uses in Tasks 1 and 3.
