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
  MIN_CARD_SECONDS,
  stageCardsSupported,
  transitionsSupported,
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

type RenderPatch = Partial<ExportSettings["renderOptions"]>;

const render = (s: ExportSettings, patch: RenderPatch): Partial<ExportSettings> => ({
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

const transitionSeconds: LookParam = {
  id: "transition-seconds",
  label: "Transition seconds",
  min: 0.1,
  read: (s) => s.transitionSeconds,
  write: (_s, n) => ({ transitionSeconds: n }),
};

const none = (help: string, modes: ExportMode[], formats: OutputFormat[]): LookVariant => ({
  id: "none",
  name: "None",
  thumbnail: "none.png",
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
      none("No card before the first stage.", ALL_MODES, MP4),
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
      none("Stages follow each other with no card.", ALL_MODES, STAGE_CARD_FORMATS),
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
      none("The video ends on the last stage.", ALL_MODES, MP4),
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
      none("The next stage follows the last shot.", ["single"], MP4),
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
      none("No counters on the footage; the FCPXML still carries shot markers.", ALL_MODES, ALL_FORMATS),
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
            write: (_s, n) => ({ gridHoldSeconds: n }),
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
        params: [transitionSeconds],
        modes: ["single"],
        formats: TRANSITION_FORMATS,
      },
      {
        id: "zoom",
        name: "Zoom blur",
        thumbnail: "transition-zoom.png",
        help: "Zooms and blurs out of a stage and into the next.",
        params: [transitionSeconds],
        modes: ["single"],
        formats: TRANSITION_FORMATS,
      },
    ],
    read: (s) => (s.transitionKind === "none" ? "cut" : s.transitionKind),
    write: (_s, id) => ({ transitionKind: id === "cut" ? "none" : (id as ExportSettings["transitionKind"]) }),
  },
];

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

const THUMBNAILS = import.meta.glob("../assets/look/*.png", { eager: true, import: "default" }) as Record<
  string,
  string
>;

/** The bundled URL for a thumbnail file name; the glob keeps the images
 *  in the build without a runtime fetch of anything but the PNG. */
export function thumbnailUrl(file: string): string {
  const hit = Object.entries(THUMBNAILS).find(([path]) => path.endsWith(`/${file}`));
  return hit ? hit[1] : "";
}
