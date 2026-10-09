/**
 * "Show where logos go": the previews (the Export rail and the Look editor)
 * draw a labelled placeholder in every logo spot no logo fills, so the
 * shooter's corner, your brand and the event's centre are visible before
 * any logo is uploaded. One per-viewer switch shared by both previews, on
 * by default; an export never draws a placeholder.
 */
import { useSyncExternalStore } from "react";

const KEY = "splitsmith.preview.logoSpots";
/** The cards that have a logo spot (identity, brand or event). */
export const LOGO_SPOT_CARDS: ReadonlySet<string> = new Set(["title", "slate", "lower-third", "closing"]);

const listeners = new Set<() => void>();

function read(): boolean {
  try {
    return window.localStorage.getItem(KEY) !== "off";
  } catch {
    return true;
  }
}

export function setLogoSpots(on: boolean): void {
  try {
    window.localStorage.setItem(KEY, on ? "on" : "off");
  } catch {
    /* storage blocked: the switch still works for this page */
  }
  current = on;
  listeners.forEach((l) => l());
}

let current: boolean | null = null;

function snapshot(): boolean {
  if (current === null) current = read();
  return current;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The switch's value and its setter, shared by every preview on the page. */
export function useLogoSpots(): [boolean, (on: boolean) => void] {
  const on = useSyncExternalStore(subscribe, snapshot, () => true);
  return [on, setLogoSpots];
}
