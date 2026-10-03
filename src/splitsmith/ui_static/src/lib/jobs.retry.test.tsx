/** The jobs hook surfaces a refused retry instead of swallowing it. */
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, type Job } from "@/lib/api";
import { useJobs } from "@/lib/jobs";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, listJobs: vi.fn(), retryJob: vi.fn() } };
});

const failed = { id: "j1", kind: "trim", status: "failed", acknowledged: false } as Job;
const BODY = { code: "feature_required", feature: "hosted_compute" };

beforeEach(() => {
  vi.mocked(api.listJobs).mockReset().mockResolvedValue([failed]);
  vi.mocked(api.retryJob).mockReset();
});

describe("useJobs retry", () => {
  it("keeps a refused retry as a line for that job", async () => {
    vi.mocked(api.retryJob).mockRejectedValue(new ApiError(403, JSON.stringify(BODY), BODY));
    const { result } = renderHook(() => useJobs());
    await waitFor(() => expect(result.current.failed).toHaveLength(1));
    await act(() => result.current.retry(failed));
    expect(result.current.retryRefusal).toEqual({
      jobId: "j1",
      text: "Running detection and renders here is not included in this account's access.",
    });
  });

  it("a retry that is not refused leaves no line", async () => {
    vi.mocked(api.retryJob).mockResolvedValue({ ...failed, id: "j2", status: "pending" });
    const { result } = renderHook(() => useJobs());
    await waitFor(() => expect(result.current.failed).toHaveLength(1));
    await act(() => result.current.retry(failed));
    expect(result.current.retryRefusal).toBeNull();
  });
});
