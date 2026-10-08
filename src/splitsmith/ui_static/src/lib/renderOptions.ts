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
import { DEFAULT_VARIANT, nonDefault, type LookChoice } from "./looks";

export type StageCardStyle = "none" | "slate" | "lower-third";
export type OutputFormat = NonNullable<MatchExportRequestPayload["output_format"]>;

export interface RenderOptions {
  /** Open with the match title card. */
  titlePage: boolean;
  /** Free-text line under the match name (level, squad, ...). */
  titleInfo: string;
  /** The shooter's scoreboard division under the name ("Classic Major");
   *  on the grid, one "Name · Division" line per shooter. */
  titleDivision: boolean;
  /** Seconds the title page and the closing card hold for. */
  titlePageDurationSeconds: number;
  /** Close with a card that repeats the title page. */
  closingCard: boolean;
  /** "Made with splitsmith" at the bottom of the closing card. */
  madeWith: boolean;
  /** A card per stage: a slate before it, or a lower-third over its head. */
  stageCardStyle: StageCardStyle;
  /** Seconds a stage card shows for. */
  stageCardDurationSeconds: number;
  /** Seconds to hold each stage's summary after its action; 0 is off.
   *  Single-shooter export only -- the grid's hold is #705's. */
  summaryHoldSeconds: number;
  /** The match summary card after the last stage (single-shooter MP4). */
  matchSummary: boolean;
  /** Seconds the match summary holds. */
  matchSummarySeconds: number;
}

export const DEFAULT_RENDER_OPTIONS: RenderOptions = {
  titlePage: false,
  titleInfo: "",
  titleDivision: true,
  titlePageDurationSeconds: 3,
  closingCard: false,
  madeWith: true,
  stageCardStyle: "none",
  stageCardDurationSeconds: 1.5,
  summaryHoldSeconds: 0,
  matchSummary: false,
  matchSummarySeconds: 6,
};

/** The shortest hold a card can have; below this a card is a flash. */
export const MIN_CARD_SECONDS = 0.5;
/** Above this a hold is almost certainly a typo (the grid's own rule). */
export const MAX_HOLD_SECONDS = 30;

/** The match cards (title page, closing card) and the summary hold exist
 *  only in the rendered MP4. */
export function cardsSupported(outputFormat: OutputFormat | undefined): boolean {
  return outputFormat === "mp4";
}

/** The per-stage card predates the match cards (#196) and reaches FCPXML
 *  too, as a Basic Title; only the FCP 7 XML has nowhere to put it. */
export function stageCardsSupported(outputFormat: OutputFormat | undefined): boolean {
  return outputFormat !== "fcp7xml";
}

/** The FCPXML carries transitions as FCP effects; both MP4 renderers
 *  draw the xfade kinds on a boundary segment (#1244). The FCP 7 XML
 *  records an "ignored" anomaly for one. */
export function transitionsSupported(
  outputFormat: OutputFormat | undefined,
  _mode: "single" | "grid" = "single",
): boolean {
  return outputFormat === "fcpxml" || outputFormat === "mp4";
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
      "title_page"
      | "title_info"
      | "title_division"
      | "title_page_duration_seconds"
      | "closing_card"
      | "made_with"
      | "summary_hold_seconds"
      | "match_summary"
      | "match_summary_seconds"
      | "title_page_variant"
      | "stage_card_variant"
      | "closing_card_variant"
    >
  >;

/** The request-body fields for the single-shooter match export. The
 *  per-stage card is `title_kind` there and reaches the XML formats too
 *  (FCPXML draws it as a Basic Title); everything else is MP4-only and
 *  omitted for any other format. */
export function matchExportFields(
  options: RenderOptions,
  outputFormat: OutputFormat | undefined,
  /** The resolved Look choice (#1246); omitted, no variant field is sent. */
  look?: LookChoice,
): MatchExportCardFields {
  const fields: MatchExportCardFields = {
    title_kind: stageCardsSupported(outputFormat) ? options.stageCardStyle : "none",
    title_duration_seconds: clampSeconds(options.stageCardDurationSeconds, MIN_CARD_SECONDS),
  };
  const stageVariant = look && nonDefault(look.stageCardVariant, DEFAULT_VARIANT);
  if (stageVariant && stageCardsSupported(outputFormat)) fields.stage_card_variant = stageVariant;
  if (!cardsSupported(outputFormat)) return fields;
  return {
    ...fields,
    title_page: options.titlePage,
    title_info: options.titleInfo.trim() || null,
    title_division: options.titleDivision,
    title_page_duration_seconds: clampSeconds(options.titlePageDurationSeconds, MIN_CARD_SECONDS),
    closing_card: options.closingCard,
    made_with: options.madeWith,
    summary_hold_seconds: clampSeconds(options.summaryHoldSeconds, 0),
    match_summary: options.matchSummary,
    match_summary_seconds: clampSeconds(options.matchSummarySeconds, MIN_CARD_SECONDS),
    ...variantFields(look, ["title_page_variant", "closing_card_variant"]),
  };
}

/** The request-body fields for the compare grid, which is always a
 *  rendered MP4. The summary hold is not the grid's (#705); the match
 *  summary is, as a tile per shooter. */
export function gridExportFields(
  options: RenderOptions,
  /** The resolved Look choice (#1246); omitted, no variant field is sent. */
  look?: LookChoice,
): Pick<
  CompareGridRequestPayload,
  | "stage_titles"
  | "title_duration_seconds"
  | "title_page"
  | "title_info"
  | "title_division"
  | "title_page_duration_seconds"
  | "closing_card"
  | "made_with"
  | "match_summary"
  | "match_summary_seconds"
  | "title_page_variant"
  | "stage_card_variant"
  | "closing_card_variant"
> {
  return {
    stage_titles: options.stageCardStyle,
    title_duration_seconds: clampSeconds(options.stageCardDurationSeconds, MIN_CARD_SECONDS),
    title_page: options.titlePage,
    title_info: options.titleInfo.trim() || null,
    title_division: options.titleDivision,
    title_page_duration_seconds: clampSeconds(options.titlePageDurationSeconds, MIN_CARD_SECONDS),
    closing_card: options.closingCard,
    made_with: options.madeWith,
    match_summary: options.matchSummary,
    match_summary_seconds: clampSeconds(options.matchSummarySeconds, MIN_CARD_SECONDS),
    ...variantFields(look, ["title_page_variant", "stage_card_variant", "closing_card_variant"]),
  };
}

const VARIANT_SOURCE = {
  title_page_variant: "titlePageVariant",
  stage_card_variant: "stageCardVariant",
  closing_card_variant: "closingCardVariant",
} as const;

/** The named variant fields of ``look`` that are not the default. */
function variantFields(
  look: LookChoice | undefined,
  names: readonly (keyof typeof VARIANT_SOURCE)[],
): Partial<Record<keyof typeof VARIANT_SOURCE, string>> {
  const out: Partial<Record<keyof typeof VARIANT_SOURCE, string>> = {};
  if (!look) return out;
  for (const name of names) {
    const value = nonDefault(look[VARIANT_SOURCE[name]], DEFAULT_VARIANT);
    if (value) out[name] = value;
  }
  return out;
}

/** True when any card or hold is on -- what a summary line or a "reset"
 *  affordance keys off. */
export function anyRenderOptionOn(options: RenderOptions): boolean {
  return (
    options.titlePage ||
    options.closingCard ||
    options.stageCardStyle !== "none" ||
    options.summaryHoldSeconds > 0 ||
    options.matchSummary
  );
}

/** The summary rail's one line for the cards: what is on, in render
 *  order, or `null` when nothing is. `surface` decides whether the
 *  summary hold counts (the grid has none) and `outputFormat` whether
 *  the match cards can exist at all (MP4 only), so the line never names
 *  a card the export will not draw. */
export function describeRenderOptions(
  options: RenderOptions,
  surface: "single" | "grid",
  outputFormat: OutputFormat | undefined,
): string | null {
  const parts: string[] = [];
  const cards = cardsSupported(outputFormat);
  if (cards && options.titlePage) parts.push("title page");
  if (stageCardsSupported(outputFormat) && options.stageCardStyle !== "none") {
    parts.push(options.stageCardStyle === "slate" ? "slate" : "lower third");
  }
  if (cards && surface === "single" && options.summaryHoldSeconds > 0) {
    parts.push(`summary ${clampSeconds(options.summaryHoldSeconds, 0)} s`);
  }
  if (cards && options.matchSummary) parts.push("match summary");
  if (cards && options.closingCard) parts.push("closing");
  return parts.length > 0 ? parts.join(" · ") : null;
}

/** Seconds the cards add to a timeline of `stageCount` stages: a slate
 *  per stage, the title page, the match summary, the closing card and, on
 *  the single-shooter export, one summary hold per stage. A lower-third rides the
 *  stage's own head and adds nothing. Mirrors what the two renderers
 *  put on the spine; the estimate is a status line, not a promise. */
export function renderOptionsSeconds(
  options: RenderOptions,
  stageCount: number,
  surface: "single" | "grid",
  outputFormat: OutputFormat | undefined,
): number {
  if (stageCount <= 0) return 0;
  let seconds = 0;
  if (stageCardsSupported(outputFormat) && options.stageCardStyle === "slate") {
    seconds += clampSeconds(options.stageCardDurationSeconds, MIN_CARD_SECONDS) * stageCount;
  }
  if (!cardsSupported(outputFormat)) return seconds;
  const titleSeconds = clampSeconds(options.titlePageDurationSeconds, MIN_CARD_SECONDS);
  if (options.titlePage) seconds += titleSeconds;
  if (options.closingCard) seconds += titleSeconds;
  if (surface === "single") seconds += clampSeconds(options.summaryHoldSeconds, 0) * stageCount;
  if (options.matchSummary) seconds += clampSeconds(options.matchSummarySeconds, MIN_CARD_SECONDS);
  return seconds;
}
