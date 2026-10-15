/**
 * Geometry for the touch beep picker (BeepReticle): a fixed centre line
 * with the waveform panning under it. The time under the line is the
 * pick, so everything here is expressed relative to that centre time and
 * a zoom in pixels per second. Pure; the component only draws.
 */

import { snapToLeadingEdge } from "@/lib/peak-snap";

/** Initial visible span: wide enough to see the neighbourhood of a wrong
 *  pick, narrow enough that the beep's 0.3-0.5 s tone reads as a block. */
export const INITIAL_SPAN_S = 8;
/** Tightest zoom: a quarter second across the phone. */
export const MIN_SPAN_S = 0.25;

export interface ReticleView {
  /** Source time under the centre line. */
  center: number;
  /** Zoom, CSS px per second. */
  pps: number;
  /** Canvas width, CSS px. */
  width: number;
}

export interface Range {
  start: number;
  end: number;
}

export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

/** Zoom limits: fit the whole range at one end, MIN_SPAN_S at the other. */
export function ppsBounds(width: number, range: Range): { min: number; max: number } {
  const duration = Math.max(range.end - range.start, MIN_SPAN_S);
  const min = width / duration;
  return { min, max: Math.max(min, width / MIN_SPAN_S) };
}

export function initialPps(width: number, range: Range): number {
  const { min, max } = ppsBounds(width, range);
  return clamp(width / INITIAL_SPAN_S, min, max);
}

export function timeAtX(view: ReticleView, x: number): number {
  return view.center + (x - view.width / 2) / view.pps;
}

export function xAtTime(view: ReticleView, t: number): number {
  return view.width / 2 + (t - view.center) * view.pps;
}

export function visibleRange(view: ReticleView): Range {
  return { start: timeAtX(view, 0), end: timeAtX(view, view.width) };
}

const TICK_STEPS = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60];
/** Minimum spacing between labelled ticks, CSS px (a "12.35" label is ~34 px). */
const MIN_LABEL_SPACING_PX = 56;

/** The labelled tick step for a zoom: the finest step that keeps labels apart. */
export function tickStep(pps: number): number {
  return TICK_STEPS.find((s) => s * pps >= MIN_LABEL_SPACING_PX) ?? TICK_STEPS[TICK_STEPS.length - 1];
}

export interface Tick {
  x: number;
  time: number;
  label: string | null;
}

/** Ruler ticks across the view: a labelled tick every ``tickStep`` and
 *  four minor ticks between. Only ticks inside ``range`` are returned, so
 *  the ruler stops where the audio does. */
export function ticks(view: ReticleView, range: Range): Tick[] {
  const step = tickStep(view.pps);
  const minor = step / 5;
  const vis = visibleRange(view);
  const lo = Math.max(vis.start, range.start);
  const hi = Math.min(vis.end, range.end);
  const out: Tick[] = [];
  const first = Math.ceil(lo / minor - 1e-9);
  const last = Math.floor(hi / minor + 1e-9);
  for (let i = first; i <= last; i++) {
    const time = i * minor;
    const major = i % 5 === 0;
    out.push({ x: xAtTime(view, time), time, label: major ? formatTime(time, step) : null });
  }
  return out;
}

/** Seconds with as many decimals as the step needs; minutes past 60 s. */
export function formatTime(t: number, step: number): string {
  const decimals = step >= 1 ? 0 : step >= 0.1 ? 1 : 2;
  if (t < 60) return t.toFixed(decimals);
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  const sec = s.toFixed(decimals);
  return `${m}:${s < 10 ? "0" : ""}${sec}`;
}

/** One value per canvas column: the max peak over the bins that column
 *  covers, or null outside the audio. Zoomed past the bin size, adjacent
 *  columns share a bin and the trace reads as blocks, which is honest. */
export function columnPeaks(peaks: number[], range: Range, view: ReticleView): (number | null)[] {
  const n = peaks.length;
  const duration = range.end - range.start;
  const out: (number | null)[] = new Array(Math.max(0, Math.floor(view.width)));
  if (n === 0 || duration <= 0) return out.fill(null);
  const binS = duration / n;
  for (let c = 0; c < out.length; c++) {
    const t0 = timeAtX(view, c);
    const t1 = timeAtX(view, c + 1);
    if (t1 <= range.start || t0 >= range.end) {
      out[c] = null;
      continue;
    }
    const i0 = clamp(Math.floor((t0 - range.start) / binS), 0, n - 1);
    const i1 = clamp(Math.ceil((t1 - range.start) / binS) - 1, i0, n - 1);
    let v = 0;
    for (let i = i0; i <= i1; i++) if (peaks[i] > v) v = peaks[i];
    out[c] = v;
  }
  return out;
}

/** How much the main view may lift the loudest bar it shows: a quiet beep
 *  fills the height, a window of hum does not become a wall of noise. */
export const MAX_GAIN = 50;

/** The main view's gain: its loudest visible bar drawn at full height
 *  (2026-10-15). The snippet's peaks are scaled to the clip's loudest
 *  sound, a shot, so a beep at 2 % of it drew as a flat line. */
export function visibleGain(cols: ReadonlyArray<number | null>): number {
  let max = 0;
  for (const v of cols) if (v != null && v > max) max = v;
  return max <= 0 ? 1 : Math.min(MAX_GAIN, Math.max(1, 1 / max));
}

/** Seconds of quiet the beep-start rule looks back over for its floor. */
const LEAD_S = 0.3;
/** The window must rise at least this far above its quiet start to hold a
 *  beep worth snapping to. */
const RISE_RATIO = 4;

function median(xs: number[]): number {
  if (xs.length === 0) return 0;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}

/** The beep's start near ``t``: the rise-foot rule (lib/peak-snap, as the
 *  desktop's "Snap to the beep's start") on the bars around ``t`` scaled
 *  to their own loudest, so a beep far quieter than the shots still has a
 *  rise. Null when nothing within ``reachS`` rises out of the quiet before
 *  it. ``peaks`` cover ``range`` evenly. */
export function beepStartNear(
  peaks: ReadonlyArray<number>,
  range: Range,
  t: number,
  reachS = 0.025,
): number | null {
  const n = peaks.length;
  const duration = range.end - range.start;
  if (n === 0 || duration <= 0) return null;
  const binS = duration / n;
  const from = clamp(Math.floor((t - LEAD_S - range.start) / binS), 0, n - 1);
  const to = clamp(Math.ceil((t + reachS + 0.05 - range.start) / binS), from + 1, n);
  const slice = peaks.slice(from, to);
  let max = 0;
  for (const v of slice) if (v > max) max = v;
  const lead = median(slice.slice(0, Math.max(1, Math.round(0.1 / binS))));
  if (max <= 0 || max < RISE_RATIO * lead) return null;
  const start = range.start + from * binS;
  const foot = snapToLeadingEdge(
    t - start,
    { peaks: slice.map((v) => v / max), duration: slice.length * binS },
    reachS,
  );
  return foot == null ? null : Math.round((foot + start) * 1000) / 1000;
}

