import { type LabEvalFixture } from "@/lib/api";

// Centralized lab outcome palette, on design tokens from
// styles/index.css so the same TP/FP/FN colours are used everywhere.
// Constraint: the three outcome tokens must resolve to *distinct*
// values -- #525's token retune set --color-split-slow to the same
// #FF2D2D as --color-destructive, which silently collapsed FP and FN
// into one red (and made FP invisible on the red-tinted waveform
// bars). FP therefore uses the amber token, matching outcomeColor's
// badge convention below (FP orange-family, FN red);
// labPalette.test.ts pins the distinctness.
export const LAB_PALETTE = {
  tp: "var(--color-split-good)", // green
  fp: "var(--color-split-ok)", // amber
  fn: "var(--color-destructive)", // "missing thing" red
  rejected: "var(--color-marker-rejected)", // neutral gray
  candidatePrimary: "var(--color-marker-detected)", // Okabe-Ito blue
  playhead: "var(--color-waveform-playhead)", // Okabe-Ito vermillion
  playWindow: "var(--color-primary)", // shadcn primary (theme-tracking)
} as const;

export function candidateLineColor(c: LabEvalFixture["candidates"][number]): string {
  if (c.kept && c.truth === 1) return LAB_PALETTE.tp;
  if (c.kept && c.truth === 0) return LAB_PALETTE.fp;
  if (!c.kept && c.truth === 1) return LAB_PALETTE.fn;
  return LAB_PALETTE.candidatePrimary;
}

export function otherCandidateColor(c: LabEvalFixture["candidates"][number]): string {
  if (c.kept && c.truth === 1) return LAB_PALETTE.tp;
  if (c.kept && c.truth === 0) return LAB_PALETTE.fp;
  return LAB_PALETTE.rejected;
}

export function outcomeLabel(c: LabEvalFixture["candidates"][number]): string {
  if (c.kept && c.truth === 1) return "TP";
  if (c.kept && c.truth === 0) return "FP";
  if (!c.kept && c.truth === 1) return "FN";
  return "TN";
}
export function outcomeColor(c: LabEvalFixture["candidates"][number]): string {
  if (c.kept && c.truth === 1) return "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300";
  if (c.kept && c.truth === 0) return "bg-orange-500/20 text-orange-700 dark:text-orange-300";
  if (!c.kept && c.truth === 1) return "bg-red-500/20 text-red-700 dark:text-red-300";
  return "bg-muted text-muted";
}

export function fmtPct(x: number): string {
  return `${(x * 100).toFixed(1)}%`;
}
