import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

/** A Footage edit on one stage's clip of a single take must put that stage
 *  on the wire; the page tests mock these wrappers and cannot see it. */

afterEach(() => {
  vi.restoreAllMocks();
});

function mockFetch() {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({}),
  } as unknown as Response);
}

function sentBody(fetchMock: ReturnType<typeof mockFetch>): Record<string, unknown> {
  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return JSON.parse(init.body as string) as Record<string, unknown>;
}

describe("take edits name the stage on the wire", () => {
  it("moveAssignment sends from_stage_number", async () => {
    const fetchMock = mockFetch();
    await api.moveAssignment("alice", "raw/take.mp4", 3, "ignored", 3);
    expect(sentBody(fetchMock)).toEqual({
      video_path: "raw/take.mp4",
      to_stage_number: 3,
      role: "ignored",
      from_stage_number: 3,
    });
  });

  it("removeVideo sends stage_number", async () => {
    const fetchMock = mockFetch();
    await api.removeVideo("alice", "raw/take.mp4", false, 3);
    expect(sentBody(fetchMock)).toEqual({ video_path: "raw/take.mp4", reset_audit: false, stage_number: 3 });
  });
});
