/**
 * BeepStep with the real BeepPreview and BeepTimeline: the preview's own
 * error / Retry swaps its <video> without BeepStep or the band rendering
 * for any other reason, and the band's playhead must follow the new
 * element from then on.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import type { BeepQueueItem, PeaksResult } from "@/lib/api";
import { resetTimelinePrefsForTests } from "@/lib/timelinePrefs";
import * as hook from "@/lib/useBeepQueue";

import { BeepStep } from "./BeepStep";

vi.mock("@/lib/useBeepQueue", async (orig) => ({
  ...(await orig<typeof import("@/lib/useBeepQueue")>()),
  useBeepQueue: vi.fn(),
}));
vi.mock("@/lib/api", () => ({
  api: {
    videoStreamUrl: () => "/proxy.mp4",
    getVideoPeaks: vi.fn(),
    getBeepQueue: vi.fn(),
  },
}));

const { api } = await import("@/lib/api");

const VIEWPORT = 1000;

const ITEM: BeepQueueItem = {
  slug: "alice",
  shooter_name: "Alice",
  stage_number: 10,
  stage_name: "B3",
  role: "primary",
  video_id: "v1",
  video_path: "raw/x.mp4",
  beep_time: 0,
  beep_confidence: 0.42,
  beep_reviewed: false,
  status: "low_confidence",
  alt_candidates: [],
  proxy_ready: true,
  snippet_ready: false,
  trim_stale: false,
};

const PEAKS: PeaksResult = {
  duration: 10,
  sample_rate: 8000,
  bins: 100,
  peaks: new Array(100).fill(0.1),
  beep_time: 0,
  trimmed: false,
};

function queueState(item: BeepQueueItem = ITEM) {
  const ITEM = item;
  return {
    data: { total_items: 1, pending_count: 1, confirmed_count: 0, origin: "local", stages: [] },
    flatItems: [ITEM],
    pendingItems: [ITEM],
    active: ITEM,
    activeKey: null,
    setActiveKey: vi.fn(),
    isMirror: false,
    editDenied: false,
    busy: false,
    error: null,
    setError: vi.fn(),
    redetecting: false,
    redetectPct: null,
    reload: vi.fn(),
    confirm: vi.fn(),
    redetect: vi.fn(),
    skip: vi.fn(),
    prevItem: vi.fn(),
    nextItem: vi.fn(),
  } as unknown as ReturnType<typeof hook.useBeepQueue>;
}

beforeEach(() => {
  vi.mocked(api.getVideoPeaks).mockResolvedValue(PEAKS);
  vi.mocked(api.getBeepQueue).mockResolvedValue({ stages: [] } as never);
  vi.mocked(hook.useBeepQueue).mockReturnValue(queueState());
  vi.spyOn(Element.prototype, "clientWidth", "get").mockReturnValue(VIEWPORT);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    const w = this.dataset.testid === "timeline-content" ? parseFloat(this.style.width) || VIEWPORT : VIEWPORT;
    return { width: w, height: 40, left: 0, top: 0, right: w, bottom: 40, x: 0, y: 0, toJSON: () => ({}) };
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
  resetTimelinePrefsForTests();
});

function renderStep() {
  render(
    <ConfirmProvider>
      <BeepStep
        slug="alice"
        stageNumber={10}
        onConfirmed={vi.fn()}
        header={{ ordinal: "10", title: "B3", sub: "Alice" }}
        mediaOnDesktop={false}
      />
    </ConfirmProvider>,
  );
}

describe("BeepStep preview remount", () => {
  it("the remounted <video> is parked at the selected candidate's time once its metadata lands", async () => {
    vi.mocked(hook.useBeepQueue).mockReturnValue(
      queueState({ ...ITEM, alt_candidates: [{ time: 3, confidence: 0.2 }] }),
    );
    renderStep();
    await screen.findByTestId("waveform-track");
    fireEvent.click(screen.getAllByRole("radio")[1]);

    const first = screen.getByTitle("Space toggles play/pause") as HTMLVideoElement;
    fireEvent.error(first);
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    const second = screen.getByTitle("Space toggles play/pause") as HTMLVideoElement;
    expect(second).not.toBe(first);
    expect(second.currentTime).toBe(0);

    act(() => {
      fireEvent(second, new Event("loadedmetadata"));
    });
    expect(second.currentTime).toBe(3);
  });

  it("the band follows the new <video> after the preview's error / Retry remount", async () => {
    render(
      <ConfirmProvider>
        <BeepStep
          slug="alice"
          stageNumber={10}
          onConfirmed={vi.fn()}
          header={{ ordinal: "10", title: "B3", sub: "Alice" }}
          mediaOnDesktop={false}
        />
      </ConfirmProvider>,
    );
    await screen.findByTestId("waveform-track");
    const first = screen.getByTitle("Space toggles play/pause") as HTMLVideoElement;

    fireEvent.error(first);
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    const second = screen.getByTitle("Space toggles play/pause") as HTMLVideoElement;
    expect(second).not.toBe(first);

    act(() => {
      second.currentTime = 4;
      fireEvent(second, new Event("timeupdate"));
    });
    expect(screen.getByTestId("timeline-playhead")).toHaveStyle({ left: "400px" });

    // Native playback state too: the band's play state follows the new element.
    Object.defineProperty(second, "paused", { get: () => false, configurable: true });
    act(() => {
      fireEvent(second, new Event("play"));
    });
    act(() => {
      second.currentTime = 6;
      fireEvent(second, new Event("timeupdate"));
    });
    expect(screen.getByTestId("timeline-playhead")).toHaveStyle({ left: "600px" });
  });
});
