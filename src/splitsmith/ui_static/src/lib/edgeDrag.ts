/**
 * The phone's edge mode (spec 2026-10-17): a sideways drag anywhere on the
 * pad moves one edge (or the whole region) by the finger's travel, one pad
 * width a second, so a pixel is a few milliseconds and no target has to be
 * hit. Every rule is the desktop's: each frame goes through
 * ``laneDrag.dragFrame`` (clamp at a same-lane neighbour, the minimum
 * length, the shot snap, the unsnapped seek), judged from where the
 * gesture began.
 */
import { SNAP_PX, dragFrame, nudge, type Frame } from "@/components/coach/laneDrag";
import type { StageEvent } from "@/lib/api";
import { snapTime } from "@/lib/events";

export const SECONDS_PER_WIDTH = 1;

export type EdgeTarget = "start" | "end" | "body";

export interface EdgeGesture {
  target: EdgeTarget;
  id: string;
  /** The region when the gesture began: the base of every frame and what Cancel restores. */
  before: StageEvent;
}

export function beginEdge(events: StageEvent[], id: string, target: EdgeTarget): EdgeGesture | null {
  const before = events.find((e) => e.id === id);
  return before ? { target, id, before } : null;
}

/** One frame: ``dxFrac`` is the finger's travel since it went down, over the pad's width. */
export function edgeFrame(
  g: EdgeGesture,
  current: StageEvent[],
  dxFrac: number,
  opts: { snap: boolean; shotTimes: number[]; duration: number; padPx: number },
): Frame | null {
  const delta = dxFrac * SECONDS_PER_WIDTH;
  // The snap tolerance is the desktop's 8 px, in the pad's seconds per pixel.
  const tolerance = (SNAP_PX / Math.max(opts.padPx, 1)) * SECONDS_PER_WIDTH;
  const snap = (t: number) => (opts.snap ? snapTime(t, [0, ...opts.shotTimes], tolerance) : t);
  if (g.target === "body") {
    return dragFrame(
      { mode: "body", id: g.id, before: g.before, grabOffsetT: 0 },
      current,
      g.before.start + delta,
      snap,
      opts.duration,
    );
  }
  const from = g.target === "start" ? g.before.start : g.before.end;
  return dragFrame({ mode: "edge", edge: g.target, id: g.id, before: g.before }, current, from + delta, snap, opts.duration);
}

/** The list with the region as it stood when the gesture began (Cancel, pointercancel). */
export function restoreRegion(current: StageEvent[], g: EdgeGesture): StageEvent[] {
  return current.map((e) => (e.id === g.id ? g.before : e));
}

/** One frame of the clip's own rate, never snapped: the desktop's keyboard nudge. */
export function stepEdge(
  current: StageEvent[],
  id: string,
  target: "start" | "end",
  dir: -1 | 1,
  fps: number,
): StageEvent[] | null {
  return nudge(current, id, dir, { alt: false, shift: target === "end" }, fps);
}

/** What pulled the edge off the finger, for the pill; null when nothing did or a neighbour did. */
export function snappedTo(
  edge: number,
  raw: number,
  shots: { shot_number: number; time_from_beep: number }[],
): string | null {
  if (Math.abs(edge - raw) < 1e-9) return null;
  if (edge === 0) return "beep";
  const shot = shots.find((s) => s.time_from_beep === edge);
  return shot ? `shot ${shot.shot_number}` : null;
}
