import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CleanupDialog } from "@/components/CleanupDialog";

/** ``fetch`` is mocked rather than ``api.*`` so the rejection the dialog
 *  sees is a genuine ``ApiError`` built by production code. ``ApiError``
 *  is not exported, and ``apiErrors.test.ts`` documents why exporting it
 *  for tests would let them drift from how errors are really constructed
 *  -- ``detail`` (a string) versus ``body`` (the dict) is exactly what
 *  ``asJobsActiveError`` reads. */
const PLAN = {
  items: [
    {
      path: "exports/stage1_a_trimmed.mp4",
      size_bytes: 1_048_576,
      category: "exports-trims",
      storage_key: "matches/m1/shooters/me/exports/stage1_a_trimmed.mp4",
      reconstructable: true,
    },
    {
      path: "exports/stage2_b_trimmed.mp4",
      size_bytes: 2_097_152,
      category: "exports-trims",
      storage_key: "matches/m1/shooters/me/exports/stage2_b_trimmed.mp4",
      reconstructable: false,
    },
  ],
  totals_by_category: { "exports-trims": { file_count: 2, bytes: 3_145_728 } },
  total_bytes: 3_145_728,
  total_file_count: 2,
};

function ok(body: unknown) {
  return { ok: true, status: 200, statusText: "OK", json: async () => body } as unknown as Response;
}

function err(status: number, body: unknown) {
  return {
    ok: false,
    status,
    statusText: "Conflict",
    json: async () => ({ detail: body }),
  } as unknown as Response;
}

/** Route by method: the plan is a GET, the apply is a POST. */
function mockFetch(applyResponse: Response) {
  vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) => {
    const method = (init as RequestInit | undefined)?.method ?? "GET";
    return Promise.resolve(method === "POST" ? applyResponse : ok(PLAN));
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("CleanupDialog", () => {
  it("shows totals from the plan", async () => {
    mockFetch(ok({ plan: PLAN, result: { deleted: [], failed: [], bytes_freed: 0 } }));
    render(<CleanupDialog slug="me" open onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /select all/i }));
    // ``formatBytes(3_145_728)`` is "3.0 MB", not "3 MB". Scope to the
    // total line: the category row renders the same string, so a bare
    // ``getByText(/3\.0 MB/)`` would throw on multiple matches.
    await waitFor(() =>
      expect(screen.getByText(/Total: 3\.0 MB/)).toBeInTheDocument(),
    );
  });

  it("lists what cannot be rebuilt, unchecked, after select all", async () => {
    mockFetch(ok({ plan: PLAN, result: { deleted: [], failed: [], bytes_freed: 0 } }));
    render(<CleanupDialog slug="me" open onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /select all/i }));

    // The unrebuildable item is shown -- never silently dropped -- in its
    // own opt-in section, and starts unchecked even after "select all".
    const region = await screen.findByRole("region", { name: /cannot be rebuilt/i });
    expect(region).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "stage2_b_trimmed.mp4" }),
    ).not.toBeChecked();
    // The reconstructable one is not in that section at all.
    expect(
      screen.queryByRole("checkbox", { name: "stage1_a_trimmed.mp4" }),
    ).not.toBeInTheDocument();
  });

  it("leaves audit-data out of select all", async () => {
    mockFetch(ok({ plan: PLAN, result: { deleted: [], failed: [], bytes_freed: 0 } }));
    render(<CleanupDialog slug="me" open onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /select all/i }));
    expect(screen.getByRole("checkbox", { name: /audit data/i })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /lossless export trims/i })).toBeChecked();
  });

  it("names the blocking job on a 409 instead of failing generically", async () => {
    mockFetch(
      err(409, {
        code: "jobs_active",
        message: "Job 'trim' is still running",
        job_id: "j1",
        kind: "trim",
      }),
    );
    render(<CleanupDialog slug="me" open onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /select all/i }));
    // Confirm is gated on every unrebuildable item being individually
    // opted into (the Task 8 review fix); tick it so this test can reach
    // apply() and exercise the 409 handling it's actually about.
    await userEvent.click(
      await screen.findByRole("checkbox", { name: "stage2_b_trimmed.mp4" }),
    );
    await userEvent.click(await screen.findByRole("button", { name: /^reclaim$/i }));
    await userEvent.click(screen.getByRole("button", { name: /confirm/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/trim/);
    expect(alert).not.toHaveTextContent(/cleanup failed/i);
  });

  it("blocks apply until every unrebuildable item is individually checked", async () => {
    const applyResponse = ok({
      plan: PLAN,
      result: { deleted: [], failed: [], bytes_freed: 0 },
    });
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) => {
      const method = (init as RequestInit | undefined)?.method ?? "GET";
      return Promise.resolve(method === "POST" ? applyResponse : ok(PLAN));
    });
    const posted = () =>
      fetchMock.mock.calls.some(
        ([, init]) => (init as RequestInit | undefined)?.method === "POST",
      );

    render(<CleanupDialog slug="me" open onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /select all/i }));
    await userEvent.click(await screen.findByRole("button", { name: /^reclaim$/i }));

    // Wait for the plan (and the unrebuildable item's own checkbox) to be
    // visible before attempting confirm, so this exercises the opt-in gate
    // itself rather than a plan-not-loaded-yet race.
    await screen.findByRole("checkbox", { name: "stage2_b_trimmed.mp4" });

    await userEvent.click(screen.getByRole("button", { name: /confirm/i }));

    // The unrebuildable item was never individually ticked -- the request
    // must never fire, even though a whole category ("select all") covers
    // it and the confirm step was reached.
    expect(posted()).toBe(false);

    await userEvent.click(screen.getByRole("checkbox", { name: "stage2_b_trimmed.mp4" }));
    await userEvent.click(screen.getByRole("button", { name: /confirm/i }));

    await waitFor(() => expect(posted()).toBe(true));
  });
});
