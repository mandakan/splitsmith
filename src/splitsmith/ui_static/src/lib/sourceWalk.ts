/**
 * The review walk of a fixture snapped from another camera, led by its
 * source: the source's shots are the stops, in order, so the walk asks where
 * each known shot is on this camera instead of auditing every sound again.
 *
 * The two cameras are lined up on the shot pattern itself, never on their
 * beeps: a wrong beep on one camera (a phone's clip with its beep at 0.0 s)
 * put every snapped shot of a fixture on the wrong sound. Each shot's sound
 * still reaches the two cameras a few ms apart (they stand metres apart and
 * the shooter moves), so a stop looks for its sound within ``SOURCE_WINDOW_S``
 * of where the fit expects it.
 */

import type { AuditMarker } from "@/components/MarkerLayer";
import { snapToLeadingEdge, type SnapPeaks } from "@/lib/peak-snap";
import { soundPeaks, type StopFlag, type WalkStop } from "@/lib/walk";

/** Offsets are voted in bins this wide. */
const VOTE_BIN_S = 0.005;
/** A source shot counts as landing on a sound this close to its fitted time. */
export const FIT_WINDOW_S = 0.025;
/** A stop looks this far either side of the fitted time for its shot. */
export const SOURCE_WINDOW_S = 0.04;
/** A kept shot no source shot claimed is moved onto an unclaimed one this
 *  close: a snap that landed on the wrong sound (a tail, an echo) is the
 *  same shot, a few tens of ms off. */
export const REPAIR_WINDOW_S = 0.3;
/** Below this share of source shots landing on a sound the fit is not
 *  trusted (the wrong clip, or a camera too far away to hear the shots). */
export const ALIGN_OK_SHARE = 0.8;
/** A sound counts as an onset at this many times the clip's median level. */
const ONSET_NOISE_FACTOR = 4;
const MIN_ONSET_LEVEL = 0.05;

/** This camera's time for a source time: ``s + offset + drift * (s - s0)``. */
export interface SourceAlignment {
  offset: number;
  /** Seconds per second: the two clocks run at slightly different rates. */
  drift: number;
  s0: number;
  /** Source shots with a sound within ``FIT_WINDOW_S`` of their fitted time. */
  matched: number;
  total: number;
}

export function mapSourceTime(a: SourceAlignment, s: number): number {
  return s + a.offset + a.drift * (s - a.s0);
}

/** The source's time for this camera's ``t``: ``mapSourceTime`` undone. */
export function unmapSourceTime(a: SourceAlignment, t: number): number {
  return a.s0 + (t - a.offset - a.s0) / (1 + a.drift);
}

/** Whether the fit is good enough to lead the walk. */
export function alignmentOk(a: SourceAlignment | null): boolean {
  return a != null && a.total > 0 && a.matched >= ALIGN_OK_SHARE * a.total;
}

/** Every loud sound's onset (its rise foot) after ``from``, in time order. */
export function soundOnsets(peaks: SnapPeaks, from: number): number[] {
  const p = peaks.peaks;
  if (p.length === 0) return [];
  const sorted = [...p].sort((a, b) => a - b);
  const threshold = Math.max(MIN_ONSET_LEVEL, ONSET_NOISE_FACTOR * sorted[Math.floor(sorted.length / 2)]);
  return soundPeaks(peaks, from)
    .filter((s) => s.v >= threshold)
    .map((s) => snapToLeadingEdge(s.t, peaks) ?? s.t);
}

function nearest(times: ReadonlyArray<number>, t: number): number | null {
  let best: number | null = null;
  for (const x of times) if (best == null || Math.abs(x - t) < Math.abs(best - t)) best = x;
  return best;
}

/** Line the source's shots up with this camera's onsets: the offset most
 *  (shot, onset) pairs agree on, then offset and drift by least squares over
 *  the shots that land within ``FIT_WINDOW_S``, three times. ``null`` with
 *  no shots or no onsets. */
export function alignToSource(
  source: ReadonlyArray<number>,
  onsets: ReadonlyArray<number>,
): SourceAlignment | null {
  if (source.length === 0 || onsets.length === 0) return null;
  const votes = new Map<number, number>();
  for (const s of source) {
    for (const t of onsets) {
      const b = Math.round((t - s) / VOTE_BIN_S);
      votes.set(b, (votes.get(b) ?? 0) + 1);
    }
  }
  let mode = 0;
  let best = -1;
  for (const b of [...votes.keys()].sort((x, y) => x - y)) {
    const v = (votes.get(b - 1) ?? 0) + (votes.get(b) ?? 0) + (votes.get(b + 1) ?? 0);
    if (v > best) {
      best = v;
      mode = b;
    }
  }
  const s0 = source[0];
  let a: SourceAlignment = { offset: mode * VOTE_BIN_S, drift: 0, s0, matched: 0, total: source.length };
  for (let round = 0; round < 3; round++) {
    const xs: number[] = [];
    const ys: number[] = [];
    for (const s of source) {
      const t = nearest(onsets, mapSourceTime(a, s));
      if (t != null && Math.abs(t - mapSourceTime(a, s)) <= FIT_WINDOW_S) {
        xs.push(s - s0);
        ys.push(t - s);
      }
    }
    if (xs.length < 2) break;
    const mx = xs.reduce((u, v) => u + v, 0) / xs.length;
    const my = ys.reduce((u, v) => u + v, 0) / ys.length;
    let sxx = 0;
    let sxy = 0;
    for (let i = 0; i < xs.length; i++) {
      sxx += (xs[i] - mx) ** 2;
      sxy += (xs[i] - mx) * (ys[i] - my);
    }
    const drift = sxx > 1e-9 ? sxy / sxx : 0;
    a = { ...a, drift, offset: my - drift * mx };
  }
  const matched = source.filter((s) => {
    const t = nearest(onsets, mapSourceTime(a, s));
    return t != null && Math.abs(t - mapSourceTime(a, s)) <= FIT_WINDOW_S;
  }).length;
  return { ...a, matched };
}

const isKept = (m: AuditMarker) => m.kind === "detected" || m.kind === "manual";

/** The walk's stops, led by the source: one per source shot, in order, on
 *  the kept shot nearest its fitted time (within ``SOURCE_WINDOW_S``), else
 *  on the nearest onset, else on the fitted time itself. A source shot left
 *  without a kept shot takes the nearest unclaimed one within
 *  ``REPAIR_WINDOW_S`` and asks for it there (``moveTo``: the snap put it on
 *  the wrong sound). Every kept shot still unclaimed is an extra to answer for. */
export function sourceStops(
  source: ReadonlyArray<number>,
  align: SourceAlignment,
  onsets: ReadonlyArray<number>,
  markers: ReadonlyArray<AuditMarker>,
): WalkStop[] {
  const kept = markers.filter(isKept);
  const used = new Set<string>();
  const stops: WalkStop[] = [];
  source.forEach((s, index) => {
    const expected = mapSourceTime(align, s);
    let claim: AuditMarker | null = null;
    for (const m of kept) {
      if (used.has(m.id) || Math.abs(m.time - expected) > SOURCE_WINDOW_S) continue;
      if (claim == null || Math.abs(m.time - expected) < Math.abs(claim.time - expected)) claim = m;
    }
    const onset = nearest(onsets, expected);
    const heard = onset != null && Math.abs(onset - expected) <= SOURCE_WINDOW_S;
    const info = { index, count: source.length, expected, heard };
    if (claim) {
      used.add(claim.id);
      stops.push({ key: `source-${index}`, markerId: claim.id, time: claim.time, origin: "source", source: info });
    } else {
      const time = Math.round((heard ? onset! : expected) * 1000) / 1000;
      stops.push({ key: `source-${index}`, markerId: null, time, origin: "source", source: info });
    }
  });
  // The snap's misplaced shots: pair each unclaimed source shot with the
  // nearest unclaimed kept shot, in time order.
  for (const stop of stops) {
    if (stop.markerId != null) continue;
    let pick: AuditMarker | null = null;
    for (const m of kept) {
      if (used.has(m.id) || Math.abs(m.time - stop.time) > REPAIR_WINDOW_S) continue;
      if (pick == null || Math.abs(m.time - stop.time) < Math.abs(pick.time - stop.time)) pick = m;
    }
    if (pick) {
      used.add(pick.id);
      stop.markerId = pick.id;
      stop.moveFrom = pick.time;
    }
  }
  for (const m of kept) {
    if (!used.has(m.id)) stops.push({ key: m.id, markerId: m.id, time: m.time, origin: "extra" });
  }
  return stops.sort((a, b) => a.time - b.time || a.key.localeCompare(b.key));
}

/** A sound's loudest point, 10 ms before ``t`` to 40 ms after. */
export function levelAt(peaks: SnapPeaks, t: number): number {
  const p = peaks.peaks;
  if (p.length === 0 || peaks.duration <= 0) return 0;
  const w = peaks.duration / p.length;
  const a = Math.max(0, Math.floor((t - 0.01) / w));
  const b = Math.min(p.length, Math.ceil((t + 0.04) / w));
  let m = 0;
  for (let i = a; i < b; i++) if (p[i] > m) m = p[i];
  return m;
}

/** The source camera's typical shot: the median level at its shots. */
export function typicalLevelAt(peaks: SnapPeaks, shots: ReadonlyArray<number>): number {
  const v = shots.map((s) => levelAt(peaks, s)).sort((x, y) => x - y);
  return v.length ? v[Math.floor(v.length / 2)] : 0;
}

/** Under this share of a typical shot a camera heard nothing there. */
const UNHEARD_SHARE = 0.1;
/** At or above this share a camera heard a shot-like sound. */
const HEARD_SHARE = 0.4;
/** Both cameras at or above this: loud for both. */
const LOUD_SHARE = 0.5;
/** Both cameras under this: quiet for both, as a shot in another bay is. */
const QUIET_SHARE = 0.35;

/** What the two cameras heard at a stop, in words. ``here`` and ``there``
 *  are shares of each camera's typical shot. The shooter's head camera sits
 *  next to the gun: the shooter's own shot is loud on it, a shot in another
 *  bay is quiet on both, and a sound only one camera's mic picked up (a
 *  bump, handling) is loud on that one alone. */
export function levelFlags(opts: {
  here: number;
  there: number;
  camera: string | null;
  origin: WalkStop["origin"];
}): StopFlag[] {
  const { here, there, origin } = opts;
  const camera = opts.camera ?? "the source camera";
  const pct = (v: number) => `${Math.round(v * 100)} %`;
  const flags: StopFlag[] = [
    { tone: "info", text: `Loudness: here ${pct(here)}, ${camera} ${pct(there)} of a typical shot.` },
  ];
  if (origin === "source" && here < UNHEARD_SHARE && there >= HEARD_SHARE) {
    flags.push({
      tone: "warn",
      kind: "verdict",
      text: `This camera hears nothing here; the ${camera} does. Most likely a sound only its mic picked up, not a shot: Not a shot here, and the source may need a look.`,
    });
  } else if (origin === "extra" && here >= LOUD_SHARE && there >= LOUD_SHARE) {
    flags.push({
      tone: "warn",
      kind: "verdict",
      text: "Loud on both cameras: more likely the shooter's own shot than one from another bay. The source may have missed it.",
    });
  } else if (origin === "extra" && here < QUIET_SHARE && there < QUIET_SHARE) {
    flags.push({ tone: "info", kind: "verdict", text: "Quiet on both cameras: most likely a shot in another bay." });
  }
  return flags;
}

/** The beep is looked for this far either side of where the fit puts it.
 *  The fit is made of shots, which travel from wherever the shooter stands;
 *  the beep travels from the timer, so it maps up to about 0.1 s off (90 ms
 *  on hostfinalen stage 7). No shot comes this soon after a beep. */
const BEEP_SEARCH_S = 0.2;

/** Where this camera's beep should be: the source's beep through the fit,
 *  on the onset of the loudest sound within ``BEEP_SEARCH_S`` of it. */
export function beepProposal(align: SourceAlignment, sourceBeep: number, peaks: SnapPeaks): number {
  const t = mapSourceTime(align, sourceBeep);
  const onset = snapToLeadingEdge(t, peaks, BEEP_SEARCH_S);
  return Math.round((onset ?? t) * 1000) / 1000;
}
