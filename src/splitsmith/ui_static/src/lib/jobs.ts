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
import { nextBatch } from "@/lib/jobBatch";

const ACTIVE_POLL_MS = 1000;
const IDLE_POLL_MS = 5000;

/** Jobs the progress strip shows. Automatic syncs run every few minutes
 *  and would make the strip flicker; they surface only when they fail. */
export function stripVisible(jobs: Job[]): Job[] {
  return jobs.filter((j) => j.kind !== "auto_sync" || j.status === "failed");
}

/** Pending or running - the set whose departures mean "something just
 *  finished". Hosts watch for active -> terminal transitions to
 *  invalidate job-derived state (#663). */
export function isJobActive(job: Job): boolean {
  return job.status === "pending" || job.status === "running";
}

export interface JobsState {
  jobs: Job[];
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
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const list = await api.listJobs({ signal: controller.signal });
      setJobs(list);
      setBatch((prev) => nextBatch(prev, stripVisible(list).filter(isJobActive)));
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

  const visible = stripVisible(jobs);
  const running = visible.filter((j) => j.status === "running");
  const pending = visible.filter((j) => j.status === "pending");
  const failed = visible.filter((j) => j.status === "failed" && !j.acknowledged);

  return {
    jobs,
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
