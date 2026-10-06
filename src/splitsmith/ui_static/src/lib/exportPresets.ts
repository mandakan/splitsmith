/**
 * The preset half of the Export form (spec 2026-09-15 s1). Pure.
 *
 * ``ExportSettings`` is every recurring field the page holds, in one
 * object, so a preset can be applied and compared without touching
 * sixteen setters. Two of its members carry a match-specific field the
 * body never stores: ``renderOptions.titleInfo`` and
 * ``uploadOptions.publishAt``. ``applyBody`` keeps them; ``settingsToBody``
 * drops them; ``isDirty`` therefore ignores them.
 */
import type { ExportPresetBody, GridFreeCell, OverlayCodec } from "@/lib/api";
import { DEFAULT_CAM_OPTIONS, fromPipLayout, type CamOptions } from "@/lib/camOptions";
import type { ExportMode } from "@/lib/exportPlan";
import {
  DEFAULT_RENDER_OPTIONS,
  describeRenderOptions,
  transitionsSupported,
  type OutputFormat,
  type RenderOptions,
} from "@/lib/renderOptions";
import { DEFAULT_UPLOAD_OPTIONS, type UploadFormOptions } from "@/lib/youtubeRows";

export type PaddingPreset = ExportPresetBody["padding_preset"];
export type TransitionKind = ExportPresetBody["transition_kind"];
export type CanvasId = ExportPresetBody["canvas"];

export const PADDING_PRESETS: Record<Exclude<PaddingPreset, "custom">, { label: string; head: number; tail: number }> =
  {
    full: { label: "Full", head: 5.0, tail: 5.0 },
    action: { label: "Action", head: 0.5, tail: 1.0 },
    highlight: { label: "Highlight", head: 1.5, tail: 2.0 },
  };

export const TRANSITIONS: { value: TransitionKind; label: string }[] = [
  { value: "none", label: "Hard cut" },
  { value: "static", label: "Static frame" },
  { value: "zoom", label: "Zoom blur" },
  { value: "fade", label: "Fade" },
  { value: "fadeblack", label: "Fade through black" },
  { value: "dissolve", label: "Dissolve" },
  { value: "slideleft", label: "Slide left" },
  { value: "slideright", label: "Slide right" },
  { value: "circleopen", label: "Circle open" },
  { value: "zoomin", label: "Zoom in" },
  { value: "hblur", label: "Horizontal blur" },
  { value: "smoothleft", label: "Smooth left" },
  { value: "wipeleft", label: "Wipe left" },
];

export const FORMAT_LABELS: Record<OutputFormat, string> = { fcpxml: "FCPXML", fcp7xml: "FCP 7 XML", mp4: "MP4" };

/** The sentinel id the API creates under, and the row's "edited" state. */
export const NEW_PRESET_ID = "new";
export const CUSTOM = "custom";
export const LAST_USED_KEY = "splitsmith.export.lastUsed";
/** Last-used for the request a phone sends a desktop to render. Kept
 *  apart from the page's own: that form only ever renders a YouTube MP4,
 *  and an FCPXML look remembered from a hosted-native match would ride
 *  into it with every card off. */
export const DESKTOP_LAST_USED_KEY = "splitsmith.export.lastUsed.desktopRender";
export const YOUTUBE_PRESET_ID = "builtin:youtube";

/** Which form the page is: its own export, or a request the desktop renders. */
export type PresetContext = "own" | "desktop";

function lastUsedKey(context: PresetContext): string {
  return context === "desktop" ? DESKTOP_LAST_USED_KEY : LAST_USED_KEY;
}

/** Whether ``body`` is something the desktop can render for a phone: the
 *  single-shooter match video, as an MP4. */
export function rendersAsDesktopRequest(body: ExportPresetBody): boolean {
  return body.mode === "single" && body.output_format === "mp4";
}

/** The format of the newest match export in a shooter's history (runs
 *  are newest first), or null when there is none. Per-stage runs say
 *  nothing about the match video and are skipped. */
export function lastMatchFormat(runs: { kind: string; formats: string[] }[]): OutputFormat | null {
  const run = runs.find((r) => r.kind === "match");
  const format = run?.formats.find((f): f is OutputFormat => f === "mp4" || f === "fcpxml" || f === "fcp7xml");
  return format ?? null;
}

/** The preset a page with nothing remembered starts on. A desktop
 *  request starts on the YouTube match video, or the first preset it can
 *  render. The page's own export follows the shooter's newest match
 *  export: a timeline format starts on the first preset writing it, or
 *  the first timeline preset (the Final Cut bundle), anything else, a first export included, on the
 *  YouTube match video. */
export function startingPreset<T extends { preset_id: string; body: ExportPresetBody }>(
  presets: T[],
  context: PresetContext,
  lastFormat: OutputFormat | null = null,
): T | null {
  const youtube = presets.find((p) => p.preset_id === YOUTUBE_PRESET_ID);
  if (context === "desktop") return youtube ?? presets.find((p) => rendersAsDesktopRequest(p.body)) ?? null;
  if (lastFormat === "fcpxml" || lastFormat === "fcp7xml") {
    const timelines = presets.filter((p) => p.body.mode === "single" && p.body.output_format !== "mp4");
    const timeline = timelines.find((p) => p.body.output_format === lastFormat) ?? timelines[0];
    if (timeline) return timeline;
  }
  return youtube ?? presets[0] ?? null;
}

export interface ExportSettings {
  mode: ExportMode;
  outputFormat: OutputFormat;
  overlayCodec: OverlayCodec;
  canvas: CanvasId;
  camOptions: CamOptions;
  youtube: boolean;
  paddingPreset: PaddingPreset;
  headPad: number;
  tailPad: number;
  transitionKind: TransitionKind;
  transitionSeconds: number;
  /** ``titleInfo`` inside is match-specific and never stored. */
  renderOptions: RenderOptions;
  includeOverlay: boolean;
  gridOverlay: boolean;
  gridHoldSeconds: number;
  gridFreeCell: GridFreeCell;
  /** ``publishAt`` inside is match-specific and never stored. */
  uploadOptions: UploadFormOptions;
}

export const DEFAULT_EXPORT_SETTINGS: ExportSettings = {
  mode: "single",
  outputFormat: "fcpxml",
  overlayCodec: "auto",
  canvas: "uhd",
  camOptions: DEFAULT_CAM_OPTIONS,
  youtube: false,
  paddingPreset: "full",
  headPad: PADDING_PRESETS.full.head,
  tailPad: PADDING_PRESETS.full.tail,
  transitionKind: "none",
  transitionSeconds: 0.5,
  renderOptions: DEFAULT_RENDER_OPTIONS,
  includeOverlay: false,
  gridOverlay: false,
  gridHoldSeconds: 0,
  gridFreeCell: "blank",
  uploadOptions: DEFAULT_UPLOAD_OPTIONS,
};

/** A seconds field being edited is NaN, and a stored body may carry
 *  null where JSON dropped one; neither is a number a preset or the
 *  summary line can use. */
function finite(n: unknown, fallback: number): number {
  return typeof n === "number" && Number.isFinite(n) ? n : fallback;
}

const D = DEFAULT_RENDER_OPTIONS;

export function settingsToBody(s: ExportSettings): ExportPresetBody {
  return {
    mode: s.mode,
    output_format: s.outputFormat,
    overlay_codec: s.overlayCodec,
    canvas: s.canvas,
    include_secondaries: s.camOptions.includeSecondaries,
    pip_layout: "stacked",
    main_camera: s.camOptions.mainCamera,
    inset_camera: s.camOptions.insetCamera,
    inset_corner: s.camOptions.insetCorner,
    inset_size: s.camOptions.insetSize,
    youtube_preset: s.youtube,
    padding_preset: s.paddingPreset,
    head_pad_seconds: finite(s.headPad, PADDING_PRESETS.full.head),
    tail_pad_seconds: finite(s.tailPad, PADDING_PRESETS.full.tail),
    transition_kind: s.transitionKind,
    transition_seconds: finite(s.transitionSeconds, 0.5),
    title_page: s.renderOptions.titlePage,
    title_page_seconds: finite(s.renderOptions.titlePageDurationSeconds, D.titlePageDurationSeconds),
    title_division: s.renderOptions.titleDivision,
    closing_card: s.renderOptions.closingCard,
    stage_card_style: s.renderOptions.stageCardStyle,
    stage_card_seconds: finite(s.renderOptions.stageCardDurationSeconds, D.stageCardDurationSeconds),
    summary_hold_seconds: finite(s.renderOptions.summaryHoldSeconds, D.summaryHoldSeconds),
    overlay: s.includeOverlay,
    grid_overlay: s.gridOverlay,
    grid_hold_seconds: finite(s.gridHoldSeconds, 0),
    grid_free_cell: s.gridFreeCell,
    upload_after_render: s.uploadOptions.enabled,
    upload_privacy: s.uploadOptions.privacy,
    upload_playlist: s.uploadOptions.playlist,
    upload_playlist_id: s.uploadOptions.playlistId,
    upload_notify: s.uploadOptions.notifySubscribers,
  };
}

/** Write a body into the settings, keeping the match-specific fields. */
export function applyBody(s: ExportSettings, body: ExportPresetBody): ExportSettings {
  return {
    mode: body.mode,
    outputFormat: body.output_format,
    overlayCodec: body.overlay_codec,
    canvas: body.canvas,
    camOptions: fromPipLayout(
      {
        includeSecondaries: body.include_secondaries,
        pipLayout: "stacked",
        mainCamera: body.main_camera ?? DEFAULT_CAM_OPTIONS.mainCamera,
        insetCamera: body.inset_camera ?? null,
        insetCorner: body.inset_corner ?? DEFAULT_CAM_OPTIONS.insetCorner,
        insetSize: body.inset_size ?? DEFAULT_CAM_OPTIONS.insetSize,
      },
      body.pip_layout,
    ),
    youtube: body.youtube_preset,
    paddingPreset: body.padding_preset,
    headPad: finite(body.head_pad_seconds, PADDING_PRESETS.full.head),
    tailPad: finite(body.tail_pad_seconds, PADDING_PRESETS.full.tail),
    transitionKind: body.transition_kind,
    transitionSeconds: finite(body.transition_seconds, 0.5),
    renderOptions: {
      ...s.renderOptions,
      titlePage: body.title_page,
      titlePageDurationSeconds: finite(body.title_page_seconds, D.titlePageDurationSeconds),
      titleDivision: body.title_division ?? D.titleDivision,
      closingCard: body.closing_card,
      stageCardStyle: body.stage_card_style,
      stageCardDurationSeconds: finite(body.stage_card_seconds, D.stageCardDurationSeconds),
      summaryHoldSeconds: finite(body.summary_hold_seconds, D.summaryHoldSeconds),
    },
    includeOverlay: body.overlay,
    gridOverlay: body.grid_overlay,
    gridHoldSeconds: finite(body.grid_hold_seconds, 0),
    gridFreeCell: body.grid_free_cell ?? "blank",
    uploadOptions: {
      ...s.uploadOptions,
      enabled: body.upload_after_render,
      privacy: body.upload_privacy,
      playlist: body.upload_playlist,
      playlistId: body.upload_playlist_id,
      notifySubscribers: body.upload_notify,
    },
  };
}

/** Field-wise equality over the body's own keys; ``schema_version`` and
 *  anything the SPA does not know are ignored on both sides. */
export function bodiesEqual(a: ExportPresetBody, b: ExportPresetBody): boolean {
  const keys = Object.keys(settingsToBody(DEFAULT_EXPORT_SETTINGS)) as (keyof ExportPresetBody)[];
  return keys.every((k) => a[k] === b[k]);
}

export function isDirty(s: ExportSettings, body: ExportPresetBody): boolean {
  return !bodiesEqual(settingsToBody(s), body);
}

export type SettingsGroup = "output" | "cut" | "look";

export interface SummaryContext {
  /** Synced secondary cameras on the selection (the cams line shows only with some). */
  secondaryCount: number;
}

const CODEC_LABELS: Record<OverlayCodec, string> = {
  auto: "auto codec",
  "hevc-alpha": "HEVC",
  "prores-4444": "ProRes 4444",
};

/** The one line a closed group shows in its header. */
export function groupSummary(s: ExportSettings, group: SettingsGroup, ctx: SummaryContext): string {
  switch (group) {
    case "output": {
      if (s.mode === "trims") return "Lossless trims";
      if (s.mode === "compare") return s.canvas === "hd" ? "1080p" : "4K";
      const parts = [FORMAT_LABELS[s.outputFormat]];
      if (s.outputFormat === "mp4" && s.youtube) parts.push("YouTube preset");
      if (s.includeOverlay) parts.push(CODEC_LABELS[s.overlayCodec]);
      if (ctx.secondaryCount > 0) {
        parts.push(s.camOptions.includeSecondaries ? `${ctx.secondaryCount} cams` : "primary only");
      }
      return parts.join(" · ");
    }
    case "cut": {
      const pad = s.paddingPreset === "custom" ? "Custom" : PADDING_PRESETS[s.paddingPreset].label;
      const head = finite(s.headPad, PADDING_PRESETS.full.head).toFixed(1);
      const tail = finite(s.tailPad, PADDING_PRESETS.full.tail).toFixed(1);
      return `${pad} ${head} / ${tail} s`;
    }
    case "look": {
      const grid = s.mode === "compare";
      const parts: string[] = [];
      const cards = describeRenderOptions(s.renderOptions, grid ? "grid" : "single", grid ? "mp4" : s.outputFormat);
      if (cards) parts.push(cards);
      if (grid ? s.gridOverlay : s.includeOverlay) parts.push("overlay");
      if (!grid && transitionsSupported(s.outputFormat) && s.transitionKind !== "none") {
        parts.push(`${s.transitionKind} ${finite(s.transitionSeconds, 0.5).toFixed(1)} s`);
      }
      return parts.length > 0 ? parts.join(" · ") : "No cards";
    }
  }
}

export interface LastUsed {
  body: ExportPresetBody;
  presetId: string | null;
}

/** The subset of ``Storage`` the page needs; tests pass a Map. */
export interface KeyValueStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export function saveLastUsed(
  storage: KeyValueStorage,
  s: ExportSettings,
  presetId: string | null,
  context: PresetContext = "own",
): void {
  try {
    storage.setItem(lastUsedKey(context), JSON.stringify({ body: settingsToBody(s), presetId }));
  } catch {
    // Private mode or blocked storage: the page works without it.
  }
}

export function loadLastUsed(storage: KeyValueStorage, context: PresetContext = "own"): LastUsed | null {
  try {
    const raw = storage.getItem(lastUsedKey(context));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<LastUsed>;
    if (!parsed || typeof parsed !== "object" || !parsed.body || typeof parsed.body !== "object") return null;
    // Fill anything a newer field added since the entry was written.
    const body = { ...settingsToBody(DEFAULT_EXPORT_SETTINGS), ...parsed.body };
    return { body, presetId: typeof parsed.presetId === "string" ? parsed.presetId : null };
  } catch {
    return null;
  }
}
