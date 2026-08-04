import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

/** Colocated with api.ts per the module's convention (exportMatch etc. are
 *  tested in-file next to production code); this suite mirrors the
 *  fetch-mocking idiom from apiErrors.test.ts (vi.spyOn(globalThis, "fetch")
 *  + vi.restoreAllMocks() in afterEach). No vi.stubGlobal, no jsdom. */

afterEach(() => {
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as unknown as Response;
}

describe("api.exportCompareGrid", () => {
  it("posts the selection to the match-scoped endpoint and returns the Job snapshot", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ id: "job-1", kind: "compare-grid", status: "pending" }),
    );

    const job = await api.exportCompareGrid({
      stage_numbers: [1, 2],
      audio_from: "mathias",
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe("/api/match/compare-export");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/json",
    );
    const body = JSON.parse(init.body as string);
    expect(body.stage_numbers).toEqual([1, 2]);
    expect(body.audio_from).toBe("mathias");
    expect(job.id).toBe("job-1");
    expect(job.kind).toBe("compare-grid");
  });

  it("forwards optional fields only when the caller sets them", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ id: "job-2", kind: "compare-grid", status: "pending" }),
    );

    await api.exportCompareGrid({
      stage_numbers: [3],
      audio_from: "casper",
      cameras: { casper: "gopro" },
      canvas_width: 1920,
      canvas_height: 1080,
      output_name: "final-grid",
    });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({
      stage_numbers: [3],
      audio_from: "casper",
      cameras: { casper: "gopro" },
      canvas_width: 1920,
      canvas_height: 1080,
      output_name: "final-grid",
    });
  });

  it("throws ApiError-shaped rejection with the server detail on failure", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      status: 400,
      statusText: "Bad Request",
      json: async () => ({ detail: "stage_numbers cannot be empty" }),
    } as unknown as Response);

    await expect(
      api.exportCompareGrid({ stage_numbers: [], audio_from: "mathias" }),
    ).rejects.toMatchObject({
      status: 400,
      detail: "stage_numbers cannot be empty",
    });
  });
});
