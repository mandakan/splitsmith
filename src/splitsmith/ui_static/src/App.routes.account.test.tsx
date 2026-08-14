/**
 * Route-resolution proof for /account (#867 Task 12).
 *
 * AccountChip has linked to /account since #867 Task 11, but nothing
 * proved the route existed until this task added it in App.tsx. This
 * renders the whole App (not just the <Account /> component in
 * isolation, which Account.test.tsx already covers) at the /account
 * path in hosted mode and confirms the page actually mounts under
 * RootLayout -- i.e. the chip's link resolves to something, not a
 * fallback redirect to /pick.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

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
      getDevicePending: vi.fn().mockRejectedValue(new Error("not found")),
      listDesktopTokens: vi.fn().mockResolvedValue({ tokens: [] }),
      createDesktopToken: vi.fn(),
      revokeDesktopToken: vi.fn(),
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

async function renderAt(path: string) {
  window.history.pushState({}, "", path);
  const { App } = await import("@/App");
  return render(<App />);
}

describe("/account route", () => {
  // #867 final review M10: same bump as App.routes.test.tsx's beforeAll.
  // This file is the third in the class paying the same route-tree
  // import cost in this hook; the default 10s hookTimeout is what
  // flaked under load once three files were competing for it. The
  // import itself is bounded work done once; raise the budget rather
  // than change what it does.
  beforeAll(async () => {
    await import("@/App");
  }, 30_000);

  it("mounts the Account page for a signed-in hosted visitor, not a redirect", async () => {
    await renderAt("/account");
    await waitFor(() => expect(vi.mocked(api.getMe)).toHaveBeenCalled());
    // Prove it landed on the real page (display-name field, desktop
    // tokens section) rather than bouncing to /pick.
    await waitFor(() => expect(screen.getByLabelText(/display name/i)).toBeInTheDocument());
    expect(await screen.findByText(/desktop sync tokens/i)).toBeInTheDocument();
    expect(window.location.pathname).toBe("/account");
  });
});
