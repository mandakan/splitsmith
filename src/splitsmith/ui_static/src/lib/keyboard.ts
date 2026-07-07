import { useEffect } from "react";

/**
 * Window-level Space -> toggle play/pause hook.
 *
 * Every Splitsmith surface that mounts a <video> or <audio> element
 * should wire this so the user can hit Space without first clicking
 * the player to give it focus. Focus tends to live on the last clicked
 * affordance (a sidebar nav row, a filter chip, a kbd), and the
 * browser's default Space-on-a-focused-button-or-link is "scroll the
 * page" or "activate the button" -- neither of which is what the
 * operator wants when they're auditioning a clip.
 *
 * Native <video controls>/<audio controls> are the tricky case. Their
 * sub-controls live in the browser's shadow DOM, so once the user
 * clicks anywhere on the control strip the event target retargets to
 * the media element and we can't tell WHICH sub-control has focus:
 *   - play button focused: the browser toggles on Space, so if we also
 *     toggled it would fire twice and cancel out.
 *   - scrubber/timeline focused: the browser does nothing on Space and
 *     lets the page scroll instead.
 * Both report the same target, so no bubble-phase handler can do the
 * right thing for both. Instead we intercept Space in the CAPTURE
 * phase -- which runs before the event ever reaches the native
 * controls -- preventDefault + stopImmediatePropagation so the browser
 * never acts, and toggle exactly once. Space then means play/pause
 * consistently no matter which sub-control the mouse last touched.
 *
 * Skips the handler when the target is a real text input (INPUT,
 * TEXTAREA, SELECT, contenteditable) so typing in a stage note /
 * search box still inserts a space. Also skips when a modifier is held
 * so we don't steal browser shortcuts (Ctrl+Space etc).
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

    function isSpace(e: KeyboardEvent): boolean {
      if (e.code !== "Space" && e.key !== " ") return false;
      return !(e.metaKey || e.ctrlKey || e.altKey);
    }

    function isTextTarget(t: EventTarget | null): boolean {
      if (!(t instanceof HTMLElement)) return false;
      return (
        t.isContentEditable ||
        t.tagName === "INPUT" ||
        t.tagName === "TEXTAREA" ||
        t.tagName === "SELECT"
      );
    }

    function isMediaTarget(t: EventTarget | null): boolean {
      return (
        t instanceof HTMLElement &&
        (t.tagName === "VIDEO" || t.tagName === "AUDIO")
      );
    }

    // Capture phase: fires before the native media controls see the
    // key, so we own Space whenever the player (any sub-control) has
    // focus. stopImmediatePropagation keeps the browser from also
    // toggling or scrolling; preventDefault covers the default action.
    function onCapture(e: KeyboardEvent) {
      if (!isSpace(e) || !isMediaTarget(e.target)) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      toggle();
    }

    // Bubble phase: focus lives elsewhere (a sidebar row, a chip), so
    // Space would otherwise scroll or activate that affordance.
    function onBubble(e: KeyboardEvent) {
      if (!isSpace(e) || isTextTarget(e.target) || isMediaTarget(e.target)) {
        return;
      }
      e.preventDefault();
      toggle();
    }

    window.addEventListener("keydown", onCapture, true);
    window.addEventListener("keydown", onBubble);
    return () => {
      window.removeEventListener("keydown", onCapture, true);
      window.removeEventListener("keydown", onBubble);
    };
  }, [toggle, enabled]);
}
