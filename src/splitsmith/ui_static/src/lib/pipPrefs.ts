/**
 * The PiP inset's corner, remembered per browser (issue #1406): a local
 * convenience, never synced. Same shape as lib/timelinePrefs.ts.
 */
import { useSyncExternalStore } from "react";

import { DEFAULT_PIP_CORNER, isPipCorner, type PipCorner } from "@/lib/pip";

export const PIP_CORNER_KEY = "splitsmith.pip.corner";

const listeners = new Set<() => void>();
let current: PipCorner | null = null;

function read(): PipCorner {
  try {
    const v = window.localStorage.getItem(PIP_CORNER_KEY);
    return isPipCorner(v) ? v : DEFAULT_PIP_CORNER;
  } catch {
    return DEFAULT_PIP_CORNER;
  }
}

export function setPipCorner(corner: PipCorner): void {
  try {
    window.localStorage.setItem(PIP_CORNER_KEY, corner);
  } catch {
    /* storage blocked: the corner still holds for this page */
  }
  current = corner;
  listeners.forEach((l) => l());
}

const snapshot = () => {
  if (current === null) current = read();
  return current;
};

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
};

export function usePipCorner(): [PipCorner, (corner: PipCorner) => void] {
  return [useSyncExternalStore(subscribe, snapshot, () => DEFAULT_PIP_CORNER), setPipCorner];
}

/** Drop the cached value so a test reads localStorage afresh. */
export function resetPipPrefsForTests(): void {
  current = null;
}
