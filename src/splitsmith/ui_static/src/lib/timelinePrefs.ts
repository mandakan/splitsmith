/**
 * The timeline band's two per-browser switches, shared by Coach, Audit and
 * the beep step (spec 2026-10-09): "Wheel zooms the timeline" (off: a plain
 * wheel scrolls the page) and "Follow playhead" (on). Same shape as
 * lib/logoSpots.ts.
 */
import { useSyncExternalStore } from "react";

export const WHEEL_ZOOMS_KEY = "splitsmith.timeline.wheelZooms";
export const FOLLOW_PLAYHEAD_KEY = "splitsmith.timeline.followPlayhead";

function makePref(key: string, fallback: boolean) {
  const listeners = new Set<() => void>();
  let current: boolean | null = null;
  const read = (): boolean => {
    try {
      const v = window.localStorage.getItem(key);
      return v === null ? fallback : v === "on";
    } catch {
      return fallback;
    }
  };
  const set = (on: boolean) => {
    try {
      window.localStorage.setItem(key, on ? "on" : "off");
    } catch {
      /* storage blocked: the switch still works for this page */
    }
    current = on;
    listeners.forEach((l) => l());
  };
  const snapshot = () => {
    if (current === null) current = read();
    return current;
  };
  const subscribe = (l: () => void) => {
    listeners.add(l);
    return () => listeners.delete(l);
  };
  return {
    use: (): [boolean, (on: boolean) => void] => [useSyncExternalStore(subscribe, snapshot, () => fallback), set],
    reset: () => {
      current = null;
    },
  };
}

const wheelZooms = makePref(WHEEL_ZOOMS_KEY, false);
const followPlayhead = makePref(FOLLOW_PLAYHEAD_KEY, true);

export const useWheelZooms = wheelZooms.use;
export const useFollowPlayhead = followPlayhead.use;

/** Drop the cached values so a test reads localStorage afresh. */
export function resetTimelinePrefsForTests(): void {
  wheelZooms.reset();
  followPlayhead.reset();
}
