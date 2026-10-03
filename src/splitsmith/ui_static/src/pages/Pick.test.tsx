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
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, type RecentProjectDetail } from "@/lib/api";
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
        access_tier: "full",
        features: ["create_match", "hosted_compute", "raw_upload", "share", "sync"],
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
    await screen.findByText(/no match open/i);
    expect(screen.getAllByTestId("account-chip")).toHaveLength(1);
  });

  it("keeps the context row", async () => {
    renderPick();
    expect(await screen.findByText(/no match open/i)).toBeInTheDocument();
  });

  it("keeps the shooter identity pill", async () => {
    renderPick();
    expect(await screen.findByText("Jane Shooter")).toBeInTheDocument();
  });

  it("does not claim the mobile account menu -- the global bar still carries it on a phone", async () => {
    mobile.value = true;
    renderPick();
    await screen.findByText(/no match open/i);
    expect(
      screen.getByRole("navigation", { name: /global/i }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("account-chip")).toBeInTheDocument();
  });
  it("hides filesystem affordances on hosted: paths, open-by-path, backup import", async () => {
    vi.mocked(api.getRecentProjectsDetail).mockResolvedValueOnce([
      {
        path: "/home/splitsmith/data/users/01K/projects/stockholm-ipsc-open-2026",
        name: "Stockholm IPSC Open 2026",
        last_opened_at: "2026-08-01T00:00:00Z",
        kind: "match",
        match_id: "stockholm-ipsc-open-2026-e986b13643",
        shooter_count: 1,
        stage_count: 12,
        stages_audited: 4,
        video_count: 11,
        match_date: null,
        club: null,
        last_modified_at: null,
        status: "in_progress",
        manual: false,
        shooter_names: ["Mathias Axell"],
        origin: "hosted",
        next_step: null,
      },
    ]);
    renderPick();
    await screen.findByText("Stockholm IPSC Open 2026");
    await waitFor(() => expect(api.getServerFeatures).toHaveBeenCalled());
    expect(screen.queryByText(/\/home\/splitsmith\/data/)).toBeNull();
    expect(screen.queryByText(/open by path/i)).toBeNull();
    expect(screen.queryByText(/import from backup/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /import backup/i })).toBeNull();
  });
});

const ALL = ["create_match", "hosted_compute", "raw_upload", "share", "sync"];

function me(features: string[]) {
  vi.mocked(api.getMe).mockResolvedValue({
    id: "u1",
    email: "m@thias.se",
    display_name: null,
    is_admin: false,
    access_tier: "sharing",
    features,
  });
}

function recent(over: Partial<RecentProjectDetail>): RecentProjectDetail {
  return {
    path: "/p/a",
    name: "Stockholm IPSC Open 2026",
    last_opened_at: "2026-08-01T00:00:00Z",
    kind: "match",
    match_id: "m-a",
    shooter_count: 1,
    stage_count: 12,
    stages_audited: 4,
    video_count: 11,
    match_date: null,
    club: null,
    last_modified_at: "2026-09-13T10:00:00Z",
    status: "in_progress",
    manual: false,
    shooter_names: ["Mathias Axell"],
    origin: "hosted",
    next_step: null,
    ...over,
  };
}

describe("Pick gating by account features", () => {
  beforeEach(() => {
    mobile.value = false;
    me(ALL);
    vi.mocked(api.getRecentProjectsDetail).mockResolvedValue([]);
  });

  it("offers New match to an account that can create matches", async () => {
    renderPick();
    expect(await screen.findByText(/no matches yet/i)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /new match/i })).toBeInTheDocument();
  });

  it("hides New match and points at the desktop app when the account cannot create", async () => {
    me(["share", "sync"]);
    renderPick();
    expect(await screen.findByText("Matches arrive here from the desktop app.")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Get the desktop app" });
    expect(link).toHaveAttribute("href", "https://splitsmith.app/#install");
    expect(screen.queryByText(/no matches yet/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /new match/i })).toBeNull();
  });

  it("keeps exactly one primary when New match is hidden and a match can continue", async () => {
    me(["share", "sync"]);
    vi.mocked(api.getRecentProjectsDetail).mockResolvedValue([
      recent({ next_step: { kind: "audit", shooter_slug: "s_ma", stage_number: 5, stage_name: "B5 Rear" } }),
    ]);
    const { container } = renderPick();
    await screen.findByRole("region", { name: /continue/i });
    expect(container.querySelectorAll(".btn-primary")).toHaveLength(1);
  });

  it("shows a refusal as its own line, not the raw JSON detail", async () => {
    vi.mocked(api.getRecentProjectsDetail).mockRejectedValue(
      new ApiError(403, JSON.stringify({ code: "account_disabled" }), { code: "account_disabled" }),
    );
    renderPick();
    expect(await screen.findByText("This account is disabled.")).toBeInTheDocument();
    expect(screen.queryByText(/account_disabled/)).toBeNull();
  });
});
