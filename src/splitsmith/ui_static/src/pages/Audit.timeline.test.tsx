/**
 * Desktop Audit on the shared timeline band (#1352, PR 2): the waveform,
 * markers and anomaly pins sit on the full-width `Timeline` under a top row
 * of video (left) and shot list (right).
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import type { StageAudit } from "@/lib/api";
import { resetTimelinePrefsForTests } from "@/lib/timelinePrefs";
import { Audit } from "@/pages/Audit";

const ctx = vi.hoisted(() => ({
  value: {
    shooters: [{ slug: "alice", name: "Alice" }],
    origin: "local",
    capabilities: ["edit", "review"],
    jobs: [],
    refresh: vi.fn(),
  } as Record<string, unknown>,
}));
vi.mock("react-router-dom", async (orig) => ({
  ...(await orig<typeof import("react-router-dom")>()),
  useOutletContext: () => ctx.value,
}));

vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return { ...actual, useDeploymentMode: () => ({ mode: "local", resolved: true }) };
});

const DURATION = 22;
const BEEP = 1.0;
const VIEWPORT = 1000;

const doc = (): StageAudit => ({
  stage_number: 3,
  stage_name: "Stage 3",
  beep_time: BEEP,
  stage_time_seconds: 20.5,
  shots: [
    { shot_number: 1, candidate_number: 1, time: 2.0, ms_after_beep: 1000, source: "detected", id: "cand-1" },
    { shot_number: 2, candidate_number: 2, time: 2.4, ms_after_beep: 1400, source: "detected", id: "cand-2" },
  ],
  _candidates_pending_audit: {
    candidates: [
      { candidate_number: 1, time: 2.0, ms_after_beep: 1000, confidence: 0.9 },
      { candidate_number: 2, time: 2.4, ms_after_beep: 1400, confidence: 0.8 },
    ],
  },
  audit_events: [],
});

// time_seconds 20.5 against a last shot 1.4 s after the beep: the stage-time
// mismatch anomaly pins at beep + stage time. 1.4 matches and pins nothing.
const project = (timeSeconds = 20.5) => ({
  name: "M",
  competitor_name: "Alice",
  trim_pre_buffer_seconds: 5,
  stages: [
    {
      stage_number: 3,
      stage_name: "Stage 3",
      time_seconds: timeSeconds,
      videos: [
        {
          video_id: "v1",
          role: "primary",
          path: "raw/stage3.mp4",
          added_at: "2026-10-01T00:00:00Z",
          beep_time: 6.0,
          beep_source: "auto",
          beep_confidence: 0.99,
          beep_reviewed: true,
          processed: { beep: true, shot_detect: true, trim: true },
          trim_version: "18f2a-3e8",
        },
      ],
    },
  ],
});

const peaksResult = () => ({
  duration: DURATION,
  sample_rate: 48000,
  bins: 44,
  peaks: Array.from({ length: 44 }, () => 0.4),
  beep_time: BEEP,
  trimmed: true,
});

const apiMock = vi.hoisted(() => ({
  getProject: vi.fn(),
  getAutomation: vi.fn(),
  getStageAudit: vi.fn(),
  getStagePeaks: vi.fn(),
  saveStageAudit: vi.fn(),
  listJobs: vi.fn(async () => []),
  getScrubSettings: vi.fn(async () => ({ full_res_scrub: false })),
  setScrubSettings: vi.fn(),
}));
vi.mock("@/lib/api", async (orig) => {
  const actual = await orig<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, ...apiMock } };
});

function renderPage() {
  return render(
    <ConfirmProvider>
      <MemoryRouter initialEntries={["/match/m1/audit/alice/3"]}>
        <Routes>
          <Route path="/match/:matchId/audit/:slug/:stage" element={<Audit />} />
        </Routes>
      </MemoryRouter>
    </ConfirmProvider>,
  );
}

let widthSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.clearAllMocks();
  resetTimelinePrefsForTests();
  Element.prototype.scrollIntoView = () => {};
  // The band measures its viewport through clientWidth; jsdom reports 0.
  widthSpy = vi.spyOn(Element.prototype, "clientWidth", "get").mockReturnValue(VIEWPORT);
  apiMock.getProject.mockResolvedValue(project());
  apiMock.getAutomation.mockResolvedValue({ settings: { beep_low_confidence_threshold: 0.97 } });
  apiMock.getStageAudit.mockResolvedValue(doc());
  apiMock.getStagePeaks.mockResolvedValue(peaksResult());
  apiMock.saveStageAudit.mockImplementation(async (_s: string, _n: number, p: StageAudit) => p);
});

afterEach(() => {
  widthSpy.mockRestore();
});

// beep + stage time: where the timer stopped, the mismatch pin's time.
const PIN_T = BEEP + 20.5;

const audioTrack = (band: HTMLElement) => within(band).getByTestId("audit-audio-track");

describe("Audit on the timeline band", () => {
  it("draws Flags, Audio and the markers in the audio track, outside the 380 px grid", async () => {
    renderPage();
    const band = await screen.findByTestId("timeline");
    expect(within(band).getByText("Flags")).toBeInTheDocument();
    // The pins' layer paints over the audio row below it.
    expect(within(band).getByTestId("audit-flags-track")).toHaveClass("z-10");
    expect(within(band).getByText("Audio")).toBeInTheDocument();
    const track = audioTrack(band);
    await waitFor(() => expect(track.querySelectorAll("[data-audit-marker]").length).toBe(2));
    // The pin sits in the band at its content x (the band scrolls it).
    const pinX = () => parseFloat(within(band).getByRole("button", { name: /Timer stopped/ }).style.left);
    expect(pinX()).toBeCloseTo((PIN_T / DURATION) * VIEWPORT, 3);
    // Not inside the top row's grid.
    expect(band.closest('[class*="lg:grid-cols-[minmax(0,1fr)_380px]"]')).toBeNull();
    // MarkerLayer's parent (what it measures for drags) is the audio track wrapper.
    expect(track.querySelector("[data-marker-layer]")?.parentElement).toBe(track);
  });

  it("keeps a pin at its time when zoomed, and clicking it seeks there", async () => {
    renderPage();
    const band = await screen.findByTestId("timeline");
    const pin = () => within(band).getByRole("button", { name: /Timer stopped/ });
    fireEvent.click(within(band).getByRole("button", { name: "Zoom in" }));
    fireEvent.click(within(band).getByRole("button", { name: "Zoom in" }));
    expect(parseFloat(pin().style.left)).toBeCloseTo((PIN_T / DURATION) * VIEWPORT * 2.25, 3);
    fireEvent.click(pin());
    const playhead = within(band).getByTestId("timeline-playhead");
    expect(parseFloat(playhead.style.left)).toBeCloseTo((PIN_T / DURATION) * VIEWPORT * 2.25, 3);
  });

  it("omits the Flags track when there are no pinned anomalies", async () => {
    apiMock.getProject.mockResolvedValue(project(1.4));
    renderPage();
    const band = await screen.findByTestId("timeline");
    await waitFor(() => expect(audioTrack(band).querySelectorAll("[data-audit-marker]").length).toBe(2));
    expect(within(band).queryByText("Flags")).toBeNull();
  });

  it("puts the video before the shot list in the top row", async () => {
    renderPage();
    await screen.findByTestId("timeline");
    const grid = document.querySelector('[class*="lg:grid-cols-[minmax(0,1fr)_380px]"]') as HTMLElement;
    expect(grid).not.toBeNull();
    const cols = Array.from(grid.children);
    expect(cols).toHaveLength(2);
    expect(cols[0].querySelector("video")).not.toBeNull();
    expect(cols[1].querySelector("video")).toBeNull();
    expect(cols[1].textContent).toMatch(/Shot/i);
    // The video fills its wide cell instead of the old fixed 380 px column.
    // The camera column is the cell itself.
    expect(cols[0].tagName).toBe("ASIDE");
    expect((cols[0] as HTMLElement).style.width).toBe("");
    expect(within(cols[0] as HTMLElement).getByTestId("cam-primary-tile")).toHaveClass("aspect-video");
  });

  it("bounds the top row on lg so the band stays on a laptop screen", async () => {
    renderPage();
    await screen.findByTestId("timeline");
    const grid = document.querySelector('[class*="lg:grid-cols-[minmax(0,1fr)_380px]"]') as HTMLElement;
    // The row's height, not the shot list's length, sets where the band
    // starts: one row track that may shrink below its content.
    expect(grid).toHaveClass("lg:h-[max(300px,calc(100dvh-560px))]", "lg:grid-rows-[minmax(0,1fr)]");
    expect(grid).not.toHaveClass("lg:items-start");
    const [video, shots] = Array.from(grid.children) as HTMLElement[];
    expect(video).toHaveClass("lg:h-full");
    // The video's height follows the tile (letterboxed), not its width.
    expect(video.querySelector("video")).toHaveClass("h-full", "w-full", "object-contain");
    expect(video.querySelector("video")).not.toHaveClass("h-auto");
    // The shot list fills its column and scrolls inside it.
    expect(shots).toHaveAttribute("aria-label", "Shots");
    expect(shots).toHaveClass("lg:h-full", "min-h-0", "flex-col");
    expect(within(shots).getByTestId("shot-list-scroll")).toHaveClass("min-h-0", "flex-1", "overflow-y-auto");
  });

  it("raises the top row's floor when the stage has a second camera", async () => {
    const p = project();
    const primary = p.stages[0].videos[0];
    p.stages[0].videos.push({ ...primary, video_id: "v2", role: "secondary", path: "raw/stage3-b.mp4" });
    apiMock.getProject.mockResolvedValue(p);
    renderPage();
    await screen.findByTestId("timeline");
    await screen.findByText(/Synced to primary beep/i);
    const grid = document.querySelector('[class*="lg:grid-cols-[minmax(0,1fr)_380px]"]') as HTMLElement;
    // The secondary strip and the sync row cost about 150 px, so the floor
    // grows by that much and the primary tile keeps a usable height.
    expect(grid).toHaveClass("lg:h-[max(450px,calc(100dvh-560px))]");
    expect(grid).not.toHaveClass("lg:h-[max(300px,calc(100dvh-560px))]");
  });

  it("adds a manual marker on a double-click in the audio row", async () => {
    renderPage();
    const band = await screen.findByTestId("timeline");
    const track = audioTrack(band);
    await waitFor(() => expect(track.querySelectorAll("[data-audit-marker]").length).toBe(2));
    // The band's row is the track wrapper's parent; the page's real handler adds.
    fireEvent.doubleClick(track.parentElement!, { clientX: 500, shiftKey: true });
    await waitFor(() => expect(track.querySelectorAll("[data-audit-marker]").length).toBe(3));
    // A double-click on a marker adds nothing.
    fireEvent.doubleClick(track.querySelector("[data-audit-marker]")!, { clientX: 100 });
    expect(track.querySelectorAll("[data-audit-marker]").length).toBe(3);
    // Nor does one on the marker's own glyph (an <svg>, not an HTMLElement) (I1).
    const marker = track.querySelector("[data-audit-marker]")!;
    fireEvent.doubleClick(marker.querySelector("svg")!, { clientX: 100 });
    expect(track.querySelectorAll("[data-audit-marker]").length).toBe(3);
  });

  describe("leading-edge snapping on add", () => {
    // The snap fetch asks for 1 ms bins (22 000 over 22 s). One shot whose
    // rise starts at bin 11010 (11.010 s), inside the 25 ms window around
    // 11.0 s, peaking 10 ms later.
    const RISE_BIN = 11010;
    const snapPeaksResult = (bins: number) => ({
      ...peaksResult(),
      bins,
      peaks: Array.from({ length: bins }, (_, i) =>
        i < RISE_BIN ? 0.01 : i < RISE_BIN + 10 ? 0.01 + (i - RISE_BIN + 1) * 0.089 : 0.01,
      ),
    });

    async function addAt(shiftKey: boolean): Promise<number> {
      apiMock.getStagePeaks.mockImplementation(async (_s: string, _n: number, bins: number) =>
        bins === 1500 ? peaksResult() : snapPeaksResult(bins),
      );
      renderPage();
      const band = await screen.findByTestId("timeline");
      const track = audioTrack(band);
      await waitFor(() => expect(track.querySelectorAll("[data-audit-marker]").length).toBe(2));
      await waitFor(() => expect(apiMock.getStagePeaks).toHaveBeenCalledWith("alice", 3, 22000));
      await act(async () => {});
      vi.spyOn(within(band).getByTestId("timeline-content"), "getBoundingClientRect").mockReturnValue(
        new DOMRect(0, 0, VIEWPORT, 140),
      );
      // clientX 500 of 1000 px over 22 s: 11.0 s under the pointer.
      fireEvent.doubleClick(track.parentElement!, { clientX: 500, shiftKey });
      const added = await waitFor(() => {
        const m = track.querySelector<HTMLElement>('[data-audit-marker-id^="manual-"]');
        expect(m).not.toBeNull();
        return m!;
      });
      return (parseFloat(added.style.left) / 100) * DURATION;
    }

    it("snaps to the shot's leading edge without Shift at fit zoom", async () => {
      // 1000 px over 22 s is 45 px/s, coarser than 2 ms per pixel.
      expect(await addAt(false)).toBeCloseTo(RISE_BIN * 0.001, 6);
    });

    it("keeps the raw time under the pointer with Shift", async () => {
      expect(await addAt(true)).toBeCloseTo(11.0, 6);
    });
  });

  it("hides the beep and timer-stop lines with the beep filter off; the ruler stays on the beep", async () => {
    renderPage();
    const band = await screen.findByTestId("timeline");
    expect(within(band).getByTestId("wave-beep")).toBeInTheDocument();
    expect(within(band).getByTestId("wave-timer-stop")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /show 2 detected/ }));
    fireEvent.click(screen.getByLabelText("Beep marker"));
    expect(within(band).queryByTestId("wave-beep")).toBeNull();
    expect(within(band).queryByTestId("wave-timer-stop")).toBeNull();
    const zero = within(within(band).getByTestId("timeline-ruler")).getByText("0");
    const left = parseFloat((zero.parentElement as HTMLElement).style.left);
    expect(left).toBeCloseTo((BEEP / DURATION) * VIEWPORT, 3);
  });

  it("renders no band, and does not crash, before peaks resolve", async () => {
    let resolvePeaks: (v: ReturnType<typeof peaksResult>) => void = () => {};
    apiMock.getStagePeaks.mockImplementation(
      () => new Promise((resolve) => {
        resolvePeaks = resolve;
      }),
    );
    renderPage();
    await screen.findByText(/Computing waveform/);
    expect(screen.queryByTestId("timeline")).toBeNull();
    await act(async () => resolvePeaks(peaksResult()));
    expect(await screen.findByTestId("timeline")).toBeInTheDocument();
  });

  it("zooms the band once per + press, with one set of zoom controls", async () => {
    renderPage();
    const band = await screen.findByTestId("timeline");
    fireEvent.keyDown(window, { key: "+" });
    expect(within(band).getByText("1.5x")).toBeInTheDocument();
    expect(screen.queryByText("2.3x")).toBeNull();
    expect(screen.getAllByRole("button", { name: "Fit" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Zoom in" })).toHaveLength(1);
  });

  it("never zooms out below Fit from the keyboard", async () => {
    // The page's old zoom branch ran first on window and set 1 / 1.5 here
    // (readout "0.7x"); the band alone stays at Fit.
    renderPage();
    const band = await screen.findByTestId("timeline");
    fireEvent.keyDown(window, { key: "-" });
    expect(within(band).getByRole("button", { name: "Fit" })).toHaveAttribute("aria-pressed", "true");
    expect(within(band).queryByText(/x$/)).toBeNull();
    expect(within(band).getByTestId("timeline-content").style.width).toBe(`${VIEWPORT}px`);
  });
});
