import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError, type AuthUser } from "@/lib/api";
import { can, featureRefusal, useCan } from "@/lib/access";

const deployment = vi.hoisted(() => ({ mode: "local" as "local" | "hosted", resolved: true }));
vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return { ...actual, useDeploymentMode: () => ({ ...deployment }) };
});

const user = (features: string[], id = "u1"): AuthUser => ({
  id,
  email: "a@x.se",
  display_name: null,
  is_admin: false,
  access_tier: "sharing",
  features,
});

// ``request()`` stores the response's raw ``detail`` as ``ApiError.body``
// (not the whole ``{detail: ...}`` envelope), so the fixtures do too.
describe("can", () => {
  it("reads the feature list, never the tier name", () => {
    const u = { ...user(["sync", "share"]), access_tier: "full" };
    expect(can(u, "create_match")).toBe(false);
    expect(can(u, "share")).toBe(true);
  });
  it("local mode (loopback user) can do everything", () => {
    expect(can(user([], "local"), "create_match")).toBe(true);
  });
  it("no user, no feature", () => {
    expect(can(null, "sync")).toBe(false);
  });
});

describe("featureRefusal", () => {
  it("names what is not included", () => {
    const err = new ApiError(403, "forbidden", { code: "feature_required", feature: "raw_upload" });
    expect(featureRefusal(err)).toBe("Uploading footage here is not included in this account's access.");
  });
  it("disabled account", () => {
    const err = new ApiError(403, "forbidden", { code: "account_disabled" });
    expect(featureRefusal(err)).toBe("This account is disabled.");
  });
  it("anything else is not a refusal", () => {
    expect(featureRefusal(new ApiError(403, "forbidden", "read_only_mirror"))).toBeNull();
    expect(featureRefusal(new ApiError(403, "forbidden", { code: "feature_required", feature: "nope" }))).toBeNull();
    expect(featureRefusal(new ApiError(409, "conflict", { code: "account_disabled" }))).toBeNull();
    expect(featureRefusal(new Error("x"))).toBeNull();
  });
  it("reads the body request() builds from a real 403 response", async () => {
    const { api } = await import("@/lib/api");
    const orig = globalThis.fetch;
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ detail: { code: "feature_required", feature: "create_match" } }), {
        status: 403,
        headers: { "Content-Type": "application/json" },
      })) as typeof fetch;
    try {
      const err = await api.getMe().catch((e: unknown) => e);
      expect(featureRefusal(err)).toBe("Creating matches here is not included in this account's access.");
    } finally {
      globalThis.fetch = orig;
    }
  });
});

describe("useCan", () => {
  // No <AuthProvider> here: the user reads as null, as it does on a
  // desktop whose /api/me failed.
  it("local mode never consults access", () => {
    deployment.mode = "local";
    expect(renderHook(() => useCan("create_match")).result.current).toBe(true);
  });
  it("hosted mode with no account has no feature", () => {
    deployment.mode = "hosted";
    expect(renderHook(() => useCan("create_match")).result.current).toBe(false);
  });
  it("an unresolved mode is not yet local", () => {
    deployment.mode = "local";
    deployment.resolved = false;
    try {
      expect(renderHook(() => useCan("create_match")).result.current).toBe(false);
    } finally {
      deployment.resolved = true;
    }
  });
});
