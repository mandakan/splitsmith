/**
 * The chip tick hues (spec 2026-09-13 s5: one hue per meaning), shared by
 * ``Chip`` and every surface that draws a class in the same hue (the time
 * budget's legend, Coach's stage strip). Its own module so ``Chip.tsx``
 * exports components only.
 */
export type ChipTick =
  | "draw"
  | "movement"
  | "transition"
  | "fire"
  | "reload"
  | "activation"
  | "muted"
  | "neutral";

/** The background class of each tick. */
export const CHIP_TICK_BG: Record<ChipTick, string> = {
  draw: "bg-led",
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
