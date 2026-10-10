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
/** The detail strip's half width, for seating the onset. */
export const DETAIL_HALF_S = 0.02;
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
  origin: "kept" | "rejected" | "burst";
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
function soundPeaks(peaks: SnapPeaks, from: number): Array<{ t: number; v: number }> {
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
): WalkStop[] {
  const stops: WalkStop[] = markers.map((m) => ({
    key: m.id,
    markerId: m.id,
    time: m.time,
    origin: isKept(m) ? "kept" : "rejected",
  }));
  for (const t of unmarkedBursts(markers, peaks, from)) {
    stops.push({ key: `burst-${Math.round(t * 1000)}`, markerId: null, time: t, origin: "burst" });
  }
  return stops.sort((a, b) => a.time - b.time || a.key.localeCompare(b.key));
}

/** A stop's state now: a shot when its marker is kept. */
export function stopState(marker: AuditMarker | null): StopState {
  return marker && isKept(marker) ? "shot" : "not_shot";
}

/** Where a stop is now: its marker's time, else where it was found. */
export function stopTime(stop: WalkStop, marker: AuditMarker | null): number {
  return marker ? marker.time : stop.time;
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

/** A stop counts as decided when a decision confirmed it in the state it is
 *  in now: for a candidate (``cand-<n>``, stable across a reload) by its key,
 *  for anything else at the same time to the half millisecond. Manual shots
 *  are renumbered by a save and reload, so their keys cannot be trusted. */
export function isDecided(
  stop: { key: string },
  state: StopState,
  time: number,
  decisions: ReadonlyArray<WalkDecision>,
): boolean {
  return decisions.some(
    (d) =>
      d.state === state &&
      ((stop.key.startsWith("cand-") && d.key === stop.key) || Math.abs(d.time - time) <= 0.0005),
  );
}

export interface StopFlag {
  tone: "warn" | "info";
  text: string;
}

/** What is worth knowing at a stop, in words; empty when nothing is. */
export function stopFlags(opts: {
  stop: WalkStop;
  marker: AuditMarker | null;
  markers: ReadonlyArray<AuditMarker>;
  peaks: SnapPeaks;
  level: number;
  /** How far the snap from another angle moved this shot, ms. */
  snapDisplacementMs: number | null;
}): StopFlag[] {
  const { stop, marker, markers, peaks, level, snapDisplacementMs } = opts;
  const flags: StopFlag[] = [];
  const t = stopTime(stop, marker);
  if (stop.origin === "burst") {
    flags.push({ tone: "warn", text: "Nothing marked here: the detector proposed no candidate for this sound." });
    const late = markers.find(
      (m) => isKept(m) && m.id !== marker?.id && m.time > t && m.time - t <= TAIL_FLAG_S,
    );
    if (late) {
      flags.push({
        tone: "warn",
        text: `A kept shot sits ${Math.round((late.time - t) * 1000)} ms later, in this sound's tail: if this is the shot, mark it (S) and reject that one (X) at its stop.`,
      });
    }
  }
  if (snapDisplacementMs != null && Math.abs(snapDisplacementMs) >= 20) {
    flags.push({
      tone: "warn",
      text: `Snapped ${Math.round(Math.abs(snapDisplacementMs))} ms from the other camera's time: check it is on the right sound.`,
    });
  }
  if (stopState(marker) === "shot") {
    const local = maxIn(peaks, t - SHOT_LEVEL_HALF_S, t + SHOT_LEVEL_HALF_S);
    if (level > 0 && local < BURST_LEVEL_FRAC * level) {
      flags.push({ tone: "warn", text: "No burst within 25 ms of this shot: it may sit in silence or a tail." });
    }
    const near = markers.filter(
      (m) => m.id !== marker!.id && isKept(m) && Math.abs(m.time - t) <= NEIGHBOUR_FLAG_S,
    );
    for (const m of near) {
      flags.push({
        tone: "warn",
        text: `Another shot ${Math.round(Math.abs(m.time - t) * 1000)} ms ${m.time < t ? "before" : "after"}: one of them may be an echo.`,
      });
    }
    const foot = snapToLeadingEdge(t, peaks);
    if (foot != null) {
      const ms = Math.round((foot - t) * 1000);
      if (Math.abs(ms) > 2) {
        flags.push({ tone: "info", text: `Rise foot ${Math.abs(ms)} ms ${ms < 0 ? "earlier" : "later"} (F places it there).` });
      }
    }
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

/** The close-up's full scale: the loudest point well around the stop, so a
 *  tail or noise draws small next to the shot it belongs to instead of
 *  filling the box. Never below the fixture's typical shot level's half. */
export function closeupScale(peaks: SnapPeaks, center: number, level: number): number {
  return Math.max(1e-6, maxIn(peaks, center - 0.4, center + 0.4), level / 2);
}

/** The sign-off line: kept shots against the stage's rounds. */
export function countCheck(kept: number, expected: number | null): { ok: boolean; text: string } {
  if (expected == null) return { ok: true, text: `${kept} shots. The stage's round count is not known.` };
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

/** A fixture's Review page, opened in its walk, with its source video when
 *  that is on this disk. */
export function walkHref(auditPath: string, videoPath: string | null): string {
  const video = videoPath ? `&video=${encodeURIComponent(videoPath)}` : "";
  return `/review?fixture=${encodeURIComponent(auditPath)}${video}&walk=1`;
}

/** The fixture to open after this one is signed off: the queue's next one
 *  whose shots need checking. */
export function nextFixtureToReview(
  pending: ReadonlyArray<DevReviewQueueItem>,
  currentSlug: string,
): DevReviewQueueItem | null {
  return pending.find((i) => i.slug !== currentSlug && i.review_status === "needs_review") ?? null;
}
