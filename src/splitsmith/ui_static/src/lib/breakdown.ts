/**
 * Breakdown's counts (#1371). Breakdown is optional by design (epic #1370):
 * these are quiet tallies, never a missing step, and nothing in Overview,
 * the next step, readiness or the export gate reads them.
 */
import type { StageEvent, StageFigures } from "@/lib/api";

/** A stage's regions by source: ``confirmed`` (manual, what every output
 *  reads) and ``proposed`` (auto, shown on the lanes only). */
export function regionCounts(events: readonly StageEvent[]): { confirmed: number; proposed: number } {
  let confirmed = 0;
  let proposed = 0;
  for (const e of events) {
    if (e.source === "manual") confirmed += 1;
    else proposed += 1;
  }
  return { confirmed, proposed };
}

/** The Breakdown nav row's count: confirmed regions over every stage's
 *  ``figures.regions``, or ``undefined`` when there are none, so the row
 *  shows nothing rather than a zero. */
export function navRegionCount(
  stages: readonly { figures?: Pick<StageFigures, "regions"> | null }[] | null | undefined,
): number | undefined {
  let total = 0;
  for (const s of stages ?? []) total += s.figures?.regions ?? 0;
  return total > 0 ? total : undefined;
}
