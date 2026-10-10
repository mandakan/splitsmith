/**
 * Breakdown's counts (#1371). Breakdown is optional by design (epic #1370):
 * these are quiet tallies, never a missing step, and nothing in Overview,
 * the next step, readiness or the export gate reads them.
 */
import type { StageEvent, StageFigures } from "@/lib/api";

/** A stage's regions by source: ``confirmed`` (manual, what every output
 *  reads) and ``proposed`` (auto, shown on the lanes only). */
export function regionCounts(events: readonly StageEvent[]): { confirmed: number; proposed: number } {
  let confirmed = 0;
  let proposed = 0;
  for (const e of events) {
    if (e.source === "manual") confirmed += 1;
    else proposed += 1;
  }
  return { confirmed, proposed };
}

/** The list's scrollTop that brings a row fully into view with the least
 *  movement (``block: "nearest"``), for a list that must scroll alone:
 *  unchanged when the row is already visible. Offsets are the row's own
 *  within the list's content. */
export function nearestScrollTop(scrollTop: number, viewHeight: number, rowTop: number, rowHeight: number): number {
  if (rowTop < scrollTop) return rowTop;
  if (rowTop + rowHeight > scrollTop + viewHeight) return Math.max(0, rowTop + rowHeight - viewHeight);
  return scrollTop;
}

/** The viewer row's floor under the splitter (#1373). */
export const VIEWER_MIN_PX = 200;
/** The splitter's own height (its hairlines included). */
export const SPLITTER_PX = 6;
/** One arrow press on the splitter; with Shift, the larger step. */
export const SPLIT_STEP_PX = 16;
export const SPLIT_STEP_LARGE_PX = 64;

/** The band's height range in px. */
export interface BandLimits {
  min: number;
  max: number;
}

/** ``room`` is the height the viewer row and the band share; ``floor`` the
 *  band's least (ruler, Audio, Shots and one lane); ``viewerChrome`` what
 *  the viewer row holds besides the video (the transport on a tall window).
 *  The video never goes under ``VIEWER_MIN_PX``, unless the room cannot
 *  hold both: then the band's floor wins. */
export function bandLimits(room: number, floor: number, viewerChrome = 0): BandLimits {
  const min = Math.round(floor);
  return { min, max: Math.max(min, Math.round(room - viewerChrome - VIEWER_MIN_PX)) };
}

export function clampBand(height: number, limits: BandLimits): number {
  return Math.round(Math.min(Math.max(height, limits.min), limits.max));
}

/** The band's height after a key on the separator, or ``null`` for a key it
 *  does not take. Up raises the separator (a taller band); Home puts it at
 *  the top (the viewer at its floor), End at the bottom (the band at its). */
export function bandForKey(key: string, shift: boolean, band: number, limits: BandLimits): number | null {
  const step = shift ? SPLIT_STEP_LARGE_PX : SPLIT_STEP_PX;
  switch (key) {
    case "ArrowUp":
      return clampBand(band + step, limits);
    case "ArrowDown":
      return clampBand(band - step, limits);
    case "Home":
      return limits.max;
    case "End":
      return limits.min;
    default:
      return null;
  }
}

/** Double-click on the separator: the band-large preset (the viewer at its
 *  floor), or, from the preset, back to the split it replaced. ``stored``
 *  is the remembered split (``null``: the page's own layout), ``current``
 *  the band's height on screen, ``previous`` what the preset replaced. */
export function toggleBandLarge(
  stored: number | null,
  current: number,
  limits: BandLimits,
  previous: number | null,
): { next: number | null; previous: number | null } {
  if (current >= limits.max - 1) return { next: previous, previous: null };
  return { next: limits.max, previous: stored };
}

/** The Audio row's height: band height beyond the band's natural height
 *  (every row at its own height) goes there first, a taller waveform. */
export function audioRowHeight(band: number | null, natural: number, base: number): number {
  if (band == null || natural <= 0) return base;
  return base + Math.max(0, Math.round(band - natural));
}

/** The Breakdown nav row's count: confirmed regions over every stage's
 *  ``figures.regions``, or ``undefined`` when there are none, so the row
 *  shows nothing rather than a zero. */
export function navRegionCount(
  stages: readonly { figures?: Pick<StageFigures, "regions"> | null }[] | null | undefined,
): number | undefined {
  let total = 0;
  for (const s of stages ?? []) total += s.figures?.regions ?? 0;
  return total > 0 ? total : undefined;
}
