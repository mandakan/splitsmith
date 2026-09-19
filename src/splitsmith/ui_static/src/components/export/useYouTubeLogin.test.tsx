/**
 * The shared YouTube login (issue #1000): start, open the consent URL,
 * poll until connected or failed, time out. The same hook runs the
 * Export row and the Account section, so its behaviour is pinned once
 * here and the component tests only check what each renders.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POLL_LIMIT_MS, POLL_MS, useYouTubeLogin } from "@/components/export/useYouTubeLogin";

const startYouTubeConnect = vi.fn();
const youtubeConnectStatus = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      startYouTubeConnect: (...a: unknown[]) => startYouTubeConnect(...a),
      youtubeConnectStatus: (...a: unknown[]) => youtubeConnectStatus(...a),
    },
  };
});

const PENDING = { state: "pending", channel_title: null, error: null };

describe("useYouTubeLogin", () => {
  const open = vi.fn();
  beforeEach(() => {
    vi.useFakeTimers();
    startYouTubeConnect.mockReset();
    youtubeConnectStatus.mockReset();
    open.mockReset();
    vi.stubGlobal("open", open);
    startYouTubeConnect.mockResolvedValue({ auth_url: "https://accounts.google.com/x", expires_at: "" });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("opens the consent url, polls, and calls back once connected", async () => {
    youtubeConnectStatus
      .mockResolvedValueOnce(PENDING)
      .mockResolvedValueOnce({ state: "connected", channel_title: "Mine", error: null });
    const onConnected = vi.fn();
    const { result } = renderHook(() => useYouTubeLogin(onConnected));
    await act(async () => {
      await result.current.connect();
    });
    expect(open).toHaveBeenCalledWith("https://accounts.google.com/x", "_blank", "noopener");
    expect(result.current.pending).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS);
    });
    expect(result.current.pending).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS);
    });
    expect(result.current.pending).toBe(false);
    expect(result.current.error).toBeNull();
    expect(onConnected).toHaveBeenCalledTimes(1);
    // Settled: no further polls.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS * 3);
    });
    expect(youtubeConnectStatus).toHaveBeenCalledTimes(2);
  });

  it("reports the server's reason on a failed login", async () => {
    youtubeConnectStatus.mockResolvedValue({ state: "failed", channel_title: null, error: "access_denied" });
    const onConnected = vi.fn();
    const { result } = renderHook(() => useYouTubeLogin(onConnected));
    await act(async () => {
      await result.current.connect();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS);
    });
    expect(result.current.pending).toBe(false);
    expect(result.current.error).toBe("access_denied");
    expect(onConnected).not.toHaveBeenCalled();
  });

  it("reports a start failure without opening a tab", async () => {
    startYouTubeConnect.mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useYouTubeLogin(vi.fn()));
    await act(async () => {
      await result.current.connect();
    });
    expect(open).not.toHaveBeenCalled();
    expect(result.current.pending).toBe(false);
    expect(result.current.error).toMatch(/boom|Could not start/);
  });

  it("ignores a dropped poll and times out after the limit", async () => {
    youtubeConnectStatus.mockRejectedValueOnce(new Error("offline")).mockResolvedValue(PENDING);
    const { result } = renderHook(() => useYouTubeLogin(vi.fn()));
    await act(async () => {
      await result.current.connect();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS * 2);
    });
    expect(result.current.pending).toBe(true);
    expect(result.current.error).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_LIMIT_MS + POLL_MS);
    });
    expect(result.current.pending).toBe(false);
    expect(result.current.error).toBe("The login timed out. Connect again.");
  });

  it("cancel stops polling", async () => {
    youtubeConnectStatus.mockResolvedValue(PENDING);
    const { result } = renderHook(() => useYouTubeLogin(vi.fn()));
    await act(async () => {
      await result.current.connect();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS);
    });
    const polls = youtubeConnectStatus.mock.calls.length;
    act(() => {
      result.current.cancel();
    });
    expect(result.current.pending).toBe(false);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS * 3);
    });
    expect(youtubeConnectStatus).toHaveBeenCalledTimes(polls);
  });
});
