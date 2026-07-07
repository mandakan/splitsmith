import { useEffect } from "react";

/**
 * Window-level Space → toggle play/pause hook.
 *
 * Every Splitsmith surface that mounts a <video> or <audio> element
 * should wire this so the user can hit Space without first clicking
 * the player to give it focus. Focus tends to live on the last clicked
 * affordance (a sidebar nav row, a filter chip, a kbd), and the
 * browser's default Space-on-a-focused-button-or-link is "scroll the
 * page" or "activate the button" -- neither of which is what the
 * operator wants when they're auditioning a clip.
 *
 * Skips the handler when the target is a real text input (INPUT,
 * TEXTAREA, contenteditable) so typing in a stage note / search box
 * still inserts a space. Also skips when a focused media element
 * (VIDEO/AUDIO) already handles Space through its native controls, so
 * a mouse click on the play button doesn't leave us double-toggling.
 * Also skips when a modifier is held so we don't steal browser
 * shortcuts (Ctrl+Space etc).
 *
 * Pass ``enabled = false`` while playback isn't ready (no clip loaded,
 * picker unavailable, etc) so Space falls through to the browser
 * default.
 */
export function useSpacePlayPause(
  toggle: () => void,
  enabled = true,
): void {
  useEffect(() => {
    if (!enabled) return;
    function onKey(e: KeyboardEvent) {
      if (e.code !== "Space" && e.key !== " ") return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target;
      if (t instanceof HTMLElement) {
        if (t.isContentEditable) return;
        if (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT") {
          return;
        }
        // A focused <video controls>/<audio controls> already toggles
        // itself on Space via the browser's native controls. Clicking
        // the play button with the mouse leaves focus there, so if we
        // also toggled we'd fire twice and cancel out (the "Space does
        // nothing after clicking play" bug). Stand down and let the
        // native control own Space when the media element is focused.
        if (t.tagName === "VIDEO" || t.tagName === "AUDIO") return;
      }
      e.preventDefault();
      toggle();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle, enabled]);
}
