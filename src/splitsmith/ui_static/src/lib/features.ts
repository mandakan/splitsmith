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
 * have (AppShell + a handful of audit-page buttons). No invalidation
 * because the flag is a server-launch decision and can only change
 * across a server restart.
 */

import { useEffect, useState } from "react";

import { api } from "./api";

export type DeploymentMode = "local" | "hosted";
type Features = { lab: boolean; mode: DeploymentMode };

let cached: Promise<Features> | null = null;

function fetchFeatures(): Promise<Features> {
  if (cached === null) {
    cached = api
      .getServerFeatures()
      .catch(() => ({ lab: false, mode: "local" }) as Features);
  }
  return cached;
}

/** Returns ``true`` when the server was launched with ``--lab``.
 *  ``false`` while loading or on fetch failure -- the safe default
 *  for hiding fixture-related affordances on end-user installs. */
export function useLabEnabled(): boolean {
  const [enabled, setEnabled] = useState(false);
  useEffect(() => {
    let alive = true;
    fetchFeatures().then((f) => {
      if (alive) setEnabled(Boolean(f.lab));
    });
    return () => {
      alive = false;
    };
  }, []);
  return enabled;
}

/** Returns the deployment mode the server is running in.
 *
 * - ``"local"`` -- ``splitsmith ui`` against the host filesystem.
 *   Folder pickers + project-folder inputs are meaningful.
 * - ``"hosted"`` -- ``splitsmith serve`` against object storage. The
 *   container filesystem is ephemeral; folder pickers / host paths
 *   are suppressed and raw uploads go through the upload endpoint.
 *
 * Defaults to ``"local"`` while the initial fetch is in flight or on
 * failure -- the conservative choice that keeps the local SPA usable
 * if /api/server/features is temporarily unreachable. */
export function useDeploymentMode(): DeploymentMode {
  const [mode, setMode] = useState<DeploymentMode>("local");
  useEffect(() => {
    let alive = true;
    fetchFeatures().then((f) => {
      if (alive) setMode(f.mode === "hosted" ? "hosted" : "local");
    });
    return () => {
      alive = false;
    };
  }, []);
  return mode;
}
