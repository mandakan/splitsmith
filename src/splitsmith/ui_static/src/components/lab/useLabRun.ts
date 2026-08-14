/**
 * Shared eval-run data hook for the Lab surfaces. Lifted verbatim (in
 * behavior) from the legacy ``Lab`` page component (deleted -- #331
 * final task): the single-flight ``runEval`` (with its ``Array.isArray``
 * slug guard, so onClick call sites can pass the callback directly
 * without a MouseEvent leaking into the request body), the mount
 * hydration that adopts the server's cached last-run + its config, and
 * the 120 ms debounced rescore that keeps slider tweaks feeling live.
 *
 * Dev-mode Lab surfaces (fixture detail page, Validate page) use this
 * hook instead of duplicating the logic.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { api, type LabEvalConfig, type LabEvalRun } from "@/lib/api";

export const DEFAULT_CONFIG: LabEvalConfig = {
  consensus: 2,
  apriori_boost: 1.0,
  tolerance_ms: 75.0,
  use_expected_rounds: true,
  voter_a_floor_override: null,
  voter_b_threshold_override: null,
  voter_c_threshold_override: null,
};

export interface UseLabRunResult {
  run: LabEvalRun | null;
  setRun: (r: LabEvalRun | null) => void;
  config: LabEvalConfig;
  setConfig: (c: Partial<LabEvalConfig>) => void;
  resetConfig: () => void;
  runEval: (slugs?: string[]) => Promise<void>;
  evalLoading: boolean;
  rescoreLoading: boolean;
  error: string | null;
}

export function useLabRun(opts?: { autoRescore?: boolean }): UseLabRunResult {
  const autoRescore = opts?.autoRescore !== false;

  const [run, setRun] = useState<LabEvalRun | null>(null);
  const [config, setConfigState] = useState<LabEvalConfig>(DEFAULT_CONFIG);
  const [evalLoading, setEvalLoading] = useState(false);
  const [rescoreLoading, setRescoreLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Set right before the mount-hydration effect adopts the server's
  // config, so the debounce effect below can tell "config changed
  // because we just hydrated" apart from "config changed because the
  // caller tuned a slider" -- only the latter should fire a rescore.
  const hydratingConfigRef = useRef(false);

  useEffect(() => {
    // Hydrate from the server's most-recent run cache so navigating
    // away and back doesn't wipe the eval state.
    api
      .getLastLabRun()
      .then((r) => {
        hydratingConfigRef.current = true;
        setRun(r);
        setConfigState(r.config);
      })
      .catch(() => {
        // 404 = no eval has run yet; that's the normal first-load case.
      });
  }, []);

  // Coalesce concurrent runEval calls. Without this, each label-save
  // fallback (when the server cache is cold) submits its own job ->
  // many labels -> many eval jobs.
  const inFlightEvalRef = useRef<Promise<void> | null>(null);
  // Optional ``slugs`` scopes the eval to a fixture subset. Guarded with
  // Array.isArray because call sites pass this directly as an onClick
  // handler (the arg is then a MouseEvent, which must not leak into the
  // request body).
  const runEval = useCallback(
    async (slugs?: unknown): Promise<void> => {
      if (inFlightEvalRef.current) return inFlightEvalRef.current;
      const wanted = Array.isArray(slugs) ? (slugs as string[]) : undefined;
      const p = (async () => {
        setEvalLoading(true);
        setError(null);
        try {
          const job = await api.runLabEval({ slugs: wanted, config, persist: true });
          const finished = await api.pollJob(job.id, () => {
            /* jobs rail polls /api/jobs on its own interval and renders
               the progress; we just need to await terminal status here. */
          });
          if (finished.status !== "succeeded") {
            throw new Error(finished.error ?? `eval ${finished.status}`);
          }
          const result = await api.getLastLabRun();
          setRun(result);
        } catch (err) {
          setError(String(err));
        } finally {
          setEvalLoading(false);
          inFlightEvalRef.current = null;
        }
      })();
      inFlightEvalRef.current = p;
      return p;
    },
    [config],
  );

  // Live rescore: when the caller moves a slider, hit /api/lab/rescore.
  // Skip when we don't have a cached universe yet (eval must run at
  // least once first), when autoRescore is off (the detail page drives
  // its own config flow), on the initial mount, and on the config change
  // caused by adopting the hydrated run's config (not a real edit).
  const mountedRef = useRef(false);
  useEffect(() => {
    if (!mountedRef.current) {
      mountedRef.current = true;
      return;
    }
    if (hydratingConfigRef.current) {
      hydratingConfigRef.current = false;
      return;
    }
    if (!autoRescore) return;
    if (!run) return;
    setRescoreLoading(true);
    const id = window.setTimeout(async () => {
      try {
        const updated = await api.rescoreLabUniverse(config);
        setRun(updated);
      } catch (err) {
        setError(String(err));
      } finally {
        setRescoreLoading(false);
      }
    }, 120);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config]);

  const setConfig = useCallback((patch: Partial<LabEvalConfig>) => {
    setConfigState((prev) => ({ ...prev, ...patch }));
  }, []);

  const resetConfig = useCallback(() => {
    setConfigState(DEFAULT_CONFIG);
  }, []);

  return {
    run,
    setRun,
    config,
    setConfig,
    resetConfig,
    runEval,
    evalLoading,
    rescoreLoading,
    error,
  };
}
