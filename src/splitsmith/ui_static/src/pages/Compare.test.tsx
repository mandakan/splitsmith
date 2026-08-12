import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { beforeAll, describe, expect, it, vi } from "vitest";

import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { api, type CompareStageResponse } from "@/lib/api";

import { Compare } from "./Compare";

// vi.hoisted so the vi.mock factory (hoisted to the top of the file) can
// reference the bundle without a temporal-dead-zone error.
const bundle = vi.hoisted(() => ({
  stage_number: 2,
  stage_name: "Standards",
  shooters: [
    {
      slug: "a",
      name: "Fast Shooter",
      stage_time_seconds: 14.32,
      beep_offset_in_clip: 1.0,
      video_ref: "trimmed/a.mp4",
      shots: [
        {
          shot_number: 1,
          time_after_beep: 1.18,
          source: "detected",
          interval_class: null,
        },
      ],
    },
    {
      slug: "b",
      name: "Slow Shooter",
      stage_time_seconds: 15.08,
      beep_offset_in_clip: 1.2,
      video_ref: "trimmed/b.mp4",
      shots: [
        {
          shot_number: 1,
          time_after_beep: 1.31,
          source: "detected",
          interval_class: null,
        },
      ],
    },
  ],
}) as CompareStageResponse);

// One playable shooter (so the visible grid + leaderboard render) plus
// one audited-but-uncached shooter (shots present, no video_ref) so the
// UnfinishedShootersBanner's "Build trim cache" button is reachable.
const unfinishedBundle = vi.hoisted(() => ({
  stage_number: 2,
  stage_name: "Standards",
  shooters: [
    {
      slug: "a",
      name: "Fast Shooter",
      stage_time_seconds: 14.32,
      beep_offset_in_clip: 1.0,
      video_ref: "trimmed/a.mp4",
      shots: [
        {
          shot_number: 1,
          time_after_beep: 1.18,
          source: "detected",
          interval_class: null,
        },
      ],
    },
    {
      slug: "c",
      name: "Uncached Shooter",
      stage_time_seconds: null,
      beep_offset_in_clip: null,
      video_ref: null,
      shots: [
        {
          shot_number: 1,
          time_after_beep: 1.4,
          source: "detected",
          interval_class: null,
        },
      ],
    },
  ],
}) as CompareStageResponse);

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listMatchShooters: vi.fn().mockResolvedValue({ shooters: [] }),
      getProject: vi.fn(),
      getStageCompare: vi.fn().mockResolvedValue(bundle),
      shooterVideoStreamUrl: (slug: string, ref: string) =>
        `/stream/${slug}/${ref}`,
    },
  };
});

// jsdom has no media playback; stub so mounting <video> never throws.
beforeAll(() => {
  HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);
  HTMLMediaElement.prototype.pause = vi.fn();
});

function renderAt(path: string, routePattern: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={routePattern} element={<Compare />} />
      </Routes>
    </MemoryRouter>,
  );
}

// Mirrors Home.capabilities.test.tsx: a parent route element renders the
// Outlet with a real MatchShellOutletContext (the operator mount's
// actual shape) so Compare's `useOutletContext()` resolves the way it
// does under MatchShell, not the way it does under the plain <Route
// element={<Compare />}> wiring `renderAt` uses above.
function OutletCtx({ ctx }: { ctx: MatchShellOutletContext }) {
  return <Outlet context={ctx} />;
}

function renderCompareWithCapabilities(capabilities: MatchShellOutletContext["capabilities"]) {
  const ctx: MatchShellOutletContext = {
    project: null,
    health: null,
    shooters: [],
    refresh: () => {},
    origin: "desktop",
    capabilities,
  };
  return render(
    <MemoryRouter initialEntries={["/match/m1/compare/2"]}>
      <Routes>
        <Route path="match/:matchId" element={<OutletCtx ctx={ctx} />}>
          <Route path="compare/:stage" element={<Compare />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("Compare cockpit layout", () => {
  it("renders the leaderboard rail and transport dock on the operator route", async () => {
    renderAt("/match/m1/compare/2", "match/:matchId/compare/:stage");
    await waitFor(() =>
      expect(screen.getByTestId("leaderboard-rail")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("transport-dock")).toBeInTheDocument();
    // The old full-width ranking table is gone.
    expect(screen.queryByText("Ranking")).not.toBeInTheDocument();
    // Operator affordances present.
    expect(screen.getByRole("button", { name: "Audit" })).toBeInTheDocument();
  });

  it("hides operator affordances on the share route", async () => {
    renderAt("/share/tok123/compare/2", "share/:token/compare/:stage");
    await waitFor(() =>
      expect(screen.getByTestId("leaderboard-rail")).toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("button", { name: "Audit" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Export FCPXML/)).not.toBeInTheDocument();
  });
});

describe("Compare trim-rebuild capability gate (#756)", () => {
  it('shows "Build trim cache" on the operator route when capabilities include edit', async () => {
    vi.mocked(api.getStageCompare).mockResolvedValueOnce(unfinishedBundle);
    renderCompareWithCapabilities(["edit", "review", "share_manage"]);
    await waitFor(() =>
      expect(screen.getByTestId("leaderboard-rail")).toBeInTheDocument(),
    );
    expect(
      await screen.findByRole("button", { name: /build trim cache/i }),
    ).toBeInTheDocument();
  });

  it('hides "Build trim cache" on the operator route when capabilities lack edit', async () => {
    vi.mocked(api.getStageCompare).mockResolvedValueOnce(unfinishedBundle);
    renderCompareWithCapabilities(["review", "share_manage"]);
    await waitFor(() =>
      expect(screen.getByTestId("leaderboard-rail")).toBeInTheDocument(),
    );
    // The banner itself still renders (missing footage is real); only
    // the write affordance inside it is gated.
    expect(screen.getByText("Missing footage")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /build trim cache/i }),
    ).not.toBeInTheDocument();
  });
});
