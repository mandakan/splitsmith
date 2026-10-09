import { act, fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PeaksResult } from "@/lib/api";
import { resetTimelinePrefsForTests } from "@/lib/timelinePrefs";

import { BeepTimeline, type BeepCandidate } from "./BeepTimeline";

vi.mock("@/lib/api", () => ({
  api: { getVideoPeaks: vi.fn() },
}));

const { api } = await import("@/lib/api");

const VIEWPORT = 1000;

function peaksFixture(over: Partial<PeaksResult> = {}): PeaksResult {
  return {
    duration: 10,
    sample_rate: 8000,
    bins: 100,
    peaks: new Array(100).fill(0.1),
    beep_time: 0,
    trimmed: false,
    ...over,
  };
}

function Harness(props: {
  videoId: string;
  videoBeepTime?: number | null;
  draftSourceTime?: number | null;
  candidates?: BeepCandidate[];
  onPick?: (t: number) => void;
  onError?: (m: string) => void;
}) {
  const mediaRef = useRef<HTMLVideoElement>(null);
  return (
    <div>
      <video ref={mediaRef} data-testid="preview-video" />
      <BeepTimeline
        slug="alice"
        stageNumber={10}
        videoId={props.videoId}
        videoBeepTime={props.videoBeepTime ?? null}
        draftSourceTime={props.draftSourceTime ?? null}
        candidates={props.candidates ?? []}
        mediaRef={mediaRef}
        onPick={props.onPick ?? vi.fn()}
        onError={props.onError}
      />
    </div>
  );
}

/** The Audio row's own wrapper div: the one Timeline attaches its
 *  press-to-scrub pointer handlers to (two levels above the waveform
 *  canvas: WaveformTrack's own relative div, then Timeline's track div). */
function audioRow() {
  return screen.getByTestId("waveform-track").parentElement!.parentElement!;
}

beforeEach(() => {
  vi.mocked(api.getVideoPeaks).mockReset();
  vi.spyOn(Element.prototype, "clientWidth", "get").mockReturnValue(VIEWPORT);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    const w = this.dataset.testid === "timeline-content" ? parseFloat(this.style.width) || VIEWPORT : VIEWPORT;
    const left = this.dataset.testid === "timeline-content" ? -(screen.queryByTestId("timeline-host")?.scrollLeft ?? 0) : 0;
    return { width: w, height: 40, left, top: 0, right: left + w, bottom: 40, x: left, y: 0, toJSON: () => ({}) };
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
  resetTimelinePrefsForTests();
});

describe("BeepTimeline", () => {
  it('renders "Loading audio" then the band with Candidates and Audio rows', async () => {
    let resolve!: (p: PeaksResult) => void;
    vi.mocked(api.getVideoPeaks).mockReturnValue(new Promise((r) => (resolve = r)));
    render(<Harness videoId="v1" />);
    expect(screen.getByText("Loading audio")).toBeInTheDocument();

    await act(async () => resolve(peaksFixture()));

    expect(screen.queryByText("Loading audio")).not.toBeInTheDocument();
    expect(screen.getByText("Candidates")).toBeInTheDocument();
    expect(screen.getByText("Audio")).toBeInTheDocument();
    expect(screen.getByTestId("waveform-track")).toBeInTheDocument();
  });

  it("calls onError and renders \"No audio\" when the fetch fails", async () => {
    vi.mocked(api.getVideoPeaks).mockRejectedValue(new Error("boom"));
    const onError = vi.fn();
    render(<Harness videoId="v1" onError={onError} />);
    await screen.findByText("No audio");
    expect(onError).toHaveBeenCalledWith("boom");
  });

  describe("press-to-scrub on the Audio row", () => {
    it("picks the press time on a press and release, seeking the video live", async () => {
      vi.mocked(api.getVideoPeaks).mockResolvedValue(peaksFixture());
      const onPick = vi.fn();
      render(<Harness videoId="v1" onPick={onPick} />);
      await screen.findByTestId("waveform-track");
      const row = audioRow();
      const video = screen.getByTestId("preview-video") as HTMLVideoElement;

      fireEvent.pointerDown(row, { pointerId: 1, button: 0, clientX: 250 });
      fireEvent.pointerUp(row, { pointerId: 1, clientX: 250 });

      expect(onPick).toHaveBeenCalledTimes(1);
      expect(onPick.mock.calls[0][0]).toBeCloseTo(2.5, 2);
      expect(video.currentTime).toBeCloseTo(2.5, 2);
    });

    it("picks only once on a drag's release, not per move", async () => {
      vi.mocked(api.getVideoPeaks).mockResolvedValue(peaksFixture());
      const onPick = vi.fn();
      render(<Harness videoId="v1" onPick={onPick} />);
      await screen.findByTestId("waveform-track");
      const row = audioRow();

      fireEvent.pointerDown(row, { pointerId: 1, button: 0, clientX: 250 });
      onPick.mockClear();
      fireEvent.pointerMove(row, { pointerId: 1, clientX: 600 });
      fireEvent.pointerUp(row, { pointerId: 1, clientX: 600 });

      expect(onPick).toHaveBeenCalledTimes(1);
      expect(onPick.mock.calls[0][0]).toBeCloseTo(6.0, 2);
    });
  });

  it("converts clip time to source time by the video-beep/peaks-beep offset", async () => {
    vi.mocked(api.getVideoPeaks).mockResolvedValue(peaksFixture({ beep_time: 5.0 }));
    const onPick = vi.fn();
    render(<Harness videoId="v1" videoBeepTime={12.0} draftSourceTime={9.5} onPick={onPick} />);
    await screen.findByTestId("waveform-track");

    // offset = 12.0 - 5.0 = 7; draftSourceTime 9.5 is local 2.5, 25% across a 10 s clip.
    const beepLine = screen.getByTestId("wave-beep");
    expect(beepLine.style.left).toBe("25%");

    const row = audioRow();
    fireEvent.pointerDown(row, { pointerId: 1, button: 0, clientX: 250 });
    fireEvent.pointerUp(row, { pointerId: 1, clientX: 250 });
    expect(onPick).toHaveBeenCalledTimes(1);
    expect(onPick.mock.calls[0][0]).toBeCloseTo(9.5, 2);
  });

  it("clicking a candidate pin calls onPick with its source time, seeks the preview there, and marks the selected pin", async () => {
    vi.mocked(api.getVideoPeaks).mockResolvedValue(peaksFixture());
    const onPick = vi.fn();
    const candidates: BeepCandidate[] = [
      { time: 3, detected: false },
      { time: 9.5, detected: false },
    ];
    render(<Harness videoId="v1" draftSourceTime={9.5} candidates={candidates} onPick={onPick} />);
    await screen.findByTestId("waveform-track");

    const unselected = screen.getByRole("button", { name: "Candidate 3.00 s" });
    const selected = screen.getByRole("button", { name: "Candidate 9.50 s" });
    expect(selected).toHaveAttribute("aria-pressed", "true");
    expect(unselected).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(unselected);
    expect(onPick).toHaveBeenCalledWith(3);
    const video = screen.getByTestId("preview-video") as HTMLVideoElement;
    expect(video.currentTime).toBeCloseTo(3, 2);
  });

  it("clamps a pick to >= 0 source seconds when the offset would carry it negative", async () => {
    // offset = videoBeepTime(0) - peaks.beep_time(5) = -5; a press at
    // local 1 s would otherwise pick -4.
    vi.mocked(api.getVideoPeaks).mockResolvedValue(peaksFixture({ beep_time: 5.0 }));
    const onPick = vi.fn();
    render(<Harness videoId="v1" videoBeepTime={0} onPick={onPick} />);
    await screen.findByTestId("waveform-track");
    const row = audioRow();

    fireEvent.pointerDown(row, { pointerId: 1, button: 0, clientX: 100 });
    fireEvent.pointerUp(row, { pointerId: 1, clientX: 100 });

    expect(onPick).toHaveBeenCalledTimes(1);
    expect(onPick.mock.calls[0][0]).toBe(0);
  });

  describe("parking the preview video", () => {
    it("parks at the detected beep once peaks resolve, and re-parks on a videoId switch", async () => {
      vi.mocked(api.getVideoPeaks).mockImplementation((_s: string, _n: number, videoId: string) =>
        Promise.resolve(
          videoId === "v1"
            ? peaksFixture({ duration: 10, beep_time: 4 })
            : peaksFixture({ duration: 20, beep_time: 3 }),
        ),
      );
      const { rerender } = render(<Harness videoId="v1" />);
      await screen.findByTestId("waveform-track");
      const video = screen.getByTestId("preview-video") as HTMLVideoElement;
      expect(video.currentTime).toBeCloseTo(4, 2);

      rerender(<Harness videoId="v2" />);
      await screen.findByTestId("waveform-track");
      expect(video.currentTime).toBeCloseTo(3, 2);
    });

    it("parks at the draft's local time, not the detected beep, when a draft exists", async () => {
      vi.mocked(api.getVideoPeaks).mockResolvedValue(peaksFixture({ beep_time: 5.0 }));
      render(<Harness videoId="v1" videoBeepTime={12.0} draftSourceTime={9.5} />);
      await screen.findByTestId("waveform-track");
      const video = screen.getByTestId("preview-video") as HTMLVideoElement;
      // offset = 12 - 5 = 7; draft 9.5 -> local 2.5.
      expect(video.currentTime).toBeCloseTo(2.5, 2);
    });

    it("does not re-park on an unrelated re-render (only once per peaks load)", async () => {
      vi.mocked(api.getVideoPeaks).mockResolvedValue(peaksFixture({ beep_time: 4 }));
      const { rerender } = render(<Harness videoId="v1" onPick={vi.fn()} />);
      await screen.findByTestId("waveform-track");
      const video = screen.getByTestId("preview-video") as HTMLVideoElement;
      expect(video.currentTime).toBeCloseTo(4, 2);

      // The operator scrubs away from the parked position...
      const row = audioRow();
      fireEvent.pointerDown(row, { pointerId: 1, button: 0, clientX: 700 });
      fireEvent.pointerUp(row, { pointerId: 1, clientX: 700 });
      expect(video.currentTime).toBeCloseTo(7, 2);

      // ...and an unrelated re-render (same videoId) must not yank it back.
      rerender(<Harness videoId="v1" onPick={vi.fn()} />);
      expect(video.currentTime).toBeCloseTo(7, 2);
    });
  });

  describe("switching videoId", () => {
    it("refetches, and a slow response for the superseded video is ignored once it lands", async () => {
      let resolveV1!: (p: PeaksResult) => void;
      let resolveV2!: (p: PeaksResult) => void;
      vi.mocked(api.getVideoPeaks).mockImplementation((_slug: string, _stage: number, videoId: string) => {
        if (videoId === "v1") return new Promise((r) => (resolveV1 = r));
        return new Promise((r) => (resolveV2 = r));
      });

      // draftSourceTime 5 on a 10 s clip (v1) is 50 %; on a 20 s clip (v2)
      // it is 25 % -- two different, checkable positions.
      const { rerender } = render(<Harness videoId="v1" draftSourceTime={5} />);
      expect(api.getVideoPeaks).toHaveBeenCalledTimes(1);

      rerender(<Harness videoId="v2" draftSourceTime={5} />);
      expect(screen.getByText("Loading audio")).toBeInTheDocument();
      expect(api.getVideoPeaks).toHaveBeenCalledTimes(2);

      await act(async () => resolveV2(peaksFixture({ duration: 20 })));
      await screen.findByTestId("wave-beep");
      expect(screen.getByTestId("wave-beep").style.left).toBe("25%");

      // The stale v1 response lands after v2's -- it must not overwrite it.
      await act(async () => resolveV1(peaksFixture({ duration: 10 })));
      expect(screen.getByTestId("wave-beep").style.left).toBe("25%");
    });

    it("resets the band to Fit", async () => {
      vi.mocked(api.getVideoPeaks).mockImplementation(
        (_slug: string, _stage: number, videoId: string) =>
          Promise.resolve(peaksFixture({ duration: videoId === "v1" ? 10 : 20 })),
      );
      const { rerender } = render(<Harness videoId="v1" />);
      await screen.findByTestId("waveform-track");

      fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
      expect(screen.getByRole("button", { name: "Fit" })).toHaveAttribute("aria-pressed", "false");

      rerender(<Harness videoId="v2" />);
      await screen.findByTestId("waveform-track");
      expect(screen.getByRole("button", { name: "Fit" })).toHaveAttribute("aria-pressed", "true");
    });

    it("re-attaches its media listeners when the preview element behind mediaRef is remounted (a camera switch)", async () => {
      vi.mocked(api.getVideoPeaks).mockResolvedValue(peaksFixture({ duration: 10, beep_time: 0 }));

      // BeepStep keys the preview <video> per item (keyOf(item)), so a
      // camera switch unmounts the old element and mounts a new one --
      // mediaRef.current changes without mediaRef itself changing and
      // without any prop BeepTimeline reads changing either.
      function RemountableHarness({ videoId }: { videoId: string }) {
        const mediaRef = useRef<HTMLVideoElement>(null);
        return (
          <div>
            <video key={videoId} ref={mediaRef} data-testid="preview-video" />
            <BeepTimeline
              slug="alice"
              stageNumber={10}
              videoId={videoId}
              videoBeepTime={null}
              draftSourceTime={null}
              candidates={[]}
              mediaRef={mediaRef}
              onPick={vi.fn()}
            />
          </div>
        );
      }

      const { rerender } = render(<RemountableHarness videoId="v1" />);
      await screen.findByTestId("waveform-track");
      const videoA = screen.getByTestId("preview-video");

      rerender(<RemountableHarness videoId="v2" />);
      await screen.findByTestId("waveform-track");
      const videoB = screen.getByTestId("preview-video");
      expect(videoB).not.toBe(videoA);

      (videoB as HTMLVideoElement).currentTime = 4;
      fireEvent(videoB, new Event("timeupdate"));

      expect(screen.getByTestId("timeline-playhead")).toHaveStyle({ left: "400px" });
    });
  });
});
