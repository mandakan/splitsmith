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
        shooters: [
          { slug: "s_1", name: "Anna" },
          { slug: "s_2", name: "Bertil" },
        ],
      }),
      getProjectIn: vi.fn().mockImplementation((_mid: string, slug: string) =>
        Promise.resolve({
          name: "HFO Masters 2026",
          stages: [
            {
              stage_number: 1,
              stage_name: "B50",
              placeholder: false,
              skipped: false,
              videos: [
                {
                  path: "raw/v.mp4",
                  role: "primary",
                  beep_time: 1.5,
                  camera_mount: "head",
                },
              ],
            },
          ],
          shooter_token: slug === "s_1" ? "s1tok" : "s2tok",
          selected_shooter_id: slug === "s_1" ? 123 : 456,
        }),
      ),
      getExportOverviewIn: vi.fn().mockResolvedValue({
        stages: [{ stage_number: 1, audit_path: "/m/hfo/audit/stage1.json" }],
      }),
      promoteFixtureIn: vi.fn().mockResolvedValue({
        slug: "stage-shots-hfo-masters-2026-stage1-s1tok",
        audit_path: "/fixtures/stage-shots-hfo-masters-2026-stage1-s1tok.json",
      }),
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

  it("promotes every shooter's run per stage, each with its own slugs", async () => {
    renderLab();
    await openPanel();
    const promoteBtn = await screen.findByRole("button", {
      name: /promote selected/i,
    });
    await userEvent.click(promoteBtn);
    await waitFor(() =>
      expect(api.promoteFixtureIn).toHaveBeenCalledWith("m-hfo", {
        stage_number: 1,
        slug: "stage-shots-hfo-masters-2026-stage1-s1tok",
        shooter_slug: "s_1",
        overwrite: false,
      }),
    );
    expect(api.promoteFixtureIn).toHaveBeenCalledWith("m-hfo", {
      stage_number: 1,
      slug: "stage-shots-hfo-masters-2026-stage1-s2tok",
      shooter_slug: "s_2",
      overwrite: false,
    });
    expect(api.promoteFixtureIn).toHaveBeenCalledTimes(2);
  });

  it("lists shooters all-selected and unchecking one excludes their rows", async () => {
    renderLab();
    await openPanel();
    const bertil = await screen.findByRole("checkbox", { name: "Bertil" });
    expect(bertil).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Anna" })).toBeChecked();
    await userEvent.click(bertil);
    await userEvent.click(
      screen.getByRole("button", { name: /promote selected/i }),
    );
    await waitFor(() =>
      expect(api.promoteFixtureIn).toHaveBeenCalledWith(
        "m-hfo",
        expect.objectContaining({ shooter_slug: "s_1" }),
      ),
    );
    expect(api.promoteFixtureIn).toHaveBeenCalledTimes(1);
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
