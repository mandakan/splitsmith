/**
 * The review queue's detail pane links out for both kinds of work the
 * redesign spec's routes table promises (#902): /review for marker
 * edits, and /dev/review/:slug for candidate labeling -- carrying the
 * dev-mode ?match= context the way every other dev surface does.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, type DevReviewQueueItem } from "@/lib/api";
import { DevReviewQueue } from "@/pages/dev/DevReviewQueue";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getDevReviewQueue: vi.fn(),
      // useLabRun deps (the queue rail's eval-pending affordance).
      getLastLabRun: vi.fn().mockRejectedValue(Object.assign(new Error("404"), { status: 404 })),
      runLabEval: vi.fn(),
      pollJob: vi.fn(),
    },
  };
});

afterEach(() => {
  vi.clearAllMocks();
});

function item(slug: string): DevReviewQueueItem {
  return {
    slug,
    audit_path: `/fixtures/${slug}.json`,
    status: "pending",
    source: "match",
    source_label: "Match promote",
    venue: "hfo",
    stage_number: 1,
    shooter: "s0fe3d797",
    n_shots: 12,
    n_disagreements: 0,
    promoted_at: null,
    age_seconds: 60,
  };
}

function renderQueue(search = "") {
  return render(
    <MemoryRouter initialEntries={[`/dev/review${search}`]}>
      <Routes>
        <Route path="dev/review" element={<DevReviewQueue />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("DevReviewQueue detail pane", () => {
  it("links the active item to marker edits and to labeling, keeping ?match=", async () => {
    vi.mocked(api.getDevReviewQueue).mockResolvedValue({
      pending: [item("stage-shots-hfo-2026-stage1-s0fe3d797")],
      flagged: [],
      done: [],
    });

    renderQueue("?match=m-1");

    const label = await screen.findByRole("link", { name: /label/i });
    expect(label).toHaveAttribute(
      "href",
      "/dev/review/stage-shots-hfo-2026-stage1-s0fe3d797?match=m-1",
    );
    expect(screen.getByRole("link", { name: /open in editor/i })).toHaveAttribute(
      "href",
      "/review?fixture=%2Ffixtures%2Fstage-shots-hfo-2026-stage1-s0fe3d797.json",
    );
  });

  it("omits the match param when the queue URL has none", async () => {
    vi.mocked(api.getDevReviewQueue).mockResolvedValue({
      pending: [item("fixture-alpha")],
      flagged: [],
      done: [],
    });

    renderQueue();

    const label = await screen.findByRole("link", { name: /label/i });
    expect(label).toHaveAttribute("href", "/dev/review/fixture-alpha");
  });
});

describe("DevReviewQueue eval-pending rail", () => {
  it("scores the pending + flagged set and reports run coverage", async () => {
    const userEvent = (await import("@testing-library/user-event")).default;
    vi.mocked(api.getDevReviewQueue).mockResolvedValue({
      pending: [item("fixture-alpha"), item("fixture-bravo")],
      flagged: [{ ...item("fixture-charlie"), status: "flagged" }],
      done: [{ ...item("fixture-done"), status: "done" }],
    });
    vi.mocked(api.runLabEval).mockResolvedValue({ id: "job-1", status: "running" } as never);
    vi.mocked(api.pollJob).mockResolvedValue({ id: "job-1", status: "succeeded" } as never);
    // Hydration 404s (factory default); the post-eval refetch returns a
    // run covering two of the three work items.
    vi.mocked(api.getLastLabRun)
      .mockRejectedValueOnce(Object.assign(new Error("404"), { status: 404 }))
      .mockResolvedValue({
        universe: {
          fixtures: [{ slug: "fixture-alpha" }, { slug: "fixture-bravo" }],
        },
        config: {},
      } as never);

    renderQueue();

    expect(await screen.findByText(/no eval yet/i)).toBeInTheDocument();
    const btn = await screen.findByRole("button", { name: /eval pending \(3\)/i });
    await userEvent.click(btn);

    expect(api.runLabEval).toHaveBeenCalledWith(
      expect.objectContaining({
        slugs: ["fixture-alpha", "fixture-bravo", "fixture-charlie"],
      }),
    );
    expect(await screen.findByText(/run covers 2 \/ 3 pending/i)).toBeInTheDocument();
  });
});
