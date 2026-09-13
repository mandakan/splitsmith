/**
 * Waveform zoom key bindings shared by the audit canvas, the review
 * page and the beep section.
 *
 * Documented keys are the bare ``+`` (or ``=``, the same physical key
 * unshifted), ``0`` and ``-`` -- the convention maps, design tools and
 * browsers themselves use. They used to be Cmd/Ctrl+1/2/3, which macOS
 * Safari never delivers to the page: Cmd+digit is "switch to tab N" at
 * the menu level and fires before any keydown handler, so
 * preventDefault had nothing to prevent. Chrome and Firefox do deliver
 * it, so the digit chords stay as silent aliases for muscle memory.
 *
 * The bare keys are ignored while typing in a text field (a "-" in a
 * stage note is a hyphen) and when any modifier other than Shift is
 * held (Cmd+- / Cmd+= is the browser's own zoom, not ours).
 */
import { isTypingTextTarget } from "@/lib/audit-input";

export type ZoomAction = "in" | "fit" | "out";

const BARE: Record<string, ZoomAction> = { "+": "in", "=": "in", "0": "fit", "-": "out" };
const LEGACY_CHORD: Record<string, ZoomAction> = { "1": "in", "2": "fit", "3": "out" };

export function zoomActionForKey(e: KeyboardEvent): ZoomAction | null {
  const mod = e.metaKey || e.ctrlKey;
  if (mod && !e.altKey) return LEGACY_CHORD[e.key] ?? null;
  if (mod || e.altKey) return null;
  const action = BARE[e.key];
  if (!action || isTypingTextTarget(e.target)) return null;
  return action;
}
