import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { CoachShot, CoachStageResponse, StageEvent } from "@/lib/api";

import { Breakdown } from "@/pages/Breakdown";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getProject: vi.fn(),
      getStageCoach: vi.fn(),
      getMatchCoachDistributions: vi.fn().mockResolvedValue(null),
      getStagePeaks: vi.fn().mockResolvedValue({
        duration: 20,
        sample_rate: 8000,
        bins: 4,
        peaks: [0.1, 0.5, 0.2, 0.1],
        beep_time: null,
        trimmed: true,
      }),
      patchStageShotCoach: vi.fn(),
      putStageEvents: vi.fn(),
      getScrubSettings: vi.fn().mockResolvedValue({ full_res_scrub: false }),
      setScrubSettings: vi.fn().mockResolvedValue({ full_res_scrub: true }),
      videoStreamUrl: (_slug: string, path: string, kind = "auto") => `http://localhost/${kind}/${path}`,
    },
  };
});

vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return { ...actual, useDeploymentMode: () => ({ mode: "local", resolved: true }) };
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
  Element.prototype.scrollIntoView = () => {};
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
});

function shot(n: number): CoachShot {
  return {
    id: `c${n}`,
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

function coach(events: StageEvent[], version = 4): CoachStageResponse {
  return {
    stage_number: 2,
    stage_name: "Stage Two",
    beep_time: 5,
    version,
    videos: [{ path: "trimmed/stage2.mp4", role: "primary", beep_in_clip: 5, kind: "trim" }],
    shots: [shot(1), shot(2), shot(3)],
    events,
    _version: "aaaaaaaaaaaaaaaa",
  };
}

const PROJECT = {
  name: "M",
  competitor_name: "Anna",
  origin: "local",
  capabilities: ["edit", "review"],
  stages: [
    { stage_number: 1, stage_name: "Stage One", time_seconds: 0, skipped: false },
    { stage_number: 2, stage_name: "Stage Two", time_seconds: 16.2, skipped: false },
    { stage_number: 3, stage_name: "Stage Three", time_seconds: 20, skipped: false },
  ],
} as never;

function Where() {
  return <span data-testid="where">{useLocation().pathname}</span>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/match/:matchId/breakdown/:slug" element={<Breakdown />} />
        <Route path="/match/:matchId/breakdown/:slug/:stage" element={<><Breakdown /><Where /></>} />
      </Routes>
    </MemoryRouter>,
  );
}

const EVENTS: StageEvent[] = [
  { id: "evt-1", kind: "movement", start: 1.2, end: 2.6, source: "manual" },
  { id: "evt-2", kind: "reload", start: 2.7, end: 4.1, source: "auto" },
];

describe("Breakdown", () => {
  beforeEach(() => {
    vi.mocked(api.getProject).mockResolvedValue(PROJECT);
    vi.mocked(api.getStageCoach).mockReset();
    vi.mocked(api.patchStageShotCoach).mockReset();
    vi.mocked(api.putStageEvents).mockReset();
    HTMLElement.prototype.setPointerCapture = vi.fn();
    HTMLElement.prototype.hasPointerCapture = vi.fn(() => true);
    HTMLElement.prototype.releasePointerCapture = vi.fn();
  });

  it("the bare shooter route lands on the first stage with a time", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(coach(EVENTS));
    renderAt("/match/m1/breakdown/anna");
    expect(await screen.findByTestId("where")).toHaveTextContent("/match/m1/breakdown/anna/2");
  });

  it("lays out the viewer, the inspector with the active shot, and the band with Audio and the lanes", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(coach(EVENTS));
    const { container } = renderAt("/match/m1/breakdown/anna/2");
    expect(await screen.findByTestId("event-evt-1")).toBeInTheDocument();
    expect(screen.getByTestId("breakdown-workspace")).toBeInTheDocument();
    expect(container.querySelector("video")).not.toBeNull();
    // One confirmed region and one proposal: the chip counts the confirmed.
    expect(screen.getByText("1 region")).toBeInTheDocument();
    expect(screen.getByText("1 proposed")).toBeInTheDocument();
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).getByRole("region", { name: "Shot 1" })).toBeInTheDocument();
    expect(within(inspector).getByRole("group", { name: "Interval class" })).toBeInTheDocument();
    // Notes and flags are Coach's review metadata, not Breakdown's.
    expect(screen.queryByRole("textbox", { name: "Coaching note" })).toBeNull();
    expect(within(inspector).getByRole("region", { name: "Shots" })).toBeInTheDocument();
    expect(screen.getByText("Audio")).toBeInTheDocument();
    expect(screen.getAllByText("Movement").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "Review in Coach" })).toHaveAttribute("href", "/match/m1/coach/anna/2");
    expect(screen.getByRole("link", { name: "Previous stage" })).toHaveAttribute("href", "/match/m1/breakdown/anna/1");
    expect(screen.getByRole("link", { name: "Next stage" })).toHaveAttribute("href", "/match/m1/breakdown/anna/3");
  });

  it("a class click writes the active shot's interval as a manual override with the coach version", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(coach(EVENTS));
    vi.mocked(api.patchStageShotCoach).mockResolvedValue(coach(EVENTS, 5));
    renderAt("/match/m1/breakdown/anna/2");
    const inspector = await screen.findByRole("complementary", { name: "Inspector" });
    fireEvent.click(within(within(inspector).getByRole("group", { name: "Interval class" })).getByRole("button", { name: "Transition" }));
    await waitFor(() =>
      expect(api.patchStageShotCoach).toHaveBeenCalledWith(
        "anna",
        2,
        expect.objectContaining({ id: "c1" }),
        { interval_class: "transition", interval_class_source: "manual" },
        4,
      ),
    );
  });

  it("selecting a region puts its card in the inspector; Keep saves it through the region hook", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(coach(EVENTS));
    vi.mocked(api.putStageEvents).mockResolvedValue({
      ...coach([EVENTS[0], { ...EVENTS[1], source: "manual" }]),
      _version: "bbbbbbbbbbbbbbbb",
    });
    renderAt("/match/m1/breakdown/anna/2");
    fireEvent.click(await screen.findByTestId("event-evt-2"));
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).getByRole("region", { name: "Region" })).toBeInTheDocument();
    expect(within(inspector).queryByRole("region", { name: "Shot 1" })).toBeNull();
    fireEvent.click(within(inspector).getByRole("button", { name: "Keep" }));
    await waitFor(() =>
      expect(api.putStageEvents).toHaveBeenCalledWith(
        "anna",
        2,
        [EVENTS[0], { ...EVENTS[1], source: "manual" }],
        "aaaaaaaaaaaaaaaa",
      ),
    );
    await waitFor(() => expect(screen.getByText("2 regions")).toBeInTheDocument());
  });
});
