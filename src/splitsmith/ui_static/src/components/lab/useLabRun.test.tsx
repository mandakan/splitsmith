/**
 * useLabRun -- the shared eval-run data hook lifted out of Lab.tsx so
 * the redesigned fixture-detail page and the Validate page can drive
 * eval runs the same way the legacy Lab page does. Pins the three
 * behaviors that must survive the lift verbatim: mount hydration from
 * the server's last-run cache (config included), the single-flight
 * scoped runEval, and the 120ms debounced rescore that only fires on a
 * real post-mount config edit (not the hydration-driven config adopt).
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, type LabEvalRun } from "@/lib/api";
import { DEFAULT_CONFIG, useLabRun } from "@/components/lab/useLabRun";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getLastLabRun: vi.fn(),
      runLabEval: vi.fn(),
      pollJob: vi.fn(),
      rescoreLabUniverse: vi.fn(),
    },
  };
});

function makeRun(overrides: Partial<LabEvalRun> = {}): LabEvalRun {
  return {
    config: DEFAULT_CONFIG,
    config_hash: "hash-1",
    built_at: "2026-08-14T00:00:00Z",
    summary: {
      n_fixtures: 1,
      n_truth: 1,
      n_kept: 1,
      true_positives: 1,
      false_positives: 0,
      false_negatives: 0,
      precision: 1,
      recall: 1,
      f1: 1,
      fp_by_reason: {},
      positives_by_subclass: {},
    },
    universe: {
      fixtures: [],
      voter_a_floor: 0.1,
      voter_b_threshold: 0.1,
      voter_c_threshold: 0.5,
      tolerance_ms: 75,
    },
    ...overrides,
  };
}

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("useLabRun", () => {
  it("hydrates from the server's last-run cache on mount", async () => {
    const RUN = makeRun({ config: { ...DEFAULT_CONFIG, consensus: 3 }, config_hash: "hydrated" });
    vi.mocked(api.getLastLabRun).mockResolvedValue(RUN);

    const { result } = renderHook(() => useLabRun());

    await waitFor(() => expect(result.current.run).toEqual(RUN));
    expect(result.current.config).toEqual(RUN.config);
  });

  it("runEval(slugs) posts the scoped eval and refreshes the run", async () => {
    const RUN2 = makeRun({ config_hash: "after-eval" });
    vi.mocked(api.getLastLabRun)
      .mockRejectedValueOnce(new Error("no run"))
      .mockResolvedValueOnce(RUN2);
    vi.mocked(api.runLabEval).mockResolvedValue({ id: "job-1", status: "running" } as never);
    vi.mocked(api.pollJob).mockResolvedValue({ id: "job-1", status: "succeeded" } as never);

    const { result } = renderHook(() => useLabRun());
    await waitFor(() => expect(api.getLastLabRun).toHaveBeenCalledTimes(1));

    await act(async () => {
      await result.current.runEval(["s1"]);
    });

    expect(api.runLabEval).toHaveBeenCalledWith({
      slugs: ["s1"],
      config: DEFAULT_CONFIG,
      persist: true,
    });
    expect(api.pollJob).toHaveBeenCalled();
    expect(api.getLastLabRun).toHaveBeenCalledTimes(2);
    expect(result.current.run).toEqual(RUN2);
    expect(result.current.evalLoading).toBe(false);
  });

  it("config changes rescore the cached universe when autoRescore is on", async () => {
    vi.mocked(api.getLastLabRun).mockRejectedValue(new Error("no run"));
    const RESCORED = makeRun({ config_hash: "rescored" });
    vi.mocked(api.rescoreLabUniverse).mockResolvedValue(RESCORED);

    const { result } = renderHook(() => useLabRun());
    await waitFor(() => expect(api.getLastLabRun).toHaveBeenCalledTimes(1));

    const RUN = makeRun();
    act(() => {
      result.current.setRun(RUN);
    });

    vi.useFakeTimers();
    act(() => {
      result.current.setConfig({ consensus: 3 });
    });
    expect(api.rescoreLabUniverse).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(150);
    });

    expect(api.rescoreLabUniverse).toHaveBeenCalledWith({ ...DEFAULT_CONFIG, consensus: 3 });
    expect(result.current.run).toEqual(RESCORED);
  });

  it("does not auto-rescore when autoRescore is false", async () => {
    vi.mocked(api.getLastLabRun).mockRejectedValue(new Error("no run"));

    const { result } = renderHook(() => useLabRun({ autoRescore: false }));
    await waitFor(() => expect(api.getLastLabRun).toHaveBeenCalledTimes(1));

    act(() => {
      result.current.setRun(makeRun());
    });

    vi.useFakeTimers();
    act(() => {
      result.current.setConfig({ consensus: 3 });
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(150);
    });

    expect(api.rescoreLabUniverse).not.toHaveBeenCalled();
  });
});
