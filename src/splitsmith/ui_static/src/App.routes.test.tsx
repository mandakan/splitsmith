/**
 * Route-tree shape after the RootLayout extraction (#550).
 *
 * Cheap structural assertions that would have caught the two things most
 * likely to go wrong in the restructure: a surface accidentally left
 * outside RootLayout (so it loses global chrome), and the login / share
 * surfaces accidentally pulled inside it (so an anonymous visitor sees an
 * account menu).
 */
import { render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getMe: vi.fn().mockResolvedValue({
        id: "local",
        email: "local@splitsmith",
        display_name: null,
        is_admin: false,
      }),
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "local" }),
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
    },
  };
});

// jsdom does not implement matchMedia; useIsMobile (consulted by
// RootLayout) needs it to exist. Stub it desktop-sized rather than
// mocking the hook, since /pick's own layout also reads matchMedia
// indirectly via useDeploymentMode-adjacent code paths.
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

async function renderAt(path: string) {
  window.history.pushState({}, "", path);
  const { App } = await import("@/App");
  return render(<App />);
}

describe("route tree", () => {
  // ``@/App`` pulls in the whole route tree -- every shell, every page.
  // The first ``import("@/App")`` in this file pays esbuild's one-time
  // transform cost for that entire graph (measured standalone: ~2.3s on
  // this host, and it competes with every other test file's own
  // transform work for the same shared pipeline). Paying that cost here,
  // in a hook with vitest's default 10s hookTimeout, keeps it off the
  // first ``it``'s 5s testTimeout budget -- otherwise whichever test
  // happens to run first eats a cold-start tax the other two never pay
  // (their own ``renderAt`` calls hit the now-warm module cache), and
  // under enough parallel load that tax alone exceeds 5s (#550 review).
  beforeAll(async () => {
    await import("@/App");
  });

  it("renders global chrome on the picker", async () => {
    await renderAt("/pick");
    await waitFor(() =>
      expect(
        screen.getByRole("navigation", { name: /global/i }),
      ).toBeInTheDocument(),
    );
  });

  it("renders global chrome on admin/workers", async () => {
    await renderAt("/admin/workers");
    await waitFor(() =>
      expect(
        screen.getByRole("navigation", { name: /global/i }),
      ).toBeInTheDocument(),
    );
  });

  // AppShell is the one surface whose own header used to carry a second
  // brand mark once GlobalBar started spanning full width above it
  // (#550 review finding 2). Global chrome coverage previously stopped at
  // Pick, admin/workers and Login -- none of which mount AppShell -- so
  // the duplicate brand shipped with a green suite.
  it("renders global chrome on an AppShell surface (/_design)", async () => {
    await renderAt("/_design");
    await waitFor(() =>
      expect(
        screen.getByRole("navigation", { name: /global/i }),
      ).toBeInTheDocument(),
    );
  });

  it("does not render global chrome on the login surface", async () => {
    // Login itself redirects to "/" once useAuth's status is "authed"
    // (see src/pages/Login.tsx), and the module-level getMe mock above
    // always resolves -- which is right for the other two tests, where
    // AuthGate must let a local-mode visitor straight through. Force this
    // one resolve to a 401 so status lands on "anon" and Login actually
    // renders instead of bouncing away before the assertion below runs.
    vi.mocked(api.getMe).mockRejectedValueOnce(new ApiError(401, "Unauthorized"));
    await renderAt("/login");
    await waitFor(() => expect(screen.getByRole("main")).toBeInTheDocument());
    expect(
      screen.queryByRole("navigation", { name: /global/i }),
    ).not.toBeInTheDocument();
  });
});
