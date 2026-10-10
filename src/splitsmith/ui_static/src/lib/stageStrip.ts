/**
 * The read-only stage strip's geometry (#1375): the whole stage from the
 * beep to the stage time in one row. Shots are ticks in their interval
 * class's hue (the chip ticks, ``BUDGET_TICK``); movement and activation are
 * tall translucent bars and a reload a short solid bar drawn over them (the
 * HUD stage bar's convention). Confirmed regions only, as every output
 * counts them. Positions are fractions of the strip's width (0..1), so the
 * component maps them to percentages and never measures. Pure, no React.
 */
import type { ChipTick } from "@/components/ui/Chip";
import type { CoachShot, StageEvent, StageEventKind } from "@/lib/api";
import { shotOrdinal } from "@/lib/coachReview";
import { confirmedEvents } from "@/lib/events";
import { BUDGET_LABEL, BUDGET_TICK, type BudgetClass } from "@/lib/timeBudget";

/** A tap this close to a shot's tick, in px, lands on the shot. */
export const STRIP_SNAP_PX = 10;

export interface StripTick {
  shotNumber: number;
  /** Seconds from the beep. */
  t: number;
  x: number;
  cls: BudgetClass;
  tick: ChipTick;
  /** "Shot 07, 11.25 s, fire": the tick button's accessible name. */
  label: string;
}

export interface StripBar {
  id: string;
  kind: StageEventKind;
  x0: number;
  x1: number;
  /** Movement and activation span the strip's height; a reload is the short bar over them. */
  tall: boolean;
}

export interface StripGeometry {
  /** Seconds the strip spans from the beep. */
  duration: number;
  ticks: StripTick[];
  /** Tall bars first, reloads last, so a reload draws over a movement. */
  bars: StripBar[];
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

export function shotLabel(shot: Pick<CoachShot, "shot_number" | "time_from_beep" | "interval_class">): string {
  const cls: BudgetClass = shot.interval_class ?? "unclassified";
  return `Shot ${shotOrdinal(shot.shot_number)}, ${shot.time_from_beep.toFixed(2)} s, ${BUDGET_LABEL[cls].toLowerCase()}`;
}

/** ``stageTime`` is seconds from the beep (the stage time, else the last shot plus one). */
export function stripGeometry(
  shots: readonly CoachShot[],
  events: readonly StageEvent[],
  stageTime: number,
): StripGeometry {
  const ordered = [...shots].sort((a, b) => a.time_from_beep - b.time_from_beep);
  const last = ordered.length ? ordered[ordered.length - 1].time_from_beep : 0;
  const longest = Math.max(stageTime, last);
  const duration = longest > 0 ? longest : 1;
  const ticks = ordered.map((s) => {
    const cls: BudgetClass = s.interval_class ?? "unclassified";
    return {
      shotNumber: s.shot_number,
      t: s.time_from_beep,
      x: clamp01(s.time_from_beep / duration),
      cls,
      tick: BUDGET_TICK[cls],
      label: shotLabel(s),
    };
  });
  const bars = confirmedEvents([...events])
    .map((e) => ({
      id: e.id,
      kind: e.kind,
      x0: clamp01(e.start / duration),
      x1: clamp01(e.end / duration),
      tall: e.kind !== "reload",
    }))
    .filter((b) => b.x1 > b.x0)
    .sort((a, b) => Number(b.tall) - Number(a.tall) || a.x0 - b.x0);
  return { duration, ticks, bars };
}

/**
 * Where a tap at ``px`` of a ``width`` px strip goes: the nearest shot when
 * its tick is within ``snapPx``, else the raw time under the tap.
 */
export function stripTarget(
  px: number,
  width: number,
  geometry: StripGeometry,
  snapPx = STRIP_SNAP_PX,
): { t: number; shotNumber: number | null } {
  if (width <= 0) return { t: 0, shotNumber: null };
  const x = clamp01(px / width);
  let best: StripTick | null = null;
  let bestPx = snapPx;
  for (const tick of geometry.ticks) {
    const d = Math.abs(tick.x - x) * width;
    if (d <= bestPx) {
      best = tick;
      bestPx = d;
    }
  }
  return best ? { t: best.t, shotNumber: best.shotNumber } : { t: x * geometry.duration, shotNumber: null };
}

/** The shot a key moves to from ``current`` (``null``: none selected). */
export function stepShot(
  ticks: readonly StripTick[],
  current: number | null,
  key: "ArrowLeft" | "ArrowRight" | "Home" | "End",
): number | null {
  if (ticks.length === 0) return null;
  if (key === "Home") return ticks[0].shotNumber;
  if (key === "End") return ticks[ticks.length - 1].shotNumber;
  const i = ticks.findIndex((t) => t.shotNumber === current);
  if (i < 0) return key === "ArrowRight" ? ticks[0].shotNumber : ticks[ticks.length - 1].shotNumber;
  const j = key === "ArrowRight" ? Math.min(ticks.length - 1, i + 1) : Math.max(0, i - 1);
  return ticks[j].shotNumber;
}

/** The playhead's position for a time from the beep. */
export function playheadX(tFromBeep: number, duration: number): number {
  return duration > 0 ? clamp01(tFromBeep / duration) : 0;
}

/** The shot at or before ``t`` (the one the playhead has passed), else null. */
export function shotAtOrBefore(ticks: readonly StripTick[], t: number): number | null {
  let found: number | null = null;
  for (const tick of ticks) {
    if (tick.t <= t + 1e-6) found = tick.shotNumber;
    else break;
  }
  return found;
}
