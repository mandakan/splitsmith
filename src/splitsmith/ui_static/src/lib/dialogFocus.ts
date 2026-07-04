/**
 * Shared keyboard + focus behavior for floating surfaces.
 *
 * WCAG 2.2 AA dialog contract (ARIA APG "dialog (modal)" pattern):
 * focus moves INTO the surface on open, Escape closes, Tab/Shift-Tab
 * are trapped inside while open (modal only), and focus RESTORES to
 * the trigger on close. Extracted from ConfirmDialog so every modal
 * shares one implementation instead of each dialog hand-rolling a
 * subset.
 *
 * Non-modal surfaces (marker-list drawer, jobs sheet) pass
 * ``trap: false``: they still get Escape + initial focus + restore,
 * but Tab may leave -- the page behind them stays interactive by
 * design, so fencing focus in would be wrong.
 *
 * ``active`` gates everything: pass the surface's open state for
 * persistent sliding surfaces (ListDrawer), or ``true`` for surfaces
 * that are conditionally rendered (their mount lifecycle is the open
 * lifecycle).
 *
 * Stack contract (stacked dialogs, e.g. jobs sheet over mobile nav
 * drawer): every active registration joins a module-level stack in
 * activation order, and ONLY the topmost registration handles keys.
 * Escape closes the topmost surface only; a covered dialog neither
 * traps Tab nor reacts to Escape while something is above it. A
 * ``trap: false`` surface on top still claims Escape but enforces no
 * trap - the covered dialog's trap stays suppressed until the upper
 * surface deactivates and pops, at which point the lower dialog
 * resumes trapping and owns the next Escape. Deactivation splices by
 * identity, so out-of-order teardown (a covered dialog closing first)
 * cannot corrupt the stack. With a single active dialog - the common
 * case - behavior is identical to the pre-stack implementation.
 */

import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Module-level stack of active dialog registrations, bottom to top.
 * Entries are identity tokens (one per activation); the token whose
 * hook instance sits at the top of the stack owns keyboard handling.
 */
const dialogStack: object[] = [];

export interface DialogFocusOptions {
  /** Trap Tab/Shift-Tab inside the surface. Default true (modal). */
  trap?: boolean;
  /** Skip the Escape-to-close binding (e.g. while an upload is running
   *  and closing must be blocked). Default false. */
  disableEscape?: boolean;
}

export function useDialogFocus(
  active: boolean,
  panelRef: RefObject<HTMLElement | null>,
  onClose: () => void,
  { trap = true, disableEscape = false }: DialogFocusOptions = {},
): void {
  // Identity token for this activation's slot in the dialog stack.
  // Null while inactive; a fresh token per open keeps stale closures
  // from matching a later activation.
  const stackTokenRef = useRef<object | null>(null);

  // Stack membership: push on activate, splice by identity on
  // deactivate (a covered dialog may close before the one above it,
  // so a plain pop would remove the wrong entry).
  useEffect(() => {
    if (!active) return;
    const token = {};
    stackTokenRef.current = token;
    dialogStack.push(token);
    return () => {
      const idx = dialogStack.indexOf(token);
      if (idx !== -1) dialogStack.splice(idx, 1);
      if (stackTokenRef.current === token) stackTokenRef.current = null;
    };
  }, [active]);

  // Initial focus + restore-on-close. Focus the first focusable control
  // (for confirm-style dialogs that is Cancel, the least destructive)
  // so a stray Enter can't fire a destructive action.
  useEffect(() => {
    if (!active) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const node = panelRef.current;
    const first = node?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? node)?.focus();
    return () => {
      previouslyFocused?.focus?.();
    };
    // panelRef is a ref object -- stable; run once per open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  useEffect(() => {
    if (!active) return;
    const onKeyDown = (e: KeyboardEvent) => {
      // Only the topmost dialog handles keys. Escape fires every
      // same-node document listener regardless of stopPropagation, so
      // without this check stacked dialogs would all close at once,
      // and a covered dialog's trap would yank Tab out of the surface
      // above it.
      if (dialogStack[dialogStack.length - 1] !== stackTokenRef.current) {
        return;
      }
      if (e.key === "Escape" && !disableEscape) {
        e.preventDefault();
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !trap) return;
      const node = panelRef.current;
      if (!node) return;
      const focusables = Array.from(
        node.querySelectorAll<HTMLElement>(FOCUSABLE),
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active_ = document.activeElement as HTMLElement | null;
      if (e.shiftKey) {
        if (active_ === first || !node.contains(active_)) {
          e.preventDefault();
          last.focus();
        }
      } else if (active_ === last || !node.contains(active_)) {
        e.preventDefault();
        first.focus();
      }
    };
    // Document-level (capture) rather than a React onKeyDown on the
    // surface: keeps working even if focus has strayed outside a
    // non-modal surface, and beats page-level shortcut listeners.
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [active, panelRef, onClose, trap, disableEscape]);
}
