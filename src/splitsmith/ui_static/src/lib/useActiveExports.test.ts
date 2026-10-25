import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, type ExportView } from "@/lib/api";
import { useActiveExports } from "@/lib/useActiveExports";

function view(status: string, retryable = false): ExportView {
  return {
    export_id: "e1",
    kind: "compare-grid",
    label: "Compare grid",
    match_id: "m1",
    status,
    started_at: "2026-10-25T12:00:00Z",
    time_left_s: status === "running" ? 60 : null,
    finish_at: null,
    approximate: true,
    rows: [
      {
        job_id: "s1",
        key: "render",
        label: "Stage 1",
        status: retryable ? "failed" : "running",
        progress: null,
        elapsed_s: null,
        remaining_s: 0,
        overrun: false,
        retryable,
      },
    ],
  } as ExportView;
}

afterEach(() => vi.restoreAllMocks());

describe("useActiveExports", () => {
  it("keeps an export that ended with a failed part, until dismissed (spec 2026-10-25)", async () => {
    const active = vi
      .spyOn(api, "activeExports")
      .mockResolvedValueOnce({ limit: 1, exports: [view("running")] })
      .mockResolvedValue({ limit: 1, exports: [] });
    vi.spyOn(api, "getExport").mockResolvedValue(view("succeeded", true));
    const { result } = renderHook(() => useActiveExports());
    await waitFor(() => expect(result.current.active?.exports).toHaveLength(1));
    await act(() => result.current.refresh());
    await waitFor(() => expect(result.current.ended.map((v) => v.export_id)).toEqual(["e1"]));
    expect(active).toHaveBeenCalled();
    act(() => result.current.dismiss("e1"));
    expect(result.current.ended).toEqual([]);
  });

  it("lets an export that ended cleanly go", async () => {
    vi.spyOn(api, "activeExports")
      .mockResolvedValueOnce({ limit: 1, exports: [view("running")] })
      .mockResolvedValue({ limit: 1, exports: [] });
    vi.spyOn(api, "getExport").mockResolvedValue(view("succeeded"));
    const { result } = renderHook(() => useActiveExports());
    await waitFor(() => expect(result.current.active?.exports).toHaveLength(1));
    await act(() => result.current.refresh());
    await waitFor(() => expect(api.getExport).toHaveBeenCalled());
    expect(result.current.ended).toEqual([]);
  });
});
