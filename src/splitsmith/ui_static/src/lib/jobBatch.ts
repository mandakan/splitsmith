/**
 * The progress strip's batch (#1190): which jobs its "N of M" counts.
 *
 * The poll list is not a history. Locally it is every retained job, hosted
 * it is the active jobs, the unacknowledged failures and the 20 most
 * recent others (#1182), so counting its terminal rows reads "21 of 22"
 * against a two-job batch. A batch is instead the set of jobs seen active
 * since the strip last had nothing active: it starts when the active set
 * goes from empty to non-empty, grows with every job seen active after
 * that, and resets when the active set is empty again.
 *
 * Known limits, both undercounts and never old rows: a page loaded
 * mid-batch starts the batch at the jobs active on its first poll, and a
 * job that is submitted and finishes between two polls is never seen
 * active. A batch member that leaves the active set by any route --
 * succeeded, failed, cancelled, or dropped from the hosted list -- counts
 * as done.
 */
import type { Job } from "@/lib/api";

/** The batch after a poll whose active (strip-visible pending or running)
 *  jobs are ``active``. Returns ``prev`` itself when nothing changed, so a
 *  state setter can bail out. */
export function nextBatch(prev: ReadonlySet<string>, active: readonly Job[]): ReadonlySet<string> {
  if (active.length === 0) return prev.size === 0 ? prev : new Set();
  if (active.every((j) => prev.has(j.id))) return prev;
  const next = new Set(prev);
  for (const j of active) next.add(j.id);
  return next;
}

export interface BatchProgress {
  /** Batch members no longer active. */
  done: number;
  /** Every batch member, done or active. */
  total: number;
}

/** The strip's count. ``active`` is folded in, so a render that sees jobs
 *  the stored batch has not caught up with still counts them. */
export function batchProgress(batch: ReadonlySet<string>, active: readonly Job[]): BatchProgress {
  const ids = new Set(batch);
  for (const j of active) ids.add(j.id);
  const activeIds = new Set(active.map((j) => j.id));
  return { done: ids.size - activeIds.size, total: ids.size };
}
