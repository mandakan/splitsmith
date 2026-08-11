/**
 * useBeepQueue hook - extracted queue/data plumbing behind BeepReview
 * (mobile beep review slice 3, #326 follow-up). These tests pin the
 * behavior that was previously only exercised indirectly through
 * BeepReview.tsx: initial load + first-pending selection, isMirror
 * derived from the queue's origin, and the confirm draft/no-draft paths.
 */
import { renderHook, waitFor, act } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { useBeepQueue } from "./useBeepQueue";
import * as api from "./api";

vi.mock("./api", () => ({
  api: {
    getBeepQueue: vi.fn(),
    confirmBeepInQueue: vi.fn(),
    overrideBeepForVideo: vi.fn(),
    detectBeepForVideo: vi.fn(),
    pollJob: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    detail = "boom";
  },
}));

const item = (over: Partial<api.BeepQueueItem> = {}): api.BeepQueueItem => ({
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

const queue = (
  items: api.BeepQueueItem[],
  origin: api.MatchOrigin = "desktop",
): api.BeepQueueResponse => ({
  total_items: items.length,
  pending_count: items.length,
  confirmed_count: 0,
  origin,
  stages: [
    {
      stage_number: 1,
      stage_name: "S1",
      items,
      total_videos: items.length,
      confirmed: 0,
    },
  ],
});

function wrapper({ children }: { children: React.ReactNode }) {
  return <MemoryRouter>{children}</MemoryRouter>;
}

describe("useBeepQueue", () => {
  beforeEach(() => vi.clearAllMocks());

  it("loads the queue, selects the first pending item, reports isMirror", async () => {
    vi.mocked(api.api.getBeepQueue).mockResolvedValue(queue([item()]));
    const { result } = renderHook(() => useBeepQueue(), { wrapper });
    await waitFor(() => expect(result.current.data).not.toBeNull());
    expect(result.current.active?.video_id).toBe("v1");
    expect(result.current.isMirror).toBe(true);
  });

  it("confirm with a draft calls override first, then confirm, then advances", async () => {
    const items = [item(), item({ video_id: "v2" })];
    vi.mocked(api.api.getBeepQueue).mockResolvedValue(queue(items));
    vi.mocked(api.api.overrideBeepForVideo).mockResolvedValue({} as never);
    vi.mocked(api.api.confirmBeepInQueue).mockResolvedValue(
      queue([item({ status: "confirmed", beep_reviewed: true }), items[1]]),
    );
    const { result } = renderHook(() => useBeepQueue(), { wrapper });
    await waitFor(() => expect(result.current.active).not.toBeNull());
    await act(() => result.current.confirm(items[0], 3.5));
    expect(api.api.overrideBeepForVideo).toHaveBeenCalledWith("alice", 1, "v1", 3.5);
    expect(api.api.confirmBeepInQueue).toHaveBeenCalledWith(
      expect.objectContaining({ slug: "alice", time: 3.5, source: "manual" }),
    );
    expect(result.current.active?.video_id).toBe("v2");
  });

  it("confirm without a draft never calls override", async () => {
    vi.mocked(api.api.getBeepQueue).mockResolvedValue(queue([item()]));
    vi.mocked(api.api.confirmBeepInQueue).mockResolvedValue(queue([]));
    const { result } = renderHook(() => useBeepQueue(), { wrapper });
    await waitFor(() => expect(result.current.active).not.toBeNull());
    await act(() => result.current.confirm(item()));
    expect(api.api.overrideBeepForVideo).not.toHaveBeenCalled();
  });
});
