/**
 * Clicking a fixture row with no eval run cached lands on the pre-eval
 * "Lite" drawer. That state must (a) tell the operator that candidate
 * labeling needs an eval first -- prominently, not as a footnote --
 * and (b) offer a fast slug-scoped eval for just this fixture, because
 * a full-corpus eval takes ~10 minutes and the cache dies with the
 * server process. Regression: the drawer silently showed a waveform
 * and a "Re-label" button that opens the plain marker editor, so the
 * labeling flow looked broken after every server restart.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import { api } from "@/lib/api";
import { Lab } from "@/pages/Lab";

vi.mock("@/components/SweepsCard", () => ({ SweepsCard: () => null }));
vi.mock("@/components/Waveform", () => ({ Waveform: () => null }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listLabFixtures: vi.fn().mockResolvedValue([
        {
          slug: "stage-shots-hfo-masters-2026-stage1-s0fe3d797",
          audit_path: "/fixtures/stage-shots-hfo-masters-2026-stage1-s0fe3d797.json",
          audio_path: "/fixtures/stage-shots-hfo-masters-2026-stage1-s0fe3d797.wav",
          has_audio: true,
          n_shots: 12,
          expected_rounds: 12,
          stage_time_seconds: 20,
          beep_time: 1.5,
          source: null,
          source_video: null,
          audit_mtime: 1,
          audio_mtime: 1,
          anchor_slug: null,
          event_id: null,
        },
      ]),
      getLastLabRun: vi.fn().mockRejectedValue(new Error("no run")),
      getRecentProjects: vi.fn().mockResolvedValue([]),
      getFixturePeaks: vi.fn().mockResolvedValue({
        peaks: [0, 0.5],
        duration: 20,
        beep_time: 1.5,
      }),
      getFixtureAudit: vi.fn().mockResolvedValue({
        stage_number: 1,
        stage_name: "B50",
        beep_time: 1.5,
        shots: [],
        videos: [],
      }),
      runLabEval: vi.fn().mockResolvedValue({ id: "job-1", status: "running" }),
      pollJob: vi.fn().mockResolvedValue({ id: "job-1", status: "succeeded" }),
    },
  };
});

afterEach(() => {
  vi.clearAllMocks();
});

const SLUG = "stage-shots-hfo-masters-2026-stage1-s0fe3d797";

function renderLabAt(slug: string) {
  return render(
    <MemoryRouter initialEntries={[`/dev/legacy/lab/${slug}`]}>
      <ConfirmProvider>
        <Routes>
          <Route path="dev/legacy/lab/:slug" element={<Lab />} />
          <Route path="dev/legacy/lab" element={<Lab />} />
        </Routes>
      </ConfirmProvider>
    </MemoryRouter>,
  );
}

describe("fixture drawer visibility", () => {
  it("scrolls the drawer into view when a fixture is selected", async () => {
    // The drawer renders below the fixture table, which at corpus size
    // is thousands of pixels tall -- without an explicit scroll the
    // row click looks like a no-op. jsdom has no scrollIntoView, so
    // install a spy to observe the call.
    const spy = vi.fn();
    Element.prototype.scrollIntoView = spy;
    renderLabAt(SLUG);
    await screen.findByText(/labeling needs an eval run/i);
    await waitFor(() => expect(spy).toHaveBeenCalled());
  });
});

describe("pre-eval fixture drawer", () => {
  it("tells the operator labeling needs an eval run first", async () => {
    renderLabAt(SLUG);
    expect(
      await screen.findByText(/labeling needs an eval run/i),
    ).toBeInTheDocument();
  });

  it("offers a slug-scoped eval for just this fixture", async () => {
    renderLabAt(SLUG);
    const btn = await screen.findByRole("button", {
      name: /eval this fixture/i,
    });
    await userEvent.click(btn);
    await waitFor(() =>
      expect(api.runLabEval).toHaveBeenCalledWith(
        expect.objectContaining({ slugs: [SLUG] }),
      ),
    );
  });
});
