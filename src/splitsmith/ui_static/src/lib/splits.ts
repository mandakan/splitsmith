/**
 * Split taxonomy - single source of truth for gap-tier judgment and
 * interval-class presentation. Shared by Coach and the Results viewer.
 *
 * Tiers are self-relative and class-aware: a gap is judged only against
 * the shooter's own distribution for the same interval class within the
 * same match (quick <= p25 < typical <= p75 < long). No judgment is made
 * when the class is unset or the class has too few samples. The baseline
 * source is isolated behind TierBaselines so a rolling cross-match
 * baseline can be swapped in later without touching consumers.
 */
import type { CoachIntervalClass, CoachMatchDistributions } from "@/lib/api";

export type TierLabel = "quick" | "typical" | "long";

export interface GapTier {
  label: TierLabel;
  color: string;
}

/** Per-class baseline the tier judgment runs against. */
export interface ClassBaseline {
  p25: number;
  p75: number;
  count: number;
}

export type TierBaselines = Partial<Record<CoachIntervalClass, ClassBaseline>>;

/** Below this many same-class samples in the match we render no tier at
 *  all - no judgment on thin evidence. */
export const MIN_BASELINE_SAMPLES = 5;

export const TIER_ORDER: TierLabel[] = ["quick", "typical", "long"];

/** The red LED color is deliberately absent: a long gap is a coaching
 *  opportunity, not an alarm. */
export const TIER_COLORS: Record<TierLabel, string> = {
  quick: "var(--color-done)",
  typical: "var(--color-ink-2)",
  long: "var(--color-live)",
};

/** Marker color for gaps with no tier (unclassified / thin baseline). */
export const TIER_NEUTRAL_COLOR = "var(--color-ink-2)";

export function gapTier(
  gap: number,
  intervalClass: CoachIntervalClass | null | undefined,
  baselines: TierBaselines | null,
): GapTier | null {
  if (!intervalClass || !baselines) return null;
  const b = baselines[intervalClass];
  if (!b || b.count < MIN_BASELINE_SAMPLES) return null;
  const label: TierLabel = gap <= b.p25 ? "quick" : gap <= b.p75 ? "typical" : "long";
  return { label, color: TIER_COLORS[label] };
}

/** Exclusive-method quartiles (matches Python ``statistics.quantiles``,
 *  the backend's method) so client-derived baselines agree with served
 *  ones. Null below 2 samples. */
function quartiles(values: readonly number[]): { p25: number; p75: number } | null {
  if (values.length < 2) return null;
  const s = [...values].sort((a, b) => a - b);
  const n = s.length;
  const at = (p: number): number => {
    const pos = p * (n + 1);
    const lo = Math.floor(pos) - 1;
    const frac = pos - Math.floor(pos);
    if (lo < 0) return s[0];
    if (lo >= n - 1) return s[n - 1];
    return s[lo] + frac * (s[lo + 1] - s[lo]);
  };
  return { p25: at(0.25), p75: at(0.75) };
}

/** Build tier baselines from the match distributions payload. The
 *  ``first_shot`` class is a stage-level scalar server-side, so its
 *  baseline is derived here from ``first_shot_seconds``. Null in, null
 *  out - callers degrade to unjudged chips when the fetch failed. */
export function baselinesFromMatchDistributions(
  resp: CoachMatchDistributions | null,
): TierBaselines | null {
  if (!resp) return null;
  const out: TierBaselines = {};
  for (const d of resp.distributions) {
    if (d.p25_s != null && d.p75_s != null) {
      out[d.interval_class] = { p25: d.p25_s, p75: d.p75_s, count: d.count };
    }
  }
  const q = quartiles(resp.first_shot_seconds);
  if (q) out.first_shot = { ...q, count: resp.first_shot_seconds.length };
  return out;
}

/** Fallback threshold for split statistics on unclassified stages.
 *  Mirrors ``CoachAutoClassifyConfig.split_max_s`` (config.py) - the
 *  auto-classifier's own split cutoff, so classifying a stage never
 *  moves the figures (issue #773). */
export const SPLIT_STAT_SPLIT_MAX = 0.5;

/**
 * The splits eligible for split statistics (fastest/avg/slowest), in
 * order. Mirrors ``splitsmith.coach.statistic_splits``; if the rule
 * changes, update both (issue #772).
 *
 * Partial classification (#775): the backend classifies on audit save and
 * backfills on coach reads, so any stage served to this function is fully
 * classified for ms-bearing shots - the some() branch is all-or-nothing in
 * practice. Shots lacking ms_after_beep keep a null class and are excluded
 * by the class filter either way.
 *
 * ``shots`` is one stage's full time-ordered shot sequence, draw first.
 * On a stage with any classified interval, exactly the "split"-classed
 * intervals count - transitions, movement and reloads are the run's
 * dead time, not its shooting. An unclassified stage falls back to the
 * auto-classifier's split rule: index 0 is the draw, anything above the
 * threshold is not a split (boundary inclusive). An empty return is
 * meaningful - render nothing rather than a zero.
 */
export function statisticSplits(
  shots: readonly { split: number; interval_class: CoachIntervalClass | null }[],
): number[] {
  if (shots.some((s) => s.interval_class !== null)) {
    return shots.filter((s) => s.interval_class === "split").map((s) => s.split);
  }
  return shots
    .filter((s, i) => i > 0 && s.split <= SPLIT_STAT_SPLIT_MAX)
    .map((s) => s.split);
}

export const INTERVAL_LABEL: Record<CoachIntervalClass, string> = {
  first_shot: "Draw",
  split: "Fire",
  transition: "Transition",
  movement: "Movement",
  reload: "Reload",
  activation: "Activation",
};

/** Index of the shot currently "live" under the playhead: the last shot
 *  whose time_absolute has passed (+20ms grace so a seek exactly onto a
 *  shot counts it). No sort assumption - scans for the max qualifying
 *  time. Returns -1 before the first shot. Shared by ResultsStage
 *  (active row) and ShotTicker so the two can never drift. */
export function currentShotIndex(
  shots: readonly { time_absolute: number }[],
  time: number,
): number {
  let idx = -1;
  let bestT = -Infinity;
  for (let i = 0; i < shots.length; i++) {
    const t = shots[i].time_absolute;
    if (t <= time + 0.02 && t >= bestT) {
      bestT = t;
      idx = i;
    }
  }
  return idx;
}

/** Beep-relative elapsed seconds for the playback readouts, clamped to
 *  the stage time (the last shot's time_from_beep) so the clock stops
 *  on the final shot the way a shot timer does - not at video end.
 *  ``stageTime`` null (no shots) leaves the upper bound open. */
export function beepElapsed(
  time: number,
  beepTime: number,
  stageTime: number | null,
): number {
  const raw = Math.max(0, time - beepTime);
  return stageTime != null ? Math.min(raw, stageTime) : raw;
}

export const INTERVAL_TONE: Record<CoachIntervalClass, string> = {
  first_shot: "text-led border-led-deep bg-led/10",
  split: "text-done border-done/40 bg-done/10",
  transition: "text-live border-live/40 bg-live/10",
  movement: "text-beep border-beep/40 bg-beep-tint",
  reload: "text-manual border-manual/40 bg-manual/10",
  activation: "text-ink-2 border-rule-strong bg-surface-3",
};
