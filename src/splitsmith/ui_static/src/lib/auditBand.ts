/**
 * What Audit's timeline band header carries (#1359): the marker legend
 * behind the Key button, which pipeline action its one menu offers, and
 * the muted readouts at the menu's end. The components are
 * components/audit/TransportLine; this module only decides.
 */

export interface LegendEntry {
  label: string;
  /** Tailwind classes for the swatch dot. */
  swatch: string;
}

/** The marker key, in the order the band draws it. */
export const LEGEND: readonly LegendEntry[] = [
  { label: "Beep", swatch: "bg-beep" },
  { label: "Timer stop", swatch: "border border-beep bg-transparent" },
  { label: "Shot", swatch: "bg-ink-2" },
  { label: "Manual", swatch: "bg-manual" },
  { label: "Rejected", swatch: "border border-rule-strong bg-transparent" },
  { label: "Flag", swatch: "bg-live" },
  { label: "Current", swatch: "bg-led" },
];

/** The pipeline action the menu offers: trim an untrimmed clip, detect on a trimmed one. */
export function bandPipelineAction(peaks: { trimmed: boolean } | null): "trim" | "detect" | null {
  if (!peaks) return null;
  return peaks.trimmed ? "detect" : "trim";
}

export interface BandReadoutInput {
  peakCount: number;
  duration: number;
  cameras: number;
}

/**
 * The menu's last lines: the waveform's resolution, and, with more than
 * one camera, that every camera plays linked to the primary (the camera
 * column's old transport row said so).
 */
export function bandReadouts({ peakCount, duration, cameras }: BandReadoutInput): string[] {
  const lines = [`${peakCount} peaks · ${duration.toFixed(2)} s`];
  if (cameras > 1) lines.push(`All ${cameras} cameras linked`);
  return lines;
}
