import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type CoachShot, type CoachStageResponse, type StageEvent } from "@/lib/api";

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
      putStageEvents: vi.fn(),
      videoStreamUrl: (_slug: string, path: string, kind = "auto", _v?: string | null, stage?: number | null) => `http://localhost/${kind}/${path}${stage != null ? `#s${stage}` : ""}`,
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
    videos: [{ path: "trimmed/stage1.mp4", role: "primary", beep_in_clip: 5, kind: "trim" }],
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

describe("Coach stage stream URL", () => {
  it("pins the video player stream to the measured clip kind", async () => {
    const shot = makeShot(1, "cand-7");
    const { container } = renderCoachStage([shot]);

    await waitFor(() => {
      const videoElement = container.querySelector("video");
      expect(videoElement?.src).toContain("/trim/");
    });
    // A single take shares its source across stages; the URL names this one.
    expect(container.querySelector("video")?.src).toMatch(/#s1$/);
  });
});

function makeCoachWithEvents(shots: CoachShot[], events: StageEvent[], version = "aaaaaaaaaaaaaaaa"): CoachStageResponse {
  return { ...makeCoach(shots), events, _version: version,
    event_summary: { movement_s: 0, moving_shots: 0, reloads: events.filter((e) => e.kind === "reload").length,
      reload_avg_s: null, overhang_s: 0.31, capacity_warning: null } };
}

function renderCoachRoute() {
  return render(<MemoryRouter initialEntries={["/match/m1/coach/anna/1"]}><Routes>
    <Route path="/match/:matchId/coach/:slug/:stage" element={<Coach />} /></Routes></MemoryRouter>);
}

describe("stage events on the Coach page", () => {
  beforeEach(() => {
    vi.mocked(api.putStageEvents).mockReset();
    vi.mocked(api.getStageCoach).mockReset();
    HTMLElement.prototype.setPointerCapture = vi.fn();
    HTMLElement.prototype.hasPointerCapture = vi.fn(() => true);
    HTMLElement.prototype.releasePointerCapture = vi.fn();
  });

  it("renders the lane editor with the payload's events and the stat strip figures", async () => {
    vi.mocked(api.getProject).mockResolvedValue({ name: "M", competitor_name: "Anna",
      stages: [{ stage_number: 1, stage_name: "Stage One", time_seconds: 16.2 }] } as never);
    vi.mocked(api.getStageCoach).mockResolvedValue(
      makeCoachWithEvents([makeShot(1, "c1"), makeShot(2, "c2")], [{ id: "evt-1", kind: "reload", start: 8.05, end: 9.47, source: "auto" }]),
    );
    render(<MemoryRouter initialEntries={["/match/m1/coach/anna/1"]}><Routes>
      <Route path="/match/:matchId/coach/:slug/:stage" element={<Coach />} /></Routes></MemoryRouter>);
    expect(await screen.findByTestId("event-evt-1")).toHaveAttribute("data-source", "auto");
    expect(screen.getByText("Overhang")).toBeInTheDocument();
    expect(screen.getByText("+0.31")).toBeInTheDocument();
  });

  it("deleting the selected region PUTs the list with _version and applies the response", async () => {
    vi.mocked(api.getProject).mockResolvedValue({ name: "M", competitor_name: "Anna",
      stages: [{ stage_number: 1, stage_name: "Stage One", time_seconds: 16.2 }] } as never);
    const first = makeCoachWithEvents([makeShot(1, "c1")], [{ id: "evt-1", kind: "reload", start: 8.05, end: 9.47, source: "auto" }], "v1v1v1v1v1v1v1v1");
    vi.mocked(api.getStageCoach).mockResolvedValue(first);
    vi.mocked(api.putStageEvents).mockResolvedValue(makeCoachWithEvents([makeShot(1, "c1")], [], "v2v2v2v2v2v2v2v2"));
    render(<MemoryRouter initialEntries={["/match/m1/coach/anna/1"]}><Routes>
      <Route path="/match/:matchId/coach/:slug/:stage" element={<Coach />} /></Routes></MemoryRouter>);
    fireEvent.click(await screen.findByTestId("event-evt-1"));
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    await waitFor(() => expect(api.putStageEvents).toHaveBeenCalledWith("anna", 1, [], "v1v1v1v1v1v1v1v1"));
    await waitFor(() => expect(screen.queryByTestId("event-evt-1")).toBeNull());
  });

  it("a 409 on the PUT reloads the coach payload", async () => {
    vi.mocked(api.getProject).mockResolvedValue({ name: "M", competitor_name: "Anna",
      stages: [{ stage_number: 1, stage_name: "Stage One", time_seconds: 16.2 }] } as never);
    const stale = makeCoachWithEvents([makeShot(1, "c1")], [{ id: "evt-1", kind: "reload", start: 8.05, end: 9.47, source: "auto" }], "old");
    const fresh = makeCoachWithEvents([makeShot(1, "c1")], [{ id: "evt-2", kind: "movement", start: 1, end: 2, source: "manual" }], "new");
    vi.mocked(api.getStageCoach).mockResolvedValueOnce(stale).mockResolvedValueOnce(fresh);
    vi.mocked(api.putStageEvents).mockRejectedValue(new ApiError(409, "version_conflict", { code: "version_conflict" }));
    render(<MemoryRouter initialEntries={["/match/m1/coach/anna/1"]}><Routes>
      <Route path="/match/:matchId/coach/:slug/:stage" element={<Coach />} /></Routes></MemoryRouter>);
    fireEvent.click(await screen.findByTestId("event-evt-1"));
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(await screen.findByTestId("event-evt-2")).toBeInTheDocument();
    expect(api.getStageCoach).toHaveBeenCalledTimes(2);
  });

  it("the region card replaces the shot editor until a shot is picked again", async () => {
    vi.mocked(api.getProject).mockResolvedValue({ name: "M", competitor_name: "Anna",
      stages: [{ stage_number: 1, stage_name: "Stage One", time_seconds: 16.2 }] } as never);
    vi.mocked(api.getStageCoach).mockResolvedValue(
      makeCoachWithEvents([makeShot(1, "c1")], [{ id: "evt-1", kind: "reload", start: 8.05, end: 9.47, source: "auto" }]),
    );
    const { container } = renderCoachRoute();
    fireEvent.click(await screen.findByTestId("event-evt-1"));
    expect(screen.getByRole("region", { name: "Region" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Shot 1" })).toBeNull();
    fireEvent.click(container.querySelector<HTMLElement>('[data-shot-number="1"]')!);
    expect(screen.queryByRole("region", { name: "Region" })).toBeNull();
    expect(screen.getByRole("region", { name: "Shot 1" })).toBeInTheDocument();
  });

  it("a desktop-origin mirror shows the lanes read-only with the list and never writes", async () => {
    vi.mocked(api.getProject).mockResolvedValue({ name: "M", competitor_name: "Anna", origin: "desktop",
      stages: [{ stage_number: 1, stage_name: "Stage One", time_seconds: 16.2 }] } as never);
    vi.mocked(api.getStageCoach).mockResolvedValue(
      makeCoachWithEvents([makeShot(1, "c1")], [{ id: "evt-1", kind: "reload", start: 8.05, end: 9.47, source: "auto" }]),
    );
    renderCoachRoute();
    fireEvent.click(await screen.findByTestId("event-evt-1"));
    expect(screen.queryByTestId("handle-evt-1-start")).toBeNull();
    expect(screen.queryByTestId("handle-evt-1-end")).toBeNull();
    // No region card, so no Delete / kind control to reach.
    expect(screen.queryByRole("region", { name: "Region" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
    // The keyboard path is closed too.
    fireEvent.keyDown(screen.getByTestId("lane-editor"), { key: "Delete" });
    // The read-only list stands in for the card.
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    await new Promise((r) => setTimeout(r, 400));
    expect(api.putStageEvents).not.toHaveBeenCalled();
  });
});
