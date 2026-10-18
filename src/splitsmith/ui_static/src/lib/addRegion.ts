/**
 * Adding a region on the phone: where a new region of ``kind`` lands
 * before edge mode tunes it. Over an open region of another kind it takes
 * that region's span (a movement under a draw, a reload on the move);
 * otherwise it fills the gap between the shots around the playhead (the
 * beep and the stage's end bound the first and last gaps). Either way it
 * is cut to the free room in its own lane around the anchor, so it never
 * overlaps a region of its kind. A new region is the user's: ``manual``.
 */
import type { StageEvent, StageEventKind } from "@/lib/api";
import { MIN_EVENT_S, nextEventId } from "@/lib/events";

export interface AddPlace {
  /** The open region, when one is: the new region goes over it. */
  over: StageEvent | null;
  /** The playhead, seconds from the beep. */
  at: number;
  shotTimes: number[];
  /** The stage's span, seconds from the beep. */
  span: number;
}

/** The span a new region would take, before its lane cuts it. */
function wanted(kind: StageEventKind, p: AddPlace): { start: number; end: number; anchor: number } {
  const shots = [...p.shotTimes].sort((a, b) => a - b);
  if (p.over) return { start: p.over.start, end: p.over.end, anchor: (p.over.start + p.over.end) / 2 };
  if (kind === "draw") return { start: 0, end: shots[0] ?? p.span, anchor: 0 };
  const at = Math.min(Math.max(p.at, 0), p.span);
  const prev = [...shots].reverse().find((t) => t <= at) ?? 0;
  const next = shots.find((t) => t > at) ?? p.span;
  return { start: prev, end: next, anchor: at };
}

/**
 * The list with a new ``kind`` region added, and its id; null when its
 * lane has no room at the anchor (or the stage has a draw already).
 */
export function addRegion(
  events: StageEvent[],
  kind: StageEventKind,
  p: AddPlace,
): { events: StageEvent[]; id: string } | null {
  if (kind === "draw" && events.some((e) => e.kind === "draw")) return null;
  const w = wanted(kind, p);
  let start = Math.max(0, w.start);
  let end = Math.min(w.end, p.span);
  for (const o of events) {
    if (o.kind !== kind) continue;
    if (o.start <= w.anchor && w.anchor < o.end) return null;
    if (o.end <= w.anchor) start = Math.max(start, o.end);
    else end = Math.min(end, o.start);
  }
  if (end - start < MIN_EVENT_S) return null;
  const id = nextEventId(events);
  return { events: [...events, { id, kind, start, end, source: "manual" }], id };
}
