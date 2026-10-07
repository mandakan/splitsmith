/**
 * The pure reader of the Looks catalog (``GET /api/looks``, spec
 * 2026-10-06 s4, #1246): which Look and which template variant the
 * settings' stored names resolve to against what is installed, so a
 * preset saved on another machine (or with a Look since deleted) falls
 * back to the default instead of a 422, and a stored variant the chosen
 * Look lacks is sent as ``default`` rather than a name the server would
 * warn about. ``useLooks`` fetches the catalog; everything here is data.
 */
import { scopeRequestPath, type LookInfo, type LookVariantInfo, type TransitionFamilyInfo } from "@/lib/api";

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

/** The catalog as a surface sees it (``useLooks``). */
export interface LooksState {
  looks: LookInfo[];
  /** The ffmpeg xfade families the server offers (#1259); empty until it answers. */
  transitions: TransitionFamilyInfo[];
  /** The fetch has answered, one way or the other. */
  loaded: boolean;
  /** It answered with an error: ``looks`` is the built-in catalog. */
  failed: boolean;
}

/** The family and direction an xfade kind belongs to, or null. */
export function transitionFamily(
  kind: string,
  transitions: readonly TransitionFamilyInfo[],
): { family: TransitionFamilyInfo; direction: { name: string; kind: string } } | null {
  for (const family of transitions) {
    const direction = family.directions.find((d) => d.kind === kind);
    if (direction) return { family, direction };
  }
  return null;
}

/** What a sting is called on its tile and the rail: "<Name> sting", so it
 *  never shares a name with an xfade family (the shipped ``wipe`` sting
 *  beside the Wipe family, #1259). */
export function stingLabel(name: string): string {
  return `${name.charAt(0).toUpperCase()}${name.slice(1).replace(/[-_]/g, " ")} sting`;
}

const FCP_LABELS: Record<string, string> = { zoom: "Zoom blur", static: "Static frame", none: "Hard cut" };

/** What the page calls a stored kind: the family label and, when the
 *  family has more than one, its direction ("Wind up"); a sting by its
 *  name; an unknown kind as itself. */
export function transitionLabel(kind: string, transitions: readonly TransitionFamilyInfo[]): string {
  if (FCP_LABELS[kind]) return FCP_LABELS[kind];
  if (kind.startsWith("sting:")) return stingLabel(kind.slice("sting:".length));
  const hit = transitionFamily(kind, transitions);
  if (!hit) return kind;
  return hit.family.directions.length > 1 ? `${hit.family.label} ${hit.direction.name}` : hit.family.label;
}

/** What a request sends for the settings' Look under the catalog's state:
 *  resolved against a loaded catalog; the stored names unresolved when
 *  the catalog could not be fetched (the server validates them and says
 *  what is installed) or has not answered yet (the page gates Export on
 *  ``loaded``), so a chosen Look is never silently swapped for the
 *  default (review of #1246). ``kinds`` are the transitions the kind
 *  filter admits: the server's xfade kinds and the chosen Look's stings, or
 *  the stored kind itself when the catalog is not there (#1259). */
export function requestLook(
  state: LooksState,
  settings: LookChoice & { transitionKind: string },
): { choice: LookChoice; kinds: string[] } {
  if (state.loaded && !state.failed) {
    const choice = resolveLookChoice(state.looks, settings);
    return {
      choice,
      kinds: [
        ...state.transitions.flatMap((f) => f.directions.map((d) => d.kind)),
        ...stingsFor(state.looks, choice.look).map((s) => s.id),
      ],
    };
  }
  return {
    choice: lookChoiceOf(settings),
    kinds: settings.transitionKind === "none" ? [] : [settings.transitionKind],
  };
}

/** An ``<img src>`` for a catalog preview path, scoped like every API request. */
export function previewSrc(path: string | null): string | null {
  return path ? scopeRequestPath(path) : null;
}

/** The settings' Look fields alone, for callers that hold the whole settings. */
export function lookChoiceOf(settings: LookChoice): LookChoice {
  return {
    look: settings.look,
    titlePageVariant: settings.titlePageVariant,
    stageCardVariant: settings.stageCardVariant,
    closingCardVariant: settings.closingCardVariant,
  };
}
