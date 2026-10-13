/**
 * Where the phone's Audit page goes next. The order is desktop Audit's
 * conveyor (``computeAuditNextStep``: the shooter's next stage, then the
 * next shooter with stages left); this module only turns a step into a
 * stage to open, and lists the shooters and stages the title's picker
 * offers.
 */
import type { ShooterListEntry, StageStatus } from "@/lib/api";
import type { AuditNextStep } from "@/lib/audit-next-step";

export interface AuditTarget {
  slug: string;
  stage: number;
}

/** The stage a step opens, or null when the conveyor is finished. A next
 *  shooter opens their own first stage: stage numbers are not 1..N. */
export function nextTarget(step: AuditNextStep, shooters: ReadonlyArray<ShooterListEntry>): AuditTarget | null {
  if (step.kind === "stage") return { slug: step.nextSlug, stage: step.nextStage };
  if (step.kind === "shooter") {
    const first = shooters
      .find((s) => s.slug === step.nextSlug)
      ?.stage_statuses.map((s) => s.stage_number)
      .sort((a, b) => a - b)[0];
    return first == null ? null : { slug: step.nextSlug, stage: first };
  }
  return null;
}

export interface JumpShooter {
  slug: string;
  name: string;
  current: boolean;
  stages: Array<{ stageNumber: number; status: StageStatus }>;
}

/** The picker's rows: every shooter in the shell's order, each with their
 *  own stages in stage order. */
export function jumpShooters(shooters: ReadonlyArray<ShooterListEntry>, currentSlug: string): JumpShooter[] {
  return shooters.map((s) => ({
    slug: s.slug,
    name: s.name,
    current: s.slug === currentSlug,
    stages: s.stage_statuses
      .map((st) => ({ stageNumber: st.stage_number, status: st.status }))
      .sort((a, b) => a.stageNumber - b.stageNumber),
  }));
}
