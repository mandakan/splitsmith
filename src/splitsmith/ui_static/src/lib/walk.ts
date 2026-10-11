/**
 * The lab Review page's walk (#1363): every place a shot could be, visited
 * once, in time order, so a fixture's shots end up complete and each on its
 * onset (the rise foot, docs/METHODOLOGY.md). Nothing is filtered out:
 *
 * - every candidate, kept or rejected, and every manual shot;
 * - every burst as loud as the fixture's shots with no marker near it, which
 *   is a shot the detector never proposed or a sound that only looks like one.
 *
 * The rise foot is drawn as a reference and placed on F; it is never the
 * default. A stop is decided when the person confirms it; decisions are
 * audit events, so a reopened fixture resumes at its first undecided stop.
 * A confirmed shot records whether its time is the rule's (``placementOf``):
 * the review settles which sounds are shots, and a definition change can
 * re-time the rule's shots from their sounds (docs/METHODOLOGY.md, comparing
 * to a timer).
 */

import type { AuditMarker } from "@/components/MarkerLayer";
import type { DevReviewQueueItem } from "@/lib/api";
import { snapToLeadingEdge, type SnapPeaks } from "@/lib/peak-snap";

/** Stamped on every decision and on the sign-off. A walk that turns out to be
 *  flawed re-flags exactly the fixtures this version signed off. */
export const WALK_METHOD = "walk-1";
export const WALK_DECIDED_EVENT = "walk_decided";

/** The close-up's half width: the shot, its lead-in and its neighbours. */
export const CLOSEUP_HALF_S = 0.15;
/** The onset strip's half width: 80 ms, the window the shot-time guide's
 *  example figures show, so the two can be compared side by side. */
export const DETAIL_HALF_S = 0.04;
/** A burst at least this share of the fixture's typical shot level ... */
export const BURST_LEVEL_FRAC = 0.35;
/** ... that is the loudest point this far either side of it ... */
export const BURST_PEAK_HALF_S = 0.05;
/** ... with no marker this close to its onset is a stop of its own. */
export const BURST_COVER_S = 0.03;
/** A sound this soon after one at least ``TAIL_RATIO`` times louder is a
 *  reflection in its tail, not a stop. A real follow-up shot this close (a
 *  fast double is 150 ms and more) arrives at a similar level. */
export const TAIL_S = 0.13;
export const TAIL_RATIO = 2.5;
/** A kept shot this close to another one is flagged. */
export const NEIGHBOUR_FLAG_S = 0.06;
/** No burst this soon after the beep is a stop: the beep tone itself, and no
 *  shot comes sooner (the detector skips the same 500 ms, METHODOLOGY.md). */
export const BEEP_GUARD_S = 0.5;
/** A kept shot this soon after an unmarked burst is pointed out from the burst:
 *  it is likely in the tail of the real shot. */
export const TAIL_FLAG_S = 0.2;
/** The level around a kept shot is read this far either side of it. */
const SHOT_LEVEL_HALF_S = 0.025;

export type StopState = "shot" | "not_shot";

export interface WalkStop {
  /** Stable for this walk session: the marker's id, or ``burst-<ms>``. */
  key: string;
  /** The marker this stop is about; ``null`` for a burst nobody marked yet. */
  markerId: string | null;
  /** Where the stop sits when the walk opens: the marker's time, or the
   *  burst's onset. */
  time: number;
  /** ``source`` / ``extra``: a walk led by the fixture's source camera
   *  (lib/sourceWalk): one of the source's shots, or a kept shot the source
   *  has none for. */
  origin: "kept" | "rejected" | "burst" | "source" | "extra";
  source?: {
    /** Which of the source's shots, from 0. */
    index: number;
    count: number;
    /** Where the fit expects it on this camera. */
    expected: number;
    /** A sound was found within the window around ``expected``. */
    heard: boolean;
  };
  /** A kept shot the walk moves to ``time`` when it opens: where it was. */
  moveFrom?: number;
}

export interface WalkDecision {
  /** The stop's key when it was decided. */
  key: string;
  state: StopState;
  /** The shot's time as confirmed, or the stop's time for "not a shot". */
  time: number;
}

function binWidth(peaks: SnapPeaks): number {
  return peaks.duration / Math.max(1, peaks.peaks.length);
}

function maxIn(peaks: SnapPeaks, lo: number, hi: number): number {
  const w = binWidth(peaks);
  const a = Math.max(0, Math.floor(lo / w));
  const b = Math.min(peaks.peaks.length, Math.ceil(hi / w));
  let m = 0;
  for (let i = a; i < b; i++) if (peaks.peaks[i] > m) m = peaks.peaks[i];
  return m;
}

const isKept = (m: AuditMarker) => m.kind === "detected" || m.kind === "manual";

/** Every sound's loudest point after ``from``: the first bin of the loudest
 *  level within ``BURST_PEAK_HALF_S`` either side, in time order. */
export function soundPeaks(peaks: SnapPeaks, from: number): Array<{ t: number; v: number }> {
  const w = binWidth(peaks);
  const half = Math.max(1, Math.round(BURST_PEAK_HALF_S / w));
  const p = peaks.peaks;
  const out: Array<{ t: number; v: number }> = [];
  for (let i = Math.max(0, Math.ceil(from / w)); i < p.length; i++) {
    let isPeak = p[i] > 0;
    for (let j = Math.max(0, i - half); isPeak && j <= Math.min(p.length - 1, i + half); j++) {
      if (p[j] > p[i] || (p[j] === p[i] && j < i)) isPeak = false;
    }
    if (!isPeak) continue;
    out.push({ t: i * w, v: p[i] });
    i += half;
  }
  return out;
}

/** The fixture's typical shot level: the median of its loudest sounds, as
 *  many as it has kept shots. Read from the audio, not from where the shots
 *  are marked: a fixture snapped from another camera can have every shot in
 *  the tail of the real one, and the level there would let every echo
 *  through as a stop. ``0`` without kept shots. */
export function typicalShotLevel(markers: ReadonlyArray<AuditMarker>, peaks: SnapPeaks, from = 0): number {
  const k = markers.filter(isKept).length;
  if (k === 0) return 0;
  const loudest = soundPeaks(peaks, from)
    .map((s) => s.v)
    .sort((a, b) => b - a)
    .slice(0, k);
  if (loudest.length === 0) return 0;
  return loudest[Math.floor((loudest.length - 1) / 2)];
}

/** Loud places with no marker near them, at their onset, in time order.
 *  ``from`` skips everything before it (the beep). */
export function unmarkedBursts(
  markers: ReadonlyArray<AuditMarker>,
  peaks: SnapPeaks,
  from = 0,
): number[] {
  const level = typicalShotLevel(markers, peaks, from);
  if (level <= 0) return [];
  const threshold = BURST_LEVEL_FRAC * level;
  const times = markers.map((m) => m.time);
  const sounds = soundPeaks(peaks, from);
  const out: number[] = [];
  for (const s of sounds) {
    if (s.v < threshold) continue;
    // A reflection in a much louder sound's tail is part of that sound.
    const inTail = sounds.some((p) => p.t < s.t && s.t - p.t <= TAIL_S && p.v >= TAIL_RATIO * s.v);
    if (inTail) continue;
    const onset = snapToLeadingEdge(s.t, peaks) ?? s.t;
    const covered = times.some((t) => t >= onset - BURST_COVER_S && t <= s.t + BURST_COVER_S);
    if (!covered) out.push(Math.round(onset * 1000) / 1000);
  }
  return out;
}

/** Every stop, in time order. */
export function walkStops(
  markers: ReadonlyArray<AuditMarker>,
  peaks: SnapPeaks,
  from = 0,
  scope: WalkScope = "all",
): WalkStop[] {
  const stops: WalkStop[] = markers
    .filter((m) => scope === "all" || isKept(m))
    .map((m) => ({
      key: m.id,
      markerId: m.id,
      time: m.time,
      origin: isKept(m) ? "kept" : "rejected",
    }));
  if (scope === "all") {
    for (const t of unmarkedBursts(markers, peaks, from)) {
      stops.push({ key: `burst-${Math.round(t * 1000)}`, markerId: null, time: t, origin: "burst" });
    }
  }
  return stops.sort((a, b) => a.time - b.time || a.key.localeCompare(b.key));
}

/** What a walk visits. ``all``: every candidate, kept or rejected, and every
 *  unmarked sound, for labels that may miss or misplace shots. ``shots``: the
 *  kept shots only, for labels a person made on this audio, where a missed
 *  shot is rare and the shot count against the stage's rounds is the net. */
export type WalkScope = "all" | "shots" | "source";

/** The scope a fixture opens with: a snapped fixture follows its source
 *  camera's shots when the source is on this disk, else every sound; a
 *  fixture a person audited walks its kept shots. */
export function defaultScope(opts: {
  snapped: boolean;
  /** The snapped fixture's source is on this disk (``source`` scope). */
  hasSource?: boolean;
  kept: number;
  expectedRounds: number | null;
}): WalkScope {
  if (opts.snapped) return opts.hasSource ? "source" : "all";
  // A person audited this audio: which sounds are shots is settled, only
  // their times are checked. A count above the stage's rounds is normal
  // (extra shots, makeups: 141 of 154 audited fixtures), never a reason to
  // audit every sound again; the end screen still compares the two.
  return "shots";
}

/** Whether a fixture's shots were snapped from another camera: the same
 *  rule as ``fixture_schema.review_status`` (an ``anchor`` block or a
 *  ``promote-from-anchor`` history entry). */
export function isSnapped(doc: { anchor?: unknown; history?: unknown }): boolean {
  if (doc.anchor) return true;
  const history = Array.isArray(doc.history) ? doc.history : [];
  return history.some((h) => typeof h === "object" && h != null && (h as { action?: unknown }).action === "promote-from-anchor");
}

/** The stop nearest a time, for a click on the stage overview. */
export function nearestStop(stops: ReadonlyArray<{ time: number }>, t: number): number {
  let best = -1;
  for (let i = 0; i < stops.length; i++) {
    if (best < 0 || Math.abs(stops[i].time - t) < Math.abs(stops[best].time - t)) best = i;
  }
  return best;
}

/** A stop's state now: a shot when its marker is kept. */
export function stopState(marker: AuditMarker | null): StopState {
  return marker && isKept(marker) ? "shot" : "not_shot";
}

/** Where a stop is now: its marker's time, else where it was found. */
export function stopTime(stop: WalkStop, marker: AuditMarker | null): number {
  return marker ? marker.time : stop.time;
}

/** How a confirmed shot's time relates to the definition. The review fixes
 *  which sound is a shot; the millisecond is the rule's. A shot left on the
 *  rule (``rule``) is re-timed from its sound whenever the definition
 *  changes; one placed anywhere else (``override``) is the person's call and
 *  kept, and listed for a second look. ``ruleTime`` is ``null`` where the
 *  rule finds no shot around the time. */
export interface Placement {
  placement: "rule" | "override";
  ruleTime: number | null;
  /** ``time - ruleTime`` in whole ms; ``null`` without a rule time. */
  offsetMs: number | null;
}

export function placementOf(time: number, peaks: SnapPeaks, footFrom: number = time): Placement {
  const foot = snapToLeadingEdge(footFrom, peaks);
  const ruleTime = foot == null ? null : Math.round(foot * 1000) / 1000;
  if (ruleTime == null) return { placement: "override", ruleTime: null, offsetMs: null };
  const offsetMs = Math.round((time - ruleTime) * 1000);
  return { placement: offsetMs === 0 ? "rule" : "override", ruleTime, offsetMs };
}

/** Decisions from audit events of this walk version, oldest first. */
export function decisionsFrom(
  events: ReadonlyArray<{ kind: string; payload: Record<string, unknown> }>,
): WalkDecision[] {
  const out: WalkDecision[] = [];
  for (const e of events) {
    if (e.kind !== WALK_DECIDED_EVENT || e.payload.method !== WALK_METHOD) continue;
    const { stop, state, time } = e.payload;
    if (typeof stop === "string" && (state === "shot" || state === "not_shot") && typeof time === "number") {
      out.push({ key: stop, state, time });
    }
  }
  return out;
}

/** A stop counts as decided when a decision confirmed exactly what it shows
 *  now: the same state at the same time, to the half millisecond. Never by
 *  key: a shot moved after its Enter must be stopped at again, a save and
 *  reload renumbers manual shots, and a re-detection renumbers candidates.
 *  The cost is one extra stop: a kept candidate rejected and then reloaded
 *  is drawn at the candidate's own time, which nobody confirmed. */
export function isDecided(state: StopState, time: number, decisions: ReadonlyArray<WalkDecision>): boolean {
  return decisions.some((d) => d.state === state && Math.abs(d.time - time) <= 0.0005);
}

export interface StopFlag {
  tone: "warn" | "info";
  text: string;
}

/** The one question a stop asks, what the page sees there, and which keys
 *  answer it. Worded from the stop's situation so the person never has to
 *  work out what is being asked. */
export interface StopPrompt {
  question: string;
  /** What the audio shows here, in a sentence. */
  seen: string;
  /** The keys that answer the question, most likely first. */
  keys: string;
  tone: "neutral" | "warn";
}

export function stopPrompt(opts: {
  marker: AuditMarker | null;
  markers: ReadonlyArray<AuditMarker>;
  time: number;
  peaks: SnapPeaks;
  level: number;
}): StopPrompt {
  const { marker, markers, time, peaks, level } = opts;
  const loud = level > 0 ? BURST_LEVEL_FRAC * level : Infinity;
  if (stopState(marker) === "not_shot") {
    // A burst starting here: the loudest point just after the stop.
    const burst = maxIn(peaks, time - 0.005, time + 0.04) >= loud;
    return burst
      ? {
          question: "Is this a shot?",
          seen: "A burst as loud as a shot starts here, and no shot is kept on it.",
          keys: "S if it is a shot (then F seats it on its onset) · Enter if it is not",
          tone: "warn",
        }
      : {
          question: "Is this a shot?",
          seen: "Nothing here is as loud as the stage's shots.",
          keys: "Enter if it is not a shot · S if it is",
          tone: "neutral",
        };
  }
  const local = maxIn(peaks, time - SHOT_LEVEL_HALF_S, time + SHOT_LEVEL_HALF_S);
  if (local < loud) {
    return {
      question: "Is this shot on a sound?",
      seen: "Nothing within 25 ms is as loud as a shot: it sits in silence or in another sound's tail. The real shot is often just before.",
      keys: "X if it is not a shot · click the start of the right sound to move it there",
      tone: "warn",
    };
  }
  const near = markers.find((m) => m.id !== marker!.id && isKept(m) && Math.abs(m.time - time) <= NEIGHBOUR_FLAG_S);
  if (near) {
    const ms = Math.round(Math.abs(near.time - time) * 1000);
    return {
      question: "Two shots on one sound?",
      seen: `Another kept shot is ${ms} ms ${near.time < time ? "before" : "after"} this one.`,
      keys: "X if this one is an echo or a duplicate · Enter if both are shots",
      tone: "warn",
    };
  }
  return {
    question: "Is this shot on its onset?",
    seen: "The red line should sit where the burst first rises out of the background.",
    keys: "Enter if it does · F seats it on the rise foot · click or arrows to place it yourself",
    tone: "neutral",
  };
}

/** What is worth knowing at a stop, in words; empty when nothing is. */
export function stopFlags(opts: {
  stop: WalkStop;
  marker: AuditMarker | null;
  markers: ReadonlyArray<AuditMarker>;
  /** How far the snap from another angle moved this shot, ms. */
  snapDisplacementMs: number | null;
}): StopFlag[] {
  const { stop, marker, markers, snapDisplacementMs } = opts;
  const flags: StopFlag[] = [];
  const t = stopTime(stop, marker);
  if (stop.origin === "source" && stop.source) {
    const { index, count, heard } = stop.source;
    const which = `Shot ${index + 1} of ${count} on the source camera`;
    if (stopState(marker) === "shot") {
      flags.push(
        stop.moveFrom != null
          ? {
              tone: "warn",
              text: `${which}. The snap had put it ${Math.round(Math.abs(stop.moveFrom - stop.time) * 1000)} ms ${stop.moveFrom > stop.time ? "later" : "earlier"}, on another sound; moved here.`,
            }
          : { tone: "info", text: `${which}.` },
      );
    }
    else if (heard) {
      flags.push({ tone: "warn", text: `${which}: no shot is kept here yet. If this sound is it, mark it Shot.` });
    } else {
      flags.push({
        tone: "warn",
        text: `${which}: no sound within 40 ms of where it should be. This camera may not have heard it.`,
      });
    }
  }
  if (stop.origin === "extra" && stopState(marker) === "shot") {
    flags.push({
      tone: "warn",
      text: "The source camera has no shot here: most likely an echo, a shot in another bay or a duplicate. Not a shot, unless you hear one the source missed.",
    });
  }
  if (stop.origin === "burst") {
    flags.push({ tone: "info", text: "The detector proposed no candidate for this sound." });
    const late = markers.find(
      (m) => isKept(m) && m.id !== marker?.id && m.time > t && m.time - t <= TAIL_FLAG_S,
    );
    if (late) {
      flags.push({
        tone: "warn",
        text: `A kept shot sits ${Math.round((late.time - t) * 1000)} ms later, in this sound's tail: if this is the shot, mark it (S) here and reject that one (X) at its stop.`,
      });
    }
  }
  // A walk led by the source re-placed the snap: its old distance means nothing.
  if (stop.origin !== "source" && snapDisplacementMs != null && Math.abs(snapDisplacementMs) >= 20) {
    flags.push({
      tone: "warn",
      text: `Snapped ${Math.round(Math.abs(snapDisplacementMs))} ms from the other camera's time: check it is on the right sound.`,
    });
  }
  return flags;
}

export type WalkAction =
  | { kind: "confirm" }
  | { kind: "shot" }
  | { kind: "not_shot" }
  | { kind: "rise_foot" }
  | { kind: "listen" }
  | { kind: "guide" }
  | { kind: "loop" }
  | { kind: "back" }
  | { kind: "nudge"; ms: number };

/** The walk's keys; ``null`` leaves the key to the page (Cmd+Z, Cmd+S, zoom). */
export function walkActionForKey(e: {
  key: string;
  shiftKey: boolean;
  metaKey: boolean;
  ctrlKey: boolean;
  altKey: boolean;
}): WalkAction | null {
  if (e.metaKey || e.ctrlKey || e.altKey) return null;
  switch (e.key) {
    case "Enter":
      return { kind: "confirm" };
    case "s":
    case "S":
      return { kind: "shot" };
    case "x":
    case "X":
      return { kind: "not_shot" };
    case "f":
    case "F":
      return { kind: "rise_foot" };
    case " ":
      return { kind: "listen" };
    case "Backspace":
      return { kind: "back" };
    case "?":
      return { kind: "guide" };
    case "l":
    case "L":
      return { kind: "loop" };
    case "ArrowLeft":
      return { kind: "nudge", ms: e.shiftKey ? -5 : -1 };
    case "ArrowRight":
      return { kind: "nudge", ms: e.shiftKey ? 5 : 1 };
    default:
      return null;
  }
}

/** The peaks' bins inside ``center +- halfS``, each with its start time. */
export function windowBins(
  peaks: SnapPeaks,
  center: number,
  halfS: number,
): Array<{ t: number; v: number }> {
  const n = peaks.peaks.length;
  if (n === 0 || peaks.duration <= 0) return [];
  const w = binWidth(peaks);
  const lo = Math.max(0, Math.floor((center - halfS) / w));
  const hi = Math.min(n, Math.ceil((center + halfS) / w));
  const out: Array<{ t: number; v: number }> = [];
  for (let i = lo; i < hi; i++) out.push({ t: i * w, v: peaks.peaks[i] });
  return out;
}

/** The sign-off line: kept shots against the stage's rounds. */
export function countCheck(
  kept: number,
  expected: number | null,
  against: "rounds" | "source" = "rounds",
): { ok: boolean; text: string } {
  if (expected == null) return { ok: true, text: `${kept} shots. The stage's round count is not known.` };
  if (against === "source") {
    if (kept === expected) return { ok: true, text: `${kept} shots, matching the source camera's ${expected}.` };
    return {
      ok: false,
      text: `${kept} shots, but the source camera has ${expected}. Check the extras and any shot this camera did not hear.`,
    };
  }
  if (kept === expected) return { ok: true, text: `${kept} shots, matching the stage's ${expected} rounds.` };
  return {
    ok: false,
    text: `${kept} shots, but the stage has ${expected} rounds. Extra shots and makeups happen; check before signing off.`,
  };
}

const GUIDE_KEY = "splitsmith.reviewWalk.guideOpen";

/** The walk's guide is open until it is hidden once; then it stays as left. */
export function readGuideOpen(): boolean {
  try {
    return window.localStorage.getItem(GUIDE_KEY) !== "0";
  } catch {
    return true;
  }
}

export function writeGuideOpen(open: boolean): void {
  try {
    window.localStorage.setItem(GUIDE_KEY, open ? "1" : "0");
  } catch {
    // Without storage the guide opens with every fixture; nothing else depends on it.
  }
}

const LOOP_KEYS = { desktop: "splitsmith.reviewWalk.loop", phone: "splitsmith.reviewWalk.loop.phone" } as const;

/** Whether each stop's sound repeats as the walk reaches it: off on the
 *  desktop until turned on, on for the phone (no keyboard to ask for it),
 *  each remembered on its own. */
export function readLoopOn(device: "desktop" | "phone"): boolean {
  try {
    const v = window.localStorage.getItem(LOOP_KEYS[device]);
    return v == null ? device === "phone" : v === "1";
  } catch {
    return device === "phone";
  }
}

export function writeLoopOn(on: boolean, device: "desktop" | "phone"): void {
  try {
    window.localStorage.setItem(LOOP_KEYS[device], on ? "1" : "0");
  } catch {
    // Without storage the loop starts at the device's default.
  }
}

/** What the walk plays around a stop, seconds: ``pre`` before it, ``post``
 *  after, then ``gap`` of silence before the next pass. The phone's loop is
 *  short and starts after a silence, so the stop is always the first sound
 *  heard and neighbouring stops sound different; the desktop keeps its wide
 *  window, which carries the shot's lead-in and its echo. */
export interface LoopShape {
  pre: number;
  post: number;
  gap: number;
}

export const WALK_LOOP: Readonly<Record<"desktop" | "phone", LoopShape>> = {
  desktop: { pre: 0.5, post: 0.7, gap: 0 },
  phone: { pre: 0.15, post: 0.25, gap: 0.6 },
};

/** The span one pass plays around ``t``, inside the clip when its length is known. */
export function loopWindow(
  t: number,
  shape: LoopShape,
  duration: number | null,
): { start: number; end: number; gap: number } {
  const start = Math.max(0, t - shape.pre);
  const end = duration != null ? Math.min(duration - 0.01, t + shape.post) : t + shape.post;
  return { start: Math.round(start * 1e6) / 1e6, end: Math.round(end * 1e6) / 1e6, gap: shape.gap };
}

/** Under this a neighbour and the stop blur into one sound at full speed. */
const BLUR_S = 0.06;

/** The other stops a pass around ``t`` plays, in words, nearest first;
 *  ``null`` when the loop holds this stop alone. */
export function loopNeighbourFlag(t: number, others: ReadonlyArray<number>, shape: LoopShape): StopFlag | null {
  const near = others
    .map((o) => o - t)
    .filter((d) => Math.abs(d) >= 0.001 && d >= -shape.pre && d <= shape.post)
    .sort((a, b) => Math.abs(a) - Math.abs(b));
  if (near.length === 0) return null;
  const words = (d: number) => `${Math.round(Math.abs(d) * 1000)} ms ${d < 0 ? "before" : "after"}`;
  if (near.length === 1) {
    const blur = Math.abs(near[0]) < BLUR_S;
    return {
      tone: blur ? "warn" : "info",
      text: `Another stop ${words(near[0])} this one plays in the loop.${blur ? " Half speed hears them as two sounds." : ""}`,
    };
  }
  return {
    tone: near.some((d) => Math.abs(d) < BLUR_S) ? "warn" : "info",
    text: `Other stops in the loop: ${near.map(words).join(", ")}.`,
  };
}

const SLOW_KEY = "splitsmith.reviewWalk.slow";

/** Half-speed listening in the walk: off until turned on, then remembered. */
export function readSlowOn(): boolean {
  try {
    return window.localStorage.getItem(SLOW_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeSlowOn(on: boolean): void {
  try {
    window.localStorage.setItem(SLOW_KEY, on ? "1" : "0");
  } catch {
    // Without storage the walk starts at full speed.
  }
}

/** Under this share of a typical shot, a sound at a stop is quiet enough
 *  to be another bay's shot or an echo. */
const QUIET_SHARE = 0.35;

/** A warning when the sound at ``t`` (10 ms before it to 40 ms after) is far
 *  quieter than the stage's typical shot: zoomed to its own loudest point a
 *  close-up makes any sound look like a shot. */
export function quietFlag(peaks: SnapPeaks, t: number, level: number): StopFlag | null {
  if (level <= 0) return null;
  const loudest = Math.max(0, ...windowBins(peaks, t + 0.015, 0.025).map((b) => b.v));
  const share = loudest / level;
  if (share >= QUIET_SHARE) return null;
  return {
    tone: "warn",
    text: `Quiet: ${Math.round(share * 100)} % of a typical shot on this stage. A shot in another bay, or an echo, sounds like this.`,
  };
}

/** The Review page fits a phone only in its walk (``?walk=1``, or the old
 *  ``?step=1``); the full editor still needs a desk. */
export function walkFitsPhone(search: URLSearchParams): boolean {
  return search.get("walk") === "1" || search.get("step") === "1";
}

/** The review's phone layout: a narrow window, a finger for a pointer (a
 *  phone held sideways is 844 px or more wide), or ``?phone=1``. Callers
 *  decide it once per page, so turning the phone never swaps the layout
 *  mid-walk. */
export function phoneLayout(search: URLSearchParams, matches: (query: string) => boolean): boolean {
  return search.get("phone") === "1" || matches("(max-width: 767px)") || matches("(pointer: coarse)");
}

/** ``window.matchMedia`` as ``phoneLayout`` takes it. */
export function mediaMatches(query: string): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(query).matches;
}

/** A fixture's Review page, opened in its walk, with its source video when
 *  that is on this disk. */
export function walkHref(auditPath: string, videoPath: string | null): string {
  const video = videoPath ? `&video=${encodeURIComponent(videoPath)}` : "";
  return `/review?fixture=${encodeURIComponent(auditPath)}${video}&walk=1`;
}

/** The fixture to open after this one is signed off: the queue's next one
 *  whose shots need checking. */
/** Where a short session picks up: a walk already started (still pending,
 *  so not signed off), else the queue's first fixture needing review. */
export function continueTarget(pending: ReadonlyArray<DevReviewQueueItem>): DevReviewQueueItem | null {
  const open = pending.filter((i) => i.review_status === "needs_review");
  return open.find((i) => (i.walk_decisions ?? 0) > 0) ?? open[0] ?? null;
}

export function nextFixtureToReview(
  pending: ReadonlyArray<DevReviewQueueItem>,
  currentSlug: string,
): DevReviewQueueItem | null {
  return pending.find((i) => i.slug !== currentSlug && i.review_status === "needs_review") ?? null;
}
