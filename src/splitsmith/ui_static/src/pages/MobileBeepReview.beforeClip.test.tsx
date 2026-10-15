/**
 * A secondary camera that started recording during the beep (2026-10-15):
 * the card's "Beep starts before the clip" finds where the tone stops in
 * the clip and sets the beep that tone's length earlier, the length taken
 * from the stage's head cams (all shooters), so the beep lands before 0.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { BeepQueueItem } from "@/lib/api";
import * as hook from "@/lib/useBeepQueue";

import { MobileBeepReview } from "./MobileBeepReview";

vi.mock("@/lib/useBeepQueue", async (orig) => ({
  ...(await orig<typeof import("@/lib/useBeepQueue")>()),
  useBeepQueue: vi.fn(),
}));
vi.mock("@/components/BeepSection", () => ({ BeepWaveformPicker: () => <div /> }));

/** 1 ms bars over ``start``..``start + 3``: hum, and a tone from ``from``
 *  for ``length`` seconds (``from`` before ``start`` cuts it off). */
function snippet(start: number, from: number, length: number, beep: number) {
  const peaks = new Array(3000).fill(0.002);
  for (let i = 0; i < 3000; i++) {
    const t = start + i / 1000;
    if (t >= from && t < from + length) peaks[i] = 0.5;
    else if (t >= from + length && t < from + length + 0.1) peaks[i] = 0.03;
  }
  return { snippet_start: start, duration: 3, sample_rate: 8000, bins: 3000, peaks, beep_time: beep, candidates: [], input_hash: "x" };
}

const apiMock = vi.hoisted(() => ({ getBeepSnippetPeaks: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: {
    beepSnippetAudioUrl: () => "/snippet.m4a",
    videoStreamUrl: () => "/proxy.mp4",
    getBeepSnippetPeaks: apiMock.getBeepSnippetPeaks,
  },
  READ_ONLY_MIRROR_MESSAGE: "read only",
}));

const item = (over: Partial<BeepQueueItem>): BeepQueueItem => ({
  slug: "alice",
  shooter_name: "Alice",
  stage_number: 3,
  stage_name: "S3",
  role: "secondary",
  video_id: "sec",
  video_path: "videos/IMG_1.MOV",
  beep_time: 0,
  beep_confidence: 1,
  beep_reviewed: true,
  status: "confirmed",
  alt_candidates: [],
  proxy_ready: false,
  snippet_ready: true,
  trim_stale: false,
  ...over,
});

const SEC = item({});
// Two shooters' head cams on stage 3, each hearing a 0.300 s tone whole.
const HEAD_A = item({ role: "primary", video_id: "pa", beep_time: 5.0 });
const HEAD_B = item({ slug: "bob", shooter_name: "Bob", role: "primary", video_id: "pb", beep_time: 8.0 });

const state = (confirm = vi.fn().mockResolvedValue(undefined)) => ({
  data: { total_items: 3, pending_count: 0, confirmed_count: 3, origin: "desktop", stages: [] },
  flatItems: [HEAD_A, HEAD_B, SEC],
  pendingItems: [],
  active: null,
  activeKey: null,
  setActiveKey: vi.fn(),
  isMirror: true,
  editDenied: false,
  busy: false,
  error: null,
  setError: vi.fn(),
  redetecting: false,
  redetectPct: null,
  reload: vi.fn(),
  confirm,
  redetect: vi.fn(),
  skip: vi.fn(),
  prevItem: vi.fn(),
  nextItem: vi.fn(),
});

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.getBeepSnippetPeaks.mockImplementation(async (_slug: string, _stage: number, video: string) =>
    video === "pa"
      ? snippet(4.0, 5.0, 0.3, 5.0)
      : video === "pb"
        ? snippet(7.0, 8.0, 0.3, 8.0)
        : // The secondary started 0.1 s into the beep: its tone stops at 0.200 s.
          snippet(0, -0.1, 0.3, 0),
  );
});

describe("a beep that starts before the clip", () => {
  it("is the tone's end in the clip less the tone the head cams heard", async () => {
    const s = state();
    vi.mocked(hook.useBeepQueue).mockReturnValue(s as unknown as ReturnType<typeof hook.useBeepQueue>);
    render(<MobileBeepReview recheck />);
    fireEvent.click(await screen.findByRole("switch", { name: "Beep starts before the clip" }));
    await waitFor(() =>
      expect(screen.getByTestId("before-clip-line")).toHaveTextContent(
        /Beep at -0\.100 s · tone 0\.300 s from 2 head cams/,
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Apply new time and confirm" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply and confirm" }));
    await waitFor(() => expect(s.confirm).toHaveBeenCalled());
    const [confirmed, time] = s.confirm.mock.calls[0] as [BeepQueueItem, number];
    expect(confirmed).toBe(SEC);
    expect(time).toBeCloseTo(-0.1, 3);
  });

  it("is not offered for a head cam", async () => {
    const s = state();
    vi.mocked(hook.useBeepQueue).mockReturnValue(
      { ...s, flatItems: [HEAD_A], pendingItems: [HEAD_A], active: HEAD_A } as unknown as ReturnType<
        typeof hook.useBeepQueue
      >,
    );
    render(<MobileBeepReview />);
    await screen.findByText(/stage 3/);
    expect(screen.queryByRole("switch", { name: "Beep starts before the clip" })).toBeNull();
  });
});
