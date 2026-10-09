/**
 * Logos beyond the cards (spec 2026-10-09 logo spots): the title page, the
 * stage slates and the closing card always draw theirs; a spot is one more
 * place the video may carry a logo. The Export page offers presets (Cards
 * only, Polished) and a Choose that shows one checkbox per spot. Polished is
 * the default, as on the server (``logo_spots.DEFAULT_LOGO_SPOTS``).
 */

export type LogoSpot = "wipe" | "summaries" | "thumbnail";
export const LOGO_SPOTS: readonly LogoSpot[] = ["summaries", "thumbnail", "wipe"];

export type LogoPreset = "cards" | "polished" | "custom";

const PRESET_SPOTS: Record<Exclude<LogoPreset, "custom">, readonly LogoSpot[]> = {
  cards: [],
  polished: ["summaries", "thumbnail", "wipe"],
};

export const DEFAULT_LOGO_SPOTS: readonly LogoSpot[] = PRESET_SPOTS.polished;

/** What each spot does, in the page's words. */
export const SPOT_COPY: Record<LogoSpot, { label: string; help: string }> = {
  wipe: {
    label: "Your brand on the wipe",
    help: "Your brand rides the Wipe sting between stages; without a brand, the shooter's logo.",
  },
  summaries: {
    label: "Shooter logo on the summaries",
    help: "Small, top right of the stage summary and the match summary, where the slates put it.",
  },
  thumbnail: {
    label: "A designed thumbnail",
    help: "The YouTube thumbnail becomes a card over an action frame: the match name large, your brand and the logos.",
  },
};

/** ``spots`` as the server takes them: known ones only, once each, sorted. */
export function normalizeSpots(spots: readonly unknown[] | null | undefined): LogoSpot[] {
  const known = new Set<LogoSpot>();
  for (const spot of spots ?? []) {
    if ((LOGO_SPOTS as readonly unknown[]).includes(spot)) known.add(spot as LogoSpot);
  }
  return LOGO_SPOTS.filter((spot) => known.has(spot));
}

/** The preset ``spots`` is, or ``custom``. */
export function presetFor(spots: readonly LogoSpot[]): LogoPreset {
  const key = normalizeSpots(spots).join(",");
  if (key === PRESET_SPOTS.cards.join(",")) return "cards";
  if (key === PRESET_SPOTS.polished.join(",")) return "polished";
  return "custom";
}

/** The spots a preset stands for; ``custom`` keeps ``current``. */
export function spotsForPreset(preset: LogoPreset, current: readonly LogoSpot[]): LogoSpot[] {
  return preset === "custom" ? normalizeSpots(current) : [...PRESET_SPOTS[preset]];
}

export function toggleSpot(spots: readonly LogoSpot[], spot: LogoSpot, on: boolean): LogoSpot[] {
  const rest = spots.filter((s) => s !== spot);
  return normalizeSpots(on ? [...rest, spot] : rest);
}

const EXTRA: Record<LogoSpot, string> = {
  summaries: "the shooter's logo on the summaries",
  thumbnail: "a designed thumbnail",
  wipe: "your brand on the wipe between stages",
};

/** One line under the control: what this choice puts in the video. */
export function logoPlanHelp(spots: readonly LogoSpot[]): string {
  const chosen = normalizeSpots(spots);
  const base = "Logos always sit on the title page, the stage slates and the closing card.";
  if (chosen.length === 0) return `${base} Nowhere else.`;
  const extras = chosen.map((spot) => EXTRA[spot]);
  const list = extras.length > 1 ? `${extras.slice(0, -1).join(", ")} and ${extras[extras.length - 1]}` : extras[0];
  return `${base} Also ${list}.`;
}
