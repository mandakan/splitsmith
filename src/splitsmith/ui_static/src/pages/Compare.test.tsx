import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeAll, describe, expect, it, vi } from "vitest";

import type { CompareStageResponse } from "@/lib/api";

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
