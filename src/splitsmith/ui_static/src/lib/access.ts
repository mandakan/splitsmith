/**
 * Account features (spec 2026-10-03). Pages ask ``can(user, feature)``;
 * nothing in the SPA compares tier names. Local mode has no accounts, so
 * the loopback user can do everything.
 */
import { ApiError, type AuthUser } from "@/lib/api";
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

const WHAT: Record<Feature, string> = {
  sync: "Syncing from the desktop app",
  share: "Sharing",
  create_match: "Creating matches here",
  raw_upload: "Uploading footage here",
  hosted_compute: "Running detection and renders here",
};

/** One muted line for a refusal, or null when ``err`` is not one.
 *
 *  ``ApiError.body`` holds the response's raw ``detail`` (see
 *  ``request()`` in api.ts), so the refusal's ``{code, feature}`` is the
 *  body itself, not nested under a ``detail`` key. */
export function featureRefusal(err: unknown): string | null {
  if (!(err instanceof ApiError) || err.status !== 403) return null;
  const detail = err.body;
  if (!detail || typeof detail !== "object") return null;
  const { code, feature } = detail as { code?: unknown; feature?: unknown };
  if (code === "account_disabled") return "This account is disabled.";
  if (code === "feature_required" && typeof feature === "string" && Object.hasOwn(WHAT, feature)) {
    return `${WHAT[feature as Feature]} is not included in this account's access.`;
  }
  return null;
}
