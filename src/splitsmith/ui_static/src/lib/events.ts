/**
 * Stage events: movement, reload and activation as regions on the stage
 * timeline (spec 2026-10-08). The TS twin of ``splitsmith/events.py``;
 * both run ``tests/fixtures/events/cases.json`` case for case, so a rule
 * that changes here changes there in the same change. Pure, no React.
 */
import type { StageEvent, StageEventSummary } from "@/lib/api";

/** Shortest region the editor produces; a handle dragged past its partner stops here. */
export const MIN_EVENT_S = 0.05;

export interface ReloadFigure {
  eventId: string;
  duration: number;
  /** Any movement region overlaps the reload. */
  moving: boolean;
  /** reload.end - end of the latest-ending overlapping movement; null when standing. */
  overhang: number | null;
}

const overlaps = (a: StageEvent, b: StageEvent) => a.start < b.end && b.start < a.end;

export function validateLanes(events: StageEvent[]): string | null {
  const byKind = new Map<string, StageEvent[]>();
  for (const e of events) byKind.set(e.kind, [...(byKind.get(e.kind) ?? []), e]);
  for (const [kind, lane] of byKind) {
    lane.sort((a, b) => a.start - b.start || a.end - b.end);
    for (let i = 1; i < lane.length; i++) {
      if (lane[i].start < lane[i - 1].end) return `${kind} events ${lane[i - 1].id} and ${lane[i].id} overlap`;
    }
  }
  return null;
}

export function nextEventId(events: Pick<StageEvent, "id">[]): string {
  let high = 0;
  for (const e of events) {
    const m = /^evt-(\d+)$/.exec(e.id);
    if (m) high = Math.max(high, Number(m[1]));
  }
  return `evt-${high + 1}`;
}

export function shotIsMoving(t: number, events: StageEvent[]): boolean {
  return events.some((e) => e.kind === "movement" && e.start <= t && t <= e.end);
}

export function enclosingMovement(event: StageEvent, events: StageEvent[]): StageEvent | null {
  let best: StageEvent | null = null;
  for (const m of events) {
    if (m.kind !== "movement" || !overlaps(m, event)) continue;
    if (!best || m.end > best.end) best = m;
  }
  return best;
}

export function reloadFigures(events: StageEvent[]): ReloadFigure[] {
  return events
    .filter((e) => e.kind === "reload")
    .sort((a, b) => a.start - b.start)
    .map((r) => {
      const m = enclosingMovement(r, events);
      return { eventId: r.id, duration: r.end - r.start, moving: m !== null, overhang: m ? r.end - m.end : null };
    });
}

function capacityWarning(shotTimes: number[], reloads: StageEvent[], capacity: number | null): string | null {
  if (capacity === null || shotTimes.length === 0) return null;
  const sorted = [...shotTimes].sort((a, b) => a - b);
  const cuts = reloads.map((r) => r.start).sort((a, b) => a - b);
  const counts: number[] = [];
  let i = 0;
  for (const cut of cuts) {
    let n = 0;
    while (i < sorted.length && sorted[i] < cut) {
      n++;
      i++;
    }
    counts.push(n);
  }
  counts.push(sorted.length - i);
  const worst = Math.max(...counts);
  return worst > capacity + 1 ? `${worst} shots without a reload` : null;
}

export function summarize(shotTimes: number[], events: StageEvent[], capacity: number | null): StageEventSummary {
  const figs = reloadFigures(events);
  return {
    movement_s: events.filter((e) => e.kind === "movement").reduce((s, e) => s + (e.end - e.start), 0),
    moving_shots: shotTimes.filter((t) => shotIsMoving(t, events)).length,
    reloads: figs.length,
    reload_avg_s: figs.length ? figs.reduce((s, f) => s + f.duration, 0) / figs.length : null,
    overhang_s: figs.reduce((s, f) => s + (f.overhang !== null && f.overhang > 0 ? f.overhang : 0), 0),
    capacity_warning: capacityWarning(shotTimes, events.filter((e) => e.kind === "reload"), capacity),
  };
}

/** Same-lane neighbours clamp a moving edge; the pair never inverts and the floor is 0. */
export function clampToLane(
  events: StageEvent[],
  id: string,
  start: number,
  end: number,
): { start: number; end: number } {
  const me = events.find((e) => e.id === id);
  let s = Math.max(0, start);
  let en = end;
  if (me) {
    for (const o of events) {
      if (o.id === id || o.kind !== me.kind) continue;
      if (o.end <= me.start && s < o.end) s = o.end; // neighbour on the left
      if (o.start >= me.end && en > o.start) en = o.start; // neighbour on the right
    }
  }
  if (en - s < MIN_EVENT_S) {
    // Whichever edge moved is the one that stops short of the other.
    if (me && start !== me.start) s = en - MIN_EVENT_S;
    else en = s + MIN_EVENT_S;
  }
  return { start: s, end: en };
}

export function snapTime(t: number, targets: number[], toleranceS: number): number {
  let best = t;
  let bestD = toleranceS;
  for (const x of targets) {
    const d = Math.abs(x - t);
    if (d <= bestD) {
      best = x;
      bestD = d;
    }
  }
  return best;
}

export function timeFromX(x: number, width: number, duration: number): number {
  if (width <= 0 || duration <= 0) return 0;
  return Math.min(Math.max(x / width, 0), 1) * duration;
}
