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
