/**
 * Opening a match from the picker while the global mode is Developer
 * must return to dev land -- the Lab, with the chosen match pinned as
 * ?match= -- not dump the operator into match mode. Match-mode picks
 * keep the classic behaviour (navigate to the match home).
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
      getMe: vi.fn().mockResolvedValue({
        id: "u1",
        email: "m@thias.se",
        display_name: null,
        is_admin: false,
      }),
      getServerFeatures: vi.fn().mockResolvedValue({ lab: true, mode: "local" }),
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
      getScoreboardIdentity: vi.fn().mockResolvedValue(null),
      getRecentProjectsDetail: vi.fn().mockResolvedValue([
        {
          path: "/m/hfo",
          name: "HFO Masters 2026",
          last_opened_at: "2026-08-14T10:00:00Z",
          kind: "match",
          match_id: "m-hfo",
          shooter_count: 1,
          stage_count: 12,
          stages_audited: 12,
          video_count: 12,
          match_date: null,
          club: null,
          last_modified_at: null,
          status: "in_progress",
          manual: false,
          shooter_names: ["Mathias"],
          origin: "local",
        } satisfies RecentProjectDetail,
      ]),
      bindProject: vi.fn().mockResolvedValue({
        status: "ok",
        version: "1.2.3",
        bound: true,
        project_name: "HFO Masters 2026",
        project_root: "/m/hfo",
        match_id: "m-hfo",
        kind: "match",
        default_shooter_slug: "s_1",
        schema_version: 1,
      }),
    },
  };
});

vi.mock("@/lib/useIsMobile", () => ({ useIsMobile: () => false }));

afterEach(() => {
  localStorage.removeItem("splitsmith.mode");
  vi.clearAllMocks();
});

function LocationProbe({ id }: { id: string }) {
  const location = useLocation();
  return <div data-testid={id}>{location.pathname + location.search}</div>;
}

function renderPick() {
  return render(
    <MemoryRouter initialEntries={["/pick"]}>
      <ModeProvider>
        <AuthProvider>
          <ConfirmProvider>
            <Routes>
              <Route path="pick" element={<Pick />} />
              <Route
                path="dev/corpus"
                element={<LocationProbe id="lab-probe" />}
              />
              <Route
                path="match/:matchId"
                element={<LocationProbe id="match-probe" />}
              />
            </Routes>
          </ConfirmProvider>
        </AuthProvider>
      </ModeProvider>
    </MemoryRouter>,
  );
}

describe("Pick in developer mode", () => {
  it("returns to the Lab with the picked match pinned", async () => {
    localStorage.setItem("splitsmith.mode", "developer");
    renderPick();
    const rows = await screen.findAllByRole("button", {
      name: /open hfo masters 2026/i,
    });
    await userEvent.click(rows[0]);
    await waitFor(() =>
      expect(screen.getByTestId("lab-probe")).toHaveTextContent(
        "/dev/corpus?match=m-hfo",
      ),
    );
    expect(api.bindProject).toHaveBeenCalledWith("/m/hfo", "HFO Masters 2026");
  });

  it("keeps the match-home destination in match mode", async () => {
    localStorage.setItem("splitsmith.mode", "match");
    renderPick();
    const rows = await screen.findAllByRole("button", {
      name: /open hfo masters 2026/i,
    });
    await userEvent.click(rows[0]);
    await waitFor(() =>
      expect(screen.getByTestId("match-probe")).toHaveTextContent("/match/m-hfo/"),
    );
  });
});
