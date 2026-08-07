/**
 * Picker chrome after the RootLayout extraction (#550).
 *
 * Pick used to route outside every shell and hand-roll its own header,
 * including its own AccountChip mount. It nests under RootLayout now, so
 * exactly one account menu must be on the page. Unlike MatchShell, Pick
 * has no nav drawer, so it must keep relying on the global bar's account
 * chip on mobile rather than claiming its own (useShellOwnsMobileAccount
 * is MatchShell-only -- see shellChromeContext.tsx).
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/lib/auth";
import { ModeProvider } from "@/lib/mode";
import { ConfirmProvider } from "@/components/useConfirm";
import { RootLayout } from "@/components/layout/RootLayout";
import { Pick } from "@/pages/Pick";

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
      getServerFeatures: vi
        .fn()
        .mockResolvedValue({ lab: false, mode: "hosted" }),
      getHealth: vi.fn().mockResolvedValue({
        status: "ok",
        version: "1.2.3",
        bound: false,
        project_name: null,
        project_root: null,
        match_id: null,
        kind: null,
        default_shooter_slug: null,
        schema_version: null,
      }),
      getScoreboardIdentity: vi.fn().mockResolvedValue({
        shooter_id: 1,
        display_name: "Jane Shooter",
        division: "Production Optics",
        club: "Bromma",
        base_url: null,
      }),
      getRecentProjectsDetail: vi.fn().mockResolvedValue([]),
    },
  };
});

// Mutable so a single test can flip the breakpoint -- same pattern as
// RootLayout.test.tsx's `mobile` object.
const mobile = vi.hoisted(() => ({ value: false }));
vi.mock("@/lib/useIsMobile", () => ({ useIsMobile: () => mobile.value }));

function renderPick() {
  return render(
    <MemoryRouter initialEntries={["/pick"]}>
      <ModeProvider>
        <AuthProvider>
          <ConfirmProvider>
            <Routes>
              <Route element={<RootLayout />}>
                <Route path="pick" element={<Pick />} />
              </Route>
            </Routes>
          </ConfirmProvider>
        </AuthProvider>
      </ModeProvider>
    </MemoryRouter>,
  );
}

describe("Pick chrome (#550)", () => {
  beforeEach(() => {
    mobile.value = false;
  });

  it("mounts no account chip of its own", async () => {
    renderPick();
    await screen.findByText(/standby/i);
    expect(screen.getAllByTestId("account-chip")).toHaveLength(1);
  });

  it("keeps the standby strip", async () => {
    renderPick();
    expect(await screen.findByText(/standby/i)).toBeInTheDocument();
  });

  it("keeps the shooter identity pill", async () => {
    renderPick();
    expect(await screen.findByText("Jane Shooter")).toBeInTheDocument();
  });

  it("does not claim the mobile account menu -- the global bar still carries it on a phone", async () => {
    mobile.value = true;
    renderPick();
    await screen.findByText(/standby/i);
    expect(
      screen.getByRole("navigation", { name: /global/i }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("account-chip")).toBeInTheDocument();
  });
});
