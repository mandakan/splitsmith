/**
 * useDeploymentMode resolution state (add-videos UX rework).
 *
 * The hook used to return a bare string that read "local" while the
 * features fetch was in flight - hosted users briefly saw local-only
 * chrome. It now returns { mode, resolved } so gated surfaces can hold
 * a neutral skeleton until the answer is real.
 *
 * The module keeps a module-level promise cache, so each test resets
 * the module registry and re-imports a fresh copy.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

beforeEach(() => {
  vi.resetModules();
});

async function setup(mode: "local" | "hosted") {
  let resolveFeatures: (v: { lab: boolean; mode: "local" | "hosted" }) => void = () => {};
  vi.doMock("@/lib/api", async (importOriginal) => {
    const actual = await importOriginal<typeof import("@/lib/api")>();
    return {
      ...actual,
      api: {
        ...actual.api,
        getServerFeatures: vi.fn(
          () =>
            new Promise<{ lab: boolean; mode: "local" | "hosted" }>((res) => {
              resolveFeatures = res;
            }),
        ),
      },
    };
  });
  const { useDeploymentMode } = await import("@/lib/features");
  return {
    useDeploymentMode,
    settle: async () => {
      await act(async () => {
        resolveFeatures({ lab: false, mode });
      });
    },
  };
}

describe("useDeploymentMode", () => {
  it("reports local + unresolved while the features fetch is in flight", async () => {
    const { useDeploymentMode } = await setup("hosted");
    const { result } = renderHook(() => useDeploymentMode());
    expect(result.current).toEqual({ mode: "local", resolved: false });
  });

  it("settles to hosted + resolved once the fetch lands", async () => {
    const { useDeploymentMode, settle } = await setup("hosted");
    const { result } = renderHook(() => useDeploymentMode());
    await settle();
    await waitFor(() =>
      expect(result.current).toEqual({ mode: "hosted", resolved: true }),
    );
  });

  it("resolves immediately for mounts after the cache has settled", async () => {
    const { useDeploymentMode, settle } = await setup("hosted");
    const first = renderHook(() => useDeploymentMode());
    await settle();
    await waitFor(() => expect(first.result.current.resolved).toBe(true));
    const second = renderHook(() => useDeploymentMode());
    expect(second.result.current).toEqual({ mode: "hosted", resolved: true });
  });
});
