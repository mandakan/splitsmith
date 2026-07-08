/**
 * Split taxonomy - single source of truth for split-speed buckets and
 * interval-class presentation. Shared by Coach and the Results viewer.
 */
import type { CoachIntervalClass } from "@/lib/api";

export interface SplitBucket {
  max: number;
  label: string;
  color: string;
}

export const SPLIT_BUCKETS: SplitBucket[] = [
  { max: 0.25, label: "fast", color: "var(--color-done)" },
  { max: 0.45, label: "ok", color: "var(--color-ink-2)" },
  { max: 0.85, label: "slow", color: "var(--color-live)" },
  { max: Infinity, label: "vslow", color: "var(--color-led)" },
];

export function splitBucket(s: number): SplitBucket {
  for (const b of SPLIT_BUCKETS) if (s <= b.max) return b;
  return SPLIT_BUCKETS[SPLIT_BUCKETS.length - 1];
}

export const INTERVAL_LABEL: Record<CoachIntervalClass, string> = {
  first_shot: "Draw",
  split: "Fire",
  transition: "Transition",
  movement: "Movement",
  reload: "Reload",
  activation: "Activation",
};

/** Index of the shot currently "live" under the playhead: the last shot
 *  whose time_absolute has passed (+20ms grace so a seek exactly onto a
 *  shot counts it). No sort assumption - scans for the max qualifying
 *  time. Returns -1 before the first shot. Shared by ResultsStage
 *  (active row) and ShotTicker so the two can never drift. */
export function currentShotIndex(
  shots: readonly { time_absolute: number }[],
  time: number,
): number {
  let idx = -1;
  let bestT = -Infinity;
  for (let i = 0; i < shots.length; i++) {
    const t = shots[i].time_absolute;
    if (t <= time + 0.02 && t >= bestT) {
      bestT = t;
      idx = i;
    }
  }
  return idx;
}

export const INTERVAL_TONE: Record<CoachIntervalClass, string> = {
  first_shot: "text-led border-led-deep bg-led/10",
  split: "text-done border-done/40 bg-done/10",
  transition: "text-live border-live/40 bg-live/10",
  movement: "text-beep border-beep/40 bg-beep-tint",
  reload: "text-manual border-manual/40 bg-manual/10",
  activation: "text-ink-2 border-rule-strong bg-surface-3",
};
