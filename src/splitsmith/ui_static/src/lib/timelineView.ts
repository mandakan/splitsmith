/**
 * Geometry of the shared timeline band (spec 2026-10-09): zoom, wheel
 * rules, ruler ticks, follow-playhead and the waveform's visible columns.
 * Pure: the component (components/timeline/Timeline.tsx) owns the DOM.
 *
 * Zoom is a multiplier over Fit; ``null`` is Fit. Content is never
 * narrower than the viewport, so 1x and below are Fit.
 */

export type Zoom = number | null;

export const MAX_ZOOM = 16;
export const ZOOM_STEP = 1.5;

/** Seconds the band runs past its last moment: the last shot is never on the edge, and a
 *  timer, sensor or detector that disagrees with the video keeps some room. */
export const BAND_TAIL_S = 1;

/** How long a stage's band runs, from the beep: the stage time or the last
 *  shot, whichever is later (a scorecard time can end before the last shot
 *  the audio holds), plus a short tail. Regions are left out on purpose: an
 *  edge dragged to the end would otherwise grow the band on every drag. */
export function bandSpan(stageTime: number, shotTimes: readonly number[]): number {
  const last = shotTimes.length > 0 ? Math.max(...shotTimes) : 0;
  const end = Math.max(stageTime, last);
  return (end > 0 ? end : 1) + BAND_TAIL_S;
}
/** exp(-deltaY * rate): a 100 px wheel notch is about 1.28x, a pinch frame a few percent. */
const ZOOM_WHEEL_RATE = 0.0025;
const LINE_PX = 16;
const PAGE_PX = 800;

/** ``max`` is the page's deepest zoom: Audit goes until 1 ms is a few pixels. */
export function clampZoom(z: number, max: number = MAX_ZOOM): Zoom {
  if (!Number.isFinite(z) || z <= 1 + 1e-9) return null;
  return Math.min(max, z);
}

export function zoomStep(zoom: Zoom, dir: 1 | -1, max: number = MAX_ZOOM): Zoom {
  return clampZoom((zoom ?? 1) * (dir === 1 ? ZOOM_STEP : 1 / ZOOM_STEP), max);
}

export function applyWheelZoom(zoom: Zoom, factor: number, max: number = MAX_ZOOM): Zoom {
  return clampZoom((zoom ?? 1) * factor, max);
}

export function contentWidth(zoom: Zoom, viewportPx: number): number {
  return zoom === null ? viewportPx : Math.round(viewportPx * zoom);
}

/** Zoom to ``next`` keeping the content point under ``anchorPx`` (viewport x) where it is. */
export function zoomAround(
  view: { zoom: Zoom; scrollLeft: number },
  next: Zoom,
  anchorPx: number,
  viewportPx: number,
): { zoom: Zoom; scrollLeft: number } {
  const before = contentWidth(view.zoom, viewportPx);
  const after = contentWidth(next, viewportPx);
  if (before <= 0 || after <= viewportPx) return { zoom: next, scrollLeft: 0 };
  const ratio = (view.scrollLeft + anchorPx) / before;
  const scrollLeft = Math.min(Math.max(ratio * after - anchorPx, 0), after - viewportPx);
  return { zoom: next, scrollLeft };
}

export interface WheelInput {
  deltaX: number;
  deltaY: number;
  /** WheelEvent.deltaMode: 0 pixels, 1 lines, 2 pages. */
  deltaMode: number;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
}

export type WheelAction = { kind: "zoom"; factor: number } | { kind: "pan"; px: number } | null;

/**
 * What a wheel over the band does. ``null`` leaves it to the page: a plain
 * vertical wheel with the toggle off must scroll the page, as everywhere
 * else. Browsers deliver a trackpad pinch as a wheel with ``ctrlKey``.
 */
export function wheelAction(e: WheelInput, wheelZooms: boolean): WheelAction {
  const k = e.deltaMode === 1 ? LINE_PX : e.deltaMode === 2 ? PAGE_PX : 1;
  const dx = e.deltaX * k;
  const dy = e.deltaY * k;
  if (e.ctrlKey || e.metaKey) return dy === 0 ? null : { kind: "zoom", factor: Math.exp(-dy * ZOOM_WHEEL_RATE) };
  if (e.shiftKey) {
    // Windows and Linux browsers turn Shift+wheel into deltaX already; macOS keeps deltaY.
    const px = dx !== 0 ? dx : dy;
    return px === 0 ? null : { kind: "pan", px };
  }
  if (dx !== 0 && Math.abs(dx) >= Math.abs(dy)) return { kind: "pan", px: dx };
  if (wheelZooms && dy !== 0) return { kind: "zoom", factor: Math.exp(-dy * ZOOM_WHEEL_RATE) };
  return null;
}

export interface RulerTick {
  /** Time in the timeline's own frame (not origin-relative). */
  t: number;
  /** Set on labelled (major) ticks: seconds from the origin. */
  label?: string;
}

const MIN_MINOR_PX = 6;
const MIN_LABEL_PX = 60;
/** Coarse to fine: minor step, major step, label decimals. */
const RUNGS: { minor: number; major: number; decimals: number }[] = [
  { minor: 10, major: 30, decimals: 0 },
  { minor: 5, major: 10, decimals: 0 },
  { minor: 1, major: 5, decimals: 0 },
  { minor: 1, major: 2, decimals: 0 },
  { minor: 0.5, major: 1, decimals: 0 },
  { minor: 0.1, major: 0.5, decimals: 1 },
  { minor: 0.1, major: 0.2, decimals: 1 },
];

/** Ticks for the visible window [t0, t1]. Frames are the finest minor step. */
export function rulerTicks(t0: number, t1: number, pxPerSec: number, origin: number, fps: number): RulerTick[] {
  if (!(pxPerSec > 0) || !(t1 > t0)) return [];
  let rung = RUNGS[0];
  for (const r of RUNGS) {
    if (r.minor * pxPerSec >= MIN_MINOR_PX && r.major * pxPerSec >= MIN_LABEL_PX) rung = r;
  }
  let { minor } = rung;
  const { major, decimals } = rung;
  const frame = fps > 0 ? 1 / fps : 0;
  if (frame > 0 && frame < minor && frame * pxPerSec >= MIN_MINOR_PX && major * pxPerSec >= MIN_LABEL_PX) minor = frame;
  const fmt = (rel: number) => {
    const text = rel.toFixed(decimals);
    return text === "-0" || text === "-0.0" ? text.slice(1) : text;
  };
  const range = (step: number) => [Math.ceil((t0 - origin) / step - 1e-9), Math.floor((t1 - origin) / step + 1e-9)];
  const out: RulerTick[] = [];
  // Majors on their own grid, so a frame step that does not divide them (24 fps) still labels whole tenths.
  const [m0, m1] = range(major);
  for (let k = m0; k <= m1; k++) out.push({ t: origin + k * major, label: fmt(k * major) });
  const [n0, n1] = range(minor);
  for (let k = n0; k <= n1; k++) {
    const q = (k * minor) / major;
    if (Math.abs(q - Math.round(q)) < 1e-6) continue;
    out.push({ t: origin + k * minor });
  }
  return out.sort((a, b) => a.t - b.t);
}

/** New scrollLeft when the playhead leaves the middle 80 %, else null. */
export function followScroll(playheadPx: number, scrollLeft: number, viewportPx: number, contentPx: number): number | null {
  if (contentPx <= viewportPx || viewportPx <= 0) return null;
  const margin = viewportPx * 0.1;
  if (playheadPx >= scrollLeft + margin && playheadPx <= scrollLeft + viewportPx - margin) return null;
  return Math.min(Math.max(playheadPx - viewportPx / 2, 0), contentPx - viewportPx);
}

/**
 * New scrollLeft when the playhead is wholly outside the visible window
 * [scrollLeft, scrollLeft + viewportPx], else null. Used while paused: a
 * seek from off the band (e.g. the shot table) should bring the playhead
 * into view, but a click or drag release inside the band must never move
 * the view out from under the pointer that just placed it there.
 */
export function revealScroll(playheadPx: number, scrollLeft: number, viewportPx: number, contentPx: number): number | null {
  if (contentPx <= viewportPx || viewportPx <= 0) return null;
  if (playheadPx >= scrollLeft && playheadPx <= scrollLeft + viewportPx) return null;
  return Math.min(Math.max(playheadPx - viewportPx / 2, 0), contentPx - viewportPx);
}

/**
 * Loudest peak under each of ``columns`` viewport columns. The track shows
 * clip seconds [from, to] across ``contentPx`` and is scrolled by
 * ``scrollLeft``; peaks are ``peaks.length`` bins over ``clipDuration``.
 * ``columns`` is the viewport width in px: column c covers content pixels
 * [scrollLeft + c, scrollLeft + c + 1), i.e. one content pixel each, so the
 * visible window is [scrollLeft, scrollLeft + columns), never the content's
 * far end.
 */
export function columnPeaks(
  peaks: number[],
  clipDuration: number,
  from: number,
  to: number,
  contentPx: number,
  scrollLeft: number,
  columns: number,
): number[] {
  const out = new Array<number>(Math.max(columns, 0)).fill(0);
  const n = peaks.length;
  if (n === 0 || clipDuration <= 0 || contentPx <= 0 || to <= from || columns <= 0) return out;
  const secPerPx = (to - from) / contentPx;
  const binsPerSec = n / clipDuration;
  for (let c = 0; c < columns; c++) {
    const a = from + (scrollLeft + c) * secPerPx;
    const b = a + secPerPx;
    let lo = Math.floor(a * binsPerSec + 1e-9);
    let hi = Math.ceil(b * binsPerSec - 1e-9) - 1;
    if (hi < 0 || lo >= n) continue;
    lo = Math.max(lo, 0);
    hi = Math.min(Math.max(hi, lo), n - 1);
    let m = 0;
    for (let i = lo; i <= hi; i++) if (peaks[i] > m) m = peaks[i];
    out[c] = m;
  }
  return out;
}

/** A stretch of the clip drawn finer than the clip's own peaks: ``start``
 *  and ``duration`` in clip seconds, ``peaks`` evenly over it. */
export interface PeaksDetail {
  start: number;
  duration: number;
  peaks: number[];
}

/**
 * ``base`` (``columnPeaks`` over the whole clip) with every column that lies
 * wholly inside ``detail`` read from the fine peaks instead. The beep picker
 * loads a few seconds at 1 ms over a source whose own peaks are 25-50 ms
 * bars, and zooming into the beep must show where the tone starts.
 */
export function mergeDetailColumns(
  base: number[],
  detail: PeaksDetail | null | undefined,
  from: number,
  to: number,
  contentPx: number,
  scrollLeft: number,
  columns: number,
): number[] {
  if (!detail || detail.duration <= 0 || detail.peaks.length === 0) return base;
  const fine = columnPeaks(
    detail.peaks,
    detail.duration,
    from - detail.start,
    to - detail.start,
    contentPx,
    scrollLeft,
    columns,
  );
  const secPerPx = contentPx > 0 ? (to - from) / contentPx : 0;
  const end = detail.start + detail.duration;
  return base.map((v, c) => {
    const a = from + (scrollLeft + c) * secPerPx;
    const b = a + secPerPx;
    return a >= detail.start - 1e-9 && b <= end + 1e-9 ? fine[c] : v;
  });
}

