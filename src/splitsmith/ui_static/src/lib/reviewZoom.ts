/**
 * Zoom and waveform resolution for the lab's fixture review.
 *
 * A fixture shot's time is its onset, placed by a person, so the review
 * zooms far past the audit's 16x and draws the waveform from peaks fetched
 * at about one bin per pixel (never finer than 1 ms, the resolution a shot
 * time is stored at).
 */

import { MAX_ZOOM } from "@/components/AuditControls";

/** Finest bin the review asks for: shot times are stored to the ms. */
const FINEST_BIN_S = 0.001;
/** How wide 1 ms is at the deepest zoom, in CSS pixels. */
const PX_PER_MS_AT_MAX = 4;

/** Peak bins to draw ``duration`` seconds at ``pixelsPerSecond`` (``null`` =
 *  fit, which keeps ``baseBins``). A power of two so neighbouring zoom steps
 *  share the server's per-bin-count cache. */
export function displayBins(
  duration: number,
  pixelsPerSecond: number | null,
  baseBins: number,
): number {
  if (pixelsPerSecond == null || duration <= 0) return baseBins;
  const finest = Math.ceil(duration / FINEST_BIN_S);
  const wanted = 2 ** Math.ceil(Math.log2(Math.max(1, duration * pixelsPerSecond)));
  return Math.max(baseBins, Math.min(finest, wanted));
}

/** The deepest zoom the review offers: until 1 ms is a few pixels wide. */
export function reviewMaxZoom(duration: number, viewportWidth: number): number {
  if (duration <= 0 || viewportWidth <= 0) return MAX_ZOOM;
  const fitPps = viewportWidth / duration;
  return Math.max(MAX_ZOOM, (PX_PER_MS_AT_MAX * 1000) / fitPps);
}
