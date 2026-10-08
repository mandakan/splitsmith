/**
 * The rail preview's plain logic (spec 2026-09-15 s3): which server card
 * a Look tile previews, the request body from the form, the caption and
 * the one line per failure. Pure; ``PreviewPane`` does the fetching.
 */
import type { ExportPreviewBody, LookInfo, PreviewCard } from "@/lib/api";
import type { ExportMode } from "@/lib/exportPlan";
import { PADDING_PRESETS, type ExportSettings } from "@/lib/exportPresets";
import { LOOK_SLOTS, type LookSlot, type LookSlotId } from "@/lib/lookGallery";
import { BUILTIN_LOOKS, DEFAULT_LOOK, DEFAULT_VARIANT, lookChoiceOf, nonDefault, resolveLookChoice, type LookChoice } from "@/lib/looks";

export interface LookFocus {
  slotId: LookSlotId;
  variantId: string;
}

export const PREVIEW_WIDTH = 960;

/** The server card for a tile; the frame for an off tile or no tile;
 *  null where only the generic thumbnail can show (transitions, and the
 *  grid's match summary, which the preview route cannot draw: it previews
 *  one shooter, and the grid's card is a tile per shooter). */
export function previewCardFor(focus: LookFocus | null, mode: ExportMode = "single"): PreviewCard | null {
  if (focus === null) return "frame";
  // The Look tiles (#1246) are not in the static table: a Look previews
  // as the title page in the chosen Look.
  if (focus.slotId === "look") return "title";
  const slot = LOOK_SLOTS.find((s) => s.id === focus.slotId);
  if (!slot) return "frame";
  if (slot.id === "transition") return null;
  if (focus.variantId === slot.variants[0].id) return "frame";
  switch (slot.id) {
    case "look":
      return "title";
    case "titlePage":
      return "title";
    case "closingCard":
      return "closing";
    case "stageCard":
      return focus.variantId === "lower-third" ? "lower-third" : "slate";
    case "summaryHold":
      return "summary";
    case "matchSummary":
      return mode === "compare" ? null : "match_summary";
    case "overlay":
      return "overlay";
  }
}

const finite = (n: number, fallback: number) => (Number.isFinite(n) ? n : fallback);

/** The settings field whose variant a card previews with; null for the
 *  cards that have no template variant (the frame, the summary, the
 *  overlay). */
export function variantForCard(settings: LookChoice, card: PreviewCard): string | null {
  switch (card) {
    case "title":
      return settings.titlePageVariant;
    case "closing":
      return settings.closingCardVariant;
    case "slate":
    case "lower-third":
      return settings.stageCardVariant;
    default:
      return null;
  }
}

export function previewBody(
  settings: ExportSettings,
  card: PreviewCard,
  stageNumber: number,
  /** The bundle name field; the match cards carry it, as the export does. */
  projectName: string = "",
  /** The installed catalog: the stored Look and variant are resolved
   *  against it first, so a preset's uninstalled Look previews as the
   *  default instead of a 422 (review of #1246). */
  looks: LookInfo[] = BUILTIN_LOOKS,
  /** The export's stage selection, in order: the match summary card
   *  summarises exactly these, as the video will. */
  stageNumbers?: readonly number[],
): ExportPreviewBody {
  const body: ExportPreviewBody = {
    card,
    stage_number: stageNumber,
    width: PREVIEW_WIDTH,
    title_info: settings.renderOptions.titleInfo.trim() || null,
    title_division: settings.renderOptions.titleDivision,
    made_with: settings.renderOptions.madeWith,
    account_brand: settings.renderOptions.accountBrand,
    project_name: projectName.trim() || null,
  };
  const resolved = resolveLookChoice(looks, lookChoiceOf(settings));
  const look = nonDefault(resolved.look, DEFAULT_LOOK);
  if (look) body.look = look;
  const variant = variantForCard(resolved, card);
  if (variant !== null && variant !== DEFAULT_VARIANT) body.variant = variant;
  // The timeline pads with the form's values; the grid and the trims
  // pad with the project's own buffers, which the server defaults to.
  if (settings.mode === "single") {
    body.head_pad_seconds = finite(settings.headPad, PADDING_PRESETS.full.head);
    body.tail_pad_seconds = finite(settings.tailPad, PADDING_PRESETS.full.tail);
  }
  // A card a template draws previews moving when its template animates
  // (#1249); the server answers a still card with the PNG it always did.
  if (MOVING_CARDS.has(card)) body.motion = true;
  if (card === "match_summary" && stageNumbers) body.stage_numbers = [...stageNumbers];
  return body;
}

const MOVING_CARDS: ReadonlySet<PreviewCard> = new Set(["title", "slate", "lower-third", "closing"]);

export function previewCaption(
  focus: LookFocus | null,
  stageNumber: number,
  /** The gallery's slots (``slotsForLook``): the transition families and
   *  stings live there, not in the static table (review of #1259). */
  slots: readonly LookSlot[] = LOOK_SLOTS,
): string {
  const stage = `Stage ${String(stageNumber).padStart(2, "0")}`;
  if (focus === null) return stage;
  const slot = slots.find((s) => s.id === focus.slotId);
  const variant = slot?.variants.find((v) => v.id === focus.variantId);
  if (!slot || !variant) return stage;
  // A transition, and the match summary (the whole match), name no stage.
  if (slot.id === "transition" || (slot.id === "matchSummary" && variant.id !== "none")) return variant.name;
  return `${variant.name} · ${stage}`;
}

export function previewLine(status: number | null): string {
  if (status === 503) return "Preview needs a browser";
  if (status === 409) return "Overlay needs audited shots";
  return "No preview";
}
