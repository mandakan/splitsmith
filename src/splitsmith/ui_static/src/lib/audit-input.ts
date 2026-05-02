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

/** Only these element kinds get auto-blurred on pointer click. Anything
 *  else (native <select>, anchors, our own divs) keeps its focus -- we
 *  saw the previous "blur whatever's focused" approach close the stage
 *  selector dropdown on every click. The real goal of this hook is just
 *  to prevent a clicked button-ish thing from eating the next Space, so
 *  we restrict the blur to actual buttons + the hidden checkboxes the
 *  filter chips use. */
function isButtonish(el: HTMLElement | null): boolean {
  if (!el) return false;
  if (el.tagName === "BUTTON") return true;
  if (el.tagName === "INPUT") {
    const t = (el as HTMLInputElement).type;
    return t === "checkbox" || t === "radio" || t === "button" || t === "submit";
  }
  // Buttons rendered as div/span with role=button (uncommon here, but be
  // robust if a future control uses it).
  return el.getAttribute("role") === "button";
}

export function useBlurOnPointerClick(): void {
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      // detail=0 indicates a synthetic click (keyboard activation, programmatic
      // .click(), accessibility tools). Keep focus in those cases.
      if (e.detail < 1) return;
      // Defer to a microtask so we don't blur during the click event chain
      // (which can derail components that move focus on click, e.g. opening
      // a popover and then focusing its content via useEffect).
      queueMicrotask(() => {
        const active = document.activeElement;
        if (!(active instanceof HTMLElement)) return;
        if (!isButtonish(active)) return;
        // If the active element is inside an open dialog/listbox/menu, leave
        // it alone -- those popups manage their own focus and a blur would
        // close them.
        if (active.closest("[role='dialog'], [role='listbox'], [role='menu']")) return;
        active.blur();
      });
    };
    document.addEventListener("click", handler);
    return () => document.removeEventListener("click", handler);
  }, []);
}
