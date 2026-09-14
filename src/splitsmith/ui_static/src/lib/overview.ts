/**
 * Overview derivation (UX PR 3, spec 2026-09-13 s4.2).
 *
 * Pure functions from one triage GET (plus the shooter list and the
 * shell's jobs snapshot) to the stage pipeline table: one row per stage,
 * one cell per shooter, one action per cell, and the five headline stats.
 * The page renders these; it never derives on its own, so the "what is
 * next" rule lives in exactly one place (`nextAction`) and the header's
 * primary button and the current row cannot disagree.
 */
import type { Job, ShooterListEntry, StageStatus, TriageCell, TriageResponse } from "@/lib/api";
import { isJobActive } from "@/lib/jobs";
import { countsAsDone } from "@/lib/stageStatus";

export type RowAction =
  | { kind: "add_footage" }
  | { kind: "confirm_beep" }
  | { kind: "running" }
  | { kind: "audit"; accept: boolean }
  | { kind: "splits" }
  | { kind: "none" };

/** Loop order, earliest first. `worst` on a row is the lowest-ranked
 *  action among its cells. */
const ACTION_RANK: Record<RowAction["kind"], number> = {
  add_footage: 0,
  confirm_beep: 1,
  running: 2,
  audit: 3,
  splits: 4,
  none: 5,
};

export interface OverviewCell {
  slug: string;
  shooterName: string;
  status: StageStatus;
  videoCount: number;
  beepTime: number | null;
  beepReviewed: boolean;
  beepConfidence: number | null;
  shotCount: number;
  flagCount: number;
  draw: number | null;
  avgSplit: number | null;
  timeSeconds: number;
  /** `countsAsDone(status)`: the only state whose figures get ink. */
  audited: boolean;
  /** Has figures but is not audited: shown dimmed, never hidden. */
  provisional: boolean;
  /** A detect / trim / shot-detect job for this shooter and stage is in flight. */
  running: boolean;
  action: RowAction;
}

export interface OverviewRow {
  stageNumber: number;
  stageName: string;
  /** One per shooter, in shooter-list order. */
  cells: OverviewCell[];
  /** The lead (audio-source or only) shooter's cell: the parent row's figures. */
  lead: OverviewCell | null;
  auditedCount: number;
  worst: RowAction;
}

const PIPELINE_KINDS = new Set(["detect_beep", "trim", "shot_detect"]);

export function rowAction(cell: Omit<OverviewCell, "action">, _threshold: number): RowAction {
  if (cell.status === "skipped") return { kind: "none" };
  if (cell.videoCount === 0) return { kind: "add_footage" };
  if (cell.beepTime == null || !cell.beepReviewed) return { kind: "confirm_beep" };
  if (cell.running) return { kind: "running" };
  if (cell.audited) return { kind: "splits" };
  return { kind: "audit", accept: cell.shotCount > 0 };
}

function toCell(raw: TriageCell, jobs: Job[], threshold: number): OverviewCell {
  const audited = countsAsDone(raw.status);
  const running = jobs.some(
    (j) =>
      isJobActive(j) &&
      PIPELINE_KINDS.has(j.kind) &&
      j.shooter_slug === raw.slug &&
      j.stage_number === raw.stage_number,
  );
  const base: Omit<OverviewCell, "action"> = {
    slug: raw.slug,
    shooterName: raw.shooter_name,
    status: raw.status,
    videoCount: raw.video_count,
    beepTime: raw.beep_time,
    beepReviewed: raw.beep_reviewed,
    beepConfidence: raw.beep_confidence,
    shotCount: raw.shot_count,
    flagCount: raw.anomalies.length,
    draw: raw.draw,
    avgSplit: raw.avg_split,
    timeSeconds: raw.time_seconds,
    audited,
    provisional: !audited && (raw.draw != null || raw.avg_split != null || raw.shot_count > 0),
    running,
  };
  return { ...base, action: rowAction(base, threshold) };
}

export function buildOverviewRows(args: {
  triage: TriageResponse;
  shooters: ShooterListEntry[];
  leadSlug: string | null;
  jobs: Job[];
}): OverviewRow[] {
  const { triage, shooters, leadSlug, jobs } = args;
  const order = new Map(shooters.map((s, i) => [s.slug, i]));
  const byStage = new Map<number, { name: string; cells: OverviewCell[] }>();
  for (const raw of triage.cells) {
    const entry = byStage.get(raw.stage_number) ?? { name: raw.stage_name, cells: [] };
    entry.cells.push(toCell(raw, jobs, triage.beep_low_confidence_threshold));
    byStage.set(raw.stage_number, entry);
  }
  return [...byStage.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([stageNumber, { name, cells }]) => {
      cells.sort((a, b) => (order.get(a.slug) ?? 99) - (order.get(b.slug) ?? 99));
      const lead = cells.find((c) => c.slug === leadSlug) ?? cells[0] ?? null;
      const worst = cells.reduce<RowAction>(
        (acc, c) => (ACTION_RANK[c.action.kind] < ACTION_RANK[acc.kind] ? c.action : acc),
        { kind: "none" },
      );
      return {
        stageNumber,
        stageName: name,
        cells,
        lead,
        auditedCount: cells.filter((c) => c.audited).length,
        worst,
      };
    });
}

/** The loop's next step: the first cell (stage order, then shooter
 *  order) whose action is confirm_beep or audit. Null when nothing is
 *  waiting on the user. */
export function nextAction(rows: OverviewRow[]): { row: OverviewRow; cell: OverviewCell } | null {
  for (const row of rows) {
    for (const cell of row.cells) {
      if (cell.action.kind === "confirm_beep" || cell.action.kind === "audit") {
        return { row, cell };
      }
    }
  }
  return null;
}

export interface OverviewStats {
  audited: number;
  total: number;
  needsFootage: number;
  avgDraw: number | null;
  avgSplit: number | null;
  scoredTime: number | null;
}

function mean(values: number[]): number | null {
  if (values.length === 0) return null;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

/** Headline stats over the lead cells: audited count, stages without
 *  footage, draw and split averaged over audited stages only (a
 *  provisional figure never enters an average), scored time summed over
 *  stages with a time. */
export function overviewStats(rows: OverviewRow[]): OverviewStats {
  const leads = rows.map((r) => r.lead).filter((c): c is OverviewCell => c != null);
  const audited = leads.filter((c) => c.audited);
  const times = leads.map((c) => c.timeSeconds).filter((t) => t > 0);
  return {
    audited: audited.length,
    total: rows.length,
    needsFootage: leads.filter((c) => c.videoCount === 0).length,
    avgDraw: mean(audited.map((c) => c.draw).filter((d): d is number => d != null)),
    avgSplit: mean(audited.map((c) => c.avgSplit).filter((d): d is number => d != null)),
    scoredTime: times.length ? times.reduce((a, b) => a + b, 0) : null,
  };
}

/** 316.9 -> "5:16.9"; under a minute, seconds with two decimals. */
export function formatClock(seconds: number): string {
  if (seconds < 60) return seconds.toFixed(2);
  const m = Math.floor(seconds / 60);
  const s = seconds - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}
