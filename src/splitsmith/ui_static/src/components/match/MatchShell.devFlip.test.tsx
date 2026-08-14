/**
 * Flipping the mode switch to Developer from a match-scoped URL must
 * carry the match along: /match/:matchId/... -> /dev/corpus?match=:id.
 * Without it, dev mode falls back to inferring a match from recents
 * order, which silently disagrees with the match the operator actually
 * chose (the "defaults to blacksmith instead of hfo" report).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type Job, type MatchProject, type ServerHealth } from "@/lib/api";
import { AuthProvider } from "@/lib/auth";
import { ModeProvider, useMode } from "@/lib/mode";
import { MatchShell } from "@/components/match/MatchShell";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getHealth: vi.fn(),
      getScoreboardIdentity: vi.fn(),
      getServerFeatures: vi.fn(),
      getMe: vi.fn(),
      listMatchShooters: vi.fn(),
      getProject: vi.fn(),
      getBeepQueue: vi.fn(),
      getTriageSummary: vi.fn(),
      listJobs: vi.fn(),
    },
  };
});

vi.mock("@/lib/useIsMobile", () => ({ useIsMobile: () => false }));

const HEALTH: ServerHealth = {
  status: "ok",
  version: "0.0.0-test",
  bound: false,
  project_name: "bromma-2026",
  project_root: "/root/bromma-2026",
  match_id: "m1",
  kind: "match",
  default_shooter_slug: "mathias",
  schema_version: 1,
};

beforeEach(() => {
  localStorage.removeItem("splitsmith.mode");
  vi.mocked(api.getHealth).mockResolvedValue(HEALTH);
  vi.mocked(api.getScoreboardIdentity).mockResolvedValue(null);
  vi.mocked(api.getServerFeatures).mockResolvedValue({ lab: true, mode: "local" });
  vi.mocked(api.getMe).mockResolvedValue({
    id: "local",
    email: "local@localhost",
    display_name: null,
    is_admin: false,
  });
  vi.mocked(api.listMatchShooters).mockResolvedValue({
    match_root: "/root",
    match_name: "Bromma Classic 2026",
    shooters: [],
    origin: "local",
    capabilities: ["edit", "review"],
  } as never);
  vi.mocked(api.getProject).mockResolvedValue({ stages: [] } as unknown as MatchProject);
  vi.mocked(api.getBeepQueue).mockResolvedValue({
    total_items: 0,
    pending_count: 0,
    confirmed_count: 0,
    stages: [],
    origin: "local",
    capabilities: ["edit", "review"],
  } as never);
  vi.mocked(api.getTriageSummary).mockResolvedValue({ flagged_count: 0 });
  vi.mocked(api.listJobs).mockResolvedValue([] as Job[]);
});

function FlipButton() {
  const { setMode } = useMode();
  return (
    <div>
      match page
      <button type="button" onClick={() => setMode("developer")}>
        Flip to developer
      </button>
    </div>
  );
}

function DevProbe() {
  const location = useLocation();
  return <div data-testid="dev-probe">{location.pathname + location.search}</div>;
}

describe("MatchShell dev flip", () => {
  it("carries the URL match into /dev/corpus?match=", async () => {
    render(
      <ModeProvider>
        <AuthProvider>
          <MemoryRouter initialEntries={["/match/m1/"]}>
            <Routes>
              <Route path="match/:matchId" element={<MatchShell />}>
                <Route index element={<FlipButton />} />
              </Route>
              <Route path="dev/corpus" element={<DevProbe />} />
            </Routes>
          </MemoryRouter>
        </AuthProvider>
      </ModeProvider>,
    );
    await screen.findByText("match page");
    await userEvent.click(
      screen.getByRole("button", { name: "Flip to developer" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("dev-probe")).toHaveTextContent(
        "/dev/corpus?match=m1",
      ),
    );
  });
});
