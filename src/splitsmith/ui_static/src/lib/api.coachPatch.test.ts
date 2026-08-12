import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

/** #844: the by-id coach PATCH and the positional route's
 *  ``expected_version`` guard both shipped with the backend, but the SPA
 *  addressed shots positionally with no guard -- so the renumbering
 *  corruption stayed reachable. These pin which route a given shot takes.
 *
 *  Fetch-mocking idiom borrowed from api.compareGrid.test.ts. */

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

const COACH_RESPONSE = {
  stage_number: 1,
  stage_name: "Stage One",
  beep_time: 5,
  version: 9,
  videos: [],
  shots: [],
};

describe("api.patchStageShotCoach", () => {
  it("addresses the by-id route when the shot carries an id", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(COACH_RESPONSE));

    await api.patchStageShotCoach(
      "anna",
      2,
      { id: "cand-9", shot_number: 3 },
      { coaching_note: "tight transition" },
      4,
    );

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe(
      "/api/shooters/anna/stages/2/shots/by-id/cand-9/coach",
    );
    expect(init.method).toBe("PATCH");
  });

  it("omits expected_version on the by-id route", async () => {
    // The by-id route is immune to renumbering, so a version guard there
    // buys nothing and costs a spurious 409 whenever the client's version
    // has moved on -- e.g. an Undo re-patch after the first patch already
    // bumped the doc.
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(COACH_RESPONSE));

    await api.patchStageShotCoach(
      "anna",
      2,
      { id: "cand-9", shot_number: 3 },
      { coaching_note: "note" },
      4,
    );

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).not.toHaveProperty(
      "expected_version",
    );
  });

  it("falls back to the positional route with expected_version when the shot has no id", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(COACH_RESPONSE));

    await api.patchStageShotCoach(
      "anna",
      2,
      { id: null, shot_number: 3 },
      { coaching_note: "note" },
      4,
    );

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe("/api/shooters/anna/stages/2/shots/3/coach");
    expect(JSON.parse(init.body as string)).toEqual({
      coaching_note: "note",
      expected_version: 4,
    });
  });

  it("sends no guard on the positional fallback when the caller has no version", async () => {
    // A caller that genuinely does not know the version must still be able
    // to patch: sending ``expected_version: undefined`` would serialise the
    // key away anyway, but an explicit null would be read as "version 0"
    // by the server model and refuse every hosted patch.
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(COACH_RESPONSE));

    await api.patchStageShotCoach(
      "anna",
      2,
      { id: null, shot_number: 3 },
      { coaching_note: "note" },
    );

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).not.toHaveProperty(
      "expected_version",
    );
  });

  it("percent-encodes an id that would otherwise change the route", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(COACH_RESPONSE));

    await api.patchStageShotCoach(
      "anna",
      2,
      { id: "manual-a/b", shot_number: 3 },
      { coaching_note: "note" },
    );

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe(
      "/api/shooters/anna/stages/2/shots/by-id/manual-a%2Fb/coach",
    );
  });
});
