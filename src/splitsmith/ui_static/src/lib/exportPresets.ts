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
import type { ExportPresetBody, OverlayCodec } from "@/lib/api";
import { DEFAULT_CAM_OPTIONS, type CamOptions } from "@/lib/camOptions";
import type { ExportMode } from "@/lib/exportPlan";
import {
  DEFAULT_RENDER_OPTIONS,
  describeRenderOptions,
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
];

export const FORMAT_LABELS: Record<OutputFormat, string> = { fcpxml: "FCPXML", fcp7xml: "FCP 7 XML", mp4: "MP4" };

/** The sentinel id the API creates under, and the row's "edited" state. */
export const NEW_PRESET_ID = "new";
export const CUSTOM = "custom";
export const LAST_USED_KEY = "splitsmith.export.lastUsed";

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
  uploadOptions: DEFAULT_UPLOAD_OPTIONS,
};

export function settingsToBody(s: ExportSettings): ExportPresetBody {
  return {
    mode: s.mode,
    output_format: s.outputFormat,
    overlay_codec: s.overlayCodec,
    canvas: s.canvas,
    include_secondaries: s.camOptions.includeSecondaries,
    pip_layout: s.camOptions.pipLayout,
    youtube_preset: s.youtube,
    padding_preset: s.paddingPreset,
    head_pad_seconds: s.headPad,
    tail_pad_seconds: s.tailPad,
    transition_kind: s.transitionKind,
    transition_seconds: s.transitionSeconds,
    title_page: s.renderOptions.titlePage,
    title_page_seconds: s.renderOptions.titlePageDurationSeconds,
    closing_card: s.renderOptions.closingCard,
    stage_card_style: s.renderOptions.stageCardStyle,
    stage_card_seconds: s.renderOptions.stageCardDurationSeconds,
    summary_hold_seconds: s.renderOptions.summaryHoldSeconds,
    overlay: s.includeOverlay,
    grid_overlay: s.gridOverlay,
    grid_hold_seconds: s.gridHoldSeconds,
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
    camOptions: { includeSecondaries: body.include_secondaries, pipLayout: body.pip_layout },
    youtube: body.youtube_preset,
    paddingPreset: body.padding_preset,
    headPad: body.head_pad_seconds,
    tailPad: body.tail_pad_seconds,
    transitionKind: body.transition_kind,
    transitionSeconds: body.transition_seconds,
    renderOptions: {
      ...s.renderOptions,
      titlePage: body.title_page,
      titlePageDurationSeconds: body.title_page_seconds,
      closingCard: body.closing_card,
      stageCardStyle: body.stage_card_style,
      stageCardDurationSeconds: body.stage_card_seconds,
      summaryHoldSeconds: body.summary_hold_seconds,
    },
    includeOverlay: body.overlay,
    gridOverlay: body.grid_overlay,
    gridHoldSeconds: body.grid_hold_seconds,
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
      const transition =
        s.transitionKind === "none" ? "cut" : `${s.transitionKind} ${s.transitionSeconds.toFixed(1)} s`;
      return `${pad} ${s.headPad.toFixed(1)} / ${s.tailPad.toFixed(1)} s · ${transition}`;
    }
    case "look": {
      const grid = s.mode === "compare";
      const parts: string[] = [];
      const cards = describeRenderOptions(s.renderOptions, grid ? "grid" : "single", grid ? "mp4" : s.outputFormat);
      if (cards) parts.push(cards);
      if (grid ? s.gridOverlay : s.includeOverlay) parts.push("overlay");
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

export function saveLastUsed(storage: KeyValueStorage, s: ExportSettings, presetId: string | null): void {
  try {
    storage.setItem(LAST_USED_KEY, JSON.stringify({ body: settingsToBody(s), presetId }));
  } catch {
    // Private mode or blocked storage: the page works without it.
  }
}

export function loadLastUsed(storage: KeyValueStorage): LastUsed | null {
  try {
    const raw = storage.getItem(LAST_USED_KEY);
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
