/**
 * The review queue's detail pane links out for both kinds of work the
 * redesign spec's routes table promises (#902): /review for marker
 * edits, and /dev/corpus/:slug for candidate labeling -- carrying the
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
      "/dev/corpus/stage-shots-hfo-2026-stage1-s0fe3d797?match=m-1",
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
    expect(label).toHaveAttribute("href", "/dev/corpus/fixture-alpha");
  });
});
