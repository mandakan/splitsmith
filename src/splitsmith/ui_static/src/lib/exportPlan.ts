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
  /** Exportable in bundle mode, but without splits: reviewed beep and a
   *  time, no audited shots. The row says so; nothing is blocked. */
  bare: boolean;
}

export interface LadderOptions {
  /** The sources live on another machine (a desktop-synced match whose
   *  desktop renders it), so this server's reachability says nothing:
   *  the desktop checks its own before it renders. Skips that rung only. */
  sourcesElsewhere?: boolean;
}

/** The blocker ladder, first match wins. */
export function stageBlock(
  stage: StageExportStatus,
  time: number | null,
  mode: ExportMode,
  hosted: boolean,
  options: LadderOptions = {},
): StageBlock | null {
  if (stage.skipped) return { reason: "Skipped" };
  if (stage.source_reachable === false && !options.sourcesElsewhere) {
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
  // A stage with a reviewed beep and a time renders without shots (the
  // trim, its chapter, the cards, the upload); it only loses the
  // shot-dependent extras, which the row and the rail say. What blocks
  // is the beep: every trim boundary hangs off it, so an unreviewed auto
  // beep is not enough.
  if (stage.ready_to_export || stage.ready_to_export_bare) return null;
  return { reason: "No confirmed beep", fix: { label: "Audit", to: "audit" } };
}

export function exportRows(
  stages: StageExportStatus[],
  times: Map<number, number>,
  mode: ExportMode,
  hosted: boolean,
  options: LadderOptions = {},
): ExportStageRow[] {
  return stages.map((stage) => {
    const time = times.get(stage.stage_number) ?? null;
    const block = stageBlock(stage, time, mode, hosted, options);
    return {
      stage,
      time: time !== null && time > 0 ? time : null,
      shots: stage.audit_shot_count > 0 ? stage.audit_shot_count : null,
      eligible: block === null,
      block,
      bare: mode === "single" && block === null && !stage.ready_to_export,
    };
  });
}

export interface EstimateOptions {
  mode: ExportMode;
  head: number;
  tail: number;
  transitionKind: string;
  transitionSeconds: number;
  /** The output format: an MP4 transition is centred on the cut and adds
   *  no time (#1244); the FCPXML estimate keeps adding one per boundary. */
  format?: "fcpxml" | "fcp7xml" | "mp4";
  /** What the generated cards add (``renderOptionsSeconds``); the
   *  caller has already applied the mode and format rules. */
  cardSeconds?: number;
}

/** Sum of the selected stage times plus pads, transitions between
 *  stages and whatever the cards add. Trims pad with the project's own
 *  buffers (passed as head / tail) and add nothing else; the grid pads
 *  the same way but does take cards. */
export function estimateDuration(
  selected: number[],
  times: Map<number, number>,
  opts: EstimateOptions,
): number {
  let duration = 0;
  for (const n of selected) duration += (times.get(n) ?? 0) + opts.head + opts.tail;
  if (opts.mode === "trims") return duration;
  const count = selected.length;
  if (opts.mode === "single" && opts.format !== "mp4" && opts.transitionKind !== "none" && count > 1) {
    duration += opts.transitionSeconds * (count - 1);
  }
  return duration + (opts.cardSeconds ?? 0);
}

export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

/** How long a job took: "0.3s", "42s", then "m:ss" like a timeline's
 *  length. A fast export rounds to nothing in ``formatDuration``. */
export function formatElapsed(seconds: number): string {
  const tenths = Math.round(Math.max(0, seconds) * 10) / 10;
  if (tenths < 10) return `${tenths.toFixed(1)}s`;
  const whole = Math.round(tenths);
  return whole < 60 ? `${whole}s` : formatDuration(whole);
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
  /** What the rail calls the transition ("Wind up"); unset, the kind itself (#1259). */
  transitionLabel?: string;
  transitionSeconds: number;
  /** ``describeRenderOptions`` for the mode and format; null is off. */
  cards: string | null;
  overlay: boolean;
  /** The overlay style's name ("Plate"); unset reads "on" (Classic). */
  overlayStyle?: string;
  /** The camera choice in words (``camsSummary``); null hides the line
   *  (the shooter has no second camera, or the mode does not take them). */
  cams: string | null;
  /** The YouTube encode preset and sidecar; null when the format has
   *  neither to offer. */
  youtube: boolean | null;
  gridCamera: string | null;
  reference: string | null;
  canvas: string | null;
  /** Selected bundle stages going out without splits (``ExportStageRow.bare``). */
  bare?: number;
}): SummaryLine[] {
  const lines: SummaryLine[] = [{ label: "Stages", value: `${args.selected} / ${args.eligible}` }];
  if (args.mode === "trims") {
    lines.push({ label: "Grid camera", value: args.gridCamera ?? "primary", dim: args.gridCamera === null });
    return lines;
  }
  const cards: SummaryLine =
    args.cards === null ? { label: "Cards", value: "off", dim: true } : { label: "Cards", value: args.cards };
  if (args.mode === "compare") {
    lines.push({ label: "Reference", value: args.reference ?? "—", dim: args.reference === null });
    lines.push({ label: "Canvas", value: args.canvas ?? "—" });
    lines.push(cards);
    return lines;
  }
  if (args.bare) {
    lines.push({ label: "Splits", value: `${args.bare} ${args.bare === 1 ? "stage" : "stages"} without`, dim: true });
  }
  lines.push({ label: "Padding", value: `${args.head.toFixed(1)} / ${args.tail.toFixed(1)} s` });
  lines.push(
    args.transitionKind === "none"
      ? { label: "Transitions", value: "cut", dim: true }
      : {
          label: "Transitions",
          value: `${args.transitionLabel ?? args.transitionKind} ${args.transitionSeconds.toFixed(1)} s`,
        },
  );
  lines.push(cards);
  lines.push(
    args.overlay ? { label: "Overlay", value: args.overlayStyle ?? "on" } : { label: "Overlay", value: "off", dim: true },
  );
  if (args.cams !== null) lines.push({ label: "Cameras", value: args.cams });
  if (args.youtube !== null) {
    lines.push(args.youtube ? { label: "YouTube", value: "preset + sidecar" } : { label: "YouTube", value: "off", dim: true });
  }
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

/** The line under an option that depends on audited shots, when some of
 *  the selected stages export bare (``ExportStageRow.bare``). One home
 *  for the copy: the Overlay field, the Stage summary field and the
 *  YouTube field read it. Null when nothing is bare. */
export function bareHint(kind: "overlay" | "summary" | "captions", bare: number): string | null {
  if (bare <= 0) return null;
  const stages = `${bare} ${bare === 1 ? "stage" : "stages"}`;
  switch (kind) {
    case "overlay":
      return `Skipped on ${stages} without splits.`;
    case "summary":
      return `Time and scoring only on ${stages} without splits.`;
    case "captions":
      return `Captions cover the audited stages only; ${stages} ${bare === 1 ? "has" : "have"} none.`;
  }
}
