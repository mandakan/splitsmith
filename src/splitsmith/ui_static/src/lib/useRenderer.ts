import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";

/** Where the renderer (the headless Chromium that draws overlays and cards)
 *  stands on a local install. ``unknown`` until the status answers, and on
 *  hosted, where the container ships it and the routes 404. */
export type RendererState =
  | { kind: "unknown" }
  | { kind: "installed" }
  | { kind: "missing" }
  | { kind: "installing"; progress: number | null }
  | { kind: "failed"; message: string };

const POLL_MS = 2000;

/** The Export page's renderer line: reads the status once on a local
 *  install and runs the install as a job. ``install`` attaches to the
 *  install the app started at launch when one is running (the route
 *  returns the active job). */
export function useRenderer(local: boolean): { state: RendererState; install: () => void } {
  const [state, setState] = useState<RendererState>({ kind: "unknown" });
  const [jobId, setJobId] = useState<string | null>(null);

  useEffect(() => {
    if (!local) return;
    let alive = true;
    api
      .getChromiumStatus()
      .then((s) => alive && setState(s.installed ? { kind: "installed" } : { kind: "missing" }))
      .catch(() => alive && setState({ kind: "unknown" }));
    return () => {
      alive = false;
    };
  }, [local]);

  useEffect(() => {
    if (!jobId) return;
    let alive = true;
    let timer = 0;
    const tick = () => {
      api
        .getJob(jobId)
        .then((job) => {
          if (!alive) return;
          if (job.status === "pending" || job.status === "running") {
            setState({ kind: "installing", progress: job.progress ?? null });
            timer = window.setTimeout(tick, POLL_MS);
            return;
          }
          setJobId(null);
          setState(
            job.status === "succeeded"
              ? { kind: "installed" }
              : { kind: "failed", message: job.error ?? "The renderer did not install." },
          );
        })
        .catch(() => {
          if (alive) setJobId(null);
        });
    };
    tick();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [jobId]);

  const install = useCallback(() => {
    setState({ kind: "installing", progress: null });
    api
      .installChromium()
      .then((job) => setJobId(job.id))
      .catch(() => setState({ kind: "failed", message: "Could not start the renderer install." }));
  }, []);

  return { state, install };
}
