import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { beforeAll, describe, expect, it, vi } from "vitest";

import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import type {
  CoachShot,
  CoachStageResponse,
  CoachVideoEntry,
  ShooterListEntry,
  StageStatus,
} from "@/lib/api";

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
      patchStageShotCoach: vi.fn(),
      videoStreamUrl: (_slug: string, path: string) => `http://localhost/${path}`,
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

function makeCoach(videos: CoachVideoEntry[], shots: CoachShot[] = []): CoachStageResponse {
  return { stage_number: 2, stage_name: "Steel Rush", beep_time: 5, version: 4, videos, shots };
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

function renderStage(
  path: string,
  shooters: ShooterListEntry[],
  opts: { videos: CoachVideoEntry[]; shots?: CoachShot[] },
) {
  vi.mocked(api.getStageCoach).mockResolvedValue(makeCoach(opts.videos, opts.shots ?? []));
  const ctx: MatchShellOutletContext = {
    project: null,
    health: null,
    shooters,
    refresh: vi.fn(),
    origin: null,
    capabilities: null,
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

const TWO_CAMS: CoachVideoEntry[] = [
  { path: "cam-primary.mp4", role: "primary", beep_in_clip: 5 },
  { path: "cam-b.mp4", role: "secondary", beep_in_clip: 12 },
];

function mainVideoSrcs(): string[] {
  // CamPicker thumbs are aria-hidden; the main player's video is not.
  return Array.from(document.querySelectorAll("video:not([aria-hidden])")).map(
    (v) => (v as HTMLVideoElement).src,
  );
}

describe("ResultsStage camera selection", () => {
  it("renders no picker for a single-camera run", async () => {
    renderStage("/match/m1/results/anna/2", [makeShooter("anna", "Anna", [[2, "audited"]])], {
      videos: [TWO_CAMS[0]],
    });
    await screen.findByText(/steel rush/i);
    expect(screen.queryByRole("group", { name: /cameras/i })).toBeNull();
  });

  it("renders the picker and swaps the player to the chosen camera", async () => {
    renderStage("/match/m1/results/anna/2", [makeShooter("anna", "Anna", [[2, "audited"]])], {
      videos: TWO_CAMS,
    });
    await screen.findByText(/steel rush/i);
    expect(mainVideoSrcs()).toEqual(["http://localhost/cam-primary.mp4"]);
    fireEvent.click(screen.getByRole("button", { name: /camera 2 of 2/i }));
    expect(mainVideoSrcs()).toEqual(["http://localhost/cam-b.mp4"]);
  });

  it("opens on the camera a moment link names via ?v=", async () => {
    renderStage(
      "/match/m1/results/anna/2?t=1.00&v=1",
      [makeShooter("anna", "Anna", [[2, "audited"]])],
      { videos: TWO_CAMS },
    );
    await screen.findByText(/steel rush/i);
    expect(mainVideoSrcs()).toEqual(["http://localhost/cam-b.mp4"]);
  });

  it("falls back to the first camera when no primary exists", async () => {
    renderStage("/match/m1/results/anna/2", [makeShooter("anna", "Anna", [[2, "audited"]])], {
      videos: [{ path: "cam-b.mp4", role: "secondary", beep_in_clip: 12 }],
    });
    await screen.findByText(/steel rush/i);
    expect(mainVideoSrcs()).toEqual(["http://localhost/cam-b.mp4"]);
  });
});
