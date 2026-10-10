/**
 * The lab Review page's save path: a save replaces the whole fixture file,
 * so whatever the page holds as the document is what lands on disk.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

import { Review } from "./Review";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getFixtureAudit: vi.fn(),
      getFixturePeaks: vi.fn(),
      saveFixtureAudit: vi.fn(),
      confirmReviewFixture: vi.fn(),
      getDevReviewQueue: vi.fn(),
    },
  };
});

const PATH = "/fx/stage-shots-x-2026-stage1-s00000000.json";
const doc = {
  beep_time: 0,
  stage_number: 1,
  shots: [{ shot_number: 1, candidate_number: 1, time: 1.5, ms_after_beep: 1500, source: "promoted" }],
  _candidates_pending_audit: { candidates: [{ candidate_number: 1, time: 1.5 }] },
  review: { status: "needs_review", derived_from: "x" },
  audit_events: [],
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[`/review?fixture=${encodeURIComponent(PATH)}`]}>
      <Routes>
        <Route path="/review" element={<Review />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Review save path", () => {
  beforeEach(() => {
    vi.mocked(api.getFixturePeaks).mockResolvedValue({ peaks: new Array(3000).fill(0.01), duration: 3 } as never);
    vi.mocked(api.saveFixtureAudit).mockImplementation(async (_p, payload) => payload as never);
  });

  it("keeps the sign-off in the document when the reload after it fails, so a later save cannot erase it", async () => {
    vi.mocked(api.getFixtureAudit)
      .mockResolvedValueOnce(structuredClone(doc) as never)
      .mockRejectedValueOnce(new Error("network"));
    vi.mocked(api.confirmReviewFixture).mockResolvedValue({
      slug: "x",
      confirmed_at: "2026-10-10T12:00:00+00:00",
      status: "done",
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Mark reviewed/ }));
    await waitFor(() => expect(api.confirmReviewFixture).toHaveBeenCalled());
    await waitFor(() => expect(api.getFixtureAudit).toHaveBeenCalledTimes(2));

    fireEvent.keyDown(window, { key: "s", metaKey: true });
    await waitFor(() => expect(api.saveFixtureAudit).toHaveBeenCalled());
    const payload = vi.mocked(api.saveFixtureAudit).mock.calls.at(-1)![1] as { review?: { status?: string } };
    expect(payload.review?.status).toBe("reviewed");
  });
});
