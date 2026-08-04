import { afterEach, describe, expect, it, vi } from "vitest";

import { api, apiErrorText } from "@/lib/api";

const FALLBACK = "Could not load the stage list.";

/** Minimal stand-in for the parts of ``Response`` that :func:`request`
 *  reads on the error path. */
function errorResponse(status: number, statusText: string, body: unknown) {
  return {
    ok: false,
    status,
    statusText,
    json: async () => body,
  } as unknown as Response;
}

/** Drive a real failing request through the api module so the thrown value
 *  is a genuine ``ApiError`` built by production code. ``ApiError`` is not
 *  exported, and exporting it purely for tests would let the test drift
 *  from how errors are actually constructed (``detail`` vs ``body`` is
 *  exactly the distinction under test), so we mock ``fetch`` instead. */
async function thrownBy(
  status: number,
  statusText: string,
  body: unknown,
): Promise<unknown> {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    errorResponse(status, statusText, body),
  );
  try {
    await api.getMatchStages();
  } catch (e) {
    return e;
  }
  throw new Error("expected getMatchStages() to reject");
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("apiErrorText", () => {
  it("shows the server's sentence when the detail is a plain string", async () => {
    const err = await thrownBy(400, "Bad Request", {
      detail: "Stage 7 is still being trimmed.",
    });
    expect(apiErrorText(err, FALLBACK)).toBe("Stage 7 is still being trimmed.");
  });

  it("falls back rather than rendering a stringified 409 no_project body", async () => {
    const detail = {
      code: "no_project",
      message: "No project is bound to this server.",
    };
    const err = await thrownBy(409, "Conflict", { detail });
    const text = apiErrorText(err, FALLBACK);

    expect(text).toBe(FALLBACK);
    // The defect this guards: ``e.message`` was ``409: {"code":...}``.
    expect(text).not.toContain("no_project");
    expect(text).not.toContain("{");
    expect(text).not.toContain(JSON.stringify(detail));
    // ...and the raw message really would have leaked it, so the
    // assertions above are not vacuous.
    expect((err as Error).message).toContain(JSON.stringify(detail));
  });

  it("falls back on a structured 424 source_unreachable body too", async () => {
    const err = await thrownBy(424, "Failed Dependency", {
      detail: { code: "source_unreachable", stage_number: 3, path: "/v/a.mp4" },
    });
    expect(apiErrorText(err, FALLBACK)).toBe(FALLBACK);
  });

  it("shows the status text when the body carries no detail at all", async () => {
    // ``body`` stays null here; the message is still prose, not JSON.
    const err = await thrownBy(500, "Internal Server Error", { oops: true });
    expect(apiErrorText(err, FALLBACK)).toBe("Internal Server Error");
  });

  it("falls back on a plain Error -- its message is internal-facing", () => {
    expect(apiErrorText(new Error("fetch failed"), FALLBACK)).toBe(FALLBACK);
    expect(apiErrorText(new TypeError("undefined is not a function"), FALLBACK)).toBe(
      FALLBACK,
    );
  });

  it("falls back on values that are not errors", () => {
    expect(apiErrorText("boom", FALLBACK)).toBe(FALLBACK);
    expect(apiErrorText(null, FALLBACK)).toBe(FALLBACK);
    expect(apiErrorText(undefined, FALLBACK)).toBe(FALLBACK);
    expect(apiErrorText({ detail: "looks like one" }, FALLBACK)).toBe(FALLBACK);
  });
});
