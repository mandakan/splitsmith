/**
 * Jobs polling state - the data half of the jobs rail (#663).
 *
 * Split out of components/Jobs.tsx so the hook can be owned by shells
 * (MatchShell, AppShell, DeveloperShell) without tripping the
 * fast-refresh only-export-components rule there. Each shell calls
 * ``useJobs()`` once and hands the state to its JobsSurface; MatchShell
 * additionally watches the active set for settlements so job-derived
 * page state (sidebar stage status, beep-review badge) can refetch.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api, featureRefusal, type Job } from "@/lib/api";
import { foldExports } from "@/lib/exportProgress";
import { nextBatch } from "@/lib/jobBatch";

const ACTIVE_POLL_MS = 1000;
const IDLE_POLL_MS = 5000;

/** Background kinds: automatic syncs run every few minutes, and a
 *  derive_audio job (spec 2026-10-14) is already told by the page that
 *  asked for it ("Preparing audio"); either would make the strip flicker. */
const BACKGROUND_KINDS = new Set(["auto_sync", "derive_audio"]);

/** Jobs the progress strip shows. Background kinds surface only when they fail. */
export function stripVisible(jobs: Job[]): Job[] {
  return jobs.filter((j) => !BACKGROUND_KINDS.has(j.kind) || j.status === "failed");
}

/** Pending or running - the set whose departures mean "something just
 *  finished". Hosts watch for active -> terminal transitions to
 *  invalidate job-derived state (#663). */
export function isJobActive(job: Job): boolean {
  return job.status === "pending" || job.status === "running";
}

/** Jobs that ended between the poll ``prev`` and the poll ``next``: seen
 *  active and now not, or absent before and already ended. The second kind
 *  matters: a fast job (a 1.5 s shot detection after a beep confirm) can
 *  start and finish between two idle polls and is never seen running.
 *  ``prev`` null is the first poll, whose finished rows are history. */
export function settledBetween(prev: Job[] | null, next: Job[]): Job[] {
  if (prev === null) return [];
  const before = new Map(prev.map((j) => [j.id, j]));
  return next.filter((j) => {
    if (isJobActive(j)) return false;
    const was = before.get(j.id);
    return was === undefined || isJobActive(was);
  });
}

export interface JobsState {
  jobs: Job[];
  /** Jobs that ended since the previous poll (``settledBetween``); a new
   *  array only when there are some, so an effect on it runs once each. */
  settled: Job[];
  running: Job[];
  pending: Job[];
  failed: Job[];
  error: string | null;
  /** A retry the server refused for the account (``feature_required``):
   *  the job it was for and the sentence to show in place of Retry. */
  retryRefusal: { jobId: string; text: string } | null;
  /** Ids of the strip's current batch (``lib/jobBatch``): every job seen
   *  active since the active set was last empty. What "N of M" counts. */
  batch: ReadonlySet<string>;
  refresh: () => Promise<void>;
  acknowledge: (job: Job) => Promise<void>;
  acknowledgeAll: () => Promise<void>;
  cancel: (job: Job) => Promise<void>;
  retry: (job: Job) => Promise<void>;
}

export function useJobs(): JobsState {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [retryRefusal, setRetryRefusal] = useState<JobsState["retryRefusal"]>(null);
  const [batch, setBatch] = useState<ReadonlySet<string>>(() => new Set());
  const [settled, setSettled] = useState<Job[]>([]);
  const lastListRef = useRef<Job[] | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const list = await api.listJobs({ signal: controller.signal });
      const ended = settledBetween(lastListRef.current, list);
      lastListRef.current = list;
      if (ended.length > 0) setSettled(ended);
      setJobs(list);
      setBatch((prev) => nextBatch(prev, foldExports(stripVisible(list)).filter(isJobActive)));
      setError(null);
    } catch (e) {
      if (controller.signal.aborted) return;
      if (e instanceof ApiError) setError(e.detail);
      else if (e instanceof Error) setError(e.message);
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => {
      abortRef.current?.abort();
    };
  }, [refresh]);

  const anyActive = jobs.some(isJobActive);
  useEffect(() => {
    const ms = anyActive ? ACTIVE_POLL_MS : IDLE_POLL_MS;
    const id = window.setInterval(() => void refresh(), ms);
    return () => window.clearInterval(id);
  }, [anyActive, refresh]);

  const acknowledge = useCallback(async (job: Job) => {
    try {
      const updated = await api.acknowledgeJob(job.id);
      setJobs((prev) => prev.map((j) => (j.id === job.id ? updated : j)));
    } catch {
      /* swallow */
    }
  }, []);

  const acknowledgeAll = useCallback(async () => {
    try {
      await api.acknowledgeAllFailures();
      void refresh();
    } catch {
      /* swallow */
    }
  }, [refresh]);

  const cancel = useCallback(async (job: Job) => {
    try {
      const updated = await api.cancelJob(job.id);
      setJobs((prev) => prev.map((j) => (j.id === job.id ? updated : j)));
    } catch {
      /* swallow */
    }
  }, []);

  const retry = useCallback(
    async (job: Job) => {
      try {
        await api.retryJob(job.id);
        setRetryRefusal(null);
        await refresh();
      } catch (e) {
        // A refusal is the answer, not a glitch: show it where Retry was.
        // Anything else stays swallowed; the next poll shows the state.
        const text = featureRefusal(e);
        if (text) setRetryRefusal({ jobId: job.id, text });
      }
    },
    [refresh],
  );

  // An export's jobs (a grid and its stage jobs) are one row (spec 2026-10-25).
  const visible = foldExports(stripVisible(jobs));
  const running = visible.filter((j) => j.status === "running");
  const pending = visible.filter((j) => j.status === "pending");
  const failed = visible.filter((j) => j.status === "failed" && !j.acknowledged);

  return {
    jobs,
    settled,
    running,
    pending,
    failed,
    error,
    retryRefusal,
    batch,
    refresh,
    acknowledge,
    acknowledgeAll,
    cancel,
    retry,
  };
}
