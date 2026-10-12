/**
 * The rise foot in Audit: whether a placed shot snaps to it, what the
 * selected shot's distance from it is, and what "Seat all on rise foot"
 * would move. The rule itself is ``lib/peak-snap.snapToLeadingEdge``
 * (docs/METHODOLOGY.md); this module only applies it.
 *
 * The foot is looked for from where a shot's sound is: the time it was last
 * placed (a drop, a drag, an add), never the time a seat put it at. On a
 * burst with a louder reflection inside the search window the rule is not
 * its own fixed point (198 of 4,508 corpus shots), and seating from the
 * seated time would walk the shot forward a few ms per press.
 */

import type { AuditMarker } from "@/components/MarkerLayer";
import { snapToLeadingEdge, type SnapPeaks } from "@/lib/peak-snap";

const SNAP_KEY = "splitsmith.audit.snapRiseFoot";

/** Snap to the rise foot when a shot is placed: on until turned off. */
export function readSnapOn(): boolean {
  try {
    return window.localStorage.getItem(SNAP_KEY) !== "0";
  } catch {
    return true;
  }
}

export function writeSnapOn(on: boolean): void {
  try {
    window.localStorage.setItem(SNAP_KEY, on ? "1" : "0");
  } catch {
    // Without storage the setting starts on with every page.
  }
}

const isKept = (m: AuditMarker) => m.kind === "detected" || m.kind === "manual";

/** The rule's time for a shot placed at ``from``, on the 1 ms grid. */
export function footFor(from: number, peaks: SnapPeaks | null | undefined): number | null {
  if (!peaks) return null;
  const foot = snapToLeadingEdge(from, peaks);
  return foot == null ? null : Math.round(foot * 1000) / 1000;
}

/** The selected shot against the rule, in words; ``null`` without a foot. */
export function footReadout(time: number, foot: number | null): { text: string; onRule: boolean } | null {
  if (foot == null) return null;
  const ms = Math.round((time - foot) * 1000);
  if (ms === 0) return { text: "On the rise foot", onRule: true };
  return { text: `${Math.abs(ms)} ms ${ms < 0 ? "before" : "after"} the rise foot`, onRule: false };
}

/** What "Seat all on rise foot" would move: every kept shot with a foot
 *  that is not already on it. ``from`` gives each shot's placed time. */
export function seatAllMoves(
  markers: ReadonlyArray<AuditMarker>,
  peaks: SnapPeaks | null | undefined,
  from: (m: AuditMarker) => number,
): Array<{ id: string; from: number; to: number }> {
  const out: Array<{ id: string; from: number; to: number }> = [];
  for (const m of markers) {
    if (!isKept(m)) continue;
    const to = footFor(from(m), peaks);
    if (to != null && Math.abs(to - m.time) >= 0.0005) out.push({ id: m.id, from: m.time, to });
  }
  return out;
}
