/**
 * The hues (spec 2026-09-13 s5: one hue per meaning), and the only place a
 * class or region kind is given a colour. Every surface looks its classes
 * up here by meaning (``timeBudget.BUDGET_TICK`` for interval classes,
 * ``laneDrag.LANE_TICK`` for region kinds): the chip and legend dots, the
 * budget bar, the lanes, the stage strip, the Reclassify tones. Each
 * variant is a literal string because Tailwind only sees literals. A colour
 * map keyed by class or kind anywhere else fails ``chipTicks.test.ts``.
 */
export type ChipTick =
  | "draw"
  | "primary"
  | "movement"
  | "transition"
  | "fire"
  | "reload"
  | "activation"
  | "muted"
  | "neutral";

/** The background class of each tick. */
export const CHIP_TICK_BG: Record<ChipTick, string> = {
  // The draw, interval class and region alike: one meaning, one hue.
  draw: "bg-draw",
  // The primary camera (Footage's clip sheet): the brand red it always had.
  primary: "bg-led",
  movement: "bg-beep",
  transition: "bg-manual",
  fire: "bg-done",
  reload: "bg-live",
  activation: "bg-ink-2",
  muted: "bg-muted",
  // No hue and no meaning: a fact about a row (a region's source) that is
  // not a budget class, a state or a kind.
  neutral: "bg-ink",
};

/** A filled bar segment with text on it (Coach's time budget). */
export const HUE_FILL: Record<ChipTick, string> = {
  draw: "bg-draw text-bg",
  primary: "bg-led text-bg",
  movement: "bg-beep text-bg",
  transition: "bg-manual text-bg",
  fire: "bg-done text-bg",
  reload: "bg-live text-bg",
  activation: "bg-ink-2 text-bg",
  muted: "bg-surface-3 text-ink-2",
  neutral: "bg-ink text-bg",
};

/** A region on a lane: a tint with its edge (Breakdown's lanes, the phone strip). */
export const HUE_REGION: Record<ChipTick, string> = {
  draw: "bg-draw/30 border-draw",
  primary: "bg-led/30 border-led",
  movement: "bg-beep/30 border-beep",
  transition: "bg-manual/30 border-manual",
  fire: "bg-done/30 border-done",
  reload: "bg-live/30 border-live",
  activation: "bg-ink-2/20 border-ink-2",
  muted: "bg-muted/20 border-muted",
  neutral: "bg-ink/20 border-ink",
};

/** A region band on Coach's stage strip; a reload draws solid over the move. */
export const HUE_STRIP: Record<ChipTick, string> = {
  draw: "bg-draw/30",
  primary: "bg-led/30",
  movement: "bg-beep/30",
  transition: "bg-manual/30",
  fire: "bg-done/30",
  reload: "bg-live",
  activation: "bg-ink-2/25",
  muted: "bg-muted/25",
  neutral: "bg-ink/25",
};

/** A pill in the class's hue: text, edge and tint (the Reclassify sheet). */
export const HUE_TONE: Record<ChipTick, string> = {
  draw: "text-draw border-draw/40 bg-draw/10",
  primary: "text-led border-led/40 bg-led/10",
  movement: "text-beep border-beep/40 bg-beep-tint",
  transition: "text-manual border-manual/40 bg-manual/10",
  fire: "text-done border-done/40 bg-done/10",
  reload: "text-live border-live/40 bg-live/10",
  activation: "text-ink-2 border-rule-strong bg-surface-3",
  muted: "text-ink-2 border-rule-strong bg-surface-3",
  neutral: "text-ink border-rule-strong bg-surface-3",
};

/** Each hue's texture (WCAG 1.4.1: colour is never the only cue). Draw and
 *  fire stay solid and differ by lightness; every other class has its own
 *  mark. A bar segment, a strip band and a legend swatch all carry it. */
export const HUE_PATTERN: Record<ChipTick, string> = {
  draw: "",
  primary: "",
  movement: "hue-stripes",
  transition: "hue-dots",
  fire: "",
  reload: "hue-hatch",
  activation: "hue-lines",
  muted: "",
  neutral: "",
};

/** A legend swatch that decodes a bar: its hue and its pattern, square so the
 *  texture shows. */
export function hueSwatch(tick: ChipTick): string {
  return `size-3 shrink-0 rounded-sm ${CHIP_TICK_BG[tick]} ${HUE_PATTERN[tick]}`.trimEnd();
}
