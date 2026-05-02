/**
 * Input-focus helpers for the audit + review screens.
 *
 * The audit UX is keyboard-driven: Space plays / pauses, arrow keys
 * nudge the playhead, M / L navigate. The standard browser behavior
 * "Space activates the focused button / checkbox" fights this -- after
 * clicking a filter chip the next Space press toggles the chip again
 * instead of playback.
 *
 * Two helpers here:
 *
 *  - ``isTypingTextTarget`` distinguishes "user is typing text" from
 *    "user has a button-ish thing focused". Only the former should
 *    swallow our hotkeys; checkboxes, buttons, and other non-text
 *    inputs are reserved for the global playback handler.
 *
 *  - ``useBlurOnPointerClick`` blurs the focused element after a
 *    mouse / touch click on a non-text control. Pointer interactions
 *    don't need lingering focus, and clearing it stops the next Space
 *    press from re-clicking the last-touched button. Keyboard
 *    activation (Enter, synthetic clicks with detail=0) keeps focus
 *    intact so users navigating by Tab don't lose their place.
 */

import { useEffect } from "react";

const TEXT_INPUT_TYPES = new Set([
  "text",
  "search",
  "url",
  "email",
  "password",
  "tel",
  "number",
  "date",
  "datetime-local",
  "month",
  "week",
  "time",
]);

export function isTypingTextTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  if (target.tagName === "TEXTAREA") return true;
  if (target.tagName === "INPUT") {
    return TEXT_INPUT_TYPES.has((target as HTMLInputElement).type);
  }
  return false;
}

export function useBlurOnPointerClick(): void {
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      // detail=0 indicates a synthetic click (keyboard activation, programmatic
      // .click(), accessibility tools). Keep focus in those cases.
      if (e.detail < 1) return;
      const active = document.activeElement;
      if (!(active instanceof HTMLElement)) return;
      if (isTypingTextTarget(active)) return;
      active.blur();
    };
    document.addEventListener("click", handler);
    return () => document.removeEventListener("click", handler);
  }, []);
}
