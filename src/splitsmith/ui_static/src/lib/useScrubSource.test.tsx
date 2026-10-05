import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { useScrubSource } from "@/lib/useScrubSource";

const mode = vi.hoisted(() => ({ value: { mode: "local", resolved: true } }));
vi.mock("@/lib/features", async (orig) => ({
  ...(await orig<typeof import("@/lib/features")>()),
  useDeploymentMode: () => mode.value,
}));
vi.mock("@/lib/api", async (orig) => {
  const actual = await orig<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, getScrubSettings: vi.fn(), setScrubSettings: vi.fn() } };
});

const cam = (path: string) => ({ path, trim_version: `t-${path}`, scrub_version: `w-${path}` });

beforeEach(() => {
  mode.value = { mode: "local", resolved: true };
  vi.mocked(api.getScrubSettings).mockReset().mockResolvedValue({ full_res_scrub: false });
  vi.mocked(api.setScrubSettings).mockReset().mockResolvedValue({ full_res_scrub: true });
});

describe("useScrubSource", () => {
  it("chooses the rendition by default and is available locally", async () => {
    const { result } = renderHook(() => useScrubSource());
    await waitFor(() => expect(result.current.available).toBe(true));
    expect(result.current.choose(cam("a"))).toEqual({ kind: "web", version: "w-a" });
  });

  it("falls back to the trim for the failed video only", async () => {
    const { result } = renderHook(() => useScrubSource());
    act(() => result.current.markFailed(cam("a")));
    expect(result.current.choose(cam("a"))).toEqual({ kind: "trim", version: "t-a" });
    expect(result.current.choose(cam("b"))).toEqual({ kind: "web", version: "w-b" });
  });

  it("a new rendition gets a fresh chance after a failure", async () => {
    const { result } = renderHook(() => useScrubSource());
    act(() => result.current.markFailed(cam("a")));
    expect(result.current.choose(cam("a")).kind).toBe("trim");
    const recut = { ...cam("a"), scrub_version: "w-a2" };
    expect(result.current.choose(recut)).toEqual({ kind: "web", version: "w-a2" });
  });

  it("the switch persists and keeps every video on the trim", async () => {
    const { result } = renderHook(() => useScrubSource());
    await waitFor(() => expect(result.current.available).toBe(true));
    act(() => result.current.setFullRes(true));
    expect(api.setScrubSettings).toHaveBeenCalledWith(true);
    expect(result.current.choose(cam("a"))).toEqual({ kind: "trim", version: "t-a" });
  });

  it("loads a saved switch", async () => {
    vi.mocked(api.getScrubSettings).mockResolvedValue({ full_res_scrub: true });
    const { result } = renderHook(() => useScrubSource());
    await waitFor(() => expect(result.current.fullRes).toBe(true));
  });

  it("hosted never asks the server and offers no switch", async () => {
    mode.value = { mode: "hosted", resolved: true };
    const { result } = renderHook(() => useScrubSource());
    await Promise.resolve();
    expect(api.getScrubSettings).not.toHaveBeenCalled();
    expect(result.current.available).toBe(false);
  });

  it("a failed settings read leaves the rendition on and the switch hidden", async () => {
    vi.mocked(api.getScrubSettings).mockRejectedValue(new Error("down"));
    const { result } = renderHook(() => useScrubSource());
    await waitFor(() => expect(api.getScrubSettings).toHaveBeenCalled());
    expect(result.current.available).toBe(false);
    expect(result.current.choose(cam("a")).kind).toBe("web");
  });
});
