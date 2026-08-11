/**
 * The device-flow pickup must not race /api/server/features (#719 final
 * review, finding 4).
 *
 * ``useDeploymentMode()`` initialises to "local" and flips only once
 * /api/server/features resolves. AuthGate's pickup used to be gated on
 * ``mode !== "local"``, which made the feature's only pickup window
 * depend on request-issue ordering: when /api/me answers first, the
 * ordinary route tree mounts, the catch-all redirect (then an async
 * LegacyMatchRedirect, since replaced by a synchronous Navigate) moves
 * off "/", and by the time the mode flips the pathname no
 * longer matches. The stashed code then survives to ambush a later visit
 * with a long-dead code.
 *
 * Own file, not a case in App.routes.hosted.test.tsx: the features cache
 * in src/lib/features.ts is module-level, so only the FIRST test in a
 * module registry can control how slowly the mode resolves. Sharing a
 * file with tests that already warmed that cache would make this one
 * pass for the wrong reason.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

/** How long /api/server/features lags /api/me here. Long enough that
 *  the catch-all's redirect (then LegacyMatchRedirect, since replaced by
 *  a synchronous Navigate) definitely lands first. */
const FEATURES_DELAY_MS = 300;

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
      // The slow one. Everything else below answers immediately, so the
      // ordering this test pins is the bad one: auth first, mode last.
      getServerFeatures: vi.fn().mockImplementation(
        () =>
          new Promise((resolve) => {
            setTimeout(() => resolve({ lab: false, mode: "hosted" }), FEATURES_DELAY_MS);
          }),
      ),
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
      getDevicePending: vi.fn().mockRejectedValue(new Error("not found")),
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

describe("AuthGate device-flow pickup vs. the features fetch", () => {
  beforeAll(async () => {
    await import("@/App");
  });

  it("picks the stash up when /api/me resolves before /api/server/features", async () => {
    sessionStorage.setItem(STASH_KEY, "ABCD-2345");
    window.history.pushState({}, "", "/");
    const { App } = await import("@/App");
    render(<App />);

    await waitFor(
      () => expect(window.location.pathname).toBe("/desktop/approve"),
      { timeout: FEATURES_DELAY_MS - 50 },
    );
    expect(window.location.search).toBe("?code=ABCD-2345");
    expect(sessionStorage.getItem(STASH_KEY)).toBeNull();
    await waitFor(() => expect(screen.getByText(/approve device/i)).toBeInTheDocument());
  });
});
