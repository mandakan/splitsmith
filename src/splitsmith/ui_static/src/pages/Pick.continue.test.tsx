/**
 * The Continue card (UX PR 8): the most recently touched match with work
 * left, named by its next action, bound and opened at that stage.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, type RecentProjectDetail } from "@/lib/api";
import { AuthProvider } from "@/lib/auth";
import { ModeProvider } from "@/lib/mode";
import { ConfirmProvider } from "@/components/useConfirm";
import { Pick } from "@/pages/Pick";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getMe: vi.fn().mockResolvedValue({ id: "u1", email: "m@thias.se", display_name: null, is_admin: false }),
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "local" }),
      getHealth: vi.fn().mockResolvedValue({
        status: "ok", version: "1.2.3", bound: false, project_name: null, project_root: null,
        match_id: null, kind: null, default_shooter_slug: null, schema_version: null,
      }),
      getScoreboardIdentity: vi.fn().mockResolvedValue(null),
      getRecentProjectsDetail: vi.fn(),
      bindProject: vi.fn().mockResolvedValue({
        status: "ok", version: "1.2.3", bound: true, project_name: "Stockholm IPSC Open 2026",
        project_root: "/m/sthlm", match_id: "m-sthlm", kind: "match", default_shooter_slug: "s_ma", schema_version: 1,
      }),
    },
  };
});

vi.mock("@/lib/useIsMobile", () => ({ useIsMobile: () => false }));

function match(over: Partial<RecentProjectDetail>): RecentProjectDetail {
  return {
    path: "/m/sthlm",
    name: "Stockholm IPSC Open 2026",
    last_opened_at: "2026-09-01T10:00:00Z",
    kind: "match",
    match_id: "m-sthlm",
    shooter_count: 1,
    stage_count: 12,
    stages_audited: 4,
    video_count: 11,
    match_date: "2026-06-27",
    club: null,
    last_modified_at: "2026-09-13T10:00:00Z",
    status: "in_progress",
    manual: false,
    shooter_names: ["Mathias Axell"],
    origin: "local",
    next_step: { kind: "audit", shooter_slug: "s_ma", stage_number: 5, stage_name: "B5 Rear" },
    ...over,
  };
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="probe">{location.pathname}</div>;
}

function renderPick() {
  return render(
    <MemoryRouter initialEntries={["/pick"]}>
      <ModeProvider>
        <AuthProvider>
          <ConfirmProvider>
            <Routes>
              <Route path="pick" element={<Pick />} />
              <Route path="match/:matchId/*" element={<LocationProbe />} />
            </Routes>
          </ConfirmProvider>
        </AuthProvider>
      </ModeProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("Pick Continue card", () => {
  it("names the next audit stage and opens the match there", async () => {
    vi.mocked(api.getRecentProjectsDetail).mockResolvedValue([
      match({ path: "/m/older", name: "Older", match_id: "m-older", last_modified_at: "2026-08-01T00:00:00Z" }),
      match({}),
    ]);
    renderPick();
    const card = await screen.findByRole("region", { name: /continue/i });
    expect(card).toHaveTextContent("Stockholm IPSC Open 2026");
    expect(card).toHaveTextContent("Audit stage 05 B5 Rear");
    expect(card).toHaveTextContent("4 / 12 stages audited");
    const go = screen.getByRole("button", { name: /^audit stage 05$/i });
    await userEvent.click(go);
    await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("/match/m-sthlm/audit/s_ma/5"));
    expect(api.bindProject).toHaveBeenCalledWith("/m/sthlm", "Stockholm IPSC Open 2026");
  });

  it("makes New match the primary when nothing can continue", async () => {
    vi.mocked(api.getRecentProjectsDetail).mockResolvedValue([
      match({ status: "archived", next_step: null }),
    ]);
    renderPick();
    await screen.findByText("Stockholm IPSC Open 2026");
    expect(screen.queryByRole("region", { name: /continue/i })).toBeNull();
    expect(screen.getByRole("button", { name: /new match/i }).className).toMatch(/btn-primary/);
    // Archived rows offer Restore, and no row carries a delete control.
    expect(screen.getByRole("button", { name: /open stockholm/i })).toHaveTextContent("Restore");
    expect(screen.queryByRole("button", { name: /delete/i })).toBeNull();
  });

  it("points a match without footage at Footage", async () => {
    vi.mocked(api.getRecentProjectsDetail).mockResolvedValue([
      match({
        status: "awaiting_footage", video_count: 0, stages_audited: 0,
        next_step: { kind: "footage", shooter_slug: null, stage_number: null, stage_name: null },
      }),
    ]);
    renderPick();
    const card = await screen.findByRole("region", { name: /continue/i });
    expect(card).toHaveTextContent("Add footage");
    await userEvent.click(screen.getByRole("button", { name: /^add footage$/i }));
    await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("/match/m-sthlm/ingest"));
  });
});
