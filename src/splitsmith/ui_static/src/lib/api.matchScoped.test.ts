import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

/** The Lab lives on /dev/* URLs, so ``scopeRequestPath`` never rewrites
 *  its bare ``/api/match/...`` calls onto a match prefix -- and since
 *  #353 Tier 1 the server resolves match roots ONLY from the
 *  ``/api/matches/{id}/...`` URL prefix (no process-level bind
 *  fallback). These functions carry the match id explicitly so the
 *  Lab's promote panel can address a match from a dev-mode URL.
 *
 *  Fetch-mocking idiom borrowed from api.coachPatch.test.ts. */

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

function mockFetch(body: unknown = {}) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(body));
}

describe("match-scoped Lab API calls", () => {
  it("lists shooters through the /api/matches/{id}/ alias", async () => {
    const fetchMock = mockFetch({ shooters: [] });
    await api.listMatchShootersIn("m-1");
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(String(url)).toBe("/api/matches/m-1/match/shooters");
  });

  it("fetches a shooter project through the alias", async () => {
    const fetchMock = mockFetch({});
    await api.getProjectIn("m-1", "s_abc");
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(String(url)).toBe("/api/matches/m-1/shooters/s_abc/project");
  });

  it("fetches the export overview through the alias", async () => {
    const fetchMock = mockFetch({ stages: [] });
    await api.getExportOverviewIn("m-1", "s_abc");
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(String(url)).toBe("/api/matches/m-1/shooters/s_abc/exports/overview");
  });

  it("POSTs the promote through the alias", async () => {
    const fetchMock = mockFetch({});
    await api.promoteFixtureIn("m-1", {
      stage_number: 3,
      slug: "stage-shots-x",
      overwrite: true,
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe("/api/matches/m-1/lab/promote");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({
      stage_number: 3,
      slug: "stage-shots-x",
      overwrite: true,
    });
  });

  it("URL-encodes the match id", async () => {
    const fetchMock = mockFetch({ shooters: [] });
    await api.listMatchShootersIn("weird/id");
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(String(url)).toBe("/api/matches/weird%2Fid/match/shooters");
  });
});
