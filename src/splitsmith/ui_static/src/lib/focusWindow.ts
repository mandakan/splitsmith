/**
 * The phone focus strip's window (spec 2026-10-17): the open region plus
 * 2 s each side, at least 3 s wide, slid (never shrunk) to stay inside
 * the stage. Times are seconds from the beep.
 */
export const FOCUS_PAD_S = 2;
export const FOCUS_MIN_S = 3;
const PLAY_PAD_S = 0.5;

export interface FocusWindow {
  from: number;
  to: number;
}

export function focusWindow(region: { start: number; end: number }, stageTime: number): FocusWindow {
  const limit = Math.max(stageTime, region.end);
  let from = region.start - FOCUS_PAD_S;
  let to = region.end + FOCUS_PAD_S;
  if (to - from < FOCUS_MIN_S) {
    const mid = (region.start + region.end) / 2;
    from = mid - FOCUS_MIN_S / 2;
    to = mid + FOCUS_MIN_S / 2;
  }
  if (from < 0) {
    to = Math.min(limit, to - from);
    from = 0;
  }
  if (to > limit) {
    from = Math.max(0, from - (to - limit));
    to = limit;
  }
  return { from, to };
}

/** ``t`` as a share of the window, clamped to 0..1. */
export function fracOf(t: number, w: FocusWindow): number {
  const span = w.to - w.from;
  if (span <= 0) return 0;
  return Math.min(Math.max((t - w.from) / span, 0), 1);
}

/** What "Play region" plays: half a second either side, inside the stage. */
export function playWindow(region: { start: number; end: number }, stageTime: number): FocusWindow {
  return { from: Math.max(0, region.start - PLAY_PAD_S), to: Math.min(stageTime, region.end + PLAY_PAD_S) };
}
