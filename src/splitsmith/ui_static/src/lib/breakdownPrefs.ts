/**
 * Breakdown's per-browser layout: the inspector folded to a rail and the
 * shot list folded to its header (#1372), and the band's height under the
 * splitter (#1373). Shared by every stage and every match, never sent
 * anywhere.
 * Same shape as lib/timelinePrefs.ts: localStorage behind try/catch, so a
 * blocked store still lets the switch work for this page.
 */
import { useSyncExternalStore } from "react";

export const INSPECTOR_FOLDED_KEY = "splitsmith.breakdown.inspectorFolded";
export const SHOTS_FOLDED_KEY = "splitsmith.breakdown.shotsFolded";
export const BAND_HEIGHT_KEY = "splitsmith.breakdown.bandHeight";

function makePref<T>(key: string, fallback: T, parse: (raw: string) => T, format: (v: T) => string | null) {
  const listeners = new Set<() => void>();
  let current: { v: T } | null = null;
  const read = (): T => {
    try {
      const raw = window.localStorage.getItem(key);
      return raw === null ? fallback : parse(raw);
    } catch {
      return fallback;
    }
  };
  const set = (v: T) => {
    try {
      const raw = format(v);
      if (raw === null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, raw);
    } catch {
      /* storage blocked: the layout still changes for this page */
    }
    current = { v };
    listeners.forEach((l) => l());
  };
  const snapshot = () => {
    if (current === null) current = { v: read() };
    return current.v;
  };
  const subscribe = (l: () => void) => {
    listeners.add(l);
    return () => listeners.delete(l);
  };
  return {
    use: (): [T, (v: T) => void] => [useSyncExternalStore(subscribe, snapshot, () => fallback), set],
    reset: () => {
      current = null;
    },
  };
}

const onOff = (raw: string) => raw === "on";
const fmtOnOff = (v: boolean) => (v ? "on" : "off");

const inspectorFolded = makePref(INSPECTOR_FOLDED_KEY, false, onOff, fmtOnOff);
const shotsFolded = makePref(SHOTS_FOLDED_KEY, false, onOff, fmtOnOff);
/** The band's height in px under the splitter (#1373); ``null`` is the
 *  page's own measured layout. Clamped to the window where it is drawn. */
const bandHeight = makePref<number | null>(
  BAND_HEIGHT_KEY,
  null,
  (raw) => {
    const n = Number(raw);
    return Number.isFinite(n) && n > 0 ? Math.round(n) : null;
  },
  (v) => (v === null ? null : String(Math.round(v))),
);

export const useInspectorFolded = inspectorFolded.use;
export const useShotsFolded = shotsFolded.use;
export const useBandHeight = bandHeight.use;

/** Drop the cached values so a test reads localStorage afresh. */
export function resetBreakdownPrefsForTests(): void {
  inspectorFolded.reset();
  shotsFolded.reset();
  bandHeight.reset();
}
