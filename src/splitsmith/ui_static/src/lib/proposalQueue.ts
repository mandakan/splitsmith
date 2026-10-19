/**
 * The phone Breakdown's queue (spec 2026-10-17): the proposals the seeder
 * made, in stage order. It is one way of choosing which region the focus
 * strip opens; a confirmed region can be open too and simply has no place
 * in the queue.
 */
import { LANES } from "@/components/coach/laneDrag";
import type { StageEvent } from "@/lib/api";

export function proposals(events: StageEvent[]): StageEvent[] {
  return events
    .filter((e) => e.source === "auto")
    .sort((a, b) => a.start - b.start || LANES.indexOf(a.kind) - LANES.indexOf(b.kind));
}

/** The first proposal starting after ``after``, else the first one, else null. */
export function nextOpenId(events: StageEvent[], after: number): string | null {
  const queue = proposals(events);
  return (queue.find((e) => e.start > after) ?? queue[0])?.id ?? null;
}

/** The last proposal starting before ``before``, else the last one, else null. */
export function prevOpenId(events: StageEvent[], before: number): string | null {
  const queue = proposals(events);
  return ([...queue].reverse().find((e) => e.start < before) ?? queue[queue.length - 1])?.id ?? null;
}

export function queuePosition(events: StageEvent[], id: string | null): { index: number; total: number } | null {
  const queue = proposals(events);
  const index = queue.findIndex((e) => e.id === id);
  return index < 0 ? null : { index, total: queue.length };
}

/**
 * The proposal one place after (``dir`` 1) or before (-1) ``id`` in the
 * queue, wrapping; null when ``id`` is the only one. By place, not by time:
 * two proposals starting together (a draw and its movement) are both
 * visited. From a confirmed region it steps by its start, from nothing to
 * the first (or last). Read it off the list *before* a keep or a delete
 * takes ``id`` out of the queue.
 */
export function adjacentId(events: StageEvent[], id: string | null, dir: 1 | -1): string | null {
  const queue = proposals(events);
  const i = queue.findIndex((e) => e.id === id);
  if (i >= 0) return queue.length > 1 ? queue[(i + dir + queue.length) % queue.length].id : null;
  const from = events.find((e) => e.id === id);
  if (dir === 1) return nextOpenId(events, from ? from.start : -Infinity);
  return prevOpenId(events, from ? from.start : Infinity);
}
