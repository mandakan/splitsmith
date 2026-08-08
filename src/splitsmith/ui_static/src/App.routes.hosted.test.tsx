/**
 * AuthGate's device-flow stash-and-bounce, in hosted mode (#719).
 *
 * AuthGate is shared by every route in the app, so the stash-on-bounce
 * and pick-up-on-"/" logic added for the device flow must not change
 * anything for a hosted visitor who never touches /desktop/approve.
 * Local mode is covered by App.routes.test.tsx and is exempt from all of
 * this (AuthGate never redirects there); this file only exercises the
 * hosted branch. Separate file, not a describe block: the features.ts
 * mode cache is per module registry (see GlobalBar.hosted.test.tsx for
 * the same split).
 */
import { render, screen, waitFor } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";

const getDevicePending = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getMe: vi.fn().mockResolvedValue({
        id: "u1",
        email: "m@thias.se",
        display_name: null,
        is_admin: false,
      }),
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "hosted" }),
      getHealth: vi.fn().mockResolvedValue({
        status: "ok",
        bound: false,
        project_name: null,
        project_root: null,
        match_id: null,
        kind: null,
        default_shooter_slug: null,
        schema_version: null,
      }),
      getScoreboardIdentity: vi.fn().mockResolvedValue(null),
      getRecentProjectsDetail: vi.fn().mockResolvedValue([]),
      getDevicePending: (...a: unknown[]) => getDevicePending(...a),
    },
  };
});

window.matchMedia =
  window.matchMedia ??
  ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }) as unknown as MediaQueryList);

const STASH_KEY = "splitsmith.deviceApproveCode";

async function renderAt(path: string) {
  window.history.pushState({}, "", path);
  const { App } = await import("@/App");
  return render(<App />);
}

describe("AuthGate device-flow stash (hosted mode)", () => {
  // Same warm-up as App.routes.test.tsx: ``@/App`` pulls in the whole
  // route tree, and paying that transform cost inside a hook (10s
  // hookTimeout) rather than the first ``it`` (5s testTimeout) keeps this
  // file from flaking under xdist load, where the first test otherwise
  // eats a cold-start tax the rest never pay.
  beforeAll(async () => {
    await import("@/App");
  });

  beforeEach(() => {
    sessionStorage.clear();
    getDevicePending.mockReset();
    getDevicePending.mockRejectedValue(new ApiError(404, "not found"));
    vi.mocked(api.getMe).mockClear();
  });

  it("stashes the code and bounces an anonymous visitor to /login", async () => {
    vi.mocked(api.getMe).mockRejectedValue(new ApiError(401, "Unauthorized"));
    await renderAt("/desktop/approve?code=ABCD-2345");
    await waitFor(() => expect(screen.getByRole("main")).toBeInTheDocument());
    expect(window.location.pathname).toBe("/login");
    expect(sessionStorage.getItem(STASH_KEY)).toBe("ABCD-2345");
  });

  it("does not stash anything for an anonymous visit to an ordinary route", async () => {
    vi.mocked(api.getMe).mockRejectedValue(new ApiError(401, "Unauthorized"));
    await renderAt("/pick");
    await waitFor(() => expect(screen.getByRole("main")).toBeInTheDocument());
    expect(window.location.pathname).toBe("/login");
    expect(sessionStorage.getItem(STASH_KEY)).toBeNull();
  });

  it("picks up a stashed code once signed in and lands on /desktop/approve", async () => {
    sessionStorage.setItem(STASH_KEY, "ABCD-2345");
    vi.mocked(api.getMe).mockResolvedValue({
      id: "u1",
      email: "m@thias.se",
      display_name: null,
      is_admin: false,
    });
    await renderAt("/");
    await waitFor(() =>
      expect(screen.getByText(/approve device/i)).toBeInTheDocument(),
    );
    expect(window.location.pathname).toBe("/desktop/approve");
    expect(window.location.search).toBe("?code=ABCD-2345");
    // Single-use: picking it up must consume it, or a later "/" visit
    // (e.g. after the operator navigates home) would re-bounce forever.
    expect(sessionStorage.getItem(STASH_KEY)).toBeNull();
  });

  it("leaves a signed-in visitor with no stash on the ordinary route", async () => {
    // This is the regression this whole file exists to catch: a hosted
    // user who never went near the device flow must land exactly where
    // they always did, not get redirected into /desktop/approve.
    vi.mocked(api.getMe).mockResolvedValue({
      id: "u1",
      email: "m@thias.se",
      display_name: null,
      is_admin: false,
    });
    await renderAt("/");
    await waitFor(() =>
      expect(screen.getByRole("navigation", { name: /global/i })).toBeInTheDocument(),
    );
    expect(window.location.pathname).not.toBe("/desktop/approve");
    expect(screen.queryByText(/approve device/i)).not.toBeInTheDocument();
  });
});
