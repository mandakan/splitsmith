/**
 * useShortViewport -- a window under 900 px tall: a laptop browser (Chrome
 * on a 1440x900 screen leaves about 790 px, a 1366x768 one about 657).
 * Breakdown's fixed workspace goes dense there (#1371): a compact region
 * card, the transport in the band's header row, the lane hints behind the
 * band's menu. matchMedia with a change listener, like ``useIsMobile``.
 */
import { useSyncExternalStore } from "react";

export const SHORT_VIEWPORT_QUERY = "(max-height: 899px)";

function subscribe(onChange: () => void): () => void {
  const mql = window.matchMedia(SHORT_VIEWPORT_QUERY);
  mql.addEventListener("change", onChange);
  return () => mql.removeEventListener("change", onChange);
}

function getSnapshot(): boolean {
  return window.matchMedia(SHORT_VIEWPORT_QUERY).matches;
}

export function useShortViewport(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}
