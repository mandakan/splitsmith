/**
 * Aggregate per-stage x per-shooter model for the match Overview.
 *
 * The backend gives each shooter a ``stage_statuses`` list; this module
 * pivots those into one row per match stage (cells across shooters) plus a
 * per-row rollup tone and match-level totals. Pure functions -- no I/O, no
 * React -- so the logic stays reviewable even though ``ui_static`` has no
 * test runner. Keep the all-todo (empty) and all-audited (complete) edge
 * cases explicit below.
 */

import type { ShooterListEntry, StageEntry, StageStatus } from "@/lib/api";
import {
  isTerminal,
  statusTone,
  type StageStatusTone,
} from "@/lib/stageStatus";

export type StageMatrixCell = {
  shooter: ShooterListEntry;
  status: StageStatus;
  tone: StageStatusTone;
};

export type StageMatrixRow = {
  stageNumber: number;
  stageName: string;
  cells: StageMatrixCell[];
  rollupTone: StageStatusTone;
  auditedCount: number;
};

export type MatchTotals = {
  totalShooterStages: number;
  auditedShooterStages: number;
  auditedPct: number;
  stagesFullyDone: number;
  stagesInProgress: number;
  stagesUntouched: number;
  hasAnyFootage: boolean;
};

/** Roll a row of per-shooter statuses up to one tone for the tile:
 *  - "done"        when every cell is terminal (audited or skipped)
 *  - "in_progress" when at least one cell is active (not yet terminal but
 *                  has footage-driven progress -- ready/partial/in_progress)
 *  - "todo"        when every cell is todo (no footage anywhere)
 *  An empty roster (no cells) rolls up to "todo". */
function rollupTone(cells: StageMatrixCell[]): StageStatusTone {
  if (cells.length === 0) return "todo";
  if (cells.every((c) => isTerminal(c.status))) return "done";
  // "active" = has footage-driven progress but is not yet terminal, i.e.
  // ready / partial / in_progress. Spelled out as (not-todo, not-terminal)
  // rather than borrowing isNextUpCandidate, whose name-vs-semantics don't
  // quite line up here (it also returns true for todo).
  if (cells.some((c) => c.status !== "todo" && !isTerminal(c.status))) {
    return "in_progress";
  }
  return "todo";
}

export function buildStageMatrix(
  stages: StageEntry[],
  shooters: ShooterListEntry[],
): StageMatrixRow[] {
  return stages.map((stage) => {
    const cells: StageMatrixCell[] = shooters.map((shooter) => {
      const entry = shooter.stage_statuses.find(
        (e) => e.stage_number === stage.stage_number,
      );
      // A shooter with no entry for this stage is treated as "todo"
      // (nothing recorded yet), which keeps the grid rectangular even if a
      // shooter's project predates a stage being added to the match.
      const status: StageStatus = entry?.status ?? "todo";
      return { shooter, status, tone: statusTone(status) };
    });
    return {
      stageNumber: stage.stage_number,
      stageName: stage.stage_name,
      cells,
      rollupTone: rollupTone(cells),
      auditedCount: cells.filter((c) => c.status === "audited").length,
    };
  });
}

export function matchTotals(
  rows: StageMatrixRow[],
  shooters: ShooterListEntry[],
): MatchTotals {
  const totalShooterStages = rows.length * shooters.length;
  const auditedShooterStages = rows.reduce((n, r) => n + r.auditedCount, 0);
  // Every non-empty row is exactly one of: fully done (all cells terminal),
  // untouched (all cells todo), or in progress (mixed). Computed explicitly
  // -- not by subtraction -- so the three buckets stay mutually consistent
  // with rollupTone and can never go negative. A zero-cell row (only
  // possible with no shooters, which the Overview never renders as this
  // variant) counts as untouched, matching rollupTone's empty -> "todo".
  const allTerminal = (r: StageMatrixRow) =>
    r.cells.length > 0 && r.cells.every((c) => isTerminal(c.status));
  const allTodo = (r: StageMatrixRow) =>
    r.cells.every((c) => c.status === "todo");
  const stagesFullyDone = rows.filter(allTerminal).length;
  const stagesUntouched = rows.filter(allTodo).length;
  const stagesInProgress = rows.filter(
    (r) => !allTerminal(r) && !allTodo(r),
  ).length;
  return {
    totalShooterStages,
    auditedShooterStages,
    auditedPct:
      totalShooterStages > 0
        ? Math.round((auditedShooterStages / totalShooterStages) * 100)
        : 0,
    stagesFullyDone,
    stagesInProgress,
    stagesUntouched,
    hasAnyFootage: shooters.some((s) => s.video_count > 0),
  };
}
