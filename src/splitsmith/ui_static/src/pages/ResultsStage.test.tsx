import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { beforeAll, describe, expect, it, vi } from "vitest";

import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import type { CoachStageResponse, ShooterListEntry, StageStatus } from "@/lib/api";

import { ResultsStage } from "@/pages/ResultsStage";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getStageCoach: vi.fn(),
      getProject: vi.fn().mockRejectedValue(new Error("no project")),
      getMatchCoachDistributions: vi.fn().mockRejectedValue(new Error("no dist")),
      videoStreamUrl: () => "http://localhost/video.mp4",
    },
  };
});

import { api } from "@/lib/api";

beforeAll(() => {
  // jsdom lacks both; ResultsStage measures the player box and the
  // ShotTicker probes prefers-reduced-motion.
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  window.matchMedia = ((query: string) => ({
    matches: true,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
});

function makeCoach(): CoachStageResponse {
  return {
    stage_number: 2,
    stage_name: "Steel Rush",
    beep_time: 5,
    videos: [{ path: "trimmed/stage2.mp4", role: "primary", beep_in_clip: 5 }],
    shots: [],
  };
}

function makeShooter(
  slug: string,
  name: string,
  statuses: [number, StageStatus][],
): ShooterListEntry {
  return {
    slug,
    name,
    selected_shooter_id: null,
    selected_competitor_id: null,
    stages_audited: statuses.filter(([, s]) => s === "audited").length,
    stages_total: statuses.length,
    video_count: 0,
    cameras: [],
    stages_missing_trim: 0,
    stage_statuses: statuses.map(([stage_number, status]) => ({ stage_number, status })),
  };
}

function Shell({ ctx }: { ctx: MatchShellOutletContext }) {
  return <Outlet context={ctx} />;
}

function renderStage(path: string, shooters: ShooterListEntry[]) {
  vi.mocked(api.getStageCoach).mockResolvedValue(makeCoach());
  const ctx: MatchShellOutletContext = {
    project: null,
    health: null,
    shooters,
    refresh: vi.fn(),
    origin: null,
  };
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Shell ctx={ctx} />}>
          <Route path="/match/:matchId/results/:slug/:stage" element={<ResultsStage />} />
          <Route path="/share/:token/results/:slug/:stage" element={<ResultsStage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

const MULTI = [
  makeShooter("anna", "Anna", [
    [1, "audited"],
    [2, "audited"],
  ]),
  makeShooter("bjorn", "Bjorn", [
    [1, "audited"],
    [2, "ready"],
  ]),
];
const SOLO = [makeShooter("anna", "Anna", [[2, "audited"]])];

describe("ResultsStage back link", () => {
  it("links back to the share overview from a share stage URL", async () => {
    renderStage("/share/tok123/results/anna/2", MULTI);
    const back = await screen.findByRole("link", { name: /all stages/i });
    expect(back).toHaveAttribute("href", "/share/tok123/results");
  });

  it("links back to the match overview on the owner surface", async () => {
    renderStage("/match/m1/results/anna/2", MULTI);
    const back = await screen.findByRole("link", { name: /all stages/i });
    expect(back).toHaveAttribute("href", "/match/m1/results");
  });
});

describe("ResultsStage shooter switcher", () => {
  it("renders a select on multi-shooter matches, disabling shooters without an audited take", async () => {
    renderStage("/share/tok123/results/anna/2", MULTI);
    const select = await screen.findByRole("combobox", { name: /shooter/i });
    expect(select).toHaveValue("anna");
    const bjorn = screen.getByRole("option", { name: "Bjorn" }) as HTMLOptionElement;
    expect(bjorn.disabled).toBe(true);
    const anna = screen.getByRole("option", { name: "Anna" }) as HTMLOptionElement;
    expect(anna.disabled).toBe(false);
  });

  it("renders plain text, no select, for a single shooter", async () => {
    renderStage("/match/m1/results/anna/2", SOLO);
    await waitFor(() => {
      expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    });
    expect(screen.getByText("Anna")).toBeInTheDocument();
  });
});
