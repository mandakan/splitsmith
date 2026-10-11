import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

/** Reading a stage never writes it: only ``seed`` (Breakdown's) asks the
 *  server to propose reload regions and keep them. */

afterEach(() => {
  vi.restoreAllMocks();
});

function mockFetch() {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => null,
  } as unknown as Response);
}

describe("getStageCoach", () => {
  it("sends no seed by default", async () => {
    const fetchMock = mockFetch();
    await api.getStageCoach("anna", 3);
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toMatch(/\/api\/shooters\/anna\/stages\/3\/coach$/);
  });

  it("asks for proposals with seed", async () => {
    const fetchMock = mockFetch();
    await api.getStageCoach("anna", 3, { seed: true });
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toMatch(/\/api\/shooters\/anna\/stages\/3\/coach\?seed=1$/);
  });
});
