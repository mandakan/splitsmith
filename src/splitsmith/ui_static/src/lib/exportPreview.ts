/**
 * The rail preview's plain logic (spec 2026-09-15 s3): which server card
 * a Look tile previews, the request body from the form, the caption and
 * the one line per failure. Pure; ``PreviewPane`` does the fetching.
 */
import type { ExportPreviewBody, PreviewCard } from "@/lib/api";
import { PADDING_PRESETS, type ExportSettings } from "@/lib/exportPresets";
import { LOOK_SLOTS, type LookSlotId } from "@/lib/lookGallery";

export interface LookFocus {
  slotId: LookSlotId;
  variantId: string;
}

export const PREVIEW_WIDTH = 960;

/** The server card for a tile; the frame for an off tile or no tile;
 *  null where only the generic thumbnail can show (transitions). */
export function previewCardFor(focus: LookFocus | null): PreviewCard | null {
  if (focus === null) return "frame";
  const slot = LOOK_SLOTS.find((s) => s.id === focus.slotId);
  if (!slot) return "frame";
  if (slot.id === "transition") return null;
  if (focus.variantId === slot.variants[0].id) return "frame";
  switch (slot.id) {
    case "titlePage":
      return "title";
    case "closingCard":
      return "closing";
    case "stageCard":
      return focus.variantId === "lower-third" ? "lower-third" : "slate";
    case "summaryHold":
      return "summary";
    case "overlay":
      return "overlay";
  }
}

const finite = (n: number, fallback: number) => (Number.isFinite(n) ? n : fallback);

export function previewBody(
  settings: ExportSettings,
  card: PreviewCard,
  stageNumber: number,
  /** The bundle name field; the match cards carry it, as the export does. */
  projectName: string = "",
): ExportPreviewBody {
  const body: ExportPreviewBody = {
    card,
    stage_number: stageNumber,
    width: PREVIEW_WIDTH,
    title_info: settings.renderOptions.titleInfo.trim() || null,
    project_name: projectName.trim() || null,
  };
  // The timeline pads with the form's values; the grid and the trims
  // pad with the project's own buffers, which the server defaults to.
  if (settings.mode === "single") {
    body.head_pad_seconds = finite(settings.headPad, PADDING_PRESETS.full.head);
    body.tail_pad_seconds = finite(settings.tailPad, PADDING_PRESETS.full.tail);
  }
  return body;
}

export function previewCaption(focus: LookFocus | null, stageNumber: number): string {
  const stage = `Stage ${String(stageNumber).padStart(2, "0")}`;
  if (focus === null) return stage;
  const slot = LOOK_SLOTS.find((s) => s.id === focus.slotId);
  const variant = slot?.variants.find((v) => v.id === focus.variantId);
  if (!slot || !variant) return stage;
  if (slot.id === "transition") return variant.name;
  return `${variant.name} · ${stage}`;
}

export function previewLine(status: number | null): string {
  if (status === 503) return "Preview needs a browser";
  if (status === 409) return "Overlay needs audited shots";
  return "No preview";
}
