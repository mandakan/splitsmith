/**
 * The generated-card and stage-summary knobs of a rendered MP4 (#973,
 * #972), as one piece of UI state, and the pure mappers from that state
 * to the request bodies the two export endpoints take.
 *
 * Two endpoints, two spellings: the single-shooter match export names the
 * per-stage card `title_kind` (it predates the cards, #196) and the
 * compare grid names it `stage_titles`. This module is the one place
 * that difference is written down; the panel never sees it.
 *
 * Only the rendered MP4 draws any of this. On the XML formats the server
 * records an "ignored" anomaly for every card field it receives, so the
 * mapper sends only what the chosen format can honour rather than
 * making the user read a note about a control they never touched.
 */

import type { CompareGridRequestPayload, MatchExportRequestPayload } from "./api";

export type StageCardStyle = "none" | "slate" | "lower-third";
export type OutputFormat = NonNullable<MatchExportRequestPayload["output_format"]>;

export interface RenderOptions {
  /** Open with the match title card. */
  titlePage: boolean;
  /** Free-text line under the match name (division, level, ...). */
  titleInfo: string;
  /** Seconds the title page and the closing card hold for. */
  titlePageDurationSeconds: number;
  /** Close with a card that repeats the title page. */
  closingCard: boolean;
  /** A card per stage: a slate before it, or a lower-third over its head. */
  stageCardStyle: StageCardStyle;
  /** Seconds a stage card shows for. */
  stageCardDurationSeconds: number;
  /** Seconds to hold each stage's summary after its action; 0 is off.
   *  Single-shooter export only -- the grid's hold is #705's. */
  summaryHoldSeconds: number;
}

export const DEFAULT_RENDER_OPTIONS: RenderOptions = {
  titlePage: false,
  titleInfo: "",
  titlePageDurationSeconds: 3,
  closingCard: false,
  stageCardStyle: "none",
  stageCardDurationSeconds: 1.5,
  summaryHoldSeconds: 0,
};

/** The shortest hold a card can have; below this a card is a flash. */
export const MIN_CARD_SECONDS = 0.5;
/** Above this a hold is almost certainly a typo (the grid's own rule). */
export const MAX_HOLD_SECONDS = 30;

/** Generated cards and the summary hold exist only in the rendered MP4. */
export function cardsSupported(outputFormat: OutputFormat | undefined): boolean {
  return outputFormat === "mp4";
}

/** Clamp a seconds field into its sane range; NaN and blanks become the
 *  floor rather than a request the server rejects. */
export function clampSeconds(value: number, floor: number): number {
  if (!Number.isFinite(value)) return floor;
  return Math.min(MAX_HOLD_SECONDS, Math.max(floor, value));
}

/** The card / summary slice of the single-shooter request body. The
 *  MP4-only fields are optional because the mapper omits them for the
 *  XML formats. */
export type MatchExportCardFields = Pick<
  MatchExportRequestPayload,
  "title_kind" | "title_duration_seconds"
> &
  Partial<
    Pick<
      MatchExportRequestPayload,
      "title_page" | "title_info" | "title_page_duration_seconds" | "closing_card" | "summary_hold_seconds"
    >
  >;

/** The request-body fields for the single-shooter match export. The
 *  per-stage card is `title_kind` there and reaches the XML formats too
 *  (FCPXML draws it as a Basic Title); everything else is MP4-only and
 *  omitted for any other format. */
export function matchExportFields(
  options: RenderOptions,
  outputFormat: OutputFormat | undefined,
): MatchExportCardFields {
  const fields: MatchExportCardFields = {
    title_kind: options.stageCardStyle,
    title_duration_seconds: clampSeconds(options.stageCardDurationSeconds, MIN_CARD_SECONDS),
  };
  if (!cardsSupported(outputFormat)) return fields;
  return {
    ...fields,
    title_page: options.titlePage,
    title_info: options.titleInfo.trim() || null,
    title_page_duration_seconds: clampSeconds(options.titlePageDurationSeconds, MIN_CARD_SECONDS),
    closing_card: options.closingCard,
    summary_hold_seconds: clampSeconds(options.summaryHoldSeconds, 0),
  };
}

/** The request-body fields for the compare grid, which is always a
 *  rendered MP4. The summary hold is not the grid's (#705). */
export function gridExportFields(
  options: RenderOptions,
): Pick<
  CompareGridRequestPayload,
  | "stage_titles"
  | "title_duration_seconds"
  | "title_page"
  | "title_info"
  | "title_page_duration_seconds"
  | "closing_card"
> {
  return {
    stage_titles: options.stageCardStyle,
    title_duration_seconds: clampSeconds(options.stageCardDurationSeconds, MIN_CARD_SECONDS),
    title_page: options.titlePage,
    title_info: options.titleInfo.trim() || null,
    title_page_duration_seconds: clampSeconds(options.titlePageDurationSeconds, MIN_CARD_SECONDS),
    closing_card: options.closingCard,
  };
}

/** True when any card or hold is on -- what a summary line or a "reset"
 *  affordance keys off. */
export function anyRenderOptionOn(options: RenderOptions): boolean {
  return (
    options.titlePage ||
    options.closingCard ||
    options.stageCardStyle !== "none" ||
    options.summaryHoldSeconds > 0
  );
}
