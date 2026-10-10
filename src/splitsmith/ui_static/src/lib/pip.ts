/**
 * Picture-in-picture cameras (epic #1405, issue #1406): every rule the
 * PipView primitive follows, pure. Which camera is big and which sits in
 * the inset, swap and cycle, the inset's size and corner, how the inset's
 * clock maps onto the big one's, and when drift is worth a seek.
 *
 * A camera is identified by an opaque id the page chooses (a video path
 * or video id). Camera order is the page's order, primary first as every
 * payload sends it; the cycle and the "n / N" counter follow it.
 */
import { scrubSource } from "@/lib/scrubSource";

export interface PipCamera {
  id: string;
  /** What the chips call it: "Cam 2", "Head cam". */
  label: string;
  /** The audio and beep source. Exactly one camera should carry it. */
  primary: boolean;
  /** The beep's position in the clip this camera streams. ``null`` means
   *  the camera cannot be lined up with the others: it never enters the
   *  inset. */
  beepInClip: number | null;
  /** Stream URL the inset plays when this camera is in it (build it with
   *  ``insetStream``). The big player's source stays the page's. */
  src: string | null;
  /** Optional chip at the inset's bottom left, e.g. "Synced +0.000 s". */
  note?: string | null;
  /** Optional still the inset shows until its first frame decodes. */
  poster?: string | null;
}

export interface PipState {
  big: string;
  /** ``null`` when no other camera can be lined up with the big one. */
  inset: string | null;
}

export type PipCorner = "tl" | "tr" | "bl" | "br";

export const PIP_CORNERS: readonly PipCorner[] = ["tl", "tr", "bl", "br"];
export const DEFAULT_PIP_CORNER: PipCorner = "tr";
/** Inset width as a share of the big camera's rendered frame. */
export const PIP_WIDTH_SHARE = 0.28;
/** Gap between the inset and the frame's edges, px. */
export const PIP_MARGIN = 10;
/** Below this inset width the chips are hidden (they crowd the frame). */
export const PIP_CHIPS_MIN_WIDTH = 180;
/** While playing, a gap under this is left alone: a seek costs a decoder
 *  flush and a visible hitch, and ``timeupdate`` fires ~4x a second. */
export const PIP_DRIFT_PLAYING_S = 0.2;
/** While paused the inset should show the same instant: under one frame
 *  at 60 fps is left alone. */
export const PIP_DRIFT_PAUSED_S = 0.015;

const syncable = (c: PipCamera) => c.beepInClip != null && Number.isFinite(c.beepInClip);

function primaryOf(cameras: readonly PipCamera[]): PipCamera | undefined {
  return cameras.find((c) => c.primary) ?? cameras[0];
}

/** The inset candidates for a given big camera, in camera order. */
function others(cameras: readonly PipCamera[], big: string): PipCamera[] {
  return cameras.filter((c) => c.id !== big && syncable(c));
}

function bigIsSyncable(cameras: readonly PipCamera[], big: string): boolean {
  const cam = cameras.find((c) => c.id === big);
  return cam != null && syncable(cam);
}

/** A new stage: the primary is big, the next syncable camera in order is
 *  the inset. */
export function initialPip(cameras: readonly PipCamera[]): PipState {
  const primary = primaryOf(cameras);
  if (!primary) return { big: "", inset: null };
  const inset = bigIsSyncable(cameras, primary.id) ? (others(cameras, primary.id)[0]?.id ?? null) : null;
  return { big: primary.id, inset };
}

/** Keep a state valid against a camera list that changed under it (a
 *  camera gained its beep, or went away). A big camera that is gone falls
 *  back to the start state. */
export function normalizePip(state: PipState, cameras: readonly PipCamera[]): PipState {
  if (!cameras.some((c) => c.id === state.big)) return initialPip(cameras);
  if (!bigIsSyncable(cameras, state.big)) return state.inset === null ? state : { big: state.big, inset: null };
  const cands = others(cameras, state.big);
  if (state.inset !== null && cands.some((c) => c.id === state.inset)) return state;
  const inset = cands[0]?.id ?? null;
  return inset === state.inset ? state : { big: state.big, inset };
}

/** The inset becomes big and the big camera takes the inset. */
export function swapPip(state: PipState): PipState {
  if (state.inset === null) return state;
  return { big: state.inset, inset: state.big };
}

/** C / Shift+C: the next (or previous) camera that is not big goes into
 *  the inset, in camera order, wrapping. With one candidate (two cameras)
 *  that would change nothing, so it swaps instead. */
export function cyclePip(state: PipState, cameras: readonly PipCamera[], dir: 1 | -1): PipState {
  if (state.inset === null) return state;
  const cands = others(cameras, state.big);
  if (cands.length <= 1) return swapPip(state);
  const at = cands.findIndex((c) => c.id === state.inset);
  const next = (at + dir + cands.length) % cands.length;
  return { big: state.big, inset: cands[at === -1 ? 0 : next].id };
}

/** "2 / 3": the inset's place among the syncable cameras, shown only with
 *  three or more (with two the swap button says it all). */
export function pipCounter(
  state: PipState,
  cameras: readonly PipCamera[],
): { n: number; total: number } | null {
  if (state.inset === null) return null;
  const usable = cameras.filter(syncable);
  if (usable.length < 3) return null;
  const n = usable.findIndex((c) => c.id === state.inset) + 1;
  return n > 0 ? { n, total: usable.length } : null;
}

/** The page's key handler asks this: C cycles forward, Shift+C back. */
export function pipKeyAction(e: {
  key: string;
  shiftKey?: boolean;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
}): 1 | -1 | null {
  if (e.metaKey || e.ctrlKey || e.altKey) return null;
  if (e.key !== "c" && e.key !== "C") return null;
  return e.shiftKey ? -1 : 1;
}

/* -------------------------------------------------------------------------
 * Geometry
 * ----------------------------------------------------------------------- */

export interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** The rendered picture of an ``object-contain`` video inside its element
 *  box (letterboxed or pillarboxed). Without intrinsic dimensions the box
 *  itself. */
export function containedFrame(box: Box, videoWidth: number, videoHeight: number): Box {
  if (!(videoWidth > 0 && videoHeight > 0) || !(box.width > 0 && box.height > 0)) return box;
  const ar = videoWidth / videoHeight;
  let width = box.width;
  let height = width / ar;
  if (height > box.height) {
    height = box.height;
    width = height * ar;
  }
  return {
    left: box.left + (box.width - width) / 2,
    top: box.top + (box.height - height) / 2,
    width,
    height,
  };
}

export interface InsetSize {
  width: number;
  height: number;
  /** False under ``PIP_CHIPS_MIN_WIDTH``: only the picture and buttons. */
  chips: boolean;
}

/** 28 % of the frame's width, 16:9. */
export function insetSize(frameWidth: number): InsetSize {
  const width = Math.max(0, Math.round(frameWidth * PIP_WIDTH_SHARE));
  return { width, height: Math.round((width * 9) / 16), chips: width >= PIP_CHIPS_MIN_WIDTH };
}

/** Where the inset sits in a frame. ``topInset`` drops the top corners
 *  below a pill the page keeps there (the beep / sync pill);
 *  ``bottomInset`` lifts the bottom ones over a readout. */
export function insetBox(
  frame: Box,
  corner: PipCorner,
  opts: { topInset?: number; bottomInset?: number } = {},
): Box {
  const { width, height } = insetSize(frame.width);
  const right = corner === "tr" || corner === "br";
  const bottom = corner === "bl" || corner === "br";
  return {
    left: right ? frame.left + frame.width - width - PIP_MARGIN : frame.left + PIP_MARGIN,
    top: bottom
      ? frame.top + frame.height - height - PIP_MARGIN - (opts.bottomInset ?? 0)
      : frame.top + PIP_MARGIN + (opts.topInset ?? 0),
    width,
    height,
  };
}

/** A drag ends: the corner whose quadrant holds the inset's centre. */
export function snapCorner(frame: Box, centerX: number, centerY: number): PipCorner {
  const right = centerX >= frame.left + frame.width / 2;
  const bottom = centerY >= frame.top + frame.height / 2;
  return bottom ? (right ? "br" : "bl") : right ? "tr" : "tl";
}

export function isPipCorner(v: unknown): v is PipCorner {
  return typeof v === "string" && (PIP_CORNERS as readonly string[]).includes(v);
}

/* -------------------------------------------------------------------------
 * Clocks
 * ----------------------------------------------------------------------- */

/** The inset's position for a big-video position: both clips are lined
 *  up on their own beep. Clamped to the inset clip's start (and end, when
 *  its duration is known). */
export function insetTime(args: {
  bigTime: number;
  bigBeep: number;
  insetBeep: number;
  insetDuration?: number | null;
}): number {
  const t = args.bigTime - args.bigBeep + args.insetBeep;
  const d = args.insetDuration;
  const capped = d != null && Number.isFinite(d) && d > 0 ? Math.min(t, d) : t;
  return Math.max(0, capped);
}

/** Is the inset far enough off to seek it? Checked on ``timeupdate`` and
 *  after a seek, never per frame. */
export function shouldCorrectDrift(args: { target: number; current: number; paused: boolean }): boolean {
  const gap = Math.abs(args.target - args.current);
  return gap > (args.paused ? PIP_DRIFT_PAUSED_S : PIP_DRIFT_PLAYING_S);
}

/* -------------------------------------------------------------------------
 * Media
 * ----------------------------------------------------------------------- */

export type InsetStreamKind = "scrub" | "trim" | "web" | "source";

/** What the inset streams for a video dict: the 720p rendition whenever
 *  there is one, two decodes at once being the point (a 4K trim stalls on
 *  its own, #1192). It ignores the full-resolution preference, which is
 *  about the big picture; a rendition that already failed on the page
 *  falls back to the trim, as ``useScrubSource`` does.
 *
 *  ``kind`` is the coach payload's pin (``CoachVideoEntry.kind``): a
 *  ``source`` camera has no trim (its beep anchor is in the source) and a
 *  ``web`` one is already the rendition. Audit's ``StageVideo`` has no
 *  ``kind`` and goes through the scrub pin. */
export function insetStream(
  video: {
    kind?: "trim" | "source" | "web";
    trim_version?: string | null;
    scrub_version?: string | null;
  },
  failed = false,
): { kind: InsetStreamKind; version: string | null } {
  if (video.kind === "source") return { kind: "source", version: null };
  if (video.kind === "web") return failed ? { kind: "trim", version: video.trim_version ?? null } : { kind: "web", version: null };
  return scrubSource({
    trimVersion: video.trim_version,
    scrubVersion: video.scrub_version,
    fullRes: false,
    failed,
  });
}
