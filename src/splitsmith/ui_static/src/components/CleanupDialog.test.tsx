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

  it("clears a stale plan when a category toggle's fetch fails, so a stray confirm can't post", async () => {
    // I1 whole-branch finding: `plan` was never invalidated when
    // `selected` changed, and a failed re-fetch's `.catch()` left the
    // previous plan in state. Tick "caches" (a small, all-reconstructable
    // plan arrives); tick "exports-trims" while that GET fails. The stale
    // "Total: 10 B" must not linger, and Confirm -- reachable because
    // `selected` is non-empty -- must not be able to post against it.
    const smallPlan = {
      items: [],
      totals_by_category: { caches: { file_count: 1, bytes: 10 } },
      total_bytes: 10,
      total_file_count: 1,
    };
    const emptyPlan = {
      items: [],
      totals_by_category: {},
      total_bytes: 0,
      total_file_count: 0,
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation((url, init) => {
        const method = (init as RequestInit | undefined)?.method ?? "GET";
        if (method === "POST") {
          return Promise.resolve(
            ok({ plan: smallPlan, result: { deleted: [], failed: [], bytes_freed: 0 } }),
          );
        }
        const href = String(url);
        if (href.includes("categories=caches%2Cexports-trims")) {
          return Promise.reject(new Error("network down"));
        }
        if (href.includes("categories=caches")) {
          return Promise.resolve(ok(smallPlan));
        }
        return Promise.resolve(ok(emptyPlan));
      });
    const posted = () =>
      fetchMock.mock.calls.some(
        ([, init]) => (init as RequestInit | undefined)?.method === "POST",
      );

    render(<CleanupDialog slug="me" open onClose={() => {}} />);

    await userEvent.click(
      screen.getByRole("checkbox", { name: /thumbnails, probes/i }),
    );
    await waitFor(() =>
      expect(screen.getByText(/Total: 10 B/)).toBeInTheDocument(),
    );

    await userEvent.click(
      screen.getByRole("checkbox", { name: /lossless export trims/i }),
    );
    await waitFor(() =>
      expect(screen.queryByText(/Total: 10 B/)).not.toBeInTheDocument(),
    );

    await userEvent.click(await screen.findByRole("button", { name: /^reclaim$/i }));
    await userEvent.click(screen.getByRole("button", { name: /confirm/i }));

    expect(posted()).toBe(false);
  });

  it("surfaces a partial apply failure instead of closing silently", async () => {
    // I4 whole-branch finding: `apply_cleanup` never raises on individual
    // delete failures by design, so a caller that only checks for a
    // thrown exception cannot tell a partial failure from full success.
    const onClose = vi.fn();
    mockFetch(
      ok({
        plan: PLAN,
        result: {
          deleted: ["exports/stage1_a_trimmed.mp4"],
          failed: [["exports/stage2_b_trimmed.mp4", "storage delete failed"]],
          bytes_freed: 1_048_576,
        },
      }),
    );
    render(<CleanupDialog slug="me" open onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: /select all/i }));
    await userEvent.click(
      await screen.findByRole("checkbox", { name: "stage2_b_trimmed.mp4" }),
    );
    await userEvent.click(await screen.findByRole("button", { name: /^reclaim$/i }));
    await userEvent.click(screen.getByRole("button", { name: /confirm/i }));

    await waitFor(() =>
      expect(screen.getByText(/1 file could not be removed/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/Freed 1\.0 MB/)).toBeInTheDocument();
    // The whole point: a partial failure must not close the dialog like a
    // silent success would.
    expect(onClose).not.toHaveBeenCalled();
  });

  it("disables confirm once every category is unchecked, even mid-confirm", async () => {
    // M6: unchecking every category at the confirm step used to leave
    // Confirm looking live while `apply()`'s own guard silently no-opped.
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) => {
      const method = (init as RequestInit | undefined)?.method ?? "GET";
      return Promise.resolve(
        method === "POST"
          ? ok({ plan: PLAN, result: { deleted: [], failed: [], bytes_freed: 0 } })
          : ok(PLAN),
      );
    });
    const posted = () =>
      fetchMock.mock.calls.some(
        ([, init]) => (init as RequestInit | undefined)?.method === "POST",
      );

    render(<CleanupDialog slug="me" open onClose={() => {}} />);
    const cachesCheckbox = screen.getByRole("checkbox", {
      name: /thumbnails, probes/i,
    });
    await userEvent.click(cachesCheckbox);
    await userEvent.click(
      await screen.findByRole("checkbox", { name: "stage2_b_trimmed.mp4" }),
    );
    await userEvent.click(await screen.findByRole("button", { name: /^reclaim$/i }));

    const confirmButton = screen.getByRole("button", { name: /confirm/i });
    await waitFor(() => expect(confirmButton).toBeEnabled());

    await userEvent.click(cachesCheckbox); // unchecks the only selected category
    await waitFor(() => expect(confirmButton).toBeDisabled());

    await userEvent.click(confirmButton);
    expect(posted()).toBe(false);
  });
});
