/**
 * Where a Menu popover opens (components/ui/Menu): under its trigger, or
 * above it when the space below cannot hold the popover and the space
 * above holds more. A menu opened from a control near the bottom of the
 * screen (Audit's timeline band header) would otherwise run off it.
 */

/** Gap between the trigger and the popover, in px. */
export const MENU_GAP = 4;

export interface AnchorBox {
  top: number;
  bottom: number;
}

/** The popover's `top` in viewport px. */
export function menuTop(anchor: AnchorBox, menuHeight: number, viewportHeight: number): number {
  const below = anchor.bottom + MENU_GAP;
  const roomBelow = viewportHeight - below;
  const roomAbove = anchor.top - MENU_GAP;
  if (menuHeight <= roomBelow || roomAbove <= roomBelow) return below;
  return Math.max(0, anchor.top - MENU_GAP - menuHeight);
}
