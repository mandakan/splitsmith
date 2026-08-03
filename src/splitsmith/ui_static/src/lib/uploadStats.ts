import { formatBytes, formatEta } from "@/lib/format";
import type { PendingUpload } from "@/lib/uploads";

/** One observation of the queue-wide sent-byte total at a point in time. */
export interface ThroughputSample {
  /** Milliseconds, from the same clock as `now`. */
  t: number;
  /** Total bytes sent across the whole queue at `t`. */
  bytes: number;
}

export interface QueueStats {
  /** 1-based position of the active file among the files that will still
   *  be attempted, or null when nothing is uploading. */
  activeIndex: number | null;
  /** How many files will still be attempted (everything not cancelled). */
  countable: number;
  doneCount: number;
  failedCount: number;
  /** Bytes sent by files that can still finish. */
  bytesSent: number;
  /** Bytes those files will send in total. */
  bytesTotal: number;
  bytesRemaining: number;
  /** 0-100, over `bytesTotal`. */
  pct: number;
  /** Seconds until the queue drains at the observed rate, or null when
   *  the window is too cold to project from. */
  etaSeconds: number | null;
}

/** The window must span at least this long before its rate is trusted --
 *  half a second of observation lets one slow chunk project absurd
 *  numbers. */
const MIN_WINDOW_MS = 2000;

/** How stale the newest reading may be before the rate is disowned.
 *  A stalled upload emits no progress events, so nothing re-trims its
 *  window -- without this the last known rate would keep projecting a
 *  confident ETA for a transfer that has stopped moving. */
const MAX_SAMPLE_AGE_MS = 10_000;

/** Files that can still contribute bytes to the run. `cancelled` will
 *  never upload and `error` already stopped, so neither belongs in a
 *  total the progress bar is expected to reach. */
function isLive(u: PendingUpload): boolean {
  return u.status === "done" || u.status === "uploading" || u.status === "queued";
}

/**
 * Derive the queue-level numbers both upload surfaces render (#556).
 *
 * Pure on purpose: `now` and `samples` are arguments rather than read
 * from a clock or a ref, so every branch is reachable from a test. The
 * dock and the modal read one computed object from the upload context
 * instead of each deriving its own -- they disagreed before, which is
 * how the two defects this replaces went unnoticed.
 */
export function queueStats(
  uploads: PendingUpload[],
  samples: ThroughputSample[],
  now: number,
): QueueStats {
  // Cancelled files drop out of the count entirely: they will not be
  // attempted, so numbering the active file against them ("uploading 4
  // of 10" when only 8 will ever run) is a lie that persists for the
  // rest of the run.
  const attempted = uploads.filter((u) => u.status !== "cancelled");
  const activePosition = attempted.findIndex((u) => u.status === "uploading");

  const live = uploads.filter(isLive);
  const bytesTotal = live.reduce((a, u) => a + u.file.size, 0);
  const bytesSent = live.reduce((a, u) => a + u.bytesSent, 0);
  const bytesRemaining = Math.max(0, bytesTotal - bytesSent);

  const stats: QueueStats = {
    activeIndex: activePosition === -1 ? null : activePosition + 1,
    countable: attempted.length,
    doneCount: uploads.filter((u) => u.status === "done").length,
    failedCount: uploads.filter((u) => u.status === "error").length,
    bytesSent,
    bytesTotal,
    bytesRemaining,
    pct: bytesTotal > 0 ? Math.min(100, Math.round((bytesSent / bytesTotal) * 100)) : 0,
    etaSeconds: null,
  };

  if (bytesRemaining <= 0 || samples.length < 2) return stats;
  const oldest = samples[0];
  const newest = samples[samples.length - 1];
  if (now - newest.t > MAX_SAMPLE_AGE_MS) return stats;
  const elapsedMs = newest.t - oldest.t;
  if (elapsedMs < MIN_WINDOW_MS) return stats;
  const moved = newest.bytes - oldest.bytes;
  if (moved <= 0) return stats;
  stats.etaSeconds = bytesRemaining / (moved / (elapsedMs / 1000));
  return stats;
}

/**
 * Compose the one-line queue readout both upload surfaces render.
 *
 * Kept out of the component so every clause is reachable from a test:
 * this is where a wrong label would hide, and a label that says
 * "Uploading 3 of 12" over a drained queue is precisely the confusion
 * #556 is about.
 */
export function summaryLine(
  queue: QueueStats,
  inFlight: boolean,
  note?: string,
): string {
  const { primary, detail } = summaryParts(queue, inFlight, note);
  return detail === null ? primary : `${primary} . ${detail}`;
}

/**
 * The same readout split into a stable headline and a volatile detail.
 *
 * The dock is 360px wide, where the full line wraps -- and wrapping
 * mid-clause changes the dock's height every time the ETA appears or the
 * byte count crosses a unit boundary. Splitting at a chosen point keeps
 * the headline on one line and confines the movement to the second.
 * `detail` is null when there is nothing beyond the headline to say.
 */
export function summaryParts(
  queue: QueueStats,
  inFlight: boolean,
  note?: string,
): { primary: string; detail: string | null } {
  const primary = [
    inFlight && queue.activeIndex !== null
      ? `Uploading ${queue.activeIndex} of ${queue.countable}`
      : `Uploads ${queue.doneCount}/${queue.countable}`,
    `${queue.pct}%`,
  ].join(" . ");
  const detail = [
    // Only worth the width while there is something left to send.
    inFlight && queue.bytesRemaining > 0 ? `${formatBytes(queue.bytesRemaining)} left` : null,
    inFlight ? formatEta(queue.etaSeconds) : null,
    queue.failedCount > 0 ? `${queue.failedCount} failed` : null,
    note,
  ]
    .filter(Boolean)
    .join(" . ");
  return { primary, detail: detail === "" ? null : detail };
}

/**
 * Trim `samples` to the trailing `windowMs` ending at `now`.
 *
 * Keeps the newest sample from *before* the cutoff, because that reading
 * is the window's left edge -- dropping it would measure the rate from
 * the cutoff instant instead of from a real observation.
 *
 * When every sample predates the cutoff the upload has produced no
 * progress tick for a whole window, i.e. it has stalled. Only the last
 * reading survives, which leaves the window too short to project from
 * and so suppresses the ETA rather than extrapolating from stale bytes.
 */
export function trimSamples(
  samples: ThroughputSample[],
  now: number,
  windowMs: number,
): ThroughputSample[] {
  const cutoff = now - windowMs;
  const firstInside = samples.findIndex((s) => s.t >= cutoff);
  if (firstInside === -1) return samples.slice(-1);
  if (firstInside === 0) return samples;
  return samples.slice(firstInside - 1);
}
