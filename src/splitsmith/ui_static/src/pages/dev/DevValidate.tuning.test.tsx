/**
 * Validate's tuning panel drives the shared ``useLabRun`` hook, so a
 * slider tweak debounces into ``api.rescoreLabUniverse`` (120ms, same
 * as legacy Lab.tsx) and the headline metrics re-render from the
 * rescored response. Pins the wiring described in the lab-redesign
 * plan's task 9: DevValidate swaps its ad-hoc eval state for the hook
 * but the run-config bar keeps driving the same config fields.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DeveloperShellOutletContext } from "@/components/developer/DeveloperShell";
import { DEFAULT_CONFIG } from "@/components/lab/useLabRun";
import { api, type LabEvalRun } from "@/lib/api";
import { DevValidate } from "@/pages/dev/DevValidate";

vi.mock("@/components/SweepsCard", () => ({ SweepsCard: () => null }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getLastLabRun: vi.fn(),
      rescoreLabUniverse: vi.fn(),
      runLabEval: vi.fn(),
      pollJob: vi.fn(),
    },
  };
});

function makeRun(overrides: Partial<LabEvalRun> = {}): LabEvalRun {
  return {
    config: DEFAULT_CONFIG,
    config_hash: "hash-1",
    built_at: "2026-08-14T00:00:00Z",
    summary: {
      n_fixtures: 3,
      n_truth: 10,
      n_kept: 9,
      true_positives: 9,
      false_positives: 0,
      false_negatives: 1,
      precision: 1,
      recall: 0.9,
      f1: 0.947,
      fp_by_reason: {},
      positives_by_subclass: {},
    },
    universe: {
      fixtures: [],
      voter_a_floor: 0.1,
      voter_b_threshold: 0.02,
      voter_c_threshold: 0.5,
      tolerance_ms: 75,
    },
    ...overrides,
  };
}

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

const outletContext: DeveloperShellOutletContext = {
  model: {
    active_version: "v3",
    recall: 0.95,
    precision: 0.98,
    f1: 0.96,
    fixture_count: 3,
    built_at: null,
    step_counts: { corpus: 3, review: 0, validate_runs: 1, retrain: 1 },
  },
  refresh: () => {},
};

function renderValidate() {
  return render(
    <MemoryRouter initialEntries={["/dev/validate"]}>
      <Routes>
        <Route element={<Outlet context={outletContext} />}>
          <Route path="dev/validate" element={<DevValidate />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("DevValidate tuning panel", () => {
  it("rescores after the debounce and the headline metrics re-render from the response", async () => {
    // Mount hydration finds no cached run yet (the normal first-load
    // case) -- every subsequent call (the post-eval refetch, plus this
    // page's own --lab-missing probe) sees the real run. This keeps the
    // hook's hydration path (which never fires here) cleanly separate
    // from the eval/rescore path this test exercises.
    vi.mocked(api.getLastLabRun)
      .mockRejectedValueOnce(new Error("no run"))
      .mockResolvedValue(makeRun());
    vi.mocked(api.runLabEval).mockResolvedValue({ id: "job-1", status: "running" } as never);
    vi.mocked(api.pollJob).mockResolvedValue({ id: "job-1", status: "succeeded" } as never);
    const RESCORED = makeRun({
      config_hash: "rescored",
      summary: {
        n_fixtures: 3,
        n_truth: 10,
        n_kept: 10,
        true_positives: 10,
        false_positives: 0,
        false_negatives: 0,
        precision: 1,
        recall: 0.987,
        f1: 0.99,
        fp_by_reason: {},
        positives_by_subclass: {},
      },
    });
    vi.mocked(api.rescoreLabUniverse).mockResolvedValue(RESCORED);

    renderValidate();

    await userEvent.click(screen.getByRole("button", { name: /^run$/i }));

    // Metrics render from the run the eval fetched.
    expect(await screen.findByText("0.900")).toBeInTheDocument();

    const slider = screen.getByRole("slider", { name: /consensus k/i });

    vi.useFakeTimers();
    fireEvent.change(slider, { target: { value: "3" } });

    // Debounced -- no rescore before the 120ms window elapses.
    expect(api.rescoreLabUniverse).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(150);
    });
    // RTL's waitFor/findBy* poll via setInterval, which fake timers
    // also intercept -- switch back to real timers before querying so
    // that machinery works again. The rescore has already resolved and
    // committed by this point (advanceTimersByTimeAsync awaited it).
    vi.useRealTimers();

    expect(api.rescoreLabUniverse).toHaveBeenCalledWith(
      expect.objectContaining({ consensus: 3 }),
    );
    expect(screen.getByText("0.987")).toBeInTheDocument();
  });

  it("has exactly one consensus control, ranged 1-3, with a live bar readout", async () => {
    // #899: the run-config bar and TuningPanel both wrote
    // config.consensus with mismatched displayed ranges ("of 3" vs
    // "of 4" -- the ensemble runs three voters). The tuning panel owns
    // the only control now; the bar cell is a read-only readout of the
    // same field.
    vi.mocked(api.getLastLabRun).mockRejectedValue(new Error("no run"));

    renderValidate();
    await screen.findByTestId("consensus-readout");

    const sliders = screen
      .getAllByRole("slider")
      .filter((s) => /consensus/i.test(s.getAttribute("aria-label") ?? "") ||
        /consensus/i.test(s.closest("label")?.textContent ?? ""));
    expect(sliders).toHaveLength(1);
    expect(sliders[0]).toHaveAttribute("max", "3");
    expect(sliders[0]).toHaveAttribute("min", "1");

    const readout = screen.getByTestId("consensus-readout");
    expect(readout).toHaveTextContent("2");
    expect(readout).toHaveTextContent("of 3");
    expect(readout.querySelector("input")).toBeNull();

    // The readout tracks the tuning panel's slider -- same field.
    fireEvent.change(sliders[0], { target: { value: "3" } });
    expect(screen.getByTestId("consensus-readout")).toHaveTextContent("3");
  });
});
