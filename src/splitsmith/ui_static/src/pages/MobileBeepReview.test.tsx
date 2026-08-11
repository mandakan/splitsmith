import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { MobileBeepReview } from "./MobileBeepReview";
import { DESTRUCTIVE_RERUN_WARNING } from "@/lib/useBeepQueue";
import * as hook from "@/lib/useBeepQueue";
import type { BeepQueueItem } from "@/lib/api";

vi.mock("@/lib/useBeepQueue", async (orig) => ({
  ...(await orig<typeof import("@/lib/useBeepQueue")>()),
  useBeepQueue: vi.fn(),
}));
vi.mock("@/components/BeepSection", () => ({
  BeepWaveformPicker: () => <div data-testid="waveform-picker" />,
}));
vi.mock("@/lib/api", () => ({
  api: {
    beepSnippetAudioUrl: () => "/snippet.m4a",
    getBeepSnippetPeaks: vi.fn().mockResolvedValue({
      snippet_start: 1,
      duration: 10,
      sample_rate: 8000,
      bins: 4,
      peaks: [0.1, 0.9, 0.2, 0.1],
      beep_time: 2,
      candidates: [],
      input_hash: "abc",
    }),
    videoStreamUrl: () => "/proxy.mp4",
  },
}));

const item = (over: Partial<BeepQueueItem> = {}): BeepQueueItem => ({
  slug: "alice",
  shooter_name: "Alice",
  stage_number: 1,
  stage_name: "S1",
  role: "primary",
  video_id: "v1",
  video_path: "videos/s1.mp4",
  beep_time: 2,
  beep_confidence: 0.4,
  beep_reviewed: false,
  status: "low_confidence",
  alt_candidates: [],
  proxy_ready: false,
  snippet_ready: true,
  trim_stale: false,
  ...over,
});

const hookState = (over: Record<string, unknown> = {}) => ({
  data: { total_items: 2, pending_count: 2, confirmed_count: 0, origin: "desktop", stages: [] },
  flatItems: [item(), item({ video_id: "v2" })],
  pendingItems: [item(), item({ video_id: "v2" })],
  active: item(),
  activeKey: "alice::1::v1",
  setActiveKey: vi.fn(),
  isMirror: true,
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
  ...over,
});

describe("MobileBeepReview", () => {
  beforeEach(() => vi.clearAllMocks());

  it("mirror item with a snippet renders the audio player, no video, no Re-detect", () => {
    vi.mocked(hook.useBeepQueue).mockReturnValue(hookState() as unknown as ReturnType<typeof hook.useBeepQueue>);
    render(<MobileBeepReview />);
    expect(screen.getByText(/video available on desktop/i)).toBeInTheDocument();
    expect(document.querySelector("video")).toBeNull();
    expect(screen.queryByRole("button", { name: /re-detect/i })).toBeNull();
    expect(screen.getByText("1 of 2")).toBeInTheDocument();
  });

  it("hosted-native item with a proxy renders video + waveform picker", () => {
    vi.mocked(hook.useBeepQueue).mockReturnValue(
      hookState({
        active: item({ proxy_ready: true, snippet_ready: false }),
        isMirror: false,
      }) as unknown as ReturnType<typeof hook.useBeepQueue>,
    );
    render(<MobileBeepReview />);
    expect(document.querySelector("video")).not.toBeNull();
    expect(screen.getByTestId("waveform-picker")).toBeInTheDocument();
  });

  it("no media renders the desktop fallback with Confirm disabled", () => {
    vi.mocked(hook.useBeepQueue).mockReturnValue(
      hookState({
        active: item({ proxy_ready: false, snippet_ready: false }),
      }) as unknown as ReturnType<typeof hook.useBeepQueue>,
    );
    render(<MobileBeepReview />);
    expect(screen.getByText(/review this beep on desktop/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /confirm beep/i })).toBeDisabled();
  });

  it("confirming a placed draft goes through the destructive warning sheet", async () => {
    const state = hookState();
    vi.mocked(hook.useBeepQueue).mockReturnValue(state as unknown as ReturnType<typeof hook.useBeepQueue>);
    render(<MobileBeepReview />);
    // Place a draft via the +10 ms nudge on the existing beep (no alt
    // candidates on this fixture item, so the nudge is the reachable
    // gesture - see brief note on adapting this to the real component).
    fireEvent.click(screen.getByRole("button", { name: /\+10 ms/i }));
    fireEvent.click(screen.getByRole("button", { name: /apply new time/i }));
    expect(screen.getByText(DESTRUCTIVE_RERUN_WARNING)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /apply and confirm/i }));
    expect(state.confirm).toHaveBeenCalledWith(
      expect.objectContaining({ video_id: "v1" }),
      expect.any(Number),
    );
  });

  it("re-detecting through the sheet clears a placed draft (finding 1)", async () => {
    // useBeepQueue.redetect() re-selects the SAME activeKey, so the
    // `useEffect(() => setDraft(null), [q.activeKey])` in the component
    // never fires on its own - the redetect sheet's onConfirm must clear
    // the draft itself, or a stale draft survives into the next Confirm.
    const state = hookState({ isMirror: false }); // Re-detect only renders off-mirror
    vi.mocked(hook.useBeepQueue).mockReturnValue(state as unknown as ReturnType<typeof hook.useBeepQueue>);
    render(<MobileBeepReview />);

    fireEvent.click(screen.getByRole("button", { name: /\+10 ms/i }));
    expect(screen.getByRole("button", { name: /apply new time and confirm/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^re-detect$/i }));
    expect(screen.getByText(DESTRUCTIVE_RERUN_WARNING)).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: /^re-detect$/i }));

    expect(state.redetect).toHaveBeenCalledWith(expect.objectContaining({ video_id: "v1" }));
    expect(screen.getByRole("button", { name: /^confirm beep$/i })).toBeInTheDocument();
  });
});
