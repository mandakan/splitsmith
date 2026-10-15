/**
 * The start beep's tone: how long it plays, and where it stops (2026-10-15).
 *
 * A secondary camera that started recording during the beep has only the
 * tone's end in its clip. The timer's tone has one length, the same in
 * every camera on the stage, so its start is that end less the length
 * measured on the stage's head cams, which heard the whole beep. One rule
 * finds the end on both sides, so its own small bias cancels.
 *
 * The rule: the tone has stopped at the first bar where the next 20 ms sit
 * under 30 % of the 40 ms before it (ending 10 ms back); the end is the
 * first bar under that line from there. Measured on Höstfinalen XI's head
 * cams, a stage's three agree within about 10 ms. Peaks are 1 ms bars over
 * ``range``.
 */
import type { Range } from "@/lib/reticle";

const AFTER_S = 0.02;
const BEFORE_S = 0.04;
const BEFORE_GAP_S = 0.01;
const DROP = 0.3;
/** The tone is at least this long; the end is looked for after it. */
const MIN_TONE_S = 0.06;
const MAX_TONE_S = 1.5;
/** A line this near the end finds it. */
const END_REACH_S = 0.05;
/** An end is a tone's only where the level before it is a real part of
 *  the loudest sound around it, not the tail of a reverb. */
const TONE_SHARE = 0.2;

function median(xs: ReadonlyArray<number>): number {
  if (xs.length === 0) return 0;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

const ms = (t: number) => Math.round(t * 1000) / 1000;

/** The first bar in ``[from, to)`` where the tone stops, or null. */
function endFrom(peaks: ReadonlyArray<number>, binS: number, from: number, to: number, minLevel: number): number | null {
  const after = Math.max(1, Math.round(AFTER_S / binS));
  const before = Math.max(1, Math.round(BEFORE_S / binS));
  const gap = Math.round(BEFORE_GAP_S / binS);
  const lo = Math.max(before + gap, from);
  const hi = Math.min(peaks.length - after, to);
  for (let i = lo; i < hi; i++) {
    const level = median(peaks.slice(i - gap - before, i - gap));
    if (level <= 0 || level < minLevel) continue;
    const line = DROP * level;
    if (median(peaks.slice(i, i + after)) >= line) continue;
    // The drop is in the next 20 ms; the tone ends at its first bar under the line.
    for (let j = i; j < i + after; j++) if (peaks[j] < line) return j;
    return i;
  }
  return null;
}

/** How long the tone that starts at ``beepStart`` plays, in seconds; null
 *  when no tone stops within 1.5 s of it. */
export function toneLength(peaks: ReadonlyArray<number>, range: Range, beepStart: number): number | null {
  const n = peaks.length;
  const duration = range.end - range.start;
  if (n === 0 || duration <= 0) return null;
  const binS = duration / n;
  const s = Math.round((beepStart - range.start) / binS);
  const level = median(peaks.slice(s + Math.round(0.01 / binS), s + Math.round(MIN_TONE_S / binS)));
  const end = endFrom(peaks, binS, s + Math.round(MIN_TONE_S / binS), s + Math.round(MAX_TONE_S / binS), TONE_SHARE * level);
  return end == null ? null : ms((end - s) * binS);
}

/** Where the tone stops near ``t`` (a line placed on its end), or null
 *  where nothing loud stops within 50 ms of it. */
export function toneEndNear(
  peaks: ReadonlyArray<number>,
  range: Range,
  t: number,
  reachS = END_REACH_S,
): number | null {
  const n = peaks.length;
  const duration = range.end - range.start;
  if (n === 0 || duration <= 0) return null;
  const binS = duration / n;
  const at = (x: number) => Math.round((x - range.start) / binS);
  let local = 0;
  for (let i = Math.max(0, at(t - 0.3)); i < Math.min(n, at(t + 0.3)); i++) if (peaks[i] > local) local = peaks[i];
  const end = endFrom(peaks, binS, at(t - reachS), at(t + reachS) + 1, TONE_SHARE * local);
  return end == null ? null : ms(range.start + end * binS);
}

/** A stage's tone from its head cams' lengths: the median (a stray one,
 *  a shot in a tail, is outvoted) and how far apart they were. */
export function stageTone(
  lengths: ReadonlyArray<number | null>,
): { tone: number; count: number; spread: number } | null {
  const ok = lengths.filter((x): x is number => x != null);
  if (ok.length === 0) return null;
  return { tone: ms(median(ok)), count: ok.length, spread: ms(Math.max(...ok) - Math.min(...ok)) };
}
