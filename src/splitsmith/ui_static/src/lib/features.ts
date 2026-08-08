/**
 * Server feature flags accessor (issue #149 follow-up).
 *
 * The Lab nav entry + every fixture-related action in the production
 * UI is gated on whether ``splitsmith ui --lab`` was passed. The flag
 * comes from the same ``/api/server/features`` endpoint AppShell
 * already polls; this hook lets non-Lab pages reuse the same answer
 * without re-fetching.
 *
 * Implementation: a tiny module-level promise cache. The first hook
 * call kicks off the fetch; subsequent calls share the same promise
 * and resolve once. Cheap and safe for the small set of consumers we
 * have. No invalidation because the flag is a server-launch decision
 * and can only change across a server restart.
 */

import { useEffect, useState } from "react";

import { api } from "./api";

export type DeploymentMode = "local" | "hosted";

export interface DeploymentModeState {
  /** The server's deployment mode. "local" until resolved. */
  mode: DeploymentMode;
  /** False while /api/server/features is still in flight. Surfaces
   *  that differ per mode render a neutral skeleton until this is
   *  true, instead of flashing the local variant at hosted users.
   *  A failed fetch resolves with the local fallback (resolved: true)
   *  so a desktop install with a flaky first request stays usable. */
  resolved: boolean;
}

type Features = { lab: boolean; mode: DeploymentMode };

let cached: Promise<Features> | null = null;
/** Synchronously readable copy of the settled answer so components
 *  mounting after the first resolve start resolved (no skeleton
 *  flash on every later mount). */
let settled: Features | null = null;

function fetchFeatures(): Promise<Features> {
  if (cached === null) {
    cached = api
      .getServerFeatures()
      .catch(() => ({ lab: false, mode: "local" }) as Features)
      .then((f) => {
        settled = f;
        return f;
      });
  }
  return cached;
}

/** Returns ``true`` when the server was launched with ``--lab``.
 *  ``false`` while loading or on fetch failure - the safe default
 *  for hiding fixture-related affordances on end-user installs. */
export function useLabEnabled(): boolean {
  const [enabled, setEnabled] = useState(settled ? Boolean(settled.lab) : false);
  useEffect(() => {
    let alive = true;
    void fetchFeatures().then((f) => {
      if (alive) setEnabled(Boolean(f.lab));
    });
    return () => {
      alive = false;
    };
  }, []);
  return enabled;
}

/** Deployment mode + whether it has actually been fetched yet.
 *
 * - ``"local"`` - ``splitsmith ui`` against the host filesystem.
 *   Folder pickers + project-folder inputs are meaningful.
 * - ``"hosted"`` - ``splitsmith serve`` against object storage;
 *   raw uploads go through the upload endpoint.
 */
export function useDeploymentMode(): DeploymentModeState {
  const [state, setState] = useState<DeploymentModeState>(() =>
    settled
      ? { mode: settled.mode === "hosted" ? "hosted" : "local", resolved: true }
      : { mode: "local", resolved: false },
  );
  useEffect(() => {
    let alive = true;
    void fetchFeatures().then((f) => {
      if (alive) {
        setState({ mode: f.mode === "hosted" ? "hosted" : "local", resolved: true });
      }
    });
    return () => {
      alive = false;
    };
  }, []);
  return state;
}
