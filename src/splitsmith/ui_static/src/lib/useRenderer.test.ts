import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { useRenderer } from "@/lib/useRenderer";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, getChromiumStatus: vi.fn(), installChromium: vi.fn(), getJob: vi.fn() },
  };
});

describe("useRenderer", () => {
  beforeEach(() => vi.clearAllMocks());

  it("says nothing on hosted and never asks", () => {
    const { result } = renderHook(() => useRenderer(false));
    expect(result.current.state).toEqual({ kind: "unknown" });
    expect(api.getChromiumStatus).not.toHaveBeenCalled();
  });

  it("reports a missing renderer, installs it as a job and ends installed", async () => {
    vi.mocked(api.getChromiumStatus).mockResolvedValue({ installed: false });
    vi.mocked(api.installChromium).mockResolvedValue({ id: "j1" } as never);
    vi.mocked(api.getJob)
      .mockResolvedValueOnce({ id: "j1", status: "running", progress: 0.4 } as never)
      .mockResolvedValue({ id: "j1", status: "succeeded", progress: 1 } as never);
    const { result } = renderHook(() => useRenderer(true));
    await waitFor(() => expect(result.current.state).toEqual({ kind: "missing" }));
    act(() => result.current.install());
    await waitFor(() => expect(result.current.state).toEqual({ kind: "installing", progress: 0.4 }));
    await waitFor(() => expect(result.current.state).toEqual({ kind: "installed" }), { timeout: 4000 });
  });

  it("says why when the install fails", async () => {
    vi.mocked(api.getChromiumStatus).mockResolvedValue({ installed: false });
    vi.mocked(api.installChromium).mockResolvedValue({ id: "j1" } as never);
    vi.mocked(api.getJob).mockResolvedValue({ id: "j1", status: "failed", error: "no network" } as never);
    const { result } = renderHook(() => useRenderer(true));
    await waitFor(() => expect(result.current.state.kind).toBe("missing"));
    act(() => result.current.install());
    await waitFor(() => expect(result.current.state).toEqual({ kind: "failed", message: "no network" }));
  });
});
