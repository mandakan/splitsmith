import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { CoachShot, CoachStageResponse } from "@/lib/api";

import { Coach } from "@/pages/Coach";

/** #844: the desktop Coach page is the other caller of the shot PATCH.
 *  Like ResultsStage it must address shots by their stable id, and must
 *  guard the positional fallback with the version its *latest* coach
 *  response carried - not the one the page first loaded. */

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getProject: vi.fn(),
      getStageCoach: vi.fn(),
      getMatchCoachDistributions: vi.fn().mockResolvedValue(null),
      patchStageShotCoach: vi.fn(),
      videoStreamUrl: () => "http://localhost/video.mp4",
    },
  };
});

import { api } from "@/lib/api";

beforeAll(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  // jsdom implements neither; Coach scrolls the active row into view.
  Element.prototype.scrollIntoView = () => {};
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
});

function makeShot(n: number, id: string | null): CoachShot {
  return {
    id,
    shot_number: n,
    ms_after_beep: n * 1000,
    time_from_beep: n,
    time_absolute: 5 + n,
    split: 0.3,
    interval_class: "split",
    interval_class_source: "auto",
    improvement_flag: false,
    coaching_note: null,
    stale: false,
    reload_hint: false,
  };
}

function makeCoach(shots: CoachShot[], version = 4): CoachStageResponse {
  return {
    stage_number: 1,
    stage_name: "Stage One",
    beep_time: 5,
    version,
    videos: [{ path: "trimmed/stage1.mp4", role: "primary", beep_in_clip: 5 }],
    shots,
  };
}

function renderCoachStage(shots: CoachShot[]) {
  vi.mocked(api.getProject).mockResolvedValue({
    name: "M",
    competitor_name: "Anna",
    stages: [{ stage_number: 1, stage_name: "Stage One", time_seconds: 30 }],
  } as unknown as Awaited<ReturnType<typeof api.getProject>>);
  vi.mocked(api.getStageCoach).mockResolvedValue(makeCoach(shots));
  return render(
    <MemoryRouter initialEntries={["/match/m1/coach/anna/1"]}>
      <Routes>
        <Route path="/match/:matchId/coach/:slug/:stage" element={<Coach />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Coach stage shot patch", () => {
  beforeEach(() => {
    vi.mocked(api.patchStageShotCoach).mockReset();
  });

  it("passes the shot itself, so the call can address the by-id route", async () => {
    const shot = makeShot(1, "cand-7");
    renderCoachStage([shot]);
    vi.mocked(api.patchStageShotCoach).mockResolvedValue(makeCoach([shot], 5));

    fireEvent.click(await screen.findByRole("button", { name: "Movement" }));

    await waitFor(() => {
      expect(api.patchStageShotCoach).toHaveBeenCalledWith(
        "anna",
        1,
        expect.objectContaining({ id: "cand-7", shot_number: 1 }),
        { interval_class: "movement", interval_class_source: "manual" },
        4,
      );
    });
  });

  it("guards a second patch with the version the first patch returned", async () => {
    const shot = makeShot(1, null);
    renderCoachStage([shot]);
    vi.mocked(api.patchStageShotCoach).mockResolvedValue(makeCoach([shot], 5));

    fireEvent.click(await screen.findByRole("button", { name: "Movement" }));
    await waitFor(() => {
      expect(api.patchStageShotCoach).toHaveBeenCalledTimes(1);
    });

    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    await waitFor(() => {
      expect(api.patchStageShotCoach).toHaveBeenCalledTimes(2);
    });
    expect(vi.mocked(api.patchStageShotCoach).mock.calls[1][4]).toBe(5);
  });
});
