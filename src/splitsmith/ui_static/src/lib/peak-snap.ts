/**
 * Where a dropped or added marker lands (#28, revised 2026-10-10).
 *
 * A shot's time is the rise foot (docs/METHODOLOGY.md): ``snapToLeadingEdge``
 * is ``splitsmith.rise_foot.rise_foot`` line for line, held to it by
 * ``tests/fixtures/rise_foot/cases.json``; change both or neither. Zoomed out, a
 * drop snaps to that leading edge, since a pixel is too coarse to place it
 * by hand; zoomed in to 2 ms per pixel or finer (or with Shift held), it
 * lands exactly where it was dropped, on the 1 ms grid shot times are stored
 * at. Pure functions over server-computed peaks (splitsmith.waveform).
 */

export interface SnapPeaks {
  peaks: number[];
  duration: number;
}

/** Search window around the drop, seconds: cursor slop without reaching
 *  the neighbouring shot on a fast string (splits are 150-400 ms). */
export const PEAK_SNAP_TOLERANCE_S = 0.025;

/** At this many pixels per second (2 ms per pixel) or more, a drop is placed
 *  exactly: the person can see the edge better than the snap can find it. */
export const EXACT_PX_PER_SECOND = 500;

/** Minimum normalized amplitude for a bin to count as a shot. Below this
 *  the window is treated as silence and the gesture keeps its raw time. */
const MIN_PEAK_AMPLITUDE = 0.05;
/** The detector's rise-foot fraction of the local peak. */
const RISE_FOOT_FRAC = 0.05;
/** The foot also sits above the noise: this many times its median level. */
const NOISE_FLOOR_FACTOR = 1.5;
/** A valley below this fraction of the peak, with the level rising again
 *  behind it, is an earlier sound (an echo, the previous shot): stop there. */
const VALLEY_FRAC = 0.25;
const MAX_WALK_S = 0.1;
const NOISE_WINDOW_S = 0.1;

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const s = [...values].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}

/** The leading edge of the shot nearest ``time``: the start of the first bin
 *  of its rise. ``null`` when the window holds no shot (silence, or only the
 *  slope of a farther one). */
export function snapToLeadingEdge(
  time: number,
  snapPeaks: SnapPeaks,
  toleranceS: number = PEAK_SNAP_TOLERANCE_S,
): number | null {
  const { peaks, duration } = snapPeaks;
  const n = peaks.length;
  if (n === 0 || duration <= 0 || !Number.isFinite(time)) return null;
  const binW = duration / n;
  const center = Math.min(n - 1, Math.max(0, Math.floor(time / binW)));
  const radius = Math.max(1, Math.round(toleranceS / binW));
  const lo = Math.max(0, center - radius);
  const hi = Math.min(n - 1, center + radius);
  let maxIdx = lo;
  for (let i = lo + 1; i <= hi; i++) {
    if (peaks[i] > peaks[maxIdx]) maxIdx = i;
  }
  const peak = peaks[maxIdx];
  if (peak < MIN_PEAK_AMPLITUDE) return null;
  if (maxIdx === hi && hi < n - 1 && peaks[hi + 1] > peaks[hi]) return null;

  const noiseFrom = Math.max(0, lo - Math.round(NOISE_WINDOW_S / binW));
  const floor = median(peaks.slice(noiseFrom, lo));
  const threshold = Math.max(RISE_FOOT_FRAC * peak, NOISE_FLOOR_FACTOR * floor);
  const maxWalk = Math.round(MAX_WALK_S / binW);
  let i = maxIdx;
  while (i > 0 && maxIdx - i < maxWalk) {
    const cur = peaks[i];
    const prev = peaks[i - 1];
    if (prev < threshold) break;
    if (cur < VALLEY_FRAC * peak && prev > cur) break;
    i--;
  }
  return i * binW;
}

/** Where a marker dropped or added at ``time`` lands. */
export function placeTime(
  time: number,
  opts: { pxPerSecond: number; shiftKey: boolean; peaks: SnapPeaks | null | undefined },
): number {
  const exact = Math.round(time * 1000) / 1000;
  if (opts.shiftKey || opts.pxPerSecond >= EXACT_PX_PER_SECOND || !opts.peaks) return exact;
  const edge = snapToLeadingEdge(time, opts.peaks);
  return edge == null ? exact : Math.round(edge * 1000) / 1000;
}
