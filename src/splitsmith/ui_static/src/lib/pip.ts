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
import type { ReactNode } from "react";

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
  /** Nothing is left to try for this camera (``insetStream`` answered
   *  ``null``): the inset says so instead of showing a black box. */
  unavailable?: boolean;
  /** Optional content at the inset's bottom left: a string is drawn as a
   *  chip ("Synced +0.000 s"); a node (Audit's clickable sync pill) as is,
   *  and a click on it never swaps. */
  note?: ReactNode;
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
/** The inset is never narrower than this (owner, epic #1405)... */
export const PIP_MIN_WIDTH = 180;
/** ...nor wider than this share of the frame, so it never buries it. */
export const PIP_MAX_SHARE = 0.45;
/** Below this inset width the chips are hidden: at 180 px a swapped
 *  primary still reads ("CAM 1..." + PRIMARY + two buttons, checked at
 *  1440 x 900), narrower crowds them. Only a frame under 400 px, where
 *  the 45 % cap wins over the floor, gets there. */
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
 *  the inset. ``start`` names a syncable camera to open big instead (a
 *  link that names a camera, a choice carried across stages); one that is
 *  missing or cannot be lined up is ignored. */
export function initialPip(cameras: readonly PipCamera[], start?: string | null): PipState {
  const chosen = start ? cameras.find((c) => c.id === start && syncable(c)) : undefined;
  const big = chosen ?? primaryOf(cameras);
  if (!big) return { big: "", inset: null };
  const inset = bigIsSyncable(cameras, big.id) ? (others(cameras, big.id)[0]?.id ?? null) : null;
  return { big: big.id, inset };
}

/** Keep a state valid against a camera list that changed under it (a
 *  camera gained its beep, or went away). A big camera that is gone falls
 *  back to the start state (``start`` as in ``initialPip``). */
export function normalizePip(state: PipState, cameras: readonly PipCamera[], start?: string | null): PipState {
  if (!cameras.some((c) => c.id === state.big)) return initialPip(cameras, start);
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

function isEditable(t: unknown): boolean {
  if (typeof HTMLElement === "undefined" || !(t instanceof HTMLElement)) return false;
  return t.isContentEditable || t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT";
}

/** The page's key handler asks this: C cycles forward, Shift+C back.
 *  Never while typing, never on auto-repeat, never with a modifier. */
export function pipKeyAction(e: {
  key: string;
  shiftKey?: boolean;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  repeat?: boolean;
  target?: EventTarget | null;
}): 1 | -1 | null {
  if (e.metaKey || e.ctrlKey || e.altKey || e.repeat) return null;
  if (isEditable(e.target)) return null;
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

/** 28 % of the frame's width, 16:9, never narrower than
 *  ``PIP_MIN_WIDTH`` (so on a small frame it is a larger share), and never
 *  wider than ``PIP_MAX_SHARE`` of the frame, which wins on a frame too
 *  small for the floor (under 400 px). */
export function insetSize(frameWidth: number): InsetSize {
  const fw = Math.max(0, frameWidth);
  const width = Math.round(Math.min(Math.max(fw * PIP_WIDTH_SHARE, PIP_MIN_WIDTH), fw * PIP_MAX_SHARE));
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

/** The keyboard's way to the corners: an arrow key while focus is in the
 *  inset moves it to that side, keeping the other axis. ``null`` for any
 *  other key, or when it is already on that side. */
export function cornerByArrow(corner: PipCorner, key: string): PipCorner | null {
  const v = corner[0] as "t" | "b";
  const h = corner[1] as "l" | "r";
  const next =
    key === "ArrowUp"
      ? `t${h}`
      : key === "ArrowDown"
        ? `b${h}`
        : key === "ArrowLeft"
          ? `${v}l`
          : key === "ArrowRight"
            ? `${v}r`
            : null;
  return next && next !== corner && isPipCorner(next) ? next : null;
}

export function isPipCorner(v: unknown): v is PipCorner {
  return typeof v === "string" && (PIP_CORNERS as readonly string[]).includes(v);
}

/* -------------------------------------------------------------------------
 * Clocks
 * ----------------------------------------------------------------------- */

/** Seconds before the inset clip's end it is held at: a seek to the very
 *  end lands on ``ended`` media, and ``play()`` on ended media restarts it
 *  at 0. */
export const PIP_END_GUARD_S = 0.1;

const knownDuration = (d: number | null | undefined): number | null =>
  d != null && Number.isFinite(d) && d > 0 ? d : null;

/** The inset's position for a big-video position: both clips are lined
 *  up on their own beep. Clamped to the inset clip's start, and to just
 *  before its end when its duration is known. */
export function insetTime(args: {
  bigTime: number;
  bigBeep: number;
  insetBeep: number;
  insetDuration?: number | null;
}): number {
  const t = args.bigTime - args.bigBeep + args.insetBeep;
  const d = knownDuration(args.insetDuration);
  const capped = d != null ? Math.min(t, Math.max(0, d - PIP_END_GUARD_S)) : t;
  return Math.max(0, capped);
}

export interface InsetPlan {
  /** Where the inset should be: the mapped time, clamped into its clip. */
  target: number;
  /** The big video's moment exists in the inset clip. Outside it (before
   *  the inset's start, past its end) the inset holds still at the clamp
   *  and never plays, like Audit's own secondaries. */
  inRange: boolean;
  /** Playing in range, with the big video playing and not stalled. */
  play: boolean;
}

/** One decision per media event: where the inset should be and whether
 *  it should play. */
export function insetPlan(args: {
  bigTime: number;
  bigBeep: number;
  insetBeep: number;
  insetDuration?: number | null;
  bigPaused: boolean;
  /** The big video is ``waiting`` / ``stalled``: hold the inset. */
  bigStalled?: boolean;
}): InsetPlan {
  const raw = args.bigTime - args.bigBeep + args.insetBeep;
  const d = knownDuration(args.insetDuration);
  const inRange = raw >= 0 && (d == null || raw < d - PIP_END_GUARD_S);
  return {
    target: insetTime(args),
    inRange,
    play: inRange && !args.bigPaused && !args.bigStalled,
  };
}

/** Is the inset far enough off to seek it? Checked on ``timeupdate`` and
 *  after a seek, never per frame. ``playing`` is whether the inset should
 *  be playing (``InsetPlan.play``): a held inset should show the instant. */
export function shouldCorrectDrift(args: { target: number; current: number; playing: boolean }): boolean {
  const gap = Math.abs(args.target - args.current);
  return gap > (args.playing ? PIP_DRIFT_PLAYING_S : PIP_DRIFT_PAUSED_S);
}

/** Audit's mapping (``camPlayback.planServedClip``) as a PiP beep: the
 *  served clip's time is the audit time plus ``offset``, so with the big
 *  camera's beep at ``auditBeep`` this camera's beep sits at
 *  ``auditBeep + offset``. A secondary without its own beep has no
 *  defensible mapping (``planServedClip`` parks it at offset 0): ``null``,
 *  so it never enters the inset. */
export function servedClipBeep(args: {
  index: number;
  offset: number;
  auditBeep: number | null;
  beepTime: number | null;
}): number | null {
  if (args.auditBeep == null) return null;
  if (args.index > 0 && args.beepTime == null) return null;
  return args.auditBeep + args.offset;
}

/* -------------------------------------------------------------------------
 * Media
 * ----------------------------------------------------------------------- */

export type InsetStreamKind = "scrub" | "trim" | "web" | "source" | "proxy";

/** What the inset streams for a video dict, given the kinds that already
 *  failed for this camera on the page; ``null`` when nothing is left to
 *  try (the page then marks the camera ``unavailable``).
 *
 *  The 720p rendition comes first whenever there is one, two decodes at
 *  once being the point (a 4K trim stalls on its own, #1192), whatever
 *  the full-resolution preference, which is about the big picture. A
 *  ``kind`` pins the clip the beep anchor was measured in, so only a kind
 *  sharing that anchor is a fallback:
 *  - ``source`` (coach payload, no trim) and ``proxy`` (Audit's untrimmed
 *    secondary, ``planServedClip``): that clip only.
 *  - ``web`` (coach payload, hosted): the rendition, then the trim.
 *  - ``trim`` or none (Audit's trimmed clips): the rendition when
 *    ``scrub_version`` names one, then the trim. */
export function insetStream(
  video: {
    kind?: "trim" | "source" | "web" | "proxy";
    trim_version?: string | null;
    scrub_version?: string | null;
  },
  failed: ReadonlySet<InsetStreamKind> = new Set(),
): { kind: InsetStreamKind; version: string | null } | null {
  const order: InsetStreamKind[] =
    video.kind === "source"
      ? ["source"]
      : video.kind === "proxy"
        ? ["proxy"]
        : video.kind === "web"
          ? ["web", "trim"]
          : video.scrub_version
            ? ["scrub", "trim"]
            : ["trim"];
  const kind = order.find((k) => !failed.has(k));
  if (!kind) return null;
  const version = kind === "scrub" ? (video.scrub_version ?? null) : kind === "trim" ? (video.trim_version ?? null) : null;
  return { kind, version };
}
