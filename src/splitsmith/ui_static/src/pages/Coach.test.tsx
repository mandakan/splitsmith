import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, onTestFinished, vi } from "vitest";

import { ApiError, type CoachShot, type CoachStageResponse, type CoachVideoEntry } from "@/lib/api";
import { NOTE_SAVE_DELAY_MS } from "@/lib/useNoteAutosave";

import { Coach } from "@/pages/Coach";

/** Coach is the review page (#1374, epic #1370): the video, the figures,
 *  the time budget, notes and flags. Regions and intervals are display
 *  only here; their editing is pinned in Breakdown.regions.test.tsx. */

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getProject: vi.fn(),
      getStageCoach: vi.fn(),
      getMatchCoachDistributions: vi.fn().mockResolvedValue(null),
      getStagePeaks: vi.fn(),
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

function makeShot(n: number, id: string | null, over: Partial<CoachShot> = {}): CoachShot {
  return {
    id,
    shot_number: n,
    ms_after_beep: n * 1000,
    time_from_beep: n,
    time_absolute: 5 + n,
    split: n === 1 ? 1 : 0.3,
    interval_class: n === 1 ? "first_shot" : "split",
    interval_class_source: "auto",
    improvement_flag: false,
    coaching_note: null,
    stale: false,
    reload_hint: false,
    ...over,
  };
}

function makeCoach(shots: CoachShot[], version = 4, extra: Partial<CoachStageResponse> = {}): CoachStageResponse {
  return {
    stage_number: 1,
    stage_name: "Stage One",
    beep_time: 5,
    version,
    videos: [{ path: "trimmed/stage1.mp4", role: "primary", beep_in_clip: 5, kind: "trim" }],
    shots,
    _version: "aaaaaaaaaaaaaaaa",
    ...extra,
  };
}

const PROJECT = {
  name: "M",
  competitor_name: "Anna",
  stages: [
    { stage_number: 1, stage_name: "Stage One", time_seconds: 16.2 },
    { stage_number: 2, stage_name: "Stage Two", time_seconds: 20 },
  ],
};

function renderCoachRoute(path = "/match/m1/coach/anna/1") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/match/:matchId/coach/:slug/:stage" element={<Coach />} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderCoachStage(shots: CoachShot[], extra: Partial<CoachStageResponse> = {}) {
  vi.mocked(api.getProject).mockResolvedValue(PROJECT as never);
  vi.mocked(api.getStageCoach).mockResolvedValue(makeCoach(shots, 4, extra));
  return renderCoachRoute();
}

beforeEach(() => {
  vi.mocked(api.patchStageShotCoach).mockReset();
  vi.mocked(api.getStageCoach).mockReset();
  vi.mocked(api.putStageEvents).mockReset();
  vi.mocked(api.getStagePeaks).mockReset();
});

describe("Coach is the review page", () => {
  it("renders no band, lane editor, region card or interval editor, and fetches no peaks", async () => {
    renderCoachStage([makeShot(1, "c1"), makeShot(2, "c2")], {
      events: [{ id: "evt-1", kind: "reload", start: 1.2, end: 1.8, source: "manual" }],
    });
    expect(await screen.findByRole("region", { name: "Shots" })).toBeInTheDocument();
    expect(screen.queryByTestId("timeline")).toBeNull();
    expect(screen.queryByTestId("lane-editor")).toBeNull();
    expect(screen.queryByTestId("event-evt-1")).toBeNull();
    expect(screen.queryByRole("region", { name: "Region" })).toBeNull();
    expect(screen.queryByRole("group", { name: "Interval class" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reclassify" })).toBeNull();
    expect(api.getStagePeaks).not.toHaveBeenCalled();
  });

  it("links to Breakdown at this stage from the header", async () => {
    renderCoachStage([makeShot(1, "c1")]);
    const link = await screen.findByRole("link", { name: "Adjust in Breakdown" });
    expect(link.getAttribute("href")).toMatch(/^\/match\/m1\/breakdown\/anna\/1(\?|$)/);
  });

  it("shows the figures: avg split by the shared rule and the draw", async () => {
    renderCoachStage([makeShot(1, "c1"), makeShot(2, "c2"), makeShot(3, "c3", { split: 0.5 })]);
    const figures = await screen.findByLabelText("Figures");
    expect(within(within(figures).getByText("Avg split").parentElement!).getByText("0.40")).toBeInTheDocument();
    expect(within(within(figures).getByText("Draw").parentElement!).getByText("1.00")).toBeInTheDocument();
  });

  it("counts confirmed regions only: a proposed reload adds no exposed figure", async () => {
    renderCoachStage([makeShot(1, "c1"), makeShot(2, "c2")], {
      events: [{ id: "evt-1", kind: "reload", start: 8.05, end: 9.47, source: "auto" }],
    });
    const figures = await screen.findByLabelText("Figures");
    expect(within(figures).getByText("On the move")).toBeInTheDocument();
    expect(within(figures).queryByText("Exposed reload")).toBeNull();
  });

  it("a standing confirmed reload is exposed for its whole duration; one inside a movement only for its overhang", async () => {
    renderCoachStage([makeShot(1, "c1"), makeShot(2, "c2")], {
      events: [
        { id: "evt-0", kind: "movement", start: 7.6, end: 9.16, source: "manual" },
        { id: "evt-1", kind: "reload", start: 8.05, end: 9.47, source: "manual" },
      ],
    });
    const figures = await screen.findByLabelText("Figures");
    expect(within(within(figures).getByText("Exposed reload").parentElement!).getByText("0.31")).toBeInTheDocument();
  });

  it("takes capacity_warning from the server, the one figure the SPA cannot compute", async () => {
    renderCoachStage([makeShot(1, "c1")], {
      events: [],
      event_summary: { movement_s: 0, moving_shots: 7, reloads: 0, reload_avg_s: null, exposed_reload_s: 9.99, capacity_warning: "9 shots without a reload" },
    });
    expect(await screen.findByText("9 shots without a reload")).toBeInTheDocument();
    expect(screen.queryByText("9.99")).toBeNull();
  });

  it("shot rows are 40 px touch targets", async () => {
    const { container } = renderCoachStage([makeShot(1, "c1")]);
    await screen.findByRole("region", { name: "Shots" });
    expect(container.querySelector('[data-shot-number="1"]')?.className).toContain("min-h-10");
  });
});

describe("Coach notes and flags", () => {
  it("passes the shot itself to the flag PATCH, so the call can address the by-id route", async () => {
    const shot = makeShot(1, "cand-7");
    renderCoachStage([shot]);
    vi.mocked(api.patchStageShotCoach).mockResolvedValue(makeCoach([{ ...shot, improvement_flag: true }], 5));
    fireEvent.click(await screen.findByRole("button", { name: "Flag" }));
    await waitFor(() => {
      expect(api.patchStageShotCoach).toHaveBeenCalledWith(
        "anna",
        1,
        expect.objectContaining({ id: "cand-7", shot_number: 1 }),
        { improvement_flag: true },
        4,
      );
    });
    expect(await screen.findByRole("button", { name: "Flagged" })).toHaveAttribute("aria-pressed", "true");
  });

  it("guards a second patch with the version the first patch returned", async () => {
    const shot = makeShot(1, null);
    renderCoachStage([shot]);
    vi.mocked(api.patchStageShotCoach)
      .mockResolvedValueOnce(makeCoach([{ ...shot, improvement_flag: true }], 5))
      .mockResolvedValueOnce(makeCoach([shot], 6));
    fireEvent.click(await screen.findByRole("button", { name: "Flag" }));
    await waitFor(() => expect(api.patchStageShotCoach).toHaveBeenCalledTimes(1));
    fireEvent.click(await screen.findByRole("button", { name: "Flagged" }));
    await waitFor(() => expect(api.patchStageShotCoach).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.patchStageShotCoach).mock.calls[1][4]).toBe(5);
  });

  it("a note saves once after a pause in typing, and on blur at once", async () => {
    const shot = makeShot(1, "c1");
    renderCoachStage([shot, makeShot(2, "c2")]);
    const box = await screen.findByRole("textbox", { name: "Note on shot 01" });
    vi.useFakeTimers();
    onTestFinished(() => {
      vi.useRealTimers();
    });
    vi.mocked(api.patchStageShotCoach).mockResolvedValue(makeCoach([{ ...shot, coaching_note: "grip" }, makeShot(2, "c2")], 5));
    fireEvent.change(box, { target: { value: "gri" } });
    fireEvent.change(box, { target: { value: "grip" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NOTE_SAVE_DELAY_MS - 10);
    });
    expect(api.patchStageShotCoach).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20);
    });
    expect(api.patchStageShotCoach).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.patchStageShotCoach).mock.calls[0][3]).toEqual({ coaching_note: "grip" });

    vi.mocked(api.patchStageShotCoach).mockResolvedValue(makeCoach([{ ...shot, coaching_note: "grip high" }, makeShot(2, "c2")], 6));
    fireEvent.change(box, { target: { value: "grip high" } });
    fireEvent.blur(box);
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.patchStageShotCoach).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.patchStageShotCoach).mock.calls[1][3]).toEqual({ coaching_note: "grip high" });
    // The pending timer was the blur's: nothing more goes out after the pause.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NOTE_SAVE_DELAY_MS * 2);
    });
    expect(api.patchStageShotCoach).toHaveBeenCalledTimes(2);
  });

  it("emptying a note clears it (an empty string, not a no-op null)", async () => {
    const shot = makeShot(1, "c1", { coaching_note: "old" });
    renderCoachStage([shot]);
    const box = await screen.findByRole("textbox", { name: "Note on shot 01" });
    vi.mocked(api.patchStageShotCoach).mockResolvedValue(makeCoach([{ ...shot, coaching_note: null }], 5));
    fireEvent.change(box, { target: { value: "" } });
    fireEvent.blur(box);
    await waitFor(() => expect(api.patchStageShotCoach).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.patchStageShotCoach).mock.calls[0][3]).toEqual({ coaching_note: "" });
  });

  it("a failed note save says so under the note, keeps the text and the page, and Retry re-sends", async () => {
    const shot = makeShot(1, "c1");
    renderCoachStage([shot]);
    const box = await screen.findByRole("textbox", { name: "Note on shot 01" });
    vi.mocked(api.patchStageShotCoach)
      .mockRejectedValueOnce(new ApiError(500, "Internal Server Error", {}))
      .mockResolvedValueOnce(makeCoach([{ ...shot, coaching_note: "grip" }], 5));
    fireEvent.change(box, { target: { value: "grip" } });
    fireEvent.blur(box);
    const notice = await screen.findByTestId("note-save-notice");
    expect(notice).toHaveTextContent("Your last note change was not saved.");
    expect(screen.getByRole("textbox", { name: "Note on shot 01" })).toHaveValue("grip");
    fireEvent.click(within(notice).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(api.patchStageShotCoach).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByTestId("note-save-notice")).toBeNull());
  });

  it("picking another shot opens its note; the first one's typing is saved on the way out", async () => {
    const one = makeShot(1, "c1");
    const two = makeShot(2, "c2", { coaching_note: "second" });
    const { container } = renderCoachStage([one, two]);
    const box = await screen.findByRole("textbox", { name: "Note on shot 01" });
    vi.mocked(api.patchStageShotCoach).mockResolvedValue(makeCoach([{ ...one, coaching_note: "x" }, two], 5));
    fireEvent.change(box, { target: { value: "x" } });
    fireEvent.click(container.querySelector<HTMLElement>('[data-shot-number="2"]')!);
    expect(await screen.findByRole("textbox", { name: "Note on shot 02" })).toHaveValue("second");
    await waitFor(() => expect(api.patchStageShotCoach).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.patchStageShotCoach).mock.calls[0][2]).toEqual(expect.objectContaining({ id: "c1" }));
  });
});

describe("Coach stage stream URL", () => {
  it("pins the video player stream to the measured clip kind", async () => {
    const { container } = renderCoachStage([makeShot(1, "cand-7")]);
    await waitFor(() => {
      expect(container.querySelector("video")?.src).toContain("/trim/");
    });
    // A single take shares its source across stages; the URL names this one.
    expect(container.querySelector("video")?.src).toMatch(/#s1$/);
  });
});

describe("Coach player source", () => {
  const trimCoach = (overrides: Partial<CoachVideoEntry>): CoachStageResponse => ({
    ...makeCoach([makeShot(1, "c1")]),
    videos: [{ path: "trimmed/stage1.mp4", role: "primary", beep_in_clip: 5, kind: "trim", trim_version: "t1", scrub_version: "s1", ...overrides }],
  });

  beforeEach(() => {
    vi.mocked(api.getProject).mockResolvedValue(PROJECT as never);
  });

  it("streams the scrub rendition when the trim has a fresh one", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(trimCoach({}));
    const { container } = renderCoachRoute();
    await screen.findByRole("region", { name: "Shots" });
    expect(container.querySelector("video")?.getAttribute("src")).toContain("/scrub/");
  });

  it("falls back to the trim after the rendition errors", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(trimCoach({}));
    const { container } = renderCoachRoute();
    await screen.findByRole("region", { name: "Shots" });
    fireEvent.error(container.querySelector("video")!);
    await waitFor(() => expect(container.querySelector("video")?.getAttribute("src")).toContain("/trim/"));
  });

  it("a source-kind primary is left alone", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(trimCoach({ kind: "source", trim_version: null, scrub_version: null }));
    const { container } = renderCoachRoute();
    await screen.findByRole("region", { name: "Shots" });
    expect(container.querySelector("video")?.getAttribute("src")).toContain("/source/");
  });

  it("a web-kind primary streams the rendition and never asks for scrub", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(trimCoach({ kind: "web" }));
    const { container } = renderCoachRoute();
    await screen.findByRole("region", { name: "Shots" });
    expect(container.querySelector("video")?.getAttribute("src")).toContain("/web/");
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
