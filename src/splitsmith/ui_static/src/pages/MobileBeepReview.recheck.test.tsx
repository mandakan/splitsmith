/**
 * The recheck pass over secondary beeps on the phone (2026-10-14): the beep
 * review pager walks every secondary camera, confirmed or not, one card at
 * a time. Looks right moves on writing nothing; a moved beep is applied,
 * which re-cuts only that camera's clip, then the pass moves on.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { BeepQueueItem } from "@/lib/api";
import * as hook from "@/lib/useBeepQueue";
import { DESTRUCTIVE_RERUN_WARNING } from "@/lib/useBeepQueue";

import { MobileBeepReview } from "./MobileBeepReview";

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
  READ_ONLY_MIRROR_MESSAGE: "read only",
}));

const item = (over: Partial<BeepQueueItem> = {}): BeepQueueItem => ({
  slug: "alice",
  shooter_name: "Alice",
  stage_number: 1,
  stage_name: "S1",
  role: "secondary",
  video_id: "v1",
  video_path: "videos/IMG_1.MOV",
  beep_time: 2,
  beep_confidence: 0.9,
  beep_reviewed: true,
  status: "confirmed",
  alt_candidates: [],
  proxy_ready: true,
  snippet_ready: false,
  trim_stale: false,
  ...over,
});

const PRIMARY = item({ role: "primary", video_id: "p1", video_path: "videos/head1.mp4" });
const SEC1 = item();
const SEC2 = item({ stage_number: 2, video_id: "v2", video_path: "videos/IMG_2.MOV" });

const hookState = (over: Record<string, unknown> = {}) => ({
  data: { total_items: 3, pending_count: 0, confirmed_count: 3, origin: "local", stages: [] },
  flatItems: [PRIMARY, SEC1, SEC2],
  pendingItems: [],
  active: null,
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
  confirm: vi.fn().mockResolvedValue(undefined),
  redetect: vi.fn(),
  skip: vi.fn(),
  prevItem: vi.fn(),
  nextItem: vi.fn(),
  ...over,
});

function renderPager(state: ReturnType<typeof hookState>, props: Record<string, unknown> = {}) {
  vi.mocked(hook.useBeepQueue).mockReturnValue(state as unknown as ReturnType<typeof hook.useBeepQueue>);
  render(<MobileBeepReview recheck {...props} />);
}

describe("MobileBeepReview rechecking the secondary beeps", () => {
  beforeEach(() => vi.clearAllMocks());

  it("pages over the secondaries only, Looks right moving on and writing nothing", () => {
    const state = hookState();
    renderPager(state);
    expect(screen.getByText("Secondary beep 1 of 2")).toBeInTheDocument();
    expect(screen.getByText(/stage 1/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Looks right, next" }));
    expect(screen.getByText("Secondary beep 2 of 2")).toBeInTheDocument();
    expect(state.confirm).not.toHaveBeenCalled();
  });

  it("starts at the camera named by start", () => {
    renderPager(hookState(), { start: "alice::2::v2" });
    expect(screen.getByText("Secondary beep 2 of 2")).toBeInTheDocument();
  });

  it("a moved beep is applied after a warning that only this camera's clip is re-cut, then the pass moves on", async () => {
    const state = hookState();
    renderPager(state);
    fireEvent.click(screen.getByRole("button", { name: "+10 ms" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply new time and confirm" }));
    expect(screen.getByText(/re-cuts this camera's clip/)).toBeInTheDocument();
    expect(screen.queryByText(DESTRUCTIVE_RERUN_WARNING)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Apply and confirm" }));
    const [confirmed, time] = vi.mocked(state.confirm).mock.calls[0] as [BeepQueueItem, number];
    expect(confirmed).toBe(SEC1);
    expect(time).toBeCloseTo(2.01, 6);
    expect(await screen.findByText("Secondary beep 2 of 2")).toBeInTheDocument();
  });

  it("after the last secondary it says the pass is done", () => {
    renderPager(hookState(), { start: "alice::2::v2" });
    fireEvent.click(screen.getByRole("button", { name: "Looks right, next" }));
    expect(screen.getByText("Every secondary beep checked.")).toBeInTheDocument();
  });

  it("offers the pass when every beep is confirmed", () => {
    vi.mocked(hook.useBeepQueue).mockReturnValue(
      hookState() as unknown as ReturnType<typeof hook.useBeepQueue>,
    );
    render(<MobileBeepReview recheckHref="/match/m1/beep-review?recheck=secondaries" />);
    expect(screen.getByRole("link", { name: "Recheck secondary beeps" })).toHaveAttribute(
      "href",
      "/match/m1/beep-review?recheck=secondaries",
    );
  });

  it("Looks right moves on even when no media was pushed for the camera", () => {
    renderPager(
      hookState({
        flatItems: [PRIMARY, item({ proxy_ready: false, snippet_ready: false }), SEC2],
      }),
    );
    const next = screen.getByRole("button", { name: "Looks right, next" });
    expect(next).toBeEnabled();
    fireEvent.click(next);
    expect(screen.getByText("Secondary beep 2 of 2")).toBeInTheDocument();
  });
});

