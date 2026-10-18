/**
 * The phone Breakdown's stage list: every stage of one shooter, in order,
 * with what Breakdown can say about it before it is opened. Proposals are
 * seeded when a stage opens, so the list counts confirmed regions only
 * (the project payload's ``figures.regions``).
 */
import type { StageEntry } from "@/lib/api";

export interface IndexRow {
  stage: number;
  name: string;
  /** False for a skipped stage or one without a stage time: nothing to break down. */
  open: boolean;
  /** One short line under the name. */
  note: string;
}

export function indexRows(stages: StageEntry[]): IndexRow[] {
  return [...stages]
    .sort((a, b) => a.stage_number - b.stage_number)
    .map((s) => {
      const open = !s.skipped && s.time_seconds > 0;
      const regions = s.figures?.regions ?? 0;
      const shots = s.figures?.shot_count ?? 0;
      let note: string;
      if (s.skipped) note = "Skipped";
      else if (!open) note = "No stage time";
      else if (regions > 0) note = `${regions} ${regions === 1 ? "region" : "regions"}`;
      else if (shots > 0) note = "Not broken down";
      else note = "No shots";
      return { stage: s.stage_number, name: s.stage_name || `Stage ${s.stage_number}`, open, note };
    });
}
