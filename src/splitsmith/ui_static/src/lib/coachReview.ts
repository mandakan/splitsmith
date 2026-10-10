/**
 * The Coach review page's figures (#1374, epic #1370). Coach reviews a
 * stage; it edits notes and flags only, so every figure here is display:
 * the average split by the shared rule (``statisticSplits``), the draw (the
 * first shot's time from the beep), and the region figures over the
 * confirmed regions only (``confirmedEvents``), the ones every export and
 * the share figures count. A proposal nobody kept never moves a figure.
 * Pure, no React.
 */
import type { CoachShot, StageEvent } from "@/lib/api";
import { confirmedEvents, summarize } from "@/lib/events";
import { statisticSplits } from "@/lib/splits";

export interface ReviewFigures {
  /** Mean of the statistic splits; null when the stage has none. */
  avgSplit: number | null;
  /** The first shot's time from the beep; null without shots. */
  draw: number | null;
  /** Shots inside a confirmed movement region. */
  movingShots: number;
  /** Confirmed reloads. */
  reloads: number;
  /** Seconds of confirmed reload no confirmed movement covers. */
  exposedReload: number;
}

export function reviewFigures(shots: readonly CoachShot[], events: readonly StageEvent[]): ReviewFigures {
  const ordered = [...shots].sort((a, b) => a.time_from_beep - b.time_from_beep);
  const splits = statisticSplits(ordered);
  const summary = summarize(
    ordered.map((s) => s.time_from_beep),
    confirmedEvents([...events]),
    null,
  );
  return {
    avgSplit: splits.length ? splits.reduce((a, b) => a + b, 0) / splits.length : null,
    draw: ordered.length ? ordered[0].time_from_beep : null,
    movingShots: summary.moving_shots,
    reloads: summary.reloads,
    exposedReload: summary.exposed_reload_s,
  };
}

/** Two-digit shot ordinal, as every shot list prints it. */
export function shotOrdinal(n: number): string {
  return String(n).padStart(2, "0");
}
