/**
 * The lab Review page's step-through (#1363): a fixture's shot times are
 * checked against the shot-time definition (the rise foot, docs/METHODOLOGY.md)
 * one disagreement at a time. Only the shots whose rise foot sits more than
 * ``STEP_MIN_MOVE_MS`` from the stored time are stepped through; the rest are
 * scanned on the full waveform before the fixture is marked reviewed.
 *
 * The suggestion is ``snapToLeadingEdge`` over the page's own 1 ms peaks, the
 * same rule a drop snaps to and the one ``splitsmith.rise_foot`` runs on the
 * server (held together by tests/fixtures/rise_foot/cases.json).
 */

import type { DevReviewQueueItem } from "@/lib/api";
import { snapToLeadingEdge, type SnapPeaks } from "@/lib/peak-snap";

/** A shot is stepped through when its rise foot is further than this. */
export const STEP_MIN_MOVE_MS = 5;

/** Half the close-up's width: wide enough to show the lead-in and the burst. */
export const CLOSEUP_HALF_S = 0.04;

export interface StepItem {
  id: string;
  stored: number;
  /** The rise foot, on the 1 ms grid. */
  suggested: number;
  /** ``suggested - stored`` in whole ms. */
  moveMs: number;
}

/** The shots to step through, in time order. */
export function stepQueue(
  shots: ReadonlyArray<{ id: string; time: number }>,
  peaks: SnapPeaks,
  minMoveMs: number = STEP_MIN_MOVE_MS,
): StepItem[] {
  const out: StepItem[] = [];
  for (const shot of [...shots].sort((a, b) => a.time - b.time)) {
    const edge = snapToLeadingEdge(shot.time, peaks);
    if (edge == null) continue;
    const suggested = Math.round(edge * 1000) / 1000;
    const moveMs = Math.round((suggested - shot.time) * 1000);
    if (Math.abs(moveMs) > minMoveMs) out.push({ id: shot.id, stored: shot.time, suggested, moveMs });
  }
  return out;
}

export type StepAction =
  | { kind: "accept" }
  | { kind: "keep" }
  | { kind: "reject" }
  | { kind: "back" }
  | { kind: "nudge"; ms: number };

/** Enter takes the rise foot, Space keeps the marker where it stands, X says
 *  it is not a shot, Backspace goes back one, arrows nudge 1 ms (Shift 5 ms).
 *  ``null`` leaves the key to the page. */
export function stepActionForKey(e: {
  key: string;
  shiftKey: boolean;
  metaKey: boolean;
  ctrlKey: boolean;
  altKey: boolean;
}): StepAction | null {
  if (e.metaKey || e.ctrlKey || e.altKey) return null;
  switch (e.key) {
    case "Enter":
      return { kind: "accept" };
    case " ":
      return { kind: "keep" };
    case "x":
    case "X":
      return { kind: "reject" };
    case "Backspace":
      return { kind: "back" };
    case "ArrowLeft":
      return { kind: "nudge", ms: e.shiftKey ? -5 : -1 };
    case "ArrowRight":
      return { kind: "nudge", ms: e.shiftKey ? 5 : 1 };
    default:
      return null;
  }
}

/** The peaks' bins inside ``center +- halfS``, each with its start time. */
export function closeupBins(
  peaks: SnapPeaks,
  center: number,
  halfS: number = CLOSEUP_HALF_S,
): Array<{ t: number; v: number }> {
  const n = peaks.peaks.length;
  if (n === 0 || peaks.duration <= 0) return [];
  const binW = peaks.duration / n;
  const lo = Math.max(0, Math.round((center - halfS) / binW));
  const hi = Math.min(n, Math.round((center + halfS) / binW));
  const out: Array<{ t: number; v: number }> = [];
  for (let i = lo; i < hi; i++) out.push({ t: i * binW, v: peaks.peaks[i] });
  return out;
}

/** The fixture to open after this one is signed off: the queue's next one
 *  whose shot times need checking. */
export function nextFixtureToReview(
  pending: ReadonlyArray<DevReviewQueueItem>,
  currentSlug: string,
): DevReviewQueueItem | null {
  return pending.find((i) => i.slug !== currentSlug && i.review_status === "needs_review") ?? null;
}
