/**
 * The Lab lives on /dev/* URLs, outside the ``/match/:matchId/`` URL
 * space -- and since #353 Tier 1 the server resolves match roots only
 * from the ``/api/matches/{id}/...`` prefix. The batch-promote panel
 * therefore carries its own match selector (fed from recent projects,
 * pinned in the ``?match=`` query param) and addresses every project
 * read plus the promote itself through the explicitly match-scoped API
 * calls. These tests pin the selector's defaulting + wiring.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { Lab } from "@/pages/Lab";

vi.mock("@/components/SweepsCard", () => ({ SweepsCard: () => null }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listLabFixtures: vi.fn().mockResolvedValue([]),
      getLastLabRun: vi.fn().mockRejectedValue(new Error("no run")),
      getRecentProjects: vi.fn().mockResolvedValue([
        {
          path: "/m/hfo",
          name: "HFO Masters 2026",
          last_opened_at: "2026-08-14T10:00:00Z",
          kind: "match",
          match_id: "m-hfo",
        },
        {
          path: "/m/vads",
          name: "VADS Easter Shoot",
          last_opened_at: "2026-08-13T10:00:00Z",
          kind: "match",
          match_id: "m-vads",
        },
        {
          path: "/m/legacy",
          name: "Old single-shooter",
          last_opened_at: "2026-08-01T10:00:00Z",
          kind: "legacy",
          match_id: null,
        },
      ]),
      listMatchShootersIn: vi.fn().mockResolvedValue({
        match_root: "/m/hfo",
        match_name: "HFO Masters 2026",
        shooters: [{ slug: "s_1", name: "Anna" }],
      }),
      getProjectIn: vi.fn().mockResolvedValue({
        name: "HFO Masters 2026",
        stages: [],
        shooter_token: "s1tok",
        selected_shooter_id: 123,
      }),
      getExportOverviewIn: vi.fn().mockResolvedValue({ stages: [] }),
    },
  };
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderLab(initialEntry = "/dev/legacy/lab") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="dev/legacy/lab" element={<Lab />} />
        <Route path="dev/legacy/lab/:slug" element={<Lab />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function openPanel() {
  await userEvent.click(
    screen.getByRole("button", { name: /promote all stages/i }),
  );
}

describe("Lab batch-promote match selector", () => {
  it("defaults to the most recent match and loads it via the match-scoped API", async () => {
    renderLab();
    await openPanel();
    await waitFor(() =>
      expect(api.listMatchShootersIn).toHaveBeenCalledWith("m-hfo"),
    );
    expect(api.getProjectIn).toHaveBeenCalledWith("m-hfo", "s_1");
    expect(api.getExportOverviewIn).toHaveBeenCalledWith("m-hfo", "s_1");
    // Selector lists only real matches (the legacy entry has no id).
    const select = screen.getByLabelText(/match/i);
    expect(select).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "HFO Masters 2026" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "VADS Easter Shoot" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: "Old single-shooter" }),
    ).not.toBeInTheDocument();
  });

  it("honours a ?match= id already in the URL", async () => {
    renderLab("/dev/legacy/lab?match=m-vads");
    await openPanel();
    await waitFor(() =>
      expect(api.listMatchShootersIn).toHaveBeenCalledWith("m-vads"),
    );
    expect(api.listMatchShootersIn).not.toHaveBeenCalledWith("m-hfo");
  });

  it("reloads through the newly selected match", async () => {
    renderLab();
    await openPanel();
    await waitFor(() =>
      expect(api.listMatchShootersIn).toHaveBeenCalledWith("m-hfo"),
    );
    await userEvent.selectOptions(screen.getByLabelText(/match/i), "m-vads");
    await waitFor(() =>
      expect(api.listMatchShootersIn).toHaveBeenCalledWith("m-vads"),
    );
  });
});
