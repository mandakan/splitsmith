/**
 * Live anomaly detection for the audit screen (issue #42).
 *
 * Mirrors ``splitsmith.report.detect_anomalies_structured`` so the panel
 * the user sees while keeping / rejecting markers shows the same flags
 * that ``report.txt`` will. Living in TypeScript instead of round-tripping
 * to the server keeps recompute zero-latency on every mutation.
 *
 * Tuning constants are duplicated from ``report.py``; SPEC.md is the
 * canonical source. If thresholds change, update both files (the backend
 * tests + this module's tests would catch the drift).
 */
import type { AuditMarker } from "@/components/MarkerLayer";

/** Beep -> last shot vs official stage time tolerance (seconds). */
const OFFICIAL_TIME_TOLERANCE_S = 0.5;
/** Splits below this look like a single shot detected twice. */
const DOUBLE_DETECTION_MAX_S = 0.08;
/** Splits above this look like a missed shot or a long transition. */
const LONG_PAUSE_MAX_S = 3.0;
/** Informational shot-count band (typical IPSC stage). */
const TYPICAL_ROUND_RANGE: [number, number] = [8, 32];

export type AnomalyKind =
  | "no_shots"
  | "stage_time_mismatch"
  | "double_detection"
  | "long_pause"
  | "shot_count_low"
  | "shot_count_high";

export type AnomalySeverity = "info" | "warn";

export interface Anomaly {
  kind: AnomalyKind;
  severity: AnomalySeverity;
  message: string;
  /** 1-based shot index for click-to-jump. Null for stage-level
   *  anomalies (count band, no shots). */
  shot_number: number | null;
  /** Time on the audit timeline (clip-local seconds) for the offending
   *  shot. Null when ``shot_number`` is null. */
  time: number | null;
}

interface KeptShot {
  /** 1-based, in the same order as ``shots[].shot_number`` after a save. */
  shot_number: number;
  /** Time on the audit timeline (clip-local seconds). */
  time: number;
  /** ``time - beep_time``; used by the report rule wording. */
  time_from_beep: number;
  /** Difference from the previous shot's ``time_from_beep`` (seconds).
   *  Shot 1's split is the draw (= ``time_from_beep``). */
  split: number;
}

/** Build the kept-shot list the audit screen treats as the source of truth.
 *
 * ``markers`` are the in-memory state from the audit page; we filter to
 * detected + manual (the user's "keep" set), sort by time, and compute
 * ``time_from_beep`` + ``split`` so the rules match :class:`Shot` exactly.
 *
 * ``beepTime`` is the audit-clip-local beep position (``peaks.beep_time``);
 * when null we fall back to 0 so anomalies still fire on shot count etc.
 */
export function keptShotsFromMarkers(
  markers: AuditMarker[],
  beepTime: number | null,
): KeptShot[] {
  const beep = beepTime ?? 0;
  const kept = markers
    .filter((m) => m.kind === "detected" || m.kind === "manual")
    .slice()
    .sort((a, b) => a.time - b.time || a.id.localeCompare(b.id));
  const out: KeptShot[] = [];
  let prev: number | null = null;
  kept.forEach((m, i) => {
    const time_from_beep = m.time - beep;
    const split = prev == null ? time_from_beep : time_from_beep - prev;
    prev = time_from_beep;
    out.push({
      shot_number: i + 1,
      time: m.time,
      time_from_beep,
      split,
    });
  });
  return out;
}

/** Compute the structured anomaly list for the current audit state.
 *
 * Mirrors :func:`splitsmith.report.detect_anomalies_structured` so the
 * audit screen and the report.txt file flag the exact same things.
 */
export function detectAnomalies(
  shots: KeptShot[],
  stageTime: number,
): Anomaly[] {
  const out: Anomaly[] = [];

  if (shots.length === 0) {
    out.push({
      kind: "no_shots",
      severity: "warn",
      message: "No shots detected in the stage window.",
      shot_number: null,
      time: null,
    });
    return out;
  }

  const last = shots[shots.length - 1];
  const delta = last.time_from_beep - stageTime;
  if (Math.abs(delta) > OFFICIAL_TIME_TOLERANCE_S) {
    const direction = delta > 0 ? "after" : "before";
    out.push({
      kind: "stage_time_mismatch",
      severity: "warn",
      message:
        `Last detected shot is ${Math.round(Math.abs(delta) * 1000)} ms ` +
        `${direction} official stage time ` +
        `(${last.time_from_beep.toFixed(3)} s vs ${stageTime.toFixed(3)} s).`,
      shot_number: last.shot_number,
      time: last.time,
    });
  }

  for (let i = 1; i < shots.length; i++) {
    const s = shots[i];
    if (s.split < DOUBLE_DETECTION_MAX_S) {
      out.push({
        kind: "double_detection",
        severity: "warn",
        message:
          `Shot ${s.shot_number} split is ${Math.round(s.split * 1000)} ms ` +
          `(< ${Math.round(DOUBLE_DETECTION_MAX_S * 1000)} ms): ` +
          `possible double-detection.`,
        shot_number: s.shot_number,
        time: s.time,
      });
    } else if (s.split > LONG_PAUSE_MAX_S) {
      out.push({
        kind: "long_pause",
        severity: "warn",
        message:
          `Shot ${s.shot_number} split is ${s.split.toFixed(3)} s ` +
          `(> ${LONG_PAUSE_MAX_S.toFixed(1)} s): missed shot or long transition?`,
        shot_number: s.shot_number,
        time: s.time,
      });
    }
  }

  const [lo, hi] = TYPICAL_ROUND_RANGE;
  if (shots.length < lo || shots.length > hi) {
    const isLow = shots.length < lo;
    out.push({
      kind: isLow ? "shot_count_low" : "shot_count_high",
      severity: "info",
      message:
        `Detected ${shots.length} shots; typical IPSC stages have ${lo}-${hi}. ` +
        `Review for ${
          isLow ? "missed shots" : "false positives (echoes / other bays)"
        }.`,
      shot_number: null,
      time: null,
    });
  }

  return out;
}
