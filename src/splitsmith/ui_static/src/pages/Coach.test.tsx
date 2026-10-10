import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, onTestFinished, vi } from "vitest";

import { ApiError, type CoachShot, type CoachStageResponse, type CoachVideoEntry, type StageEvent } from "@/lib/api";
import { summarize } from "@/lib/events";
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
      reload_avg_s: null, exposed_reload_s: 0.31, capacity_warning: null } };
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
    // The strip now computes from these events (summarize, #1324), not the
    // mocked event_summary below: a movement region overlapping the reload
    // leaves 9.47 - 9.16 = 0.31 of it exposed, same as the hardcoded figure
    // makeCoachWithEvents used to send, so the two cannot be told apart by
    // this assertion alone -- the parity test below is what pins it.
    vi.mocked(api.getStageCoach).mockResolvedValue(
      makeCoachWithEvents(
        [makeShot(1, "c1"), makeShot(2, "c2")],
        [
          { id: "evt-0", kind: "movement", start: 7.6, end: 9.16, source: "manual" },
          { id: "evt-1", kind: "reload", start: 8.05, end: 9.47, source: "manual" },
        ],
      ),
    );
    render(<MemoryRouter initialEntries={["/match/m1/coach/anna/1"]}><Routes>
      <Route path="/match/:matchId/coach/:slug/:stage" element={<Coach />} /></Routes></MemoryRouter>);
    expect(await screen.findByTestId("event-evt-1")).toHaveAttribute("data-source", "manual");
    const strip = screen.getByText("Exposed reload").parentElement!;
    expect(within(strip).getByText("0.31")).toBeInTheDocument();
    expect(screen.queryByText("+0.31")).toBeNull();
  });

  it("shows a standing reload's whole duration as exposed in the stat strip", async () => {
    vi.mocked(api.getProject).mockResolvedValue({ name: "M", competitor_name: "Anna",
      stages: [{ stage_number: 1, stage_name: "Stage One", time_seconds: 16.2 }] } as never);
    vi.mocked(api.getStageCoach).mockResolvedValue(
      makeCoachWithEvents([makeShot(1, "c1"), makeShot(2, "c2")], [
        { id: "evt-1", kind: "reload", start: 8.05, end: 9.47, source: "manual" },
      ]),
    );
    renderCoachRoute();
    await screen.findByTestId("event-evt-1");
    const strip = screen.getByText("Exposed reload").parentElement!;
    expect(within(strip).getByText("1.42")).toBeInTheDocument();
  });

  it("ignores a stale server moving/exposed figure and computes it from the local events; capacity_warning still comes from the server", async () => {
    vi.mocked(api.getProject).mockResolvedValue({ name: "M", competitor_name: "Anna",
      stages: [{ stage_number: 1, stage_name: "Stage One", time_seconds: 16.2 }] } as never);
    const movement: StageEvent = { id: "evt-0", kind: "movement", start: 7.6, end: 9.16, source: "manual" };
    const reload: StageEvent = { id: "evt-1", kind: "reload", start: 8.05, end: 9.47, source: "manual" };
    const shots = [makeShot(1, "c1"), makeShot(2, "c2")];
    // The TS twin of the server's own ``events.stage_event_summary`` (all
    // regions, no capacity) for this exact list -- what the real server
    // response would carry.
    const real = summarize(shots.map((s) => s.time_from_beep), [movement, reload], null);
    expect(real.exposed_reload_s).toBeCloseTo(0.31);
    const coach = makeCoachWithEvents(shots, [movement, reload], "v1v1v1v1v1v1v1v1");
    coach.event_summary = {
      // A stale server figure (e.g. computed before an edit landed): if the
      // page read this instead of the local events, it would show 9.99, not
      // the real 0.31.
      ...real,
      moving_shots: 7,
      exposed_reload_s: 9.99,
      capacity_warning: "9 shots without a reload",
    };
    vi.mocked(api.getStageCoach).mockResolvedValue(coach);
    renderCoachRoute();
    expect(await screen.findByText("0.31")).toBeInTheDocument();
    expect(screen.queryByText("9.99")).toBeNull();
    // Neither shot (t=1, t=2) falls inside the movement region (7.6-9.16),
    // so the real, locally-computed count is 0 -- not the server's stale 7.
    const onMove = screen.getByText("On the move").parentElement!;
    expect(within(onMove).getByText("0")).toBeInTheDocument();
    expect(within(onMove).queryByText("7")).toBeNull();
    // capacity_warning has no local source (the SPA has no division
    // capacity), so it still comes straight from the server payload.
    expect(screen.getByText("9 shots without a reload")).toBeInTheDocument();
  });

  it("a nudge updates the stat strip before its PUT resolves", async () => {
    vi.mocked(api.getProject).mockResolvedValue({ name: "M", competitor_name: "Anna",
      stages: [{ stage_number: 1, stage_name: "Stage One", time_seconds: 16.2 }] } as never);
    vi.mocked(api.getStageCoach).mockResolvedValue(
      makeCoachWithEvents(
        [makeShot(1, "c1"), makeShot(2, "c2")],
        [
          { id: "evt-0", kind: "movement", start: 7.6, end: 9.16, source: "manual" },
          { id: "evt-1", kind: "reload", start: 8.05, end: 9.47, source: "manual" },
        ],
        "v1v1v1v1v1v1v1v1",
      ),
    );
    // Never resolves: proves the strip's new figure appears without waiting
    // on the PUT, let alone its answer.
    vi.mocked(api.putStageEvents).mockImplementation(() => new Promise(() => {}));
    renderCoachRoute();
    expect(await screen.findByText("0.31")).toBeInTheDocument();
    // Selecting the region opens the region card, which shows its own
    // "Exposed" readout alongside the strip's -- scope to the strip (the
    // container that also holds "On the move") so the two are not conflated.
    const strip = screen.getByText("On the move").closest("div")!.parentElement!;
    const region = await screen.findByTestId("event-evt-1");
    fireEvent.click(region);
    vi.useFakeTimers();
    onTestFinished(() => {
      vi.useRealTimers();
    });
    // Shift+ArrowRight moves the reload's end one frame later (1/30 s),
    // widening the exposed time from 0.31 to ~0.34.
    fireEvent.keyDown(region, { key: "ArrowRight", shiftKey: true });
    expect(within(strip).getByText("0.34")).toBeInTheDocument();
    expect(within(strip).queryByText("0.31")).toBeNull();
    // Still true once the debounce fires the (unresolved) PUT.
    expect(api.putStageEvents).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(COMMIT_DEBOUNCE_MS);
    });
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);
    expect(within(strip).getByText("0.34")).toBeInTheDocument();
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

  it("counts only confirmed regions in the stat strip: a proposed reload adds no exposed figure until Keep", async () => {
    vi.mocked(api.getProject).mockResolvedValue({ name: "M", competitor_name: "Anna",
      stages: [{ stage_number: 1, stage_name: "Stage One", time_seconds: 16.2 }] } as never);
    const auto: StageEvent = { id: "evt-1", kind: "reload", start: 8.05, end: 9.47, source: "auto" };
    vi.mocked(api.getStageCoach).mockResolvedValue(makeCoachWithEvents([makeShot(1, "c1")], [auto], "v1v1v1v1v1v1v1v1"));
    vi.mocked(api.putStageEvents).mockResolvedValue(
      makeCoachWithEvents([makeShot(1, "c1")], [{ ...auto, source: "manual" }], "v2v2v2v2v2v2v2v2"));
    renderCoachRoute();
    fireEvent.click(await screen.findByTestId("event-evt-1"));
    // The card still describes the proposal itself...
    const card = screen.getByRole("region", { name: "Region" });
    expect(within(card).getByText("Exposed")).toBeInTheDocument();
    // ...but the stage total in the strip does not count it.
    expect(screen.getByText("On the move")).toBeInTheDocument();
    expect(screen.queryByText("Exposed reload")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));
    const strip = (await screen.findByText("Exposed reload")).parentElement!;
    expect(within(strip).getByText("1.42")).toBeInTheDocument();
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
    fireEvent.keyDown(region, { key: "Delete" });
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

  it("the timeline band's options menu toggles full-resolution video through the scrub settings", async () => {
    // The "More" menu that used to live on the lane editor itself moved onto
    // the shared Timeline band (spec 2026-10-09); the switch is unchanged.
    vi.mocked(api.getProject).mockResolvedValue(PROJECT as never);
    vi.mocked(api.getStageCoach).mockResolvedValue(trimCoach({}));
    const { container } = renderRoute();
    await screen.findByTestId("lane-editor");
    fireEvent.click(await screen.findByRole("button", { name: "Timeline options" }));
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

describe("Coach stage timeline band", () => {
  // The band needs a real zoom pipeline test (clicking Zoom in), which needs
  // a non-zero viewport: jsdom's clientWidth is 0 by default, same stub as
  // components/timeline/Timeline.test.tsx.
  let widthSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    widthSpy = vi.spyOn(Element.prototype, "clientWidth", "get").mockReturnValue(1000);
    vi.mocked(api.getStagePeaks).mockReset();
    vi.mocked(api.getStagePeaks).mockResolvedValue({
      duration: 20,
      sample_rate: 8000,
      bins: 4,
      peaks: [0.1, 0.5, 0.2, 0.1],
      beep_time: 2,
      trimmed: true,
    });
  });

  afterEach(() => {
    widthSpy.mockRestore();
  });

  function renderCoachTimeline(stages: { stage_number: number; time_seconds: number }[] = [{ stage_number: 1, time_seconds: 20 }]) {
    vi.mocked(api.getProject).mockResolvedValue({
      name: "M",
      competitor_name: "Anna",
      stages: stages.map((s) => ({ stage_number: s.stage_number, stage_name: `Stage ${s.stage_number}`, time_seconds: s.time_seconds })),
    } as unknown as Awaited<ReturnType<typeof api.getProject>>);
    vi.mocked(api.getStageCoach).mockImplementation((_slug: string, stageNumber: number) =>
      Promise.resolve({ ...makeCoach([makeShot(1, "c1")]), stage_number: stageNumber }),
    );
    return render(
      <MemoryRouter initialEntries={["/match/m1/coach/anna/1"]}>
        <Routes>
          <Route path="/match/:matchId/coach/:slug/:stage" element={<Coach />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("draws the lanes and an audio track in a full-width timeline band", async () => {
    renderCoachTimeline();
    const band = await screen.findByTestId("timeline");
    expect(within(band).getByText("Audio")).toBeInTheDocument();
    expect(within(band).getByText("Reload")).toBeInTheDocument();
    expect(within(band).getByTestId("lane-editor")).toBeInTheDocument();
    // The band is outside the two-column grid: no ancestor carries the
    // 380 px column template.
    expect(band.closest("[class*='380px']")).toBeNull();
  });

  it("shows No audio when the peaks request fails, and the lanes still work", async () => {
    vi.mocked(api.getStagePeaks).mockReset();
    vi.mocked(api.getStagePeaks).mockRejectedValue(new ApiError(404, "no trim"));
    renderCoachTimeline();
    expect(await screen.findByText("No audio")).toBeInTheDocument();
    expect(screen.getByTestId("lane-editor")).toBeInTheDocument();
  });

  // This pins the per-stage remount CoachStage's own `key` already does
  // (Coach.tsx's top-level `Coach()`, `key={`${slug}-${stage}`}`): there is
  // no explicit zoom reset in CoachStageInner for it to pin instead. It
  // fails if that key stops covering the stage number (verified by hand:
  // temporarily dropping the stage from the key reproduces the fresh-mount
  // `useState<Zoom>(null)` as a stale one-time init instead, and this test
  // fails because the second "timeline" is the *same* Timeline instance
  // carrying zoom 1.5 forward).
  it("starts each stage at Fit", async () => {
    renderCoachTimeline([
      { stage_number: 1, time_seconds: 20 },
      { stage_number: 2, time_seconds: 20 },
    ]);
    const band = await screen.findByTestId("timeline");
    fireEvent.click(within(band).getByRole("button", { name: "Zoom in" }));
    expect(within(band).getByText("1.5x")).toBeInTheDocument();
    // Navigate to the next stage with the page's own next-stage control. It
    // is rendered through Button asChild + Link, so its accessible role is
    // "link", not "button".
    fireEvent.click(screen.getByRole("link", { name: "Next stage" }));
    const next = await screen.findByTestId("timeline");
    expect(within(next).getByRole("button", { name: "Fit" })).toHaveAttribute("aria-pressed", "true");
  });

  it("draws an empty audio track while peaks are loading, never flashing No audio", async () => {
    let resolvePeaks: (p: Awaited<ReturnType<typeof api.getStagePeaks>>) => void = () => {};
    vi.mocked(api.getStagePeaks).mockReset();
    vi.mocked(api.getStagePeaks).mockImplementation(
      () => new Promise((resolve) => {
        resolvePeaks = resolve;
      }),
    );
    renderCoachTimeline();
    const band = await screen.findByTestId("timeline");
    expect(within(band).getByTestId("lane-editor")).toBeInTheDocument();
    expect(screen.queryByText("No audio")).toBeNull();
    await act(async () => {
      resolvePeaks({ duration: 20, sample_rate: 8000, bins: 4, peaks: [0.1, 0.5, 0.2, 0.1], beep_time: 2, trimmed: true });
    });
    expect(screen.queryByText("No audio")).toBeNull();
  });

  it("shows No audio once the peaks request has settled without any", async () => {
    let rejectPeaks: (e: unknown) => void = () => {};
    vi.mocked(api.getStagePeaks).mockReset();
    vi.mocked(api.getStagePeaks).mockImplementation(
      () => new Promise((_resolve, reject) => {
        rejectPeaks = reject;
      }),
    );
    renderCoachTimeline();
    await screen.findByTestId("timeline");
    expect(screen.queryByText("No audio")).toBeNull();
    await act(async () => {
      rejectPeaks(new ApiError(404, "no trim"));
    });
    expect(await screen.findByText("No audio")).toBeInTheDocument();
  });
});
