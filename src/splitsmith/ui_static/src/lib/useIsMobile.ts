/**
 * useIsMobile - viewport gate for the mobile shell and desktop-only
 * signpost. Below Tailwind's md breakpoint (768px) = mobile. Uses
 * matchMedia with a change listener (not resize) and initializes
 * synchronously so phones never flash the desktop layout.
 */
import { useSyncExternalStore } from "react";

const QUERY = "(max-width: 767px)";

function subscribe(onChange: () => void): () => void {
  const mql = window.matchMedia(QUERY);
  mql.addEventListener("change", onChange);
  return () => mql.removeEventListener("change", onChange);
}

function getSnapshot(): boolean {
  return window.matchMedia(QUERY).matches;
}

export function useIsMobile(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}
