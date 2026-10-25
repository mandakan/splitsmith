import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, type Job } from "@/lib/api";
import { useJobs } from "@/lib/jobs";

function job(id: string, kind: string, status: Job["status"], exportId: string | null): Job {
  return {
    id,
    kind,
    status,
    export_id: exportId,
    parts: null,
    match_id: "m1",
    stage_number: null,
    shooter_slug: null,
    video_id: null,
    progress: null,
    message: null,
    error: null,
    cancel_requested: false,
    acknowledged: false,
    result: null,
    timings: null,
    created_at: "2026-10-25T12:00:00Z",
    updated_at: "2026-10-25T12:00:00Z",
    started_at: null,
    finished_at: null,
  };
}

afterEach(() => vi.restoreAllMocks());

describe("useJobs", () => {
  it("shows an export's jobs as one row (spec 2026-10-25)", async () => {
    vi.spyOn(api, "listJobs").mockResolvedValue([
      job("s1", "compare-grid-stage", "running", "e1"),
      job("s2", "compare-grid-stage", "pending", "e1"),
      job("g", "compare-grid", "pending", "e1"),
      job("b", "detect_beep", "running", null),
    ]);
    const { result } = renderHook(() => useJobs());
    await waitFor(() => expect(result.current.jobs).toHaveLength(4));
    expect(result.current.running.map((j) => j.id)).toEqual(["g", "b"]);
    expect(result.current.pending).toEqual([]);
  });
});
