/**
 * Time budget (UX PR 7, spec 2026-09-13 s4.6).
 *
 * Where the seconds of a stage went: each shot's split accrues to its
 * stored interval class (the draw is `first_shot`; an unclassified shot
 * accrues to "unclassified"). The sum of the segments is the stage time
 * (the last shot's time from the beep) to the millisecond -- there is no
 * other input. Per type: the total, the share, the count, the average,
 * the delta against the shooter's own match distribution for that type,
 * and the outliers (an interval longer than twice the type's median on
 * the match; decision 2, 2026-09-14). Pure; the page renders it.
 */
import type { ChipTick } from "@/components/ui/Chip";
import type { CoachIntervalClass, CoachMatchDistributions, CoachShot } from "@/lib/api";

export type BudgetClass = CoachIntervalClass | "unclassified";

/** Taxonomy order, draw first: the order the bar and the rows use. */
export const BUDGET_ORDER: BudgetClass[] = [
  "first_shot",
  "movement",
  "transition",
  "split",
  "reload",
  "activation",
  "unclassified",
];

export const BUDGET_LABEL: Record<BudgetClass, string> = {
  first_shot: "Draw",
  movement: "Movement",
  transition: "Transition",
  split: "Fire",
  reload: "Reload",
  activation: "Activation",
  unclassified: "Unclassified",
};

/** The chip tick each class carries everywhere (one hue per meaning). */
export const BUDGET_TICK: Record<BudgetClass, ChipTick> = {
  first_shot: "draw",
  movement: "movement",
  transition: "transition",
  split: "fire",
  reload: "reload",
  activation: "activation",
  unclassified: "muted",
};

export interface BudgetOutlier {
  shotNumber: number;
  seconds: number;
}

export interface BudgetSegment {
  cls: BudgetClass;
  seconds: number;
  count: number;
  /** seconds / total; 0 when the stage has no time. */
  share: number;
  avg: number | null;
  /** avg minus the match's mean for the class; null without a baseline. */
  vsMatch: number | null;
  outliers: BudgetOutlier[];
}

export interface TimeBudget {
  /** Sum of every split == the last shot's time from the beep. */
  total: number;
  /** Taxonomy order, zero-count classes omitted. */
  segments: BudgetSegment[];
  outlierCount: number;
  /** At least one shot carries a stored class. */
  classified: boolean;
}

const OUTLIER_FACTOR = 2;

/** The class's match baseline. The draw has no distribution row; its
 *  baseline is derived from the payload's per-stage first-shot list. */
function baseline(
  distributions: CoachMatchDistributions | null,
  cls: BudgetClass,
): { mean_s: number | null; median_s: number | null } | null {
  if (!distributions || cls === "unclassified") return null;
  if (cls === "first_shot") {
    const v = (distributions.first_shot_seconds ?? []).filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
    if (v.length === 0) return null;
    const mid = Math.floor(v.length / 2);
    return {
      mean_s: v.reduce((a, b) => a + b, 0) / v.length,
      median_s: v.length % 2 ? v[mid] : (v[mid - 1] + v[mid]) / 2,
    };
  }
  return distributions.distributions.find((d) => d.interval_class === cls) ?? null;
}

export function timeBudget(shots: CoachShot[], distributions: CoachMatchDistributions | null): TimeBudget {
  const total = shots.reduce((sum, s) => sum + s.split, 0);
  const acc = new Map<BudgetClass, { seconds: number; count: number; outliers: BudgetOutlier[] }>();
  for (const s of shots) {
    const cls: BudgetClass = s.interval_class ?? "unclassified";
    const a = acc.get(cls) ?? { seconds: 0, count: 0, outliers: [] };
    a.seconds += s.split;
    a.count += 1;
    const median = baseline(distributions, cls)?.median_s ?? null;
    if (median != null && median > 0 && s.split > OUTLIER_FACTOR * median) {
      a.outliers.push({ shotNumber: s.shot_number, seconds: s.split });
    }
    acc.set(cls, a);
  }
  const segments: BudgetSegment[] = BUDGET_ORDER.filter((c) => acc.has(c)).map((cls) => {
    const a = acc.get(cls)!;
    const avg = a.count > 0 ? a.seconds / a.count : null;
    const mean = baseline(distributions, cls)?.mean_s ?? null;
    return {
      cls,
      seconds: a.seconds,
      count: a.count,
      share: total > 0 ? a.seconds / total : 0,
      avg,
      vsMatch: avg != null && mean != null ? avg - mean : null,
      outliers: a.outliers,
    };
  });
  return {
    total,
    segments,
    outlierCount: segments.reduce((n, s) => n + s.outliers.length, 0),
    classified: shots.some((s) => s.interval_class != null),
  };
}

export interface StageBudget extends TimeBudget {
  stageNumber: number;
  stageName: string;
}

export interface MatchBudget {
  stages: StageBudget[];
  /** The longest stage's total: the bars' shared axis. */
  maxTotal: number;
  /** Share of every classified second by class, across the stages. */
  shareByClass: Partial<Record<BudgetClass, number>>;
  outlierCount: number;
  classifiedCount: number;
}

export function matchBudget(
  perStage: { stageNumber: number; stageName: string; shots: CoachShot[] }[],
  distributions: CoachMatchDistributions | null,
): MatchBudget {
  const stages = perStage.map((s) => ({ ...timeBudget(s.shots, distributions), stageNumber: s.stageNumber, stageName: s.stageName }));
  const seconds = new Map<BudgetClass, number>();
  let classifiedSeconds = 0;
  for (const st of stages) {
    if (!st.classified) continue;
    for (const seg of st.segments) {
      seconds.set(seg.cls, (seconds.get(seg.cls) ?? 0) + seg.seconds);
      classifiedSeconds += seg.seconds;
    }
  }
  const shareByClass: Partial<Record<BudgetClass, number>> = {};
  for (const [cls, s] of seconds) shareByClass[cls] = classifiedSeconds > 0 ? s / classifiedSeconds : 0;
  return {
    stages,
    maxTotal: stages.reduce((m, s) => Math.max(m, s.total), 0),
    shareByClass,
    outlierCount: stages.reduce((n, s) => n + s.outlierCount, 0),
    classifiedCount: stages.filter((s) => s.classified).length,
  };
}
