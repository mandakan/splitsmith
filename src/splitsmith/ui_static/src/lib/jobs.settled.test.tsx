/** A job that starts and finishes between two polls still counts as
 *  settled: a 1.5 s shot detection after a beep confirm was never seen
 *  running, so Audit kept the copy it loaded before detection and the
 *  next save was refused as "saved elsewhere". */
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type Job } from "@/lib/api";
import { settledBetween, useJobs } from "@/lib/jobs";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, listJobs: vi.fn() } };
});

const job = (id: string, status: Job["status"], kind = "shot_detect"): Job =>
  ({ id, kind, status, match_id: "m", shooter_slug: "albin", stage_number: 7, acknowledged: false }) as Job;

const OLD = [job("old1", "succeeded"), job("old2", "failed", "trim")];

beforeEach(() => {
  vi.mocked(api.listJobs).mockReset();
});

describe("settledBetween", () => {
  it("is a job seen active that ended, or one that appeared already ended", () => {
    const prev = [...OLD, job("a", "running")];
    const next = [...OLD, job("a", "succeeded"), job("fast", "succeeded"), job("b", "pending")];
    expect(settledBetween(prev, next).map((j) => j.id)).toEqual(["a", "fast"]);
  });

  it("counts nothing on the first poll, whatever the list holds", () => {
    expect(settledBetween(null, OLD)).toEqual([]);
  });
});

describe("useJobs settled", () => {
  it("reports a detection that ran entirely between two polls", async () => {
    vi.mocked(api.listJobs).mockResolvedValueOnce(OLD);
    const { result } = renderHook(() => useJobs());
    await waitFor(() => expect(api.listJobs).toHaveBeenCalledTimes(1));
    expect(result.current.settled).toEqual([]);

    vi.mocked(api.listJobs).mockResolvedValueOnce([...OLD, job("fast", "succeeded")]);
    await act(() => result.current.refresh());
    expect(result.current.settled.map((j) => j.id)).toEqual(["fast"]);
  });
});
