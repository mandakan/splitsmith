/**
 * Share compare route (#700 Task 4): `/share/:token/compare/:stage`
 * mounts Compare behind DesktopGate on desktop, and the desktop-only
 * signpost (linking back to `/share/:token/results`) on mobile.
 *
 * Mirrors App.routes.test.tsx / App.routes.hosted.test.tsx's renderAt +
 * module-level api-mock pattern. AuthGate bypasses gating entirely for
 * `/share/` paths (see App.tsx), so no getServerFeatures/getHealth
 * mocking is needed here - only what ShareShell and Compare themselves
 * fetch. getMe is mocked purely so the "not called on a share route"
 * test below has a spy to assert on; before the AuthProvider fix (see
 * lib/auth.tsx) this file produced a real 401 fetch and a
 * console.warn("auth: /api/me failed", ...) on every test in this
 * describe, since AuthGate bypassing the *gate* never stopped
 * AuthProvider from firing the *fetch*. useIsMobile is mocked with the
 * vi.hoisted toggle established by MatchShell.test.tsx /
 * RootLayout.test.tsx so the same route can be exercised at both
 * widths; a separate file (not a shared describe in
 * App.routes.test.tsx) because that mock is module-level and must not
 * leak into the other route tests, same rationale as
 * App.routes.hosted.test.tsx.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { api, type CompareStageResponse } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listMatchShooters: vi.fn(),
      getProject: vi.fn(),
      getStageCompare: vi.fn(),
      getMe: vi.fn().mockRejectedValue(new Error("getMe should not be called on a share route")),
    },
  };
});

const mobile = vi.hoisted(() => ({ value: false }));
vi.mock("@/lib/useIsMobile", () => ({
  useIsMobile: () => mobile.value,
}));

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

// One shooter with no resolvable trim - exercises the CompareEmptyState
// viewer-neutral copy and confirms no "Audit {name}" CTA leaks through.
const COMPARE_BUNDLE: CompareStageResponse = {
  stage_number: 2,
  stage_name: "Stage Two",
  shooters: [
    {
      slug: "ann",
      name: "Ann",
      video_ref: null,
      beep_offset_in_clip: null,
      duration_seconds: null,
      stage_time_seconds: null,
      shots: [],
    },
  ],
};

async function renderAt(path: string) {
  window.history.pushState({}, "", path);
  const { App } = await import("@/App");
  return render(<App />);
}

describe("share compare route (#700)", () => {
  // Same warm-up rationale as App.routes.test.tsx: pay the whole route
  // tree's transform cost in beforeAll's 10s hookTimeout, not the first
  // test's 5s testTimeout.
  beforeAll(async () => {
    await import("@/App");
  });

  beforeEach(() => {
    mobile.value = false;
    vi.mocked(api.listMatchShooters).mockResolvedValue({
      match_root: "/x",
      match_name: "m",
      shooters: [],
      origin: null,
    } as never);
    vi.mocked(api.getProject).mockRejectedValue(new Error("no default shooter"));
    vi.mocked(api.getStageCompare).mockResolvedValue(COMPARE_BUNDLE);
    vi.mocked(api.getMe).mockClear();
  });

  // Regression for the doomed-fetch bug this file's top comment
  // documents: AuthGate only bypasses the *gate*, so AuthProvider still
  // fired GET /api/me unconditionally on mount, producing a guaranteed
  // 401 (and a console.warn) on every share page load. AuthProvider now
  // recognizes the /share/ path at mount time and settles straight to
  // anonymous without ever calling getMe.
  it("does not call getMe on a share route", async () => {
    await renderAt("/share/tok123/compare/2");
    await screen.findByTestId("compare-page");
    expect(api.getMe).not.toHaveBeenCalled();
  });

  it("mounts Compare behind DesktopGate on desktop, with the Audit tab hidden", async () => {
    await renderAt("/share/tok123/compare/2");
    expect(await screen.findByTestId("compare-page")).toBeInTheDocument();
    // Operator-only tabs never render for an anonymous share viewer.
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Audit" }),
      ).not.toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("button", { name: "Coach" }),
    ).not.toBeInTheDocument();
    // Viewer-neutral empty-state copy, not audit instructions.
    expect(
      screen.getByText(
        /the match owner hasn't prepared comparison video for this stage yet/i,
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /audit ann/i }),
    ).not.toBeInTheDocument();
  });

  it("renders the desktop-only notice on mobile, linking to share results", async () => {
    mobile.value = true;
    await renderAt("/share/tok123/compare/2");
    expect(
      await screen.findByText(/this screen needs a desktop/i),
    ).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /^results$/i });
    expect(link).toHaveAttribute("href", "/share/tok123/results");
  });
});
