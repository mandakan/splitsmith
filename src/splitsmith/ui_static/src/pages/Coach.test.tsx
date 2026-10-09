import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, onTestFinished, vi } from "vitest";

import { ApiError, type CoachShot, type CoachStageResponse, type CoachVideoEntry, type StageEvent } from "@/lib/api";
import { COMMIT_DEBOUNCE_MS } from "@/lib/useStageEvents";

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
      getScrubSettings: vi.fn().mockResolvedValue({ full_res_scrub: false }),
      setScrubSettings: vi.fn().mockResolvedValue({ full_res_scrub: true }),
      videoStreamUrl: (_slug: string, path: string, kind = "auto", _v?: string | null, stage?: number | null) => `http://localhost/${kind}/${path}${stage != null ? `#s${stage}` : ""}`,
    },
  };
});

vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return {
    ...actual,
    useDeploymentMode: () => ({ mode: "local", resolved: true }),
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

  it("Keep on a proposal PUTs the list with that region manual and every other field unchanged", async () => {
    vi.mocked(api.getProject).mockResolvedValue({ name: "M", competitor_name: "Anna",
      stages: [{ stage_number: 1, stage_name: "Stage One", time_seconds: 16.2 }] } as never);
    const move: StageEvent = { id: "evt-1", kind: "movement", start: 7.6, end: 9.16, source: "manual" };
    const auto: StageEvent = { id: "evt-2", kind: "reload", start: 8.05, end: 9.47, source: "auto", note: "late grip" };
    vi.mocked(api.getStageCoach).mockResolvedValue(makeCoachWithEvents([makeShot(1, "c1")], [move, auto], "v1v1v1v1v1v1v1v1"));
    vi.mocked(api.putStageEvents).mockResolvedValue(
      makeCoachWithEvents([makeShot(1, "c1")], [move, { ...auto, source: "manual" }], "v2v2v2v2v2v2v2v2"));
    renderCoachRoute();
    fireEvent.click(await screen.findByTestId("event-evt-2"));
    fireEvent.click(await screen.findByRole("button", { name: "Keep" }));
    await waitFor(() => expect(api.putStageEvents).toHaveBeenCalledTimes(1));
    expect(api.putStageEvents).toHaveBeenCalledWith("anna", 1, [move, { ...auto, source: "manual" }], "v1v1v1v1v1v1v1v1");
    await waitFor(() => expect(screen.getByTestId("event-evt-2")).toHaveAttribute("data-source", "manual"));
    expect(screen.queryByRole("button", { name: "Keep" })).toBeNull();
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

  describe("a failed region save says so under the lane editor", () => {
    const PROJECT = { name: "M", competitor_name: "Anna",
      stages: [{ stage_number: 1, stage_name: "Stage One", time_seconds: 16.2 }] } as never;
    const region = (id: string, start: number): StageEvent => ({ id, kind: "movement", start, end: start + 1, source: "manual" });
    const stageWith = (events: StageEvent[], version: string) => makeCoachWithEvents([makeShot(1, "c1")], events, version);
    const conflict = () => new ApiError(409, "version_conflict", { code: "version_conflict" });
    const deleteRegion = async (id: string) => {
      fireEvent.click(await screen.findByTestId(`event-${id}`));
      fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    };
    const notices = () => screen.queryAllByTestId("region-save-notice");

    beforeEach(() => {
      vi.mocked(api.getProject).mockResolvedValue(PROJECT);
    });

    it("a 409 that discarded the edit shows the notice and keeps the page", async () => {
      vi.mocked(api.getStageCoach)
        .mockResolvedValueOnce(stageWith([region("evt-1", 1)], "old"))
        .mockResolvedValueOnce(stageWith([region("evt-2", 4)], "new"));
      vi.mocked(api.putStageEvents).mockRejectedValue(conflict());
      renderCoachRoute();
      await deleteRegion("evt-1");
      const notice = await screen.findByTestId("region-save-notice");
      expect(notice).toHaveTextContent("Your last region change was not saved. The stage changed elsewhere and was reloaded.");
      expect(notice).toHaveAttribute("role", "status");
      expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
      expect(screen.getByTestId("lane-editor")).toBeInTheDocument();
      expect(screen.getByTestId("event-evt-2")).toBeInTheDocument();
    });

    it("Retry is disabled, and says why, while another region edit is on its way", async () => {
      vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([region("evt-1", 1), region("evt-2", 4)], "v1"));
      let failSecond: (e: unknown) => void = () => {};
      vi.mocked(api.putStageEvents)
        .mockRejectedValueOnce(new ApiError(500, "Internal Server Error", {}))
        .mockImplementationOnce(() => new Promise((_r, j) => { failSecond = j; }));
      renderCoachRoute();
      await deleteRegion("evt-1");
      const retry = await screen.findByRole("button", { name: "Retry" });
      expect(retry).toBeEnabled();
      // The list reverted, so evt-2 is there to edit; the edit waits in the debounce, then flies.
      await deleteRegion("evt-2");
      expect(retry).toBeDisabled();
      expect(retry).toHaveAttribute("title", "Wait for the current region change to save");
      await waitFor(() => expect(api.putStageEvents).toHaveBeenCalledTimes(2));
      expect(retry).toBeDisabled();
      await act(async () => { failSecond(new ApiError(500, "Internal Server Error", {})); });
      await waitFor(() => expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled());
      expect(screen.getByRole("button", { name: "Retry" })).not.toHaveAttribute("title");
    });

    it("a 500 shows the notice with Retry; Retry re-sends the edit and its success clears the notice", async () => {
      vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([region("evt-1", 1)], "v1"));
      vi.mocked(api.putStageEvents)
        .mockRejectedValueOnce(new ApiError(500, "Internal Server Error", {}))
        .mockResolvedValueOnce(stageWith([], "v2"));
      renderCoachRoute();
      await deleteRegion("evt-1");
      const notice = await screen.findByTestId("region-save-notice");
      expect(within(notice).getByText("Your last region change was not saved.")).toBeInTheDocument();
      expect(notice).not.toHaveTextContent(/Internal Server Error|Service Unavailable/);
      // The server's message is for diagnosis: the line's tooltip, not its copy.
      expect(within(notice).getByText("Your last region change was not saved.")).toHaveAttribute("title", "Internal Server Error");
      expect(notice).toHaveAttribute("role", "alert");
      expect(screen.getByTestId("lane-editor")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
      await waitFor(() => expect(api.putStageEvents).toHaveBeenCalledTimes(2));
      expect(api.putStageEvents).toHaveBeenLastCalledWith("anna", 1, [], "v1");
      await waitFor(() => expect(notices()).toHaveLength(0));
      expect(screen.queryByTestId("event-evt-1")).toBeNull();
    });

    it("a 409 whose reload fails shows the notice, not the error page", async () => {
      vi.mocked(api.getStageCoach)
        .mockResolvedValueOnce(stageWith([region("evt-1", 1)], "v1"))
        .mockRejectedValueOnce(new ApiError(503, "Service Unavailable", {}));
      vi.mocked(api.putStageEvents).mockRejectedValue(conflict());
      renderCoachRoute();
      await deleteRegion("evt-1");
      const notice = await screen.findByTestId("region-save-notice");
      expect(within(notice).getByText("Your last region change was not saved.")).toBeInTheDocument();
      expect(notice).not.toHaveTextContent(/Internal Server Error|Service Unavailable/);
      expect(within(notice).getByText("Your last region change was not saved.")).toHaveAttribute("title", "Service Unavailable");
      expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
      expect(screen.getByTestId("lane-editor")).toBeInTheDocument();
      // Not saved, so not shown as saved: the deleted region is back.
      expect(screen.getByTestId("event-evt-1")).toBeInTheDocument();
    });

    it("the next successful save clears a shown notice, and Dismiss hides one", async () => {
      vi.mocked(api.getStageCoach)
        .mockResolvedValueOnce(stageWith([region("evt-1", 1)], "a"))
        .mockResolvedValueOnce(stageWith([region("evt-2", 4), region("evt-3", 7)], "b"));
      vi.mocked(api.putStageEvents)
        .mockRejectedValueOnce(conflict())
        .mockResolvedValueOnce(stageWith([region("evt-3", 7)], "c"))
        .mockRejectedValueOnce(new ApiError(500, "Internal Server Error", {}));
      renderCoachRoute();
      await deleteRegion("evt-1");
      await screen.findByTestId("region-save-notice");
      await deleteRegion("evt-2");
      await waitFor(() => expect(api.putStageEvents).toHaveBeenCalledTimes(2));
      await waitFor(() => expect(notices()).toHaveLength(0));
      await deleteRegion("evt-3");
      await screen.findByRole("button", { name: "Retry" });
      fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
      expect(notices()).toHaveLength(0);
    });
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

  it("a local match is editable: the handles show", async () => {
    // capabilities_for_origin("local") on the server.
    vi.mocked(api.getProject).mockResolvedValue({ name: "M", competitor_name: "Anna", origin: "local",
      capabilities: ["edit", "review"],
      stages: [{ stage_number: 1, stage_name: "Stage One", time_seconds: 16.2 }] } as never);
    vi.mocked(api.getStageCoach).mockResolvedValue(
      makeCoachWithEvents([makeShot(1, "c1")], [{ id: "evt-1", kind: "reload", start: 8.05, end: 9.47, source: "auto" }]),
    );
    renderCoachRoute();
    expect(await screen.findByTestId("handle-evt-1-start")).toBeInTheDocument();
    expect(screen.getByTestId("handle-evt-1-end")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Regions" })).toBeNull();
  });

  it("a desktop-origin mirror shows the lanes read-only with the list and never writes", async () => {
    // What a hosted mirror's GET .../project carries: capabilities_for_origin("desktop") has no edit.
    vi.mocked(api.getProject).mockResolvedValue({ name: "M", competitor_name: "Anna", origin: "desktop",
      capabilities: ["review", "share_manage", "comment_write"],
      stages: [{ stage_number: 1, stage_name: "Stage One", time_seconds: 16.2 }] } as never);
    vi.mocked(api.getStageCoach).mockResolvedValue(
      makeCoachWithEvents([makeShot(1, "c1")], [{ id: "evt-1", kind: "reload", start: 8.05, end: 9.47, source: "auto" }]),
    );
    renderCoachRoute();
    const region = await screen.findByTestId("event-evt-1");
    // Fake timers from here: a write would wait out the commit debounce, and
    // advancing past it is deterministic where a real sleep flakes under load.
    vi.useFakeTimers();
    onTestFinished(() => {
      vi.useRealTimers();
    });
    fireEvent.click(region);
    expect(screen.queryByTestId("handle-evt-1-start")).toBeNull();
    expect(screen.queryByTestId("handle-evt-1-end")).toBeNull();
    // No region card, so no Delete / kind control to reach.
    expect(screen.queryByRole("region", { name: "Region" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
    // The keyboard path is closed too.
    fireEvent.keyDown(screen.getByTestId("lane-editor"), { key: "Delete" });
    // The read-only list stands in for the card.
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(COMMIT_DEBOUNCE_MS * 2);
    });
    expect(api.putStageEvents).not.toHaveBeenCalled();
  });
});

describe("Coach player source", () => {
  const PROJECT = {
    name: "M",
    competitor_name: "Anna",
    stages: [{ stage_number: 1, stage_name: "Stage One", time_seconds: 16.2 }],
  };

  function renderRoute() {
    return render(
      <MemoryRouter initialEntries={["/match/m1/coach/anna/1"]}>
        <Routes>
          <Route path="/match/:matchId/coach/:slug/:stage" element={<Coach />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  const trimCoach = (overrides: Partial<CoachVideoEntry>): CoachStageResponse => ({
    ...makeCoach([makeShot(1, "c1")]),
    videos: [{ path: "trimmed/stage1.mp4", role: "primary", beep_in_clip: 5, kind: "trim", trim_version: "t1", scrub_version: "s1", ...overrides }],
  });

  it("streams the scrub rendition when the trim has a fresh one", async () => {
    vi.mocked(api.getProject).mockResolvedValue(PROJECT as never);
    vi.mocked(api.getStageCoach).mockResolvedValue(trimCoach({}));
    const { container } = renderRoute();
    await screen.findByTestId("lane-editor");
    expect(container.querySelector("video")?.getAttribute("src")).toContain("/scrub/");
  });

  it("falls back to the trim after the rendition errors", async () => {
    vi.mocked(api.getProject).mockResolvedValue(PROJECT as never);
    vi.mocked(api.getStageCoach).mockResolvedValue(trimCoach({}));
    const { container } = renderRoute();
    await screen.findByTestId("lane-editor");
    fireEvent.error(container.querySelector("video")!);
    await waitFor(() => expect(container.querySelector("video")?.getAttribute("src")).toContain("/trim/"));
  });

  it("a source-kind primary is left alone", async () => {
    // Review focus 5.
    vi.mocked(api.getProject).mockResolvedValue(PROJECT as never);
    vi.mocked(api.getStageCoach).mockResolvedValue(trimCoach({ kind: "source", trim_version: null, scrub_version: null }));
    const { container } = renderRoute();
    await screen.findByTestId("lane-editor");
    expect(container.querySelector("video")?.getAttribute("src")).toContain("/source/");
  });

  it("the lane editor menu toggles full-resolution video through the scrub settings", async () => {
    vi.mocked(api.getProject).mockResolvedValue(PROJECT as never);
    vi.mocked(api.getStageCoach).mockResolvedValue(trimCoach({}));
    const { container } = renderRoute();
    await screen.findByTestId("lane-editor");
    fireEvent.click(await screen.findByRole("button", { name: "More" }));
    fireEvent.click(await screen.findByRole("menuitemcheckbox", { name: /Full-resolution video/ }));
    expect(api.setScrubSettings).toHaveBeenCalledWith(true);
    await waitFor(() => expect(container.querySelector("video")?.getAttribute("src")).toContain("/trim/"));
  });
});

describe("match coach time budget rows", () => {
  it("names a stage without a name and links it to that stage's coach", async () => {
    vi.mocked(api.getProject).mockResolvedValue({
      name: "M",
      competitor_name: "Anna",
      stages: [{ stage_number: 2, stage_name: "", time_seconds: 28.4 }],
    } as unknown as Awaited<ReturnType<typeof api.getProject>>);
    vi.mocked(api.getStageCoach).mockResolvedValue({ ...makeCoach([makeShot(1, "c1"), makeShot(2, "c2")]), stage_number: 2 });
    render(
      <MemoryRouter initialEntries={["/match/m1/coach/anna"]}>
        <Routes>
          <Route path="/match/:matchId/coach/:slug" element={<Coach />} />
        </Routes>
      </MemoryRouter>,
    );
    const budget = await screen.findByRole("region", { name: "Time budget by stage" });
    const link = await within(budget).findByRole("link", { name: "Stage 2" });
    expect(link.getAttribute("href")).toMatch(/\/coach\/anna\/2$/);
  });
});
