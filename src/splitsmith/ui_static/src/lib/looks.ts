/**
 * The pure reader of the Looks catalog (``GET /api/looks``, spec
 * 2026-10-06 s4, #1246): which Look and which template variant the
 * settings' stored names resolve to against what is installed, so a
 * preset saved on another machine (or with a Look since deleted) falls
 * back to the default instead of a 422, and a stored variant the chosen
 * Look lacks is sent as ``default`` rather than a name the server would
 * warn about. ``useLooks`` fetches the catalog; everything here is data.
 */
import { scopeRequestPath, type LookInfo, type LookVariantInfo } from "@/lib/api";
import type { ExportSettings } from "@/lib/exportPresets";

export const DEFAULT_LOOK = "splitsmith";
export const DEFAULT_VARIANT = "default";

/** A request carries a Look field only when it is not the default, so
 *  an untouched form sends the body it always sent and the server's
 *  own defaults (the ``card_variant`` knob, the default Look) apply. */
export const nonDefault = (value: string, fallback: string): string | undefined =>
  value === fallback ? undefined : value;

/** The settings fields a request's Look choice is resolved from. */
export interface LookChoice {
  look: string;
  titlePageVariant: string;
  stageCardVariant: string;
  closingCardVariant: string;
}

/** The card slots the catalog names; ``stage_card`` is the page's one
 *  choice for both the slate and the lower third. */
export type LookSlotName = "title_page" | "slate" | "lower_third" | "closing" | "stage_card";

/** What the page knows before the catalog answers (or when it cannot):
 *  the shipped default with its still card only and no previews. */
export const BUILTIN_LOOKS: LookInfo[] = [
  {
    name: DEFAULT_LOOK,
    label: "Splitsmith",
    source: "shipped",
    accent_series: [],
    preview: null,
    slots: {
      title_page: [{ name: DEFAULT_VARIANT, preview: null }],
      slate: [{ name: DEFAULT_VARIANT, preview: null }],
      lower_third: [{ name: DEFAULT_VARIANT, preview: null }],
      summary: [],
      closing: [{ name: DEFAULT_VARIANT, preview: null }],
      transition: [],
    },
  },
];

function lookNamed(looks: LookInfo[], name: string): LookInfo | undefined {
  return looks.find((l) => l.name === name);
}

/** The stored Look when it is installed, else the default. */
export function visibleLook(looks: LookInfo[], name: string): string {
  return lookNamed(looks, name) ? name : DEFAULT_LOOK;
}

/** A slot's variants for a Look (the default Look's when ``look`` is not
 *  installed); ``stage_card`` reads the slate's. */
export function variantsFor(looks: LookInfo[], look: string, slot: LookSlotName): LookVariantInfo[] {
  const info = lookNamed(looks, visibleLook(looks, look)) ?? BUILTIN_LOOKS[0];
  const key = slot === "stage_card" ? "slate" : slot;
  return info.slots[key] ?? [];
}

/** The stored variant when the Look offers it, else ``default``. */
export function visibleVariant(looks: LookInfo[], look: string, slot: LookSlotName, stored: string): string {
  return variantsFor(looks, look, slot).some((v) => v.name === stored) ? stored : DEFAULT_VARIANT;
}

/** The Look's stings as transition kinds (``sting:<name>``) with their previews. */
export function stingsFor(looks: LookInfo[], look: string): { id: string; name: string; preview: string | null }[] {
  const info = lookNamed(looks, visibleLook(looks, look));
  return (info?.slots.transition ?? []).map((v) => ({ id: `sting:${v.name}`, name: v.name, preview: v.preview }));
}

/** Every stored field resolved against the catalog: what a request sends. */
export function resolveLookChoice(looks: LookInfo[], settings: LookChoice): LookChoice {
  const look = visibleLook(looks, settings.look);
  return {
    look,
    titlePageVariant: visibleVariant(looks, look, "title_page", settings.titlePageVariant),
    stageCardVariant: visibleVariant(looks, look, "stage_card", settings.stageCardVariant),
    closingCardVariant: visibleVariant(looks, look, "closing", settings.closingCardVariant),
  };
}

/** An ``<img src>`` for a catalog preview path, scoped like every API request. */
export function previewSrc(path: string | null): string | null {
  return path ? scopeRequestPath(path) : null;
}

/** The settings' Look fields alone, for callers that hold the whole settings. */
export function lookChoiceOf(settings: ExportSettings): LookChoice {
  return {
    look: settings.look,
    titlePageVariant: settings.titlePageVariant,
    stageCardVariant: settings.stageCardVariant,
    closingCardVariant: settings.closingCardVariant,
  };
}
