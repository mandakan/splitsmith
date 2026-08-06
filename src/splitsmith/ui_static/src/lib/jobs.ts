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

import { ApiError, api, type Job } from "@/lib/api";

const ACTIVE_POLL_MS = 1000;
const IDLE_POLL_MS = 5000;

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
  refresh: () => Promise<void>;
  acknowledge: (job: Job) => Promise<void>;
  acknowledgeAll: () => Promise<void>;
  cancel: (job: Job) => Promise<void>;
}

export function useJobs(): JobsState {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const list = await api.listJobs({ signal: controller.signal });
      setJobs(list);
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

  const running = jobs.filter((j) => j.status === "running");
  const pending = jobs.filter((j) => j.status === "pending");
  const failed = jobs.filter((j) => j.status === "failed" && !j.acknowledged);

  return {
    jobs,
    running,
    pending,
    failed,
    error,
    refresh,
    acknowledge,
    acknowledgeAll,
    cancel,
  };
}
