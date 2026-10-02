import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { api, type CompareStageResponse, type MatchProject } from "@/lib/api";

import { Compare } from "./Compare";

function shooter(slug: string, video: boolean) {
  return {
    slug,
    name: slug.toUpperCase(),
    stage_time_seconds: 10,
    beep_offset_in_clip: video ? 1.0 : null,
    video_ref: video ? `trimmed/${slug}.mp4` : null,
    shots: [{ shot_number: 1, time_after_beep: 1.2, source: "detected", interval_class: null }],
  };
}

// Stage 2 and 4 have two shooters on video; stage 3 only one.
const bundles: Record<number, CompareStageResponse> = {
  2: { stage_number: 2, stage_name: "Two", shooters: [shooter("a", true), shooter("b", true)] },
  3: { stage_number: 3, stage_name: "Three", shooters: [shooter("a", false), shooter("b", true)] },
  4: { stage_number: 4, stage_name: "Four", shooters: [shooter("a", true), shooter("b", true)] },
} as unknown as Record<number, CompareStageResponse>;

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listMatchShooters: vi.fn().mockResolvedValue({ shooters: [{ slug: "a" }] }),
      getProject: vi.fn(),
      getStageCompare: vi.fn(),
      getStageCoach: vi.fn().mockResolvedValue(null),
      shooterVideoStreamUrl: (slug: string, ref: string) => `/stream/${slug}/${ref}`,
    },
  };
});

const play = vi.fn().mockResolvedValue(undefined);
beforeAll(() => {
  HTMLMediaElement.prototype.play = play;
  HTMLMediaElement.prototype.pause = vi.fn();
  Object.defineProperty(HTMLMediaElement.prototype, "readyState", { configurable: true, get: () => 4 });
  vi.mocked(api.getProject).mockResolvedValue({
    stages: [2, 3, 4].map((n) => ({ stage_number: n })),
  } as unknown as MatchProject);
  vi.mocked(api.getStageCompare).mockImplementation((n: number) => Promise.resolve(bundles[n]));
});

function Where() {
  const loc = useLocation();
  return <output data-testid="where">{loc.pathname + loc.search}</output>;
}

function renderAt(entry: { pathname: string; search: string; state?: unknown }) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route
          path="/match/:matchId/compare/:stage"
          element={
            <>
              <Compare />
              <Where />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Compare play all", () => {
  it("starts the grid, and at the end of the stage moves on, skipping a stage with one shooter on video", async () => {
    renderAt({ pathname: "/match/m1/compare/2", search: "?play=all", state: { autoplay: true } });
    await screen.findByText("Two");
    expect(screen.getByRole("button", { name: "Play all stages: on" })).toBeInTheDocument();
    await waitFor(() => expect(play).toHaveBeenCalled());

    const audio = document.querySelector('video[src="/stream/a/trimmed/a.mp4"]') as HTMLVideoElement;
    fireEvent(audio, new Event("ended"));

    await screen.findByText("Four");
    expect(screen.getByTestId("where")).toHaveTextContent("/match/m1/compare/4?play=all");
  });

  it("a cold play-all link waits for Play and never skips", async () => {
    play.mockClear();
    renderAt({ pathname: "/match/m1/compare/3", search: "?play=all" });
    await screen.findByText("Three");
    expect(play).not.toHaveBeenCalled();
    expect(screen.getByTestId("where")).toHaveTextContent("/match/m1/compare/3?play=all");
  });
});
