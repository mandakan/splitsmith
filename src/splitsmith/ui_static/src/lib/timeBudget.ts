/**
 * Time budget (UX PR 7, spec 2026-09-13 s4.6).
 *
 * Where the seconds of a stage went: each shot's split accrues to its
 * stored interval class (the draw is `first_shot`; an unclassified shot
 * accrues to "unclassified"). On a stage broken down (#1528) the confirmed
 * regions take the seconds they cover first: inside each gap after the
 * draw, reload regions' time is Reload, then movement regions' Movement,
 * then activation regions' Activation, and only the rest goes to the gap's
 * class. The Draw is always the timer draw, beep to first shot, split into
 * reaction and presentation when a confirmed draw region's start was moved
 * (``drawParts``). The sum of the segments is the stage time (the last
 * shot's time from the beep) to the millisecond either way. Per type: the
 * total, the share, the count (gaps that gave it time), the average, the
 * delta against the shooter's own match distribution for that type, and the
 * outliers (a gap's share longer than twice the type's median on the match;
 * decision 2, 2026-09-14). A stage whose start is not standard (a table
 * start, an unloaded gun) has no Draw delta or outlier: its draw is not
 * compared with holstered ones. Pure; the page renders it.
 */
import type { ChipTick } from "@/components/ui/Chip";
import type { CoachIntervalClass, CoachMatchDistributions, CoachShot, StageEvent } from "@/lib/api";
import { confirmedEvents, drawParts } from "@/lib/events";

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
  /** The Draw only: its split at a moved draw region (reaction + presentation = seconds). */
  parts?: { reaction: number; presentation: number } | null;
}

/** What else a stage's budget reads beyond its shots (#1528). */
export interface BudgetContext {
  /** The stage's regions; only confirmed ones count. */
  events?: readonly StageEvent[];
  /** False for a non-standard start: its Draw is not compared with the match. */
  standardStart?: boolean;
}

type Span = [number, number];

/** The union of ``spans``, sorted and merged. */
function union(spans: Span[]): Span[] {
  const sorted = spans.filter(([a, b]) => b > a).sort((x, y) => x[0] - y[0]);
  const out: Span[] = [];
  for (const [a, b] of sorted) {
    const last = out[out.length - 1];
    if (last && a <= last[1]) last[1] = Math.max(last[1], b);
    else out.push([a, b]);
  }
  return out;
}

/** ``spans`` clipped to ``[lo, hi]`` with ``taken`` removed. */
function clipMinus(spans: Span[], lo: number, hi: number, taken: Span[]): Span[] {
  let pieces: Span[] = spans.map(([a, b]) => [Math.max(a, lo), Math.min(b, hi)] as Span).filter(([a, b]) => b > a);
  for (const [ta, tb] of taken) {
    pieces = pieces.flatMap(([a, b]): Span[] => {
      if (tb <= a || ta >= b) return [[a, b]];
      const left: Span[] = ta > a ? [[a, ta]] : [];
      const right: Span[] = tb < b ? [[tb, b]] : [];
      return [...left, ...right];
    });
  }
  return pieces;
}

const length = (spans: Span[]) => spans.reduce((n, [a, b]) => n + (b - a), 0);

/** The region kinds that take a gap's seconds, in the order they take them. */
const REGION_CLASSES: { kind: StageEvent["kind"]; cls: BudgetClass }[] = [
  { kind: "reload", cls: "reload" },
  { kind: "movement", cls: "movement" },
  { kind: "activation", cls: "activation" },
];

/** One gap's seconds by class: the regions' first, the rest to ``own``. */
function gapShares(lo: number, hi: number, own: BudgetClass, regions: Map<BudgetClass, Span[]>): Map<BudgetClass, number> {
  const out = new Map<BudgetClass, number>();
  const taken: Span[] = [];
  for (const { cls } of REGION_CLASSES) {
    const pieces = clipMinus(regions.get(cls) ?? [], lo, hi, union(taken));
    const sec = length(pieces);
    if (sec > 0) {
      out.set(cls, (out.get(cls) ?? 0) + sec);
      taken.push(...pieces);
    }
  }
  const rest = hi - lo - length(union(taken));
  if (rest > 1e-9 || out.size === 0) out.set(own, (out.get(own) ?? 0) + Math.max(0, rest));
  return out;
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

export function timeBudget(
  shots: CoachShot[],
  distributions: CoachMatchDistributions | null,
  context: BudgetContext = {},
): TimeBudget {
  const total = shots.reduce((sum, s) => sum + s.split, 0);
  const confirmed = confirmedEvents([...(context.events ?? [])]);
  const standard = context.standardStart ?? true;
  const regions = new Map<BudgetClass, Span[]>();
  for (const { kind, cls } of REGION_CLASSES) {
    regions.set(cls, union(confirmed.filter((e) => e.kind === kind).map((e) => [e.start, e.end] as Span)));
  }
  // A non-standard start's draw is not a draw to measure against the match's.
  const compared = (cls: BudgetClass) => (cls === "first_shot" && !standard ? null : baseline(distributions, cls));
  const acc = new Map<BudgetClass, { seconds: number; count: number; outliers: BudgetOutlier[] }>();
  const ordered = [...shots].sort((a, b) => a.time_from_beep - b.time_from_beep);
  ordered.forEach((s, i) => {
    const own: BudgetClass = s.interval_class ?? "unclassified";
    // The draw is the timer's, whole; every later gap gives the regions
    // inside it their seconds first.
    const shares =
      i === 0 || confirmed.length === 0
        ? new Map<BudgetClass, number>([[own, s.split]])
        : gapShares(ordered[i - 1].time_from_beep, s.time_from_beep, own, regions);
    for (const [cls, seconds] of shares) {
      const a = acc.get(cls) ?? { seconds: 0, count: 0, outliers: [] };
      a.seconds += seconds;
      a.count += 1;
      const median = compared(cls)?.median_s ?? null;
      if (median != null && median > 0 && seconds > OUTLIER_FACTOR * median) {
        a.outliers.push({ shotNumber: s.shot_number, seconds });
      }
      acc.set(cls, a);
    }
  });
  const first = ordered.length ? ordered[0].time_from_beep : null;
  const segments: BudgetSegment[] = BUDGET_ORDER.filter((c) => acc.has(c)).map((cls) => {
    const a = acc.get(cls)!;
    const avg = a.count > 0 ? a.seconds / a.count : null;
    const mean = compared(cls)?.mean_s ?? null;
    return {
      cls,
      seconds: a.seconds,
      count: a.count,
      share: total > 0 ? a.seconds / total : 0,
      avg,
      vsMatch: avg != null && mean != null ? avg - mean : null,
      outliers: a.outliers,
      ...(cls === "first_shot" ? { parts: drawParts(first, confirmed) } : {}),
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
  perStage: ({ stageNumber: number; stageName: string; shots: CoachShot[] } & BudgetContext)[],
  distributions: CoachMatchDistributions | null,
): MatchBudget {
  const stages = perStage.map((s) => ({
    ...timeBudget(s.shots, distributions, { events: s.events, standardStart: s.standardStart }),
    stageNumber: s.stageNumber,
    stageName: s.stageName,
  }));
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
