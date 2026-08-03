import type { ShooterListEntry } from "@/lib/api";

/**
 * Next step in the audit conveyor. `Save & next` chains within the current
 * shooter, then jumps to the next shooter with work remaining, then lands
 * in a finish state. Stage-local prev/next (the `[` / `]` keys) stays
 * within-shooter -- only the conveyor CTA crosses shooter boundaries.
 */
export type AuditNextStep =
  | {
      kind: "stage";
      nextSlug: string;
      nextStage: number;
      label: string;
      sublabel: string;
    }
  | {
      kind: "shooter";
      nextSlug: string;
      label: string;
      sublabel: string;
    }
  | { kind: "finish"; label: string; sublabel: string };

export function computeAuditNextStep(args: {
  shooters: ShooterListEntry[];
  activeSlug: string | undefined;
  stages: { stageNumber: number; stageName: string }[];
  activeStage: number | null;
}): AuditNextStep {
  const { shooters, activeSlug, stages, activeStage } = args;
  if (activeStage == null || stages.length === 0) {
    return { kind: "finish", label: "Save", sublabel: "No more stages" };
  }
  const stageIdx = stages.findIndex((s) => s.stageNumber === activeStage);
  if (stageIdx === -1) {
    return { kind: "finish", label: "Save", sublabel: "No more stages" };
  }
  if (stageIdx < stages.length - 1) {
    const next = stages[stageIdx + 1];
    return {
      kind: "stage",
      nextSlug: activeSlug ?? "",
      nextStage: next.stageNumber,
      label: "Save & next stage",
      sublabel: `Stage ${String(next.stageNumber).padStart(2, "0")} · ${next.stageName}`,
    };
  }
  // Last stage of current shooter; look for the next shooter with work
  // remaining. We rotate the list starting after the active shooter so
  // partially-done shooters earlier in the list still get picked up after
  // a full pass.
  if (activeSlug && shooters.length > 1) {
    const myIdx = shooters.findIndex((s) => s.slug === activeSlug);
    if (myIdx !== -1) {
      const rotated = [...shooters.slice(myIdx + 1), ...shooters.slice(0, myIdx)];
      const next = rotated.find((s) => s.stages_audited < s.stages_total);
      if (next) {
        // The pickup stage is read off the next shooter's OWN list, not
        // assumed to be 01. Stage numbers stopped being 1..N when the
        // stage-list editor shipped (#521): on a match whose stage 1 was
        // removed the next shooter starts at 02, and the hardcoded "01"
        // named a stage that no longer exists. `stage_statuses` carries
        // one entry per stage in that shooter's project and the server
        // emits it `sorted(...)` by stage_number, so `.at(0)` is their
        // first stage. Falls back to the bare name rather than inventing
        // a number when the list is empty.
        const firstStage = next.stage_statuses.at(0)?.stage_number;
        return {
          kind: "shooter",
          nextSlug: next.slug,
          label: "Save & next shooter",
          sublabel:
            firstStage != null
              ? `${next.name} · stage ${String(firstStage).padStart(2, "0")}`
              : next.name,
        };
      }
    }
  }
  return {
    kind: "finish",
    label: "Save & finish audit",
    sublabel: "No more stages or shooters",
  };
}
