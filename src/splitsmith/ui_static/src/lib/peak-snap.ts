/**
 * Nearest-local-peak snapping for marker drop / add gestures (#28).
 *
 * Pure function over a server-computed peaks array (see
 * splitsmith.waveform.compute_peaks). Kept side-effect-free so it is
 * unit-testable the day the SPA gains a test runner.
 */

export interface SnapPeaks {
  peaks: number[];
  duration: number;
}

/** Default snap window, seconds. Transients the user aims at are a few
 *  ms wide; 25 ms of forgiveness covers cursor slop without jumping to
 *  the neighboring shot on a fast string (typical splits 150-400 ms). */
export const PEAK_SNAP_TOLERANCE_S = 0.025;

/** Minimum normalized amplitude for a bin to count as a peak. Below this
 *  the window is treated as silence and the gesture keeps its raw time. */
const MIN_PEAK_AMPLITUDE = 0.05;

/** Returns the bin-center time of the strongest local peak within
 *  +/- toleranceS of `time`, or null when the window has no meaningful
 *  local maximum (silence, or the max sits on the window edge with the
 *  envelope still rising outside - that is the slope of a farther peak,
 *  not one the user aimed at). */
export function snapToPeak(
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
  if (peaks[maxIdx] < MIN_PEAK_AMPLITUDE) return null;
  if (maxIdx === lo && lo > 0 && peaks[lo - 1] > peaks[lo]) return null;
  if (maxIdx === hi && hi < n - 1 && peaks[hi + 1] > peaks[hi]) return null;
  return (maxIdx + 0.5) * binW;
}
