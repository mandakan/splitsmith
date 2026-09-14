/**
 * Splits table derivation (UX PR 4, spec 2026-09-13 s4.5).
 *
 * Pure functions from the per-shooter project payloads (one GET per
 * shooter, on the owner surface and on the anonymous share surface alike)
 * to the Splits page: one row per stage with one cell per shooter, runs
 * of stages with nothing to show collapsed into one line, the five
 * headline stats, and the scoreboard's own totals. The page renders
 * these; it derives nothing on its own, so the owner table, the phone
 * cards and the share table cannot disagree about what a stage shows.
 *
 * Provenance rule: splits (draw, avg, fastest, shots) exist only on an
 * audited stage -- the server's ``figures`` field is what the audit
 * produced. The scorecard is imported and shows dimmed whenever the
 * scoreboard has it, audited or not.
 */
import type { MatchProject, ShooterListEntry, StageEntry, StageScorecard, StageStatus } from "@/lib/api";
import { countsAsDone, deriveStageStatus } from "@/lib/stageStatus";

export interface SplitsCell {
  slug: string;
  shooterName: string;
  status: StageStatus;
  audited: boolean;
  skipped: boolean;
  draw: number | null;
  avgSplit: number | null;
  fastestSplit: number | null;
  shotCount: number;
  timeSeconds: number;
  scorecard: StageScorecard | null;
  /** Non-ignored registered videos on the stage. */
  videoCount: number;
}

export type CollapseReason = "no_footage" | "not_audited" | "no_video";

export type SplitsRow =
  | {
      kind: "stage";
      stageNumber: number;
      stageName: string;
      /** One per shooter with a project, in shooter-list order. */
      cells: SplitsCell[];
      /** The filtered shooter's cell, else the lead's, else the first. */
      lead: SplitsCell | null;
      auditedCount: number;
    }
  | {
      kind: "collapsed";
      from: number;
      to: number;
      firstName: string;
      lastName: string;
      reason: CollapseReason;
      count: number;
      /** The collapsed stages' lead cells: hidden from the table, still
       *  counted by the stats and the scoreboard totals, so the owner and
       *  share surfaces (which collapse differently) sum the same stages. */
      leads: SplitsCell[];
    };

function toCell(entry: StageEntry, shooter: ShooterListEntry): SplitsCell {
  const status = entry.status ?? deriveStageStatus(entry);
  const f = entry.figures ?? null;
  return {
    slug: shooter.slug,
    shooterName: shooter.name,
    status,
    audited: countsAsDone(status),
    skipped: status === "skipped",
    draw: f?.draw ?? null,
    avgSplit: f?.avg_split ?? null,
    fastestSplit: f?.fastest_split ?? null,
    shotCount: f?.shot_count ?? 0,
    timeSeconds: entry.time_seconds,
    scorecard: entry.scorecard,
    videoCount: entry.videos.filter((v) => v.role !== "ignored").length,
  };
}

interface StageAcc {
  stageNumber: number;
  stageName: string;
  cells: SplitsCell[];
}

/** Why a stage has no splits row. Owner: footage missing vs. not yet
 *  audited (skipped counts as not audited: the skip was a decision, the
 *  row still names the stage). Share: always "no video" -- audit state
 *  is the owner's business. */
function collapseReason(lead: SplitsCell | null, share: boolean): CollapseReason {
  if (share) return "no_video";
  if (lead == null || lead.videoCount === 0) return "no_footage";
  return "not_audited";
}

export function buildSplitsRows(args: {
  projects: Record<string, MatchProject | null>;
  shooters: ShooterListEntry[];
  leadSlug: string | null;
  filterSlug: string | null;
  share: boolean;
}): SplitsRow[] {
  const { projects, shooters, leadSlug, filterSlug, share } = args;
  const byStage = new Map<number, StageAcc>();
  for (const shooter of shooters) {
    const p = projects[shooter.slug];
    if (!p) continue;
    for (const entry of p.stages) {
      if (entry.placeholder) continue;
      const acc = byStage.get(entry.stage_number) ?? {
        stageNumber: entry.stage_number,
        stageName: entry.stage_name,
        cells: [],
      };
      acc.cells.push(toCell(entry, shooter));
      byStage.set(entry.stage_number, acc);
    }
  }
  const stages = [...byStage.values()].sort((a, b) => a.stageNumber - b.stageNumber);

  const rows: SplitsRow[] = [];
  for (const st of stages) {
    const cells = filterSlug ? st.cells.filter((c) => c.slug === filterSlug) : st.cells;
    const lead =
      (filterSlug ? cells.find((c) => c.slug === filterSlug) : cells.find((c) => c.slug === leadSlug)) ??
      cells[0] ??
      null;
    const auditedCount = cells.filter((c) => c.audited).length;
    // A stage row needs something to show: the lead's splits, or (on
    // "All") any shooter's. Owner not-audited stages keep a row of their
    // own because each carries its own Audit link.
    const showRow = auditedCount > 0 || (!share && lead != null && lead.videoCount > 0);
    if (showRow) {
      rows.push({ kind: "stage", stageNumber: st.stageNumber, stageName: st.stageName, cells, lead, auditedCount });
      continue;
    }
    const reason = collapseReason(lead, share);
    const prev = rows[rows.length - 1];
    if (prev && prev.kind === "collapsed" && prev.reason === reason && prev.to === st.stageNumber - 1) {
      prev.to = st.stageNumber;
      prev.lastName = st.stageName;
      prev.count += 1;
      if (lead) prev.leads.push(lead);
    } else {
      rows.push({
        kind: "collapsed",
        from: st.stageNumber,
        to: st.stageNumber,
        firstName: st.stageName,
        lastName: st.stageName,
        reason,
        count: 1,
        leads: lead ? [lead] : [],
      });
    }
  }
  return rows;
}

export interface SplitsStats {
  avgDraw: number | null;
  avgSplit: number | null;
  fastestSplit: number | null;
  shots: number;
  scoredTime: number | null;
  audited: number;
  total: number;
}

function leads(rows: SplitsRow[]): SplitsCell[] {
  return rows.flatMap((r) => (r.kind === "stage" ? (r.lead ? [r.lead] : []) : r.leads));
}

function mean(values: number[]): number | null {
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
}

/** Headline stats over the lead cells: draw and split averaged over
 *  audited stages only, fastest split the minimum of those, shots summed
 *  over them; scored time is the scoreboard sum over every stage with a
 *  time (the same rule Overview uses), independent of audit. `total`
 *  counts every stage, collapsed runs included. */
export function splitsStats(rows: SplitsRow[]): SplitsStats {
  const all = leads(rows);
  const audited = all.filter((c) => c.audited);
  const times = all.map((c) => c.timeSeconds).filter((t) => t > 0);
  const fastest = audited.map((c) => c.fastestSplit).filter((v): v is number => v != null);
  return {
    avgDraw: mean(audited.map((c) => c.draw).filter((v): v is number => v != null)),
    avgSplit: mean(audited.map((c) => c.avgSplit).filter((v): v is number => v != null)),
    fastestSplit: fastest.length ? Math.min(...fastest) : null,
    shots: audited.reduce((sum, c) => sum + c.shotCount, 0),
    scoredTime: times.length ? times.reduce((a, b) => a + b, 0) : null,
    audited: audited.length,
    total: rows.reduce((n, r) => n + (r.kind === "stage" ? 1 : r.count), 0),
  };
}

export interface ScoreboardTotals {
  time: number;
  hitFactor: number | null;
  alphas: number;
  charlies: number;
  deltas: number;
  misses: number;
}

/** The scoreboard's own totals over the lead cells that carry a
 *  scorecard; hit factor derived as points over time. Null when no lead
 *  is scored, so an unscored match shows no footer. Collapsed runs
 *  contribute through their hidden leads, so the share table (which
 *  collapses more) sums the same stages as the owner table. */
export function scoreboardTotals(rows: SplitsRow[]): ScoreboardTotals | null {
  const scored = leads(rows).filter((c) => c.scorecard != null);
  if (scored.length === 0) return null;
  const sum = (pick: (s: StageScorecard) => number | null) =>
    scored.reduce((acc, c) => acc + (pick(c.scorecard as StageScorecard) ?? 0), 0);
  const time = scored.reduce((acc, c) => acc + c.timeSeconds, 0);
  const points = sum((s) => s.stage_points);
  return {
    time,
    hitFactor: time > 0 ? points / time : null,
    alphas: sum((s) => s.alphas),
    charlies: sum((s) => s.charlies),
    deltas: sum((s) => s.deltas),
    misses: sum((s) => s.misses),
  };
}

/** "12A 6C 1D", then M / NS / P only when non-zero, then DQ. A card
 *  with no hit counts at all renders a dash rather than "0A 0C 0D". */
export function formatHits(sc: StageScorecard): string {
  if (sc.alphas == null && sc.charlies == null && sc.deltas == null) return "—";
  const parts = [`${sc.alphas ?? 0}A`, `${sc.charlies ?? 0}C`, `${sc.deltas ?? 0}D`];
  if (sc.misses) parts.push(`${sc.misses}M`);
  if (sc.no_shoots) parts.push(`${sc.no_shoots}NS`);
  if (sc.procedurals) parts.push(`${sc.procedurals}P`);
  if (sc.dq) parts.push("DQ");
  return parts.join(" ");
}

/** Where "Play all" starts: the first stage row whose lead is audited. */
export function firstPlayable(rows: SplitsRow[]): { slug: string; stageNumber: number } | null {
  for (const r of rows) {
    if (r.kind === "stage" && r.lead?.audited) return { slug: r.lead.slug, stageNumber: r.stageNumber };
  }
  return null;
}

/** The latest scorecard sync on the project, for the header sub-line. */
export function scorecardSyncedAt(project: MatchProject | null): string | null {
  if (!project) return null;
  let latest: string | null = null;
  for (const s of project.stages) {
    if (s.scorecard_updated_at && (latest == null || s.scorecard_updated_at > latest)) {
      latest = s.scorecard_updated_at;
    }
  }
  return latest;
}
