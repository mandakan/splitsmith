/**
 * useActiveShare - resolves the match's live share URL for OWNER pages
 * (moment-followups: share-aware "Copy link at moment"). Pins the three
 * gates that must never fire a fetch (share view, no share capability)
 * plus the happy-path revoked-skip and the silent-error fallback.
 */
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { useActiveShare } from "./useActiveShare";
import { api } from "./api";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listShares: vi.fn(),
    },
  };
});

vi.mock("./features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./features")>();
  return { ...actual, useDeploymentMode: vi.fn() };
});

import { useDeploymentMode } from "./features";

const listShares = vi.mocked(api.listShares);
const mockDeploymentMode = vi.mocked(useDeploymentMode);

function wrapperAt(pathname: string) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <MemoryRouter initialEntries={[pathname]}>{children}</MemoryRouter>;
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockDeploymentMode.mockReturnValue({ mode: "hosted", resolved: true });
});

describe("useActiveShare", () => {
  it("returns the first live share url, skipping revoked ones", async () => {
    listShares.mockResolvedValue({
      shares: [
        {
          id: "revoked-1",
          url: "https://x.test/share/revoked",
          created_at: "2026-08-01T00:00:00Z",
          revoked_at: "2026-08-02T00:00:00Z",
        },
        {
          id: "live-1",
          url: "https://x.test/share/live",
          created_at: "2026-08-03T00:00:00Z",
          revoked_at: null,
        },
      ],
    });

    const { result } = renderHook(() => useActiveShare(), {
      wrapper: wrapperAt("/match/m1/results"),
    });

    await waitFor(() => expect(result.current.shareUrl).toBe("https://x.test/share/live"));
    expect(listShares).toHaveBeenCalledTimes(1);
  });

  it("normalizes a trailing slash on the share url", async () => {
    listShares.mockResolvedValue({
      shares: [
        {
          id: "live-1",
          url: "https://x.test/share/live/",
          created_at: "2026-08-03T00:00:00Z",
          revoked_at: null,
        },
      ],
    });

    const { result } = renderHook(() => useActiveShare(), {
      wrapper: wrapperAt("/match/m1/results"),
    });

    await waitFor(() => expect(result.current.shareUrl).toBe("https://x.test/share/live"));
  });

  it("returns null and never fetches on a share view", async () => {
    const { result } = renderHook(() => useActiveShare(), {
      wrapper: wrapperAt("/share/tok123/results"),
    });

    expect(result.current.shareUrl).toBe(null);
    await new Promise((r) => setTimeout(r, 0));
    expect(listShares).not.toHaveBeenCalled();
  });

  it("returns null and never fetches without the share capability (local mode)", async () => {
    mockDeploymentMode.mockReturnValue({ mode: "local", resolved: true });

    const { result } = renderHook(() => useActiveShare(), {
      wrapper: wrapperAt("/match/m1/results"),
    });

    expect(result.current.shareUrl).toBe(null);
    await new Promise((r) => setTimeout(r, 0));
    expect(listShares).not.toHaveBeenCalled();
  });

  it("resolves to null silently when the fetch fails", async () => {
    listShares.mockRejectedValue(new Error("boom"));

    const { result } = renderHook(() => useActiveShare(), {
      wrapper: wrapperAt("/match/m1/results"),
    });

    await waitFor(() => expect(listShares).toHaveBeenCalledTimes(1));
    expect(result.current.shareUrl).toBe(null);
  });

  it("returns null when there is no live share", async () => {
    listShares.mockResolvedValue({
      shares: [
        {
          id: "revoked-1",
          url: "https://x.test/share/revoked",
          created_at: "2026-08-01T00:00:00Z",
          revoked_at: "2026-08-02T00:00:00Z",
        },
      ],
    });

    const { result } = renderHook(() => useActiveShare(), {
      wrapper: wrapperAt("/match/m1/results"),
    });

    await waitFor(() => expect(listShares).toHaveBeenCalledTimes(1));
    expect(result.current.shareUrl).toBe(null);
  });
});
