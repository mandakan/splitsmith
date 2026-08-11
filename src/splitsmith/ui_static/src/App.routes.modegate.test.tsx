/**
 * AuthGate must hold the route tree on standby until the deployment mode
 * has genuinely resolved (#734).
 *
 * useDeploymentMode() starts at { mode: "local", resolved: false } and
 * flips once /api/server/features answers. Before this fix AuthGate only
 * gated on auth status, so the route tree mounted on the provisional
 * "local" default while the real mode was still in flight - any
 * mode-gated surface underneath could fire local-only requests against a
 * hosted server. Pick's own data fetch (getRecentProjectsDetail) is used
 * here as the observable proxy for "the tree mounted".
 *
 * Own file, not a case in App.routes.pickup.test.tsx or the other route
 * files: the features cache in src/lib/features.ts is module-level, so
 * only the first test in a module registry can control how slowly the
 * mode resolves. Sharing a file with tests that already warmed that
 * cache would make this one pass for the wrong reason.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** How long /api/server/features lags everything else. Long enough that
 *  the pre-fix tree would definitely have mounted and fired its fetch. */
const FEATURES_DELAY_MS = 200;

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
      // The slow one. Everything else below answers immediately.
      getServerFeatures: vi.fn().mockImplementation(
        () =>
          new Promise((resolve) => {
            setTimeout(() => resolve({ lab: false, mode: "local" }), FEATURES_DELAY_MS);
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

// jsdom does not implement matchMedia; useIsMobile (consulted by
// RootLayout, which /pick renders under) needs it to exist.
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

import { api } from "@/lib/api";

describe("AuthGate mode-resolution gate (#734)", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("holds the route tree on standby until /api/server/features resolves", async () => {
    window.history.pushState({}, "", "/pick");
    const { App } = await import("@/App");
    render(<App />);

    // While the mode is unresolved the tree must not mount: Pick's own
    // data fetch is the observable proxy for "the tree mounted".
    expect(await screen.findByRole("status", { name: /loading/i })).toBeInTheDocument();
    expect(api.getRecentProjectsDetail).not.toHaveBeenCalled();

    await waitFor(
      () => expect(api.getRecentProjectsDetail).toHaveBeenCalled(),
      { timeout: FEATURES_DELAY_MS + 1000 },
    );
    expect(screen.queryByRole("status", { name: /loading/i })).not.toBeInTheDocument();
  });
});
