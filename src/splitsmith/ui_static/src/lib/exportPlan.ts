/**
 * Export page derivations (spec 2026-09-13 s4.8). Pure functions from the
 * export overview rows: which stages a mode can export, and for the ones
 * it cannot, why in one line plus the fix; the duration estimate; the
 * summary rail's rows. Hosted copy never names a drive: the only fix for
 * an unreachable source on a hosted install is a re-upload from Footage.
 */
import type { StageExportStatus } from "@/lib/api";

export type ExportMode = "single" | "trims" | "compare";

export type FixTarget = "audit" | "footage" | "relink" | "scores";

export interface StageBlock {
  /** One line, no issue numbers. */
  reason: string;
  /** Rendered as the row's link when present. */
  fix?: { label: string; to: FixTarget };
  /** Attention (amber) rather than a plain grey blocker. */
  warn?: boolean;
}

export interface ExportStageRow {
  stage: StageExportStatus;
  /** Stage time from the project (the overview rows do not carry it). */
  time: number | null;
  shots: number | null;
  eligible: boolean;
  block: StageBlock | null;
}

/** The blocker ladder, first match wins. */
export function stageBlock(
  stage: StageExportStatus,
  time: number | null,
  mode: ExportMode,
  hosted: boolean,
): StageBlock | null {
  if (stage.skipped) return { reason: "Skipped" };
  if (stage.source_reachable === false) {
    return hosted
      ? {
          reason: "Upload missing -- the original upload is no longer stored",
          fix: { label: "Re-upload", to: "footage" },
          warn: true,
        }
      : {
          reason: "Source offline -- the video file is not reachable",
          fix: { label: "Relink", to: "relink" },
          warn: true,
        };
  }
  if (mode === "compare") return null;
  if (!stage.has_primary) return { reason: "No footage", fix: { label: "Footage", to: "footage" } };
  if (time === null || time <= 0) return { reason: "No stage time", fix: { label: "Import scores", to: "scores" } };
  if (mode === "trims") {
    return stage.ready_to_trim ? null : { reason: "No confirmed beep", fix: { label: "Audit", to: "audit" } };
  }
  return stage.ready_to_export ? null : { reason: "Not audited yet", fix: { label: "Audit", to: "audit" } };
}

export function exportRows(
  stages: StageExportStatus[],
  times: Map<number, number>,
  mode: ExportMode,
  hosted: boolean,
): ExportStageRow[] {
  return stages.map((stage) => {
    const time = times.get(stage.stage_number) ?? null;
    const block = stageBlock(stage, time, mode, hosted);
    return {
      stage,
      time: time !== null && time > 0 ? time : null,
      shots: stage.audit_shot_count > 0 ? stage.audit_shot_count : null,
      eligible: block === null,
      block,
    };
  });
}

export interface EstimateOptions {
  mode: ExportMode;
  head: number;
  tail: number;
  transitionKind: string;
  transitionSeconds: number;
  titleKind: string;
  titleSeconds: number;
}

/** Sum of the selected stage times plus pads, transitions between
 *  stages and a slate per stage. Trims and the grid pad with the
 *  project's own buffers (passed as head / tail) and add nothing else. */
export function estimateDuration(
  selected: number[],
  times: Map<number, number>,
  opts: EstimateOptions,
): number {
  let duration = 0;
  for (const n of selected) duration += (times.get(n) ?? 0) + opts.head + opts.tail;
  if (opts.mode !== "single") return duration;
  const count = selected.length;
  if (opts.transitionKind !== "none" && count > 1) duration += opts.transitionSeconds * (count - 1);
  if (opts.titleKind === "slate" && count > 0) duration += opts.titleSeconds * count;
  return duration;
}

export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

export interface SummaryLine {
  label: string;
  value: string;
  /** An "off" / default value, shown muted. */
  dim?: boolean;
}

export function summaryLines(args: {
  mode: ExportMode;
  selected: number;
  eligible: number;
  head: number;
  tail: number;
  transitionKind: string;
  transitionSeconds: number;
  titleKind: string;
  overlay: boolean;
  gridCamera: string | null;
  reference: string | null;
  canvas: string | null;
}): SummaryLine[] {
  const lines: SummaryLine[] = [{ label: "Stages", value: `${args.selected} / ${args.eligible}` }];
  if (args.mode === "trims") {
    lines.push({ label: "Grid camera", value: args.gridCamera ?? "primary", dim: args.gridCamera === null });
    return lines;
  }
  if (args.mode === "compare") {
    lines.push({ label: "Reference", value: args.reference ?? "—", dim: args.reference === null });
    lines.push({ label: "Canvas", value: args.canvas ?? "—" });
    return lines;
  }
  lines.push({ label: "Padding", value: `${args.head.toFixed(1)} / ${args.tail.toFixed(1)} s` });
  lines.push(
    args.transitionKind === "none"
      ? { label: "Transitions", value: "cut", dim: true }
      : { label: "Transitions", value: `${args.transitionKind} ${args.transitionSeconds.toFixed(1)} s` },
  );
  lines.push(
    args.titleKind === "none"
      ? { label: "Titles", value: "off", dim: true }
      : { label: "Titles", value: args.titleKind },
  );
  lines.push(args.overlay ? { label: "Overlay", value: "on" } : { label: "Overlay", value: "off", dim: true });
  return lines;
}

/** "Stage 3", "Stages 1-3" for a contiguous run, "Stages 1, 2, 4"
 *  otherwise. A range label over a gapped selection would be a lie, and
 *  gaps are normal since #521 let a stage be removed without renumbering. */
export function stageLabel(stages: number[]): string {
  if (stages.length === 0) return "No stages";
  if (stages.length === 1) return `Stage ${stages[0]}`;
  const sorted = [...stages].sort((a, b) => a - b);
  const contiguous = sorted.every((n, i) => i === 0 || n === sorted[i - 1] + 1);
  return contiguous
    ? `Stages ${sorted[0]}-${sorted[sorted.length - 1]}`
    : `Stages ${sorted.join(", ")}`;
}
