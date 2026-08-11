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
      // The device-flow surface 404s in local mode; the approve page is
      // only reachable here by planting a stash by hand (see the
      // device-flow describe block below).
      getDevicePending: vi.fn().mockRejectedValue(new Error("not found")),
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
  it("renders AppShell chrome on an AppShell surface (/_design)", async () => {
    await renderAt("/_design");
    await waitFor(() =>
      expect(
        screen.getByRole("navigation", { name: /global/i }),
      ).toBeInTheDocument(),
    );
    // AppShell-only markup: the sidebar's "Design system" nav link. Pick
    // also has global chrome, so that assertion alone can't tell the two
    // shells apart -- this is the assertion that actually pins AppShell.
    expect(
      screen.getByRole("link", { name: /design system/i }),
    ).toBeInTheDocument();
  });

  /**
   * The device-flow pickup in AuthGate is deliberately NOT gated on
   * deployment mode (#719 final review, finding 4). useDeploymentMode()
   * starts at "local" and flips async, so a mode check there is a race
   * against /api/me: lose it and the ordinary tree mounts, navigates off
   * "/", and the one pickup window this feature has is gone.
   *
   * Dropping the check is only safe because a local install cannot hold
   * a stash in the first place -- that premise is what these two tests
   * pin, rather than leaving it as an argument in a comment.
   */
  it("never stashes a device code in local mode", async () => {
    // AuthGate's stash write lives on the hosted anonymous bounce, which
    // local mode never takes: it returns children before reaching it,
    // whatever /api/me did. So even the one URL that carries a code
    // leaves sessionStorage empty here.
    sessionStorage.clear();
    vi.mocked(api.getMe).mockRejectedValueOnce(new ApiError(401, "Unauthorized"));
    await renderAt("/desktop/approve?code=ABCD-2345");
    await waitFor(() => expect(screen.getByText(/approve device/i)).toBeInTheDocument());
    expect(sessionStorage.getItem("splitsmith.deviceApproveCode")).toBeNull();
    expect(window.location.pathname).toBe("/desktop/approve");
  });

  it("leaves a local-mode visitor with no stash on the ordinary route", async () => {
    // The everyday local case: "/" behaves exactly as it always did, no
    // pickup redirect anywhere in sight.
    sessionStorage.clear();
    await renderAt("/");
    await waitFor(() =>
      expect(screen.getByRole("navigation", { name: /global/i })).toBeInTheDocument(),
    );
    expect(window.location.pathname).not.toBe("/desktop/approve");
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
