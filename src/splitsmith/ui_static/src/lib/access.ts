/**
 * Account features (spec 2026-10-03). Pages ask ``can(user, feature)``;
 * nothing in the SPA compares tier names. Local mode has no accounts, so
 * the loopback user can do everything.
 */
import type { AuthUser } from "@/lib/api";
import { useAuthUser } from "@/lib/auth";
import { useDeploymentMode } from "@/lib/features";

export type Feature = "sync" | "share" | "create_match" | "raw_upload" | "hosted_compute";

const LOOPBACK_ID = "local";

export function can(user: AuthUser | null | undefined, feature: Feature): boolean {
  if (!user) return false;
  if (user.id === LOOPBACK_ID) return true;
  // A server that predates features sends none: treat that as none.
  return Array.isArray(user.features) && user.features.includes(feature);
}

/** ``can`` for the current account. Local mode never consults access:
 *  every feature is on there, even if ``/api/me`` failed (the desktop is
 *  never gated on it). */
export function useCan(feature: Feature): boolean {
  const { mode, resolved } = useDeploymentMode();
  const user = useAuthUser();
  return (resolved && mode === "local") || can(user, feature);
}

// The refusal sentence lives beside ``apiErrorText`` (which maps it for
// every page); re-exported here for callers that read access helpers.
export { featureRefusal } from "@/lib/api";
