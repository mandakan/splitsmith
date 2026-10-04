/** The jobs hook tracks the strip's batch across polls (#1190): old
 *  succeeded rows the poll list carries never join it. */
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type Job } from "@/lib/api";
import { useJobs } from "@/lib/jobs";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, listJobs: vi.fn() } };
});

const job = (id: string, status: Job["status"], kind = "trim"): Job =>
  ({ id, kind, status, match_id: "m", acknowledged: false }) as Job;

// The hosted poll list keeps the 20 most recent finished jobs (#1182).
const OLD = Array.from({ length: 20 }, (_, i) => job(`old${i}`, "succeeded"));

beforeEach(() => {
  vi.mocked(api.listJobs).mockReset();
});

describe("useJobs batch", () => {
  it("collects jobs seen active, keeps them once done, resets when idle", async () => {
    vi.mocked(api.listJobs).mockResolvedValueOnce([
      ...OLD,
      job("a", "running"),
      job("b", "pending"),
      job("s", "running", "auto_sync"),
    ]);
    const { result } = renderHook(() => useJobs());
    await waitFor(() => expect(result.current.running).toHaveLength(1));
    expect([...result.current.batch].sort()).toEqual(["a", "b"]);

    vi.mocked(api.listJobs).mockResolvedValueOnce([...OLD, job("a", "succeeded"), job("b", "running")]);
    await act(() => result.current.refresh());
    expect([...result.current.batch].sort()).toEqual(["a", "b"]);

    vi.mocked(api.listJobs).mockResolvedValueOnce([...OLD, job("a", "succeeded"), job("b", "succeeded")]);
    await act(() => result.current.refresh());
    expect(result.current.batch.size).toBe(0);

    vi.mocked(api.listJobs).mockResolvedValueOnce([
      ...OLD,
      job("a", "succeeded"),
      job("b", "succeeded"),
      job("c", "running"),
    ]);
    await act(() => result.current.refresh());
    expect([...result.current.batch]).toEqual(["c"]);
  });
});
