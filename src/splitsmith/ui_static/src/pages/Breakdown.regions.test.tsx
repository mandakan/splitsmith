/**
 * Region editing and the timeline band, moved here from the Coach page
 * tests when Coach became the review page (#1374, epic #1370): Breakdown is
 * now the only page that edits regions, so the save chain, the save notice,
 * the capability gate and the band are pinned on it.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, onTestFinished, vi } from "vitest";

import { ApiError, type CoachShot, type CoachStageResponse, type StageEvent } from "@/lib/api";
import { COMMIT_DEBOUNCE_MS } from "@/lib/useStageEvents";

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
      reclassifyStageCoach: vi.fn(),
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

function stageWith(events: StageEvent[], version = "aaaaaaaaaaaaaaaa", shots = [makeShot(1, "c1")]): CoachStageResponse {
  return {
    stage_number: 1,
    stage_name: "Stage One",
    beep_time: 5,
    version: 4,
    videos: [{ path: "trimmed/stage1.mp4", role: "primary", beep_in_clip: 5, kind: "trim" }],
    shots,
    events,
    _version: version,
  };
}

const PROJECT = {
  name: "M",
  competitor_name: "Anna",
  stages: [{ stage_number: 1, stage_name: "Stage One", time_seconds: 16.2 }],
} as never;

function renderBreakdown() {
  return render(
    <MemoryRouter initialEntries={["/match/m1/breakdown/anna/1"]}>
      <Routes>
        <Route path="/match/:matchId/breakdown/:slug/:stage" element={<Breakdown />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.mocked(api.getProject).mockReset();
  vi.mocked(api.getProject).mockResolvedValue(PROJECT);
  vi.mocked(api.getStageCoach).mockReset();
  vi.mocked(api.putStageEvents).mockReset();
  HTMLElement.prototype.setPointerCapture = vi.fn();
  HTMLElement.prototype.hasPointerCapture = vi.fn(() => true);
  HTMLElement.prototype.releasePointerCapture = vi.fn();
});

describe("region saves on Breakdown", () => {
  it("deleting the selected region PUTs the list with _version and applies the response", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(
      stageWith([{ id: "evt-1", kind: "reload", start: 8.05, end: 9.47, source: "auto" }], "v1v1v1v1v1v1v1v1"),
    );
    vi.mocked(api.putStageEvents).mockResolvedValue(stageWith([], "v2v2v2v2v2v2v2v2"));
    renderBreakdown();
    fireEvent.click(await screen.findByTestId("event-evt-1"));
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    await waitFor(() => expect(api.putStageEvents).toHaveBeenCalledWith("anna", 1, [], "v1v1v1v1v1v1v1v1"));
    await waitFor(() => expect(screen.queryByTestId("event-evt-1")).toBeNull());
  });

  it("Keep on a proposal PUTs the list with that region manual and every other field unchanged", async () => {
    const move: StageEvent = { id: "evt-1", kind: "movement", start: 7.6, end: 9.16, source: "manual" };
    const auto: StageEvent = { id: "evt-2", kind: "reload", start: 8.05, end: 9.47, source: "auto", note: "late grip" };
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([move, auto], "v1v1v1v1v1v1v1v1"));
    vi.mocked(api.putStageEvents).mockResolvedValue(stageWith([move, { ...auto, source: "manual" }], "v2v2v2v2v2v2v2v2"));
    renderBreakdown();
    fireEvent.click(await screen.findByTestId("event-evt-2"));
    fireEvent.click(await screen.findByRole("button", { name: "Keep" }));
    await waitFor(() => expect(api.putStageEvents).toHaveBeenCalledTimes(1));
    expect(api.putStageEvents).toHaveBeenCalledWith("anna", 1, [move, { ...auto, source: "manual" }], "v1v1v1v1v1v1v1v1");
    await waitFor(() => expect(screen.getByTestId("event-evt-2")).toHaveAttribute("data-source", "manual"));
    expect(screen.queryByRole("button", { name: "Keep" })).toBeNull();
  });

  it("a 409 on the PUT reloads the coach payload", async () => {
    const stale = stageWith([{ id: "evt-1", kind: "reload", start: 8.05, end: 9.47, source: "auto" }], "old");
    const fresh = stageWith([{ id: "evt-2", kind: "movement", start: 1, end: 2, source: "manual" }], "new");
    vi.mocked(api.getStageCoach).mockResolvedValueOnce(stale).mockResolvedValueOnce(fresh);
    vi.mocked(api.putStageEvents).mockRejectedValue(new ApiError(409, "version_conflict", { code: "version_conflict" }));
    renderBreakdown();
    fireEvent.click(await screen.findByTestId("event-evt-1"));
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(await screen.findByTestId("event-evt-2")).toBeInTheDocument();
    expect(api.getStageCoach).toHaveBeenCalledTimes(2);
  });

  it("Reclassify lives on Breakdown and applies the response", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([]));
    vi.mocked(api.reclassifyStageCoach).mockResolvedValue(stageWith([]));
    renderBreakdown();
    fireEvent.click(await screen.findByRole("button", { name: "Reclassify" }));
    await waitFor(() => expect(api.reclassifyStageCoach).toHaveBeenCalledWith("anna", 1));
  });
});

describe("a failed region save says so in the inspector", () => {
  const region = (id: string, start: number): StageEvent => ({ id, kind: "movement", start, end: start + 1, source: "manual" });
  const conflict = () => new ApiError(409, "version_conflict", { code: "version_conflict" });
  const deleteRegion = async (id: string) => {
    fireEvent.click(await screen.findByTestId(`event-${id}`));
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
  };
  const notices = () => screen.queryAllByTestId("region-save-notice");

  it("a 409 that discarded the edit shows the notice and keeps the page", async () => {
    vi.mocked(api.getStageCoach)
      .mockResolvedValueOnce(stageWith([region("evt-1", 1)], "old"))
      .mockResolvedValueOnce(stageWith([region("evt-2", 4)], "new"));
    vi.mocked(api.putStageEvents).mockRejectedValue(conflict());
    renderBreakdown();
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
    renderBreakdown();
    await deleteRegion("evt-1");
    const retry = await screen.findByRole("button", { name: "Retry" });
    expect(retry).toBeEnabled();
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
    renderBreakdown();
    await deleteRegion("evt-1");
    const notice = await screen.findByTestId("region-save-notice");
    expect(within(notice).getByText("Your last region change was not saved.")).toHaveAttribute("title", "Internal Server Error");
    expect(notice).toHaveAttribute("role", "alert");
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
    renderBreakdown();
    await deleteRegion("evt-1");
    const notice = await screen.findByTestId("region-save-notice");
    expect(within(notice).getByText("Your last region change was not saved.")).toHaveAttribute("title", "Service Unavailable");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.getByTestId("lane-editor")).toBeInTheDocument();
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
    renderBreakdown();
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

describe("Breakdown and the edit capability", () => {
  it("a local match is editable: the handles show", async () => {
    vi.mocked(api.getProject).mockResolvedValue({ ...(PROJECT as object), origin: "local", capabilities: ["edit", "review"] } as never);
    vi.mocked(api.getStageCoach).mockResolvedValue(
      stageWith([{ id: "evt-1", kind: "reload", start: 8.05, end: 9.47, source: "auto" }]),
    );
    renderBreakdown();
    expect(await screen.findByTestId("handle-evt-1-start")).toBeInTheDocument();
    expect(screen.getByTestId("handle-evt-1-end")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Regions" })).toBeNull();
  });

  it("a desktop-origin mirror shows the lanes read-only with the list and never writes", async () => {
    vi.mocked(api.getProject).mockResolvedValue({
      ...(PROJECT as object),
      origin: "desktop",
      capabilities: ["review", "share_manage", "comment_write"],
    } as never);
    vi.mocked(api.getStageCoach).mockResolvedValue(
      stageWith([{ id: "evt-1", kind: "reload", start: 8.05, end: 9.47, source: "auto" }]),
    );
    renderBreakdown();
    const region = await screen.findByTestId("event-evt-1");
    vi.useFakeTimers();
    onTestFinished(() => {
      vi.useRealTimers();
    });
    fireEvent.click(region);
    expect(screen.queryByTestId("handle-evt-1-start")).toBeNull();
    expect(screen.queryByRole("region", { name: "Region" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
    fireEvent.keyDown(region, { key: "Delete" });
    expect(screen.getByRole("list", { name: "Regions" })).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(COMMIT_DEBOUNCE_MS * 2);
    });
    expect(api.putStageEvents).not.toHaveBeenCalled();
  });
});

describe("Breakdown timeline band", () => {
  // jsdom's clientWidth is 0 by default; the zoom pipeline needs a viewport.
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

  function renderTimeline(stages: number[] = [1]) {
    vi.mocked(api.getProject).mockResolvedValue({
      name: "M",
      competitor_name: "Anna",
      stages: stages.map((n) => ({ stage_number: n, stage_name: `Stage ${n}`, time_seconds: 20 })),
    } as never);
    vi.mocked(api.getStageCoach).mockImplementation((_slug: string, stageNumber: number) =>
      Promise.resolve({ ...stageWith([]), stage_number: stageNumber }),
    );
    return renderBreakdown();
  }

  it("draws the lanes and an audio track", async () => {
    renderTimeline();
    const band = await screen.findByTestId("timeline");
    expect(within(band).getByText("Audio")).toBeInTheDocument();
    expect(within(band).getByText("Reload")).toBeInTheDocument();
    expect(within(band).getByTestId("lane-editor")).toBeInTheDocument();
  });

  it("shows No audio when the peaks request fails, and the lanes still work", async () => {
    vi.mocked(api.getStagePeaks).mockReset();
    vi.mocked(api.getStagePeaks).mockRejectedValue(new ApiError(404, "no trim"));
    renderTimeline();
    expect(await screen.findByText("No audio")).toBeInTheDocument();
    expect(screen.getByTestId("lane-editor")).toBeInTheDocument();
  });

  it("starts each stage at Fit", async () => {
    renderTimeline([1, 2]);
    const band = await screen.findByTestId("timeline");
    fireEvent.click(within(band).getByRole("button", { name: "Zoom in" }));
    expect(within(band).getByText("1.5x")).toBeInTheDocument();
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
    renderTimeline();
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
    renderTimeline();
    await screen.findByTestId("timeline");
    expect(screen.queryByText("No audio")).toBeNull();
    await act(async () => {
      rejectPeaks(new ApiError(404, "no trim"));
    });
    expect(await screen.findByText("No audio")).toBeInTheDocument();
  });

  it("the timeline band's options menu toggles full-resolution video through the scrub settings", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue({
      ...stageWith([]),
      videos: [{ path: "trimmed/stage1.mp4", role: "primary", beep_in_clip: 5, kind: "trim", trim_version: "t1", scrub_version: "s1" }],
    });
    const { container } = renderBreakdown();
    await screen.findByTestId("lane-editor");
    fireEvent.click(await screen.findByRole("button", { name: "Timeline options" }));
    fireEvent.click(await screen.findByRole("menuitemcheckbox", { name: /Full-resolution video/ }));
    expect(api.setScrubSettings).toHaveBeenCalledWith(true);
    await waitFor(() => expect(container.querySelector("video")?.getAttribute("src")).toContain("/trim/"));
  });
});
